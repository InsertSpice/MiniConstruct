"""Synthetic Hyperflow topology, disposable Projects/config and mocked HTTP only."""
from copy import deepcopy
import base64
import io
import json
import math
from pathlib import Path
import re

from PIL import Image
import pytest

from miniconstruct import execution as ex
from miniconstruct.api.execution_routes import validate_draft
from miniconstruct.comfy_media import picture_free_source_error
from miniconstruct.execution_state import RendererSeedSettings
from miniconstruct.i2va_contract import state_identity
from miniconstruct.models.takes import RendererJobState
from miniconstruct.workflow_preparation import prepare_workflow
from test_i2va_native_continuation import env, choose, send, accept, I2VA_PROMPT
from test_picture_free_i2va import successor


BUNDLE = Path(__file__).resolve().parents[1] / "docs/examples/i2v-hyperflow-native-only"
IDENTITY = "ad445d5c57e64f3b2440efc2ba55e747400594ab0ecd4d161298d91c7e50cec0"


def read(name):
    return json.loads((BUNDLE / name).read_text(encoding="utf-8"))


def create_draft(name):
    return ex.ProfileCreate.model_validate(read(name))


def picture(factory):
    # Synthetic pixels at the public fixture's example dimensions.
    payload = io.BytesIO()
    Image.new("RGB", (1200, 800), (70, 90, 110)).save(payload, format="PNG")
    asset = factory(role="first_frame_anchor")
    return asset.model_copy(update={"image": asset.image.model_copy(update={
        "width": 1200, "height": 800,
        "data_url": "data:image/png;base64," + base64.b64encode(payload.getvalue()).decode(),
    })})


def start(env, workspace_factory, image_asset_factory):
    store, config, _ = env
    original = ex.Profile.model_validate(read("initial-profile-fixture.json"))
    first = store.create_project(workspace_factory(
        mode="I2VA", durationSeconds=12, assets=[picture(image_asset_factory)]))
    choose(env, first, original, I2VA_PROMPT)
    # Optional Picture metadata is blocked; no validator exception is introduced.
    with pytest.raises(ex.InvalidBindings, match="cannot be safely disabled"):
        prepare_workflow(store, first.project_id, first.active_generation_id, original)
    initial = config.save("profiles", create_draft("initial-profile-create-draft.json"))
    assert initial.workflow == original.workflow
    choose(env, first, initial, I2VA_PROMPT)
    prepared = prepare_workflow(store, first.project_id, first.active_generation_id, initial)
    assert prepared.native_continuation.i2va_state_identity == IDENTITY
    assert not prepared.native_continuation.consume
    assert prepared.graph["55"]["inputs"]["mode"] == "disabled"
    assert "prev_latent" not in prepared.graph["55"]["inputs"]
    assert prepared.graph["35"]["inputs"]["value"] == 12
    calls = []
    receipt = send(env, first, initial, calls, prepared.execution_fingerprint)
    assert [c.url.path for c in calls] == ["/upload/image", "/prompt"]
    store.update_render_job(first.project_id, first.active_generation_id,
                            receipt.render_submission_id, RendererJobState(status="completed"))
    accept(env, first, receipt)
    only = config.save("profiles", create_draft("successor-profile-create.json"))
    second = successor(env, first, workspace_factory, only)
    return first, initial, receipt, only, second


def test_public_bundle_typed_api_validation_and_precise_adaptation(env):
    original = ex.Profile.model_validate(read("initial-profile-fixture.json"))
    preview = ex.Profile.model_validate(read("successor-profile-draft.json"))
    draft = create_draft("successor-profile-create.json")
    assert validate_draft(draft)["valid"]
    assert preview.id != original.id and preview.name != original.name
    graph = read("successor-api-workflow.json")
    assert graph == preview.workflow == json.loads(draft.workflow_json)
    assert env[1].save("profiles", draft).workflow == graph
    assert not ex.native_mapping_errors(graph, draft.bindings, draft.native_continuation)
    assert state_identity(original.workflow, original.native_continuation) == IDENTITY
    assert state_identity(graph, draft.native_continuation) == IDENTITY
    assert draft.exposed_controls == original.exposed_controls
    assert draft.workflow_control_presets == original.workflow_control_presets
    assert {b.source.kind for b in draft.bindings} == {"generated_prompt", "duration", "renderer_seed"}
    assert set(original.workflow) - set(graph) == {"50", "51", "52"}
    changed = {n for n in graph if graph[n] != original.workflow[n]}
    assert changed == {"6", "15", "16", "55", "56", "59"}
    assert all(picture_free_source_error(n) is None for n in graph.values())
    assert not any("first_frame" in n["inputs"] for n in graph.values())
    assert (graph["6"]["inputs"]["width"], graph["6"]["inputs"]["height"]) == (1120, 736)
    scale = math.sqrt(.8 * 1024 * 1024 / (1200 * 800))
    assert [round(axis * scale / 32) * 32 for axis in (1200, 800)] == [1120, 736]
    assert graph["15"]["inputs"]["audio"] == ["57", 1]
    contract = read("fixture-contract.json")
    assert contract["modelCodecStateIdentity"] == IDENTITY
    assert contract["syntheticPicturePixels"] == [1200, 800]
    assert contract["canvas"] == [1120, 736]
    assert contract["kind"] == "synthetic-regression-fixture"
    assert original.workflow["58"]["inputs"]["lora_1"]["lora"] == "example/turbo-8step.safetensors"
    assert {key for key in original.workflow["58"]["inputs"] if key.startswith("lora_")} == {"lora_1", "lora_2"}
    # Committed examples contain no local drive paths, network shares or home paths.
    for name in ("initial-profile-fixture.json", "initial-profile-create-draft.json",
                 "successor-api-workflow.json", "successor-profile-create.json",
                 "successor-profile-draft.json", "fixture-contract.json"):
        text = (BUNDLE / name).read_text(encoding="utf-8")
        assert not re.search(r"[A-Za-z]:[\\/]|\\\\\\\\|/Users/|/home/", text)


def test_synthetic_hyperflow_g1_g2_g3_exact_accepted_lineage(env, workspace_factory, image_asset_factory):
    store, config, _ = env
    first, initial, r1, only, second = start(env, workspace_factory, image_asset_factory)
    source = store.continuation_status(first.project_id, first.active_generation_id)["executions"][0]
    store.set_renderer_seed_settings(second.project_id, second.active_generation_id,
                                    RendererSeedSettings(mode="fixed", fixedSeed=24680))
    # Use the fixture duration through normal authoring/preparation.
    second = store.save_project(second.project_id, second.workspace.model_copy(update={"duration_seconds": 12}))
    choose(env, second, only)
    prepared = prepare_workflow(store, second.project_id, second.active_generation_id, only, inspection=False)
    native, graph = prepared.native_continuation, prepared.graph
    assert native.consume and native.source_id == r1.continuation_execution_id
    assert native.load_key == source.storage_namespace
    assert (native.load_segment_index, native.save_segment_index) == (1, 2)
    assert native.storage_namespace != source.storage_namespace
    assert graph["59"]["inputs"]["latent_path"] == source.storage_namespace
    assert graph["59"]["inputs"]["clip_index"] == 1
    assert graph["56"]["inputs"]["filename_prefix"] == native.storage_namespace + "/clip"
    assert graph["56"]["inputs"]["clip_index"] == 2
    assert graph["55"]["inputs"]["mode"] == "always_extend"
    assert graph["55"]["inputs"]["prev_latent"] == ["59", 0]
    assert prepared.renderer_seed_controlled
    assert prepared.renderer_seed_settings.fixed_seed == 24680
    assert graph["8"]["inputs"]["noise_seed"] == 123456789
    assert graph["35"]["inputs"]["value"] == 12
    assert native.i2va_state_identity == source.i2va_state_identity == IDENTITY
    assert not prepared.asset_requirements and not second.workspace.assets
    assert all(picture_free_source_error(n) is None for n in graph.values())
    calls = []
    r2 = send(env, second, only, calls, prepared.execution_fingerprint)
    assert [c.url.path for c in calls] == ["/prompt"]
    stored = store.continuation_status(second.project_id, second.active_generation_id)["executions"][0]
    expected = deepcopy(graph)
    # Send resolves the reviewed seed authority and creates a fresh Save attempt.
    expected["56"]["inputs"]["filename_prefix"] = stored.save_key
    expected["8"]["inputs"]["noise_seed"] = 24680
    assert json.loads(calls[0].content)["prompt"] == expected
    assert r2.renderer_seed == 24680
    assert stored.source_id == source.id and stored.save_segment_index == 2
    store.update_render_job(second.project_id, second.active_generation_id,
                            r2.render_submission_id, RendererJobState(status="completed"))
    accept(env, second, r2)
    # A newer unaccepted reroll cannot replace the selected accepted attempt.
    send(env, second, only, [])
    third = successor(env, second, workspace_factory, only)
    g3 = prepare_workflow(store, third.project_id, third.active_generation_id, only)
    assert g3.native_continuation.source_id == r2.continuation_execution_id
    assert g3.native_continuation.load_key == stored.storage_namespace
    assert (g3.native_continuation.load_segment_index, g3.native_continuation.save_segment_index) == (2, 3)
    assert g3.native_continuation.save_key != stored.save_key
    assert g3.native_continuation.i2va_state_identity == IDENTITY
    assert not g3.asset_requirements
    assert config.get("profiles", initial.id).workflow == initial.workflow


def test_synthetic_effective_lora_controls_cannot_fake_compatibility(env, workspace_factory, image_asset_factory):
    store, _, _ = env
    _, _, _, only, second = start(env, workspace_factory, image_asset_factory)
    store.set_workflow_control_values(second.project_id, second.active_generation_id, str(only.id),
        "generation", {"enable_turbo_lora": True})
    with pytest.raises(ex.InvalidBindings, match="Effective model/codec controls differ"):
        prepare_workflow(store, second.project_id, second.active_generation_id, only)
    calls = []
    with pytest.raises(ex.InvalidBindings, match="Effective model/codec controls differ"):
        send(env, second, only, calls)
    assert not calls


@pytest.mark.parametrize("dependency", ["model", "video_vae", "audio_vae", "format"])
def test_public_model_codec_and_format_mismatch_rejected_before_transport(
        env, workspace_factory, image_asset_factory, dependency):
    _, config, _ = env
    _, _, _, only, second = start(env, workspace_factory, image_asset_factory)
    data = create_draft("successor-profile-create.json").model_dump(mode="json", by_alias=True)
    graph = json.loads(data["workflowJson"])
    if dependency == "format":
        data["nativeContinuation"]["compatibilityKey"] = "example-incompatible-format-v2"
    else:
        node, key = {"model": ("1", "unet_name"), "video_vae": ("4", "vae_name"),
                     "audio_vae": ("5", "vae_name")}[dependency]
        graph[node]["inputs"][key] = "example-incompatible.safetensors"
    data["workflowJson"] = json.dumps(graph)
    incompatible = config.save("profiles", ex.ProfileCreate.model_validate(data))
    assert state_identity(incompatible.workflow, incompatible.native_continuation) != IDENTITY
    calls = []
    with pytest.raises(ex.InvalidBindings, match="Native-only I2VA unavailable"):
        prepare_workflow(env[0], second.project_id, second.active_generation_id, incompatible)
    with pytest.raises(ex.InvalidBindings, match="Native-only I2VA unavailable"):
        send(env, second, incompatible, calls)
    assert not calls
