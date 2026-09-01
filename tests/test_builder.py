from __future__ import annotations

import json

from miniconstruct.h3.builder import assemble_prompt, build_reference_manifest, numbered_assets


def test_t2va_uses_base_guide_only(workspace_factory):
    result = assemble_prompt(workspace_factory(), False)
    assert "integrated_multimodal_description" in result.inspector_text
    assert "six-section full-reference grammar" not in result.inspector_text
    assert "Shot count: auto" in result.inspector_text


def test_keyframe_mode_guidance(workspace_factory, image_asset_factory):
    first = image_asset_factory(role="first_frame_anchor")
    last = image_asset_factory("last", "last_frame_anchor", 1)
    i2va = assemble_prompt(workspace_factory(mode="I2VA", assets=[first]), True)
    assert "at 0.00 seconds" in i2va.inspector_text
    fl2va = assemble_prompt(workspace_factory(mode="FL2VA", assets=[first, last], durationSeconds=7.25), True)
    assert "Picture 2" in fl2va.inspector_text and "7.25 seconds" in fl2va.inspector_text
    l2va = assemble_prompt(workspace_factory(mode="L2VA", assets=[last]), True)
    assert "official last-frame alignment grammar" in l2va.inspector_text


def test_independent_numbering_and_reordering(workspace_factory, image_asset_factory):
    assets = [
        image_asset_factory("p-late", order=5, filename="late.png"),
        image_asset_factory("p-first", order=0, filename="first.png"),
        {"id": "v2", "kind": "video", "filename": "b.mp4", "mimeType": "video/mp4", "role": "motion_action", "order": 4},
        {"id": "v1", "kind": "video", "filename": "a.mp4", "mimeType": "video/mp4", "role": "camera_movement", "order": 0},
        {"id": "a1", "kind": "audio", "filename": "a.wav", "mimeType": "audio/wav", "role": "voice_timbre", "order": 8},
    ]
    workspace = workspace_factory(mode="Ref2VA", assets=assets)
    labels = {asset.id: label for asset, label in numbered_assets(workspace)}
    assert labels == {"p-first": "Picture 1", "p-late": "Picture 2", "v1": "Video 1", "v2": "Video 2", "a1": "Audio 1"}


def test_subject_relationships_stay_user_semantics(workspace_factory, image_asset_factory):
    workspace = workspace_factory(
        mode="Ref2VA", assets=[image_asset_factory()],
        referenceLabels="Picture 1 defines Subject 7; Subject 7 is not Picture 7.",
    )
    manifest = build_reference_manifest(workspace)
    assert manifest["assets"][0]["h3Label"] == "Picture 1"
    assert "Subject 7" in manifest["referenceLabels"]


def test_image_vision_payload_and_media_metadata_only(workspace_factory, image_asset_factory):
    assets = [
        image_asset_factory(),
        {"id": "v", "kind": "video", "filename": "tail.mp4", "mimeType": "video/mp4", "role": "continuation_source"},
        {"id": "a", "kind": "audio", "filename": "voice.wav", "mimeType": "audio/wav", "role": "voice_timbre"},
    ]
    assembled = assemble_prompt(workspace_factory(mode="Ref2VA", assets=assets), True)
    payload = json.dumps(assembled.messages)
    assert '"type": "image_url"' in payload
    assert "video_url" not in payload and "input_audio" not in payload
    assert "data:video" not in payload and "data:audio" not in payload


def test_generation_uses_one_leading_system_message_without_losing_layers_or_images(workspace_factory, image_asset_factory):
    assembled = assemble_prompt(workspace_factory(mode="Ref2VA", assets=[image_asset_factory()]), True)
    assert [message["role"] for message in assembled.messages] == ["system", "user"]
    system = assembled.messages[0]["content"]
    headings = [
        "MiniConstruct core operating instructions", "Official MiniMax H3 guide (normative)",
        "Mode and reference guidance", "Subject and reference semantics",
        "Canonical workspace/reference manifest", "Generation policy",
    ]
    assert all(heading in system for heading in headings)
    assert [system.index(heading) for heading in headings] == sorted(system.index(heading) for heading in headings)
    assert any(part.get("type") == "image_url" for part in assembled.messages[1]["content"])
    assert "===== Official MiniMax H3 guide (normative) =====" in assembled.inspector_text


def test_vision_warning_is_explicit(workspace_factory, image_asset_factory):
    result = assemble_prompt(workspace_factory(mode="Ref2VA", assets=[image_asset_factory()]), False)
    assert result.warnings and "not sent as visual inputs" in result.warnings[0]
    assert "image_url" not in json.dumps(result.messages)


def test_normal_continuation(workspace_factory):
    video = {"id": "v", "kind": "video", "filename": "source.mp4", "mimeType": "video/mp4", "role": "continuation_source"}
    text = assemble_prompt(workspace_factory(mode="Ref2VA", assets=[video]), False).inspector_text
    assert "normal continuation source" in text
    assert "without forcing a replay" in text


def test_seamless_overlap_known_duration_and_camera_handoff(workspace_factory):
    video = {"id": "v", "kind": "video", "filename": "tail.mp4", "mimeType": "video/mp4", "durationSeconds": 1.72, "role": "seamless_overlap_continuation"}
    text = assemble_prompt(workspace_factory(mode="Ref2VA", assets=[video]), False).inspector_text
    assert "00:00.000–00:01.720" in text
    assert "continues monotonically forward" in text
    assert "overlap remains within Shot 1" in text
    assert "0.5–1.0 seconds" in text


def test_seamless_overlap_missing_duration(workspace_factory):
    video = {"id": "v", "kind": "video", "filename": "tail.mp4", "mimeType": "video/mp4", "role": "seamless_overlap_continuation"}
    text = assemble_prompt(workspace_factory(mode="Ref2VA", assets=[video]), False).inspector_text
    assert "complete duration of <Video 1>" in text
    assert "00:00.000–" not in text


def test_audio_roles_and_no_implicit_audio_from_video(workspace_factory):
    assets = [
        {"id": "v", "kind": "video", "filename": "sound.mp4", "mimeType": "video/mp4", "role": "general_video"},
        {"id": "a1", "kind": "audio", "filename": "full.wav", "mimeType": "audio/wav", "role": "full_reuse"},
        {"id": "a2", "kind": "audio", "filename": "part.wav", "mimeType": "audio/wav", "role": "partial_reuse"},
        {"id": "a3", "kind": "audio", "filename": "voice.wav", "mimeType": "audio/wav", "role": "voice_timbre"},
        {"id": "a4", "kind": "audio", "filename": "beat.wav", "mimeType": "audio/wav", "role": "music_beat_rhythm", "options": {"syncChoreography": True}},
    ]
    manifest = build_reference_manifest(workspace_factory(mode="Ref2VA", assets=assets))
    assert [item["h3Label"] for item in manifest["assets"] if item["kind"] == "audio"] == ["Audio 1", "Audio 2", "Audio 3", "Audio 4"]
    assert len([item for item in manifest["assets"] if item["kind"] == "video"]) == 1


def test_dialogue_verbatim_and_speaker_mapping_instruction(workspace_factory):
    workspace = workspace_factory(dialogue="Subject 2: Don't touch that!\nSubject 1: I won't.")
    text = assemble_prompt(workspace, False).inspector_text
    assert "Don't touch that!" in text and "I won't." in text
    assert "actual speech order" in text
