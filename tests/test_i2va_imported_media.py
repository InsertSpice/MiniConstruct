"""Reject imported sources through real picture-free Profile validation, offline."""
import json

import pytest
from pydantic import ValidationError

from miniconstruct import execution as ex
from miniconstruct.i2va_contract import state_identity
from miniconstruct.workflow_preparation import prepare_workflow
from test_i2va_native_continuation import env, draft, send
from test_picture_free_i2va import successor_draft, initial, successor


def graph_draft(graph):
    data = successor_draft().model_dump(by_alias=True)
    data["workflowJson"] = json.dumps(graph)
    return ex.ProfileCreate(**data)


def neutral_graph():
    return json.loads(successor_draft().workflow_json)


def reject_profile(env, graph, message):
    data = graph_draft(graph)
    # Both configuration writes and persisted Profile loading use production
    # native_mapping_errors; testing just the leaf helper would miss either seam.
    with pytest.raises(ex.InvalidBindings, match=message):
        env[1].save("profiles", data)
    with pytest.raises(ValidationError, match=message):
        ex.Profile(**data.model_dump(exclude={"workflow_json"}), workflow=graph,
                   fingerprint=ex.workflow_fingerprint(graph))


@pytest.mark.parametrize("class_type,widget,value", [
    ("LoadImage", "image", "stale_picture.png"),
    ("LoadVideo", "file", "stale_previous_video.mp4"),
    ("LoadAudio", "audio", "stale_audio.wav"),
    ("LoadImageMask", "image", "stale_mask.png"),
    ("VHS_LoadVideo", "video", "stale_previous_video.mp4"),
    ("VHS_LoadVideoPath", "video", "stale_previous_video.mp4"),
    ("VHS_LoadVideoFFmpeg", "video", "stale_previous_video.mp4"),
    ("VHS_LoadVideoFFmpegPath", "video", "stale_previous_video.mp4"),
    ("VHS_LoadImagePath", "image", "stale_picture.png"),
    ("VHS_LoadImages", "directory", "previous_images"),
    ("VHS_LoadImagesPath", "directory", "previous_images"),
    ("VHS_LoadAudio", "audio_file", "stale_audio.wav"),
    ("VHS_LoadAudioUpload", "audio", "stale_audio.wav"),
])
def test_disconnected_known_media_loaders_cannot_hide_in_successor_profiles(env, class_type, widget, value):
    graph = neutral_graph()
    graph["70"] = {"class_type": class_type, "inputs": {widget: value}}
    reject_profile(env, graph, f"{class_type} is an imported")


@pytest.mark.parametrize("value", ["", ["35", 0], {"dataUrl": "data:video/mp4;base64,AAAA"}])
def test_loader_classification_is_not_dependent_on_scalar_filename_or_connection(env, value):
    graph = neutral_graph()
    graph["70"] = {"class_type": "LoadVideo", "inputs": {"file": value}}
    reject_profile(env, graph, "LoadVideo is an imported Video loader")


@pytest.mark.parametrize("source", ["video", "audio"])
def test_imported_audio_mux_reproduction_rejected_even_with_unchanged_model_identity(env, source):
    graph = neutral_graph()
    identity = state_identity(graph, successor_draft().native_continuation)
    if source == "video":
        graph["70"] = {"class_type": "LoadVideo", "inputs": {"file": "stale_previous_video.mp4"}}
        graph["71"] = {"class_type": "GetVideoComponents", "inputs": {"video": ["70", 0]}}
        graph["15"]["inputs"]["audio"] = ["71", 1]
    else:
        graph["70"] = {"class_type": "LoadAudio", "inputs": {"audio": "stale_audio.wav"}}
        graph["15"]["inputs"]["audio"] = ["70", 0]
    assert state_identity(graph, successor_draft().native_continuation) == identity
    reject_profile(env, graph, "is an imported (Video|Audio) loader")


@pytest.mark.parametrize("payload", [
    "data:image/png;base64,AAAA", "data:video/mp4;base64,AAAA", "data:audio/wav;base64,AAAA",
    {"mediaPayload": {"sha256": "a" * 64, "byteLength": 4}},
    {"sha256": "a" * 64, "byteLength": 4},
    {"waveform": [[0.0, 0.0]], "sample_rate": 24000},
])
def test_attached_media_constants_are_rejected_without_guessing_input_names(env, payload):
    graph = neutral_graph()
    graph["70"] = {"class_type": "OpaqueConstant", "inputs": {"value": {"nested": payload}}}
    reject_profile(env, graph, "contains an attached media")


@pytest.mark.parametrize("class_type", ["OpaqueLoader", "AcmeVideoImporter", "ReadAudio"])
def test_unrecognized_loading_nodes_fail_as_unclassified_not_verified_media(env, class_type):
    graph = neutral_graph()
    graph["70"] = {"class_type": class_type, "inputs": {"value": "previous_media.bin"}}
    reject_profile(env, graph, "unrecognized loading semantics; unsupported")


def test_generated_audio_video_processing_and_output_muxing_remain_valid(env):
    graph = neutral_graph()
    original_identity = state_identity(graph, successor_draft().native_continuation)
    # Original VAEDecodeAudio → CreateVideo → SaveVideo is already valid.
    profile = env[1].save("profiles", graph_draft(graph))
    assert profile.workflow["14"]["class_type"] == "VAEDecodeAudio"
    assert profile.workflow["15"]["inputs"]["audio"] == ["14", 0]
    assert profile.workflow["16"]["inputs"]["video"] == ["15", 0]
    # Components of a generated video and normal audio connections are not
    # imported media just because their inputs are named video or audio.
    graph["71"] = {"class_type": "GetVideoComponents", "inputs": {"video": ["15", 0]}}
    graph["72"] = {"class_type": "CreateVideo", "inputs": {
        "images": ["71", 0], "audio": ["71", 1], "fps": 24}}
    graph["16"]["inputs"]["video"] = ["72", 0]
    profile = env[1].save("profiles", graph_draft(graph))
    assert state_identity(profile.workflow, profile.native_continuation) == original_identity


@pytest.mark.parametrize("mode", ["I2VA", "Ref2VA"])
def test_extra_media_classification_only_applies_to_explicit_picture_free_capability(env, mode):
    original = draft(modes=[mode])
    graph = json.loads(original.workflow_json)
    graph["70"] = {"class_type": "LoadVideo", "inputs": {"file": "ordinary_reference.mp4"}}
    graph["71"] = {"class_type": "OpaqueLoader", "inputs": {"value": "ordinary_reference.bin"}}
    data = original.model_dump(by_alias=True)
    data["workflowJson"] = json.dumps(graph)
    profile = env[1].save("profiles", ex.ProfileCreate(**data))
    assert profile.bindings[2].presence == "required"
    assert not profile.native_continuation.picture_free_i2va


def test_authorized_native_successor_without_picture_still_prepares_and_sends_and_rechecks_sources(
        env, workspace_factory, image_asset_factory, monkeypatch):
    store, config, _ = env
    first, _, _ = initial(env, workspace_factory, image_asset_factory)
    profile = config.save("profiles", successor_draft())
    second = successor(env, first, workspace_factory, profile)
    prepared = prepare_workflow(store, second.project_id, second.active_generation_id, profile)
    assert prepared.native_continuation.consume and not prepared.asset_requirements
    assert not second.workspace.assets and "first_frame" not in prepared.graph["6"]["inputs"]
    calls = []
    send(env, second, profile, calls, prepared.execution_fingerprint)
    assert [call.url.path for call in calls] == ["/prompt"]
    # Simulate a stale in-memory Profile obtained from a configuration seam;
    # Prepare/Send must re-run graph validation before any renderer transport.
    tampered = profile.model_copy(deep=True)
    tampered.workflow["70"] = {"class_type": "LoadVideo", "inputs": {"file": "stale_previous_video.mp4"}}
    tampered.fingerprint = ex.workflow_fingerprint(tampered.workflow)
    assert state_identity(tampered.workflow, tampered.native_continuation) == prepared.native_continuation.i2va_state_identity
    original_get = config.get
    monkeypatch.setattr(config, "get", lambda collection, key: tampered if collection == "profiles" else original_get(collection, key))
    calls.clear()
    with pytest.raises(ex.InvalidBindings, match="LoadVideo is an imported Video loader"):
        send(env, second, profile, calls)
    assert calls == []
