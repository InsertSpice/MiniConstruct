"""Saved browser reference mutations through real persistence/Prepare/Send, offline."""
import base64
import hashlib
import io
import json

import httpx
import pytest
from PIL import Image
from fastapi.testclient import TestClient

from miniconstruct.app import app
from miniconstruct import execution as ex
from miniconstruct.execution_authoring import execution_authoring_fingerprint
from miniconstruct.execution_routing import effective_references, GenerationRouting, AssetRouting
from miniconstruct.execution_state import SelectionWrite, SavedPromptWrite
from miniconstruct.execution_send import ExecutionSendRequest, send_generation, deterministic_name
from miniconstruct.h3.builder import assemble_prompt, build_reference_manifest, build_h3_input_map
from miniconstruct.h3.vision_input import prompt_writer_image
from miniconstruct.models.workspace import ReferenceAsset, SubjectRecord
from miniconstruct.project_store import ProjectStore, AssetPayloadError
from miniconstruct.workflow_preparation import prepare_workflow
from test_workflow_preparation import capacity_profile
from test_validator import valid_ref_prompt


def picture(color, asset_id="outfit"):
    output = io.BytesIO()
    source = io.BytesIO()
    Image.new("RGB", (8, 6), color).save(source, "PNG")
    # Browser ingestion canonicalizes the selected PNG before reference persistence.
    Image.open(io.BytesIO(source.getvalue())).convert("RGB").save(output, "JPEG", quality=88)
    payload = output.getvalue()
    return ReferenceAsset.model_validate({"id": asset_id, "kind": "image", "filename": "outfit.png",
        "mimeType": "image/jpeg", "role": "subject_identity", "order": 0, "notes": "Keep the costume",
        "options": {"authoredOption": True},
        "subjectIdentity": {"subjectId": "subject-1", "focus": "outfit", "view": "rear", "layout": "reference_sheet"},
        "image": {"data_url": "data:image/jpeg;base64," + base64.b64encode(payload).decode(), "width": 8, "height": 6}}), payload


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Live provider/ComfyUI calls are forbidden")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)


@pytest.mark.parametrize("operation", ["replace", "remove-add"])
def test_browser_mutation_without_project_save_uses_only_new_bytes(operation, tmp_path, monkeypatch, workspace_factory):
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(tmp_path / "projects"))
    store, config = ProjectStore(), ex.ExecutionStore(tmp_path / "execution")
    profile = config.save("profiles", capacity_profile(1))
    connection = config.save("connections", ex.ConnectionWrite(name="Offline", baseUrl="http://never.test"))
    a, old_bytes = picture("red")
    b, new_bytes = picture("blue", "outfit" if operation == "replace" else "new-outfit")
    project = store.create_project(workspace_factory(mode="Ref2VA", assets=[a], creativeRequest="Saved Main Prompt"))
    p, g = project.project_id, project.active_generation_id
    old_map = effective_references(project.workspace)
    prompt = valid_ref_prompt()
    old_source = store.execution_snapshot(p, g)[1].current_fingerprint
    old_request = SelectionWrite(prompt=prompt, sourceFingerprint=old_source, referenceMap=old_map)
    store.select_execution_prompt(p, g, old_request)
    historical = store.save_prompt(p, g, SavedPromptWrite(prompt=prompt))
    before = prepare_workflow(store, p, g, profile)
    store.set_execution_routing(p, g, GenerationRouting(assets={a.id: AssetRouting()}))
    url = f"/api/projects/{p}/generations/{g}/reference-assets"
    fingerprint = project.project_fingerprint
    with TestClient(app) as client:
        if operation == "remove-add":
            removed = client.put(url, json={"assets": [], "subjects": [], "expectedProjectFingerprint": fingerprint})
            assert removed.status_code == 200, removed.text
            fingerprint = removed.json()["projectFingerprint"]
            assert store.read_generation(p, g).workspace.assets == []
            assert store.execution_snapshot(p, g)[1].selected is None
            assert store.execution_routing(p, g).assets == {}
            with pytest.raises(AssetPayloadError):
                store.execution_payload(p, g, a.id, "image", before.asset_requirements[0].content_sha256)
        response = client.put(url, json={"assets": [b.model_dump(mode="json", by_alias=True)],
            "subjects": [{"id": "subject-1", "number": 1}], "expectedProjectFingerprint": fingerprint})
        assert response.status_code == 200, response.text
        assert response.json()["changed"]

    # Current Main Prompt stays saved, irrespective of an unrelated browser edit.
    current = store.read_generation(p, g).workspace
    assert current.creative_request == "Saved Main Prompt"
    assert current.assets == [b]
    assert store.execution_snapshot(p, g)[1].selected is None
    assert store.saved_prompt_status(p, g).prompts[0].prompt == historical.prompts[0].prompt
    assert store.saved_prompt_status(p, g).prompts[0].stale
    with pytest.raises(ex.InvalidBindings, match="no H3 prompt"):
        prepare_workflow(store, p, g, profile)
    calls = []
    body = ExecutionSendRequest(projectId=p, generationId=g, profileId=profile.id, connectionId=connection.id,
                                expectedExecutionFingerprint=before.execution_fingerprint)
    with pytest.raises(ex.InvalidBindings):
        send_generation(body, store=store, config=config, transport=httpx.MockTransport(lambda req: calls.append(req)))
    assert calls == []
    if operation == "remove-add":
        with pytest.raises(ex.InvalidBindings, match="Picture authority changed"):
            store.select_execution_prompt(p, g, old_request)

    # The actual writer assembly uses B's derivative; execution keeps processed canonical B.
    from miniconstruct.generation import resolve_narrative_request
    from miniconstruct.models.api import GenerationRequest
    from test_story_next import LLM
    request = GenerationRequest(workspace=current, llm=LLM, filesystemProjectId=p, generationId=g)
    resolved = resolve_narrative_request(request, store)
    writer = assemble_prompt(resolved.prompt_workspace, True)
    with pytest.raises(AssetPayloadError, match="Reference Assets differ"):
        resolve_narrative_request(request.model_copy(update={"workspace": project.workspace}), store)
    serialized = json.dumps(writer.messages)
    assert prompt_writer_image(b.image.data_url)[0] in serialized
    assert prompt_writer_image(a.image.data_url)[0] not in serialized
    manifest = build_reference_manifest(current)
    assert [entry["id"] for entry in build_h3_input_map(current)] == [b.id]
    assert len(manifest["assets"]) == 1
    new_source = store.execution_snapshot(p, g)[1].current_fingerprint
    assert new_source != old_source
    store.select_execution_prompt(p, g, SelectionWrite(prompt=prompt, sourceFingerprint=new_source,
                                                       referenceMap=effective_references(current)))
    prepared = prepare_workflow(store, p, g, profile)
    requirement = prepared.asset_requirements[0]
    digest = hashlib.sha256(new_bytes).hexdigest()
    assert requirement.content_sha256 == digest != hashlib.sha256(old_bytes).hexdigest()
    name = deterministic_name(requirement, "image/jpeg")
    assert name == f"mc-{digest}.png"
    assert prepared.execution_fingerprint != before.execution_fingerprint
    # Reselecting alone cannot revive the old prepared receipt.
    with pytest.raises(ex.InvalidBindings, match="Prepared execution is stale"):
        send_generation(body, store=store, config=config, transport=httpx.MockTransport(lambda req: calls.append(req)))
    assert calls == []

    def comfy(request):
        calls.append(request)
        if request.url.path.endswith("/upload/image"):
            assert new_bytes in request.content and old_bytes not in request.content
            assert name.encode() in request.content
            return httpx.Response(200, json={"name": name, "subfolder": "", "type": "input"})
        graph = json.loads(request.content)
        assert graph["prompt"]["image-1"]["inputs"]["image"] == name
        return httpx.Response(200, json={"prompt_id": graph["prompt_id"], "number": 1, "node_errors": {}})

    result = send_generation(body.model_copy(update={"expected_execution_fingerprint": prepared.execution_fingerprint}),
        store=store, config=config, transport=httpx.MockTransport(comfy))
    assert result.submitted and len(calls) == 2
    assert store.load_project(p).workspace.assets == [b]
    assert store.load_project(p).sequence.subjects[0].id == "subject-1"
    assert execution_authoring_fingerprint(current) != execution_authoring_fingerprint(project.workspace)
    assert json.loads((store.root / f"project-{p}" / "project.json").read_text())["schemaVersion"] == 17


def test_unchanged_picture_digest_semantics_and_failure_are_atomic(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path)
    a, payload = picture("red")
    project = store.create_project(workspace_factory(mode="Ref2VA", assets=[a]))
    p, g = project.project_id, project.active_generation_id
    asset = a.model_dump(by_alias=True)
    asset["image"].pop("data_url")
    asset["imageSha256"] = hashlib.sha256(payload).hexdigest()
    receipt = store.sync_reference_assets(p, g, [asset], project.workspace.subjects, project.project_fingerprint)
    assert not receipt["changed"] and receipt["projectFingerprint"] == project.project_fingerprint
    asset["subjectIdentity"]["focus"] = "face"
    receipt = store.sync_reference_assets(p, g, [asset], project.workspace.subjects, receipt["projectFingerprint"])
    assert receipt["changed"]
    assert store.load_project(p).workspace.assets[0].subject_identity.focus == "face"
    asset["imageSha256"] = "0" * 64
    with pytest.raises(AssetPayloadError):
        store.sync_reference_assets(p, g, [asset], [], receipt["projectFingerprint"])
    assert store.load_project(p).project_fingerprint == receipt["projectFingerprint"]
    assert store.load_project(p).workspace.assets[0].image == a.image


def test_reference_boundary_allocates_only_required_subjects_and_preserves_profiles(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path)
    a, _ = picture("red")
    project = store.create_project(workspace_factory(mode="Ref2VA", assets=[a]), character_profiles=[
        {"subjectId": "subject-1", "characterProfile": {"appearance": "A scarlet coat"}}])
    p, g = project.project_id, project.active_generation_id
    b, _ = picture("blue", "new")
    b.subject_identity.subject_id = "subject-2"
    store.sync_reference_assets(p, g, [a.model_dump(by_alias=True), b.model_dump(by_alias=True)],
        [SubjectRecord(id="subject-2", number=2), SubjectRecord(id="unused", number=3)], project.project_fingerprint)
    loaded = store.load_project(p)
    assert {subject.id for subject in loaded.sequence.subjects} == {"subject-1", "subject-2"}
    assert loaded.sequence.next_subject_number == 3
    assert len(loaded.workspace.subjects) == 2
    store.sync_reference_assets(p, g, [], [], loaded.project_fingerprint)
    loaded = store.load_project(p)
    assert {subject.id for subject in loaded.sequence.subjects} == {"subject-1", "subject-2"}
    assert loaded.workspace.subjects == []
    assert loaded.sequence.subjects[0].character_profile.appearance == "A scarlet coat"


def test_removed_picture_labels_cannot_execute_from_legacy_selected_map(tmp_path, workspace_factory):
    store, config = ProjectStore(tmp_path / "projects"), ex.ExecutionStore(tmp_path / "execution")
    a, _ = picture("red", "first")
    b, _ = picture("blue", "second")
    b.order = 1
    project = store.create_project(workspace_factory(mode="Ref2VA", assets=[a, b]))
    p, g = project.project_id, project.active_generation_id
    selected = store.select_execution_prompt(p, g, SelectionWrite(prompt=valid_ref_prompt(),
        sourceFingerprint=store.execution_snapshot(p, g)[1].current_fingerprint,
        referenceMap=effective_references(project.workspace))).selected
    store.sync_reference_assets(p, g, [b.model_dump(by_alias=True)], project.workspace.subjects, project.project_fingerprint)
    current = store.read_generation(p, g).workspace
    assert [(ref.label, ref.asset_id) for ref in effective_references(current)] == [("Picture 1", "second")]
    # Simulate a legacy sidecar retaining historical selection after removal.
    directory = store.root / f"project-{p}"
    sidecar = store._read_execution_state(directory)
    sidecar.selections[g] = selected
    store._write_execution_state(directory, sidecar)
    profile = config.save("profiles", capacity_profile(2))
    with pytest.raises(ex.InvalidBindings, match="Picture authority changed"):
        prepare_workflow(store, p, g, profile)


def test_mutation_endpoint_rejects_unrelated_fields_conflicts_and_deleted_digest(tmp_path, monkeypatch, workspace_factory):
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(tmp_path))
    store = ProjectStore()
    a, payload = picture("red")
    project = store.create_project(workspace_factory(mode="Ref2VA", assets=[a]))
    p, g = project.project_id, project.active_generation_id
    url = f"/api/projects/{p}/generations/{g}/reference-assets"
    body = {"assets": [], "subjects": [], "expectedProjectFingerprint": project.project_fingerprint}
    with TestClient(app) as client:
        assert client.put(url, json={**body, "creativeRequest": "must not save"}).status_code == 422
        removed = client.put(url, json=body)
        assert removed.status_code == 200
        assert client.put(url, json=body).status_code == 409
        compact = a.model_dump(by_alias=True)
        compact["image"].pop("data_url")
        compact["imageSha256"] = hashlib.sha256(payload).hexdigest()
        response = client.put(url, json={**body, "assets": [compact],
            "expectedProjectFingerprint": removed.json()["projectFingerprint"]})
        assert response.status_code == 400
    assert store.load_project(p).workspace.assets == []


def test_removal_preserves_independent_duplicate_and_shared_blob(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path)
    a, payload = picture("red")
    project = store.create_project(workspace_factory(mode="Ref2VA", assets=[a]))
    original_id = project.active_generation_id
    duplicate = store.duplicate_generation(project.project_id, original_id)
    other_id = duplicate.active_generation_id
    # Duplicate snapshots may preserve local IDs; the mutable identity is the
    # sourceGenerationId + assetId pair, rather than project-global ID or hash.
    other = store.read_generation(project.project_id, other_id)
    receipt = store.sync_reference_assets(project.project_id, original_id, [], [], duplicate.project_fingerprint)
    assert receipt["changed"]
    assert store.read_generation(project.project_id, original_id).workspace.assets == []
    assert store.read_generation(project.project_id, other_id).workspace.assets == other.workspace.assets
    assert store.load_project(project.project_id).sequence.subjects
    assert store.execution_payload(project.project_id, other_id, a.id, "image", hashlib.sha256(payload).hexdigest())[0].read_bytes() == payload


async def test_story_proposal_becomes_stale_after_reference_mutation(tmp_path, workspace_factory):
    from test_story_next import OfflineNextClient, LLM
    from miniconstruct.models.story_operations import StoryNextRequest
    from miniconstruct.story_operations import plan_next
    from miniconstruct.project_store import StaleProjectSnapshotError
    store = ProjectStore(tmp_path)
    a, _payload = picture("red")
    project = store.create_project(workspace_factory(mode="Ref2VA", assets=[a]), story_idea="A costume change.")
    proposal = await plan_next(store, project.project_id, StoryNextRequest(
        anchorGenerationId=project.active_generation_id, requestedCount=1, llm=LLM), OfflineNextClient())
    store.sync_reference_assets(project.project_id, project.active_generation_id, [], [], project.project_fingerprint)
    with pytest.raises(StaleProjectSnapshotError):
        store.apply_story_plan_next(project.project_id, proposal)


def test_edit_guard_allows_only_render_history_drift_and_orders_equal_clock_receipts(tmp_path, workspace_factory, monkeypatch):
    from miniconstruct.models.takes import RenderSubmission, RendererJobState, TakePromptSnapshot
    from miniconstruct.project_store import StaleProjectSnapshotError
    from test_takes import select_prompt
    from uuid import uuid4
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory())
    p, g = project.project_id, project.active_generation_id
    before = store.project_revision(p)
    snapshot = TakePromptSnapshot.capture(select_prompt(store, project))
    monkeypatch.setattr(store, "_now", lambda: project.updated_at)
    submission = RenderSubmission(connectionId=uuid4(), workflowProfileId=uuid4(), workflowFingerprint="a" * 64,
        profileFingerprint="b" * 64, comfyPromptId=uuid4(), promptSnapshot=snapshot)
    first = store.record_render_submission(p, g, submission)
    store.update_render_job(p, g, submission.id, RendererJobState(status="running"))
    second = store.project_revision(p)
    assert before["projectUpdatedAt"] < first["projectUpdatedAt"] < second["projectUpdatedAt"]
    assert before["projectEditFingerprint"] == second["projectEditFingerprint"]
    # The original API remains strict without explicit narrow authority.
    with pytest.raises(StaleProjectSnapshotError):
        store.sync_reference_assets(p, g, [], [], before["projectFingerprint"])
    receipt = store.sync_reference_assets(p, g, [], [], before["projectFingerprint"], before["projectEditFingerprint"])
    assert not receipt["changed"]
    setup = store.sync_execution_setup(p, g, 9, before["projectFingerprint"], "4:3", before["projectEditFingerprint"])
    assert setup["projectEditFingerprint"] != before["projectEditFingerprint"]
    with pytest.raises(StaleProjectSnapshotError):
        store.sync_reference_assets(p, g, [], [], before["projectFingerprint"], before["projectEditFingerprint"])
