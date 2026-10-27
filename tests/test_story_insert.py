"""Offline production-path regressions for Generation-owned Insert Between."""
import json
from copy import deepcopy
from uuid import uuid4

import pytest

from miniconstruct.llm.client import LLMBackendError
from miniconstruct.models.api import LLMSettings
from miniconstruct.models.story_operations import StoryInsertRequest
from miniconstruct.models.story_memory import StoryContinuityMemory
from miniconstruct.models.workspace import SubjectIdentityReference
from miniconstruct.project_store import ProjectStore, StaleProjectSnapshotError
from miniconstruct.story_operations import plan_insert


LLM = LLMSettings.model_validate({"modelId": "offline", "endpoint": {
    "id": "offline", "displayName": "Offline", "baseUrl": "http://never.test/v1"}})
BRIDGE_FACT = "11111111-1111-4111-8111-111111111111"
PACKAGE_THREAD = "22222222-2222-4222-8222-222222222222"


class OfflineInsertClient:
    def __init__(self, transform=None):
        self.calls, self.transform, self.last_usage = [], transform, None

    def __call__(self, _settings): return self
    async def __aenter__(self): return self
    async def __aexit__(self, *_): pass

    async def generate(self, messages, *, response_instruction=None):
        assert response_instruction is None
        self.calls.append(messages)
        content = messages[1]["content"]
        context = json.loads(content[0]["text"] if isinstance(content, list) else content)
        response = {"slots": [{
            "planItemId": slot_id, "scenePlan": f"Bridge beat {index}", "dialogue": "",
            "incomingState": "At the intact bridge", "intendedOutgoingState": "Approaching the protected next beat",
            "relationshipToPrevious": "new_shot_preserve_continuity", "cameraIntent": "Medium shot",
            "shotCount": 1,
            "referenceRecommendations": [], "memoryPatchAfter": {},
        } for index, slot_id in enumerate(context["slotIds"])], "warnings": []}
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
    return store.save_project(project_id, active.workspace, story_memory_generation_id=generation_id,
                              story_memory=memory)


def make_story(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path)
    start = StoryContinuityMemory(recentSummary="Alice starts a journey.")
    project = store.create_project(workspace_factory(creativeRequest="G1"), story_idea="Alice and Bob's package.",
                                   story_memory=start, planning={"scenePlan": "G1"})
    for label in ("G2", "G3", "G4", "G5"):
        project = store.create_generation(project.project_id, workspace=workspace_factory(creativeRequest=label))
        project = store.save_project(project.project_id, project.workspace, planning={"scenePlan": label})
    generations = store.read_story_snapshot(project.project_id).generations
    authoritative = StoryContinuityMemory(
        recentSummary="Alice reaches an intact bridge.",
        durableFacts=[{"id": BRIDGE_FACT, "text": "The bridge is intact."}],
        openThreads=[{"id": PACKAGE_THREAD, "text": "Alice still needs to deliver a package to Bob."}],
    )
    terminal = StoryContinuityMemory(
        recentSummary="The bridge has collapsed and the package has been delivered.",
        durableFacts=[{"id": BRIDGE_FACT, "text": "The bridge has collapsed."}], openThreads=[],
    )
    # G3 is A. Giving each later Generation a different stored snapshot makes
    # preservation checks detect any accidental replay/rewrite on Apply.
    project = set_memory(store, project.project_id, generations[2].generation_id, authoritative)
    project = set_memory(store, project.project_id, generations[3].generation_id,
                         terminal.model_copy(update={"recent_summary": "G4 later stored memory."}))
    project = set_memory(store, project.project_id, generations[4].generation_id, terminal)
    return store, project, authoritative, terminal, generations[2].generation_id


async def propose(store, project, upstream_id, client=None, count=2, instruction="Bridge naturally."):
    return await plan_insert(store, project.project_id, StoryInsertRequest(
        upstreamGenerationId=upstream_id, requestedCount=count, instruction=instruction, llm=LLM),
        client or OfflineInsertClient())


async def test_insert_sanitizes_unknown_memory_patch_fields(tmp_path, workspace_factory):
    store, project, _memory, _terminal, upstream_id = make_story(tmp_path, workspace_factory)
    client = OfflineInsertClient(lambda response, _context: response["slots"][0].__setitem__(
        "memoryPatchAfter", {"field": "invented value"}))
    proposal = await propose(store, project, upstream_id, client, count=1)
    assert len(client.calls) == 1
    assert any("Generation 4" in warning and "field" in warning for warning in proposal.warnings)
    assert proposal.slots[0].memory_patch_after.fact_upserts == []
    assert proposal.slots[0].memory_patch_after.recent_summary is None


async def test_insert_uses_a_owned_memory_not_later_truth_and_marks_only_downstream(tmp_path, workspace_factory):
    store, project, a_memory, terminal_memory, a_id = make_story(tmp_path, workspace_factory)
    before = store.read_story_snapshot(project.project_id)
    downstream_memory = [deepcopy(g.story_memory_after) for g in before.generations[3:]]
    client = OfflineInsertClient()
    proposal = await propose(store, project, a_id, client)
    context = prompt_context(client)
    encoded = json.dumps(context)
    assert context["storyMemory"] == a_memory.model_dump(by_alias=True)
    assert context["storyMemoryAuthority"]["sourceGenerationId"] == a_id
    assert "intact" in encoded and "still needs to deliver" in encoded
    assert "collapsed" not in encoded and "has been delivered" not in encoded
    assert len(context["upstreamHistory"]) == 3
    assert len(context["downstreamProtectedBoundary"]) == 2
    assert context["downstreamProtectedBoundary"][0]["scenePlan"] == "G4"
    assert "storyMemoryAfter" not in encoded

    applied = store.apply_story_plan_insert(project.project_id, proposal)
    after = store.read_story_snapshot(project.project_id)
    old_ids = [g.generation_id for g in before.generations]
    assert [g.generation_id for g in after.generations] == old_ids[:3] + [g.generation_id for g in after.generations[3:5]] + old_ids[3:]
    assert applied.active_generation_id == before.manifest.sequence.active_generation_id
    assert after.generations[2].story_memory_after == a_memory
    assert after.generations[2].story_memory_status == "trusted"
    assert [g.story_memory_status for g in after.generations[3:5]] == ["trusted", "trusted"]
    assert [g.story_memory_after for g in after.generations[5:]] == downstream_memory
    assert all(g.story_memory_status == "needs_review" for g in after.generations[5:])
    assert terminal_memory.recent_summary in after.generations[-1].story_memory_after.recent_summary


async def test_insert_preserves_legacy_unknown_downstream_status_and_snapshots(tmp_path, workspace_factory):
    store, project, _a_memory, _terminal, a_id = make_story(tmp_path, workspace_factory)
    project = store.create_generation(project.project_id, workspace=workspace_factory(creativeRequest="G6"))
    path = manifest_path(store, project)
    raw = json.loads(path.read_text())
    # A=G3 trusted; B=G4 legacy_unknown; C=G5 trusted; D=G6 needs_review.
    raw["sequence"]["generations"][3]["storyMemoryStatus"] = "legacy_unknown"
    raw["sequence"]["generations"][4]["storyMemoryStatus"] = "trusted"
    raw["sequence"]["generations"][5]["storyMemoryStatus"] = "needs_review"
    path.write_text(json.dumps(raw))
    before = store.read_story_snapshot(project.project_id)
    downstream_before = [(g.story_memory_after.model_dump(mode="json"), g.story_memory_status)
                         for g in before.generations[3:]]
    proposal = await propose(store, project, a_id, count=1)
    store.apply_story_plan_insert(project.project_id, proposal)
    after = store.read_story_snapshot(project.project_id)
    assert [g.story_memory_status for g in after.generations[3:]] == ["trusted", "legacy_unknown", "needs_review", "needs_review"]
    assert [g.story_memory_after.model_dump(mode="json") for g in after.generations[4:]] == [item[0] for item in downstream_before]
    assert after.generations[2].story_memory_status == "trusted"


@pytest.mark.parametrize("count", [1, 4])
async def test_insert_slot_count_order_and_sequential_memory_are_enforced(tmp_path, workspace_factory, count):
    store, project, _a_memory, _terminal, a_id = make_story(tmp_path, workspace_factory)

    def evolve(response, context):
        first = context["memoryCreationIds"]["newThreadIds"][0]
        response["slots"][0]["memoryPatchAfter"] = {"threadUpserts": [{"id": first, "text": "Alice is crossing the bridge."}]}
        if len(response["slots"]) > 1:
            response["slots"][1]["memoryPatchAfter"] = {"threadUpserts": [{"id": first, "text": "Alice crossed the bridge."}]}

    proposal = await propose(store, project, a_id, OfflineInsertClient(evolve), count=count)
    if count > 1:
        assert proposal.slots[1].memory_patch_after.thread_upserts[0].text == "Alice crossed the bridge."
    applied = store.apply_story_plan_insert(project.project_id, proposal)
    inserted = store.read_story_snapshot(project.project_id).generations[3:3 + count]
    assert applied.generation_count == 5 + count
    assert inserted[0].story_memory_after.open_threads[-1].text == "Alice is crossing the bridge."
    if count > 1:
        assert inserted[1].story_memory_after.open_threads[-1].text == "Alice crossed the bridge."

    # A fresh malformed response cannot write a partial sequence.
    malformed = await propose(store, applied, a_id, count=count)
    before = manifest_path(store, applied).read_bytes()
    if count == 1:
        malformed.slots[0].plan_item_id = str(uuid4())
    else:
        malformed.slots.reverse()
    with pytest.raises(ValueError, match="slot count, identity or order"):
        store.apply_story_plan_insert(project.project_id, malformed)
    assert manifest_path(store, applied).read_bytes() == before


async def test_insert_consumed_temp_id_cannot_be_reintroduced_after_resolution(tmp_path, workspace_factory):
    store, project, _a_memory, _terminal, a_id = make_story(tmp_path, workspace_factory)
    def reuse(response, context):
        temp = context["memoryCreationIds"]["newThreadIds"][0]
        response["slots"][0]["memoryPatchAfter"] = {"threadUpserts": [{"id": temp, "text": "Created once."}]}
        response["slots"][1]["memoryPatchAfter"] = {"resolvedThreadIds": [temp]}
        response["slots"][2]["memoryPatchAfter"] = {"threadUpserts": [{"id": temp, "text": "Reintroduced."}]}
    with pytest.raises(LLMBackendError, match="Consumed temporary memory ID"):
        await propose(store, project, a_id, OfflineInsertClient(reuse), count=3)


@pytest.mark.parametrize("status", ["needs_review", "legacy_unknown"])
async def test_insert_blocks_untrusted_a_and_terminal_selection(tmp_path, workspace_factory, status):
    store, project, _a_memory, _terminal, a_id = make_story(tmp_path, workspace_factory)
    path = manifest_path(store, project)
    raw = json.loads(path.read_text())
    raw["sequence"]["generations"][2]["storyMemoryStatus"] = status
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="blocked"):
        await propose(store, project, a_id)

    raw["sequence"]["generations"][2]["storyMemoryStatus"] = "trusted"
    path.write_text(json.dumps(raw))
    terminal_id = raw["sequence"]["generations"][-1]["generationId"]
    with pytest.raises(ValueError, match="nonterminal"):
        await propose(store, project, terminal_id)


async def test_insert_stale_fingerprint_missing_a_and_changed_b_write_nothing(tmp_path, workspace_factory):
    store, project, _a_memory, _terminal, a_id = make_story(tmp_path, workspace_factory)
    proposal = await propose(store, project, a_id)
    before = manifest_path(store, project).read_bytes()
    # A different saved field makes the signed bounded fingerprint stale.
    active = store.activate_generation(project.project_id, project.sequence.generations[3].generation_id)
    store.save_project(project.project_id, active.workspace, planning={"scenePlan": "Changed B"})
    with pytest.raises(StaleProjectSnapshotError):
        store.apply_story_plan_insert(project.project_id, proposal)
    assert manifest_path(store, project).read_bytes() != before

    proposal = await propose(store, project, a_id)
    before = manifest_path(store, project).read_bytes()
    ids = [g.generation_id for g in store.read_story_snapshot(project.project_id).generations]
    store.reorder_generations(project.project_id, [ids[0], ids[1], ids[2], ids[4], ids[3]])
    with pytest.raises(StaleProjectSnapshotError):
        store.apply_story_plan_insert(project.project_id, proposal)
    assert manifest_path(store, project).read_bytes() != before


async def test_insert_deleted_a_is_a_no_write_stale_authority_conflict(tmp_path, workspace_factory):
    store, project, _a_memory, _terminal, a_id = make_story(tmp_path, workspace_factory)
    proposal = await propose(store, project, a_id)
    project = store.activate_generation(project.project_id, a_id)
    store.delete_generation(project.project_id, a_id)
    before = manifest_path(store, project).read_bytes()
    with pytest.raises(StaleProjectSnapshotError, match="no longer exists"):
        store.apply_story_plan_insert(project.project_id, proposal)
    assert manifest_path(store, project).read_bytes() == before


async def test_insert_rejects_model_slot_mismatch_and_future_references(tmp_path, workspace_factory, image_asset_factory):
    store, project, _a_memory, _terminal, a_id = make_story(tmp_path, workspace_factory)
    snapshots = store.read_story_snapshot(project.project_id)
    # Put a future identity image on terminal and save it. It must not enter a
    # vision prompt or the Insert reference allow-list.
    terminal_id = snapshots.generations[-1].generation_id
    image = image_asset_factory("future-identity", role="environment")
    active = store.activate_generation(project.project_id, terminal_id)
    store.save_project(project.project_id, active.workspace.model_copy(update={"assets": [image]}))

    def malicious(response, context):
        response["slots"].reverse()
    with pytest.raises(LLMBackendError, match="slot count, identity or order"):
        await propose(store, project, a_id, OfflineInsertClient(malicious))

    client = OfflineInsertClient()
    proposal = await plan_insert(store, project.project_id, StoryInsertRequest(
        upstreamGenerationId=a_id, requestedCount=1, llm=LLM.model_copy(update={"supportsVision": True})), client)
    context = prompt_context(client)
    assert "future-identity" not in json.dumps(client.calls)
    assert context["referenceCutoff"]["inclusiveGenerationId"] == a_id


async def test_insert_cannot_use_future_slot_memory_and_recalls_only_relevant_history(tmp_path, workspace_factory, image_asset_factory):
    store = ProjectStore(tmp_path)
    def identity(asset_id, subject_id):
        return image_asset_factory(asset_id).model_copy(update={
            "subject_identity": SubjectIdentityReference(subjectId=subject_id),
        })

    project = store.create_project(workspace_factory(subjects=[{"id": "alice", "number": 1}, {"id": "bob", "number": 2}],
        assets=[identity("alice-history", "alice"), identity("bob-history", "bob")]))
    for label in ("G2", "G3", "G4", "G5"):
        project = store.create_generation(project.project_id, workspace=workspace_factory(creativeRequest=label))
    a_id = store.read_story_snapshot(project.project_id).generations[3].generation_id
    project = set_memory(store, project.project_id, a_id, StoryContinuityMemory(
        durableFacts=[{"id": BRIDGE_FACT, "text": "Alice remains recognizable.", "relatedSubjectIds": ["alice"]}]))
    snapshot = store.read_story_insert_snapshot(project.project_id, a_id, "Keep Alice consistent")
    assert {(reference.generation.generation_id, reference.asset_id) for reference in snapshot.historical_references} == {
        (store.read_story_snapshot(project.project_id).generations[0].generation_id, "alice-history")}

    def participates(response, _context): response["slots"][0]["participatingSubjectIds"] = ["alice"]
    proposal = await propose(store, project, a_id, OfflineInsertClient(participates), count=1)
    assert proposal.slots[0].reference_recommendations == []
    store.apply_story_plan_insert(project.project_id, proposal)
    inserted = store.read_story_snapshot(project.project_id).generations[4]
    assert [subject.id for subject in inserted.workspace.subjects] == ["alice"]
    assert [asset.role for asset in inserted.workspace.assets] == ["subject_identity"]

    def use_future_memory_slot(response, context):
        response["slots"][0]["memoryPatchAfter"] = {"factUpserts": [{"id": str(uuid4()), "text": "Unissued ID."}]}

    with pytest.raises(LLMBackendError, match="existing ID or issued temporary slot"):
        await propose(store, project, a_id, OfflineInsertClient(use_future_memory_slot), count=2)
