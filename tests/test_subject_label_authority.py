"""Stored Subject labels stay authoritative in H3 and across template copies."""
import json
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from miniconstruct.app import app
from miniconstruct.execution_state import SavedPromptWrite
from miniconstruct.h3.builder import assemble_prompt, build_reference_manifest, build_h3_input_map, _reference_semantics
from miniconstruct.h3.repair import repair_identity_provenance, assemble_repair_prompt
from miniconstruct.models.api import RepairRequest
from miniconstruct.h3.validator import validate_workspace_prompt
from miniconstruct.models.workspace import validate_authoring_workspace
from miniconstruct.project_store import ProjectStore, ProjectStoreError
from test_validator import valid_ref_prompt


def test_subject_three_request_mocked_writer_audit_and_repair(workspace_factory, image_asset_factory):
    pictures = [image_asset_factory("body"), image_asset_factory("face", order=1)]
    for picture in pictures:
        picture.subject_identity.subject_id = "character-c"
    workspace = workspace_factory(mode="Ref2VA", durationSeconds=6, subjects=[
        {"id": "character-c", "number": 3}], assets=pictures, nextSubjectNumber=88)
    assembled = assemble_prompt(workspace, True)
    assert "Assigned current Subject labels: <Subject 3>." in assembled.inspector_text
    assert "GOOD: '<Subject 3> is Alice" in assembled.inspector_text
    assert "<Subject 1>" not in _reference_semantics(workspace, True)
    assert "<Subject 3> is defined by <Picture 1>, <Picture 2>." in assembled.inspector_text
    assert build_reference_manifest(workspace)["subjects"][0]["label"] == "Subject 3"
    assert all(entry["metadata"][0] == "Subject 3" for entry in build_h3_input_map(workspace))
    correct = valid_ref_prompt().replace("<Subject 1>", "<Subject 3>").replace(
        "is the woman in <Picture 1>.", "is Alice, the woman shown in <Picture 1> and <Picture 2>.")
    writer = Mock(return_value=correct)
    output = writer(assembled.messages)
    assert validate_workspace_prompt(output, workspace).valid
    wrong = output.replace("<Subject 3>", "<Subject 1>")
    audit = validate_workspace_prompt(wrong, workspace)
    assert not audit.valid
    errors = [f.message for f in audit.findings if f.code == "identity_reference_missing"]
    assert len(errors) == 2 and all("<Subject 3>" in error for error in errors)
    assert repair_identity_provenance(wrong, workspace, audit.findings) == wrong
    assert repair_identity_provenance(correct, workspace, []) == correct
    request = RepairRequest.model_validate({
        "workspace": workspace.model_dump(by_alias=True), "prompt": wrong,
        "llm": {"baseUrl": "http://local.test/v1", "model": "mock-writer"}, "findings": []})
    repair = assemble_repair_prompt(request, audit.findings)
    assert "Assigned current Subject labels: <Subject 3>." in repair.inspector_text
    assert "<Subject 3> must cite identity provenance <Picture 2>" in repair.inspector_text


def test_sparse_multiple_subject_writer_authority(workspace_factory, image_asset_factory):
    pictures = [image_asset_factory("a"), image_asset_factory("b", order=1)]
    # Picture order crosses Subject order: select the example pair from one
    # canonical identity asset instead of combining independent first items.
    for picture, subject_id in zip(pictures, ("b", "a")):
        picture.subject_identity.subject_id = subject_id
    workspace = workspace_factory(mode="Ref2VA", subjects=[
        {"id": "a", "number": 2}, {"id": "b", "number": 5}], assets=pictures)
    assembled = assemble_prompt(workspace, True)
    expected = "GOOD: '<Subject 5> is Alice, the woman shown in <Picture 1>, with ...'."
    false_pair = "<Subject 2> is Alice, the woman shown in <Picture 1>"
    for text in (assembled.inspector_text, json.dumps(assembled.messages)):
        assert "Assigned current Subject labels: <Subject 2>, <Subject 5>." in text
        assert "never compact or renumber" in text
        assert expected in text
        assert false_pair not in text


def test_writer_identity_example_uses_placeholders_without_identity_pair(workspace_factory, image_asset_factory):
    style = image_asset_factory(role="style_appearance")
    workspace = workspace_factory(mode="Ref2VA", subjects=[{"id": "a", "number": 2}], assets=[style])
    semantics = _reference_semantics(workspace, True)
    assert "GOOD: '<Subject N> is Alice, the woman shown in <Picture N>, with ...'." in semantics
    assert "<Picture 1> is the identity reference" not in semantics



@pytest.mark.parametrize("copy_mode", ["fresh", "full"])
def test_copy_preserves_sparse_cast_and_real_watermark(tmp_path, workspace_factory, copy_mode):
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory(subjects=[
        {"id": "one", "number": 1}, {"id": "three", "number": 3}], nextSubjectNumber=4))
    copied = store.duplicate_project(project.project_id, "Copy", copy_mode=copy_mode)
    assert [(s.id, s.number) for s in copied.sequence.subjects] == [("one", 1), ("three", 3)]
    assert copied.sequence.next_subject_number == 4
    data = copied.workspace.model_dump(by_alias=True)
    data["subjects"].append({"id": "new-four", "number": 4})
    saved = store.save_project(copied.project_id, validate_authoring_workspace(data))
    assert [(s.id, s.number) for s in saved.sequence.subjects] == [("one", 1), ("three", 3), ("new-four", 4)]
    assert saved.sequence.next_subject_number == 5


def test_saved_text_does_not_gain_a_new_identity_after_deletion(tmp_path, workspace_factory, image_asset_factory):
    store = ProjectStore(tmp_path)
    picture = image_asset_factory()
    project = store.create_project(workspace_factory(mode="Ref2VA", assets=[picture]))
    history = "<Subject 1> is the previous character from <Picture 1>."
    store.save_prompt(project.project_id, project.active_generation_id, SavedPromptWrite(prompt=history))
    old_id = project.sequence.subjects[0].id
    data = project.workspace.model_dump(by_alias=True)
    data["assets"] = []
    project = store.save_project(project.project_id, validate_authoring_workspace(data))
    project = store.delete_sequence_subject(project.project_id, old_id)
    data = project.workspace.model_dump(by_alias=True)
    data["subjects"] = [{"id": "unrelated-character", "number": 1}]
    with pytest.raises(ProjectStoreError, match="expected Subject 2"):
        store.save_project(project.project_id, validate_authoring_workspace(data))
    data["subjects"][0]["number"] = 2
    saved = store.save_project(project.project_id, validate_authoring_workspace(data))
    assert [(s.id, s.number) for s in saved.sequence.subjects] == [("unrelated-character", 2)]
    assert store.saved_prompt_status(saved.project_id, saved.active_generation_id).prompts[0].prompt == history


@pytest.mark.parametrize("operation", ["new", "attach-existing"])
def test_fresh_copy_ui_reference_removal_keeps_cast_and_requires_explicit_identity(
        tmp_path, workspace_factory, image_asset_factory, monkeypatch, operation):
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(tmp_path))
    store = ProjectStore(tmp_path)
    picture = image_asset_factory()
    source = store.create_project(workspace_factory(mode="Ref2VA", assets=[picture]))
    old_id = source.sequence.subjects[0].id
    with TestClient(app) as client:
        copied = client.post(f"/api/projects/{source.project_id}/save-as", json={"name": "Fresh", "copyMode": "fresh"})
        assert copied.status_code == 201, copied.text
        project = store.load_project(copied.json()["id"])
        url = f"/api/projects/{project.project_id}/generations/{project.active_generation_id}/reference-assets"
        # Same narrow reference-authority request sent by the mounted Remove action.
        removed = client.put(url, json={"assets": [], "subjects": [], "expectedProjectFingerprint": project.project_fingerprint})
        assert removed.status_code == 200, removed.text
        after = store.load_project(project.project_id)
        assert after.workspace.subjects == [] and after.workspace.assets == []
        assert [(s.id, s.number) for s in after.sequence.subjects] == [(old_id, 1)]
        assert after.sequence.next_subject_number == 2
        subject_id, number = ("different-character", 2) if operation == "new" else (old_id, 1)
        added_picture = picture.model_dump(by_alias=True)
        added_picture["subjectIdentity"]["subjectId"] = subject_id
        added = client.put(url, json={"assets": [added_picture], "subjects": [{"id": subject_id, "number": number}],
                                     "expectedProjectFingerprint": after.project_fingerprint})
        assert added.status_code == 200, added.text
        final = store.load_project(project.project_id)
        assert final.workspace.assets[0].subject_identity.subject_id == subject_id
        assert [(s.id, s.number) for s in final.workspace.subjects] == [(subject_id, number)]
        assert final.sequence.next_subject_number == (3 if operation == "new" else 2)
        assert len(final.sequence.subjects) == (2 if operation == "new" else 1)
