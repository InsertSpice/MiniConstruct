from __future__ import annotations

import pytest

from miniconstruct.h3.validator import _subject_is_explicitly_offscreen, canonicalize_bare_reference_labels, validate_prompt, validate_workspace_prompt
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


def test_retention_visibility_rejects_subject_listed_in_another_subjects_shot(image_asset_factory):
    prompt = valid_ref_prompt().replace(
        "<Subject 1> (appears in [Shot 1]): fully_preserved - identity is retained.",
        "<Subject 1> (appears in [Shot 1], [Shot 2], [Shot 3]): fully_preserved - identity is retained.\n"
        "<Subject 2> (appears in [Shot 2], [Shot 3]): fully_preserved - identity is retained.",
    ).replace(
        "[Shot 1] <Subject 1> (S1) says, <d>[English] Ready?</d>",
        "[Shot 1] <Subject 1> and <Subject 2> enter.\n"
        "[Shot 2] At 00:02.000, <Subject 1> speaks.\n"
        "[Shot 3] At 00:04.000, <Subject 2> watches.",
    ).replace("<Subject 1> is the woman in <Picture 1>.", "<Subject 1> is the woman in <Picture 1>.\n<Subject 2> is her companion.")
    result = validate_prompt(prompt, H3Mode.REF2VA, 6, [image_asset_factory()])
    assert any(item.code == "retention_subject_not_in_shot" and "Subject 1" in item.message and "Shot 3" in item.message for item in result.findings)


def test_retention_visibility_rejects_explicitly_offscreen_subject(image_asset_factory):
    prompt = valid_ref_prompt().replace(
        "<Subject 1> (appears in [Shot 1]): fully_preserved - identity is retained.",
        "<Subject 1> (appears in [Shot 1]): fully_preserved - identity is retained.\n"
        "<Subject 2> (appears in [Shot 1]): fully_preserved - identity is retained.",
    ).replace(
        "[Shot 1] <Subject 1> (S1) says, <d>[English] Ready?</d>",
        "[Shot 1] <Subject 1> speaks toward <Subject 2>, who is explicitly off-screen.",
    ).replace("<Subject 1> is the woman in <Picture 1>.", "<Subject 1> is the woman in <Picture 1>.\n<Subject 2> is her companion.")
    result = validate_prompt(prompt, H3Mode.REF2VA, 6, [image_asset_factory()])
    assert any(item.code == "retention_subject_offscreen" for item in result.findings)


def test_retention_visibility_accepts_matching_subject_shot_map(image_asset_factory):
    prompt = valid_ref_prompt().replace(
        "<Subject 1> (appears in [Shot 1]): fully_preserved - identity is retained.",
        "<Subject 1> (appears in [Shot 1], [Shot 2]): fully_preserved - identity is retained.\n"
        "<Subject 2> (appears in [Shot 1], [Shot 3]): fully_preserved - identity is retained.",
    ).replace(
        "[Shot 1] <Subject 1> (S1) says, <d>[English] Ready?</d>",
        "[Shot 1] <Subject 1> and <Subject 2> enter.\n"
        "[Shot 2] At 00:02.000, <Subject 1> speaks.\n"
        "[Shot 3] At 00:04.000, <Subject 2> watches.",
    ).replace("<Subject 1> is the woman in <Picture 1>.", "<Subject 1> is the woman in <Picture 1>.\n<Subject 2> is her companion.")
    result = validate_prompt(prompt, H3Mode.REF2VA, 6, [image_asset_factory()])
    assert not any(item.code.startswith("retention_subject_") for item in result.findings)


def test_retention_visibility_does_not_guess_from_ambiguous_prose(image_asset_factory):
    prompt = valid_ref_prompt().replace(
        "<Subject 1> (S1) says, <d>[English] Ready?</d>",
        "[Shot 1] <Subject 1> waits in silence while off-screen footsteps approach.",
    )
    result = validate_prompt(prompt, H3Mode.REF2VA, 6, [image_asset_factory()])
    assert not any(item.code.startswith("retention_subject_") for item in result.findings)


@pytest.mark.parametrize("shot_text", [
    "<Subject 2> off-screen.",
    "<Subject 2> (off-screen).",
    "<Subject 1> looks toward <Subject 2> off-screen.",
])
def test_explicit_offscreen_detector_accepts_compact_subject_forms(shot_text):
    assert _subject_is_explicitly_offscreen(shot_text, 2)


def test_explicit_offscreen_detector_preserves_visible_and_all_occurrences_rule():
    assert not _subject_is_explicitly_offscreen("<Subject 2> enters the frame.", 2)
    assert not _subject_is_explicitly_offscreen("<Subject 2> off-screen; later <Subject 2> enters the frame.", 2)


@pytest.mark.parametrize("phrase", [
    "defined by Picture 1", "Subject 2 walks forward", "uses Video 1", "voice from Audio 1",
])
def test_bare_reference_labels_are_structural_errors(phrase):
    result = validate_prompt(VALID_BASE.replace("a cyclist waits", phrase), H3Mode.T2VA, 8)
    assert any(item.code == "bare_reference_label" for item in result.findings)


def test_canonical_reference_labels_and_literal_text_are_not_flagged():
    prompt = VALID_BASE.replace(
        "a cyclist waits",
        '<Subject 2> walks toward <Subject 1> with <Picture 1>, <Video 1>, and <Audio 1>. '
        '<d>[English] Look at Picture 1.</d> A sign reads "Picture 1".',
    )
    result = validate_prompt(prompt, H3Mode.T2VA, 8)
    assert not any(item.code == "bare_reference_label" for item in result.findings)


def test_retention_unknown_shot_is_a_structural_error(image_asset_factory):
    prompt = valid_ref_prompt().replace("[Shot 1]", "[Shot 1]").replace(
        "<Subject 1> (appears in [Shot 1]):",
        "<Subject 1> (appears in [Shot 1], [Shot 4]):",
    )
    result = validate_prompt(prompt, H3Mode.REF2VA, 6, [image_asset_factory()])
    assert any(item.code == "retention_unknown_shot" and "Shot 4" in item.message for item in result.findings)
    assert not any(item.code == "retention_subject_not_in_shot" and "Shot 1" in item.message for item in result.findings)


def test_canonicalize_bare_labels_preserves_literal_spans():
    text = 'relative to Subject 1; <d>[English] Subject 1.</d> A sign reads "Picture 2".'
    assert canonicalize_bare_reference_labels(text) == 'relative to <Subject 1>; <d>[English] Subject 1.</d> A sign reads "Picture 2".'


def test_observed_retention_and_bare_label_regression(image_asset_factory):
    prompt = """subject_definitions:
<Subject 1> is defined by <Picture 1>.
<Subject 2> has narrower-set eyes relative to Subject 1.

summary:
[reference generation] A scene.

retention_analysis:
<Subject 1> (appears in [Shot 1], [Shot 3]): fully_preserved.
<Subject 2> (appears in [Shot 1], [Shot 4]): fully_preserved.

detailed_description:
[Shot 1] <Subject 1> and <Subject 2> enter.
[Shot 2] At 00:02.000, <Subject 1> looks toward <Subject 2> off-screen.
[Shot 3] At 00:04.000, <Subject 2> watches.

overall_soundscape:
Quiet.

non_diegetic_music:
N/A"""
    result = validate_prompt(prompt, H3Mode.REF2VA, 6, [image_asset_factory()])
    codes = {item.code for item in result.findings}
    assert {"bare_reference_label", "retention_subject_not_in_shot", "retention_unknown_shot"} <= codes
    repaired = canonicalize_bare_reference_labels(prompt)
    assert "relative to <Subject 1>" in repaired
    assert "<Subject 2> off-screen" in repaired


def test_comparison_scale_requires_canonical_picture_provenance(image_asset_factory):
    first = image_asset_factory("identity-1", order=0)
    second = image_asset_factory("identity-2", order=1)
    comparison = image_asset_factory("comparison", role="character_comparison_scale", order=2)
    prompt = valid_ref_prompt().replace(
        "<Subject 1> is the woman in <Picture 1>.",
        "<Subject 1> is the woman in <Picture 1>.\n<Subject 2> is defined by <Picture 2>.\n"
        "<Picture 3> is a Character Comparison / Scale reference for <Subject 1> and <Subject 2>.",
    )
    result = validate_prompt(prompt, H3Mode.REF2VA, 6, [first, second, comparison])
    assert not any(item.code == "comparison_scale_reference_missing" for item in result.findings)


def test_comparison_scale_relation_without_picture_provenance_is_invalid(image_asset_factory):
    first = image_asset_factory("identity-1", order=0)
    second = image_asset_factory("identity-2", order=1)
    comparison = image_asset_factory("comparison", role="character_comparison_scale", order=2)
    prompt = valid_ref_prompt().replace(
        "<Subject 1> is the woman in <Picture 1>.",
        "<Subject 1> is the woman in <Picture 1> and is half a head taller than <Subject 2>. "
        "<Subject 2> is defined by <Picture 2>.",
    )
    result = validate_prompt(prompt, H3Mode.REF2VA, 6, [first, second, comparison])
    assert any(item.code == "comparison_scale_reference_missing" and "<Picture 3>" in item.message for item in result.findings)


def test_comparison_scale_picture_appears_in_retention_is_invalid(image_asset_factory):
    comparison = image_asset_factory("comparison", role="character_comparison_scale", order=0)
    prompt = valid_ref_prompt().replace(
        "<Subject 1> is the woman in <Picture 1>.",
        "<Subject 1> is the woman in <Picture 1>.\n<Picture 1> establishes the relative scale relationship.",
    ).replace(
        "retention_analysis:",
        "retention_analysis:\n<Picture 1> (appears in [Shot 1]): fully_preserved - scale retained.",
    )
    result = validate_prompt(prompt, H3Mode.REF2VA, 6, [comparison])
    assert any(item.code == "comparison_scale_picture_appears_in_shot" for item in result.findings)


def test_comparison_scale_retention_is_scoped_to_retention_analysis(image_asset_factory):
    comparison = image_asset_factory("comparison", role="character_comparison_scale", order=0)
    prompt = valid_ref_prompt().replace(
        "<Subject 1> is the woman in <Picture 1>.",
        "<Subject 1> is the woman in <Picture 1>.\n<Picture 1>: relative scale reference for <Subject 1> and <Subject 2>.",
    )
    result = validate_prompt(prompt, H3Mode.REF2VA, 6, [comparison])
    codes = {item.code for item in result.findings}
    assert "comparison_scale_reference_missing" not in codes
    assert "comparison_scale_picture_appears_in_shot" not in codes


def test_comparison_scale_relationship_retention_accepts_gemma_and_qwen_markers(image_asset_factory):
    comparison = image_asset_factory("comparison", role="character_comparison_scale", order=0)
    prompt = valid_ref_prompt().replace(
        "<Subject 1> is the woman in <Picture 1>.",
        "<Subject 1> is the woman in <Picture 1>.\n<Picture 1> establishes the relative scale relationship.",
    ).replace(
        "retention_analysis:",
        "retention_analysis:\n<Picture 1> (relative scale): fully_preserved - retain scale.",
    )
    result = validate_prompt(prompt, H3Mode.REF2VA, 6, [comparison])
    assert not any(item.code.startswith("comparison_scale_") for item in result.findings)
    qwen = prompt.replace("(relative scale): fully_preserved", "(scale relationship between [Shot 1]-[Shot 3]): weak_reference")
    assert not any(item.code.startswith("comparison_scale_") for item in validate_prompt(qwen, H3Mode.REF2VA, 6, [comparison]).findings)


def test_comparison_scale_retention_is_required(image_asset_factory):
    comparison = image_asset_factory("comparison", role="character_comparison_scale", order=0)
    prompt = valid_ref_prompt().replace(
        "<Subject 1> is the woman in <Picture 1>.",
        "<Subject 1> is the woman in <Picture 1>.\n<Picture 1> establishes the relative scale relationship.",
    )
    result = validate_prompt(prompt, H3Mode.REF2VA, 6, [comparison])
    assert any(item.code == "comparison_scale_retention_missing" for item in result.findings)


def test_comparison_scale_must_be_defined_in_subject_definitions(image_asset_factory):
    identity = image_asset_factory("identity", order=0)
    comparison = image_asset_factory("comparison", role="character_comparison_scale", order=1)
    prompt = valid_ref_prompt().replace(
        "[Shot 1] <Subject 1> (S1) says, <d>[English] Ready?</d>",
        "[Shot 1] <Picture 2> establishes scale for <Subject 1>.",
    )
    result = validate_prompt(prompt, H3Mode.REF2VA, 6, [identity, comparison])
    assert any(item.code == "comparison_scale_reference_missing" for item in result.findings)


def test_natural_comparison_definition_does_not_require_internal_role_words(image_asset_factory):
    first = image_asset_factory("identity-1", order=0)
    second = image_asset_factory("identity-2", order=1)
    comparison = image_asset_factory("comparison", role="character_comparison_scale", order=2)
    prompt = valid_ref_prompt().replace(
        "<Subject 1> is the woman in <Picture 1>.",
        "<Subject 1> is the woman in <Picture 1>.\n<Subject 2> is defined by <Picture 2>.\n"
        "<Picture 3> establishes that <Subject 1> is visibly taller than <Subject 2>, approximately half a head higher.",
    )
    result = validate_prompt(prompt, H3Mode.REF2VA, 6, [first, second, comparison])
    assert not any(item.code == "comparison_scale_reference_missing" for item in result.findings)


def test_colon_comparison_definition_is_accepted(image_asset_factory):
    first = image_asset_factory("identity-1", order=0)
    second = image_asset_factory("identity-2", order=1)
    comparison = image_asset_factory("comparison", role="character_comparison_scale", order=2)
    prompt = valid_ref_prompt().replace(
        "<Subject 1> is the woman in <Picture 1>.",
        "<Subject 1> is the woman in <Picture 1>.\n<Subject 2> is defined by <Picture 2>.\n"
        "<Picture 3>: establishes the relative scale relationship.",
    )
    result = validate_prompt(prompt, H3Mode.REF2VA, 6, [first, second, comparison])
    assert not any(item.code == "comparison_scale_reference_missing" for item in result.findings)


def test_multiple_comparison_pictures_are_checked_independently(image_asset_factory):
    first = image_asset_factory("identity-1", order=0)
    second = image_asset_factory("identity-2", order=1)
    third = image_asset_factory("comparison-1", role="character_comparison_scale", order=2)
    fourth = image_asset_factory("comparison-2", role="character_comparison_scale", order=3)
    prompt = valid_ref_prompt().replace(
        "<Subject 1> is the woman in <Picture 1>.",
        "<Subject 1> is the woman in <Picture 1>.\n<Subject 2> is defined by <Picture 2>.\n"
        "<Picture 3> establishes one relative scale relationship.",
    )
    result = validate_prompt(prompt, H3Mode.REF2VA, 6, [first, second, third, fourth])
    missing = [item.message for item in result.findings if item.code == "comparison_scale_reference_missing"]
    assert missing == ["Character Comparison / Scale <Picture 4> requires a relationship definition in subject_definitions."]


def test_workspace_identity_picture_provenance_is_per_subject(workspace_factory, image_asset_factory):
    first = image_asset_factory("first", order=0)
    second = image_asset_factory("second", order=1)
    second.subject_identity.subject_id = "subject-2"
    workspace = workspace_factory(
        mode="Ref2VA", assets=[first, second],
        subjects=[{"id": "subject-1", "number": 1}, {"id": "subject-2", "number": 2}], nextSubjectNumber=3,
    )
    prompt = valid_ref_prompt().replace(
        "<Subject 1> is the woman in <Picture 1>.",
        "<Subject 1> is the woman in <Picture 1>.\n<Subject 2> is the companion in <Picture 2>.",
    )
    assert not any(item.code == "identity_reference_missing" for item in validate_workspace_prompt(prompt, workspace).findings)
    missing = validate_workspace_prompt(prompt.replace("<Picture 2>", "her reference"), workspace)
    assert any(item.code == "identity_reference_missing" and "<Subject 2>" in item.message for item in missing.findings)
