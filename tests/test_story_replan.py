"""Offline production-path regressions for bounded Replan Range."""
from copy import deepcopy
import json
from uuid import uuid4

import pytest

from miniconstruct.llm.client import LLMBackendError
from miniconstruct.models.api import LLMSettings
from miniconstruct.models.story_memory import StoryContinuityMemory
from miniconstruct.models.story_operations import StoryReplanRequest
from miniconstruct.models.workspace import SubjectIdentityReference, SubjectRecord
from miniconstruct.project_store import ProjectStore, StaleProjectSnapshotError
from miniconstruct.story_operations import plan_replan


LLM = LLMSettings.model_validate({"modelId": "offline", "endpoint": {
    "id": "offline", "displayName": "Offline", "baseUrl": "http://never.test/v1"}})
BRIDGE_FACT = "11111111-1111-4111-8111-111111111111"
PACKAGE_THREAD = "22222222-2222-4222-8222-222222222222"


class OfflineReplanClient:
    def __init__(self, transform=None):
        self.calls, self.transform, self.last_usage = [], transform, None

    def __call__(self, _settings):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def generate(self, messages, *, response_instruction=None):
        assert response_instruction is None
        self.calls.append(messages)
        content = messages[1]["content"]
        context = json.loads(content[0]["text"] if isinstance(content, list) else content)
        relationship = None if context["startOrdinal"] == 1 else "new_shot_preserve_continuity"
        response = {"items": [{
            "existingGenerationId": generation_id, "scenePlan": f"Replanned beat {index + 1}", "dialogue": "",
            "incomingState": "Bridge intact", "intendedOutgoingState": "Delivery still unresolved",
            "relationshipToPrevious": relationship if index == 0 else "independent", "cameraIntent": "Tense medium shot",
            "shotCount": 1,
            "referenceRecommendations": [], "memoryPatchAfter": {},
        } for index, generation_id in enumerate(context["targetGenerationIds"])], "warnings": []}
        if self.transform:
            self.transform(response, context)
        return json.dumps(response)


def prompt_context(client):
    content = client.calls[-1][1]["content"]
    return json.loads(content[0]["text"] if isinstance(content, list) else content)


def manifest_path(store, project):
    return store.root / f"project-{project.project_id}" / "project.json"


def set_memory(store, project_id, generation_id, memory):
    active = store.activate_generation(project_id, generation_id)
    return store.save_project(project_id, active.workspace, story_memory_generation_id=generation_id, story_memory=memory)


def make_story(tmp_path, workspace_factory, length=8):
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory(creativeRequest="G1"), story_idea="Alice must deliver a package to Bob.",
                                   planning={"scenePlan": "G1"})
    for ordinal in range(2, length + 1):
        project = store.create_generation(project.project_id, workspace=workspace_factory(creativeRequest=f"G{ordinal}"))
        project = store.save_project(project.project_id, project.workspace, planning={"scenePlan": f"G{ordinal}"})
    generations = store.read_story_snapshot(project.project_id).generations
    upstream = StoryContinuityMemory(
        recentSummary="Alice reaches the intact bridge while travelling to deliver a package to Bob.",
        durableFacts=[{"id": BRIDGE_FACT, "text": "The bridge is intact."}],
        openThreads=[{"id": PACKAGE_THREAD, "text": "The package delivery to Bob is unresolved."}],
    )
    downstream = StoryContinuityMemory(
        recentSummary="The bridge has collapsed and Alice delivered the package to Bob.",
        durableFacts=[{"id": BRIDGE_FACT, "text": "The bridge has collapsed."}], openThreads=[],
    )
    # G2 is U for the main range; distant snapshots intentionally disagree so
    # accidental terminal-memory authority is observable in the model input.
    project = set_memory(store, project.project_id, generations[1].generation_id, upstream)
    for index in range(3, length):
        project = set_memory(store, project.project_id, generations[index].generation_id,
                             downstream.model_copy(update={"recent_summary": f"G{index + 1} later memory: bridge collapsed; package delivered."}))
    return store, project, upstream, downstream, [generation.generation_id for generation in generations]


async def propose(store, project, start_id, end_id, client=None, instruction="Make the beats more tense."):
    return await plan_replan(store, project.project_id, StoryReplanRequest(
        startGenerationId=start_id, endGenerationId=end_id, instruction=instruction, llm=LLM),
        client or OfflineReplanClient())


async def test_replan_sanitizes_unknown_memory_patch_fields(tmp_path, workspace_factory):
    store, project, _upstream, _downstream, ids = make_story(tmp_path, workspace_factory, length=6)
    client = OfflineReplanClient(lambda response, _context: response["items"][0].__setitem__(
        "memoryPatchAfter", {"field": "invented value"}))
    proposal = await propose(store, project, ids[2], ids[3], client)
    assert len(client.calls) == 1
    assert any("Generation 3" in warning and "field" in warning for warning in proposal.warnings)
    assert proposal.items[0].memory_patch_after.fact_upserts == []
    assert proposal.items[0].memory_patch_after.recent_summary is None


async def test_replan_uses_u_memory_not_later_truth_and_atomically_invalidates_downstream(tmp_path, workspace_factory):
    store, project, upstream, _downstream, ids = make_story(tmp_path, workspace_factory)
    # B is known/trusted, its successor is legacy_unknown, then a known stale state.
    path = manifest_path(store, project)
    raw = json.loads(path.read_text())
    raw["sequence"]["generations"][6]["storyMemoryStatus"] = "legacy_unknown"
    raw["sequence"]["generations"][7]["storyMemoryStatus"] = "needs_review"
    path.write_text(json.dumps(raw))
    store.activate_generation(project.project_id, ids[5])
    before = store.read_story_snapshot(project.project_id)
    downstream_before = [(deepcopy(g.story_memory_after), g.story_memory_status) for g in before.generations[5:]]

    def evolve(response, context):
        first_slot = context["memoryCreationIds"]["newFactIds"][0]
        response["items"][0]["memoryPatchAfter"] = {
            "recentSummary": "Alice steps onto the intact bridge; delivery remains unresolved.",
            "factUpserts": [{"id": first_slot, "text": "Alice has started crossing the intact bridge."}],
        }
        response["items"][1]["memoryPatchAfter"] = {
            "factUpserts": [{"id": first_slot, "text": "Alice is midway across the intact bridge."}],
        }

    client = OfflineReplanClient(evolve)
    proposal = await propose(store, project, ids[2], ids[4], client)
    context = prompt_context(client)
    encoded = json.dumps(context)
    assert context["storyMemory"] == upstream.model_dump(by_alias=True)
    assert context["storyMemoryAuthority"]["sourceGenerationId"] == ids[1]
    assert "bridge is intact" in encoded and "unresolved" in encoded
    assert "bridge collapsed" not in encoded and "package delivered" not in encoded
    assert len(context["upstreamProtectedHistory"]) == 2
    assert len(context["targetRange"]) == 3
    assert len(context["downstreamProtectedBoundary"]) == 2
    assert "storyMemoryAfter" not in encoded

    applied = store.apply_story_plan_replan(project.project_id, proposal)
    after = store.read_story_snapshot(project.project_id)
    assert applied.active_generation_id == ids[5]
    assert [generation.generation_id for generation in after.generations] == ids
    assert after.generations[1].story_memory_after == upstream
    assert after.generations[1].story_memory_status == "trusted"
    assert [g.story_memory_status for g in after.generations[2:5]] == ["trusted", "trusted", "trusted"]
    assert [g.workspace.dialogue for g in after.generations[2:5]] == ["", "", ""]
    assert after.generations[2].story_memory_after.durable_facts[-1].text == "Alice has started crossing the intact bridge."
    assert after.generations[3].story_memory_after.durable_facts[-1].text == "Alice is midway across the intact bridge."
    assert [g.story_memory_after for g in after.generations[5:]] == [item[0] for item in downstream_before]
    assert [g.story_memory_status for g in after.generations[5:]] == ["needs_review", "legacy_unknown", "needs_review"]


async def test_replan_consumed_temp_id_cannot_be_reintroduced_after_resolution(tmp_path, workspace_factory):
    store, project, _upstream, _downstream, ids = make_story(tmp_path, workspace_factory)
    def reuse(response, context):
        temp = context["memoryCreationIds"]["newFactIds"][0]
        response["items"][0]["memoryPatchAfter"] = {"factUpserts": [{"id": temp, "text": "Created once."}]}
        response["items"][1]["memoryPatchAfter"] = {"resolvedFactIds": [temp]}
        response["items"][2]["memoryPatchAfter"] = {"factUpserts": [{"id": temp, "text": "Reintroduced."}]}
    with pytest.raises(LLMBackendError, match="Consumed temporary memory ID"):
        await propose(store, project, ids[2], ids[4], OfflineReplanClient(reuse))


@pytest.mark.parametrize("start,end", [(0, 0), (0, 5), (7, 7)])
async def test_replan_accepts_single_six_and_terminal_ranges_with_correct_first_relationship(tmp_path, workspace_factory, start, end):
    store, project, _upstream, _downstream, ids = make_story(tmp_path, workspace_factory)
    proposal = await propose(store, project, ids[start], ids[end])
    assert [item.existing_generation_id for item in proposal.items] == ids[start:end + 1]
    if start == 0:
        assert proposal.items[0].relationship_to_previous is None
    else:
        assert proposal.items[0].relationship_to_previous == "new_shot_preserve_continuity"
    store.apply_story_plan_replan(project.project_id, proposal)
    after = store.read_story_snapshot(project.project_id)
    assert all(g.story_memory_status == "trusted" for g in after.generations[start:end + 1])


async def test_replan_rejects_reversed_oversized_unknown_and_untrusted_upstream(tmp_path, workspace_factory):
    store, project, _upstream, _downstream, ids = make_story(tmp_path, workspace_factory)
    with pytest.raises(ValueError, match="must not precede"):
        await propose(store, project, ids[4], ids[2])
    with pytest.raises(ValueError, match="at most"):
        await propose(store, project, ids[0], ids[6])
    with pytest.raises(ValueError, match="existing"):
        await propose(store, project, str(uuid4()), ids[2])
    raw = json.loads(manifest_path(store, project).read_text())
    raw["sequence"]["generations"][1]["storyMemoryStatus"] = "needs_review"
    manifest_path(store, project).write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="blocked"):
        await propose(store, project, ids[2], ids[2])


@pytest.mark.parametrize("damage", ["reversed", "partial", "extra", "wrong-id", "first-relationship"])
async def test_replan_rejects_noncanonical_model_coverage_without_writing(tmp_path, workspace_factory, damage):
    store, project, _upstream, _downstream, ids = make_story(tmp_path, workspace_factory)

    def malicious(response, _context):
        if damage == "reversed":
            response["items"].reverse()
        elif damage == "partial":
            response["items"].pop()
        elif damage == "extra":
            response["items"].append(deepcopy(response["items"][0]))
        elif damage == "wrong-id":
            response["items"][0]["existingGenerationId"] = str(uuid4())
        else:
            response["items"][0]["relationshipToPrevious"] = None

    before = manifest_path(store, project).read_bytes()
    with pytest.raises(LLMBackendError):
        await propose(store, project, ids[2], ids[4], OfflineReplanClient(malicious))
    assert manifest_path(store, project).read_bytes() == before


async def test_replan_stale_fingerprint_and_changed_boundary_write_nothing(tmp_path, workspace_factory):
    store, project, _upstream, _downstream, ids = make_story(tmp_path, workspace_factory)
    proposal = await propose(store, project, ids[2], ids[4])
    active = store.activate_generation(project.project_id, ids[5])
    store.save_project(project.project_id, active.workspace, planning={"scenePlan": "Changed B"})
    after_change = manifest_path(store, project).read_bytes()
    with pytest.raises(StaleProjectSnapshotError):
        store.apply_story_plan_replan(project.project_id, proposal)
    assert manifest_path(store, project).read_bytes() == after_change

    proposal = await propose(store, project, ids[2], ids[4])
    store.reorder_generations(project.project_id, [ids[0], ids[1], ids[2], ids[4], ids[3], ids[5], ids[6], ids[7]])
    after_reorder = manifest_path(store, project).read_bytes()
    with pytest.raises(StaleProjectSnapshotError):
        store.apply_story_plan_replan(project.project_id, proposal)
    assert manifest_path(store, project).read_bytes() == after_reorder


async def test_replan_preserves_existing_assets_and_materializes_only_pre_cutoff_references(tmp_path, workspace_factory, image_asset_factory):
    store, project, _upstream, _downstream, ids = make_story(tmp_path, workspace_factory)
    source_asset = image_asset_factory("historical", role="environment")
    target_asset = image_asset_factory("target-kept", role="environment")
    future_asset = image_asset_factory("future-forbidden", role="environment")
    for generation_id, asset in ((ids[1], source_asset), (ids[2], target_asset), (ids[5], future_asset)):
        active = store.activate_generation(project.project_id, generation_id)
        store.save_project(project.project_id, active.workspace.model_copy(update={"assets": [asset]}))

    def recommend(response, context):
        assert (ids[1], "historical") in {(pair["sourceGenerationId"], pair["assetId"])
                                            for pair in context["allowedReferencePairs"]}
        assert (ids[5], "future-forbidden") not in {(pair["sourceGenerationId"], pair["assetId"])
                                                       for pair in context["allowedReferencePairs"]}
        response["items"][0]["referenceRecommendations"] = [{
            "sourceGenerationId": ids[1], "assetId": "historical", "recommendation": "Preserve the earlier location texture.",
        }]

    proposal = await propose(store, project, ids[2], ids[2], OfflineReplanClient(recommend))
    store.apply_story_plan_replan(project.project_id, proposal)
    target = store.read_generation(project.project_id, ids[2])
    assert any(asset.id == "target-kept" for asset in target.workspace.assets)
    assert len(target.workspace.assets) == 2


async def test_replan_self_reference_to_existing_target_asset_is_a_noop(tmp_path, workspace_factory, image_asset_factory):
    store, project, _upstream, _downstream, ids = make_story(tmp_path, workspace_factory)
    target_asset = image_asset_factory("target-kept").model_copy(update={
        "subject_identity": SubjectIdentityReference(subjectId="alice"),
    })
    active = store.activate_generation(project.project_id, ids[2])
    store.save_project(project.project_id, active.workspace.model_copy(update={
        "assets": [target_asset],
        "subjects": [SubjectRecord(id="alice", number=1)],
        "next_subject_number": 2,
    }))
    before_manifest = json.loads(manifest_path(store, project).read_text())
    before_target = next(g for g in before_manifest["sequence"]["generations"] if g["generationId"] == ids[2])
    before_subjects = deepcopy(before_target["workspace"]["subjects"])
    before_next_subject_number = before_target["workspace"]["nextSubjectNumber"]
    before_asset_files = deepcopy(before_target["assetFiles"])

    def recommend(response, context):
        pairs = {(pair["sourceGenerationId"], pair["assetId"]) for pair in context["allowedReferencePairs"]}
        assert (ids[2], "target-kept") in pairs
        response["items"][0]["referenceRecommendations"] = [{
            "sourceGenerationId": ids[2], "assetId": "target-kept", "recommendation": "Keep the established face.",
        }]

    proposal = await propose(store, project, ids[2], ids[2], OfflineReplanClient(recommend))
    assert [(reference.source_generation_id, reference.asset_id)
            for reference in proposal.items[0].reference_recommendations] == [(ids[2], "target-kept")]
    store.apply_story_plan_replan(project.project_id, proposal)

    target = store.read_generation(project.project_id, ids[2])
    assert [asset.id for asset in target.workspace.assets] == ["target-kept"]
    assert [subject.model_dump() for subject in target.workspace.subjects] == before_subjects
    assert target.workspace.next_subject_number == 2
    assert target.planning.scene_plan == "Replanned beat 1"
    assert target.incoming_state == "Bridge intact"
    assert target.planning.camera_intent == "Tense medium shot"
    assert target.story_memory_status == "trusted"

    after_manifest = json.loads(manifest_path(store, project).read_text())
    after_target = next(g for g in after_manifest["sequence"]["generations"] if g["generationId"] == ids[2])
    assert after_target["workspace"]["subjects"] == before_subjects
    assert after_target["workspace"]["nextSubjectNumber"] == before_next_subject_number
    assert after_target["assetFiles"] == before_asset_files


async def test_replan_recalls_relevant_historical_subject_identity_without_irrelevant_subjects(tmp_path, workspace_factory, image_asset_factory):
    store = ProjectStore(tmp_path)

    def identity(asset_id, subject_id):
        return image_asset_factory(asset_id).model_copy(update={
            "subject_identity": SubjectIdentityReference(subjectId=subject_id),
        })

    project = store.create_project(workspace_factory(
        subjects=[{"id": "alice", "number": 1}, {"id": "bob", "number": 2}], nextSubjectNumber=3,
        assets=[identity("alice-history", "alice"), identity("bob-history", "bob")], creativeRequest="G1",
    ))
    for ordinal in range(2, 8):
        project = store.create_generation(project.project_id, workspace=workspace_factory(creativeRequest=f"G{ordinal}"))
    ids = [generation.generation_id for generation in store.read_story_snapshot(project.project_id).generations]
    set_memory(store, project.project_id, ids[3], StoryContinuityMemory(
        durableFacts=[{"id": BRIDGE_FACT, "text": "Alice remains recognizable.", "relatedSubjectIds": ["alice"]}],
    ))
    snapshot = store.read_story_replan_snapshot(project.project_id, ids[4], ids[4], "Keep Alice consistent")
    assert {(reference.generation.generation_id, reference.asset_id) for reference in snapshot.historical_references} == {
        (ids[0], "alice-history")
    }
