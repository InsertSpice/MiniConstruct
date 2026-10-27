"""Production-shaped graph contracts, neutral prompts and mocked HTTP; no renders."""
from copy import deepcopy
import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from miniconstruct import execution as ex
from miniconstruct.execution_send import ExecutionSendRequest, send_generation
from miniconstruct.execution_state import SelectionWrite
from miniconstruct.models.workspace import validate_authoring_workspace
from miniconstruct.native_continuation import profile_identity, resolve_native
from miniconstruct.native_continuation_models import ContinuationDecision, ContinuationSettings
from miniconstruct.project_store import ProjectStore, ProjectStoreError
from miniconstruct.workflow_preparation import prepare_workflow
from test_execution_send import recorder
from test_validator import VALID_BASE


I2VA_PROMPT = ("For the target video, at 0.00 seconds into the target video, "
               "<Picture 1> (from [Shot 1]) is fully referenced.\n\n" + VALID_BASE)


def draft(*, independent_dimensions=False, presence="required", modes=None):
    graph = json.loads((Path(__file__).parent / "fixtures/i2va-scaled-picture-native-workflow.json").read_text())
    if independent_dimensions:
        graph["6"]["inputs"].update(width=1024, height=768)
        # This explicitly revised export omits the now-unused size node. Leaving
        # it as a loader-fed terminal remains conservatively unsafe offline.
        del graph["52"]
    return ex.ProfileCreate(name="Scaled Picture native", compatibleModes=modes or ["I2VA"],
        workflowJson=json.dumps(graph), bindings=[
            {"source": {"kind": "generated_prompt"}, "nodeId": "6", "inputName": "prompt"},
            {"source": {"kind": "duration"}, "nodeId": "35", "inputName": "value"},
            {"source": {"kind": "reference_asset", "assetKind": "image", "ordinal": 1},
             "nodeId": "50", "inputName": "image", "presence": presence, "routes": [
                 {"nodeId": "6", "inputName": "first_frame", "expectedSourceNodeId": "51",
                  "expectedOutputIndex": 0, "channel": "image"}]},
        ], nativeContinuation={"compatibilityKey": "example-i2va-native-v1",
            "inputs": {
                "enable": {"nodeId": "55", "inputName": "mode", "enabledValue": "auto", "disabledValue": "disabled"},
                "context_frames": {"nodeId": "55", "inputName": "context_length"},
                "save_key": {"nodeId": "56", "inputName": "filename_prefix"},
                "save_segment_index": {"nodeId": "56", "inputName": "clip_index"},
                "load_key": {"nodeId": "59", "inputName": "latent_path"},
                "load_segment_index": {"nodeId": "59", "inputName": "clip_index", "unsetValue": 0},
            },
            "sourceLinks": [{"nodeId": "55", "inputName": "prev_latent",
                             "expectedSourceNodeId": "59", "expectedOutputIndex": 0}],
            "requiredInputs": ["context_frames", "save_segment_index", "load_segment_index"],
            "contextFrames": 22, "contextGrid": {"minimum": 5, "step": 17, "maximum": 39},
            "requiresTailReference": False, "allowEndpointAnchor": False,
            "audioContinuity": False, "audioEnabled": False})


@pytest.fixture
def env(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("I2VA tests must never call live HTTP")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    monkeypatch.setenv("MINICONSTRUCT_EXECUTION_ROOT", str(tmp_path / "execution"))
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(tmp_path / "projects"))
    config, store = ex.ExecutionStore(), ProjectStore()
    connection = config.save("connections", ex.ConnectionWrite(name="Mock", baseUrl="http://example.invalid"))
    return store, config, connection


def choose(env, project, profile, prompt=VALID_BASE):
    store = env[0]
    store.set_continuation_settings(project.project_id, project.active_generation_id,
                                    ContinuationSettings(profileId=profile.id))
    status = store.execution_snapshot(project.project_id, project.active_generation_id)[1]
    store.select_execution_prompt(project.project_id, project.active_generation_id,
        SelectionWrite(prompt=prompt, sourceFingerprint=status.current_fingerprint))


def send(env, project, profile, calls, fingerprint=None, failure=None):
    store, config, connection = env
    return send_generation(ExecutionSendRequest(projectId=project.project_id, generationId=project.active_generation_id,
        profileId=profile.id, connectionId=connection.id, expectedExecutionFingerprint=fingerprint),
        store=store, config=config, transport=recorder(calls, failure))


def accept(env, project, receipt, *, accepted=True):
    return env[0].decide_continuation(project.project_id, project.active_generation_id,
        receipt.continuation_execution_id, ContinuationDecision(status="available", accept=accepted))


def successor(env, first, workspace, profile):
    store = env[0]
    project = store.create_generation(first.project_id, workspace=workspace)
    project = store.save_project(project.project_id, project.workspace,
        planning={"relationshipToPrevious": "continue_previous_shot"})
    choose(env, project, profile, I2VA_PROMPT if workspace.assets else VALID_BASE)
    return project


def test_initial_i2va_scaled_picture_save_only_and_mock_prompt_provenance(env, workspace_factory, image_asset_factory):
    store, config, connection = env
    profile = config.save("profiles", draft())
    original = deepcopy(profile.workflow)
    first = store.create_project(workspace_factory(mode="I2VA", assets=[image_asset_factory(role="first_frame_anchor")]))
    choose(env, first, profile, I2VA_PROMPT)
    prepared = prepare_workflow(store, first.project_id, first.active_generation_id, profile)
    graph = prepared.graph
    assert graph["6"]["inputs"]["first_frame"] == ["51", 0]
    assert graph["6"]["inputs"]["width"] == ["52", 0] and graph["6"]["inputs"]["height"] == ["52", 1]
    assert graph["52"]["inputs"]["image"] == ["51", 0]
    assert graph["51"]["inputs"]["image"] == ["50", 0] and graph["51"]["inputs"]["megapixels"] == 0.8
    assert graph["55"]["inputs"]["mode"] == "disabled" and "prev_latent" not in graph["55"]["inputs"]
    assert graph["59"]["inputs"]["latent_path"] == "" and graph["59"]["inputs"]["clip_index"] == 0
    assert graph["56"]["inputs"]["clip_index"] == 1
    assert len(prepared.asset_requirements) == 1 and not prepared.native_continuation.consume
    calls = []
    receipt = send(env, first, profile, calls, prepared.execution_fingerprint)
    assert [c.url.path for c in calls] == ["/upload/image", "/prompt"]
    wire = json.loads(calls[-1].content)
    submitted = wire["prompt"]
    record = store.continuation_status(first.project_id, first.active_generation_id)["executions"][0]
    expected = deepcopy(graph)
    expected["50"]["inputs"]["image"] = receipt.uploaded_inputs[0].filename
    expected["56"]["inputs"]["filename_prefix"] = record.save_key
    assert submitted == expected and submitted["6"]["inputs"]["prompt"] == I2VA_PROMPT
    assert record.project_id == receipt.project_id and record.generation_id == receipt.generation_id
    assert record.workflow_profile_id == profile.id and record.connection_id == connection.id
    assert record.profile_fingerprint == profile_identity(profile) and record.workflow_fingerprint == profile.fingerprint
    assert record.execution_fingerprint == prepared.execution_fingerprint
    assert record.render_submission_id == receipt.render_submission_id and str(receipt.prompt_id) == wire["prompt_id"]
    assert record.status == "pending" and not record.confirmed and record.source_id is None
    assert record.save_key.startswith(f"mc/{first.project_id}/{first.active_generation_id}/")
    assert profile.workflow == original and config.get("profiles", profile.id).workflow == original


def test_scaled_picture_optional_error_identifies_dimension_dependencies_without_pruning(env):
    _, config, _ = env
    profile = config.save("profiles", draft(presence="optional"))
    original = deepcopy(profile.workflow)
    errors = ex.profile_readiness_errors(profile.workflow, profile.bindings)
    message = " ".join(errors)
    assert "cannot be safely disabled" in message
    assert "6 / width" in message and "6 / height" in message
    assert "52 output 0" in message and "52 output 1" in message
    assert "dimensions" in message and "Required" in message
    assert profile.workflow == original


def test_unused_loader_fed_size_terminal_still_requires_explicit_graph_revision(env):
    _, config, _ = env
    data = draft(presence="optional")
    graph = json.loads(data.workflow_json)
    graph["6"]["inputs"].update(width=1024, height=768)
    # Replacing dimensions does not authorize pruning the leftover size sink.
    data.workflow_json = json.dumps(graph)
    profile = config.save("profiles", data)
    errors = ex.profile_readiness_errors(profile.workflow, profile.bindings)
    assert "node 52 (GetImageSize)" in " ".join(errors)
    assert profile.workflow == graph


@pytest.mark.parametrize("presence", ["required", "optional"])
def test_required_and_optional_picture_routes_still_require_exact_source_edges(env, presence):
    _, config, _ = env
    data = draft(presence=presence)
    data.bindings[2].routes[0].expected_output_index = 1
    with pytest.raises(ex.InvalidBindings, match="expected source node and output index"):
        config.save("profiles", data)


def test_required_picture_route_cannot_claim_an_unrelated_source(env, workspace_factory, image_asset_factory):
    store, config, _ = env
    data = draft()
    graph = json.loads(data.workflow_json)
    graph["unrelated"] = {"class_type": "LoadImage", "inputs": {"image": "unrelated.png"}}
    graph["6"]["inputs"]["first_frame"] = ["unrelated", 0]
    data.bindings[2].routes[0].expected_source_node_id = "unrelated"
    data.workflow_json = json.dumps(graph)
    # The declared link exists, but it is not from the semantically bound loader.
    profile = config.save("profiles", data)
    first = store.create_project(workspace_factory(mode="I2VA", assets=[image_asset_factory(role="first_frame_anchor")]))
    choose(env, first, profile, I2VA_PROMPT)
    with pytest.raises(ex.InvalidBindings, match="outside the safe reference-branch boundary"):
        prepare_workflow(store, first.project_id, first.active_generation_id, profile)


@pytest.mark.parametrize("with_predecessor", [False, True])
def test_optional_slot_does_not_bypass_ordinary_i2va_first_frame(env, workspace_factory, image_asset_factory, with_predecessor):
    store, config, _ = env
    profile = config.save("profiles", draft(independent_dimensions=True, presence="optional"))
    data = workspace_factory().model_dump(by_alias=True)
    data["mode"] = "I2VA"
    empty_draft = validate_authoring_workspace(data)
    with pytest.raises(ValidationError, match="exactly one first-frame"):
        workspace_factory(mode="I2VA")
    if with_predecessor:
        first = store.create_project(workspace_factory(mode="I2VA", assets=[image_asset_factory(role="first_frame_anchor")]))
        choose(env, first, profile, I2VA_PROMPT)
        accept(env, first, send(env, first, profile, []))
        project = successor(env, first, empty_draft, profile)
        assert resolve_native(store, project.project_id, project.active_generation_id, profile)["consume"]
    else:
        project = store.create_project(empty_draft)
        choose(env, project, profile)
    calls = []
    with pytest.raises(ex.InvalidBindings, match="I2VA requires exactly one first-frame Picture"):
        prepare_workflow(store, project.project_id, project.active_generation_id, profile)
    with pytest.raises(ex.InvalidBindings, match="I2VA requires exactly one first-frame Picture"):
        send(env, project, profile, calls)
    assert calls == []


def test_native_only_same_mode_optional_graph_preserves_exact_accepted_source(env, workspace_factory):
    store, config, _ = env
    # Same renderer node, independently supplied dimensions, honest T2VA authoring.
    profile = config.save("profiles", draft(independent_dimensions=True, presence="optional", modes=["T2VA"]))
    assert not ex.profile_readiness_errors(profile.workflow, profile.bindings)
    first = store.create_project(workspace_factory())
    choose(env, first, profile)
    accepted = send(env, first, profile, [])
    accept(env, first, accepted)
    newer_unaccepted = send(env, first, profile, [])
    second = successor(env, first, workspace_factory(), profile)
    prepared = prepare_workflow(store, second.project_id, second.active_generation_id, profile)
    native = prepared.native_continuation
    assert native.consume and native.source_id == accepted.continuation_execution_id != newer_unaccepted.continuation_execution_id
    assert str(native.source_generation_id) == first.active_generation_id
    assert native.save_segment_index == 2 and native.load_segment_index == 1 and native.context_frames == 22
    assert not native.audio_supported and not native.audio_enabled
    assert not prepared.asset_requirements and prepared.inactive_slots == ["Picture 1"]
    assert set(prepared.graph) == set(profile.workflow)  # No name-based pruning, including the orphan scaler.
    assert "first_frame" not in prepared.graph["6"]["inputs"]
    assert prepared.graph["6"]["inputs"]["width"] == 1024 and prepared.graph["6"]["inputs"]["height"] == 768
    assert prepared.graph["55"]["inputs"]["prev_latent"] == ["59", 0]
    source = store.continuation_status(first.project_id, first.active_generation_id)["executions"][0]
    assert native.load_key == source.storage_namespace
    calls = []
    result = send(env, second, profile, calls, prepared.execution_fingerprint)
    assert [c.url.path for c in calls] == ["/prompt"]
    submitted = json.loads(calls[0].content)["prompt"]
    record = store.continuation_status(second.project_id, second.active_generation_id)["executions"][0]
    expected = deepcopy(prepared.graph)
    expected["56"]["inputs"]["filename_prefix"] = record.save_key
    assert submitted == expected and submitted["59"]["inputs"] == prepared.graph["59"]["inputs"]
    assert record.source_id == source.id and record.render_submission_id == result.render_submission_id
    assert record.storage_namespace != source.storage_namespace and record.execution_fingerprint == prepared.execution_fingerprint
    # Revocation after Prepare must stop Send without touching the renderer.
    store.decide_continuation(first.project_id, first.active_generation_id, source.id,
                             ContinuationDecision(status="unavailable"))
    calls.clear()
    with pytest.raises(ex.InvalidBindings, match="stale"):
        send(env, second, profile, calls, prepared.execution_fingerprint)
    assert calls == []


@pytest.mark.parametrize("state", ["missing", "unaccepted", "cross_mode"])
def test_native_only_missing_unaccepted_and_cross_mode_sources_fail_before_transport(env, workspace_factory, image_asset_factory, state):
    store, config, _ = env
    profile = config.save("profiles", draft(independent_dimensions=True, presence="optional", modes=["I2VA", "T2VA"]))
    first = store.create_project(workspace_factory(mode="I2VA", assets=[image_asset_factory(role="first_frame_anchor")])
                                 if state == "cross_mode" else workspace_factory())
    choose(env, first, profile, I2VA_PROMPT if state == "cross_mode" else VALID_BASE)
    if state != "missing":
        accept(env, first, send(env, first, profile, []), accepted=state != "unaccepted")
    second = successor(env, first, workspace_factory(), profile)
    store.save_project(second.project_id, second.workspace,
        planning={"relationshipToPrevious": "continue_previous_shot", "visualHandoff": {"strategy": "native_h3_continuation"}})
    calls = []
    with pytest.raises(ProjectStoreError, match="Native H3 continuation unavailable"):
        send(env, second, profile, calls)
    assert calls == []


def test_i2va_new_first_frame_conflicts_with_native_state(env, workspace_factory, image_asset_factory):
    store, config, _ = env
    profile = config.save("profiles", draft())
    first = store.create_project(workspace_factory(mode="I2VA", assets=[image_asset_factory(role="first_frame_anchor")]))
    choose(env, first, profile, I2VA_PROMPT)
    accept(env, first, send(env, first, profile, []))
    second = successor(env, first, workspace_factory(mode="I2VA", assets=[image_asset_factory("new", role="first_frame_anchor")]), profile)
    resolution = resolve_native(store, second.project_id, second.active_generation_id, profile)
    assert not resolution["consume"] and "first-frame anchor" in resolution["reason"]
    assert not profile.native_continuation.allow_endpoint_anchor
    store.save_project(second.project_id, second.workspace,
        planning={"relationshipToPrevious": "continue_previous_shot", "visualHandoff": {"strategy": "native_h3_continuation"}})
    calls = []
    with pytest.raises(ProjectStoreError, match="first-frame anchor"):
        send(env, second, profile, calls)
    assert calls == []


def test_wrong_ref2va_native_mapping_and_stale_source_link_are_rejected(env, workspace_factory):
    store, config, _ = env
    wrong = draft()
    wrong.native_continuation.inputs["enable"].node_id = "164"
    with pytest.raises(ex.InvalidBindings, match="Native enable"):
        config.save("profiles", wrong)
    profile = config.save("profiles", draft(independent_dimensions=True, presence="optional", modes=["T2VA"]))
    project = store.create_project(workspace_factory())
    choose(env, project, profile)
    profile.workflow["55"]["inputs"]["prev_latent"] = ["59", 1]
    with pytest.raises(ex.InvalidBindings, match="expected source node and output index"):
        prepare_workflow(store, project.project_id, project.active_generation_id, profile)


def test_duplicate_i2va_profile_edits_are_independent_and_do_not_select_it(env, workspace_factory, image_asset_factory):
    store, config, _ = env
    original = config.save("profiles", draft())
    first = store.create_project(workspace_factory(mode="I2VA", assets=[image_asset_factory(role="first_frame_anchor")]))
    choose(env, first, original, I2VA_PROMPT)
    disk = config.path.read_bytes()
    copy = config.duplicate_profile_draft(original.id)
    assert config.path.read_bytes() == disk
    graph = json.loads(copy.workflow_json)
    graph["6"]["inputs"].update(width=1024, height=768)
    copy.workflow_json = json.dumps(graph)
    copy.bindings[2].presence = "optional"
    copy.native_continuation.context_frames = 5
    duplicate = config.save("profiles", copy)
    assert duplicate.id != original.id and duplicate.fingerprint != original.fingerprint
    assert config.get("profiles", original.id) == original
    assert original.native_continuation.context_frames == 22 and original.bindings[2].presence == "required"
    assert duplicate.native_continuation.context_frames == 5
    assert store.continuation_status(first.project_id, first.active_generation_id)["settings"].profile_id == original.id
