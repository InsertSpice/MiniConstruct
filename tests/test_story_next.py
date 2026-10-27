"""Production-path offline long-range memory, identity and transaction regressions."""
from copy import deepcopy
import json
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from miniconstruct.app import app
from miniconstruct.llm.client import LLMBackendError
from miniconstruct.models.api import LLMSettings
from miniconstruct.models.story_memory import StoryContinuityMemory, StoryMemoryPatch, apply_memory_patch
from miniconstruct.models.story_operations import StoryNextRequest, StoryNextResponse
from miniconstruct.models.workspace import ImagePayload
from miniconstruct.project_store import ProjectStore, ProjectStoreError, StaleProjectSnapshotError, MalformedProjectError
from miniconstruct.story_operations import plan_next

LLM = LLMSettings.model_validate({"modelId": "offline", "endpoint": {
    "id": "offline", "displayName": "Offline", "baseUrl": "http://never.test/v1"}})
FACT = "11111111-1111-4111-8111-111111111111"
THREAD = "22222222-2222-4222-8222-222222222222"


def memory():
    return StoryContinuityMemory(recentSummary="Alice is approaching the station.",
        durableFacts=[{"id": FACT, "text": "Alice carries the brass key from the first scene.", "relatedSubjectIds": ["alice"]}],
        openThreads=[{"id": THREAD, "text": "Alice promised to return the key to Carol.", "relatedSubjectIds": ["alice"]}])


class OfflineNextClient:
    """No HTTP implementation; used by production service and production benchmark."""
    def __init__(self, transform=None):
        self.calls = []
        self.transform = transform
        self.last_usage = None

    def __call__(self, settings):
        self.settings = settings
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
        result = {"slots": [{"planItemId": sid, "scenePlan": f"Next beat {i}",
            "dialogue": "Alice: The key.\n", "incomingState": "At the station", "intendedOutgoingState": "Moving inside",
            "relationshipToPrevious": "new_shot_preserve_continuity", "cameraIntent": "Wide shot",
            "shotCount": 1,
            "referenceRecommendations": [], "memoryPatchAfter": {}} for i, sid in enumerate(context["slotIds"])],
            "warnings": []}
        if self.transform:
            self.transform(result, context)
        return json.dumps(result)


def context_of(client):
    content = client.calls[-1][1]["content"]
    return json.loads(content[0]["text"] if isinstance(content, list) else content)


def create_story(tmp_path, workspace_factory, length=12, old_picture=None):
    store = ProjectStore(tmp_path)
    first = workspace_factory(subjects=[{"id": "alice", "number": 1}], nextSubjectNumber=2,
                              creativeRequest="HISTORICAL_ONLY", assets=[old_picture] if old_picture else [])
    project = store.create_project(first, story_idea="Return the key.", story_memory=memory())
    store.set_subject_narrative_name(project.project_id, "alice", "Alice")
    for _ in range(length - 1):
        project = store.create_generation(project.project_id, workspace=workspace_factory(creativeRequest="Local rhythm"))
    return store, project


async def propose(store, project, client=None, count=3):
    return await plan_next(store, project.project_id,
        StoryNextRequest(anchorGenerationId=project.sequence.generations[-1].generation_id,
                         requestedCount=count, llm=LLM), client or OfflineNextClient())


async def test_plan_next_sanitizes_unknown_memory_patch_fields(tmp_path, workspace_factory):
    store, project = create_story(tmp_path, workspace_factory, length=4)
    client = OfflineNextClient(lambda response, _context: response["slots"][0].__setitem__(
        "memoryPatchAfter", {"field": "invented value"}))
    proposal = await propose(store, project, client, count=1)
    assert len(client.calls) == 1
    assert proposal.slots[0].memory_patch_after.model_dump(by_alias=True) == {
        "recentSummary": None, "factUpserts": [], "resolvedFactIds": [], "threadUpserts": [], "resolvedThreadIds": [],
    }
    assert any("Generation 5" in warning and "field" in warning for warning in proposal.warnings)


def manifest_path(store, project):
    return store.root / f"project-{project.project_id}" / "project.json"


@pytest.mark.parametrize("length", [4, 12, 30, 60])
async def test_long_range_memory_fixed_window_no_old_picture(tmp_path, workspace_factory, image_asset_factory, length):
    store, project = create_story(tmp_path, workspace_factory, length, image_asset_factory(role="environment"))
    client = OfflineNextClient()
    request = StoryNextRequest(anchorGenerationId=project.active_generation_id, requestedCount=3,
                              llm=LLM.model_copy(update={"supports_vision": True, "story_planner_vision": True}))
    before = manifest_path(store, project).read_bytes()
    proposal = await plan_next(store, project.project_id, request, client)
    context = context_of(client)
    assert len(client.calls) == 1 and manifest_path(store, project).read_bytes() == before
    assert len(context["localContext"]) == 3
    assert len(context["slotIds"]) == len(proposal.slots) == 3
    assert context["storyMemory"] == memory().model_dump(by_alias=True)
    assert "HISTORICAL_ONLY" not in json.dumps(client.calls)
    assert "image_url" not in json.dumps(client.calls) and "reference.png" not in json.dumps(client.calls)
    assert [g["ordinal"] for g in context["localContext"]] == list(range(length - 2, length + 1))
    assert all("workspace" not in g and "creativeControls" not in g for g in context["localContext"])
    assert context["sequenceNarrativeCast"][0]["subjectId"] == "alice"
    assert all("existingGenerationId" not in item.model_dump(by_alias=True) for item in proposal.slots)
    assert all(item.memory_patch_after == StoryMemoryPatch() for item in proposal.slots)
    store.apply_story_plan_next(project.project_id, proposal)
    appended = store.read_story_snapshot(project.project_id).generations[length:]
    assert len(appended) == 3
    assert all(g.story_memory_after == memory() and g.story_memory_status == "trusted" for g in appended)


async def test_atomic_append_updates_fact_resolves_thread_preserves_active_and_dormant(tmp_path, workspace_factory):
    store, project = create_story(tmp_path, workspace_factory)
    old_ids = [g.generation_id for g in project.sequence.generations]
    store.activate_generation(project.project_id, old_ids[0])
    def change(result, context):
        result["slots"][-1]["memoryPatchAfter"] = {"factUpserts": [{"id": FACT, "text": "Alice returned the brass key.", "relatedSubjectIds": ["alice"]}],
            "resolvedThreadIds": [THREAD], "threadUpserts": [{"id": context["memoryCreationIds"]["newThreadIds"][0], "text": "Find the signal source."}]}
    proposal = await propose(store, project, OfflineNextClient(change))
    applied = store.apply_story_plan_next(project.project_id, proposal)
    after = store.read_story_snapshot(project.project_id)
    assert applied.active_generation_id == old_ids[0]
    assert [g.generation_id for g in after.generations[:12]] == old_ids
    new = after.generations[12:]
    assert [g.planning.scene_plan for g in new] == [f"Next beat {i}" for i in range(3)]
    assert all(g.workspace.dialogue == "Alice: The key.\n" for g in new)
    assert all(not g.workspace.subjects and not g.workspace.assets for g in new)
    assert all(str(UUID(g.generation_id)) == g.generation_id and g.generation_id not in proposal.authority.slot_ids for g in new)
    terminal_memory = store._story_memory(after.manifest)
    assert terminal_memory.durable_facts[0].id == FACT
    assert len(terminal_memory.durable_facts) == 1
    assert terminal_memory.open_threads[0].id not in proposal.authority.new_thread_ids
    context = store.read_story_operation_snapshot(project.project_id, new[-1].generation_id).context
    assert THREAD not in json.dumps(context["storyMemory"])
    assert "returned" in context["storyMemory"]["durableFacts"][0]["text"]
    assert applied.sequence.subjects[0].id == "alice"
    with pytest.raises(ValueError):
        store.apply_story_plan_next(project.project_id, proposal)


async def test_memory_stales_but_far_history_edit_does_not(tmp_path, workspace_factory):
    store, project = create_story(tmp_path, workspace_factory, 60)
    proposal = await propose(store, project)
    old = store.activate_generation(project.project_id, project.sequence.generations[1].generation_id)
    store.save_project(project.project_id, old.workspace.model_copy(update={"creative_request": "Unrelated earlier edit"}))
    applied = store.apply_story_plan_next(project.project_id, proposal)
    assert applied.active_generation_id == old.active_generation_id
    proposal = await propose(store, applied)
    updated = applied.story_memory_after.model_copy(update={"recent_summary": "User correction"})
    store.save_project(project.project_id, applied.workspace, story_memory_generation_id=applied.active_generation_id, story_memory=updated)
    before = manifest_path(store, project).read_bytes()
    with pytest.raises(ValueError, match="confirm"):
        store.apply_story_plan_next(project.project_id, proposal)
    assert manifest_path(store, project).read_bytes() == before


async def test_rename_keeps_memory_links_and_stales_relevant_cast(tmp_path, workspace_factory):
    store, project = create_story(tmp_path, workspace_factory)
    proposal = await propose(store, project)
    renamed = store.set_subject_narrative_name(project.project_id, "alice", "Alicia")
    assert renamed.story_memory_after == memory()
    with pytest.raises(StaleProjectSnapshotError):
        store.apply_story_plan_next(project.project_id, proposal)
    client = OfflineNextClient()
    await propose(store, project, client)
    cast = context_of(client)["sequenceNarrativeCast"]
    assert cast[0]["subjectId"] == "alice" and cast[0]["narrativeName"] == "Alicia"
    assert "narrativeAliases" not in cast[0]


@pytest.mark.parametrize("damage", ["unknown", "duplicate", "subject", "canonical", "slots", "order", "envelope", "reference"])
async def test_invalid_apply_rolls_back(tmp_path, workspace_factory, damage):
    store, project = create_story(tmp_path, workspace_factory, 4)
    proposal = await propose(store, project)
    if damage in {"unknown", "duplicate"}:
        proposal.slots[-1].memory_patch_after.resolved_thread_ids = [str(uuid4())] if damage == "unknown" else [THREAD, THREAD]
    elif damage in {"subject", "canonical"}:
        proposal.slots[-1].memory_patch_after = StoryMemoryPatch(factUpserts=[{"id": FACT if damage == "subject" else str(uuid4()),
            "text": "Changed", "relatedSubjectIds": ["unknown"] if damage == "subject" else []}])
    elif damage == "slots":
        proposal.slots[0].plan_item_id = str(uuid4())
    elif damage == "order":
        proposal.slots.reverse()
    elif damage == "envelope":
        proposal.authority.slot_ids.reverse()
    elif damage == "reference":
        from miniconstruct.models.story_planning import StoryReferenceRecommendation
        proposal.slots[0].reference_recommendations = [StoryReferenceRecommendation(
            sourceGenerationId=project.active_generation_id, assetId="unknown", recommendation="Use")]
    before = manifest_path(store, project).read_bytes()
    with pytest.raises(ValueError):
        store.apply_story_plan_next(project.project_id, proposal)
    assert manifest_path(store, project).read_bytes() == before


@pytest.mark.parametrize("failure", ["materialize", "workspace", "replace"])
async def test_preparation_failure_leaves_every_file_unchanged(tmp_path, workspace_factory, monkeypatch, failure):
    store, project = create_story(tmp_path, workspace_factory, 4)
    proposal = await propose(store, project)
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    def fail(*args, **kwargs):
        raise ProjectStoreError("Injected failure")
    method = {"materialize": "_materialize_story_references", "workspace": "_validate_persisted_workspace", "replace": "_atomic_write"}[failure]
    monkeypatch.setattr(store, method, fail)
    with pytest.raises(ProjectStoreError):
        store.apply_story_plan_next(project.project_id, proposal)
    assert {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("damage", ["extra", "reorder", "omit", "invent", "memory", "mode"])
async def test_model_response_cannot_change_authority(tmp_path, workspace_factory, damage):
    store, project = create_story(tmp_path, workspace_factory, 4)
    def change(result, context):
        if damage == "extra": result["sequenceContext"] = "Unauthorized"
        elif damage == "reorder": result["slots"].reverse()
        elif damage == "omit": result["slots"].pop()
        elif damage == "invent": result["slots"][0]["generationId"] = str(uuid4())
        elif damage == "mode": result["slots"][0]["mode"] = "Ref2VA"
        elif damage == "memory": result["slots"][-1]["memoryPatchAfter"] = {"resolvedFactIds": [str(uuid4())]}
    client = OfflineNextClient(change)
    before = manifest_path(store, project).read_bytes()
    with pytest.raises(LLMBackendError):
        await propose(store, project, client)
    assert len(client.calls) == 1 and manifest_path(store, project).read_bytes() == before


def test_memory_bounds_and_resolution_semantics():
    for value in [{"recentSummary": "x" * 6001}, {"durableFacts": [{"id": str(uuid4()), "text": "x"}] * 65},
                  {"openThreads": [{"id": str(uuid4()), "text": "x" * 501}]}, {"scratchpad": "hidden"}]:
        with pytest.raises(ValueError): StoryContinuityMemory.model_validate(value)
    assert apply_memory_patch(memory(), StoryMemoryPatch(), [], [], {"alice"}) == memory()


def large_memory():
    return StoryContinuityMemory(
        recentSummary="S" * 6000,
        durableFacts=[{"id": str(uuid4()), "text": f"Fact {i} " + "x" * 480, "relatedSubjectIds": []}
                       for i in range(64)],
        openThreads=[{"id": str(uuid4()), "text": f"Thread {i} " + "y" * 480, "relatedSubjectIds": []}
                     for i in range(64)],
    )


@pytest.mark.parametrize("context_window", [65536, 131072])
async def test_known_context_windows_allow_full_large_story_memory(tmp_path, workspace_factory, context_window):
    store, project = create_story(tmp_path, workspace_factory, 4)
    full_memory = large_memory()
    project = store.save_project(project.project_id, project.workspace, story_memory_generation_id=project.active_generation_id, story_memory=full_memory)
    client = OfflineNextClient()
    request = StoryNextRequest(anchorGenerationId=project.active_generation_id, requestedCount=3,
                               llm=LLM.model_copy(update={"context_window": context_window}))
    await plan_next(store, project.project_id, request, client)
    context = context_of(client)
    assert len(client.calls) == 1
    assert context["storyMemory"] == full_memory.model_dump(by_alias=True)
    assert context["storyMemory"]["recentSummary"] == "S" * 6000
    assert len(context["storyMemory"]["durableFacts"]) == 64
    assert len(context["storyMemory"]["openThreads"]) == 64


async def test_small_known_context_warns_but_reaches_provider_without_truncating_memory(tmp_path, workspace_factory):
    store, project = create_story(tmp_path, workspace_factory, 4)
    full_memory = large_memory()
    project = store.save_project(project.project_id, project.workspace, story_memory_generation_id=project.active_generation_id, story_memory=full_memory)
    client = OfflineNextClient()
    request = StoryNextRequest(anchorGenerationId=project.active_generation_id, requestedCount=3,
                               llm=LLM.model_copy(update={"context_window": 16384}))
    proposal = await plan_next(store, project.project_id, request, client)
    assert len(client.calls) == 1
    assert any("estimate is approximate" in warning for warning in proposal.warnings)
    assert store.read_story_snapshot(project.project_id).manifest.sequence.generations[-1].story_memory_after == full_memory


async def test_unknown_and_modest_context_windows_are_allowed(tmp_path, workspace_factory):
    store, project = create_story(tmp_path, workspace_factory, 4)
    project = store.save_project(project.project_id, project.workspace, story_memory_generation_id=project.active_generation_id, story_memory=large_memory())
    client = OfflineNextClient()
    await plan_next(store, project.project_id, StoryNextRequest(
        anchorGenerationId=project.active_generation_id, requestedCount=3, llm=LLM), client)
    assert len(client.calls) == 1

    modest = store.save_project(project.project_id, project.workspace, story_memory_generation_id=project.active_generation_id, story_memory=memory())
    client = OfflineNextClient()
    await plan_next(store, modest.project_id, StoryNextRequest(
        anchorGenerationId=modest.active_generation_id, requestedCount=3,
        llm=LLM.model_copy(update={"context_window": 16384})), client)
    assert len(client.calls) == 1


@pytest.mark.parametrize("length,count", [(1, 1), (2, 4)])
async def test_short_sequence_windows_and_batch_extremes(tmp_path, workspace_factory, length, count):
    store, project = create_story(tmp_path, workspace_factory, length)
    client = OfflineNextClient()
    proposal = await propose(store, project, client, count)
    assert len(context_of(client)["localContext"]) == length
    assert len(proposal.slots) == count
    assert store.apply_story_plan_next(project.project_id, proposal).generation_count == length + count


def test_memory_links_block_subject_deletion_and_mutated_model_bounds(tmp_path, workspace_factory):
    from miniconstruct.project_store import SequenceSubjectInUseError
    store, project = create_story(tmp_path, workspace_factory, 4)
    before = manifest_path(store, project).read_bytes()
    with pytest.raises(SequenceSubjectInUseError): store.delete_sequence_subject(project.project_id, "alice")
    invalid = memory()
    invalid.durable_facts[0].text = "x" * 501
    with pytest.raises(ValueError): store.save_project(project.project_id, project.workspace, story_memory_generation_id=project.active_generation_id, story_memory=invalid)
    assert manifest_path(store, project).read_bytes() == before


@pytest.mark.parametrize("version", range(1, 8))
def test_frozen_legacy_reads_nonmutating_then_upgrade(tmp_path, workspace_factory, version):
    store, project = create_story(tmp_path, workspace_factory, 1)
    path = manifest_path(store, project)
    raw = json.loads(path.read_text())
    raw["schemaVersion"] = version
    raw["sequence"].pop("storyMemory", None)
    for legacy_generation in raw["sequence"]["generations"]:
        legacy_generation.pop("storyMemoryAfter", None)
        legacy_generation.pop("storyMemoryStatus", None)
    if version < 7:
        for s in raw["sequence"]["subjects"]:
            s.pop("narrativeName"); s.pop("narrativeAliases")
    if version < 6:
        raw["sequence"].pop("subjects"); raw["sequence"].pop("nextSubjectNumber")
    if version < 5: raw.pop("storyIdea")
    if version == 1:
        g = raw.pop("sequence")["generations"][0]
        raw.update(workspace=g["workspace"], assetFiles=g["assetFiles"])
    elif version < 4:
        for g in raw["sequence"]["generations"]:
            g.pop("planning")
            if version == 2: g.pop("continuity")
        if version == 2: raw["sequence"].pop("continuityContext")
    path.write_text(json.dumps(raw))
    before = path.read_bytes()
    loaded = store.load_project(project.project_id)
    assert path.read_bytes() == before and loaded.story_memory_after == StoryContinuityMemory()
    saved = store.save_project(project.project_id, loaded.workspace)
    assert json.loads(path.read_text())["schemaVersion"] == 17
    assert saved.workspace.schema_version == 1


def test_v8_memory_roundtrip_duplicate_and_invalid_links(tmp_path, workspace_factory):
    store, project = create_story(tmp_path, workspace_factory, 4)
    assert store.load_project(project.project_id).story_memory_after == memory()
    duplicate = store.duplicate_project(project.project_id, "Copy")
    assert duplicate.story_memory_after == memory()
    path = manifest_path(store, project)
    raw = json.loads(path.read_text()); raw["sequence"]["generations"][-1]["storyMemoryAfter"]["durableFacts"][0]["relatedSubjectIds"] = ["missing"]
    path.write_text(json.dumps(raw)); before = path.read_bytes()
    with pytest.raises(MalformedProjectError): store.load_project(project.project_id)
    assert path.read_bytes() == before


async def test_terminal_and_count_guard_before_model(tmp_path, workspace_factory):
    store, project = create_story(tmp_path, workspace_factory, 4)
    client = OfflineNextClient()
    request = StoryNextRequest(anchorGenerationId=project.sequence.generations[0].generation_id, requestedCount=1, llm=LLM)
    with pytest.raises(ValueError, match="terminal"):
        await plan_next(store, project.project_id, request, client)
    assert not client.calls
    for count in [0, 5, 2.5, True]:
        with pytest.raises(ValueError): StoryNextRequest(anchorGenerationId=project.active_generation_id, requestedCount=count, llm=LLM)


def test_memory_api_save_save_as_and_invalid_subject(tmp_path, workspace_factory, monkeypatch):
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(tmp_path))
    client = TestClient(app)
    workspace = workspace_factory(subjects=[{"id": "alice", "number": 1}], nextSubjectNumber=2).model_dump(by_alias=True)
    response = client.post("/api/projects", json={"workspace": workspace, "storyMemory": memory().model_dump(by_alias=True)})
    assert response.status_code == 201
    project = response.json()
    assert project["storyMemoryAfter"]["durableFacts"][0]["id"] == FACT
    copied = client.post(f'/api/projects/{project["id"]}/save-as', json={"name": "Copy"})
    assert copied.status_code == 201 and copied.json()["storyMemoryAfter"] == project["storyMemoryAfter"]
    corrected = deepcopy(project["storyMemoryAfter"])
    corrected["recentSummary"] = "User correction"
    saved = client.put(f'/api/projects/{project["id"]}', json={"workspace": workspace, "storyMemory": corrected, "storyMemoryGenerationId": project["sequence"]["activeGenerationId"], "expectedProjectFingerprint": project["projectFingerprint"]})
    assert saved.status_code == 200 and saved.json()["storyMemoryAfter"] == corrected
    path = tmp_path / f'project-{project["id"]}' / "project.json"
    project = saved.json()
    before = path.read_bytes()
    corrected["durableFacts"][0]["relatedSubjectIds"] = ["missing"]
    rejected = client.put(f'/api/projects/{project["id"]}', json={"workspace": workspace, "storyMemory": corrected, "storyMemoryGenerationId": project["sequence"]["activeGenerationId"], "expectedProjectFingerprint": project["projectFingerprint"]})
    assert rejected.status_code == 400 and path.read_bytes() == before


@pytest.mark.parametrize("vision", [False, True])
async def test_local_reference_projection_and_safe_materialization(tmp_path, workspace_factory, image_asset_factory, vision):
    store, project = create_story(tmp_path, workspace_factory, 4)
    picture = image_asset_factory(role="subject_identity")
    data = picture.model_dump(by_alias=True)
    data["subjectIdentity"] = {"subjectId": "alice"}
    workspace = workspace_factory(mode="Ref2VA", assets=[data], subjects=[{"id": "alice", "number": 1}], nextSubjectNumber=2)
    project = store.save_project(project.project_id, workspace)
    def recommend(result, context):
        result["slots"][0]["referenceRecommendations"] = [{"sourceGenerationId": project.active_generation_id,
            "assetId": picture.id, "recommendation": "Identity evidence"}]
    client = OfflineNextClient(recommend)
    proposal = await plan_next(store, project.project_id,
        StoryNextRequest(anchorGenerationId=project.active_generation_id, requestedCount=3,
                         llm=LLM.model_copy(update={"supports_vision": vision, "story_planner_vision": vision})), client)
    assert ("image_url" in json.dumps(client.calls)) == vision
    context = context_of(client)
    assert context["localContext"][-1]["assets"][0]["id"] == picture.id
    assert "data:image" not in json.dumps(context)
    store.apply_story_plan_next(project.project_id, proposal)
    new = store.read_story_snapshot(project.project_id).generations[-3:]
    assert new[0].workspace.subjects[0].id == "alice"
    assert len(new[0].workspace.assets) == 1
    assert new[0].workspace.assets[0].id != picture.id
    assert new[0].workspace.assets[0].image == picture.image
    assert all(g.workspace.mode == "Ref2VA" for g in new)
    assert all(not g.workspace.assets and not g.workspace.subjects for g in new[1:])


async def test_long_range_subject_identity_reference_recall_is_bounded_and_applies(tmp_path, workspace_factory, image_asset_factory):
    identity = image_asset_factory(asset_id="alice-identity", role="subject_identity")
    data = identity.model_dump(by_alias=True)
    data["subjectIdentity"] = {"subjectId": "alice", "focus": "face", "view": "front", "layout": "single_view"}
    store, project = create_story(tmp_path, workspace_factory, 60, data)
    historical_id = project.sequence.generations[0].generation_id

    def recommend(result, _context):
        result["slots"][0]["referenceRecommendations"] = [{
            "sourceGenerationId": historical_id, "assetId": "alice-identity", "recommendation": "Returning identity",
        }]

    client = OfflineNextClient(recommend)
    proposal = await plan_next(store, project.project_id, StoryNextRequest(
        anchorGenerationId=project.active_generation_id, requestedCount=3,
        llm=LLM.model_copy(update={"supports_vision": True, "story_planner_vision": True})), client)
    context = context_of(client)
    assert [item["ordinal"] for item in context["localContext"]] == [58, 59, 60]
    assert "HISTORICAL_ONLY" not in json.dumps(context)
    assert [(item["sourceGenerationId"], item["asset"]["id"]) for item in context["historicalReferenceContext"]] == [
        (historical_id, "alice-identity")]
    assert (historical_id, "alice-identity") in store.read_story_operation_snapshot(
        project.project_id, project.active_generation_id).source.allowed_refs
    assert len([part for part in client.calls[0][1]["content"] if part.get("type") == "image_url"]) == 1
    store.apply_story_plan_next(project.project_id, proposal)
    applied = store.read_story_snapshot(project.project_id).generations[-3:]
    assert [subject.id for subject in applied[0].workspace.subjects] == ["alice"]
    assert applied[0].workspace.assets[0].image == identity.image
    assert all(not generation.workspace.assets and not generation.workspace.subjects for generation in applied[1:])


async def test_plan_next_participation_carries_historical_identity_without_recommendation(tmp_path, workspace_factory, image_asset_factory):
    identity = image_asset_factory(asset_id="alice-identity", role="subject_identity")
    data = identity.model_dump(by_alias=True); data["subjectIdentity"] = {"subjectId": "alice"}
    store, project = create_story(tmp_path, workspace_factory, 30, data)

    def participates(result, _context): result["slots"][0]["participatingSubjectIds"] = ["alice"]
    proposal = await propose(store, project, OfflineNextClient(participates), count=1)
    assert proposal.slots[0].reference_recommendations == []
    store.apply_story_plan_next(project.project_id, proposal)
    generation = store.read_story_snapshot(project.project_id).generations[-1]
    assert [subject.id for subject in generation.workspace.subjects] == ["alice"]
    assert [asset.role for asset in generation.workspace.assets] == ["subject_identity"]


async def test_historical_recall_requires_relevance_and_aliases_still_resolve(tmp_path, workspace_factory, image_asset_factory):
    identity = image_asset_factory(asset_id="alice-identity")
    data = identity.model_dump(by_alias=True); data["subjectIdentity"] = {"subjectId": "alice"}
    store, project = create_story(tmp_path, workspace_factory, 30, data)
    historical_id = project.sequence.generations[0].generation_id
    project = store.save_project(project.project_id, project.workspace, story_memory_generation_id=project.active_generation_id, story_memory=StoryContinuityMemory())
    snapshot = store.read_story_operation_snapshot(project.project_id, project.active_generation_id)
    assert snapshot.context["historicalReferenceContext"] == []
    assert (historical_id, "alice-identity") not in snapshot.source.allowed_refs

    renamed = store.set_subject_narrative_name(project.project_id, "alice", "Alicia")
    alias_memory = StoryContinuityMemory(durableFacts=[{"id": FACT, "text": "Alice must return soon."}])
    renamed = store.save_project(renamed.project_id, renamed.workspace, story_memory_generation_id=renamed.active_generation_id, story_memory=alias_memory)
    snapshot = store.read_story_operation_snapshot(renamed.project_id, renamed.active_generation_id)
    assert snapshot.context["sequenceNarrativeCast"][0]["narrativeName"] == "Alicia"
    assert "narrativeAliases" not in snapshot.context["sequenceNarrativeCast"][0]
    assert [(item["sourceGenerationId"], item["asset"]["id"]) for item in snapshot.context["historicalReferenceContext"]] == [
        (historical_id, "alice-identity")]


async def test_historical_payload_dedup_prefers_local_and_nonvision_keeps_metadata(tmp_path, workspace_factory, image_asset_factory):
    identity = image_asset_factory(asset_id="old-alice")
    data = identity.model_dump(by_alias=True); data["subjectIdentity"] = {"subjectId": "alice"}
    store, project = create_story(tmp_path, workspace_factory, 4, data)
    local = identity.model_dump(by_alias=True); local["id"] = "local-alice"; local["subjectIdentity"] = {"subjectId": "alice"}
    project = store.save_project(project.project_id, workspace_factory(assets=[local], subjects=[{"id": "alice", "number": 1}], nextSubjectNumber=2))
    snapshot = store.read_story_operation_snapshot(project.project_id, project.active_generation_id)
    assert snapshot.context["historicalReferenceContext"] == []
    client = OfflineNextClient()
    await plan_next(store, project.project_id, StoryNextRequest(anchorGenerationId=project.active_generation_id,
        requestedCount=1, llm=LLM.model_copy(update={"supports_vision": False})), client)
    assert "historicalReferenceContext" in context_of(client)
    assert isinstance(client.calls[0][1]["content"], str)


async def test_distinct_historical_identity_payloads_with_same_filename_are_not_deduplicated(tmp_path, workspace_factory, image_asset_factory):
    face = image_asset_factory(asset_id="alice-face", filename="alice.png")
    face_data = face.model_dump(by_alias=True); face_data["subjectIdentity"] = {"subjectId": "alice", "focus": "face"}
    full = image_asset_factory(asset_id="alice-full", filename="alice.png")
    full_data = full.model_dump(by_alias=True)
    from base64 import b64encode
    from io import BytesIO
    from PIL import Image
    pixels = BytesIO()
    Image.new("RGB", (2, 1), "blue").save(pixels, format="PNG")
    full_data["image"] = ImagePayload(data_url="data:image/png;base64," + b64encode(pixels.getvalue()).decode(), width=2, height=1).model_dump()
    full_data["subjectIdentity"] = {"subjectId": "alice", "focus": "full_body"}
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory(subjects=[{"id": "alice", "number": 1}], nextSubjectNumber=2,
        assets=[face_data, full_data]), story_memory=memory())
    store.set_subject_narrative_name(project.project_id, "alice", "Alice")
    for _ in range(29):
        project = store.create_generation(project.project_id, workspace=workspace_factory(creativeRequest="Unrelated recent activity"))
    client = OfflineNextClient()
    await plan_next(store, project.project_id, StoryNextRequest(anchorGenerationId=project.active_generation_id,
        requestedCount=1, llm=LLM.model_copy(update={"supports_vision": True, "story_planner_vision": True})), client)
    context = context_of(client)
    assert {item["asset"]["id"] for item in context["historicalReferenceContext"]} == {"alice-face", "alice-full"}
    assert len([part for part in client.calls[0][1]["content"] if part.get("type") == "image_url"]) == 2


async def test_local_generic_same_pixels_does_not_erase_historical_identity(tmp_path, workspace_factory, image_asset_factory):
    identity = image_asset_factory(asset_id="alice-identity", role="subject_identity")
    historical = identity.model_dump(by_alias=True); historical["subjectIdentity"] = {"subjectId": "alice"}
    store, project = create_story(tmp_path, workspace_factory, 60, historical)
    generic = identity.model_dump(by_alias=True); generic["id"] = "local-generic"; generic["role"] = "general_visual"
    project = store.save_project(project.project_id, workspace_factory(assets=[generic]))
    snapshot = store.read_story_operation_snapshot(project.project_id, project.active_generation_id)
    assert [(item["asset"]["id"], item["asset"]["role"]) for item in snapshot.context["historicalReferenceContext"]] == [
        ("alice-identity", "subject_identity")]
    client = OfflineNextClient()
    await plan_next(store, project.project_id, StoryNextRequest(anchorGenerationId=project.active_generation_id,
        requestedCount=1, llm=LLM.model_copy(update={"supports_vision": True, "story_planner_vision": True})), client)
    content = client.calls[0][1]["content"]
    assert len([part for part in content if part.get("type") == "image_url"]) == 1
    assert sum(1 for part in content if part.get("type") == "text" and "visual input" in part.get("text", "")) == 2


async def test_comparison_scale_historical_recall_apply_and_role_aware_staleness(tmp_path, workspace_factory, image_asset_factory):
    identity = image_asset_factory(asset_id="alice-identity", role="subject_identity")
    identity_data = identity.model_dump(by_alias=True); identity_data["subjectIdentity"] = {"subjectId": "alice"}
    comparison = image_asset_factory(asset_id="alice-bob-scale", role="character_comparison_scale")
    comparison_data = comparison.model_dump(by_alias=True)
    comparison_data["comparisonSubjects"] = ["alice", "bob"]
    comparison_data["subjectIdentity"] = {"subjectId": "alice"}
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory(
        subjects=[{"id": "alice", "number": 1}, {"id": "bob", "number": 2}, {"id": "carol", "number": 3}], nextSubjectNumber=4,
        assets=[identity_data, comparison_data]), story_memory=memory())
    for _ in range(7):
        project = store.create_generation(project.project_id, workspace=workspace_factory(creativeRequest="Recent activity"))
    source = project.sequence.generations[0].generation_id

    def recommend(result, _context):
        result["slots"][0]["referenceRecommendations"] = [
            {"sourceGenerationId": source, "assetId": "alice-identity", "recommendation": "Identity"},
            {"sourceGenerationId": source, "assetId": "alice-bob-scale", "recommendation": "Scale"},
        ]
    client = OfflineNextClient(recommend)
    proposal = await plan_next(store, project.project_id, StoryNextRequest(anchorGenerationId=project.active_generation_id,
        requestedCount=1, llm=LLM.model_copy(update={"supports_vision": True, "story_planner_vision": True})), client)
    operation = store.read_story_operation_snapshot(project.project_id, project.active_generation_id)
    assert next(item for item in operation.context["historicalReferenceContext"]
                if item["asset"]["id"] == "alice-bob-scale")["asset"]["role"] == "character_comparison_scale"
    assert {(source, "alice-identity"), (source, "alice-bob-scale")} <= operation.source.allowed_refs
    assert len([part for part in client.calls[0][1]["content"] if part.get("type") == "image_url"]) == 1
    assert (source, "alice-bob-scale") in operation.source.allowed_refs
    store.apply_story_plan_next(project.project_id, proposal)
    applied = store.read_story_snapshot(project.project_id).generations[-1]
    assert {subject.id for subject in applied.workspace.subjects} == {"alice", "bob"}
    assert {asset.role for asset in applied.workspace.assets} == {"subject_identity", "character_comparison_scale"}

    # Re-propose, then change the projected comparison association: the bounded
    # fingerprint must reject the old proposal without reading identity metadata.
    project = store.load_project(project.project_id)
    terminal = project.sequence.generations[-1].generation_id
    proposal = await plan_next(store, project.project_id, StoryNextRequest(anchorGenerationId=terminal,
        requestedCount=1, llm=LLM), OfflineNextClient())
    old = store.activate_generation(project.project_id, source)
    changed = old.workspace.model_copy(deep=True)
    changed.assets[1].comparison_subject_ids = ["alice", "carol"]
    store.save_project(project.project_id, changed)
    with pytest.raises(StaleProjectSnapshotError):
        store.apply_story_plan_next(project.project_id, proposal)


async def test_exact_semantic_duplicate_chooses_newest_historical_occurrence(tmp_path, workspace_factory, image_asset_factory):
    identity = image_asset_factory(asset_id="alice-identity")
    data = identity.model_dump(by_alias=True); data["subjectIdentity"] = {"subjectId": "alice"}
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory(subjects=[{"id": "alice", "number": 1}], nextSubjectNumber=2,
        assets=[data]), story_memory=memory())
    first = project.sequence.generations[0].generation_id
    project = store.create_generation(project.project_id, workspace=workspace_factory(
        subjects=[{"id": "alice", "number": 1}], nextSubjectNumber=2, assets=[data]))
    newest = project.sequence.generations[1].generation_id
    for _ in range(4):
        project = store.create_generation(project.project_id, workspace=workspace_factory(creativeRequest="Recent"))
    snapshot = store.read_story_operation_snapshot(project.project_id, project.active_generation_id)
    assert [(item["sourceGenerationId"], item["asset"]["id"]) for item in snapshot.context["historicalReferenceContext"]] == [
        (newest, "alice-identity")]
    assert first != newest


async def test_projected_historical_reference_stales_but_unrelated_history_does_not(tmp_path, workspace_factory, image_asset_factory):
    identity = image_asset_factory(asset_id="alice-identity")
    data = identity.model_dump(by_alias=True); data["subjectIdentity"] = {"subjectId": "alice"}
    store, project = create_story(tmp_path, workspace_factory, 30, data)
    proposal = await propose(store, project)
    first = store.activate_generation(project.project_id, project.sequence.generations[0].generation_id)
    changed = first.workspace.model_copy(deep=True); changed.assets[0].notes = "Corrected identity evidence"
    store.save_project(project.project_id, changed)
    with pytest.raises(StaleProjectSnapshotError):
        store.apply_story_plan_next(project.project_id, proposal)

    current = store.load_project(project.project_id)
    proposal = await propose(store, current)
    unrelated = store.activate_generation(current.project_id, current.sequence.generations[1].generation_id)
    store.save_project(current.project_id, unrelated.workspace.model_copy(update={"creative_request": "Unrelated historical edit"}))
    applied = store.apply_story_plan_next(current.project_id, proposal)
    assert applied.generation_count == 33


async def test_invented_plan_next_references_are_sanitized_before_apply(tmp_path, workspace_factory):
    store, project = create_story(tmp_path, workspace_factory, 4)

    def invent(result, context):
        result["slots"][0]["referenceRecommendations"] = [
            {"sourceGenerationId": "courier_character_model", "assetId": "sealed_letter_prop", "recommendation": "Invented"},
        ]

    proposal = await propose(store, project, OfflineNextClient(invent))
    assert proposal.slots[0].reference_recommendations == []
    store.apply_story_plan_next(project.project_id, proposal)
    assert all(not generation.workspace.assets for generation in store.read_story_snapshot(project.project_id).generations[-3:])


async def test_plan_next_keeps_valid_reference_and_drops_invented_duplicates(tmp_path, workspace_factory, image_asset_factory):
    store, project = create_story(tmp_path, workspace_factory, 4)
    picture = image_asset_factory(asset_id="shared", role="environment")
    project = store.save_project(project.project_id, workspace_factory(assets=[picture.model_dump(by_alias=True)]))
    source = project.active_generation_id

    def mixed(result, context):
        result["slots"][0]["referenceRecommendations"] = [
            {"sourceGenerationId": source, "assetId": "shared", "recommendation": "Keep"},
            {"sourceGenerationId": source, "assetId": "shared", "recommendation": "Drop duplicate"},
            {"sourceGenerationId": "city_environment", "assetId": "quiet_city_env", "recommendation": "Drop invented"},
        ]

    proposal = await propose(store, project, OfflineNextClient(mixed))
    assert [recommendation.recommendation for recommendation in proposal.slots[0].reference_recommendations] == ["Keep"]
    store.apply_story_plan_next(project.project_id, proposal)
    materialized = store.read_story_snapshot(project.project_id).generations[-3].workspace.assets
    assert len(materialized) == 1 and materialized[0].image == picture.image


async def test_plan_next_discards_unsupported_complete_memory_field_names(tmp_path, workspace_factory):
    store, project = create_story(tmp_path, workspace_factory, 4)

    def qwen_shape(result, context):
        result["slots"][-1]["memoryPatchAfter"] = {"durableFacts": [], "openThreads": []}

    client = OfflineNextClient(qwen_shape)
    proposal = await propose(store, project, client)
    assert proposal.slots[-1].memory_patch_after.fact_upserts == []
    assert proposal.slots[-1].memory_patch_after.thread_upserts == []
    assert any("durableFacts" in warning and "openThreads" in warning for warning in proposal.warnings)
    assert len(client.calls) == 1


async def test_plan_next_prompt_names_read_only_memory_and_exact_patch_keys(tmp_path, workspace_factory):
    store, project = create_story(tmp_path, workspace_factory, 60)
    client = OfflineNextClient()
    await propose(store, project, client)
    system = client.calls[0][0]["content"]
    assert "memoryPatchAfter must be {} when no memory update is needed" in system
    assert "recentSummary, factUpserts, resolvedFactIds, threadUpserts, and resolvedThreadIds" in system
    assert "Never use generic placeholders such as field, key, name, or value" in system
    assert "durableFacts and openThreads in the supplied" in system
    assert "localContext[].assets is the complete set of reference" in system
    assert "descriptive nouns in Scene Plan or Story Memory are not asset IDs" in system


async def test_local_asset_change_stales_and_late_failure_rolls_back(tmp_path, workspace_factory, image_asset_factory, monkeypatch):
    store, project = create_story(tmp_path, workspace_factory, 4)
    project = store.save_project(project.project_id, workspace_factory(assets=[image_asset_factory(role="environment")]))
    proposal = await propose(store, project)
    workspace = project.workspace.model_copy(deep=True)
    workspace.assets[0].notes = "Changed available evidence"
    project = store.save_project(project.project_id, workspace)
    with pytest.raises(StaleProjectSnapshotError): store.apply_story_plan_next(project.project_id, proposal)
    proposal = await propose(store, project)
    before = manifest_path(store, project).read_bytes()
    original = store._materialize_story_references
    calls = 0
    def fail_second(*args):
        nonlocal calls
        calls += 1
        if calls == 2: raise ProjectStoreError("second reference materialization failed")
        return original(*args)
    monkeypatch.setattr(store, "_materialize_story_references", fail_second)
    with pytest.raises(ProjectStoreError): store.apply_story_plan_next(project.project_id, proposal)
    assert calls == 2 and manifest_path(store, project).read_bytes() == before


async def test_memory_overflow_rejects_generation_creation(tmp_path, workspace_factory):
    store, project = create_story(tmp_path, workspace_factory, 4)
    full = memory()
    full.durable_facts.extend(type(full.durable_facts[0])(id=str(uuid4()), text=f"Fact {i}") for i in range(63))
    project = store.save_project(project.project_id, project.workspace, story_memory_generation_id=project.active_generation_id, story_memory=full)
    proposal = await propose(store, project)
    proposal.slots[-1].memory_patch_after = StoryMemoryPatch(factUpserts=[{"id": proposal.authority.new_fact_ids[-1], "text": "One too many"}])
    before = manifest_path(store, project).read_bytes()
    with pytest.raises(ValueError): store.apply_story_plan_next(project.project_id, proposal)
    assert manifest_path(store, project).read_bytes() == before


async def test_operation_api_and_replay_rejection(tmp_path, workspace_factory, monkeypatch):
    import warnings
    from miniconstruct.api import routes
    store, project = create_story(tmp_path, workspace_factory, 4)
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(tmp_path))
    offline = OfflineNextClient()
    async def invoke(store, project_id, request):
        return await plan_next(store, project_id, request, offline)
    monkeypatch.setattr(routes, "plan_next", invoke)
    client = TestClient(app)
    response = client.post(f"/api/projects/{project.project_id}/story-plan/next", json={
        "anchorGenerationId": project.active_generation_id, "requestedCount": 3, "llm": LLM.model_dump(mode="json", by_alias=True)})
    assert response.status_code == 200 and len(offline.calls) == 1
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        applied = client.post(f"/api/projects/{project.project_id}/story-plan/next/apply", json={"draft": response.json()})
    assert not any("PydanticSerializationUnexpectedValue" in str(item.message) for item in caught)
    assert applied.status_code == 200 and len(applied.json()["sequence"]["generations"]) == 7
    replay = client.post(f"/api/projects/{project.project_id}/story-plan/next/apply", json={"draft": response.json()})
    assert replay.status_code == 400


async def test_benchmark_scaling_production_path():
    from pathlib import Path
    from miniconstruct.benchmark.runner import BenchmarkRunner, load_suite
    from test_benchmark_planner import profile
    root = Path(__file__).resolve().parents[1] / "benchmarks"
    client = OfflineNextClient()
    result = await BenchmarkRunner(root, client).run(profile(), load_suite(root, "story-next-scaling").cases)
    sizes = []
    for case, length in zip(result["cases"], [4, 12, 30, 60]):
        assert case["status"] == "completed", case.get("error")
        assert case["machineChecks"]["passed"]
        metrics = case["storyMetrics"]
        assert metrics["sourceSequenceLength"] == length
        assert metrics["localContextGenerationCount"] == metrics["requestedNewGenerationCount"] == 3
        assert metrics["responseSlotCount"] == metrics["temporarySlotCount"] == 3
        assert metrics["storyMemoryFactCount"] == metrics["storyMemoryThreadCount"] == 1
        assert metrics["providerPromptTokens"] is None
        sizes.append(metrics["modelInput"]["userTextCharacters"])
    assert max(sizes) - min(sizes) < 20  # Only visible ordinal digit widths grow, not history.
    assert len(client.calls) == 4


async def test_benchmark_historical_identity_recall_production_path():
    from pathlib import Path
    from miniconstruct.benchmark.runner import BenchmarkRunner, load_suite
    from test_benchmark_planner import profile
    root = Path(__file__).resolve().parents[1] / "benchmarks"
    client = OfflineNextClient()
    result = await BenchmarkRunner(root, client).run(profile(), load_suite(root, "story-next-historical-identity").cases)
    case = result["cases"][0]
    assert case["status"] == "completed", case.get("error")
    assert case["storyMetrics"]["sourceSequenceLength"] == 30
    assert case["storyMetrics"]["localContextGenerationCount"] == 3
    assert case["storyMetrics"]["historicalReferenceCount"] == case["storyMetrics"]["allowedHistoricalReferencePairCount"] == 1
