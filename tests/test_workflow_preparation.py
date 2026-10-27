"""Offline F3 regression matrix: actual ProjectStore, synthetic graphs/payloads."""
from copy import deepcopy
import asyncio
import json
import shutil
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from miniconstruct.app import app
from miniconstruct import execution as ex
from miniconstruct.execution_authoring import execution_authoring_fingerprint as fingerprint
from miniconstruct.execution_state import HistoryAdoptionWrite, ManualEditAdoptionWrite, SelectionWrite
from miniconstruct.execution_routing import effective_references
from miniconstruct.h3.builder import numbered_assets
from miniconstruct.h3.validator import validate_workspace_prompt
from miniconstruct.models.workspace import CreativeInterpretation, SubjectIdentityFocus, ReferenceAsset, validate_authoring_workspace
from miniconstruct.project_store import ProjectStore, ProjectStoreError
from miniconstruct.workflow_preparation import prepare_workflow
from miniconstruct.execution_send import ExecutionSendRequest, send_generation
from test_validator import VALID_BASE, valid_ref_prompt


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("F3 must never invoke real HTTP transport")
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)


def mapping(kind, node, name, **source):
    return {"source": {"kind": kind, **source}, "nodeId": node, "inputName": name}


def capacity_profile(count=3, *, av=False):
    graph = {"h3": {"class_type": "SyntheticH3", "inputs": {"prompt": "old prompt", "duration": 99,
                "model": "model.safetensors", "lora": "detail.safetensors", "sampler": "euler",
                "sigma": 1.5, "opaque": {"do": ["not", "interpret"]}, "context": ["context", 0]}},
             "context": {"class_type": "PromptSource", "inputs": {}}}
    bindings = [mapping("generated_prompt", "h3", "prompt"), mapping("duration", "h3", "duration")]
    slots = [("image", n, "LoadImage", "image") for n in range(1, count + 1)]
    if av:
        slots += [("audio", 1, "LoadAudio", "audio"), ("video", 1, "LoadVideo", "file")]
    for kind, ordinal, loader, name in slots:
        node = f"{kind}-{ordinal}"
        graph[node] = {"class_type": loader, "inputs": {name: f"old_{node}.test"}}
        graph["h3"]["inputs"][node] = [node, 0]
        bindings.append({**mapping("reference_asset", node, name, assetKind=kind, ordinal=ordinal),
                         "controlledConsumers": [{"nodeId": "h3", "inputName": node, "outputIndex": 0}]})
        if kind == "video":
            bindings[-1].pop("controlledConsumers")
            bindings[-1]["routes"] = [{"nodeId": "h3", "inputName": node, "expectedSourceNodeId": node,
                                      "expectedOutputIndex": 0, "channel": "video_frames"}]
    return ex.ProfileCreate(name="Capacity", compatibleModes=list(ex.H3Mode), workflowJson=json.dumps(graph), bindings=bindings)


@pytest.fixture
def store(tmp_path):
    return ProjectStore(tmp_path / "projects")


@pytest.fixture
def profile(tmp_path):
    return ex.ExecutionStore(tmp_path / "execution").save("profiles", capacity_profile())


def select(store, project, prompt=VALID_BASE, **overrides):
    return store.select_execution_prompt(project.project_id, project.active_generation_id,
        SelectionWrite(prompt=prompt, sourceFingerprint=fingerprint(project.workspace), **overrides))


def prepared(store, project, profile):
    return prepare_workflow(store, project.project_id, project.active_generation_id, profile)


@pytest.mark.parametrize("save_duration", [False, True], ids=["visible-unsaved-duration", "project-saved-duration"])
def test_prepare_binds_current_one_shot_h3_duration(save_duration, store, workspace_factory, profile):
    project = store.create_project(workspace_factory(durationSeconds=6))
    select(store, project, authoringPath="one_shot")
    six_seconds = prepare_workflow(store, project.project_id, project.active_generation_id, profile)
    assert six_seconds.graph["h3"]["inputs"]["duration"] == 6

    if save_duration:
        project = store.save_project(project.project_id,
            project.workspace.model_copy(update={"duration_seconds": 9}),
            expected_project_fingerprint=project.project_fingerprint)
    else:
        store.sync_execution_duration(project.project_id, project.active_generation_id, 9,
                                      project.project_fingerprint)

    nine_seconds = prepare_workflow(store, project.project_id, project.active_generation_id, profile)
    assert nine_seconds.graph["h3"]["inputs"]["duration"] == 9
    assert nine_seconds.execution_fingerprint != six_seconds.execution_fingerprint
    assert "different saved authoring" in " ".join(nine_seconds.warnings)
    assert store.read_generation(project.project_id, project.active_generation_id).workspace.duration_seconds == 9


def test_execution_setup_sync_is_bounded_atomic_and_concurrency_checked(tmp_path, monkeypatch, workspace_factory):
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(tmp_path / "projects"))
    store = ProjectStore()
    project = store.create_project(workspace_factory(durationSeconds=6, aspectRatio="4:3", creativeRequest="Saved request"))
    other = store.create_generation(project.project_id)
    other_workspace = store.read_generation(project.project_id, other.active_generation_id).workspace
    original = store.read_generation(project.project_id, project.active_generation_id).workspace.model_dump(by_alias=True)
    fingerprint = other.project_fingerprint
    url = f"/api/execution/projects/{project.project_id}/generations/{project.active_generation_id}/execution-setup"
    body = {"durationSeconds": 8, "aspectRatio": "16:9", "expectedProjectFingerprint": fingerprint}
    with TestClient(app) as client:
        for invalid in ["", "x" * 41, 169, None]:
            assert client.put(url, json={**body, "aspectRatio": invalid}).status_code == 422
        assert client.put(url, json={**body, "creativeRequest": "Dirty request"}).status_code == 422
        assert store.load_project(project.project_id).project_fingerprint == fingerprint
        response = client.put(url, json=body)
        assert response.status_code == 200
        receipt = response.json()
        assert receipt["projectFingerprint"] != fingerprint
        assert client.put(url, json={**body, "expectedProjectFingerprint": receipt["projectFingerprint"]}).json() == receipt
        stale = client.put(url, json={**body, "aspectRatio": "9:16"})
        assert stale.status_code == 422
        assert "Project changed since loading" in stale.text
    saved = store.read_generation(project.project_id, project.active_generation_id).workspace.model_dump(by_alias=True)
    assert saved == {**original, "durationSeconds": 8.0, "aspectRatio": "16:9"}
    assert store.read_generation(project.project_id, other.active_generation_id).workspace == other_workspace


@pytest.mark.parametrize("ratio,bound,expected", [
    ("16:9", True, "16:9 (Widescreen)"), ("4:3", True, "4:3 (Standard)"),
    ("9:16", True, "9:16 (Portrait Widescreen)"), ("1:1", True, "1:1 (Square)"), ("3:4", True, "3:4 (Portrait Standard)"),
    ("auto", True, "auto"), ("custom:11/7", True, "custom:11/7"),
    ("16:9", False, "4:3 (Standard)"),
])
def test_bounded_setup_reaches_prepared_and_submitted_graph(store, tmp_path, workspace_factory, ratio, bound, expected):
    draft = capacity_profile(0)
    graph = json.loads(draft.workflow_json)
    graph["ratio"] = {"class_type": "ResolutionSelector", "inputs": {"aspect_ratio": "4:3 (Standard)"}}
    bindings = list(draft.bindings)
    if bound:
        bindings.append(ex.Binding.model_validate(mapping("aspect_ratio", "ratio", "aspect_ratio",
            valueMap={"16:9": "16:9 (Widescreen)", "4:3": "4:3 (Standard)", "9:16": "9:16 (Portrait Widescreen)", "1:1": "1:1 (Square)", "3:4": "3:4 (Portrait Standard)"})))
    config = ex.ExecutionStore(tmp_path / "execution")
    profile = config.save("profiles", draft.model_copy(update={"workflow_json": json.dumps(graph), "bindings": bindings}))
    profile = config.save("profiles", ex.ProfileUpdate(name="Renamed", compatibleModes=profile.compatible_modes,
        bindings=profile.bindings, exposedControls=profile.exposed_controls), profile.id)
    profile = ex.ExecutionStore(tmp_path / "execution").get("profiles", profile.id)
    if bound:
        assert profile.bindings[-1].source.value_map["16:9"] == "16:9 (Widescreen)"
    project = store.create_project(workspace_factory(durationSeconds=6, aspectRatio="4:3"))
    select(store, project)
    old = prepared(store, project, profile)
    assert old.graph["ratio"]["inputs"]["aspect_ratio"] == "4:3 (Standard)"
    receipt = store.sync_execution_setup(project.project_id, project.active_generation_id, 8, project.project_fingerprint, ratio)
    assert receipt["aspectRatio"] == ratio
    result = prepared(store, project, profile)
    assert result.graph["ratio"]["inputs"]["aspect_ratio"] == expected
    assert result.graph["h3"]["inputs"]["duration"] == 8
    if bound:
        resolution = next(item for item in result.resolutions if item.node_id == "ratio")
        assert (resolution.source_value, resolution.renderer_value) == (ratio, expected)
    if bound and ratio == "custom:11/7":
        assert any("raw value will be sent" in warning for warning in result.warnings)
    connection = config.save("connections", ex.ConnectionWrite(name="Offline", baseUrl="http://never.test"))
    sent = []
    def queue(request):
        body = json.loads(request.content)
        assert request.url.path == "/prompt"
        sent.append(body)
        return httpx.Response(200, json={"prompt_id": body["prompt_id"], "number": 1, "node_errors": {}})
    with pytest.raises(ex.InvalidBindings, match="stale"):
        send_generation(ExecutionSendRequest(projectId=project.project_id, generationId=project.active_generation_id,
            profileId=profile.id, connectionId=connection.id, expectedExecutionFingerprint=old.execution_fingerprint),
            store=store, config=config, transport=httpx.MockTransport(queue))
    assert not sent
    send_generation(ExecutionSendRequest(projectId=project.project_id, generationId=project.active_generation_id,
        profileId=profile.id, connectionId=connection.id, expectedExecutionFingerprint=result.execution_fingerprint),
        store=store, config=config, transport=httpx.MockTransport(queue))
    assert sent[0]["prompt"]["ratio"]["inputs"]["aspect_ratio"] == expected
    assert sent[0]["prompt"]["h3"]["inputs"]["duration"] == 8


def test_prepare_uses_manual_duration_override_for_story_generation_without_rewriting_planning(store, workspace_factory, profile):
    project = store.create_project(workspace_factory(durationSeconds=6), story_idea="A cyclist reaches shelter.")
    project = store.save_project(project.project_id, project.workspace,
        planning={"scenePlan": "The cyclist rides through rain and reaches shelter."},
        expected_project_fingerprint=project.project_fingerprint)
    _generation, status = store.execution_snapshot(project.project_id, project.active_generation_id)
    assert status.current_fingerprints["story"]
    store.select_execution_prompt(project.project_id, project.active_generation_id,
        SelectionWrite(prompt=VALID_BASE, sourceFingerprint=status.current_fingerprints["story"], authoringPath="story"))

    store.sync_execution_duration(project.project_id, project.active_generation_id, 9,
                                  project.project_fingerprint)
    result = prepare_workflow(store, project.project_id, project.active_generation_id, profile)

    assert result.graph["h3"]["inputs"]["duration"] == 9
    assert store.read_generation(project.project_id, project.active_generation_id).planning.scene_plan == project.planning.scene_plan


def test_execution_duration_sync_endpoint_updates_only_current_generation_duration(tmp_path, monkeypatch, workspace_factory):
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(tmp_path / "projects"))
    store = ProjectStore()
    project = store.create_project(workspace_factory(durationSeconds=6, creativeRequest="Saved request"))
    url = f"/api/execution/projects/{project.project_id}/generations/{project.active_generation_id}/execution-duration"
    with TestClient(app) as client:
        response = client.put(url, json={"durationSeconds": 9,
            "expectedProjectFingerprint": project.project_fingerprint})
        invalid = client.put(url, json={"durationSeconds": 16,
            "expectedProjectFingerprint": response.json()["projectFingerprint"]})

    assert response.status_code == 200
    saved = store.read_generation(project.project_id, project.active_generation_id)
    assert saved.workspace.duration_seconds == 9
    assert saved.workspace.creative_request == "Saved request"
    assert invalid.status_code == 422


@pytest.mark.parametrize("source_kind", ["generated_prompt", "manual_edit", "adopted_history"])
def test_story_selection_with_blank_main_prompt_prepares_exact_h3_via_api(
    tmp_path, monkeypatch, workspace_factory, source_kind,
):
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("MINICONSTRUCT_EXECUTION_ROOT", str(tmp_path / "execution"))
    store = ProjectStore()
    project = store.create_project(workspace_factory(), story_idea="A cyclist reaches shelter.")
    store.save_project(project.project_id, project.workspace.model_copy(update={"creative_request": ""}),
                       planning={"scenePlan": "The cyclist rides through rain and reaches shelter."})
    profile = ex.ExecutionStore().save("profiles", capacity_profile(0))
    selection_path = f"/api/execution/projects/{project.project_id}/generations/{project.active_generation_id}/selection"
    exact_prompt = VALID_BASE.replace("Rain and distant traffic.", "Heavy rain against the shelter roof.")
    with TestClient(app) as client:
        status = client.get(selection_path).json()
        assert status["currentFingerprints"]["story"]
        if source_kind == "generated_prompt":
            selected = client.put(selection_path, json={"prompt": exact_prompt,
                "sourceFingerprint": status["currentFingerprints"]["story"], "authoringPath": "story"})
        else:
            endpoint = "adopt-manual-edit" if source_kind == "manual_edit" else "adopt-history"
            selected = client.post(f"{selection_path}/{endpoint}",
                                   json={"prompt": exact_prompt, "authoringPath": "story"})
        assert selected.status_code == 200, selected.text
        assert selected.json()["selected"]["sourceKind"] == source_kind
        prepared_result = client.post("/api/execution/prepare", json={
            "projectId": project.project_id, "generationId": project.active_generation_id,
            "profileId": str(profile.id),
        })
        assert prepared_result.status_code == 200, prepared_result.text
        prepared_body = prepared_result.json()
        assert prepared_body["graph"]["h3"]["inputs"]["prompt"] == exact_prompt
        assert prepared_body["selectedPrompt"]["authoringPath"] == "story"
        assert prepared_body["selectedPrompt"]["prompt"] == exact_prompt
        client.delete(selection_path)
        adopted_again = client.post(f"{selection_path}/adopt-manual-edit",
                               json={"prompt": exact_prompt, "authoringPath": "one_shot"})
        assert adopted_again.status_code == 200
        assert adopted_again.json()["selected"]["prompt"] == exact_prompt


def ref_workspace(workspace_factory, image_asset_factory, count=1):
    return workspace_factory(mode="Ref2VA", assets=[image_asset_factory(f"pic-{n}", role="general_visual", order=n,
        filename=f"reference-{n}.png") for n in range(1, count + 1)])


def test_none_by_default_and_restart(store, workspace_factory):
    p = store.create_project(workspace_factory())
    assert store.execution_snapshot(p.project_id, p.active_generation_id)[1].selected is None
    chosen = select(store, p)
    assert chosen.selected.prompt == VALID_BASE and not chosen.stale
    restored = ProjectStore(store.root).execution_snapshot(p.project_id, p.active_generation_id)[1]
    assert restored == chosen
    assert "schemaVersion" in json.loads((store.root / f"project-{p.project_id}" / "execution-state.json").read_text())


@pytest.mark.parametrize("prompt", ["", "   "])
def test_bad_selection_rejected(store, workspace_factory, prompt):
    p = store.create_project(workspace_factory())
    with pytest.raises((ValidationError, ex.InvalidBindings)):
        select(store, p, prompt)
    assert store.execution_snapshot(p.project_id, p.active_generation_id)[1].selected is None


def test_snapshot_mismatch_adopts_exact_text(store, workspace_factory):
    p = store.create_project(workspace_factory())
    selected = store.select_execution_prompt(p.project_id, p.active_generation_id,
        SelectionWrite(prompt=VALID_BASE, sourceFingerprint="f" * 64)).selected
    assert selected.prompt == VALID_BASE and selected.source_kind == "manual_edit"


def test_makoto_japanese_candidate_audit_and_exact_execution(store, workspace_factory, profile):
    source = 'Makoto: "You\'re so beautiful."'
    japanese = 'Makoto: 「君はとても美しいね。」'
    prompt = VALID_BASE.replace("a cyclist waits.",
        "Makoto says <d>[Japanese] 君はとても美しいね。</d> and <Audio 1> plays.")
    p = store.create_project(workspace_factory().model_copy(update={"dialogue": source}))
    japanese_workspace = p.workspace.model_copy(update={"dialogue": japanese})
    quote_audit = validate_workspace_prompt(prompt.replace(" and <Audio 1> plays", ""), japanese_workspace)
    assert not any(f.code == "dialogue_changed" for f in quote_audit.findings)
    audit = validate_workspace_prompt(prompt, japanese_workspace)
    assert any(f.code == "dangling_asset" and f.severity == "ERROR" for f in audit.findings)
    assert any(f.code == "dialogue_changed" for f in validate_workspace_prompt(prompt, p.workspace).findings)

    current = store.execution_snapshot(p.project_id, p.active_generation_id)[1].current_fingerprint
    selected = store.select_execution_prompt(p.project_id, p.active_generation_id,
        SelectionWrite(prompt=prompt, sourceFingerprint=current)).selected
    assert selected.prompt == prompt and selected.source_kind == "generated_prompt"
    first = prepared(store, p, profile)
    assert first.graph["h3"]["inputs"]["prompt"] == prompt
    adopted_history = store.adopt_history_execution_prompt(p.project_id, p.active_generation_id,
        HistoryAdoptionWrite(prompt=prompt)).selected
    assert adopted_history.source_kind == "adopted_history" and adopted_history.prompt == prompt
    assert prepared(store, p, profile).graph["h3"]["inputs"]["prompt"] == prompt

    store.save_project(p.project_id, p.workspace.model_copy(update={"creative_request": "New saved authority"}))
    assert store.execution_snapshot(p.project_id, p.active_generation_id)[1].stale
    stale = prepared(store, p, profile)
    assert stale.graph["h3"]["inputs"]["prompt"] == prompt
    adopted = store.select_execution_prompt(p.project_id, p.active_generation_id,
        SelectionWrite(prompt=prompt, sourceFingerprint=current)).selected
    assert adopted.source_kind == "manual_edit" and adopted.prompt == prompt
    manual = prompt + "\n  exact manual tail"
    selected = store.adopt_manual_edit_execution_prompt(p.project_id, p.active_generation_id,
        ManualEditAdoptionWrite(prompt=manual)).selected
    assert selected.prompt == manual
    assert prepared(store, p, profile).graph["h3"]["inputs"]["prompt"] == manual


def test_reference_map_changes_fail_closed_after_save(store, workspace_factory,
                                                                            image_asset_factory, tmp_path):
    workspace = ref_workspace(workspace_factory, image_asset_factory, 2)
    p = store.create_project(workspace)
    prompt = valid_ref_prompt()
    old_map = effective_references(p.workspace)
    select(store, p, prompt, referenceMap=old_map)
    profile = ex.ExecutionStore(tmp_path / "profiles").save("profiles", capacity_profile(2))
    reordered = p.workspace.model_copy(update={"assets": [
        p.workspace.assets[0].model_copy(update={"order": 2}),
        p.workspace.assets[1].model_copy(update={"order": 1})]})
    store.save_project(p.project_id, reordered)
    with pytest.raises(ex.InvalidBindings, match="no H3 prompt"):
        prepared(store, p, profile)
    with pytest.raises(ex.InvalidBindings, match="Picture authority changed"):
        select(store, p, prompt, referenceMap=old_map)

    removed = reordered.model_copy(update={"assets": []})
    store.save_project(p.project_id, removed)
    with pytest.raises(ex.InvalidBindings, match="no H3 prompt"):
        prepared(store, p, profile)


def test_unrelated_routing_change_and_unknown_history_map_are_advisory(store, workspace_factory,
                                                                         image_asset_factory, tmp_path):
    p = store.create_project(ref_workspace(workspace_factory, image_asset_factory, 2))
    profile = ex.ExecutionStore(tmp_path / "profiles").save("profiles", capacity_profile(2))
    select(store, p, VALID_BASE, referenceMap=effective_references(p.workspace))
    store.save_project(p.project_id, p.workspace.model_copy(update={"assets": [p.workspace.assets[0]]}))
    with pytest.raises(ex.InvalidBindings, match="no H3 prompt"):
        prepared(store, p, profile)
    store.adopt_history_execution_prompt(p.project_id, p.active_generation_id,
        HistoryAdoptionWrite(prompt="invalid H3 candidate"))
    result = prepared(store, p, profile)
    assert result.ready and result.graph["h3"]["inputs"]["prompt"] == "invalid H3 candidate"
    assert not any("Original reference-label mapping is unavailable" in w for w in result.warnings)


@pytest.mark.parametrize("source_kind", ["history", "manual"])
def test_unknown_reference_map_warns_only_for_media_labels(store, workspace_factory,
                                                           image_asset_factory, tmp_path, source_kind):
    p = store.create_project(ref_workspace(workspace_factory, image_asset_factory))
    profile = ex.ExecutionStore(tmp_path / "profiles").save("profiles", capacity_profile(1))
    prompts = ("Subject 1 watches.", "<Picture 1> shows the setting.")
    for prompt in prompts:
        if source_kind == "history":
            store.adopt_history_execution_prompt(p.project_id, p.active_generation_id,
                HistoryAdoptionWrite(prompt=prompt))
        else:
            store.adopt_manual_edit_execution_prompt(p.project_id, p.active_generation_id,
                ManualEditAdoptionWrite(prompt=prompt))
        result = prepared(store, p, profile)
        assert result.ready and result.graph["h3"]["inputs"]["prompt"] == prompt
        warning = any("Original reference-label mapping is unavailable" in w for w in result.warnings)
        assert warning == ("<Picture 1>" in prompt)
        assert any(d.code == "reference.history_unavailable" and d.severity == "warning"
                   for d in result.diagnostics) == ("<Picture 1>" in prompt)


def test_invalid_h3_findings_remain_advisory_for_explicit_prepare(store, workspace_factory, profile):
    prompt = VALID_BASE + " <Audio 1> Subject 1"
    audit = validate_workspace_prompt(prompt, workspace_factory())
    assert not audit.valid
    assert "dangling_asset" in {f.code for f in audit.findings}
    assert "bare_reference_label" not in {f.code for f in audit.findings}
    p = store.create_project(workspace_factory())
    select(store, p, prompt)
    result = prepared(store, p, profile)
    assert result.ready and result.graph["h3"]["inputs"]["prompt"] == prompt


def test_profile_handoff_projection_difference_warns_and_prepares(store, workspace_factory, profile, monkeypatch):
    from miniconstruct.visual_handoff import HandoffProjection
    p = store.create_project(workspace_factory())
    select(store, p)
    original = store.visual_handoff

    def changed_projection(project_id, generation_id=None, workspace=None, *, profile=None, **kwargs):
        result = original(project_id, generation_id, workspace, profile=profile, **kwargs)
        result.identity = {"strategy": "profile-specific" if profile else "authoring"}
        return result

    monkeypatch.setattr(store, "visual_handoff", changed_projection)
    result = prepared(store, p, profile)
    assert result.ready and result.graph["h3"]["inputs"]["prompt"] == VALID_BASE
    assert any("changes visual handoff evidence" in warning for warning in result.warnings)


def test_history_adoption_computes_fingerprint_and_marks_source_honestly(store, workspace_factory):
    p = store.create_project(workspace_factory())
    selected = store.adopt_history_execution_prompt(p.project_id, p.active_generation_id,
                                                    HistoryAdoptionWrite(prompt=VALID_BASE)).selected
    assert selected.prompt == VALID_BASE
    assert selected.source_fingerprint == fingerprint(p.workspace)
    assert selected.source_kind == "adopted_history"
    restored = ProjectStore(store.root).execution_snapshot(p.project_id, p.active_generation_id)[1].selected
    assert restored.source_kind == "adopted_history"
    sidecar = json.loads((store.root / f"project-{p.project_id}" / "execution-state.json").read_text())
    assert sidecar["schemaVersion"] == 4


def test_old_sidecar_without_source_kind_defaults_to_generated_prompt(store, workspace_factory):
    p = store.create_project(workspace_factory())
    select(store, p)
    sidecar = store.root / f"project-{p.project_id}" / "execution-state.json"
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    data["selections"][p.active_generation_id].pop("sourceKind", None)
    sidecar.write_text(json.dumps(data), encoding="utf-8")
    restored = ProjectStore(store.root).execution_snapshot(p.project_id, p.active_generation_id)[1].selected
    assert restored.source_kind == "generated_prompt"


def test_history_adoption_accepts_validation_errors(store, workspace_factory):
    p = store.create_project(workspace_factory())
    for prompt in ("not an H3 prompt", valid_ref_prompt()):
        selected = store.adopt_history_execution_prompt(p.project_id, p.active_generation_id,
            HistoryAdoptionWrite(prompt=prompt)).selected
        assert selected.prompt == prompt and selected.source_kind == "adopted_history"


def test_manual_edit_adoption_computes_current_authority_and_marks_source_honestly(store, workspace_factory):
    p = store.create_project(workspace_factory())
    selected = store.adopt_manual_edit_execution_prompt(p.project_id, p.active_generation_id,
                                                        ManualEditAdoptionWrite(prompt=VALID_BASE)).selected
    assert selected.prompt == VALID_BASE
    assert selected.source_fingerprint == fingerprint(p.workspace)
    assert selected.reference_map == []
    assert selected.source_kind == "manual_edit"
    restored = ProjectStore(store.root).execution_snapshot(p.project_id, p.active_generation_id)[1].selected
    assert restored.source_kind == "manual_edit"
    sidecar = json.loads((store.root / f"project-{p.project_id}" / "execution-state.json").read_text())
    assert sidecar["schemaVersion"] == 4


def test_manual_edit_adoption_derives_current_reference_map_and_rejects_missing_reference(store, workspace_factory, image_asset_factory):
    p = store.create_project(ref_workspace(workspace_factory, image_asset_factory))
    selected = store.adopt_manual_edit_execution_prompt(p.project_id, p.active_generation_id,
                                                        ManualEditAdoptionWrite(prompt=valid_ref_prompt())).selected
    assert [(ref.label, ref.asset_id) for ref in selected.reference_map] == [("Picture 1", "pic-1")]
    no_picture = store.create_project(workspace_factory())
    selected = store.adopt_manual_edit_execution_prompt(no_picture.project_id, no_picture.active_generation_id,
        ManualEditAdoptionWrite(prompt=valid_ref_prompt())).selected
    assert selected.prompt == valid_ref_prompt()


def test_manual_edit_adoption_rejects_client_provenance_but_accepts_invalid_h3(store, workspace_factory):
    p = store.create_project(workspace_factory())
    with pytest.raises(ValidationError):
        ManualEditAdoptionWrite.model_validate({"prompt": VALID_BASE, "sourceFingerprint": "f" * 64,
                                                "referenceMap": []})
    for prompt in ("not an H3 prompt", valid_ref_prompt()):
        selected = store.adopt_manual_edit_execution_prompt(p.project_id, p.active_generation_id,
            ManualEditAdoptionWrite(prompt=prompt)).selected
        assert selected.prompt == prompt


def test_history_adoption_requires_server_computed_authority_and_prepare_uses_selection(tmp_path, monkeypatch, workspace_factory):
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("MINICONSTRUCT_EXECUTION_ROOT", str(tmp_path / "execution"))
    project = ProjectStore().create_project(workspace_factory())
    profile = ex.ExecutionStore().save("profiles", capacity_profile(0))
    path = f"/api/execution/projects/{project.project_id}/generations/{project.active_generation_id}/selection/adopt-history"
    body = {"prompt": VALID_BASE}
    with TestClient(app) as client:
        assert client.post(path, json={**body, "sourceFingerprint": "f" * 64}).status_code == 422
        selection_path = path.removesuffix("/adopt-history")
        assert client.get(selection_path).json()["selected"] is None
        adopted = client.post(path, json=body)
        assert adopted.status_code == 200, adopted.text
        selected = adopted.json()["selected"]
        assert selected["sourceFingerprint"] == fingerprint(project.workspace)
        assert selected["sourceKind"] == "adopted_history"
        prepared_result = client.post("/api/execution/prepare", json={
            "projectId": project.project_id, "generationId": project.active_generation_id,
            "profileId": str(profile.id),
        })
        assert prepared_result.status_code == 200, prepared_result.text
        assert prepared_result.json()["sentToComfyUI"] is False


def test_manual_edit_adoption_route_computes_authority_and_prepare_accepts_selection(tmp_path, monkeypatch, workspace_factory):
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("MINICONSTRUCT_EXECUTION_ROOT", str(tmp_path / "execution"))
    project = ProjectStore().create_project(workspace_factory())
    profile = ex.ExecutionStore().save("profiles", capacity_profile(0))
    path = f"/api/execution/projects/{project.project_id}/generations/{project.active_generation_id}/selection/adopt-manual-edit"
    selection_path = path.removesuffix("/adopt-manual-edit")
    original = VALID_BASE
    edited = VALID_BASE.replace("Rain and distant traffic.", "Heavy rain against the shelter roof.")
    with TestClient(app) as client:
        assert client.post(path, json={"prompt": VALID_BASE, "sourceFingerprint": "f" * 64,
                                       "referenceMap": []}).status_code == 422
        assert client.put(selection_path, json={"prompt": original,
            "sourceFingerprint": fingerprint(project.workspace)}).status_code == 200
        accepted = client.post(path, json={"prompt": "invalid H3 candidate"})
        assert accepted.status_code == 200
        assert client.get(selection_path).json()["selected"]["prompt"] == "invalid H3 candidate"
        adopted = client.post(path, json={"prompt": edited})
        assert adopted.status_code == 200, adopted.text
        selected = adopted.json()["selected"]
        assert selected["prompt"] == edited
        assert selected["sourceFingerprint"] == fingerprint(project.workspace)
        assert selected["referenceMap"] == []
        assert selected["sourceKind"] == "manual_edit"
        sidecar = ProjectStore().execution_snapshot(project.project_id, project.active_generation_id)[1].selected
        assert sidecar.prompt == edited and sidecar.source_kind == "manual_edit"
        prepared_result = client.post("/api/execution/prepare", json={
            "projectId": project.project_id, "generationId": project.active_generation_id,
            "profileId": str(profile.id),
        })
        assert prepared_result.status_code == 200, prepared_result.text
        assert prepared_result.json()["sentToComfyUI"] is False
        assert prepared_result.json()["selectedPrompt"]["prompt"] == edited
        assert prepared_result.json()["graph"]["h3"]["inputs"]["prompt"] == edited
        client.delete(selection_path)
        assert client.get(selection_path).json()["selected"] is None
        assert client.post(path, json={"prompt": VALID_BASE}).json()["selected"]["sourceKind"] == "manual_edit"


def test_replace_clear_scoped_and_story_unchanged(store, workspace_factory):
    p = store.create_project(workspace_factory())
    other = store.create_project(workspace_factory())
    story = store.read_story_snapshot(p.project_id)
    first = select(store, p).selected
    second = select(store, p, VALID_BASE.replace("Rain", "Wind")).selected
    assert second.id != first.id and second.prompt != first.prompt
    assert store.execution_snapshot(other.project_id, other.active_generation_id)[1].selected is None
    after = store.read_story_snapshot(p.project_id)
    assert story.fingerprint == after.fingerprint and story.manifest.updated_at == after.manifest.updated_at
    assert after.manifest.schema_version == 17
    assert store.clear_execution_prompt(p.project_id, p.active_generation_id).selected is None
    assert store.read_story_snapshot(p.project_id).fingerprint == story.fingerprint


def test_new_duplicate_delete_generation(store, workspace_factory):
    p = store.create_project(workspace_factory())
    original = select(store, p).selected
    duplicate = store.duplicate_generation(p.project_id)
    assert store.execution_snapshot(p.project_id, duplicate.active_generation_id)[1].selected is None
    chosen = select(store, duplicate).selected
    store.delete_generation(p.project_id, duplicate.active_generation_id)
    assert store.execution_snapshot(p.project_id, p.active_generation_id)[1].selected == original
    sidecar = json.loads((store.root / f"project-{p.project_id}" / "execution-state.json").read_text())
    assert duplicate.active_generation_id not in sidecar["selections"]
    fresh = store.create_generation(p.project_id)
    assert store.execution_snapshot(p.project_id, fresh.active_generation_id)[1].selected is None


@pytest.mark.parametrize("changed", [False, True])
def test_save_as_preserves_provenance_and_remaps_ids(store, workspace_factory, changed):
    p = store.create_project(workspace_factory())
    selected = select(store, p).selected
    fork = store.duplicate_project(p.project_id, "Fork", active_workspace=workspace_factory(durationSeconds=9) if changed else None)
    result = store.execution_snapshot(fork.project_id, fork.active_generation_id)[1]
    assert fork.active_generation_id != p.active_generation_id
    assert result.selected == selected and result.stale == changed
    assert store.execution_snapshot(p.project_id, p.active_generation_id)[1].selected == selected


def test_directory_copy_and_project_delete(store, workspace_factory, tmp_path):
    p = store.create_project(workspace_factory())
    chosen = select(store, p)
    copied = tmp_path / "backup"
    shutil.copytree(store.root, copied)
    assert ProjectStore(copied).execution_snapshot(p.project_id, p.active_generation_id)[1] == chosen
    store.delete_project(p.project_id)
    assert not (store.root / f"project-{p.project_id}").exists()


def test_stale_save_warns_and_prepare_uses_selected_bytes(store, profile, workspace_factory):
    p = store.create_project(workspace_factory())
    chosen = select(store, p)
    store.save_project(p.project_id, workspace_factory(durationSeconds=9))
    state = store.execution_snapshot(p.project_id, p.active_generation_id)[1]
    assert state.stale and state.selected == chosen.selected
    result = prepared(store, p, profile)
    assert result.graph["h3"]["inputs"]["prompt"] == chosen.selected.prompt
    assert any("different saved authoring" in warning for warning in result.warnings)
    store.save_project(p.project_id, workspace_factory())
    assert not store.execution_snapshot(p.project_id, p.active_generation_id)[1].stale
    assert prepared(store, p, profile).ready


@pytest.mark.parametrize("change", [
    {"duration_seconds": 9}, {"creative_request": "Changed"}, {"dialogue": "Hello"},
    {"shots": 3}, {"aspect_ratio": "9:16"}, {"reference_labels": "Changed"},
    {"creative_interpretation": CreativeInterpretation.EXPANSIVE}, {"variations": 2},
])
def test_fingerprint_authoring_fields(workspace_factory, change):
    w = workspace_factory()
    assert fingerprint(w) != fingerprint(w.model_copy(update=change))


@pytest.mark.parametrize("field,value", [("role", "environment"), ("notes", "different"), ("options", {"crop": True}),
                                         ("filename", "renamed.png")])
def test_reference_metadata_affects_fingerprint(workspace_factory, image_asset_factory, field, value):
    w = ref_workspace(workspace_factory, image_asset_factory)
    changed = w.model_copy(deep=True)
    setattr(changed.assets[0], field, value)
    assert fingerprint(changed) != fingerprint(w)


def test_payload_storage_invariant_and_content_change(store, workspace_factory, image_asset_factory):
    w = ref_workspace(workspace_factory, image_asset_factory)
    p = store.create_project(w)
    hydrated = store.load_project(p.project_id).workspace
    assert fingerprint(w) == fingerprint(hydrated) == fingerprint(deepcopy(w))
    changed = deepcopy(w)
    changed.assets[0].image.data_url = "data:image/png;base64,AQ=="
    assert fingerprint(changed) != fingerprint(w)
    assert fingerprint(w.model_copy(update={"project_name": "Renamed", "project_id": str(uuid4())})) == fingerprint(w)


def test_subject_relationship_changes(workspace_factory, image_asset_factory):
    w = workspace_factory(mode="Ref2VA", assets=[image_asset_factory()])
    changed = deepcopy(w)
    changed.assets[0].subject_identity.focus = SubjectIdentityFocus.FACE
    assert fingerprint(changed) != fingerprint(w)
    changed = deepcopy(w)
    changed.subjects[0].number = 3
    assert fingerprint(changed) != fingerprint(w)


@pytest.mark.parametrize("mode,roles", [
    ("T2VA", []), ("I2VA", ["first_frame_anchor"]),
    ("FL2VA", ["last_frame_anchor", "first_frame_anchor"]),
    ("L2VA", ["last_frame_anchor"]), ("Ref2VA", ["general_visual"]),
])
def test_all_modes_and_canonical_anchors(store, profile, workspace_factory, image_asset_factory, mode, roles):
    w = workspace_factory(mode=mode, assets=[image_asset_factory(f"a-{i}", role=role, order=i) for i, role in enumerate(roles)])
    prompt = valid_ref_prompt() if mode == "Ref2VA" else VALID_BASE
    if mode == "I2VA":
        prompt = "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.\n\n" + prompt
    if mode == "FL2VA":
        prompt = "How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark; Picture 2 (from Shot 2) aligns with the 8.00-second mark.\n\n" + prompt
    if mode == "L2VA":
        prompt = "How the reference pictures align with the target video — <Picture 1> (from [Shot 2]) aligns with the 8.00-second mark.\n\n" + prompt
    p = store.create_project(w)
    select(store, p, prompt)
    result = prepared(store, p, profile)
    assert result.mode == mode and len(result.asset_requirements) == len(roles)
    assert result.graph["h3"]["inputs"]["prompt"] == prompt
    assert result.graph["h3"]["inputs"]["duration"] == 8
    if mode == "FL2VA":
        assert result.asset_requirements[0].asset_id == "a-1"


def test_compiler_copy_determinism_and_no_connection(store, profile, workspace_factory):
    p = store.create_project(workspace_factory())
    select(store, p)
    original = profile.model_dump(mode="json")
    a, b = prepared(store, p, profile), prepared(store, p, profile)
    assert a == b and not a.sent_to_comfyui and not a.transport_ready
    assert profile.model_dump(mode="json") == original
    a.graph["h3"]["inputs"]["model"] = "mutated"
    assert b.graph["h3"]["inputs"]["model"] == "model.safetensors"
    assert profile.model_dump(mode="json") == original


@pytest.mark.parametrize("failure", ["missing_selection", "mode", "missing_prompt_binding", "missing_reference_binding"])
def test_prepare_rejections(store, profile, workspace_factory, image_asset_factory, failure):
    w = ref_workspace(workspace_factory, image_asset_factory) if failure == "missing_reference_binding" else workspace_factory()
    p = store.create_project(w)
    if failure != "missing_selection": select(store, p, valid_ref_prompt() if failure == "missing_reference_binding" else VALID_BASE)
    if failure == "mode": profile.compatible_modes = [ex.H3Mode.I2VA]
    if failure == "missing_prompt_binding": profile.bindings = profile.bindings[1:]
    if failure == "missing_reference_binding": profile.bindings = profile.bindings[:2]
    with pytest.raises(ex.InvalidBindings): prepared(store, p, profile)


@pytest.mark.parametrize("count", [1, 2, 3])
def test_same_profile_variable_capacity(store, profile, workspace_factory, image_asset_factory, count):
    p = store.create_project(ref_workspace(workspace_factory, image_asset_factory, count))
    select(store, p, valid_ref_prompt())
    before = deepcopy(profile.workflow)
    result = prepared(store, p, profile)
    assert [a.semantic_label for a in result.asset_requirements] == [f"Picture {n}" for n in range(1, count + 1)]
    assert result.inactive_slots == [f"Picture {n}" for n in range(count + 1, 4)]
    for n in range(count + 1, 4):
        assert f"image-{n}" not in result.graph["h3"]["inputs"]
        assert result.graph[f"image-{n}"]["inputs"]["image"] == {"$inactiveReference": f"Picture {n}"}
    assert profile.workflow == before
    dumped = result.model_dump_json(by_alias=True)
    assert "base64" not in dumped and str(store.root) not in dumped
    for requirement in result.asset_requirements:
        assert len(requirement.content_sha256) == 64 and len(requirement.id) == 64
        assert result.graph[requirement.node_id]["inputs"][requirement.input_name] == {"$assetRequirement": requirement.id}
        assert requirement.filename == f"reference-{requirement.semantic_label[-1]}.png"


def test_missing_ordinal_never_substitutes_or_compacts(store, profile, workspace_factory, image_asset_factory):
    p = store.create_project(ref_workspace(workspace_factory, image_asset_factory, 1))
    select(store, p, valid_ref_prompt())
    profile.bindings[3].source.ordinal = 4
    result = prepared(store, p, profile)
    assert "Picture 4" in result.inactive_slots and len(result.asset_requirements) == 1
    assert result.asset_requirements[0].semantic_label == "Picture 1"


@pytest.mark.parametrize("failure", ["required", "unconfigured", "undeclared_consumer", "shared_loader"])
def test_absent_optional_safety(store, profile, workspace_factory, failure):
    p = store.create_project(workspace_factory()); select(store, p)
    before = deepcopy(profile.workflow)
    if failure == "required": profile.bindings[2].presence = "required"
    if failure == "unconfigured": profile.bindings[2].controlled_consumers = []
    if failure == "undeclared_consumer":
        profile.workflow["sizing"] = {"class_type": "Sizing", "inputs": {"image": ["image-1", 0]}}
        before = deepcopy(profile.workflow)
    if failure == "shared_loader":
        profile.workflow["image-1"]["inputs"]["another"] = "test.png"
        profile.bindings.append(ex.Binding.model_validate(mapping("reference_asset", "image-1", "another", assetKind="image", ordinal=2)))
        before = deepcopy(profile.workflow)
    with pytest.raises(ex.InvalidBindings, match="required|safely disabled|shared"):
        prepared(store, p, profile)
    assert profile.workflow == before


def test_multi_consumer_explicit_contract_and_required_present(store, profile, workspace_factory, image_asset_factory):
    profile.workflow["sizing"] = {"class_type": "Sizing", "inputs": {
        "image": ["image-1", 1], "scale": 2, "context": ["context", 0]}}
    p = store.create_project(ref_workspace(workspace_factory, image_asset_factory))
    select(store, p, valid_ref_prompt())
    profile.bindings[2].presence = "required"
    assert prepared(store, p, profile).graph["sizing"]["inputs"]["image"] == ["image-1", 1]
    profile.bindings[2].presence = "optional"
    profile.bindings[2].controlled_consumers.append(ex.ControlledConsumer(nodeId="sizing", inputName="image", outputIndex=1))
    blank = store.create_project(workspace_factory()); select(store, blank)
    result = prepared(store, blank, profile)
    assert result.graph["sizing"]["inputs"] == {"scale": 2, "context": ["context", 0]}
    assert "image-1" not in result.graph["h3"]["inputs"]
    assert profile.workflow["sizing"]["inputs"]["image"] == ["image-1", 1]


@pytest.mark.parametrize("kind", ["audio", "video"])
def test_av_legacy_metadata_persistence_and_missing_payload(store, tmp_path, workspace_factory, image_asset_factory, kind):
    w = ref_workspace(workspace_factory, image_asset_factory)
    w.assets.append(ReferenceAsset.model_validate({"id": kind, "kind": kind, "filename": f"test.{kind}",
        "mimeType": f"{kind}/synthetic", "role": f"general_{kind}", "durationSeconds": 4}))
    p = store.create_project(w)
    reloaded = store.load_project(p.project_id)
    assert reloaded.workspace.assets[-1] == w.assets[-1]
    # Active Workspace envelope preserves metadata only; no A/V bytes exist.
    from miniconstruct.models.api import ProjectEnvelope
    envelope = ProjectEnvelope.model_validate({"format": "MiniConstruct Project", "schemaVersion": 1,
        "workspace": reloaded.workspace.model_dump(mode="json", by_alias=True)})
    assert envelope.workspace.assets[-1].image is None
    profile = ex.ExecutionStore(tmp_path / "ex").save("profiles", capacity_profile(av=True))
    select(store, p, valid_ref_prompt())
    with pytest.raises(ex.InvalidBindings, match="metadata only"):
        prepared(store, p, profile)


def test_missing_picture_payload_fails(store, workspace_factory, image_asset_factory, profile):
    p = store.create_project(ref_workspace(workspace_factory, image_asset_factory)); select(store, p, valid_ref_prompt())
    payload = next((store.root / f"project-{p.project_id}" / "assets" / "pictures").iterdir())
    payload.unlink()
    with pytest.raises(ex.InvalidBindings, match="missing"): prepared(store, p, profile)


def _video_asset(store):
    async def chunks():
        yield b"synthetic video bytes"
    payload = asyncio.run(store.ingest_media(chunks(), "video", "video/mp4"))
    return ReferenceAsset.model_validate({"id": "clip", "kind": "video", "filename": "clip.mp4",
        "mimeType": "video/mp4", "role": "general_video", "durationSeconds": 1,
        "mediaPayload": payload.model_dump(by_alias=True)})


VIDEO_PROMPT = """subject_definitions:
No supplied subjects.

summary:
[reference generation] A cyclist crosses a station.

retention_analysis:
The scene is fully_preserved.

detailed_description:
The target uses live action.
[Shot 1] A cyclist crosses a station.

overall_soundscape:
Station ambience.

non_diegetic_music:
N/A"""


def _multi_hop_video_profile(tmp_path, *, paired_audio=True, unrelated=False):
    graph = {
        "h3": {"class_type": "SyntheticH3", "inputs": {"prompt": "old"}},
        "170": {"class_type": "LoadVideo", "inputs": {"file": "old.mp4"}},
        "171": {"class_type": "GetVideoComponents", "inputs": {"video": ["170", 0]}},
        "136": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {
            "ref_videos.ref_video_0": ["171", 0],
            **({"ref_video_audios.ref_video_audio_0": ["171", 1]} if paired_audio else {}),
            "context": ["prompt-source", 0]}},
        "prompt-source": {"class_type": "PromptSource", "inputs": {}},
    }
    targets = [{"channel": "video_frames", "nodeId": "136", "inputName": "ref_videos.ref_video_0",
                "expectedSourceNodeId": "171", "expectedOutputIndex": 0}]
    if paired_audio:
        targets.append({"channel": "embedded_audio", "nodeId": "136", "inputName": "ref_video_audios.ref_video_audio_0",
                        "expectedSourceNodeId": "171", "expectedOutputIndex": 1})
    if unrelated:
        graph["other"] = {"class_type": "UnrelatedOutput", "inputs": {"media": ["171", 1]}}
    data = ex.ProfileCreate(name="Multi-hop", compatibleModes=list(ex.H3Mode), workflowJson=json.dumps(graph),
        bindings=[mapping("generated_prompt", "h3", "prompt"),
                  {**mapping("reference_asset", "170", "file", assetKind="video", ordinal=1),
                   "routes": targets}])
    return ex.ExecutionStore(tmp_path / "execution").save("profiles", data)


def test_multihop_video_absence_cuts_only_declared_terminals(store, tmp_path, workspace_factory):
    profile = _multi_hop_video_profile(tmp_path)
    project = store.create_project(workspace_factory(mode="T2VA")); select(store, project)
    result = prepared(store, project, profile)
    assert result.graph["170"]["inputs"]["file"] == {"$inactiveReference": "Video 1"}
    assert result.graph["171"]["inputs"]["video"] == ["170", 0]
    assert "ref_videos.ref_video_0" not in result.graph["136"]["inputs"]
    assert "ref_video_audios.ref_video_audio_0" not in result.graph["136"]["inputs"]
    assert result.inactive_slots == ["Video 1"] and result.asset_requirements == []


def test_multihop_video_present_uses_one_requirement_for_paired_audio(store, tmp_path, workspace_factory):
    profile = _multi_hop_video_profile(tmp_path)
    project = store.create_project(workspace_factory(mode="Ref2VA", assets=[_video_asset(store)])); select(store, project, VIDEO_PROMPT)
    from miniconstruct.execution_routing import GenerationRouting, AssetRouting
    store.set_execution_routing(project.project_id, project.active_generation_id,
                                GenerationRouting(assets={"clip": AssetRouting(useEmbeddedAudio=True)}))
    result = prepared(store, project, profile)
    assert len(result.asset_requirements) == 1 and result.asset_requirements[0].asset_kind == "video"
    assert result.graph["170"]["inputs"]["file"]["$assetRequirement"] == result.asset_requirements[0].id
    assert result.graph["171"]["inputs"]["video"] == ["170", 0]
    assert result.graph["136"]["inputs"]["ref_videos.ref_video_0"] == ["171", 0]
    assert result.graph["136"]["inputs"]["ref_video_audios.ref_video_audio_0"] == ["171", 1]


def test_multihop_shared_undeclared_path_fails_without_pruning(store, tmp_path, workspace_factory):
    profile = _multi_hop_video_profile(tmp_path, unrelated=True)
    project = store.create_project(workspace_factory(mode="T2VA")); select(store, project)
    with pytest.raises(ex.InvalidBindings, match="still reaches executable workflow output"):
        prepared(store, project, profile)
    assert profile.workflow["other"]["inputs"]["media"] == ["171", 1]


def test_multihop_intermediate_only_cut_is_rejected(store, tmp_path, workspace_factory):
    profile = _multi_hop_video_profile(tmp_path)
    profile.bindings[1].routes = [ex.ReferenceRoute(channel="video_frames",
        nodeId="171", inputName="video", expectedSourceNodeId="170", expectedOutputIndex=0)]
    project = store.create_project(workspace_factory(mode="T2VA")); select(store, project)
    with pytest.raises(ex.InvalidBindings, match="outside the safe reference-branch boundary"):
        prepared(store, project, profile)


def test_multihop_post_boundary_cut_is_rejected(store, tmp_path, workspace_factory):
    profile = _multi_hop_video_profile(tmp_path, paired_audio=False)
    profile.workflow["outside"] = {"class_type": "PromptSource", "inputs": {}}
    profile.workflow["136"]["inputs"]["prompt"] = ["outside", 0]
    profile.workflow["downstream"] = {"class_type": "SaveVideo", "inputs": {"video": ["136", 0]}}
    profile.bindings[1].routes = [ex.ReferenceRoute(channel="video_frames",
        nodeId="downstream", inputName="video", expectedSourceNodeId="136", expectedOutputIndex=0)]
    project = store.create_project(workspace_factory(mode="T2VA")); select(store, project)
    with pytest.raises(ex.InvalidBindings, match="outside the safe reference-branch boundary"):
        prepared(store, project, profile)


def test_ambiguous_cycle_uses_conservative_boundary_failure(store, tmp_path, workspace_factory):
    profile = _multi_hop_video_profile(tmp_path, paired_audio=False)
    profile.workflow["136"]["inputs"].pop("context")
    profile.workflow["136"]["inputs"]["loop"] = ["172", 0]
    profile.workflow["172"] = {"class_type": "LoopAdapter", "inputs": {"back": ["136", 0]}}
    project = store.create_project(workspace_factory(mode="T2VA")); select(store, project)
    with pytest.raises(ex.InvalidBindings, match="safe optional-reference boundary"):
        prepared(store, project, profile)


def test_sink_without_merge_is_not_a_safe_boundary(store, tmp_path, workspace_factory):
    profile = _multi_hop_video_profile(tmp_path, paired_audio=False)
    profile.workflow["136"]["inputs"].pop("context")
    profile.workflow["downstream"] = {"class_type": "SaveVideo", "inputs": {"video": ["136", 0]}}
    profile.bindings[1].routes = [ex.ReferenceRoute(channel="video_frames",
        nodeId="downstream", inputName="video", expectedSourceNodeId="136", expectedOutputIndex=0)]
    project = store.create_project(workspace_factory(mode="T2VA")); select(store, project)
    with pytest.raises(ex.InvalidBindings, match="safe optional-reference boundary"):
        prepared(store, project, profile)


def test_unbound_constants_and_known_loader_warnings(store, profile, workspace_factory):
    for node, cls, name in [("unused-image", "LoadImage", "image"), ("unused-audio", "LoadAudio", "audio"),
                            ("unused-video", "LoadVideo", "file"), ("custom", "MyLoadImage", "image")]:
        profile.workflow[node] = {"class_type": cls, "inputs": {name: "<img onerror=evil>.test", "other": 42}}
    p = store.create_project(workspace_factory()); select(store, p)
    result = prepared(store, p, profile)
    assert result.graph["h3"]["inputs"]["opaque"] == {"do": ["not", "interpret"]}
    assert result.graph["custom"] == profile.workflow["custom"]
    warnings = "\n".join(result.warnings)
    for cls in ("LoadImage", "LoadAudio", "LoadVideo"): assert f"Unbound {cls}" in warnings
    assert "MyLoadImage" not in warnings and "<img onerror=evil>.test" in warnings


def test_safe_inspection_redacts_payload_paths_secrets(store, profile, workspace_factory):
    profile.workflow["h3"]["inputs"].update({"file": "C:\\private\\model", "api_key": "secret-value", "blob": "data:image/png;base64,AA=="})
    p = store.create_project(workspace_factory()); select(store, p)
    result = prepared(store, p, profile)
    text = result.model_dump_json()
    assert "secret-value" not in text and "base64" not in text and "C:" not in text
    assert profile.workflow["h3"]["inputs"]["api_key"] == "secret-value"


def test_legacy_v1_migration_no_consumer_invention(tmp_path, store, workspace_factory):
    execution = ex.ExecutionStore(tmp_path / "ex")
    profile = execution.save("profiles", capacity_profile())
    raw = json.loads(execution.path.read_text()); raw["schemaVersion"] = 1
    for binding in raw["profiles"][0]["bindings"]:
        binding.pop("presence", None); binding.pop("controlledConsumers", None)
    execution.path.write_text(json.dumps(raw))
    restored = ex.ExecutionStore(tmp_path / "ex").get("profiles", profile.id)
    assert restored.bindings[0].source.kind == "generated_prompt"
    assert restored.bindings[2].presence == "optional" and not restored.bindings[2].controlled_consumers
    assert ex.profile_readiness_errors(restored.workflow, restored.bindings)
    p = store.create_project(workspace_factory()); select(store, p)
    with pytest.raises(ex.InvalidBindings, match="safely disabled"): prepared(store, p, restored)
    execution.save("connections", ex.ConnectionWrite(name="unused", baseUrl="http://not-contacted.test"))
    assert json.loads(execution.path.read_text())["schemaVersion"] == 7


def test_v2_direct_consumer_migrates_to_exact_v3_target(tmp_path):
    execution = ex.ExecutionStore(tmp_path / "ex")
    profile = execution.save("profiles", capacity_profile(1))
    raw = json.loads(execution.path.read_text()); raw["schemaVersion"] = 2
    raw["profiles"][0]["bindings"][2].pop("deactivationTargets", None)
    raw["profiles"][0]["bindings"][2]["controlledConsumers"] = [
        {"nodeId": "h3", "inputName": "image-1", "outputIndex": 0}]
    execution.path.write_text(json.dumps(raw))
    restored = execution.get("profiles", profile.id)
    assert restored.bindings[2].deactivation_targets[0].model_dump(by_alias=True) == {
        "nodeId": "h3", "inputName": "image-1", "expectedSourceNodeId": "image-1", "expectedOutputIndex": 0}
    execution.save("connections", ex.ConnectionWrite(name="migrate", baseUrl="http://migration.test"))
    persisted = json.loads(execution.path.read_text())
    assert persisted["schemaVersion"] == 7
    migrated = persisted["profiles"][0]["bindings"][2]
    assert "controlledConsumers" not in migrated and migrated["deactivationTargets"][0]["expectedSourceNodeId"] == "image-1"


@pytest.mark.parametrize("change", ["wrong_link", "duplicate_control", "non_reference"])
def test_typed_contract_validation(tmp_path, change):
    data = capacity_profile()
    if change == "wrong_link": data.bindings[2].controlled_consumers[0].output_index = 2
    if change == "duplicate_control": data.bindings[2].controlled_consumers *= 2
    if change == "non_reference":
        with pytest.raises(ValidationError):
            ex.Binding.model_validate({**mapping("duration", "h3", "duration"), "presence": "required"})
        return
    with pytest.raises(ex.InvalidBindings): ex.ExecutionStore(tmp_path).save("profiles", data)


@pytest.mark.parametrize("mode", ["I2VA", "FL2VA", "L2VA", "Ref2VA"])
def test_authoring_drafts_can_select_explicit_prompt(store, workspace_factory, mode):
    w = workspace_factory().model_dump(mode="json", by_alias=True); w["mode"] = mode
    p = store.create_project(validate_authoring_workspace(w))
    assert select(store, p).selected.prompt == VALID_BASE


def test_api_saved_authority_restart_and_workspace_export_boundary(tmp_path, monkeypatch, workspace_factory):
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("MINICONSTRUCT_EXECUTION_ROOT", str(tmp_path / "execution"))
    p = ProjectStore().create_project(workspace_factory())
    profile = ex.ExecutionStore().save("profiles", capacity_profile(0))
    path = f"/api/execution/projects/{p.project_id}/generations/{p.active_generation_id}/selection"
    with TestClient(app) as client:
        assert client.get(path).json()["selected"] is None
        assert client.put(path, json={"prompt": VALID_BASE}).status_code == 422
        assert client.put(path, json={"prompt": VALID_BASE, "sourceFingerprint": fingerprint(p.workspace)}).status_code == 200
    # Fresh client, no generation outputs, no History and no connection.
    with TestClient(app) as client:
        assert client.get(path).json()["selected"]["prompt"] == VALID_BASE
        body = {"projectId": p.project_id, "generationId": p.active_generation_id, "profileId": str(profile.id)}
        result = client.post("/api/execution/prepare", json=body)
        assert result.status_code == 200, result.text
        assert result.json()["sentToComfyUI"] is False
        assert client.post("/api/execution/prepare", json={**body, "workspace": {}}).status_code == 422
        assert client.post("/api/execution/prepare", json={**body, "prompt": VALID_BASE}).status_code == 422
        assert client.get(path.replace(p.active_generation_id, str(uuid4()))).status_code == 404
        assert client.get(path.replace(p.project_id, str(uuid4()))).status_code == 404
        assert client.delete(path).json()["selected"] is None
        assert client.post("/api/execution/prepare", json=body).status_code == 422


@pytest.mark.parametrize("pictures,kinds,works", [(2, [], True), (1, ["video"], False), (3, ["video", "audio"], False)])
def test_same_full_capacity_profile_legacy_av_missing_payload(store, tmp_path, workspace_factory, image_asset_factory, pictures, kinds, works):
    profile = ex.ExecutionStore(tmp_path / "execution").save("profiles", capacity_profile(av=True))
    w = ref_workspace(workspace_factory, image_asset_factory, pictures)
    for kind in kinds:
        w.assets.append(ReferenceAsset.model_validate({"id": kind, "kind": kind, "filename": f"test.{kind}",
            "mimeType": f"{kind}/synthetic", "role": f"general_{kind}", "durationSeconds": 4}))
    p = store.create_project(w); select(store, p, valid_ref_prompt())
    if works:
        result = prepared(store, p, profile)
        assert result.inactive_slots == ["Picture 3", "Audio 1", "Video 1"]
        assert len(result.asset_requirements) == 2
    else:
        with pytest.raises(ex.InvalidBindings, match="file payload unavailable"): prepared(store, p, profile)


def test_aspect_ratio_binding_prepares_each_generation_from_h3_setup_without_profile_mutation(store, tmp_path, workspace_factory):
    graph = {"h3": {"class_type": "SyntheticH3", "inputs": {"prompt": "old", "aspectRatio": "4:3", "megapixels": 0.7}}}
    profile_draft = ex.ProfileCreate(name="Aspect", compatibleModes=list(ex.H3Mode), workflowJson=json.dumps(graph),
        bindings=[mapping("generated_prompt", "h3", "prompt"),
                  {"source": {"kind": "aspect_ratio", "valueMap": {"16:9": "16:9 (Widescreen)", "4:3": "4:3 (Standard)", "9:16": "9:16 (Portrait Widescreen)"}},
                   "nodeId": "h3", "inputName": "aspectRatio"}],
        exposedControls=[{"id": "megapixels", "label": "Megapixels",
            "target": {"nodeId": "h3", "inputName": "megapixels"}, "type": "float", "defaultValue": 0.7}])
    profile = ex.ExecutionStore(tmp_path / "execution").save("profiles", profile_draft)
    project = store.create_project(workspace_factory())
    gen2 = store.create_generation(project.project_id, workspace=workspace_factory(aspectRatio="16:9"))
    gen3 = store.create_generation(project.project_id, workspace=workspace_factory(aspectRatio="16:9"))
    original_profile_workflow = deepcopy(profile.workflow)
    first_selection = select(store, gen3)
    first = prepare_workflow(store, project.project_id, gen3.active_generation_id, profile)
    assert first.graph["h3"]["inputs"]["aspectRatio"] == "16:9 (Widescreen)"
    resolution = next(item for item in first.resolutions if item.input_name == "aspectRatio")
    assert (resolution.source_value, resolution.renderer_value) == ("16:9", "16:9 (Widescreen)")
    assert first.graph["h3"]["inputs"]["megapixels"] == 0.7
    assert first.execution_fingerprint
    assert first.workflow_controls == [{"id": "megapixels", "label": "Megapixels", "value": 0.7, "source": "profile_default"}]

    store.set_workflow_control_values(project.project_id, gen3.active_generation_id, str(profile.id),
        "generation", {"megapixels": 1.0})
    overridden = prepare_workflow(store, project.project_id, gen3.active_generation_id, profile)
    assert overridden.graph["h3"]["inputs"]["aspectRatio"] == "16:9 (Widescreen)"
    assert overridden.graph["h3"]["inputs"]["megapixels"] == 1.0
    assert overridden.workflow_controls[0]["value"] == 1.0
    assert overridden.execution_fingerprint != first.execution_fingerprint
    assert overridden.current_fingerprint == first.current_fingerprint

    config = ex.ExecutionStore(tmp_path / "execution")
    connection = config.save("connections", ex.ConnectionWrite(name="Synthetic", baseUrl="http://example.test"))
    sent = []
    def handle(request):
        body = json.loads(request.content); sent.append(body)
        return httpx.Response(200, json={"prompt_id": body["prompt_id"], "number": 1, "node_errors": {}})
    send_generation(ExecutionSendRequest(projectId=project.project_id, generationId=gen3.active_generation_id,
        profileId=profile.id, connectionId=connection.id), store=store, config=config,
        transport=httpx.MockTransport(handle))
    assert sent[0]["prompt"]["h3"]["inputs"]["aspectRatio"] == "16:9 (Widescreen)"
    assert sent[0]["prompt"]["h3"]["inputs"]["megapixels"] == 1.0

    gen4 = store.create_generation(project.project_id, workspace=workspace_factory(aspectRatio="9:16"))
    select(store, gen4)
    second = prepare_workflow(store, project.project_id, gen4.active_generation_id, profile)
    assert second.graph["h3"]["inputs"]["aspectRatio"] == "9:16 (Portrait Widescreen)"
    assert second.graph["h3"]["inputs"]["megapixels"] == 0.7
    assert second.execution_fingerprint != first.execution_fingerprint
    gen5 = store.create_generation(project.project_id, workspace=workspace_factory(aspectRatio="4:3"))
    select(store, gen5)
    third = prepare_workflow(store, project.project_id, gen5.active_generation_id, profile)
    assert third.graph["h3"]["inputs"]["aspectRatio"] == "4:3 (Standard)"
    assert third.execution_fingerprint != first.execution_fingerprint
    assert profile.workflow == original_profile_workflow
    assert first_selection.selected.prompt == VALID_BASE
    assert gen2.active_generation_id != gen3.active_generation_id


def test_aspect_ratio_no_mapping_is_identity_with_advisory_warning(store, tmp_path, workspace_factory):
    graph = {"h3": {"class_type": "SyntheticH3", "inputs": {"prompt": "old", "ratio": "4:3"}}}
    profile = ex.ExecutionStore(tmp_path / "execution").save("profiles", ex.ProfileCreate(
        name="Aspect", compatibleModes=list(ex.H3Mode), workflowJson=json.dumps(graph),
        bindings=[mapping("generated_prompt", "h3", "prompt"), mapping("aspect_ratio", "h3", "ratio")]))
    project = store.create_project(workspace_factory(aspectRatio="custom:11/7"))
    select(store, project)
    result = prepare_workflow(store, project.project_id, project.active_generation_id, profile)
    assert result.graph["h3"]["inputs"]["ratio"] == "custom:11/7"
    assert any(warning.startswith('No renderer Aspect Ratio mapping exists for "custom:11/7"; raw value will be sent.')
               for warning in result.warnings)
    assert any('raw-value support is unverified' in warning for warning in result.warnings)


@pytest.mark.parametrize("ratio", ["16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3", "21:9"])
def test_standard_ratio_missing_from_explicit_value_map_blocks_prepare(store, tmp_path, workspace_factory, ratio):
    draft = capacity_profile(0)
    graph = json.loads(draft.workflow_json)
    graph["ratio"] = {"class_type": "ResolutionSelector", "inputs": {"aspect_ratio": "mapped enum"}}
    binding = ex.Binding.model_validate(mapping("aspect_ratio", "ratio", "aspect_ratio", valueMap={"auto": "mapped enum"}))
    profile = ex.ExecutionStore(tmp_path / "execution").save("profiles", draft.model_copy(
        update={"workflow_json": json.dumps(graph), "bindings": [*draft.bindings, binding]}))
    project = store.create_project(workspace_factory(aspectRatio=ratio)); select(store, project)
    with pytest.raises(ex.InvalidBindings, match="Aspect Ratio mapping missing.*ratio / aspect_ratio"):
        prepared(store, project, profile)


def test_unbound_ratio_warning_names_missing_execution_authority(store, tmp_path, workspace_factory):
    profile = ex.ExecutionStore(tmp_path / "execution").save("profiles", capacity_profile(0))
    project = store.create_project(workspace_factory(aspectRatio="9:16")); select(store, project)
    result = prepared(store, project, profile)
    assert any("no Aspect Ratio authority" in warning and "Add a modern aspect_ratio" in warning for warning in result.warnings)


def test_ratio_only_change_rejects_stale_send_after_profile_reload(store, tmp_path, workspace_factory):
    config = ex.ExecutionStore(tmp_path / "execution")
    draft = capacity_profile(0)
    graph = json.loads(draft.workflow_json)
    graph["ratio"] = {"class_type": "ResolutionSelector", "inputs": {"aspect_ratio": "4:3 (Standard)"}}
    profile = config.save("profiles", draft.model_copy(update={"workflow_json": json.dumps(graph), "bindings": [
        *draft.bindings, ex.Binding.model_validate(mapping("aspect_ratio", "ratio", "aspect_ratio",
            valueMap={"4:3": "4:3 (Standard)", "9:16": "9:16 (Portrait Widescreen)"}))]}))
    connection = config.save("connections", ex.ConnectionWrite(name="Offline", baseUrl="http://never.test"))
    project = store.create_project(workspace_factory(aspectRatio="4:3", durationSeconds=8)); select(store, project)
    old = prepared(store, project, profile)
    store.sync_execution_setup(project.project_id, project.active_generation_id, 8, project.project_fingerprint, "9:16")
    assert store.read_generation(project.project_id, project.active_generation_id).workspace.duration_seconds == 8
    sent = []
    def queue(request):
        body = json.loads(request.content); sent.append(body)
        return httpx.Response(200, json={"prompt_id": body["prompt_id"], "number": 1, "node_errors": {}})
    reloaded = ex.ExecutionStore(tmp_path / "execution")
    with pytest.raises(ex.InvalidBindings, match="stale"):
        send_generation(ExecutionSendRequest(projectId=project.project_id, generationId=project.active_generation_id,
            profileId=profile.id, connectionId=connection.id, expectedExecutionFingerprint=old.execution_fingerprint),
            store=store, config=reloaded, transport=httpx.MockTransport(queue))
    assert not sent
    fresh = prepared(store, project, reloaded.get("profiles", profile.id))
    assert fresh.execution_fingerprint != old.execution_fingerprint
    send_generation(ExecutionSendRequest(projectId=project.project_id, generationId=project.active_generation_id,
        profileId=profile.id, connectionId=connection.id, expectedExecutionFingerprint=fresh.execution_fingerprint),
        store=store, config=reloaded, transport=httpx.MockTransport(queue))
    assert sent[0]["prompt"]["ratio"]["inputs"]["aspect_ratio"] == "9:16 (Portrait Widescreen)"


def test_legacy_aspect_ratio_workflow_control_projects_h3_setup_to_its_resolution_target(store, tmp_path, workspace_factory):
    draft = capacity_profile(0)
    graph = json.loads(draft.workflow_json)
    graph["115"] = {"class_type": "ResolutionSelector", "inputs": {"aspect_ratio": "4:3 (Standard)"}}
    control = ex.ExposedControl.model_validate({"id": "aspect_ratio", "label": "Aspect Ratio",
        "target": {"nodeId": "115", "inputName": "aspect_ratio"}, "type": "enum",
        "defaultValue": "4:3 (Standard)", "choices": ["4:3 (Standard)", "9:16 (Portrait Widescreen)"]})
    profile = ex.ExecutionStore(tmp_path / "execution").save("profiles", draft.model_copy(update={
        "workflow_json": json.dumps(graph), "exposed_controls": [control]}))
    project = store.create_project(workspace_factory(aspectRatio="9:16")); select(store, project)
    result = prepared(store, project, profile)
    assert result.graph["115"]["inputs"]["aspect_ratio"] == "9:16 (Portrait Widescreen)"
    assert any("115 / aspect_ratio is driven by H3 Setup" in info for info in result.information)
    assert not any("Replace this control" in warning for warning in result.warnings)

@pytest.mark.parametrize("failure", [None, "missing", "invalid_target"])
def test_i2va_first_picture_binding_with_coexisting_profiles(store, tmp_path, workspace_factory, image_asset_factory, failure):
    configuration = ex.ExecutionStore(tmp_path / "profiles")
    ref = configuration.save("profiles", capacity_profile().model_copy(update={"name": "Ref2VA", "compatible_modes": [ex.H3Mode.REF2VA]}))
    image = configuration.save("profiles", capacity_profile().model_copy(update={"name": "I2VA", "compatible_modes": [ex.H3Mode.I2VA]}))
    before = ref.model_dump(mode="json"), image.model_dump(mode="json")
    project = store.create_project(workspace_factory(mode="I2VA", assets=[image_asset_factory("first", role="first_frame_anchor")]))
    prompt = "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.\n\n" + VALID_BASE
    select(store, project, prompt)
    candidate = image.model_copy(deep=True)
    if failure == "missing":
        candidate.bindings = [b for b in candidate.bindings if not (b.source.kind == "reference_asset" and b.source.ordinal == 1)]
    elif failure == "invalid_target":
        candidate.bindings[2].input_name = "nonexistent"
    if failure:
        with pytest.raises(ex.InvalidBindings):
            prepared(store, project, candidate)
    else:
        result = prepared(store, project, candidate)
        assert result.asset_requirements[0].asset_id == "first"
        assert result.graph["h3"]["inputs"]["prompt"] == prompt
    assert (ref.model_dump(mode="json"), image.model_dump(mode="json")) == before


def test_structured_preparation_diagnostics_and_api_projections(tmp_path, monkeypatch, workspace_factory):
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("MINICONSTRUCT_EXECUTION_ROOT", str(tmp_path / "execution"))
    store, config = ProjectStore(), ex.ExecutionStore()
    draft = capacity_profile(0)
    graph = json.loads(draft.workflow_json)
    graph["h3"]["inputs"]["api_key"] = "private-test-value"
    graph["h3"]["inputs"]["ratio"] = "16:9"
    draft.workflow_json = json.dumps(graph)
    draft.bindings.append(ex.Binding.model_validate(mapping("aspect_ratio", "h3", "ratio",
        valueMap={"16:9": "16:9"}, enumChoices=["16:9"])))
    profile = config.save("profiles", draft)
    project = store.create_project(workspace_factory(aspectRatio="16:9"))
    select(store, project)
    baseline = prepared(store, project, profile)
    assert baseline.warnings == []
    codes = {item.code: item.severity for item in baseline.diagnostics}
    assert codes == {"seed.workflow_default": "info", "preparation.offline": "info",
                     "inspection.private_constants": "info"}
    assert all(item.message in baseline.information for item in baseline.diagnostics)
    with TestClient(app) as client:
        response = client.post("/api/execution/prepare", json={"projectId": project.project_id,
            "generationId": project.active_generation_id, "profileId": str(profile.id)})
    assert response.status_code == 200
    payload = response.json()
    assert payload["diagnostics"] == [d.model_dump() for d in baseline.diagnostics]
    assert payload["warnings"] == [] and payload["information"] == baseline.information
    assert "private-test-value" not in response.text
    # Presentation-only diagnostic changes cannot affect materialization or identity.
    from miniconstruct.preparation_diagnostics import PreparationDiagnostics
    original = PreparationDiagnostics.add
    monkeypatch.setattr(PreparationDiagnostics, "add",
        lambda self, code, message, severity="warning": original(self, code, message, "warning"))
    changed = prepared(store, project, profile)
    assert changed.graph == baseline.graph
    assert changed.execution_fingerprint == baseline.execution_fingerprint
    assert changed.profile_fingerprint == baseline.profile_fingerprint
    assert changed.warnings and not changed.information
    execution = prepare_workflow(store, project.project_id, project.active_generation_id, profile, inspection=False)
    assert execution.execution_fingerprint == baseline.execution_fingerprint
    assert execution.graph["h3"]["inputs"]["api_key"] == "private-test-value"
    assert profile.workflow["h3"]["inputs"]["api_key"] == "private-test-value"


def test_diagnostic_order_deduplication_and_no_error_downgrade():
    from miniconstruct.preparation_diagnostics import PreparationDiagnostics, PreparationDiagnostic
    report = PreparationDiagnostics()
    report.add("first", "First note", "info")
    report.extend("future.unknown", ["Unknown concern", "Unknown concern"])
    report.add("fatal", "First note", "error")
    report.add("lower", "First note", "info")
    assert [(d.code, d.severity) for d in report.items] == [("fatal", "error"), ("future.unknown", "warning")]
    assert report.messages("warning") == ["Unknown concern"]
    assert report.messages("error") == ["First note"]
    with pytest.raises(ValidationError):
        PreparationDiagnostic(code="unknown", message="Unknown concern", severity="unrecognized")


def test_unused_reference_history_changes_are_informational():
    from miniconstruct.workflow_preparation import reference_map_warnings
    from miniconstruct.preparation_diagnostics import PreparationDiagnostics
    from miniconstruct.execution_routing import EffectiveReference
    old = [EffectiveReference(label="Audio 1", assetId="old-audio", channel="audio")]
    current = [EffectiveReference(label="Audio 1", assetId="new-audio", channel="audio")]
    info = PreparationDiagnostics()
    assert reference_map_warnings("No media labels are used here.", old, current, diagnostics=info) == []
    assert info.items[0].code == "reference.unused_routing_changed" and info.items[0].severity == "info"
    warning = PreparationDiagnostics()
    assert reference_map_warnings("Use <Audio 1>.", old, current, diagnostics=warning)
    assert warning.items[0].code == "reference.source_changed" and warning.items[0].severity == "warning"
