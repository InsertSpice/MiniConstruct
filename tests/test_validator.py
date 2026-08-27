from __future__ import annotations

from miniconstruct.h3.validator import validate_prompt
from miniconstruct.models.workspace import H3Mode


VALID_BASE = """integrated_multimodal_description: [Shot 1] Live-action, a cyclist waits. [Shot 2] At 00:03.500, the camera cuts to the cyclist crossing.

overall_soundscape: Rain and distant traffic.

non_diegetic_music: N/A"""


def test_valid_base_output():
    result = validate_prompt(VALID_BASE, H3Mode.T2VA, 8)
    assert result.valid


def test_missing_base_section():
    result = validate_prompt(VALID_BASE.replace("overall_soundscape:", "sound:"), H3Mode.T2VA, 8)
    assert not result.valid
    assert any(item.code == "missing_section" for item in result.findings)


def test_invalid_shot_ordering():
    prompt = VALID_BASE.replace(
        " [Shot 2] At 00:03.500, the camera cuts to the cyclist crossing.",
        " [Shot 2] At 00:05.000, the camera cuts to the cyclist crossing. [Shot 3] At 00:04.000, the view changes early.",
    )
    result = validate_prompt(prompt, H3Mode.T2VA, 8)
    assert not result.valid
    assert any(item.code == "shot_time_order" for item in result.findings)


def test_shot_outside_duration():
    result = validate_prompt(VALID_BASE.replace("00:03.500", "00:08.000"), H3Mode.T2VA, 8)
    assert not result.valid
    assert any(item.code == "shot_outside_duration" for item in result.findings)


def test_i2va_exact_alignment(image_asset_factory):
    prompt = "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.\n\n" + VALID_BASE
    result = validate_prompt(prompt, H3Mode.I2VA, 8, [image_asset_factory(role="first_frame_anchor")])
    assert result.valid


def valid_ref_prompt():
    return """subject_definitions:
<Subject 1> is the woman in <Picture 1>.

summary:
[reference generation] <Subject 1> walks through a station.

retention_analysis:
<Subject 1> (appears in [Shot 1]): fully_preserved - identity is retained.

detailed_description:
The target uses live action.
[Shot 1] <Subject 1> (S1) says, <d>[English] Ready?</d>

overall_soundscape:
Station ambience.

non_diegetic_music:
N/A"""


def test_valid_ref2va_output(image_asset_factory):
    result = validate_prompt(valid_ref_prompt(), H3Mode.REF2VA, 6, [image_asset_factory()], "Subject 1: Ready?")
    assert result.valid


def test_dangling_reference_label(image_asset_factory):
    result = validate_prompt(valid_ref_prompt().replace("<Picture 1>", "<Picture 2>"), H3Mode.REF2VA, 6, [image_asset_factory()])
    assert not result.valid
    assert any(item.code == "dangling_asset" for item in result.findings)


def test_missing_ref2va_section(image_asset_factory):
    prompt = valid_ref_prompt().replace("retention_analysis:", "retention:")
    result = validate_prompt(prompt, H3Mode.REF2VA, 6, [image_asset_factory()])
    assert not result.valid


def test_dialogue_must_remain_verbatim():
    result = validate_prompt(VALID_BASE, H3Mode.T2VA, 8, dialogue="Subject 2: Exact line!")
    assert not result.valid and any(item.code == "dialogue_changed" for item in result.findings)


def test_one_speaker_dialogue_is_verified_verbatim():
    prompt = VALID_BASE.replace(
        "[Shot 1] Live-action, a cyclist waits.",
        "[Shot 1] A cyclist (S1) says, <d>[English] Keep moving.</d>",
    )
    result = validate_prompt(prompt, H3Mode.T2VA, 8, dialogue="Subject 4: Keep moving.")
    assert result.valid


def test_multiple_speaker_dialogue_is_verified_verbatim():
    prompt = VALID_BASE.replace(
        "[Shot 1] Live-action, a cyclist waits.",
        "[Shot 1] The woman (S1) says, <d>[English] Ready?</d> The man (S2) replies, <d>[English] Now.</d>",
    )
    result = validate_prompt(prompt, H3Mode.T2VA, 8, dialogue="Subject 9: Ready?\nSubject 3: Now.")
    assert result.valid


def test_dialogue_without_language_prefix_is_still_checked():
    prompt = VALID_BASE.replace(
        "[Shot 1] Live-action, a cyclist waits.",
        "[Shot 1] An off-screen speaker (S1) says, <d>Keep moving.</d>",
    )
    assert validate_prompt(prompt, H3Mode.T2VA, 8, dialogue="Narrator: Keep moving.").valid
