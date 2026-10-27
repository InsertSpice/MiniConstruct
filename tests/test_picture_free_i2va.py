"""Picture-free successors through production authoring/Prepare/Send, mocked transport."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from miniconstruct import execution as ex
from miniconstruct.generation import resolve_narrative_request, revalidate_native_authoring
from miniconstruct.generation import generate_workspace_prompts
from miniconstruct.h3.builder import assemble_prompt, _mode_guidance, numbered_assets
from miniconstruct.h3.validator import validate_workspace_prompt
from miniconstruct.i2va_contract import state_identity
from miniconstruct.models.api import GenerationRequest
from miniconstruct.models.takes import RendererJobState, TakeImport
from miniconstruct.models.workspace import Workspace, validate_authoring_workspace
from miniconstruct.native_continuation import resolve_native
from miniconstruct.native_continuation_models import ContinuationSettings, ContinuationDecision
from miniconstruct.project_store import ProjectStore, ProjectStoreError
from miniconstruct.workflow_preparation import prepare_workflow
from test_i2va_native_continuation import env, draft, choose, send, accept, I2VA_PROMPT
from test_validator import VALID_BASE
from test_visual_handoff import video


def successor_draft():
    data = draft().model_dump(mode="json", by_alias=True)
    data.update(name="Native-only I2VA successor", workflowJson=(Path(__file__).parent /
        "fixtures/i2va-native-only-workflow.json").read_text())
    data["bindings"] = data["bindings"][:2]
    data["nativeContinuation"]["inputs"]["enable"]["enabledValue"] = "always_extend"
    data["nativeContinuation"]["pictureFreeI2va"] = {
        "version": 1, "rendererContract": "minimax_h3_extender_av_v1", "conditioningNodeId": "6"}
    return ex.ProfileCreate(**data)


def initial(env, workspace_factory, image_asset_factory, *, completed=True, accepted=True):
    store, config, _ = env
    profile = config.save("profiles", draft())
    first = store.create_project(workspace_factory(mode="I2VA", assets=[image_asset_factory(role="first_frame_anchor")]))
    choose(env, first, profile, I2VA_PROMPT)
    receipt = send(env, first, profile, [])
    if completed:
        store.update_render_job(first.project_id, first.active_generation_id, receipt.render_submission_id,
                                RendererJobState(status="completed"))
    if accepted:
        accept(env, first, receipt)
    return first, profile, receipt


def successor(env, first, workspace_factory, profile, *, strategy="native_i2va_continuation", assets=None):
    store = env[0]
    raw = workspace_factory().model_dump(mode="json", by_alias=True)
    raw.update(mode="I2VA", assets=assets or [])
    project = store.create_generation(first.project_id, workspace=validate_authoring_workspace(raw))
    project = store.save_project(project.project_id, project.workspace, planning={
        "relationshipToPrevious": "continue_previous_shot", "visualHandoff": {"strategy": strategy},
        "scenePlan": "The cyclist continues across the crossing.", "cameraIntent": "Track the ongoing movement."})
    store.set_continuation_settings(project.project_id, project.active_generation_id, ContinuationSettings(profileId=profile.id))
    if resolve_native(store, project.project_id, project.active_generation_id, profile)["consume"]:
        choose(env, project, profile)
    return project


def request_for(project):
    return GenerationRequest.model_validate({"filesystemProjectId": project.project_id,
        "generationId": project.active_generation_id, "workspace": project.workspace.model_dump(mode="json", by_alias=True),
        "llm": {"baseUrl": "http://example.invalid/v1", "model": "offline"}})


def test_g1_g2_g3_exact_native_lineage_prompt_graph_and_reload(env, workspace_factory, image_asset_factory):
    store, config, connection = env
    first, image_profile, r1 = initial(env, workspace_factory, image_asset_factory)
    only = config.save("profiles", successor_draft())
    second = successor(env, first, workspace_factory, only)
    request = resolve_narrative_request(request_for(second), store)
    workspace = request.prompt_workspace
    assert workspace._native_i2va and not second.workspace._native_i2va
    assert workspace._native_i2va_context == VALID_BASE
    assert not workspace.assets and numbered_assets(workspace) == []
    assert "there is no new first-frame Picture" in _mode_guidance(workspace)
    assert "<Picture 1>" not in _mode_guidance(workspace)
    assert validate_workspace_prompt(VALID_BASE, workspace).valid
    assert not validate_workspace_prompt(I2VA_PROMPT, workspace).valid
    assert not validate_workspace_prompt(VALID_BASE, second.workspace).valid
    assembled = assemble_prompt(workspace, False, request.narrative_subjects, request.continuity_context)
    assert "Track the ongoing movement" in assembled.inspector_text
    prepared = prepare_workflow(store, second.project_id, second.active_generation_id, only, inspection=False)
    native = prepared.native_continuation
    source = store.continuation_status(first.project_id, first.active_generation_id)["executions"][0]
    assert source.i2va_state_identity == native.i2va_state_identity
    assert native.consume and native.source_id == r1.continuation_execution_id
    assert native.load_key == source.storage_namespace and native.load_segment_index == 1
    assert native.save_segment_index == 2 and not prepared.asset_requirements
    assert prepared.graph["6"]["inputs"]["width"] == 1024
    assert prepared.graph["6"]["inputs"]["height"] == 768
    assert "first_frame" not in prepared.graph["6"]["inputs"]
    assert prepared.graph["55"]["inputs"]["mode"] == "always_extend"
    assert prepared.graph["55"]["inputs"]["prev_latent"] == ["59", 0]
    assert prepared.graph["59"]["inputs"]["latent_path"] == source.storage_namespace
    assert prepared.graph["59"]["inputs"]["clip_index"] == 1
    calls = []
    r2 = send(env, second, only, calls, prepared.execution_fingerprint)
    assert [c.url.path for c in calls] == ["/prompt"]
    submitted = json.loads(calls[0].content)["prompt"]
    stored = store.continuation_status(second.project_id, second.active_generation_id)["executions"][0]
    expected = deepcopy(prepared.graph)
    expected["56"]["inputs"]["filename_prefix"] = stored.save_key
    assert submitted == expected
    assert stored.connection_id == connection.id and stored.render_submission_id == r2.render_submission_id
    assert stored.source_id == source.id and stored.storage_namespace != source.storage_namespace
    assert stored.save_segment_index == 2 and stored.execution_fingerprint == prepared.execution_fingerprint
    assert store.activate_generation(first.project_id, first.active_generation_id).workspace.assets
    assert store.activate_generation(first.project_id, second.active_generation_id).workspace.assets == []
    assert prepare_workflow(store, second.project_id, second.active_generation_id, only).execution_fingerprint == prepared.execution_fingerprint
    store.update_render_job(second.project_id, second.active_generation_id, r2.render_submission_id,
                            RendererJobState(status="completed"))
    accept(env, second, r2)
    third = successor(env, second, workspace_factory, only)
    reloaded = ProjectStore().load_project(first.project_id)
    assert reloaded.active_generation_id == third.active_generation_id
    assert reloaded.planning.visual_handoff.strategy == "native_i2va_continuation"
    g3 = prepare_workflow(ProjectStore(), third.project_id, third.active_generation_id, only)
    assert g3.native_continuation.source_id == r2.continuation_execution_id
    assert g3.native_continuation.load_key == stored.storage_namespace
    assert (g3.native_continuation.load_segment_index, g3.native_continuation.save_segment_index) == (2, 3)
    assert image_profile.bindings[2].presence == "required"


@pytest.mark.parametrize("case", ["missing", "unaccepted", "pending", "revoked", "legacy", "failed"])
def test_unavailable_native_source_never_calls_provider_or_renderer(env, workspace_factory, image_asset_factory, case):
    store, config, _ = env
    first, _, receipt = initial(env, workspace_factory, image_asset_factory,
                                completed=case != "pending", accepted=case != "unaccepted")
    if case in {"missing", "revoked"}:
        store.decide_continuation(first.project_id, first.active_generation_id, receipt.continuation_execution_id,
                                 ContinuationDecision(status="unavailable"))
    if case in {"legacy", "failed"}:
        directory, *_ = store._output_context(first.project_id, first.active_generation_id)
        state = store._read_execution_state(directory)
        if case == "legacy":
            state.continuation_executions[0].i2va_state_identity = None
        else:
            state.continuation_executions[0].confirmed = False
            state.continuation_executions[0].status = "unavailable"
        store._write_execution_state(directory, state)
    profile = config.save("profiles", successor_draft())
    second = successor(env, first, workspace_factory, profile)
    with pytest.raises(ProjectStoreError, match="Native H3 continuation unavailable"):
        resolve_narrative_request(request_for(second), store)
    calls = []
    with pytest.raises(ex.InvalidBindings, match="Native-only I2VA unavailable"):
        send(env, second, profile, calls)
    assert calls == []


@pytest.mark.parametrize("change", ["model", "vae", "format", "cross_mode"])
def test_model_codec_format_and_mode_are_strict(env, workspace_factory, image_asset_factory, change):
    store, config, _ = env
    first, _, receipt = initial(env, workspace_factory, image_asset_factory)
    data = successor_draft().model_dump(by_alias=True)
    graph = json.loads(data["workflowJson"])
    if change == "model": graph["1"]["inputs"]["unet_name"] = "other-family.safetensors"
    if change == "vae": graph["4"]["inputs"]["vae_name"] = "other-codec.safetensors"
    if change == "format": data["nativeContinuation"]["compatibilityKey"] = "other-native-format"
    if change == "cross_mode":
        directory, *_ = store._output_context(first.project_id, first.active_generation_id)
        state = store._read_execution_state(directory)
        state.continuation_executions[0].mode = "T2VA"
        store._write_execution_state(directory, state)
    data["workflowJson"] = json.dumps(graph)
    profile = config.save("profiles", ex.ProfileCreate(**data))
    second = successor(env, first, workspace_factory, profile)
    assert not resolve_native(store, second.project_id, second.active_generation_id, profile)["consume"]
    calls = []
    with pytest.raises(ex.InvalidBindings, match="unavailable"):
        send(env, second, profile, calls)
    assert calls == []


@pytest.mark.parametrize("change", ["scaled_picture", "linked_width", "imported_image", "first_frame", "wrong_load",
                                     "wrong_slot", "auto_extender", "anchor", "tail", "version"])
def test_profile_rejects_unsafe_topology_and_declarations(env, change):
    data = successor_draft().model_dump(by_alias=True)
    graph = json.loads(data["workflowJson"])
    if change == "scaled_picture":
        graph = json.loads(draft().workflow_json)  # Exact neutral 50→51→52→6 dependency.
        graph["6"]["inputs"].pop("first_frame")
    if change == "linked_width": graph["6"]["inputs"]["width"] = ["59", 0]
    if change == "imported_image": graph["50"] = {"class_type": "LoadImage", "inputs": {"image": "imported.png"}}
    if change == "first_frame": graph["6"]["inputs"]["first_frame"] = ["59", 1]
    if change == "wrong_load": data["nativeContinuation"]["sourceLinks"][0]["expectedOutputIndex"] = 1
    if change == "wrong_slot": data["nativeContinuation"]["inputs"]["load_segment_index"]["inputName"] = "latent_path"
    if change == "auto_extender": data["nativeContinuation"]["inputs"]["enable"]["enabledValue"] = "auto"
    if change == "anchor": data["nativeContinuation"]["allowEndpointAnchor"] = True
    if change == "tail": data["nativeContinuation"]["requiresTailReference"] = True
    if change == "version": data["nativeContinuation"]["pictureFreeI2va"]["version"] = 2
    data["workflowJson"] = json.dumps(graph)
    with pytest.raises((ex.InvalidBindings, ValidationError), match="Picture-free|Picture/loader|source link|source node|version"):
        env[1].save("profiles", ex.ProfileCreate(**data))


def test_picture_conflict_independent_and_auto_cannot_enable_new_path(env, workspace_factory, image_asset_factory):
    store, config, _ = env
    first, _, _ = initial(env, workspace_factory, image_asset_factory)
    profile = config.save("profiles", successor_draft())
    second = successor(env, first, workspace_factory, profile, assets=[image_asset_factory(role="first_frame_anchor").model_dump(mode="json", by_alias=True)])
    with pytest.raises(ex.InvalidBindings, match="expects no new Picture"):
        prepare_workflow(store, second.project_id, second.active_generation_id, profile)
    second = store.save_project(second.project_id, second.workspace.model_copy(update={"assets": []}),
        planning={"relationshipToPrevious": "independent", "visualHandoff": {"strategy": "native_i2va_continuation"}})
    with pytest.raises(ex.InvalidBindings, match="Continue previous shot"):
        prepare_workflow(store, second.project_id, second.active_generation_id, profile)
    second = store.save_project(second.project_id, second.workspace, planning={
        "relationshipToPrevious": "continue_previous_shot", "visualHandoff": {"strategy": "auto"}})
    with pytest.raises(ex.InvalidBindings, match="Explicitly select"):
        prepare_workflow(store, second.project_id, second.active_generation_id, profile)


def test_send_revalidates_revocation_connection_profile_and_prompt(env, workspace_factory, image_asset_factory):
    store, config, _ = env
    first, image_profile, receipt = initial(env, workspace_factory, image_asset_factory)
    only = config.save("profiles", successor_draft())
    second = successor(env, first, workspace_factory, only)
    request = resolve_narrative_request(request_for(second), store)
    prepared = prepare_workflow(store, second.project_id, second.active_generation_id, only)
    other = config.save("connections", ex.ConnectionWrite(name="Other", baseUrl="http://other.invalid"))
    calls = []
    with pytest.raises(ex.InvalidBindings, match="another ComfyUI Connection"):
        send((store, config, other), second, only, calls, prepared.execution_fingerprint)
    assert calls == []
    # Explicit selected-prompt authority cannot label this as ordinary I2VA.
    choose(env, second, only, I2VA_PROMPT)
    with pytest.raises(ex.InvalidBindings, match="Native-only I2VA prompt"):
        send(env, second, only, calls)
    choose(env, second, only)
    # Browsing/switching Profiles cannot turn the request into an unconditioned clip.
    with pytest.raises(ex.InvalidBindings, match="picture-free successor"):
        prepare_workflow(store, second.project_id, second.active_generation_id, image_profile)
    store.decide_continuation(first.project_id, first.active_generation_id, receipt.continuation_execution_id,
                             ContinuationDecision(status="unavailable"))
    with pytest.raises(ProjectStoreError, match="unavailable"):
        revalidate_native_authoring(request, store)
    with pytest.raises(ex.InvalidBindings, match="unavailable"):
        send(env, second, only, calls, prepared.execution_fingerprint)
    assert calls == []


def test_source_reaccept_changes_execution_and_authoring_fingerprints(env, workspace_factory, image_asset_factory):
    store, config, _ = env
    first, image_profile, receipt = initial(env, workspace_factory, image_asset_factory)
    only = config.save("profiles", successor_draft())
    second = successor(env, first, workspace_factory, only)
    request = resolve_narrative_request(request_for(second), store)
    prepared = prepare_workflow(store, second.project_id, second.active_generation_id, only)
    # A later unaccepted reroll is never selected by chronology.
    reroll = send(env, first, image_profile, [])
    assert resolve_native(store, second.project_id, second.active_generation_id, only)["record"].id == receipt.continuation_execution_id
    store.update_render_job(first.project_id, first.active_generation_id, reroll.render_submission_id,
                            RendererJobState(status="completed"))
    accept(env, first, reroll)
    fresh = prepare_workflow(store, second.project_id, second.active_generation_id, only)
    assert fresh.execution_fingerprint != prepared.execution_fingerprint
    assert fresh.native_continuation.source_id == reroll.continuation_execution_id
    with pytest.raises(ProjectStoreError, match="changed during authoring"):
        revalidate_native_authoring(request, store)
    calls = []
    with pytest.raises(ex.InvalidBindings, match="stale"):
        send(env, second, only, calls, prepared.execution_fingerprint)
    assert calls == []


async def test_selected_take_and_continue_again_preserve_exact_native_mode(env, workspace_factory, image_asset_factory, tmp_path):
    store, config, _ = env
    first, _, _ = initial(env, workspace_factory, image_asset_factory)
    only = config.save("profiles", successor_draft())
    second = successor(env, first, workspace_factory, only)
    receipt = send(env, second, only, [])
    store.update_render_job(second.project_id, second.active_generation_id, receipt.render_submission_id,
                            RendererJobState(status="completed"))
    accept(env, second, receipt)
    async def chunks(): yield video(tmp_path / "neutral.mp4")
    result = await store.import_take(second.project_id, second.active_generation_id, chunks(), "neutral.mp4",
        TakeImport(sourceKind="comfyui_submission", renderSubmissionId=receipt.render_submission_id))
    take = result["takes"][-1]
    store.select_take(second.project_id, second.active_generation_id, take["id"])
    current = store.load_project(second.project_id)
    third = store.continue_selected_take(second.project_id, second.active_generation_id,
        expected_project_fingerprint=current.project_fingerprint, take_id=take["id"], take_sha256=take["sha256"])
    assert third.workspace.mode.value == "I2VA" and third.workspace.assets == []
    assert third.planning.visual_handoff.strategy == "native_i2va_continuation"
    third = store.save_project(third.project_id, third.workspace.model_copy(update={"creative_request": "Continue cycling."}))
    choose(env, third, only)
    assert prepare_workflow(store, third.project_id, third.active_generation_id, only).native_continuation.source_id == receipt.continuation_execution_id


def test_server_projection_cannot_be_supplied_by_json(workspace_factory):
    data = workspace_factory().model_dump(mode="json", by_alias=True)
    data.update(mode="I2VA", assets=[])
    with pytest.raises(ValidationError, match="first-frame"):
        Workspace.model_validate(data)
    data["_native_i2va"] = True
    with pytest.raises(ValidationError):
        validate_authoring_workspace(data)


def test_native_only_cannot_start_g1_and_duplicate_profiles_remain_independent(env, workspace_factory):
    store, config, _ = env
    profile = config.save("profiles", successor_draft())
    raw = workspace_factory().model_dump(mode="json", by_alias=True)
    raw["mode"] = "I2VA"
    first = store.create_project(validate_authoring_workspace(raw))
    store.save_project(first.project_id, first.workspace, planning={
        "visualHandoff": {"strategy": "native_i2va_continuation"}})
    store.set_continuation_settings(first.project_id, first.active_generation_id, ContinuationSettings(profileId=profile.id))
    calls = []
    with pytest.raises(ex.InvalidBindings, match="unavailable"):
        send(env, first, profile, calls)
    with pytest.raises(ProjectStoreError, match="unavailable"):
        resolve_narrative_request(request_for(first), store)
    assert calls == []
    duplicate = config.save("profiles", config.duplicate_profile_draft(profile.id))
    assert duplicate.id != profile.id and duplicate.native_continuation.picture_free_i2va == profile.native_continuation.picture_free_i2va
    duplicate.workflow["6"]["inputs"]["width"] = 768
    assert config.get("profiles", profile.id).workflow["6"]["inputs"]["width"] == 1024


@pytest.mark.parametrize("revoke_during_provider", [False, True])
async def test_production_generation_uses_mock_provider_and_revalidates_acceptance(env, workspace_factory, image_asset_factory, revoke_during_provider):
    store, config, _ = env
    first, _, receipt = initial(env, workspace_factory, image_asset_factory)
    profile = config.save("profiles", successor_draft())
    second = successor(env, first, workspace_factory, profile)
    calls = []
    class OfflineProvider:
        def __init__(self, settings): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def generate(self, messages):
            calls.append(messages)
            assert "Accepted preceding scene authoring" in messages[0]["content"]
            assert "Allowed Picture references: none" in messages[0]["content"]
            if revoke_during_provider:
                store.decide_continuation(first.project_id, first.active_generation_id, receipt.continuation_execution_id,
                                         ContinuationDecision(status="unavailable"))
            return VALID_BASE
    if revoke_during_provider:
        with pytest.raises(ProjectStoreError, match="unavailable"):
            await generate_workspace_prompts(request_for(second), client_factory=OfflineProvider)
    else:
        result = await generate_workspace_prompts(request_for(second), client_factory=OfflineProvider)
        assert result.variations[0].generated.validation.valid
        assert result.variations[0].generated.reference_map == []
    assert len(calls) == 1


def test_story_native_only_preserves_scene_camera_and_exact_dialogue(env, workspace_factory, image_asset_factory):
    store, config, _ = env
    first, _, _ = initial(env, workspace_factory, image_asset_factory)
    profile = config.save("profiles", successor_draft())
    second = successor(env, first, workspace_factory, profile)
    second = store.save_project(second.project_id, second.workspace.model_copy(update={"dialogue": "Ride on!"}))
    request = resolve_narrative_request(request_for(second).model_copy(update={"authoring_path": "story"}), store)
    assembled = assemble_prompt(request.prompt_workspace, False, request.narrative_subjects,
                                request.continuity_context, request.story_context)
    for text in ("The cyclist continues across", "Track the ongoing movement", "Ride on!"):
        assert text in assembled.inspector_text
    spoken = VALID_BASE.replace("a cyclist waits.", 'a cyclist continues and calls <d>[English] Ride on!</d>.')
    assert validate_workspace_prompt(spoken, request.prompt_workspace).valid
    assert not validate_workspace_prompt(VALID_BASE, request.prompt_workspace).valid


def test_final_send_recompilation_blocks_revocation_before_prompt(env, workspace_factory, image_asset_factory, monkeypatch):
    from miniconstruct import execution_send
    store, config, _ = env
    first, _, receipt = initial(env, workspace_factory, image_asset_factory)
    profile = config.save("profiles", successor_draft())
    second = successor(env, first, workspace_factory, profile)
    original = execution_send.prepare_workflow
    compiles = []
    def compiler(*args, **kwargs):
        compiles.append(kwargs.get("revalidate"))
        if kwargs.get("revalidate") is not None:
            store.decide_continuation(first.project_id, first.active_generation_id, receipt.continuation_execution_id,
                                     ContinuationDecision(status="unavailable"))
        return original(*args, **kwargs)
    monkeypatch.setattr(execution_send, "prepare_workflow", compiler)
    calls = []
    with pytest.raises(ex.InvalidBindings, match="unavailable"):
        send(env, second, profile, calls)
    assert len(compiles) == 2 and calls == []


@pytest.mark.parametrize("external", [False, True])
async def test_selected_take_cannot_substitute_for_accepted_native_execution(env, workspace_factory, image_asset_factory, tmp_path, external):
    store, config, _ = env
    first, image_profile, _ = initial(env, workspace_factory, image_asset_factory)
    reroll = send(env, first, image_profile, [])
    store.update_render_job(first.project_id, first.active_generation_id, reroll.render_submission_id,
                            RendererJobState(status="completed"))
    async def chunks(): yield video(tmp_path / "neutral-other.mp4")
    result = await store.import_take(first.project_id, first.active_generation_id, chunks(), "neutral-other.mp4",
        TakeImport(sourceKind="external_unknown") if external else
        TakeImport(sourceKind="comfyui_submission", renderSubmissionId=reroll.render_submission_id))
    store.select_take(first.project_id, first.active_generation_id, result["takes"][-1]["id"])
    profile = config.save("profiles", successor_draft())
    second = successor(env, first, workspace_factory, profile)
    calls = []
    with pytest.raises(ex.InvalidBindings, match="unavailable"):
        send(env, second, profile, calls)
    assert calls == []


def test_translated_spoken_output_refresh_and_prepare_use_native_authority(env, workspace_factory, image_asset_factory, monkeypatch):
    from fastapi.testclient import TestClient
    from miniconstruct.app import app
    from miniconstruct.api import routes
    from miniconstruct.dialogue_language import dialogue_fingerprint
    from miniconstruct.models.workspace import DialogueLanguage, DialogueResolution
    store, config, _ = env
    first, _, receipt = initial(env, workspace_factory, image_asset_factory)
    profile = config.save("profiles", successor_draft())
    second = successor(env, first, workspace_factory, profile)
    second = store.save_project(second.project_id, second.workspace.model_copy(update={"dialogue": "Keep going!"}),
                                dialogue_language=DialogueLanguage.JA)
    resolution = DialogueResolution(sourceText="Keep going!", targetLanguage="ja", resolvedText="進もう！",
        sourceFingerprint=dialogue_fingerprint("Keep going!", DialogueLanguage.JA))
    calls = []
    async def translator(workspace, settings):
        calls.append(workspace.dialogue)
        return resolution
    monkeypatch.setattr(routes, "resolve_dialogue", translator)
    body = {"filesystemProjectId": second.project_id, "generationId": second.active_generation_id,
        "workspace": second.workspace.model_dump(mode="json", by_alias=True),
        "llm": {"baseUrl": "http://example.invalid/v1", "model": "offline"}}
    client = TestClient(app)
    response = client.post("/api/dialogue/resolve", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["resolution"]["resolvedText"] == "進もう！" and calls == ["Keep going!"]
    second = store.save_project(second.project_id, second.workspace.model_copy(update={"dialogue_resolution": resolution}))
    spoken = VALID_BASE.replace("a cyclist waits.", "a cyclist calls <d>[Japanese] 進もう！</d>.")
    choose(env, second, profile, spoken)
    assert prepare_workflow(store, second.project_id, second.active_generation_id, profile).native_continuation.consume
    choose(env, second, profile, VALID_BASE)
    with pytest.raises(ex.InvalidBindings, match="Exact dialogue"):
        prepare_workflow(store, second.project_id, second.active_generation_id, profile)
    store.decide_continuation(first.project_id, first.active_generation_id, receipt.continuation_execution_id,
                             ContinuationDecision(status="unavailable"))
    response = client.post("/api/dialogue/resolve", json=body)
    assert response.status_code == 400 and "unavailable" in response.text and len(calls) == 1


def test_model_dependency_identity_handles_shared_adapters_without_relying_on_node_ids():
    data = successor_draft()
    graph = json.loads(data.workflow_json)
    graph["adapter"] = {"class_type": "ApplyHyperFlow", "inputs": {"model": ["1", 0], "strength": 0.5}}
    graph["11"]["inputs"]["model"] = ["adapter", 0]
    original = state_identity(graph, data.native_continuation)
    assert original
    graph["renamed-model"] = graph.pop("1")
    graph["adapter"]["inputs"]["model"] = ["renamed-model", 0]
    assert state_identity(graph, data.native_continuation) == original
    graph["adapter"]["inputs"]["strength"] = 0.7
    assert state_identity(graph, data.native_continuation) != original
    graph["adapter"]["inputs"]["model"] = ["adapter", 0]
    assert state_identity(graph, data.native_continuation) is None
