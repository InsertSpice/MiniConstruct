"""Exact, explicit Generation-owned prompt snapshots without provider/renderer access."""
import hashlib
import json
from uuid import uuid4

import pytest

from miniconstruct.execution_state import (SavedPromptWrite, ManualEditAdoptionWrite,
    ProjectExecutionState, SAVED_PROMPT_LIMIT)
from miniconstruct.project_store import ProjectStore, GenerationNotFoundError, ProjectStoreError


def save(store, project, generation, prompt):
    return store.save_prompt(project, generation, SavedPromptWrite(prompt=prompt))


def test_exact_multiple_round_trip_dedup_stale_and_independent_selection(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory())
    pid, gid = project.project_id, project.active_generation_id
    prompt = "  <H3>\nCafé — 雪!\n</H3>  \n"
    first = save(store, pid, gid, prompt)
    a = first.prompts[0]
    assert a.prompt == prompt
    assert a.sha256 == hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    assert a.authoring_path == "one_shot" and not a.stale and not first.already_saved
    second = save(store, pid, gid, "B")
    assert [item.prompt for item in second.prompts] == ["B", prompt]
    duplicate = save(store, pid, gid, prompt)
    assert duplicate.already_saved and len(duplicate.prompts) == 2
    assert duplicate.active_saved_prompt_id == a.id
    assert duplicate.prompts[1] == a  # first-save dedup preserves original metadata
    assert store.execution_snapshot(pid, gid)[1].selected is None
    reopened = ProjectStore(tmp_path).saved_prompt_status(pid, gid)
    assert reopened == duplicate.model_copy(update={"already_saved": False})
    selected = store.adopt_manual_edit_execution_prompt(pid, gid, ManualEditAdoptionWrite(prompt="execution only"))
    assert selected.selected.source_kind == "manual_edit"
    store.save_project(pid, workspace_factory(durationSeconds=9))
    status = store.saved_prompt_status(pid, gid)
    assert all(item.stale for item in status.prompts)
    assert status.prompts[1].prompt == prompt
    before = store.execution_snapshot(pid, gid)[1].selected
    # Load only activates the saved snapshot; it never adopts execution text.
    loaded = store.activate_saved_prompt(pid, gid, str(status.prompts[0].id))
    assert loaded.active_saved_prompt_id == status.prompts[0].id
    deleted = store.delete_saved_prompt(pid, gid, str(loaded.active_saved_prompt_id))
    assert deleted.active_saved_prompt_id is None
    assert [item.prompt for item in deleted.prompts] == [prompt]
    assert store.execution_snapshot(pid, gid)[1].selected == before
    raw = json.loads((tmp_path / f"project-{pid}" / "execution-state.json").read_text())
    assert raw["schemaVersion"] == 4 and "savedPromptDrafts" not in raw
    assert raw["savedPrompts"][gid]["activeSavedPromptId"] is None


def test_generation_lifecycle_and_save_as(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory())
    pid, first = project.project_id, project.active_generation_id
    save(store, pid, first, "A"); saved = save(store, pid, first, "B")
    store.activate_saved_prompt(pid, first, str(saved.prompts[1].id))
    project = store.create_generation(pid)
    second = project.active_generation_id
    assert store.saved_prompt_status(pid, second).prompts == []
    save(store, pid, second, "C")
    store.reorder_generations(pid, [second, first])
    for gid, texts in [(first, ["B", "A"]), (second, ["C"])]:
        store.activate_generation(pid, gid)
        assert [item.prompt for item in store.saved_prompt_status(pid, gid).prompts] == texts
    copy = store.duplicate_project(pid, "Copy")
    copied_ids = [g.generation_id for g in copy.sequence.generations]
    assert [item.prompt for item in store.saved_prompt_status(copy.project_id, copied_ids[0]).prompts] == ["C"]
    copied = store.saved_prompt_status(copy.project_id, copied_ids[1])
    assert [item.prompt for item in copied.prompts] == ["B", "A"]
    assert copied.active_saved_prompt_id == saved.prompts[1].id
    store.delete_saved_prompt(pid, first, str(saved.prompts[1].id))
    assert len(store.saved_prompt_status(copy.project_id, copied_ids[1]).prompts) == 2
    duplicate = store.duplicate_generation(pid, second)
    assert store.saved_prompt_status(pid, duplicate.active_generation_id).prompts == []
    store.delete_generation(pid, duplicate.active_generation_id)
    store.activate_generation(pid, second); store.delete_generation(pid, second)
    raw = json.loads((tmp_path / f"project-{pid}" / "execution-state.json").read_text())
    assert second not in raw["savedPrompts"]
    with pytest.raises(GenerationNotFoundError):
        save(store, pid, second, "orphan")


def test_v3_draft_read_is_deterministic_and_preserves_bytes_until_mutation(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory())
    pid, gid = project.project_id, project.active_generation_id
    first = save(store, pid, gid, "  exact legacy 雪\n")
    draft = first.prompts[0].model_dump(mode="json", by_alias=True, exclude={"id", "stale", "save_number"})
    path = tmp_path / f"project-{pid}" / "execution-state.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["schemaVersion"] = 3; raw.pop("savedPrompts")
    raw["savedPromptDrafts"] = {gid: draft}
    path.write_text(json.dumps(raw, indent=1), encoding="utf-8")
    before = path.read_bytes()
    migrated = store.saved_prompt_status(pid, gid)
    assert len(migrated.prompts) == 1
    item = migrated.prompts[0]
    assert item.model_dump(mode="json", by_alias=True, exclude={"id", "stale", "save_number"}) == draft
    assert migrated.active_saved_prompt_id == item.id
    assert item.save_number == 1 and migrated.next_save_number == 2
    assert ProjectStore(tmp_path).saved_prompt_status(pid, gid) == migrated
    assert path.read_bytes() == before
    save(store, pid, gid, "second")
    canonical = json.loads(path.read_text(encoding="utf-8"))
    assert canonical["schemaVersion"] == 4 and "savedPromptDrafts" not in canonical
    assert canonical["savedPrompts"][gid]["prompts"][0]["id"] == str(item.id)
    assert len(canonical["savedPrompts"][gid]["prompts"]) == 2


@pytest.mark.parametrize("version", [1, 2, 3, 4])
def test_empty_sidecar_compatibility(version):
    state = ProjectExecutionState.model_validate({"schemaVersion": version})
    assert state.schema_version == 4 and state.saved_prompts == {}


def test_nonactive_delete_retains_active_and_wrong_generation_rejected(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path); p = store.create_project(workspace_factory())
    pid, gid = p.project_id, p.active_generation_id
    a = save(store, pid, gid, "A").prompts[0]
    b = save(store, pid, gid, "B").prompts[0]
    result = store.delete_saved_prompt(pid, gid, str(a.id))
    assert result.active_saved_prompt_id == b.id
    other = store.create_generation(pid).active_generation_id
    with pytest.raises(ProjectStoreError, match="does not belong"):
        store.activate_saved_prompt(pid, other, str(b.id))
    with pytest.raises(ProjectStoreError, match="does not belong"):
        store.delete_saved_prompt(pid, other, str(b.id))


def test_limit_rejects_without_eviction_and_duplicate_still_works(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path); p = store.create_project(workspace_factory())
    pid, gid = p.project_id, p.active_generation_id
    state = store._read_execution_state(tmp_path / f"project-{pid}")
    from miniconstruct.execution_state import SavedPrompt, SavedPromptCollection
    fingerprint = store.execution_snapshot(pid, gid)[1].current_fingerprints["one_shot"]
    state.saved_prompts[gid] = SavedPromptCollection(prompts=[SavedPrompt(prompt=str(i), saveNumber=i + 1,
        sha256=hashlib.sha256(str(i).encode()).hexdigest(), savedAt="2026-10-03T00:00:00Z",
        sourceFingerprint=fingerprint, authoringPath="one_shot") for i in range(SAVED_PROMPT_LIMIT)])
    store._write_execution_state(tmp_path / f"project-{pid}", state)
    path = tmp_path / f"project-{pid}" / "execution-state.json"; before = path.read_bytes()
    with pytest.raises(ProjectStoreError, match="Delete an older entry"):
        save(store, pid, gid, "overflow")
    assert path.read_bytes() == before
    assert save(store, pid, gid, "0").already_saved


def test_failed_sidecar_write_preserves_previous_collection(tmp_path, workspace_factory, monkeypatch):
    store = ProjectStore(tmp_path); p = store.create_project(workspace_factory())
    save(store, p.project_id, p.active_generation_id, "original")
    path = tmp_path / f"project-{p.project_id}" / "execution-state.json"; before = path.read_bytes()
    def fail_write(*args):
        raise OSError("disk unavailable")
    monkeypatch.setattr(store, "_atomic_write", fail_write)
    with pytest.raises(OSError):
        save(store, p.project_id, p.active_generation_id, "replacement")
    assert path.read_bytes() == before


def test_saved_prompt_api_is_generation_scoped(tmp_path, workspace_factory, monkeypatch):
    from fastapi.testclient import TestClient
    from miniconstruct.app import app
    from miniconstruct import project_store
    monkeypatch.setattr(project_store, "default_projects_root", lambda: tmp_path)
    store = ProjectStore(tmp_path); p = store.create_project(workspace_factory())
    base = f"/api/execution/projects/{p.project_id}/generations/{p.active_generation_id}/saved-prompt"
    with TestClient(app) as client:
        saved = client.put(base, json={"prompt": " <H3>\n雪\n</H3> "})
        assert saved.status_code == 200
        item = saved.json()["prompts"][0]
        assert item["prompt"] == " <H3>\n雪\n</H3> "
        assert client.get(base).json()["prompts"][0] == item
        assert client.put(base, json={"prompt": item["prompt"]}).json()["alreadySaved"]
        assert client.post(f"{base}/{item['id']}/load").json()["activeSavedPromptId"] == item["id"]
        assert client.delete(f"{base}/{item['id']}").json()["activeSavedPromptId"] is None
        assert client.get(base).json()["prompts"] == []
        assert client.post(f"{base}/{uuid4()}/load").status_code == 422


def test_visible_story_candidate_can_be_saved_after_story_path_is_removed(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path); p = store.create_project(workspace_factory())
    status = store.save_prompt(p.project_id, p.active_generation_id,
        SavedPromptWrite(prompt="exact older Story text", authoringPath="story"))
    assert status.prompts[0].prompt == "exact older Story text"
    assert status.prompts[0].authoring_path == "one_shot"


def test_stable_numbers_survive_delete_duplicate_save_as_and_independent_generations(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path); p = store.create_project(workspace_factory())
    pid, gid = p.project_id, p.active_generation_id
    for text in ("A", "B", "C"):
        status = save(store, pid, gid, text)
    assert [item.save_number for item in status.prompts] == [3, 2, 1]
    assert status.next_save_number == 4
    store.delete_saved_prompt(pid, gid, str(status.prompts[1].id))
    dedup = save(store, pid, gid, "A")
    assert dedup.already_saved and [item.save_number for item in dedup.prompts] == [3, 1]
    assert dedup.next_save_number == 4
    fourth = save(store, pid, gid, "D")
    assert [item.save_number for item in fourth.prompts] == [4, 3, 1]
    for item in fourth.prompts:
        store.delete_saved_prompt(pid, gid, str(item.id))
    assert save(store, pid, gid, "E").prompts[0].save_number == 5
    copy = store.duplicate_project(pid, "Numbered copy")
    copied_id = copy.sequence.generations[0].generation_id
    copied = store.saved_prompt_status(copy.project_id, copied_id)
    assert copied.prompts[0].save_number == 5 and copied.next_save_number == 6
    assert save(store, copy.project_id, copied_id, "F").prompts[0].save_number == 6
    second = store.create_generation(pid).active_generation_id
    assert save(store, pid, second, "G2").prompts[0].save_number == 1
    duplicate = store.duplicate_generation(pid, gid).active_generation_id
    assert save(store, pid, duplicate, "duplicate").prompts[0].save_number == 1


def test_early_v4_numbers_adapt_without_rewriting_or_losing_snapshot_metadata(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path); p = store.create_project(workspace_factory())
    pid, gid = p.project_id, p.active_generation_id
    for text in ("  A\n雪", "B", "C"):
        numbered = save(store, pid, gid, text)
    path = tmp_path / f"project-{pid}" / "execution-state.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    collection = raw["savedPrompts"][gid]
    collection.pop("nextSaveNumber")
    for item in collection["prompts"]:
        item.pop("saveNumber")
    originals = collection["prompts"]
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    before = path.read_bytes()
    adapted = store.saved_prompt_status(pid, gid)
    assert adapted.active_saved_prompt_id == numbered.active_saved_prompt_id
    assert [item.save_number for item in adapted.prompts] == [3, 2, 1]
    assert adapted.next_save_number == 4
    for item, original in zip(reversed(adapted.prompts), originals):
        assert item.model_dump(mode="json", by_alias=True, exclude={"stale", "save_number"}) == original
    assert ProjectStore(tmp_path).saved_prompt_status(pid, gid) == adapted
    assert path.read_bytes() == before
    assert save(store, pid, gid, "D").prompts[0].save_number == 4
    canonical = json.loads(path.read_text(encoding="utf-8"))["savedPrompts"][gid]
    assert canonical["nextSaveNumber"] == 5
    assert [item["saveNumber"] for item in canonical["prompts"]] == [1, 2, 3, 4]


def test_update_and_save_as_new_preserve_identity_metadata_and_execution(tmp_path, workspace_factory, monkeypatch):
    store = ProjectStore(tmp_path); p = store.create_project(workspace_factory())
    pid, gid = p.project_id, p.active_generation_id
    first = save(store, pid, gid, "A").prompts[0]
    selected = store.adopt_manual_edit_execution_prompt(pid, gid, ManualEditAdoptionWrite(prompt="execution only")).selected
    store.save_project(pid, workspace_factory(durationSeconds=9))
    monkeypatch.setattr(store, "_now", lambda: "2026-10-06T01:00:00+00:00")
    def update(item, text):
        return store.save_prompt(pid, gid, SavedPromptWrite(prompt=text, savedPromptId=item.id))
    updated = update(first, "  B\n雪 ")
    b = updated.prompts[0]
    assert len(updated.prompts) == 1 and b.id == first.id and b.save_number == 1
    assert b.sha256 == hashlib.sha256(b.prompt.encode()).hexdigest()
    assert b.source_fingerprint != first.source_fingerprint and not b.stale
    assert b.saved_at == "2026-10-06T01:00:00+00:00" and b.saved_at != first.saved_at
    assert updated.next_save_number == 2 and updated.active_saved_prompt_id == b.id
    copied = store.save_prompt(pid, gid, SavedPromptWrite(prompt="C", saveAsNew=True))
    c = copied.prompts[0]
    assert c.id != b.id and c.save_number == 2 and copied.prompts[1] == b
    d = update(c, "D")
    assert [item.prompt for item in d.prompts] == ["D", b.prompt] and d.next_save_number == 3
    identical = store.save_prompt(pid, gid, SavedPromptWrite(prompt="D", saveAsNew=True))
    assert [item.save_number for item in identical.prompts] == [3, 2, 1]
    assert identical.prompts[0].id != c.id
    unchanged = update(identical.prompts[0], "D")
    assert len(unchanged.prompts) == 3 and unchanged.next_save_number == 4
    # Updating an older entry to an identical hash is also legal; order remains Save N.
    collision = update(b, "D")
    assert [item.save_number for item in collision.prompts] == [3, 2, 1]
    assert len({item.sha256 for item in collision.prompts}) == 1
    assert store.execution_snapshot(pid, gid)[1].selected == selected
    reopened = ProjectStore(tmp_path).saved_prompt_status(pid, gid)
    assert reopened == collision
    copy = store.duplicate_project(pid, "Copy")
    copied_gid = copy.active_generation_id
    copied_status = store.saved_prompt_status(copy.project_id, copied_gid)
    assert copied_status == collision
    store.save_prompt(copy.project_id, copied_gid, SavedPromptWrite(prompt="copy edit", savedPromptId=b.id))
    assert store.saved_prompt_status(pid, gid) == collision


def test_update_and_save_as_new_capacity_deleted_and_wrong_owner(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path); p = store.create_project(workspace_factory())
    pid, gid = p.project_id, p.active_generation_id
    from miniconstruct.execution_state import SavedPrompt, SavedPromptCollection
    directory = tmp_path / f"project-{pid}"
    state = store._read_execution_state(directory)
    fingerprint = store.execution_snapshot(pid, gid)[1].current_fingerprints["one_shot"]
    state.saved_prompts[gid] = SavedPromptCollection(prompts=[SavedPrompt(prompt=str(i), saveNumber=i+1,
        sha256=hashlib.sha256(str(i).encode()).hexdigest(), savedAt="2026-10-03T00:00:00Z",
        sourceFingerprint=fingerprint, authoringPath="one_shot") for i in range(100)])
    store._write_execution_state(directory, state)
    target = state.saved_prompts[gid].prompts[0]
    updated = store.save_prompt(pid, gid, SavedPromptWrite(prompt="edited", savedPromptId=target.id))
    assert len(updated.prompts) == 100 and updated.next_save_number == 101
    before = (directory / "execution-state.json").read_bytes()
    for text in ("edited", "new"):
        with pytest.raises(ProjectStoreError, match="Delete an older entry"):
            store.save_prompt(pid, gid, SavedPromptWrite(prompt=text, saveAsNew=True))
    assert (directory / "execution-state.json").read_bytes() == before
    other = store.create_generation(pid).active_generation_id
    other_project = store.create_project(workspace_factory())
    for project_id, generation_id in [(pid, other), (other_project.project_id, other_project.active_generation_id)]:
        with pytest.raises(ProjectStoreError, match="does not belong"):
            store.save_prompt(project_id, generation_id, SavedPromptWrite(prompt="wrong", savedPromptId=target.id))
    store.delete_saved_prompt(pid, gid, str(target.id))
    with pytest.raises(ProjectStoreError, match="does not belong"):
        store.save_prompt(pid, gid, SavedPromptWrite(prompt="stale", savedPromptId=target.id))
    new = store.save_prompt(pid, gid, SavedPromptWrite(prompt="new"))
    assert new.prompts[0].id != target.id and new.prompts[0].save_number == 101


def test_save_operation_api_and_validation(tmp_path, workspace_factory, monkeypatch):
    from fastapi.testclient import TestClient
    from miniconstruct.app import app
    from miniconstruct import project_store
    monkeypatch.setattr(project_store, "default_projects_root", lambda: tmp_path)
    store = ProjectStore(tmp_path); p = store.create_project(workspace_factory())
    base = f"/api/execution/projects/{p.project_id}/generations/{p.active_generation_id}/saved-prompt"
    with TestClient(app) as client:
        first = client.put(base, json={"prompt": "A"}).json()["prompts"][0]
        updated = client.put(base, json={"prompt": "B", "savedPromptId": first["id"]})
        assert updated.status_code == 200 and len(updated.json()["prompts"]) == 1
        assert updated.json()["prompts"][0]["id"] == first["id"]
        new = client.put(base, json={"prompt": "B", "saveAsNew": True})
        assert new.status_code == 200 and len(new.json()["prompts"]) == 2
        assert client.put(base, json={"prompt": "C", "savedPromptId": str(uuid4())}).status_code == 422
        assert client.put(base, json={"prompt": "C", "savedPromptId": first["id"], "saveAsNew": True}).status_code == 422
        assert client.put(base, json={"prompt": "", "saveAsNew": True}).status_code == 422
        client.delete(f"{base}/{first['id']}")
        assert client.put(base, json={"prompt": "C", "savedPromptId": first["id"]}).status_code == 422
