"""Generation-owned memory acceptance tests: only offline production operations."""
from copy import deepcopy
import json

import pytest
from fastapi.testclient import TestClient

from miniconstruct.app import app
from miniconstruct.models.story_memory import StoryContinuityMemory, StoryMemoryPatch, apply_memory_patch
from miniconstruct.project_store import ProjectStore, StaleProjectSnapshotError, MalformedProjectError
from miniconstruct.llm.client import LLMBackendError
from test_story_next import create_story, propose, OfflineNextClient, THREAD, FACT, manifest_path, memory


G3_SUMMARY = "Alice has reached the open railway underpass while travelling to deliver medicine to Bob before curfew."
G6_SUMMARY = "Alice crossed the railway underpass earlier. The underpass was later sealed after an electrical fire, and Alice has since delivered the medicine package to Bob."


def snapshot(summary, fact, thread=False):
    return StoryContinuityMemory(recentSummary=summary,
        durableFacts=[{"id": FACT, "text": fact}],
        openThreads=[{"id": THREAD, "text": "Alice still needs to deliver the medicine to Bob."}] if thread else [])


def generations(store, project):
    return store._generations(store.read_story_snapshot(project.project_id).manifest)[0]


def save_memory(store, project, generation_id, value):
    active = store.activate_generation(project.project_id, generation_id)
    return store.save_project(project.project_id, active.workspace, story_memory=value,
        story_memory_generation_id=generation_id, expected_project_fingerprint=active.project_fingerprint)


def test_exact_g3_g6_independent_summary_facts_threads_save_reload(tmp_path, workspace_factory, monkeypatch):
    store, project = create_story(tmp_path, workspace_factory, 6)
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(tmp_path))
    client = TestClient(app)
    ids = [g.generation_id for g in project.sequence.generations]
    early = snapshot(G3_SUMMARY, "The railway underpass is open.", True)
    late = snapshot(G6_SUMMARY, "The railway underpass is sealed after an electrical fire.")
    for gid, value in [(ids[2], early), (ids[5], late)]:
        response = client.put(f"/api/projects/{project.project_id}/active-generation/{gid}")
        assert response.status_code == 200, response.text
        active = response.json()
        response = client.put(f"/api/projects/{project.project_id}", json={
            "workspace": active["workspace"], "storyMemory": value.model_dump(by_alias=True),
            "storyMemoryGenerationId": gid, "expectedProjectFingerprint": active["projectFingerprint"]})
        assert response.status_code == 200, response.text
        assert response.json()["storyMemoryAfter"] == value.model_dump(by_alias=True)
    for _ in range(2):
        store = ProjectStore(tmp_path)
        for gid, value in [(ids[2], early), (ids[5], late)]:
            active = store.activate_generation(project.project_id, gid)
            assert active.story_memory_after == value
            assert store.load_project(project.project_id).story_memory_after == value
    late.durable_facts.clear()
    save_memory(store, project, ids[5], late)
    assert store.activate_generation(project.project_id, ids[2]).story_memory_after == early
    raw = json.loads(manifest_path(store, project).read_text())
    assert raw["schemaVersion"] == 17 and "storyMemory" not in raw["sequence"]
    manifest = store.read_story_snapshot(project.project_id).manifest
    assert store._story_memory(manifest) is manifest.sequence.generations[-1].story_memory_after


def test_inheritance_deep_copy_edit_invalidation_unchanged_and_confirm(tmp_path, workspace_factory):
    store, project = create_story(tmp_path, workspace_factory, 3)
    g3 = project.active_generation_id
    intact = snapshot("Bridge intact", "The bridge is intact.")
    project = save_memory(store, project, g3, intact)
    project = store.create_generation(project.project_id)
    g4 = project.active_generation_id
    assert project.story_memory_after == intact
    raw = generations(store, project)
    assert raw[2].story_memory_after is not raw[3].story_memory_after
    assert raw[2].story_memory_after.durable_facts[0] is not raw[3].story_memory_after.durable_facts[0]
    project = save_memory(store, project, g4, snapshot("Bridge damaged", "The bridge is damaged."))
    assert generations(store, project)[2].story_memory_after == intact
    store.create_generation(project.project_id)
    before = [g.story_memory_after.model_dump() for g in generations(store, project)]
    save_memory(store, project, g3, intact.model_copy(deep=True))
    assert all(g.story_memory_status == "trusted" for g in generations(store, project))
    changed = intact.model_copy(update={"recent_summary": "Corrected historical state"}, deep=True)
    save_memory(store, project, g3, changed)
    after = generations(store, project)
    assert [g.story_memory_status for g in after] == ["trusted"] * 3 + ["needs_review"] * 2
    assert [g.story_memory_after.model_dump() for g in after[3:]] == before[3:]
    save_memory(store, project, g4, after[3].story_memory_after)
    assert [g.story_memory_status for g in generations(store, project)][3:] == ["trusted", "needs_review"]


@pytest.mark.parametrize("operation,boundary", [("before_first", 1), ("before", 2), ("after", 3), ("duplicate", 3), ("delete", 2), ("reorder", 1)])
def test_structural_operations_keep_id_snapshots_and_invalidate_suffix(tmp_path, workspace_factory, operation, boundary):
    store, project = create_story(tmp_path, workspace_factory, 6)
    original = generations(store, project)
    ids = [g.generation_id for g in original]
    for i, gid in enumerate(ids):
        save_memory(store, project, gid, StoryContinuityMemory(recentSummary=f"State {i}"))
    original = generations(store, project)
    states = {g.generation_id: g.story_memory_after.model_dump() for g in original}
    if operation in {"before_first", "before", "after"}:
        index = {"before_first": 0, "before": 2, "after": 3}[operation]
        project = store.create_generation(project.project_id, insert_index=index)
        expected = original[index - 1].story_memory_after if index else StoryContinuityMemory()
        assert project.story_memory_after == expected
        boundary = index + 1
    elif operation == "duplicate":
        project = store.duplicate_generation(project.project_id, ids[2])
        assert project.story_memory_after == original[2].story_memory_after
        boundary = 4
    elif operation == "delete":
        store.activate_generation(project.project_id, ids[2])
        store.delete_generation(project.project_id, ids[2])
    else:
        store.reorder_generations(project.project_id, [ids[0], ids[2], ids[1], *ids[3:]])
    result = generations(store, project)
    for g in result:
        if g.generation_id in states:
            assert g.story_memory_after.model_dump() == states[g.generation_id]
    assert [g.story_memory_status for g in result] == ["trusted"] * boundary + ["needs_review"] * (len(result) - boundary)


def test_v8_migration_terminal_only_no_invented_history_and_strict_v9(tmp_path, workspace_factory):
    store, project = create_story(tmp_path, workspace_factory, 6)
    path = manifest_path(store, project)
    raw = json.loads(path.read_text())
    raw["schemaVersion"] = 8
    raw["sequence"]["storyMemory"] = memory().model_dump(by_alias=True)
    for g in raw["sequence"]["generations"]:
        g.pop("storyMemoryAfter"); g.pop("storyMemoryStatus")
    path.write_text(json.dumps(raw))
    before = path.read_bytes()
    migrated = generations(store, project)
    assert path.read_bytes() == before
    assert all(g.story_memory_status == "legacy_unknown" and g.story_memory_after == StoryContinuityMemory() for g in migrated[:-1])
    assert migrated[-1].story_memory_status == "trusted" and migrated[-1].story_memory_after == memory()
    project = store.save_project(project.project_id, project.workspace)
    canonical = json.loads(path.read_text())
    assert canonical["schemaVersion"] == 17 and "storyMemory" not in canonical["sequence"]
    save_memory(store, project, migrated[2].generation_id, StoryContinuityMemory(recentSummary=G3_SUMMARY))
    statuses = [g.story_memory_status for g in generations(store, project)]
    assert statuses == ["legacy_unknown", "legacy_unknown", "trusted", "legacy_unknown", "legacy_unknown", "needs_review"]
    canonical["sequence"]["storyMemory"] = {}
    path.write_text(json.dumps(canonical))
    with pytest.raises(MalformedProjectError): store.load_project(project.project_id)


async def test_three_slot_memory_evolution_and_temp_id_authority(tmp_path, workspace_factory):
    store, project = create_story(tmp_path, workspace_factory, 3)
    def evolve(result, context):
        new_id = context["memoryCreationIds"]["newFactIds"][1]
        result["slots"][0]["memoryPatchAfter"] = {"recentSummary": "Promise remains open"}
        result["slots"][1]["memoryPatchAfter"] = {"factUpserts": [{"id": new_id, "text": "New fact"}]}
        result["slots"][2]["memoryPatchAfter"] = {"resolvedThreadIds": [THREAD],
            "factUpserts": [{"id": new_id, "text": "New fact confirmed"}]}
    draft = await propose(store, project, OfflineNextClient(evolve))
    store.apply_story_plan_next(project.project_id, draft)
    g4, g5, g6 = generations(store, project)[3:]
    assert [len(g.story_memory_after.open_threads) for g in [g4, g5, g6]] == [1, 1, 0]
    assert len(g4.story_memory_after.durable_facts) == 1
    assert g5.story_memory_after.durable_facts[-1].text == "New fact"
    assert g6.story_memory_after.durable_facts[-1].text == "New fact confirmed"
    assert g5.story_memory_after.durable_facts[-1].id == g6.story_memory_after.durable_facts[-1].id
    assert g5.story_memory_after.durable_facts[-1].id not in draft.authority.new_fact_ids
    assert all(g.story_memory_status == "trusted" for g in [g4, g5, g6])
    project = store.load_project(project.project_id)
    draft = await propose(store, project)
    draft.slots[0].memory_patch_after.fact_upserts = deepcopy(g5.story_memory_after.durable_facts[-1:])
    draft.slots[0].memory_patch_after.fact_upserts[0].id = draft.authority.new_fact_ids[-1]
    # Gemma regression: an unused operation-wide ID may first appear in slot 1.
    store.apply_story_plan_next(project.project_id, draft)


def test_qwen_thread_to_fact_cross_type_requires_resolve_then_new_fact_id():
    before = StoryContinuityMemory(openThreads=[{"id": THREAD, "text": "Ferry toll unpaid."}])
    new_fact_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    with pytest.raises(ValueError, match="Duplicate or conflicting"):
        apply_memory_patch(before, StoryMemoryPatch(
            factUpserts=[{"id": THREAD, "text": "Ferry toll paid."}], resolvedThreadIds=[THREAD]),
            [new_fact_id], [], set())
    after = apply_memory_patch(before, StoryMemoryPatch(
        factUpserts=[{"id": new_fact_id, "text": "Ferry toll paid."}], resolvedThreadIds=[THREAD]),
        [new_fact_id], [], set())
    assert after.open_threads == []
    assert after.durable_facts[0].id == new_fact_id


async def test_consumed_temp_id_cannot_be_reintroduced_after_resolution(tmp_path, workspace_factory):
    store, project = create_story(tmp_path, workspace_factory, 3)
    def reuse(response, context):
        temp = context["memoryCreationIds"]["newFactIds"][0]
        response["slots"][0]["memoryPatchAfter"] = {"factUpserts": [{"id": temp, "text": "Created once."}]}
        response["slots"][1]["memoryPatchAfter"] = {"resolvedFactIds": [temp]}
        response["slots"][2]["memoryPatchAfter"] = {"factUpserts": [{"id": temp, "text": "Reintroduced."}]}
    with pytest.raises(LLMBackendError, match="Consumed temporary memory ID"):
        await propose(store, project, OfflineNextClient(reuse))


async def test_terminal_trust_only_and_stale_save_targets(tmp_path, workspace_factory):
    store, project = create_story(tmp_path, workspace_factory, 6)
    ids = [g.generation_id for g in project.sequence.generations]
    old = store.activate_generation(project.project_id, ids[2])
    before = manifest_path(store, project).read_bytes()
    with pytest.raises(StaleProjectSnapshotError):
        store.save_project(project.project_id, old.workspace, story_memory=memory(), story_memory_generation_id=ids[5])
    assert manifest_path(store, project).read_bytes() == before
    changed = save_memory(store, project, ids[2], StoryContinuityMemory(recentSummary=G3_SUMMARY))
    with pytest.raises(StaleProjectSnapshotError):
        store.save_project(project.project_id, old.workspace, expected_project_fingerprint=old.project_fingerprint)
    client = OfflineNextClient()
    with pytest.raises(ValueError, match="confirm"):
        await propose(store, changed, client)
    assert not client.calls
    terminal = generations(store, project)[-1]
    changed = save_memory(store, project, ids[5], terminal.story_memory_after)
    await propose(store, changed, client)
    assert len(client.calls) == 1
    assert generations(store, project)[3].story_memory_status == "needs_review"


def test_save_as_targets_active_snapshot_and_preserves_source(tmp_path, workspace_factory):
    store, project = create_story(tmp_path, workspace_factory, 6)
    ids = [g.generation_id for g in project.sequence.generations]
    active = store.activate_generation(project.project_id, ids[2])
    before = manifest_path(store, project).read_bytes()
    changed = StoryContinuityMemory(recentSummary=G3_SUMMARY)
    copied = store.duplicate_project(project.project_id, "Historical fork", active_workspace=active.workspace,
        story_memory=changed, story_memory_generation_id=ids[2], expected_project_fingerprint=active.project_fingerprint)
    assert copied.story_memory_after == changed
    cloned = generations(store, copied)
    assert not set(ids) & {g.generation_id for g in cloned}
    assert cloned[-1].story_memory_after == memory()
    assert [g.story_memory_status for g in cloned] == ["trusted"] * 3 + ["needs_review"] * 3
    assert manifest_path(store, project).read_bytes() == before
    active = store.activate_generation(copied.project_id, cloned[-1].generation_id)
    saved = store.save_project(copied.project_id, active.workspace)
    assert saved.sequence.generations[-1].story_memory_status == "needs_review"


@pytest.mark.parametrize("field", ["storyMemoryAfter", "storyMemoryStatus"])
def test_v9_rejects_missing_snapshot_fields(tmp_path, workspace_factory, field):
    store, project = create_story(tmp_path, workspace_factory, 2)
    path = manifest_path(store, project)
    raw = json.loads(path.read_text())
    del raw["sequence"]["generations"][0][field]
    path.write_text(json.dumps(raw))
    before = path.read_bytes()
    with pytest.raises(MalformedProjectError): store.load_project(project.project_id)
    assert path.read_bytes() == before


def test_untrusted_inheritance_never_creates_legacy_unknown(tmp_path, workspace_factory):
    store, project = create_story(tmp_path, workspace_factory, 2)
    path = manifest_path(store, project)
    raw = json.loads(path.read_text())
    raw["sequence"]["generations"][-1]["storyMemoryStatus"] = "legacy_unknown"
    path.write_text(json.dumps(raw))
    appended = store.create_generation(project.project_id)
    assert appended.sequence.generations[-1].story_memory_status == "needs_review"
    assert appended.story_memory_after == memory()
    assert appended.sequence.generations[-2].story_memory_status == "legacy_unknown"


async def test_intermediate_overflow_rejects_whole_batch_even_if_later_resolved(tmp_path, workspace_factory):
    from uuid import uuid4
    from miniconstruct.llm.client import LLMBackendError
    store, project = create_story(tmp_path, workspace_factory, 3)
    full = StoryContinuityMemory(durableFacts=[{"id": str(uuid4()), "text": f"Fact {i}"} for i in range(64)])
    project = save_memory(store, project, project.active_generation_id, full)
    def overflow(result, context):
        result["slots"][0]["memoryPatchAfter"] = {"factUpserts": [{"id": context["memoryCreationIds"]["newFactIds"][0], "text": "Overflow"}]}
        result["slots"][-1]["memoryPatchAfter"] = {"resolvedFactIds": [full.durable_facts[0].id]}
    before = manifest_path(store, project).read_bytes()
    with pytest.raises(LLMBackendError, match="limits"):
        await propose(store, project, OfflineNextClient(overflow))
    assert manifest_path(store, project).read_bytes() == before
