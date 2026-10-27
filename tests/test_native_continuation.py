"""Native execution capability: real project authority, fictional graphs, mocked HTTP."""
import json
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from miniconstruct.app import app
from miniconstruct import execution as ex
from miniconstruct.execution_send import send_generation, retry_generation, ExecutionSendRequest
from miniconstruct.execution_state import SelectionWrite, ProjectExecutionState
from miniconstruct.execution_state import ManualEditAdoptionWrite, HistoryAdoptionWrite
from miniconstruct.execution_state import RendererSeedSettings
from miniconstruct.models.takes import TakeImport, RendererJobState
from miniconstruct.native_continuation import resolve_native, compatibility_identity, profile_identity
from miniconstruct.generation import resolve_narrative_request
from miniconstruct.h3.builder import assemble_prompt
from miniconstruct.native_continuation_models import NativeCapability, FrameGrid, ContinuationSettings, ContinuationDecision
from miniconstruct.models.api import GenerationRequest
from miniconstruct.models.workspace import H3Mode
from miniconstruct.project_store import ProjectStore, ProjectStoreError
from miniconstruct.workflow_preparation import prepare_workflow
from test_execution_send import recorder
from test_workflow_preparation import capacity_profile
from test_validator import VALID_BASE, valid_ref_prompt
from test_visual_handoff import video
from test_story_initial_range import LLM as STORY_LLM, OfflineInitialRangeClient
from miniconstruct.models.story_operations import StoryInitialRangeRequest
from miniconstruct.story_operations import build_initial_range


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Native capability tests must never call live HTTP")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)


def native_draft(av=False):
    draft = capacity_profile(av=av).model_dump(by_alias=True)
    graph = json.loads(draft["workflowJson"])
    semantics = ["enable", "chain_id", "generation_id", "predecessor_generation_id", "segment_index",
                 "context_frames", "extension_frames", "save_enabled", "save_key", "load_key",
                 "audio_enabled", "source_out_frame"]
    graph["native"] = {"class_type": "FictionalAVStateAdapter", "inputs": {key: "" for key in semantics}}
    draft["workflowJson"] = json.dumps(graph)
    draft["nativeContinuation"] = {"compatibilityKey": "generic-model-mode-v1", "contextFrames": 25,
        "contextGrid": {"minimum": 1, "step": 4, "maximum": 97, "alignment": "ceil"},
        "extensionGrid": {"minimum": 1, "step": 4, "alignment": "ceil"}, "framesPerSecond": 24,
        "audioContinuity": True, "audioEnabled": True, "requiredInputs": ["context_frames", "save_enabled"],
        "inputs": {key: {"nodeId": "native", "inputName": key} for key in semantics}}
    return draft


def pmhaidn_shape():
    """Fictional graph with the live profile's exact 164/165/167 mapping contract."""
    draft = json.loads((Path(__file__).parent / "fixtures/h3-pure-latent-continuation-profile.json").read_text())
    draft["compatibleModes"] = ["T2VA", "Ref2VA"]
    graph = json.loads(draft["workflowJson"])
    graph["165"] = graph.pop("native-save")
    graph["165"]["inputs"].pop("enabled")
    graph["decode"]["inputs"]["latent"] = ["165", 0]
    graph["164"]["inputs"].pop("audio_enabled")
    graph["sampler"]["inputs"]["seed"] = 123
    draft["workflowJson"] = json.dumps(graph)
    draft["bindings"].append({"source": {"kind": "renderer_seed"}, "nodeId": "sampler", "inputName": "seed"})
    draft["nativeContinuation"] = {
        "kind": "native_h3_av", "version": 1, "adapter": "opaque_keys",
        "compatibilityKey": "pmhaidn-h3-pure-latent-v1",
        "inputs": {
            "enable": {"nodeId": "164", "inputName": "mode", "enabledValue": "auto", "disabledValue": "disabled", "unsetValue": -1},
            "save_key": {"nodeId": "165", "inputName": "filename_prefix", "enabledValue": True, "disabledValue": False, "unsetValue": -1},
            "load_key": {"nodeId": "167", "inputName": "latent_path", "enabledValue": True, "disabledValue": False, "unsetValue": -1},
            "save_segment_index": {"nodeId": "165", "inputName": "clip_index", "enabledValue": True, "disabledValue": False, "unsetValue": -1},
            "load_segment_index": {"nodeId": "167", "inputName": "clip_index", "enabledValue": True, "disabledValue": False, "unsetValue": 0},
            "context_frames": {"nodeId": "164", "inputName": "context_length", "enabledValue": True, "disabledValue": False, "unsetValue": -1},
        },
        "sourceLinks": [{"nodeId": "164", "inputName": "prev_latent", "expectedSourceNodeId": "167", "expectedOutputIndex": 0}],
        "requiredInputs": ["save_segment_index", "load_segment_index", "context_frames"],
        "videoContinuity": True, "audioContinuity": False, "audioEnabled": False,
        "contextFrames": 22, "contextGrid": {"minimum": 5, "step": 17, "maximum": 39, "alignment": "reject"},
        "extensionGrid": None, "sourceOutGrid": None, "framesPerSecond": None,
        "requiresTailReference": False, "allowEndpointAnchor": False,
    }
    return draft


@pytest.fixture
def env(tmp_path, monkeypatch, workspace_factory):
    monkeypatch.setenv("MINICONSTRUCT_EXECUTION_ROOT", str(tmp_path / "execution"))
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(tmp_path / "projects"))
    config, store = ex.ExecutionStore(), ProjectStore()
    profile = config.save("profiles", ex.ProfileCreate(**native_draft()))
    connection = config.save("connections", ex.ConnectionWrite(name="Synthetic renderer", baseUrl="http://localhost:8188"))
    first = store.create_project(workspace_factory())
    store.set_continuation_settings(first.project_id, first.active_generation_id, ContinuationSettings(profileId=profile.id))
    return store, config, profile, connection, first


def choose(store, project, prompt=VALID_BASE):
    status = store.execution_snapshot(project.project_id, project.active_generation_id)[1]
    return store.select_execution_prompt(project.project_id, project.active_generation_id,
        SelectionWrite(prompt=prompt, sourceFingerprint=status.current_fingerprint))


@pytest.mark.asyncio
async def test_continue_selected_take_preserves_exact_native_lineage_without_sending(env, tmp_path):
    store, _, profile, _, first = env
    choose(store, first)
    receipt, calls = send(env)
    confirm(env, first, receipt)
    async def chunks(): yield video(tmp_path / "selected.mp4")
    outputs = await store.import_take(first.project_id, first.active_generation_id, chunks(), "selected.mp4",
        TakeImport(sourceKind="comfyui_submission", renderSubmissionId=receipt.render_submission_id))
    take = outputs["takes"][-1]
    store.select_take(first.project_id, first.active_generation_id, take["id"])
    saved = store.load_project(first.project_id)
    before_calls = list(calls)
    new = store.continue_selected_take(first.project_id, first.active_generation_id,
        expected_project_fingerprint=saved.project_fingerprint, take_id=take["id"], take_sha256=take["sha256"])
    assert new.workspace.mode == "T2VA"
    native = resolve_native(store, new.project_id, new.active_generation_id)
    assert native["consume"] and native["record"].id == receipt.continuation_execution_id
    assert native["sourceGenerationId"] == first.active_generation_id
    assert new.outputs["renderSubmissions"] == [] and calls == before_calls
    assert store.continuation_status(new.project_id, new.active_generation_id)["acceptedId"] is None


def send(env, project=None, failure=None, **kwargs):
    store, config, profile, connection, first = env
    p = project or first
    calls = []
    result = send_generation(ExecutionSendRequest(projectId=p.project_id, generationId=p.active_generation_id,
        profileId=profile.id, connectionId=connection.id, **kwargs), store=store, config=config,
        transport=recorder(calls, failure))
    return result, calls


def confirm(env, project, receipt):
    env[0].decide_continuation(project.project_id, project.active_generation_id, receipt.continuation_execution_id,
        ContinuationDecision(status="available"))


def next_generation(env, workspace_factory, relationship="continue_previous_shot", strategy="auto", **workspace):
    store, _, profile, _, first = env
    p = store.create_generation(first.project_id, workspace=workspace_factory(**workspace))
    p = store.save_project(p.project_id, p.workspace,
        planning={"relationshipToPrevious": relationship, "visualHandoff": {"strategy": strategy}})
    store.set_continuation_settings(p.project_id, p.active_generation_id, ContinuationSettings(profileId=profile.id))
    return p


def test_profile_contract_validation_and_read_without_rewrite(env):
    _, config, profile, _, _ = env
    before = config.path.read_bytes()
    assert config.get("profiles", profile.id).native_continuation.audio_continuity
    assert config.path.read_bytes() == before
    assert json.loads(before)["schemaVersion"] == 7
    legacy = json.loads(before); legacy["schemaVersion"] = 3
    legacy["profiles"][0].pop("nativeContinuation")
    config.path.write_text(json.dumps(legacy))
    old = config.path.read_bytes()
    assert config.get("profiles", profile.id).native_continuation is None
    assert config.path.read_bytes() == old
    assert ProjectExecutionState.model_validate({"schemaVersion": 1}).schema_version == 4


@pytest.mark.parametrize("broken", ["missing", "node", "input", "link", "duplicate", "required", "switch"])
def test_invalid_profile_mapping_is_rejected(env, broken):
    draft = native_draft()
    cap = draft["nativeContinuation"]
    if broken == "missing": cap["inputs"].pop("enable")
    elif broken == "node": cap["inputs"]["enable"]["nodeId"] = "absent"
    elif broken == "input": cap["inputs"]["enable"]["inputName"] = "absent"
    elif broken == "link": cap["inputs"]["enable"] = {"nodeId": "h3", "inputName": "context"}
    elif broken == "duplicate": cap["inputs"]["enable"] = {"nodeId": "h3", "inputName": "prompt"}
    elif broken == "required": cap["requiredInputs"].append("extension_seconds")
    else: cap["inputs"]["enable"].update(enabledValue=True, disabledValue=True)
    with pytest.raises((ValidationError, ex.InvalidBindings)):
        env[1].save("profiles", ex.ProfileCreate(**draft))


def test_frame_grids_audio_and_profile_fixed_context(env):
    assert FrameGrid(minimum=3, step=7, alignment="ceil").resolve(10) == 10
    assert FrameGrid(minimum=3, step=7, alignment="ceil").resolve(11) == 17
    assert FrameGrid(minimum=3, step=7, alignment="floor").resolve(11) == 10
    with pytest.raises(ValueError): FrameGrid(minimum=3, step=7).resolve(11)
    cap = native_draft()["nativeContinuation"]
    cap["audioContinuity"] = False
    with pytest.raises(ValueError, match="unsupported"): NativeCapability(**cap)
    store, _, profile, _, first = env
    before = store.continuation_status(first.project_id, first.active_generation_id)["settings"]
    with pytest.raises(ex.InvalidBindings):
        store.set_continuation_settings(first.project_id, first.active_generation_id,
                                       ContinuationSettings(profileId=profile.id, contextFrames=1000))
    assert store.continuation_status(first.project_id, first.active_generation_id)["settings"] == before


def test_g1_save_only_send_pending_then_explicit_confirmation(env):
    store, _, profile, _, first = env
    assert profile.native_continuation.source_links == []
    choose(store, first)
    prepared = prepare_workflow(store, first.project_id, first.active_generation_id, profile)
    inputs = prepared.graph["native"]["inputs"]
    assert inputs["enable"] is False and inputs["save_enabled"] is True
    assert inputs["load_key"] == "" and inputs["predecessor_generation_id"] == ""
    assert inputs["generation_id"] == first.active_generation_id and inputs["segment_index"] == 1
    assert store.continuation_status(first.project_id, first.active_generation_id)["executions"] == []
    result, calls = send(env, expectedExecutionFingerprint=prepared.execution_fingerprint)
    assert len(calls) == 1 and calls[0].url.path == "/prompt"
    status = store.continuation_status(first.project_id, first.active_generation_id)
    record = status["executions"][0]
    assert record.status == "pending" and status["acceptedId"] is None
    assert record.render_submission_id == result.render_submission_id
    assert record.save_key == json.loads(calls[0].content)["prompt"]["native"]["inputs"]["save_key"]
    confirm(env, first, result)
    assert store.continuation_status(first.project_id, first.active_generation_id)["acceptedId"] == record.id


def test_continuation_status_api_projects_exact_renderer_submission(env):
    store, _, profile, _, first = env
    choose(store, first)
    receipt, _ = send(env)
    client = TestClient(app)
    path = f"/api/execution/projects/{first.project_id}/generations/{first.active_generation_id}/continuation"
    status = client.get(path)
    assert status.status_code == 200, status.text
    execution = status.json()["executions"][0]
    submission = store.generation_outputs(first.project_id, first.active_generation_id)["renderSubmissions"][0]
    assert execution["renderSubmissionId"] == submission["id"] == str(receipt.render_submission_id)
    assert execution["renderSubmission"] == {
        "id": submission["id"], "createdAt": submission["createdAt"], "comfyPromptId": submission["comfyPromptId"],
        "jobStatus": "queued", "outputFilenames": [],
    }
    assert status.json()["renderSubmissions"] == [{
        "id": submission["id"], "createdAt": submission["createdAt"], "comfyPromptId": submission["comfyPromptId"],
        "jobStatus": "queued", "outputFilenames": [],
    }]


@pytest.mark.asyncio
async def test_native_summary_joins_imported_filename_by_submission_not_newest(env, tmp_path):
    store, _, _, _, first = env
    choose(store, first)
    older, _ = send(env)
    newest, _ = send(env)
    media = video(tmp_path / "imported.mp4")
    async def chunks():
        yield media
    await store.import_take(first.project_id, first.active_generation_id, chunks(), "Generation_00143_.mp4",
        TakeImport(sourceKind="comfyui_submission", renderSubmissionId=older.render_submission_id))
    status = store.continuation_status(first.project_id, first.active_generation_id, renderer_summary=True)
    rows = {row["id"]: row for row in status["executions"]}
    assert rows[str(older.continuation_execution_id)]["renderSubmission"]["outputFilenames"] == ["Generation_00143_.mp4"]
    assert rows[str(newest.continuation_execution_id)]["renderSubmission"]["outputFilenames"] == []
    assert status["acceptedId"] is None


def test_failed_native_retry_gets_fresh_namespace_and_same_predecessor(env, workspace_factory):
    store, config, _, _, first = env
    choose(store, first); prior, _ = send(env); confirm(env, first, prior)
    second = next_generation(env, workspace_factory); choose(store, second)
    failed, calls = send(env, second)
    original = next(r for r in store.continuation_status(second.project_id, second.active_generation_id)["executions"]
                    if r.id == failed.continuation_execution_id)
    store.update_render_job(second.project_id, second.active_generation_id,
        failed.render_submission_id, RendererJobState(status="failed"))
    retry = retry_generation(second.project_id, second.active_generation_id, failed.render_submission_id,
        store=store, config=config, transport=recorder(calls))
    fresh = next(r for r in store.continuation_status(second.project_id, second.active_generation_id)["executions"]
                 if r.id == retry.continuation_execution_id)
    assert fresh.id != original.id and fresh.save_key != original.save_key
    assert fresh.storage_namespace != original.storage_namespace
    assert fresh.source_id == original.source_id == prior.continuation_execution_id
    assert fresh.load_key == original.load_key
    assert store.continuation_status(second.project_id, second.active_generation_id)["acceptedId"] is None


def test_g2_g3_predecessor_only_reorder_rerolls_and_fingerprints(env, workspace_factory):
    store, _, profile, _, first = env
    choose(store, first); r1, _ = send(env); confirm(env, first, r1)
    second = next_generation(env, workspace_factory); choose(store, second)
    g2 = prepare_workflow(store, second.project_id, second.active_generation_id, profile)
    assert g2.native_continuation.source_generation_id.hex == first.active_generation_id.replace("-", "")
    assert not g2.asset_requirements and g2.reference_map == []
    r2, _ = send(env, second); confirm(env, second, r2)
    third = next_generation(env, workspace_factory); choose(store, third)
    g3 = prepare_workflow(store, third.project_id, third.active_generation_id, profile)
    assert g3.native_continuation.source_id == r2.continuation_execution_id
    # A pending reroll of G2 does not displace its accepted execution.
    reroll, _ = send(env, second)
    assert resolve_native(store, third.project_id, third.active_generation_id)["record"].id == r2.continuation_execution_id
    before_authoring = store.execution_snapshot(third.project_id, third.active_generation_id)[1].current_fingerprint
    confirm(env, second, reroll)
    assert store.execution_snapshot(third.project_id, third.active_generation_id)[1].current_fingerprint == before_authoring
    updated = prepare_workflow(store, third.project_id, third.active_generation_id, profile)
    assert updated.execution_fingerprint != g3.execution_fingerprint
    assert updated.native_continuation.source_id == reroll.continuation_execution_id
    assert updated.native_continuation.load_key != updated.native_continuation.save_key
    with pytest.raises(ex.InvalidBindings, match="stale"):
        send(env, third, expectedExecutionFingerprint=g3.execution_fingerprint)
    store.reorder_generations(first.project_id, [second.active_generation_id, first.active_generation_id, third.active_generation_id])
    resolved = resolve_native(store, third.project_id, third.active_generation_id)
    assert resolved["record"].id == r1.continuation_execution_id


def test_story_g2_h3_direction_keeps_native_execution_state_out_of_prompt(env, workspace_factory):
    store, _, profile, _, first = env
    choose(store, first); receipt, _ = send(env); confirm(env, first, receipt)
    second = next_generation(env, workspace_factory)
    second = store.save_project(second.project_id, second.workspace,
        incoming_state="A planned standing pose facing the doorway.",
        planning={"relationshipToPrevious": "continue_previous_shot",
                  "scenePlan": "She hears the latch move, turns toward it, and calls out."})
    raw = GenerationRequest.model_validate({
        "workspace": second.workspace.model_dump(mode="json", by_alias=True),
        "filesystemProjectId": second.project_id, "generationId": second.active_generation_id,
        "authoringPath": "story",
        "llm": {"baseUrl": "http://127.0.0.1:8888/v1", "model": "offline", "reasoningMode": "off"},
    })
    resolved = resolve_narrative_request(raw, store)
    assert resolved._visual_handoff.native["consume"] is True
    native_execution = resolve_native(store, second.project_id, second.active_generation_id)
    assert native_execution["record"].id == receipt.continuation_execution_id
    assert resolved._visual_handoff.native["requiresTailReference"] is False
    assembled = assemble_prompt(resolved.prompt_workspace, resolved.llm.supports_vision,
        resolved.narrative_subjects, resolved.continuity_context, resolved.story_context)
    instructions = "\n".join(str(message.get("content", "")) for message in assembled.messages)
    for required in ("continue directly from its rendered pose", "do not restage these to approximate",
                     "Continue the existing motion naturally before progressing into the current Scene Plan"):
        assert required in instructions
    for private_value in filter(None, (str(receipt.continuation_execution_id), str(profile.id),
                          native_execution["record"].save_key, native_execution["record"].load_key,
                          "native-artifact-uuid", "latent-cache-key")):
        assert private_value not in instructions


@pytest.mark.parametrize("relationship,strategy,consume", [
    ("independent", "auto", False), ("new_shot_preserve_continuity", "auto", False),
    ("continue_previous_shot", "none", False), ("new_shot_preserve_continuity", "native_h3_continuation", True)])
def test_explicit_authority_and_relationships(env, workspace_factory, relationship, strategy, consume):
    store, _, profile, _, first = env
    choose(store, first); receipt, _ = send(env); confirm(env, first, receipt)
    p = next_generation(env, workspace_factory, relationship, strategy)
    choose(store, p)
    prepared = prepare_workflow(store, p.project_id, p.active_generation_id, profile)
    assert prepared.native_continuation.consume is consume
    assert prepared.native_continuation.save


def test_explicit_native_unavailable_blocks_and_auto_textual_fallback(env, workspace_factory):
    store, _, profile, _, first = env
    p = next_generation(env, workspace_factory)
    summary = store.visual_handoff(p.project_id, p.active_generation_id).summary()
    assert summary["status"] == "textual_fallback" and summary["warnings"]
    store.save_project(p.project_id, p.workspace, planning={"visualHandoff": {"strategy": "native_h3_continuation"}})
    with pytest.raises(ProjectStoreError, match="Confirm|confirmed"):
        prepare_workflow(store, p.project_id, p.active_generation_id, profile)


async def test_selected_take_lineage_external_and_mismatched_reroll(env, workspace_factory, tmp_path):
    store, _, profile, _, first = env
    choose(store, first); r1, _ = send(env); confirm(env, first, r1)
    r2, _ = send(env)
    data = video(tmp_path / "generic.mp4", frames=3, audio=False)
    async def chunks(): yield data
    known = await store.import_take(first.project_id, first.active_generation_id, chunks(), "generic.mp4",
        TakeImport(sourceKind="comfyui_submission", renderSubmissionId=r2.render_submission_id))
    take = known["takes"][-1]["id"]
    store.select_take(first.project_id, first.active_generation_id, take)
    p = next_generation(env, workspace_factory)
    assert not resolve_native(store, p.project_id, p.active_generation_id)["available"]
    confirm(env, first, r2)
    assert resolve_native(store, p.project_id, p.active_generation_id)["record"].id == r2.continuation_execution_id
    with pytest.raises(ex.InvalidBindings, match="different/unknown"):
        confirm(env, first, r1)
    external = await store.import_take(first.project_id, first.active_generation_id, chunks(), "external.mp4", TakeImport(sourceKind="external_unknown"))
    store.select_take(first.project_id, first.active_generation_id, external["takes"][-1]["id"])
    assert "external/unknown" in resolve_native(store, p.project_id, p.active_generation_id)["reason"]


def test_settings_execution_staleness_not_prompt_staleness_and_out_point(env, workspace_factory):
    store, _, profile, _, first = env
    choose(store, first); receipt, _ = send(env); confirm(env, first, receipt)
    p = next_generation(env, workspace_factory); choose(store, p)
    before = prepare_workflow(store, p.project_id, p.active_generation_id, profile)
    store.set_continuation_settings(p.project_id, p.active_generation_id,
        ContinuationSettings(profileId=profile.id, contextFrames=26, audioEnabled=False, sourceOutFrame=80))
    after = prepare_workflow(store, p.project_id, p.active_generation_id, profile)
    assert before.current_fingerprint == after.current_fingerprint
    assert before.execution_fingerprint != after.execution_fingerprint
    assert after.native_continuation.context_frames == 29
    assert after.graph["native"]["inputs"]["source_out_frame"] == 80
    assert after.graph["native"]["inputs"]["audio_enabled"] is False
    assert before.graph["native"]["inputs"]["source_out_frame"] == -1


def test_failed_send_no_artifact_and_server_owned_api(env):
    store, _, _, _, p = env
    choose(store, p)
    with pytest.raises(ex.InvalidBindings):
        send(env, failure=lambda *args: httpx.Response(400, json={"node_errors": {}}))
    assert store.continuation_status(p.project_id, p.active_generation_id)["executions"] == []
    client = TestClient(app)
    path = f"/api/execution/projects/{p.project_id}/generations/{p.active_generation_id}/continuation"
    assert client.get(path).status_code == 200
    assert client.put(path + "/settings", json={"saveKey": "C:/private/state"}).status_code == 422
    assert client.put(path + "/" + str(uuid4()), json={"status": "available"}).status_code == 422


def test_native_mapping_fixture_and_core_no_package_names():
    fixture = json.loads((Path(__file__).parent / "fixtures/native-continuation-profile.json").read_text())
    draft = ex.ProfileCreate(**fixture)
    assert not ex.native_mapping_errors(ex.parse_workflow(draft.workflow_json), draft.bindings, draft.native_continuation)
    shaped = json.loads((Path(__file__).parent / "fixtures/h3-pure-latent-continuation-profile.json").read_text())
    shaped_draft = ex.ProfileCreate(**shaped)
    assert not ex.native_mapping_errors(ex.parse_workflow(shaped_draft.workflow_json), shaped_draft.bindings,
                                         shaped_draft.native_continuation)
    core = (Path(__file__).parents[1] / "miniconstruct/native_continuation.py").read_text()
    assert "class_type" not in core and "stat(" not in core and "glob(" not in core and "getmtime" not in core


@pytest.mark.parametrize("broken", ["target", "input", "source", "wrong_output", "duplicate", "collision", "missing_edge"])
def test_native_source_link_validation_is_exact(env, broken):
    fixture = json.loads((Path(__file__).parent / "fixtures/h3-pure-latent-continuation-profile.json").read_text())
    cap = fixture["nativeContinuation"]
    link = cap["sourceLinks"][0]
    if broken == "target":
        link["nodeId"] = "absent"
    elif broken == "input":
        link["inputName"] = "absent"
    elif broken == "source":
        link["expectedSourceNodeId"] = "absent"
    elif broken == "wrong_output":
        link["expectedOutputIndex"] = 1
    elif broken == "duplicate":
        cap["sourceLinks"].append(dict(link))
    elif broken == "missing_edge":
        graph = json.loads(fixture["workflowJson"])
        graph["164"]["inputs"].pop("prev_latent")
        fixture["workflowJson"] = json.dumps(graph)
    else:
        cap["inputs"]["enable"] = {"nodeId": "164", "inputName": "prev_latent"}
    with pytest.raises((ValidationError, ex.InvalidBindings)):
        env[1].save("profiles", ex.ProfileCreate(**fixture))


def test_pure_latent_shape_uses_directional_slots_and_save_only_first_generation(env, workspace_factory):
    store, config, _, connection, first = env
    fixture = json.loads((Path(__file__).parent / "fixtures/h3-pure-latent-continuation-profile.json").read_text())
    fixture["compatibleModes"] = ["T2VA", "Ref2VA"]
    profile = config.save("profiles", ex.ProfileCreate(**fixture))
    store.set_continuation_settings(first.project_id, first.active_generation_id, ContinuationSettings(profileId=profile.id))
    choose(store, first)
    first_prepared = prepare_workflow(store, first.project_id, first.active_generation_id, profile)
    first_inputs = first_prepared.graph
    assert first_prepared.native_continuation.consume is False
    assert first_inputs["164"]["inputs"]["mode"] == "disabled"
    assert "prev_latent" not in first_inputs["164"]["inputs"]
    assert first_inputs["167"]["inputs"] == {"latent_path": "", "clip_index": 0}
    assert first_inputs["native-save"]["inputs"]["enabled"] is True
    assert first_inputs["native-save"]["inputs"]["clip_index"] == 1
    assert first_inputs["164"]["inputs"]["context_length"] == 22
    assert first_inputs["164"]["inputs"]["audio_enabled"] is False
    first_receipt, calls = send((store, config, profile, connection, first), expectedExecutionFingerprint=first_prepared.execution_fingerprint)
    sent_first = json.loads(calls[0].content)["prompt"]
    assert "prev_latent" not in sent_first["164"]["inputs"]
    assert sent_first["native-save"]["inputs"]["clip_index"] == 1
    confirm((store, config, profile, connection, first), first, first_receipt)
    first_record = store.continuation_status(first.project_id, first.active_generation_id)["executions"][0]

    second = next_generation((store, config, profile, connection, first), workspace_factory)
    choose(store, second)
    second_prepared = prepare_workflow(store, second.project_id, second.active_generation_id, profile)
    second_inputs = second_prepared.graph
    assert second_prepared.native_continuation.consume is True
    assert second_inputs["164"]["inputs"]["mode"] == "auto"
    assert second_inputs["164"]["inputs"]["prev_latent"] == ["167", 0]
    assert second_prepared.native_continuation.load_key == first_record.storage_namespace
    assert second_inputs["167"]["inputs"]["latent_path"] == first_record.storage_namespace
    assert second_inputs["167"]["inputs"]["clip_index"] == 1
    assert second_inputs["native-save"]["inputs"]["clip_index"] == 2
    assert second_prepared.native_continuation.save_key != second_prepared.native_continuation.load_key
    assert second_prepared.native_continuation.save_segment_index == 2
    assert second_prepared.native_continuation.load_segment_index == 1
    assert second_inputs["164"]["inputs"]["audio_enabled"] is True


@pytest.mark.asyncio
async def test_story_created_g2_auto_consumes_exact_accepted_g1_latent(env, workspace_factory):
    store, config, _, connection, _ = env
    fixture = json.loads((Path(__file__).parent / "fixtures/h3-pure-latent-continuation-profile.json").read_text())
    fixture["compatibleModes"] = ["T2VA", "Ref2VA"]
    profile = config.save("profiles", ex.ProfileCreate(**fixture))
    first = store.create_project(workspace_factory(), story_idea="A courier reaches the observatory.")
    store.set_continuation_settings(first.project_id, first.active_generation_id, ContinuationSettings(profileId=profile.id))
    choose(store, first)
    first_receipt, _ = send((store, config, profile, connection, first))
    confirm((store, config, profile, connection, first), first, first_receipt)
    accepted = store.continuation_status(first.project_id, first.active_generation_id)["executions"][0]

    def continue_previous(result, _):
        result["items"][1]["relationshipToPrevious"] = "continue_previous_shot"
        result["items"][1]["dialogue"] = ""
    draft = await build_initial_range(store, first.project_id,
        StoryInitialRangeRequest(requestedTotalCount=2, llm=STORY_LLM), OfflineInitialRangeClient(continue_previous))
    store.apply_story_initial_range(first.project_id, draft)
    story = store.read_story_snapshot(first.project_id)
    second_id = story.generations[1].generation_id
    assert story.generations[1].planning.visual_handoff.strategy == "auto"
    assert store.continuation_status(first.project_id, second_id)["settings"].profile_id == profile.id
    status = store.execution_snapshot(first.project_id, second_id)[1]
    store.select_execution_prompt(first.project_id, second_id,
        SelectionWrite(prompt=VALID_BASE, sourceFingerprint=status.current_fingerprints["story"], authoringPath="story"))
    prepared = prepare_workflow(store, first.project_id, second_id, profile)
    native = prepared.native_continuation
    assert native.consume and native.source_id == accepted.id
    assert str(native.source_generation_id) == first.active_generation_id
    assert native.context_frames == 22 and native.load_segment_index == 1 and native.save_segment_index == 2
    assert native.load_key == accepted.storage_namespace and native.save_key != native.load_key
    assert prepared.graph["164"]["inputs"]["mode"] == "auto"
    assert prepared.graph["164"]["inputs"]["prev_latent"] == ["167", 0]
    assert prepared.graph["167"]["inputs"]["latent_path"] == accepted.storage_namespace
    assert prepared.graph["167"]["inputs"]["clip_index"] == 1
    assert prepared.graph["native-save"]["inputs"]["clip_index"] == 2
    assert not prepared.asset_requirements


def test_pmhaidn_shaped_g1_uses_non_consuming_slot_zero_only_as_sentinel(env):
    store, config, _, connection, first = env
    profile = config.save("profiles", ex.ProfileCreate(**pmhaidn_shape()))
    store.set_continuation_settings(first.project_id, first.active_generation_id, ContinuationSettings(profileId=profile.id))
    choose(store, first)
    prepared = prepare_workflow(store, first.project_id, first.active_generation_id, profile)
    native = prepared.native_continuation
    assert native.consume is False and native.load_segment_index is None and native.save_segment_index == 1
    assert prepared.graph["164"]["inputs"]["mode"] == "disabled"
    assert "prev_latent" not in prepared.graph["164"]["inputs"]
    assert prepared.graph["167"]["inputs"] == {"latent_path": "", "clip_index": 0}
    assert prepared.graph["165"]["inputs"]["clip_index"] == 1
    receipt, calls = send((store, config, profile, connection, first))
    submitted = json.loads(calls[0].content)["prompt"]
    assert submitted["164"]["inputs"]["mode"] == "disabled"
    assert "prev_latent" not in submitted["164"]["inputs"]
    assert submitted["167"]["inputs"]["clip_index"] == 0
    assert submitted["165"]["inputs"]["clip_index"] == 1
    assert receipt.continuation_execution_id is not None


@pytest.mark.asyncio
async def test_pmhaidn_shaped_story_g2_submits_exact_accepted_g1_slot(env, workspace_factory):
    store, config, _, connection, _ = env
    profile = config.save("profiles", ex.ProfileCreate(**pmhaidn_shape()))
    first = store.create_project(workspace_factory(), story_idea="A courier reaches the observatory.")
    store.set_continuation_settings(first.project_id, first.active_generation_id, ContinuationSettings(profileId=profile.id))
    choose(store, first)
    first_receipt, _ = send((store, config, profile, connection, first))
    confirm((store, config, profile, connection, first), first, first_receipt)
    accepted = store.continuation_status(first.project_id, first.active_generation_id)["executions"][0]

    def continue_previous(result, _):
        result["items"][1]["relationshipToPrevious"] = "continue_previous_shot"
        result["items"][1]["dialogue"] = ""
    draft = await build_initial_range(store, first.project_id,
        StoryInitialRangeRequest(requestedTotalCount=2, llm=STORY_LLM), OfflineInitialRangeClient(continue_previous))
    store.apply_story_initial_range(first.project_id, draft)
    second_id = store.read_story_snapshot(first.project_id).generations[1].generation_id
    assert store.continuation_status(first.project_id, second_id)["settings"].profile_id == profile.id
    assert store.read_story_snapshot(first.project_id).generations[1].planning.visual_handoff.strategy == "auto"
    status = store.execution_snapshot(first.project_id, second_id)[1]
    store.select_execution_prompt(first.project_id, second_id,
        SelectionWrite(prompt=VALID_BASE, sourceFingerprint=status.current_fingerprints["story"], authoringPath="story"))
    assert store.generation_outputs(first.project_id, first.active_generation_id)["selectedTakeId"] is None
    prepared = prepare_workflow(store, first.project_id, second_id, profile)
    native = prepared.native_continuation
    assert native.consume and native.source_id == accepted.id
    assert str(native.source_generation_id) == first.active_generation_id
    assert native.load_segment_index == accepted.save_segment_index == 1
    assert native.save_segment_index == 2 and native.context_frames == 22
    assert native.load_key == accepted.storage_namespace
    assert native.save_key != accepted.save_key and native.storage_namespace != accepted.storage_namespace
    assert prepared.graph["164"]["inputs"]["mode"] == "auto"
    assert prepared.graph["164"]["inputs"]["prev_latent"] == ["167", 0]
    assert prepared.graph["167"]["inputs"]["latent_path"] == accepted.storage_namespace
    assert prepared.graph["167"]["inputs"]["clip_index"] == 1
    assert prepared.graph["165"]["inputs"]["filename_prefix"] == native.save_key
    assert prepared.graph["165"]["inputs"]["clip_index"] == 2
    assert not prepared.asset_requirements

    calls = []
    receipt = send_generation(ExecutionSendRequest(projectId=first.project_id, generationId=second_id,
        profileId=profile.id, connectionId=connection.id,
        expectedExecutionFingerprint=prepared.execution_fingerprint),
        store=store, config=config, transport=recorder(calls))
    assert len(calls) == 1 and calls[0].url.path == "/prompt"
    submitted = json.loads(calls[0].content)["prompt"]
    assert submitted["164"]["inputs"]["mode"] == "auto"
    assert submitted["164"]["inputs"]["prev_latent"] == ["167", 0]
    assert submitted["167"]["inputs"]["latent_path"] == accepted.storage_namespace
    assert submitted["167"]["inputs"]["clip_index"] == 1
    assert submitted["165"]["inputs"]["clip_index"] == 2
    assert submitted["165"]["inputs"]["filename_prefix"] != accepted.save_key
    assert receipt.renderer_seed == submitted["sampler"]["inputs"]["seed"]
    sent_record = store.continuation_status(first.project_id, second_id)["executions"][0]
    assert sent_record.consume and sent_record.source_id == accepted.id
    assert sent_record.load_segment_index == 1 and sent_record.save_segment_index == 2
    assert sent_record.storage_namespace != accepted.storage_namespace
    expected = deepcopy(prepared.graph)
    expected["sampler"]["inputs"]["seed"] = receipt.renderer_seed
    # Send generates a new native save namespace for each attempt; compare
    # unrelated graph nodes and the exact predecessor-facing mapped inputs.
    assert submitted["164"] == expected["164"]
    assert submitted["167"] == expected["167"]
    assert submitted["conditioning"] == expected["conditioning"]


@pytest.mark.parametrize("prompt_source", ["manual", "history"])
def test_accepted_native_state_survives_graph_drift_and_h3_prompt_change(env, workspace_factory, prompt_source):
    store, config, profile, connection, first = env
    choose(store, first)
    first_receipt, _ = send(env)
    confirm(env, first, first_receipt)
    second = next_generation(env, workspace_factory)
    choose(store, second)

    second_receipt, _ = send(env, second)
    confirm(env, second, second_receipt)
    accepted = store.continuation_status(second.project_id, second.active_generation_id)["executions"][0]

    third = next_generation(env, workspace_factory)
    edited = VALID_BASE.replace("Rain and distant traffic.", "A hand-edited rainstorm fills the shelter roof.")
    if prompt_source == "history":
        selected = store.adopt_history_execution_prompt(third.project_id, third.active_generation_id,
            HistoryAdoptionWrite(prompt=edited)).selected
    else:
        selected = store.adopt_manual_edit_execution_prompt(third.project_id, third.active_generation_id,
            ManualEditAdoptionWrite(prompt=edited)).selected
    assert selected.prompt == edited

    # Drift both the graph fingerprint and current native Load wiring. Accepted
    # G2 state remains the source; these current mappings own G3 graph wiring.
    draft = native_draft()
    graph = json.loads(draft["workflowJson"])
    graph["native"]["inputs"]["reimported_constant"] = 1
    graph["native-load-current"] = {"class_type": "FictionalCurrentLoad", "inputs": {
        "latent_path": "", "clip_index": 0}}
    draft["nativeContinuation"]["inputs"]["load_key"] = {
        "nodeId": "native-load-current", "inputName": "latent_path"}
    draft["nativeContinuation"]["inputs"]["load_segment_index"] = {
        "nodeId": "native-load-current", "inputName": "clip_index"}
    draft["workflowJson"] = json.dumps(graph)
    current = config.save("profiles", ex.ProfileUpdate(**draft), profile.id)
    resolved = resolve_native(store, third.project_id, third.active_generation_id, current)
    assert resolved["available"] and resolved["consume"]
    assert resolved["record"].id == accepted.id
    assert any("different Workflow Profile graph" in warning for warning in resolved["warnings"])

    prepared = prepare_workflow(store, third.project_id, third.active_generation_id, current)
    native = prepared.native_continuation
    assert native.consume and native.source_id == accepted.id
    assert str(native.source_generation_id) == second.active_generation_id
    assert native.load_key == accepted.storage_namespace
    assert native.storage_namespace != accepted.storage_namespace
    assert prepared.graph["native-load-current"]["inputs"] == {
        "latent_path": accepted.storage_namespace, "clip_index": accepted.save_segment_index}
    assert prepared.graph["native"]["inputs"]["load_key"] == ""
    assert prepared.graph["h3"]["inputs"]["prompt"] == edited
    assert any("different Workflow Profile graph" in warning for warning in prepared.warnings)
    assert any(d.code == "native.compatibility" and d.severity == "warning" for d in prepared.diagnostics)

    calls = []
    receipt = send_generation(ExecutionSendRequest(projectId=third.project_id, generationId=third.active_generation_id,
        profileId=current.id, connectionId=connection.id,
        expectedExecutionFingerprint=prepared.execution_fingerprint),
        store=store, config=config, transport=recorder(calls))
    assert len(calls) == 1 and calls[0].url.path == "/prompt"
    submitted = json.loads(calls[0].content)["prompt"]
    assert submitted["h3"]["inputs"]["prompt"] == edited
    assert submitted["native-load-current"]["inputs"] == {
        "latent_path": accepted.storage_namespace, "clip_index": accepted.save_segment_index}
    assert receipt.submitted
    sent = store.continuation_status(third.project_id, third.active_generation_id)["executions"][0]
    assert sent.consume and sent.source_id == accepted.id


def test_renderer_seed_preference_does_not_change_accepted_native_compatibility(env, workspace_factory):
    store, config, _, connection, first = env
    draft = native_draft()
    graph = json.loads(draft["workflowJson"])
    graph["native"]["inputs"]["renderer_seed"] = 44
    draft["workflowJson"] = json.dumps(graph)
    draft["bindings"].append({"source": {"kind": "renderer_seed"}, "nodeId": "native", "inputName": "renderer_seed"})
    profile = config.save("profiles", ex.ProfileCreate(**draft))
    store.set_continuation_settings(first.project_id, first.active_generation_id, ContinuationSettings(profileId=profile.id))
    choose(store, first)
    receipt, _ = send((store, config, profile, connection, first))
    confirm((store, config, profile, connection, first), first, receipt)
    identity = compatibility_identity(profile)
    second = next_generation((store, config, profile, connection, first), workspace_factory)
    choose(store, second)
    before = prepare_workflow(store, second.project_id, second.active_generation_id, profile)
    assert before.native_continuation.consume
    store.set_renderer_seed_settings(first.project_id, first.active_generation_id,
                                     RendererSeedSettings(mode="fixed", fixedSeed=12345))
    store.set_renderer_seed_settings(second.project_id, second.active_generation_id,
                                     RendererSeedSettings(mode="fixed", fixedSeed=67890))
    after = prepare_workflow(store, second.project_id, second.active_generation_id, profile)
    assert after.execution_fingerprint != before.execution_fingerprint
    assert after.current_fingerprint == before.current_fingerprint
    assert after.native_continuation.consume and after.native_continuation.source_id == receipt.continuation_execution_id
    assert after.native_continuation.compatibility_fingerprint == identity == compatibility_identity(profile)


def test_preset_edits_do_not_change_accepted_native_predecessor(env, workspace_factory):
    store, config, _, connection, first = env
    draft = native_draft()
    graph = json.loads(draft["workflowJson"])
    graph["native"]["inputs"]["preset_flag"] = False
    draft["workflowJson"] = json.dumps(graph)
    draft["exposedControls"] = [{"id": "preset_flag", "label": "Preset flag",
        "target": {"nodeId": "native", "inputName": "preset_flag"}, "type": "boolean", "defaultValue": False}]
    profile = config.save("profiles", ex.ProfileCreate(**draft))
    store.set_continuation_settings(first.project_id, first.active_generation_id, ContinuationSettings(profileId=profile.id))
    choose(store, first)
    chain = store, config, profile, connection, first
    receipt, _ = send(chain)
    confirm(chain, first, receipt)
    second = next_generation(chain, workspace_factory)
    choose(store, second)
    identity = profile_identity(profile)
    for presets in ([{"name": "Turbo", "values": {"preset_flag": True}}],
                    [{"name": "Quality", "values": {"preset_flag": False}}], []):
        profile = config.save("profiles", ex.ProfileUpdate(name=profile.name,
            compatibleModes=list(profile.compatible_modes), bindings=profile.bindings,
            nativeContinuation=profile.native_continuation, exposedControls=profile.exposed_controls,
            workflowControlPresets=presets), profile.id)
        prepared = prepare_workflow(store, second.project_id, second.active_generation_id, profile)
        assert profile_identity(profile) == identity
        assert prepared.native_continuation.consume
        assert prepared.native_continuation.source_id == receipt.continuation_execution_id


@pytest.mark.asyncio
async def test_native_attempt_removal_preserves_render_submission_take_and_media(env, tmp_path, workspace_factory):
    store, config, profile, connection, first = env
    choose(store, first)
    pending, _ = send(env)
    media = video(tmp_path / "attempt.mp4")
    async def chunks(): yield media
    imported = await store.import_take(first.project_id, first.active_generation_id, chunks(), "attempt.mp4",
        TakeImport(sourceKind="comfyui_submission", renderSubmissionId=pending.render_submission_id))
    take_id = imported["takes"][-1]["id"]
    media_path, _ = store.take_media(first.project_id, first.active_generation_id, take_id)
    second = next_generation(env, workspace_factory)
    client = TestClient(app)
    base = f"/api/execution/projects/{first.project_id}/generations/{first.active_generation_id}/continuation"
    assert client.delete(f"{base}/{uuid4()}").status_code == 422
    assert client.delete(f"/api/execution/projects/{uuid4()}/generations/{first.active_generation_id}/continuation/{pending.continuation_execution_id}").status_code == 404
    assert client.delete(f"/api/execution/projects/{first.project_id}/generations/{second.active_generation_id}/continuation/{pending.continuation_execution_id}").status_code == 422
    removed = client.delete(f"{base}/{pending.continuation_execution_id}")
    assert removed.status_code == 200 and removed.json()["executions"] == []
    assert media_path.read_bytes() == media
    outputs = store.generation_outputs(first.project_id, first.active_generation_id)
    assert any(row["id"] == str(pending.render_submission_id) for row in outputs["renderSubmissions"])
    assert any(row["id"] == take_id and row["renderSubmissionId"] == str(pending.render_submission_id) for row in outputs["takes"])
    assert store.continuation_status(first.project_id, first.active_generation_id)["executions"] == []


def test_accepted_attempt_requires_revocation_and_bulk_only_clears_unavailable(env):
    store, _, _, _, first = env
    choose(store, first)
    accepted, _ = send(env)
    confirm(env, first, accepted)
    pending, _ = send(env)
    unavailable, _ = send(env)
    available_other, _ = send(env)
    store.decide_continuation(first.project_id, first.active_generation_id,
                              available_other.continuation_execution_id,
                              ContinuationDecision(status="available", accept=False))
    store.decide_continuation(first.project_id, first.active_generation_id,
                              unavailable.continuation_execution_id, ContinuationDecision(status="unavailable"))
    base = f"/api/execution/projects/{first.project_id}/generations/{first.active_generation_id}/continuation"
    client = TestClient(app)
    blocked = client.delete(f"{base}/{accepted.continuation_execution_id}")
    assert blocked.status_code == 422 and "Revoke acceptance" in blocked.text
    cleaned = client.delete(base + "/unavailable")
    assert cleaned.status_code == 200 and cleaned.json()["removedCount"] == 1
    assert {row["id"] for row in cleaned.json()["executions"]} == {
        str(accepted.continuation_execution_id), str(pending.continuation_execution_id),
        str(available_other.continuation_execution_id)}
    assert cleaned.json()["acceptedId"] == str(accepted.continuation_execution_id)
    assert len(store.generation_outputs(first.project_id, first.active_generation_id)["renderSubmissions"]) == 4
    revoked = client.put(f"{base}/{accepted.continuation_execution_id}",
                          json={"status": "available", "accept": False})
    assert revoked.status_code == 200 and revoked.json()["acceptedId"] is None
    assert next(row for row in revoked.json()["executions"] if row["id"] == str(accepted.continuation_execution_id))["status"] == "available"
    assert client.delete(f"{base}/{accepted.continuation_execution_id}").status_code == 200
    assert client.delete(base + "/unavailable").json()["removedCount"] == 0
    assert len(store.generation_outputs(first.project_id, first.active_generation_id)["renderSubmissions"]) == 4


def test_pending_predecessor_reroll_never_replaces_explicitly_accepted_source(env, workspace_factory):
    store, _, profile, _, first = env
    choose(store, first)
    accepted, _ = send(env); confirm(env, first, accepted)
    source_a = store.continuation_status(first.project_id, first.active_generation_id)["executions"][0]
    second = next_generation(env, workspace_factory); choose(store, second)
    assert prepare_workflow(store, second.project_id, second.active_generation_id, profile).native_continuation.source_id == source_a.id
    reroll, _ = send(env, first)
    assert resolve_native(store, second.project_id, second.active_generation_id)["record"].id == source_a.id
    confirm(env, first, reroll)
    assert prepare_workflow(store, second.project_id, second.active_generation_id, profile).native_continuation.source_id == reroll.continuation_execution_id
    store.decide_continuation(first.project_id, first.active_generation_id, reroll.continuation_execution_id,
                              ContinuationDecision(status="unavailable"))
    assert resolve_native(store, second.project_id, second.active_generation_id)["record"] is None


@pytest.mark.parametrize("both", [False, True])
async def test_native_vs_tail_fallback_upload_conflicts(env, workspace_factory, image_asset_factory, tmp_path, both):
    store, config, old_profile, connection, _ = env
    draft = native_draft(av=True)
    draft["nativeContinuation"]["requiresTailReference"] = both
    profile = config.save("profiles", ex.ProfileCreate(**draft))
    first = store.create_project(workspace_factory(mode="Ref2VA", assets=[image_asset_factory(role="subject_identity")]))
    store.set_continuation_settings(first.project_id, first.active_generation_id, ContinuationSettings(profileId=profile.id))
    env = store, config, profile, connection, first
    choose(store, first, valid_ref_prompt())
    receipt, _ = send(env); confirm(env, first, receipt)
    data = video(tmp_path / "synthetic.mp4")
    async def chunks(): yield data
    imported = await store.import_take(first.project_id, first.active_generation_id, chunks(), "synthetic.mp4",
        TakeImport(sourceKind="comfyui_submission", renderSubmissionId=receipt.render_submission_id))
    store.select_take(first.project_id, first.active_generation_id, imported["takes"][-1]["id"])
    p = next_generation(env, workspace_factory, mode="Ref2VA", assets=[image_asset_factory(role="subject_identity")])
    tail_prompt = valid_ref_prompt().replace("[reference generation]", "[reference generation + video continuation]")
    choose(store, p, tail_prompt if both else valid_ref_prompt())
    prepared = prepare_workflow(store, p.project_id, p.active_generation_id, profile)
    assert prepared.native_continuation.consume
    assert [r.asset_kind for r in prepared.asset_requirements].count("video") == int(both)
    assert [r.asset_kind for r in prepared.asset_requirements].count("image") == 1
    _, calls = send(env, p)
    assert len([c for c in calls if c.url.path == "/upload/image"]) == 1 + int(both)
    if not both:
        assert "video-1" not in json.loads(calls[-1].content)["prompt"]["h3"]["inputs"]
    # External Selected Take safely falls back to a real tail, even though native state exists.
    external = await store.import_take(first.project_id, first.active_generation_id, chunks(), "external.mp4", TakeImport(sourceKind="external_unknown"))
    store.select_take(first.project_id, first.active_generation_id, external["takes"][-1]["id"])
    handoff = store.visual_handoff(p.project_id, p.active_generation_id, materialize=True)
    assert not handoff.native["consume"] and handoff.policy.strategy == "tail_reference_continuation"
    choose(store, p, tail_prompt)
    fallback = prepare_workflow(store, p.project_id, p.active_generation_id, profile)
    assert not fallback.native_continuation.consume
    assert any(r.asset_kind == "video" for r in fallback.asset_requirements)


def test_unsupported_audio_setting_still_fails_but_profile_drift_keeps_accepted_state(env, workspace_factory):
    store, config, profile, _, first = env
    choose(store, first); receipt, _ = send(env); confirm(env, first, receipt)
    p = next_generation(env, workspace_factory)
    draft = native_draft()
    draft["nativeContinuation"].update(audioContinuity=False, audioEnabled=False)
    profile = config.save("profiles", ex.ProfileUpdate(**draft), profile.id)
    resolved = resolve_native(store, p.project_id, p.active_generation_id)
    assert resolved["available"] and resolved["consume"] and not resolved["audioSupported"]
    assert any("different Workflow Profile graph" in warning for warning in resolved["warnings"])
    with pytest.raises(ex.InvalidBindings, match="audio"):
        store.set_continuation_settings(p.project_id, p.active_generation_id, ContinuationSettings(profileId=profile.id, audioEnabled=True))


def test_profile_switch_and_confirmation_api(env, workspace_factory):
    store, _, profile, _, first = env
    choose(store, first); receipt, _ = send(env)
    client = TestClient(app)
    base = f"/api/execution/projects/{first.project_id}/generations/{first.active_generation_id}/continuation"
    response = client.put(base + f"/{receipt.continuation_execution_id}", json={"status": "available", "accept": True})
    assert response.status_code == 200
    assert response.json()["executions"][0]["status"] == "available"
    p = next_generation(env, workspace_factory)
    assert store.visual_handoff(p.project_id, p.active_generation_id).native["consume"]
    response = client.put(base + f"/{receipt.continuation_execution_id}", json={"status": "unavailable"})
    assert response.status_code == 200
    assert not resolve_native(store, p.project_id, p.active_generation_id)["consume"]


def test_foreign_connection_blocks_before_network(env, workspace_factory):
    store, config, profile, _, first = env
    choose(store, first); receipt, _ = send(env); confirm(env, first, receipt)
    p = next_generation(env, workspace_factory); choose(store, p)
    other = config.save("connections", ex.ConnectionWrite(name="Other renderer", baseUrl="http://localhost:9999"))
    with pytest.raises(ex.InvalidBindings, match="another ComfyUI Connection"):
        send((store, config, profile, other, first), p)


def test_prompt_semantics_have_no_latent_media_labels(env, workspace_factory):
    from miniconstruct.generation import resolve_narrative_request
    from miniconstruct.models.api import GenerationRequest
    store, _, _, _, first = env
    choose(store, first); receipt, _ = send(env); confirm(env, first, receipt)
    p = next_generation(env, workspace_factory)
    request = resolve_narrative_request(GenerationRequest(workspace=p.workspace,
        filesystemProjectId=p.project_id, generationId=p.active_generation_id,
        llm={"baseUrl": "http://never.test", "model": "offline"}), store)
    assert request._visual_handoff.asset is None
    from miniconstruct.h3.continuity import continuity_layers
    text = str(continuity_layers(request._continuity_context))
    assert "Native H3 latent context is execution state" in text
    assert "<Video 1>" not in text and "<Picture 1>" not in text


def test_auto_native_handoff_needs_no_selected_take_or_missing_take_warning(env, workspace_factory, image_asset_factory):
    store, config, profile, connection, _ = env
    first = store.create_project(workspace_factory(mode="Ref2VA", assets=[image_asset_factory()]))
    store.set_continuation_settings(first.project_id, first.active_generation_id, ContinuationSettings(profileId=profile.id))
    env = store, config, profile, connection, first
    choose(store, first, valid_ref_prompt()); receipt, _ = send(env); confirm(env, first, receipt)
    p = next_generation(env, workspace_factory, mode="Ref2VA", assets=[image_asset_factory()])
    assert first.outputs["selectedTakeId"] is None
    handoff = store.visual_handoff(p.project_id, p.active_generation_id, p.workspace, strict=False)
    assert handoff.status == "native_available"
    assert handoff.native["consume"] is True
    assert handoff.warnings == []
    from miniconstruct.generation import resolve_narrative_request
    from miniconstruct.models.api import GenerationRequest
    request = resolve_narrative_request(GenerationRequest(workspace=p.workspace,
        filesystemProjectId=p.project_id, generationId=p.active_generation_id,
        llm={"baseUrl": "http://never.test", "model": "offline"}), store)
    assert not any("No previous Selected Take" in warning for warning in request._continuity_context.warnings)
    store.save_project(p.project_id, p.workspace, planning={"visualHandoff": {"strategy": "endpoint_frame"}})
    with pytest.raises(ProjectStoreError, match="no available managed Take for endpoint handoff"):
        store.visual_handoff(p.project_id, p.active_generation_id, p.workspace)
    store.save_project(p.project_id, p.workspace, planning={"visualHandoff": {"strategy": "auto"}})
    store.set_continuation_settings(p.project_id, p.active_generation_id, ContinuationSettings())
    fallback = store.visual_handoff(p.project_id, p.active_generation_id, p.workspace, strict=False)
    assert fallback.status == "no_selected_take"
    assert any("No previous Selected Take" in warning for warning in fallback.warnings)


@pytest.mark.asyncio
async def test_story_created_g2_consumes_accepted_native_g1_without_selected_take(
    env, workspace_factory, image_asset_factory,
):
    store, config, profile, connection, _ = env
    image = image_asset_factory(role="subject_identity")
    first = store.create_project(workspace_factory(mode="Ref2VA", assets=[image],
        subjects=[{"id": "subject-1", "number": 1}], nextSubjectNumber=2),
        story_idea="A courier continues through the station.")
    store.set_continuation_settings(first.project_id, first.active_generation_id,
                                    ContinuationSettings(profileId=profile.id))
    chain = store, config, profile, connection, first
    choose(store, first, valid_ref_prompt())
    receipt, _ = send(chain)
    confirm(chain, first, receipt)
    assert first.outputs["selectedTakeId"] is None

    def continue_shot(result, _context):
        result["items"][1]["relationshipToPrevious"] = "continue_previous_shot"
        result["items"][1]["participatingSubjectIds"] = ["subject-1"]
        result["items"][1]["scenePlan"] = "The courier continues across the station concourse."
        result["items"][1]["dialogue"] = ""

    draft = await build_initial_range(store, first.project_id,
        StoryInitialRangeRequest(requestedTotalCount=2, llm=STORY_LLM),
        OfflineInitialRangeClient(continue_shot))
    store.apply_story_initial_range(first.project_id, draft)
    g2 = store.read_story_snapshot(first.project_id).generations[1]
    handoff = store.visual_handoff(first.project_id, g2.generation_id, g2.workspace, strict=False)
    assert handoff.status == "native_available" and handoff.native["consume"]
    assert handoff.native["contextFrames"] == profile.native_continuation.context_grid.resolve(profile.native_continuation.context_frames)
    assert not any("No previous Selected Take" in warning for warning in handoff.warnings)
    status = store.adopt_manual_edit_execution_prompt(first.project_id, g2.generation_id,
        ManualEditAdoptionWrite(prompt=valid_ref_prompt(), authoringPath="story"))
    assert not status.stale
    prepared = prepare_workflow(store, first.project_id, g2.generation_id, profile)
    assert prepared.native_continuation.consume
    assert prepared.native_continuation.source_id == receipt.continuation_execution_id
    assert prepared.graph["h3"]["inputs"]["prompt"] == valid_ref_prompt()
    assert not any("No previous Selected Take" in warning for warning in prepared.warnings)


def test_profile_choice_changes_only_native_mechanics_without_staling_prompt(env, workspace_factory):
    store, _, profile, _, first = env
    choose(store, first)
    receipt, _ = send(env)
    confirm(env, first, receipt)
    second = next_generation(env, workspace_factory)
    store.set_continuation_settings(second.project_id, second.active_generation_id, ContinuationSettings())
    choose(store, second)
    before = store.execution_snapshot(second.project_id, second.active_generation_id)[1]
    assert not before.stale
    prepared = prepare_workflow(store, second.project_id, second.active_generation_id, profile)
    assert prepared.native_continuation.consume
    store.set_continuation_settings(second.project_id, second.active_generation_id,
                                    ContinuationSettings(profileId=profile.id, contextFrames=26))
    after = store.execution_snapshot(second.project_id, second.active_generation_id)[1]
    assert not after.stale and after.current_fingerprint == before.current_fingerprint
    assert prepare_workflow(store, second.project_id, second.active_generation_id, profile).native_continuation.consume


def test_save_as_does_not_share_chain_artifacts(env):
    store, _, _, _, first = env
    choose(store, first); receipt, _ = send(env); confirm(env, first, receipt)
    copy = store.duplicate_project(first.project_id, "Independent copy")
    assert copy.project_id != first.project_id
    assert store.continuation_status(copy.project_id, copy.active_generation_id)["executions"] == []


@pytest.mark.parametrize("allow", [False, True])
def test_endpoint_conflict_requires_profile_opt_in(env, workspace_factory, image_asset_factory, allow):
    store, config, _, connection, _ = env
    draft = native_draft(); draft["nativeContinuation"]["allowEndpointAnchor"] = allow
    profile = config.save("profiles", ex.ProfileCreate(**draft))
    first = store.create_project(workspace_factory(mode="Ref2VA", assets=[image_asset_factory(role="subject_identity")]))
    store.set_continuation_settings(first.project_id, first.active_generation_id, ContinuationSettings(profileId=profile.id))
    env = store, config, profile, connection, first
    choose(store, first, valid_ref_prompt()); receipt, _ = send(env); confirm(env, first, receipt)
    p = next_generation(env, workspace_factory, strategy="native_h3_continuation", mode="Ref2VA",
                        assets=[image_asset_factory(role="first_frame_anchor")])
    if not allow:
        with pytest.raises(ProjectStoreError, match="first-frame anchor"):
            prepare_workflow(store, p.project_id, p.active_generation_id, profile)
    else:
        choose(store, p, valid_ref_prompt().replace("[reference generation]", "[keyframe completion]"))
        prepared = prepare_workflow(store, p.project_id, p.active_generation_id, profile)
        assert prepared.native_continuation.consume
        assert len(prepared.asset_requirements) == 1


def test_profile_without_capability_keeps_legacy_textual_preparation(env, workspace_factory):
    store, config, _, _, _ = env
    profile = config.save("profiles", capacity_profile())
    p = next_generation(env, workspace_factory)
    store.set_continuation_settings(p.project_id, p.active_generation_id, ContinuationSettings())
    choose(store, p)
    prepared = prepare_workflow(store, p.project_id, p.active_generation_id, profile)
    assert prepared.native_continuation is None and not prepared.asset_requirements
    assert any("No previous Selected Take" in w for w in prepared.warnings)


async def complete_native(env, project, receipt):
    """Use renderer reconciliation, which establishes availability, never trust."""
    from miniconstruct.render_reconciliation import reconcile_submission
    def handle(request):
        assert request.url.path == f"/api/jobs/{receipt.prompt_id}"
        return httpx.Response(200, json={"id": str(receipt.prompt_id), "status": "completed", "outputs": {}})
    await reconcile_submission(project.project_id, project.active_generation_id, receipt.render_submission_id,
        store=env[0], config=env[1], transport=httpx.MockTransport(handle))


async def import_native_take(store, project, receipt, data):
    async def chunks():
        yield data
    outputs = await store.import_take(project.project_id, project.active_generation_id, chunks(), "native.mp4",
        TakeImport(sourceKind="comfyui_submission", renderSubmissionId=receipt.render_submission_id))
    return outputs["takes"][-1]["id"]


async def endpoint_case(env, workspace_factory, image_asset_factory, tmp_path, *, strategy="endpoint_then_immediate_cut"):
    store, _, _, _, first = env
    choose(store, first)
    receipt, _ = send(env)
    await complete_native(env, first, receipt)
    confirm(env, first, receipt)
    take = await import_native_take(store, first, receipt, video(tmp_path / "endpoint.mp4", frames=3, audio=False))
    store.update_render_job(first.project_id, first.active_generation_id, receipt.render_submission_id,
        RendererJobState(status="completed", outputs=[{"id": "a" * 64, "filename": "Generation_00271_.mp4",
            "subfolder": "", "type": "output", "importedTakeId": take}]))
    # H3 endpoint authoring must work with no native-capable Profile at all.
    store.set_continuation_settings(first.project_id, first.active_generation_id, ContinuationSettings())
    second = store.create_generation(first.project_id, workspace=workspace_factory(
        mode="Ref2VA", assets=[image_asset_factory(role="environment")]))
    second = store.save_project(second.project_id, second.workspace, incoming_state="The person remains at the doorway.",
        planning={"relationshipToPrevious": "new_shot_preserve_continuity", "visualHandoff": {"strategy": strategy}})
    return first, second, receipt, take


@pytest.mark.parametrize("strategy", ["endpoint_then_immediate_cut", "endpoint_frame", "auto"])
async def test_accepted_render_endpoint_without_selection_or_native_profile(
        env, workspace_factory, image_asset_factory, tmp_path, strategy):
    from miniconstruct.generation import resolve_narrative_request_async
    store = env[0]
    first, second, receipt, take = await endpoint_case(env, workspace_factory, image_asset_factory, tmp_path, strategy=strategy)
    directory = store._project_directory(first.project_id, must_exist=True)
    before = (directory / "project.json").read_bytes()
    request = GenerationRequest(workspace=second.workspace, filesystemProjectId=second.project_id,
                                generationId=second.active_generation_id, llm={"baseUrl": "http://never.test", "model": "offline"})
    # Fresh requests model Generate and Regenerate; async owners share finalization.
    for resolved in [resolve_narrative_request(request, store), await resolve_narrative_request_async(request, store)]:
        handoff = resolved._visual_handoff
        assert handoff.policy.take_id == take
        assert handoff.policy.strategy == ("endpoint_then_immediate_cut" if strategy == "auto" else strategy)
        assert handoff.summary()["sourceAuthority"] == "accepted_render"
        assert handoff.identity["renderSubmissionId"] == str(receipt.render_submission_id)
        assert handoff.identity["nativeAttemptId"] == str(receipt.continuation_execution_id)
        assert not handoff.native["consume"] and not handoff.native["supported"]
        assert resolved.continuity_context.predecessor_observation is None
        assert resolved.continuity_context.incoming_state == "The person remains at the doorway."
        assert len(resolved.effective_workspace.assets) == len(second.workspace.assets) + 1
    assert (directory / "project.json").read_bytes() == before
    assert store.generation_outputs(first.project_id, first.active_generation_id)["selectedTakeId"] is None


@pytest.mark.parametrize("failure", ["no_take", "missing", "multiple", "unaccepted", "revoked", "unavailable", "wrong_project", "wrong_generation", "wrong_submission"])
async def test_endpoint_fallback_rejects_missing_ambiguous_or_untrusted_sources(
        env, workspace_factory, image_asset_factory, tmp_path, failure):
    store = env[0]
    first, second, receipt, take = await endpoint_case(env, workspace_factory, image_asset_factory, tmp_path)
    directory = store._project_directory(first.project_id, must_exist=True)
    if failure == "no_take":
        store.delete_take(first.project_id, first.active_generation_id, take)
    elif failure == "missing":
        _, _, _, _, generation = store._output_context(first.project_id, first.active_generation_id)
        store._output_path(directory, store._take(generation, take).managed_media_path).unlink()
    elif failure == "multiple":
        await import_native_take(store, first, receipt, video(tmp_path / "other.mp4", frames=5, audio=False))
    elif failure in {"revoked", "unavailable"}:
        store.decide_continuation(first.project_id, first.active_generation_id, receipt.continuation_execution_id,
            ContinuationDecision(status="available" if failure == "revoked" else "unavailable", accept=False))
    else:
        state = store._read_execution_state(directory)
        record = state.continuation_executions[0]
        updates = {"confirmed": False} if failure == "unaccepted" else {
            {"wrong_project": "project_id", "wrong_generation": "generation_id", "wrong_submission": "render_submission_id"}[failure]: uuid4()}
        state.continuation_executions[0] = record.model_copy(update=updates)
        store._write_execution_state(directory, state)
    message = "multiple available Takes" if failure == "multiple" else (
        "no available managed Take" if failure in {"no_take", "missing"} else "Endpoint handoff needs")
    with pytest.raises(ProjectStoreError, match=message):
        store.visual_handoff(second.project_id, second.active_generation_id, materialize=True)
    # A manual external Take must never be guessed as the accepted output.
    async def chunks(): yield video(tmp_path / "manual.mp4", frames=7, audio=False)
    imported = await store.import_take(first.project_id, first.active_generation_id, chunks(), "manual.mp4",
                                       TakeImport(sourceKind="external_unknown"))
    with pytest.raises(ProjectStoreError, match=message):
        store.visual_handoff(second.project_id, second.active_generation_id)
    manual = imported["takes"][-1]["id"]
    store.select_take(first.project_id, first.active_generation_id, manual)
    assert store.visual_handoff(second.project_id, second.active_generation_id).policy.take_id == manual


async def test_endpoint_default_authority_and_selected_take_override(env, workspace_factory, image_asset_factory, tmp_path):
    store = env[0]
    first, second, receipt, take = await endpoint_case(env, workspace_factory, image_asset_factory, tmp_path)
    # Another confirmed execution does not substitute for the default pointer.
    store.activate_generation(first.project_id, first.active_generation_id)
    store.set_continuation_settings(first.project_id, first.active_generation_id, ContinuationSettings(profileId=env[2].id))
    choose(store, first)
    other, _ = send(env)
    confirm(env, first, other)
    other_take = await import_native_take(store, first, other, video(tmp_path / "other.mp4", frames=5, audio=False))
    confirm(env, first, receipt)
    assert store.visual_handoff(second.project_id, second.active_generation_id).policy.take_id == take
    store.select_take(first.project_id, first.active_generation_id, other_take)
    handoff = store.visual_handoff(second.project_id, second.active_generation_id)
    assert handoff.policy.take_id == other_take and handoff.summary()["sourceAuthority"] == "selected_take"
    # Explicit missing selection retains recovery semantics; accepted Take cannot replace it.
    _, _, _, _, generation = store._output_context(first.project_id, first.active_generation_id)
    store._output_path(store._project_directory(first.project_id, must_exist=True), store._take(generation, other_take).managed_media_path).unlink()
    with pytest.raises(ProjectStoreError, match="Previous Selected Take media is missing"):
        store.visual_handoff(second.project_id, second.active_generation_id)


async def test_duplicate_endpoint_resolves_predecessor_without_inheriting_outputs(env, workspace_factory, image_asset_factory, tmp_path):
    store = env[0]
    first, _, _, take = await endpoint_case(env, workspace_factory, image_asset_factory, tmp_path)
    store.activate_generation(first.project_id, first.active_generation_id)
    store.save_project(first.project_id, first.workspace,
        planning={"relationshipToPrevious": "new_shot_preserve_continuity", "visualHandoff": {"strategy": "endpoint_then_immediate_cut"}})
    duplicate = store.duplicate_generation(first.project_id, first.active_generation_id)
    outputs = store.generation_outputs(first.project_id, duplicate.active_generation_id)
    assert outputs["selectedTakeId"] is None and outputs["takes"] == [] and outputs["renderSubmissions"] == []
    assert store.continuation_status(first.project_id, duplicate.active_generation_id)["executions"] == []
    handoff = store.visual_handoff(first.project_id, duplicate.active_generation_id, duplicate.workspace.model_copy(update={"mode": H3Mode.REF2VA}), strict=False)
    assert handoff.policy.take_id == take


async def test_derived_endpoint_revalidates_acceptance_after_extraction(
        env, workspace_factory, image_asset_factory, tmp_path, monkeypatch):
    store = env[0]
    first, second, receipt, _ = await endpoint_case(env, workspace_factory, image_asset_factory, tmp_path)
    original = store._handoff_artifact
    def revoke_after_extraction(*args):
        result = original(*args)
        store.decide_continuation(first.project_id, first.active_generation_id, receipt.continuation_execution_id,
            ContinuationDecision(status="available", accept=False))
        return result
    monkeypatch.setattr(store, "_handoff_artifact", revoke_after_extraction)
    with pytest.raises(ProjectStoreError, match="Visual handoff unavailable"):
        await store.visual_handoff_async(second.project_id, second.active_generation_id, materialize=True)


async def test_completed_selected_take_cannot_confirm_native_state(env, workspace_factory, tmp_path):
    store, _, profile, _, first = env
    choose(store, first)
    receipt, _ = send(env)
    await complete_native(env, first, receipt)
    status = store.continuation_status(first.project_id, first.active_generation_id)
    assert status["executions"][0].status == "available" and status["acceptedId"] is None
    take = await import_native_take(store, first, receipt, video(tmp_path / "audit.mp4", frames=3, audio=False))
    store.select_take(first.project_id, first.active_generation_id, take)
    second = next_generation(env, workspace_factory)
    resolved = resolve_native(store, second.project_id, second.active_generation_id)
    assert not resolved["consume"] and not resolved["available"]
    assert "Accept native state" in resolved["reason"]
    assert store.visual_handoff(second.project_id, second.active_generation_id).status == "textual_fallback"
    choose(store, second)
    assert not prepare_workflow(store, second.project_id, second.active_generation_id, profile).native_continuation.consume
    fallback, calls = send(env, second)
    assert json.loads(calls[-1].content)["prompt"]["native"]["inputs"]["enable"] is False
    assert fallback.continuation_execution_id is not None
    store.save_project(second.project_id, second.workspace,
        planning={"visualHandoff": {"strategy": "native_h3_continuation"}})
    with pytest.raises(ProjectStoreError, match="Accept native state"):
        prepare_workflow(store, second.project_id, second.active_generation_id, profile)
    with pytest.raises(ProjectStoreError, match="Accept native state"):
        send(env, second)


@pytest.mark.parametrize("confirmed_a", [False, True])
async def test_selected_take_requires_its_own_confirmation_over_default(env, workspace_factory, tmp_path, confirmed_a):
    store, _, profile, _, first = env
    choose(store, first)
    a, _ = send(env)
    await complete_native(env, first, a)
    if confirmed_a:
        confirm(env, first, a)
    b, _ = send(env)
    await complete_native(env, first, b)
    confirm(env, first, b)
    second = next_generation(env, workspace_factory)
    choose(store, second)
    default = prepare_workflow(store, second.project_id, second.active_generation_id, profile)
    assert default.native_continuation.source_id == b.continuation_execution_id
    data = video(tmp_path / "lineage.mp4", frames=3, audio=False)
    take_a = await import_native_take(store, first, a, data)
    store.select_take(first.project_id, first.active_generation_id, take_a)
    resolution = resolve_native(store, second.project_id, second.active_generation_id)
    assert resolution["consume"] is confirmed_a
    assert (resolution["record"].id if confirmed_a else None) == (a.continuation_execution_id if confirmed_a else None)
    status = store.continuation_status(first.project_id, first.active_generation_id)
    assert status["acceptedId"] == b.continuation_execution_id
    assert {r.id: r.confirmed for r in status["executions"]} == {
        a.continuation_execution_id: confirmed_a, b.continuation_execution_id: True}
    if confirmed_a:
        prepared = prepare_workflow(store, second.project_id, second.active_generation_id, profile)
        # Changing only the default cannot invalidate the selected, confirmed source.
        confirm(env, first, a)
        assert prepare_workflow(store, second.project_id, second.active_generation_id, profile).execution_fingerprint == prepared.execution_fingerprint
        client = TestClient(app)
        base = f"/api/execution/projects/{first.project_id}/generations/{first.active_generation_id}/continuation"
        assert client.delete(f"{base}/{b.continuation_execution_id}").status_code == 422
        assert client.put(f"{base}/{b.continuation_execution_id}", json={"status": "available", "accept": False}).status_code == 200
        assert resolve_native(store, second.project_id, second.active_generation_id)["consume"]
        revoked = client.put(f"{base}/{a.continuation_execution_id}", json={"status": "available", "accept": False})
        assert revoked.status_code == 200 and revoked.json()["acceptedId"] is None
        assert not resolve_native(store, second.project_id, second.active_generation_id)["consume"]
        after = prepare_workflow(store, second.project_id, second.active_generation_id, profile)
        assert after.execution_fingerprint != prepared.execution_fingerprint
        # Native trust is outside ordinary H3 identity when the fallback is textual.
        assert after.current_fingerprint == prepared.current_fingerprint
        calls = []
        with pytest.raises(ex.InvalidBindings, match="stale"):
            send_generation(ExecutionSendRequest(projectId=second.project_id, generationId=second.active_generation_id,
                profileId=profile.id, connectionId=env[3].id, expectedExecutionFingerprint=prepared.execution_fingerprint),
                store=store, config=env[1], transport=recorder(calls))
        assert calls == []
        assert client.delete(f"{base}/{a.continuation_execution_id}").status_code == 200
        store.select_take(first.project_id, first.active_generation_id, take_a)
        assert resolve_native(store, second.project_id, second.active_generation_id)["record"] is None
    store.select_take(first.project_id, first.active_generation_id, None)
    if not confirmed_a:
        assert prepare_workflow(store, second.project_id, second.active_generation_id, profile).execution_fingerprint == default.execution_fingerprint


async def test_available_reroll_does_not_replace_default_or_inherit_confirmation(env, workspace_factory, tmp_path):
    store, _, _, _, first = env
    choose(store, first)
    a, _ = send(env); confirm(env, first, a)
    b, _ = send(env)
    await complete_native(env, first, b)
    second = next_generation(env, workspace_factory)
    assert resolve_native(store, second.project_id, second.active_generation_id)["record"].id == a.continuation_execution_id
    take_b = await import_native_take(store, first, b, video(tmp_path / "reroll.mp4", frames=3, audio=False))
    store.select_take(first.project_id, first.active_generation_id, take_b)
    assert not resolve_native(store, second.project_id, second.active_generation_id)["consume"]
    assert store.continuation_status(first.project_id, first.active_generation_id)["acceptedId"] == a.continuation_execution_id
    assert store.visual_handoff(second.project_id, second.active_generation_id).status == "textual_fallback"


@pytest.mark.parametrize("status", ["available", "unavailable"])
async def test_revoking_default_keeps_other_confirmation_without_choosing_replacement(env, workspace_factory, tmp_path, status):
    store, _, _, _, first = env
    choose(store, first)
    a, _ = send(env); confirm(env, first, a)
    b, _ = send(env); confirm(env, first, b)
    second = next_generation(env, workspace_factory)
    store.decide_continuation(first.project_id, first.active_generation_id, b.continuation_execution_id,
        ContinuationDecision(status=status, accept=False))
    current = store.continuation_status(first.project_id, first.active_generation_id)
    assert current["acceptedId"] is None
    assert {r.id: r.confirmed for r in current["executions"]} == {a.continuation_execution_id: True, b.continuation_execution_id: False}
    assert not resolve_native(store, second.project_id, second.active_generation_id)["consume"]
    data = video(tmp_path / "confirmed.mp4", frames=3, audio=False)
    take_a = await import_native_take(store, first, a, data)
    take_b = await import_native_take(store, first, b, data)
    store.select_take(first.project_id, first.active_generation_id, take_a)
    assert resolve_native(store, second.project_id, second.active_generation_id)["record"].id == a.continuation_execution_id
    store.select_take(first.project_id, first.active_generation_id, take_b)
    assert not resolve_native(store, second.project_id, second.active_generation_id)["consume"]


@pytest.mark.parametrize("kind", ["external_unknown", "selected_prompt"])
async def test_take_without_exact_submission_never_uses_confirmed_native(env, workspace_factory, tmp_path, kind):
    store, _, _, _, first = env
    selected = choose(store, first).selected
    receipt, _ = send(env); confirm(env, first, receipt)
    data = video(tmp_path / "unknown.mp4", frames=3, audio=False)
    async def chunks(): yield data
    provenance = TakeImport(sourceKind=kind, **({"selectedPromptId": selected.id} if kind == "selected_prompt" else {}))
    imported = await store.import_take(first.project_id, first.active_generation_id, chunks(), "unknown.mp4", provenance)
    store.select_take(first.project_id, first.active_generation_id, imported["takes"][-1]["id"])
    second = next_generation(env, workspace_factory)
    resolved = resolve_native(store, second.project_id, second.active_generation_id)
    assert not resolved["consume"] and "external/unknown" in resolved["reason"]


async def test_unconfirmed_native_uses_existing_ref2va_tail_fallback(env, workspace_factory, image_asset_factory, tmp_path):
    store, config, _, connection, _ = env
    profile = config.save("profiles", ex.ProfileCreate(**native_draft(av=True)))
    first = store.create_project(workspace_factory(mode="Ref2VA", assets=[image_asset_factory(role="subject_identity")]))
    store.set_continuation_settings(first.project_id, first.active_generation_id, ContinuationSettings(profileId=profile.id))
    chain = store, config, profile, connection, first
    choose(store, first, valid_ref_prompt())
    receipt, _ = send(chain)
    await complete_native(chain, first, receipt)
    take = await import_native_take(store, first, receipt, video(tmp_path / "tail.mp4"))
    store.select_take(first.project_id, first.active_generation_id, take)
    second = next_generation(chain, workspace_factory, mode="Ref2VA", assets=[image_asset_factory(role="subject_identity")])
    handoff = store.visual_handoff(second.project_id, second.active_generation_id, materialize=True)
    assert not handoff.native["consume"] and handoff.policy.strategy == "tail_reference_continuation"
    choose(store, second, valid_ref_prompt().replace("[reference generation]", "[reference generation + video continuation]"))
    prepared = prepare_workflow(store, second.project_id, second.active_generation_id, profile)
    assert not prepared.native_continuation.consume and any(r.asset_kind == "video" for r in prepared.asset_requirements)


@pytest.mark.parametrize("strategy", ["auto", "native_h3_continuation"])
@pytest.mark.parametrize("phase", ["upload", "verification"])
def test_revoke_during_send_upload_prevents_queue(env, workspace_factory, image_asset_factory, strategy, phase, monkeypatch):
    store, config, profile, connection, _ = env
    first = store.create_project(workspace_factory(mode="Ref2VA", assets=[image_asset_factory()]))
    store.set_continuation_settings(first.project_id, first.active_generation_id, ContinuationSettings(profileId=profile.id))
    chain = store, config, profile, connection, first
    choose(store, first, valid_ref_prompt()); receipt, _ = send(chain); confirm(chain, first, receipt)
    second = next_generation(chain, workspace_factory, strategy=strategy, mode="Ref2VA", assets=[image_asset_factory()])
    choose(store, second, valid_ref_prompt())
    prepared = prepare_workflow(store, second.project_id, second.active_generation_id, profile)
    assert prepared.native_continuation.consume
    def revoke(request=None, calls=None):
        if request is None or request.url.path.endswith("/upload/image"):
            store.decide_continuation(first.project_id, first.active_generation_id, receipt.continuation_execution_id,
                ContinuationDecision(status="available", accept=False))
    if phase == "verification":
        import hashlib
        original = hashlib.file_digest
        def digest(*args, **kwargs):
            result = original(*args, **kwargs)
            revoke()
            return result
        monkeypatch.setattr(hashlib, "file_digest", digest)
    calls = []
    with pytest.raises((ex.InvalidBindings, ProjectStoreError), match="Execution changed|Confirm|confirmed"):
        send_generation(ExecutionSendRequest(projectId=second.project_id, generationId=second.active_generation_id,
            profileId=profile.id, connectionId=connection.id, expectedExecutionFingerprint=prepared.execution_fingerprint),
            store=store, config=config, transport=recorder(calls, revoke if phase == "upload" else None))
    assert calls and all(request.url.path.endswith(("/upload/image", "/view")) for request in calls)


def test_revoked_native_authority_during_remote_reuse_never_queues(env, workspace_factory, image_asset_factory):
    from test_staged_media import Remote
    remote = Remote()
    store, config, profile, connection, _ = env
    first = store.create_project(workspace_factory(mode="Ref2VA", assets=[image_asset_factory()]))
    store.set_continuation_settings(first.project_id, first.active_generation_id, ContinuationSettings(profileId=profile.id))
    chain = store, config, profile, connection, first
    choose(store, first, valid_ref_prompt())
    def submit(project):
        prepared = prepare_workflow(store, project.project_id, project.active_generation_id, profile)
        return send_generation(ExecutionSendRequest(projectId=project.project_id, generationId=project.active_generation_id,
            profileId=profile.id, connectionId=connection.id, expectedExecutionFingerprint=prepared.execution_fingerprint),
            store=store, config=config, transport=httpx.MockTransport(remote.handle))
    receipt = submit(first)
    confirm(chain, first, receipt)
    second = next_generation(chain, workspace_factory, mode="Ref2VA", assets=[image_asset_factory()])
    choose(store, second, valid_ref_prompt())
    submit(second)
    before = remote.counts()
    def revoke(request):
        if request.url.path.endswith('/view'):
            store.decide_continuation(first.project_id, first.active_generation_id, receipt.continuation_execution_id,
                                     ContinuationDecision(status="available", accept=False))
    remote.before = revoke
    with pytest.raises((ex.InvalidBindings, ProjectStoreError), match="Execution changed|Confirm|confirmed"):
        submit(second)
    assert remote.counts()[0] == before[0]
    assert remote.counts()[1] > before[1]
    assert remote.counts()[2] == before[2]


@pytest.mark.parametrize("version", [1, 2, 3, 4])
def test_old_sidecar_confirmation_uses_only_persisted_accepted_pointer_without_rewrite(env, workspace_factory, version):
    store, _, _, _, first = env
    choose(store, first)
    a, _ = send(env); confirm(env, first, a)
    b, _ = send(env); confirm(env, first, b)
    second = next_generation(env, workspace_factory)
    path = store._project_directory(first.project_id, must_exist=True) / "execution-state.json"
    raw = json.loads(path.read_bytes()); raw["schemaVersion"] = version
    for record in raw["continuationExecutions"]:
        record.pop("confirmed")
    path.write_text(json.dumps(raw), encoding="utf-8")
    before = path.read_bytes()
    status = store.continuation_status(first.project_id, first.active_generation_id)
    assert {r.id: r.confirmed for r in status["executions"]} == {a.continuation_execution_id: False, b.continuation_execution_id: True}
    assert resolve_native(store, second.project_id, second.active_generation_id)["record"].id == b.continuation_execution_id
    assert path.read_bytes() == before
    # Explicit false is authoritative even if an inconsistent pointer survives.
    raw["continuationExecutions"][1]["confirmed"] = False
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert resolve_native(store, second.project_id, second.active_generation_id)["record"] is None
    store.decide_continuation(first.project_id, first.active_generation_id, b.continuation_execution_id,
        ContinuationDecision(status="available", accept=False))
    canonical = json.loads(path.read_bytes())
    assert canonical["schemaVersion"] == 4 and not canonical["acceptedContinuations"]
    assert all(record["confirmed"] is False for record in canonical["continuationExecutions"])


@pytest.mark.parametrize("cleanup", ["full", "fresh", "clear", "delete"])
def test_confirmation_is_owned_by_attempt_and_cleared_with_execution_chain(env, workspace_factory, cleanup):
    store, _, _, _, first = env
    choose(store, first)
    a, _ = send(env); confirm(env, first, a)
    b, _ = send(env); confirm(env, first, b)
    second = next_generation(env, workspace_factory)
    if cleanup in {"full", "fresh"}:
        destination = store.duplicate_project(first.project_id, "Copy", copy_mode=cleanup)
        directory = store._project_directory(destination.project_id, must_exist=True)
        assert all(r.confirmed for r in store.continuation_status(first.project_id, first.active_generation_id)["executions"])
    else:
        if cleanup == "clear":
            current = store.load_project(first.project_id)
            store.clear_generated_state(first.project_id, current.project_fingerprint)
        else:
            store.activate_generation(first.project_id, first.active_generation_id)
            store.delete_generation(first.project_id, first.active_generation_id)
        directory = store._project_directory(first.project_id, must_exist=True)
    state = store._read_execution_state(directory)
    assert not state.continuation_executions and not state.accepted_continuations
    if cleanup == "clear":
        assert not resolve_native(store, second.project_id, second.active_generation_id)["consume"]


def test_initial_native_save_and_artifact_lifecycle_are_information(env):
    store, _, profile, _, first = env
    choose(store, first)
    result = prepare_workflow(store, first.project_id, first.active_generation_id, profile)
    assert result.native_continuation and not result.native_continuation.consume
    codes = {d.code: d for d in result.diagnostics}
    assert codes["native.save_only"].severity == "info"
    assert codes["native.artifact_lifecycle"].severity == "info"
    assert codes["preparation.offline"].severity == "info"
    assert codes["native.save_only"].message in result.information
    assert not any("save current execution only" in w or "Queue acceptance" in w for w in result.warnings)
