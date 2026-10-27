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


@pytest.mark.parametrize("mode", [H3Mode.T2VA, H3Mode.I2VA, H3Mode.FL2VA, H3Mode.L2VA])
@pytest.mark.parametrize("subject", [1, 2, 17])
def test_base_modes_reject_structural_subject_tokens_but_preserve_literal_payload(mode, subject):
    prompt = VALID_BASE.replace("a cyclist waits", f'<Subject {subject}> waits')
    result = validate_prompt(prompt, mode, 8)
    assert not result.valid
    errors = [item for item in result.findings if item.code == "unsupported_reference_mode"]
    assert len(errors) == 1
    assert errors[0].severity == "ERROR"
    assert errors[0].message == f"<Subject {subject}> references are only valid in Ref2VA."

    literal = VALID_BASE.replace("a cyclist waits", '<d>[English] <Subject 1> waits.</d>')
    assert not any(item.code == "unsupported_reference_mode" for item in validate_prompt(literal, mode, 8).findings)


@pytest.mark.parametrize("mode", [H3Mode.T2VA, H3Mode.I2VA, H3Mode.FL2VA, H3Mode.L2VA])
def test_subject_rule_keeps_each_base_modes_picture_alignment_grammar(mode, image_asset_factory):
    picture = image_asset_factory("first", role="first_frame_anchor")
    last = image_asset_factory("last", role="last_frame_anchor")
    prompt = VALID_BASE
    assets = []
    if mode == H3Mode.I2VA:
        assets = [picture]
        prompt = "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.\n\n" + prompt
    elif mode == H3Mode.FL2VA:
        assets = [picture, last]
        prompt = "How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark; Picture 2 (from Shot 2) aligns with the 8.00-second mark.\n\n" + prompt
    elif mode == H3Mode.L2VA:
        assets = [last]
        prompt = "How the reference pictures align with the target video — <Picture 1> (from [Shot 2]) aligns with the 8.00-second mark.\n\n" + prompt

    assert validate_prompt(prompt, mode, 8, assets).valid
    invalid = prompt.replace("a cyclist waits", "<Subject 17> waits")
    result = validate_prompt(invalid, mode, 8, assets)
    assert not result.valid
    assert any(item.code == "unsupported_reference_mode" for item in result.findings)


@pytest.mark.parametrize("mode", [H3Mode.T2VA, H3Mode.I2VA, H3Mode.FL2VA, H3Mode.L2VA])
@pytest.mark.parametrize("ordinal", [1, 17])
def test_base_modes_treat_bare_subject_labels_as_ordinary_prose(mode, ordinal, image_asset_factory):
    picture = image_asset_factory("first", role="first_frame_anchor")
    last = image_asset_factory("last", role="last_frame_anchor")
    prompt = VALID_BASE
    assets = []
    if mode == H3Mode.I2VA:
        assets = [picture]
        prompt = "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.\n\n" + prompt
    elif mode == H3Mode.FL2VA:
        assets = [picture, last]
        prompt = "How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark; Picture 2 (from Shot 2) aligns with the 8.00-second mark.\n\n" + prompt
    elif mode == H3Mode.L2VA:
        assets = [last]
        prompt = "How the reference pictures align with the target video — <Picture 1> (from [Shot 2]) aligns with the 8.00-second mark.\n\n" + prompt

    prompt = prompt.replace("a cyclist waits", f"Subject {ordinal} waits")
    result = validate_prompt(prompt, mode, 8, assets)
    assert result.valid
    assert not any(item.code in {"bare_reference_label", "unsupported_reference_mode"} for item in result.findings)


@pytest.mark.parametrize("mode", [H3Mode.T2VA, H3Mode.I2VA, H3Mode.FL2VA, H3Mode.L2VA])
def test_base_mode_canonicalization_does_not_create_subject_tokens(mode):
    assert canonicalize_bare_reference_labels("Subject 1 waits.", mode) == "Subject 1 waits."
    assert canonicalize_bare_reference_labels('<d>[English] Subject 1, report.</d>', mode) == '<d>[English] Subject 1, report.</d>'


def test_ref2va_subject_canonicalization_remains_unchanged():
    assert canonicalize_bare_reference_labels("Subject 1 waits.", H3Mode.REF2VA) == "<Subject 1> waits."
    result = validate_prompt(valid_ref_prompt().replace("<Subject 1> walks", "Subject 1 walks"), H3Mode.REF2VA, 6)
    assert any(item.code == "bare_reference_label" and 'use canonical H3 label <Subject 1>' in item.message for item in result.findings)


def test_fl2va_required_alignment_does_not_conflict_with_bare_label_rule(image_asset_factory):
    header = "How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark; Picture 2 (from Shot 2) aligns with the 8.00-second mark."
    assets = [image_asset_factory("first", role="first_frame_anchor"), image_asset_factory("last", role="last_frame_anchor")]
    prompt = header + "\n\n" + VALID_BASE
    assert validate_prompt(prompt, H3Mode.FL2VA, 8, assets).valid
    assert canonicalize_bare_reference_labels(prompt) == prompt
    invalid = prompt.replace("a cyclist waits", "Picture 1 shows a cyclist who waits")
    assert any(f.code == "bare_reference_label" for f in validate_prompt(invalid, H3Mode.FL2VA, 8, assets).findings)
    assert canonicalize_bare_reference_labels(invalid).startswith(header)
    for changed in (prompt.replace("8.00-second", "9.00-second"), prompt.replace("Picture 2 (from Shot 2)", "Picture 2 (from Shot 3)")):
        assert not validate_prompt(changed, H3Mode.FL2VA, 8, assets).valid


@pytest.mark.parametrize(
    "prompt",
    (
        "Here is the finished prompt:\n" + VALID_BASE,
        "Preface\n```text\n" + VALID_BASE + "\n```\nHope this helps.",
    ),
)
def test_outer_chatter_and_embedded_fences_are_rejected(prompt):
    result = validate_prompt(prompt, H3Mode.T2VA, 8)
    assert not result.valid
    assert any(item.code == "extraneous_preamble" for item in result.findings)
    if "```" in prompt:
        assert any(item.code == "markdown_fence" for item in result.findings)


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


@pytest.mark.parametrize("spoken", [
    '<d>[English] Finally...</d>',
])
def test_ref2va_exact_dialogue_accepts_main_description_carriers(image_asset_factory, spoken):
    prompt = valid_ref_prompt().replace('<d>[English] Ready?</d>', spoken)
    result = validate_prompt(prompt, H3Mode.REF2VA, 6, [image_asset_factory()], "Subject 1: Finally...")
    assert result.valid, result.findings


@pytest.mark.parametrize("spoken", ['"Finally!"', '"Almost finally..."', 'No spoken line.'])
def test_ref2va_changed_or_omitted_dialogue_fails(image_asset_factory, spoken):
    prompt = valid_ref_prompt().replace('<d>[English] Ready?</d>', spoken)
    result = validate_prompt(prompt, H3Mode.REF2VA, 6, [image_asset_factory()], "Subject 1: Finally...")
    assert any(item.code == "dialogue_changed" for item in result.findings)


def test_ref2va_retention_analysis_cannot_satisfy_dialogue(image_asset_factory):
    prompt = valid_ref_prompt().replace('identity is retained.', 'identity is retained; Finally...')
    result = validate_prompt(prompt, H3Mode.REF2VA, 6, [image_asset_factory()], "Subject 1: Finally...")
    assert any(item.code == "dialogue_changed" for item in result.findings)


def test_quoted_authoring_dialogue_matches_ref2va_transport_without_retention_copy(image_asset_factory):
    spoken = valid_ref_prompt().replace(
        '<Subject 1> (S1) says, <d>[English] Ready?</d>',
        '<Subject 2> (S1) lets out a soft, breathy exhale and whispers, <d>[English] Finally...</d>',
    )
    # Subject 2 needs an ordinary definition and retention entry for Ref2VA structure.
    spoken = spoken.replace('<Subject 1>', '<Subject 2>')
    result = validate_prompt(spoken, H3Mode.REF2VA, 6, [image_asset_factory()], 'Jane: "Finally..."')
    assert result.valid, result.findings
    assert "Finally..." not in spoken.split("detailed_description:")[0]
    changed = validate_prompt(spoken.replace('<d>[English] Finally...</d>', '<d>[English] Finally!</d>'),
                              H3Mode.REF2VA, 6, [image_asset_factory()], 'Jane: "Finally..."')
    assert any(item.code == "dialogue_changed" for item in changed.findings)


def test_endpoint_handoff_requires_keyframe_completion_and_rejects_video_claim(image_asset_factory):
    anchor = image_asset_factory(role="first_frame_anchor").model_copy(update={
        "options": {"visualHandoff": {"strategy": "endpoint_frame"}},
    })
    missing = validate_prompt(valid_ref_prompt(), H3Mode.REF2VA, 6, [anchor])
    assert any(item.code == "endpoint_handoff_requires_keyframe_completion" for item in missing.findings)
    wrong = validate_prompt(valid_ref_prompt().replace("[reference generation]", "[video continuation]"), H3Mode.REF2VA, 6, [anchor])
    assert any(item.code == "endpoint_handoff_rejects_video_continuation" for item in wrong.findings)
    correct = validate_prompt(valid_ref_prompt().replace("[reference generation]", "[keyframe completion]"), H3Mode.REF2VA, 6, [anchor])
    assert not any(item.code.startswith("endpoint_handoff_") for item in correct.findings)


def test_ref2va_preamble_is_rejected_even_when_the_six_sections_are_valid(image_asset_factory):
    result = validate_prompt("Generated prompt:\n" + valid_ref_prompt(), H3Mode.REF2VA, 6, [image_asset_factory()])
    assert not result.valid
    assert any(item.code == "extraneous_preamble" for item in result.findings)


def test_dangling_reference_label(image_asset_factory):
    result = validate_prompt(valid_ref_prompt().replace("<Picture 1>", "<Picture 2>"), H3Mode.REF2VA, 6, [image_asset_factory()])
    assert not result.valid
    assert any(item.code == "dangling_asset" for item in result.findings)


@pytest.mark.parametrize("label,code", [
    ("<Audio 99>", "dangling_asset"),
    ("<Picture 99>", "dangling_asset"),
    ("<Video 99>", "dangling_asset"),
    ("<Subject 99>", "dangling_subject"),
    ("Subject 1", "bare_reference_label"),
])
def test_strict_reference_findings_remain_visible(label, code, image_asset_factory):
    prompt = valid_ref_prompt().replace("Station ambience.", f"Station ambience. {label}")
    result = validate_prompt(prompt, H3Mode.REF2VA, 6, [image_asset_factory()])
    assert any(item.code == code and item.severity == "ERROR" for item in result.findings)


def test_reference_label_limit_is_based_on_supplied_assets(image_asset_factory):
    prompt = valid_ref_prompt() + "\n<Picture 2>"
    result = validate_prompt(prompt, H3Mode.REF2VA, 6, [image_asset_factory()])
    assert not result.valid
    assert any(item.code == "dangling_asset" for item in result.findings)


def test_missing_ref2va_section(image_asset_factory):
    prompt = valid_ref_prompt().replace("retention_analysis:", "retention:")
    result = validate_prompt(prompt, H3Mode.REF2VA, 6, [image_asset_factory()])
    assert not result.valid


def test_dialogue_must_remain_verbatim():
    result = validate_prompt(VALID_BASE, H3Mode.T2VA, 8, dialogue="Subject 2: Exact line!")
    assert not result.valid and any(item.code == "dialogue_changed" for item in result.findings)


def test_japanese_spoken_payload_accepts_language_tag_and_rejects_split_utterance():
    spoken = "いい？…んっ"
    valid = VALID_BASE.replace("a cyclist waits.", f"Makoto says <d>[Japanese] {spoken}</d>.")
    assert not any(f.code == "dialogue_changed" for f in validate_prompt(valid, H3Mode.T2VA, 8, dialogue=spoken).findings)
    for bad in (valid.replace(f"<d>[Japanese] {spoken}</d>", "<d>[Japanese] いい？</d> <d>[Japanese] んっ</d>"),):
        assert any(f.code == "dialogue_changed" for f in validate_prompt(bad, H3Mode.T2VA, 8, dialogue=spoken).findings)


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


def test_speaker_label_is_separate_from_language_prefix():
    prompt = VALID_BASE.replace(
        "[Shot 1] Live-action, a cyclist waits.",
        "[Shot 1] An off-screen speaker (S1) says, <d>[English] Keep moving.</d>",
    )
    assert validate_prompt(prompt, H3Mode.T2VA, 8, dialogue="Narrator: Keep moving.").valid


def test_h3_language_metadata_does_not_weaken_exact_spoken_payload():
    expected = "The door was open when I got here."
    valid = VALID_BASE.replace("a cyclist waits.", f"Iris says <d>[English] {expected}</d>.")
    assert not any(f.code == "dialogue_changed" for f in validate_prompt(valid, H3Mode.T2VA, 8, dialogue=expected).findings)
    quoted = valid.replace(f"[English] {expected}", f'[English] "{expected}"')
    assert any(f.code == "dialogue_changed" for f in validate_prompt(quoted, H3Mode.T2VA, 8, dialogue=expected).findings)
    annotated = valid.replace(f"[English] {expected}", f"[English] [excited] {expected}")
    assert any(f.code == "dialogue_changed" for f in validate_prompt(annotated, H3Mode.T2VA, 8, dialogue=expected).findings)


def test_h3_language_tag_is_removed_once_and_literal_language_like_text_is_kept():
    expected = "[English] is written here."
    prompt = VALID_BASE.replace("a cyclist waits.", f"A sign reads <d>[English] {expected}</d>.")
    assert not any(f.code == "dialogue_changed" for f in validate_prompt(prompt, H3Mode.T2VA, 8, dialogue=expected).findings)


def test_sequence_language_tag_mismatch_is_structural_not_spoken_change():
    expected = "いつ出発するの？"
    prompt = VALID_BASE.replace("a cyclist waits.", f"Makoto says <d>[English] {expected}</d>.")
    result = validate_prompt(prompt, H3Mode.T2VA, 8, dialogue=expected, dialogue_language="ja")
    codes = {item.code for item in result.findings}
    assert "dialogue_language_tag_mismatch" in codes
    assert "dialogue_changed" not in codes


def test_concrete_sequence_language_accepts_matching_japanese_tag():
    expected = "いつ出発するの？"
    prompt = VALID_BASE.replace("a cyclist waits.", f"Makoto says <d>[Japanese] {expected}</d>.")
    result = validate_prompt(prompt, H3Mode.T2VA, 8, dialogue=expected, dialogue_language="ja")
    codes = {item.code for item in result.findings}
    assert "dialogue_changed" not in codes
    assert "dialogue_language_tag_mismatch" not in codes


def test_missing_h3_language_tag_does_not_misreport_spoken_payload_change():
    expected = "Ok, so when?"
    prompt = VALID_BASE.replace("a cyclist waits.", f"A speaker says <d>{expected}</d>.")
    result = validate_prompt(prompt, H3Mode.T2VA, 8, dialogue=expected)
    codes = {item.code for item in result.findings}
    assert "dialogue_language_tag_missing" in codes
    assert "dialogue_changed" not in codes


def test_exact_dialogue_only_in_ordinary_prose_requires_h3_dialogue_block():
    expected = "Hello there."
    prompt = VALID_BASE.replace("a cyclist waits.", 'She looks toward him and says, "Hello there."')
    result = validate_prompt(prompt, H3Mode.T2VA, 8, dialogue=expected)
    codes = {item.code for item in result.findings}
    assert "dialogue_language_tag_missing" in codes
    assert "dialogue_changed" not in codes


@pytest.mark.parametrize("language,spoken", [("English", "Hello there."), ("French", "Bonjour.")])
def test_as_written_accepts_supported_language_tag(language, spoken):
    prompt = VALID_BASE.replace("a cyclist waits.", f"A speaker says <d>[{language}] {spoken}</d>.")
    result = validate_prompt(prompt, H3Mode.T2VA, 8, dialogue=spoken)
    assert not any(item.code in {"dialogue_changed", "dialogue_language_tag_missing"} for item in result.findings)


@pytest.mark.parametrize("tag,spoken,sequence_language", [
    ("EN", "Hello.", "as_written"),
    ("EN", "Hello.", "en"),
    ("Klingon", "こんにちは。", "ja"),
])
def test_unsupported_leading_language_tag_is_syntax_error_not_changed_speech(
    tag, spoken, sequence_language,
):
    prompt = VALID_BASE.replace("a cyclist waits.", f"A speaker says <d>[{tag}] {spoken}</d>.")
    result = validate_prompt(
        prompt, H3Mode.T2VA, 8, dialogue=spoken,
        dialogue_language=sequence_language,
    )
    codes = {item.code for item in result.findings}
    assert "dialogue_language_tag_invalid" in codes
    assert "dialogue_changed" not in codes


@pytest.mark.parametrize("wrapper", [('"', '"'), ('“', '”'), ('「', '」'), ('『', '』')])
def test_outer_dialogue_quotes_are_authoring_punctuation(wrapper):
    spoken = "君はとても美しいね。"
    prompt = VALID_BASE.replace("a cyclist waits.", f"Makoto says <d>{spoken}</d>.")
    dialogue = f"Makoto: {wrapper[0]}{spoken}{wrapper[1]}"
    assert not any(f.code == "dialogue_changed" for f in
                   validate_prompt(prompt, H3Mode.T2VA, 8, dialogue=dialogue).findings)
    changed = prompt.replace(spoken, "君は美しい。")
    assert any(f.code == "dialogue_changed" for f in
               validate_prompt(changed, H3Mode.T2VA, 8, dialogue=dialogue).findings)


def test_outer_dialogue_quotes_preserve_internal_quotation_and_punctuation():
    spoken = "彼は『行け』と言った。"
    prompt = VALID_BASE.replace("a cyclist waits.", f"Makoto says <d>{spoken}</d>.")
    assert not any(f.code == "dialogue_changed" for f in
                   validate_prompt(prompt, H3Mode.T2VA, 8, dialogue=f"Makoto: 「{spoken}」").findings)
    assert any(f.code == "dialogue_changed" for f in
               validate_prompt(prompt.replace("『行け』", "行け"), H3Mode.T2VA, 8,
                               dialogue=f"Makoto: 「{spoken}」").findings)


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
    if phrase.startswith("Subject "):
        prompt = valid_ref_prompt().replace("<Subject 1> (S1)", "Subject 1 (S1)")
        result = validate_prompt(prompt, H3Mode.REF2VA, 6)
    else:
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


def test_identity_provenance_outside_subject_record_fails(workspace_factory, image_asset_factory):
    asset = image_asset_factory()
    workspace = workspace_factory(mode="Ref2VA", assets=[asset])
    split = valid_ref_prompt().replace(
        "<Subject 1> is the woman in <Picture 1>.",
        "<Subject 1> is Alice, a woman with blonde hair.\n<Picture 1> provides the visual identity reference for <Subject 1>.",
    )
    result = validate_workspace_prompt(split, workspace)
    assert any(item.code == "identity_reference_missing" for item in result.findings)
    assert any(item.code == "identity_picture_definition" for item in result.findings)


def test_identity_picture_in_second_sentence_of_same_subject_record_is_valid(workspace_factory, image_asset_factory):
    workspace = workspace_factory(mode="Ref2VA", assets=[image_asset_factory()])
    prompt = valid_ref_prompt().replace(
        "<Subject 1> is the woman in <Picture 1>.",
        "<Subject 1> is Alice, a blonde young woman. Her visual identity comes from <Picture 1>.",
    )
    result = validate_workspace_prompt(prompt, workspace)
    assert not any(item.code == "identity_reference_missing" for item in result.findings)


def test_identity_only_picture_retention_is_rejected_but_independent_anchor_is_allowed(workspace_factory, image_asset_factory):
    identity = image_asset_factory()
    workspace = workspace_factory(mode="Ref2VA", assets=[identity])
    malformed = valid_ref_prompt().replace(
        "retention_analysis:\n<Subject 1>",
        "retention_analysis:\n<Picture 1> (identity provenance): weak_reference - used to establish <Subject 1>'s visual identity without reproducing the reference sheet layout or clothing.\n<Subject 1>",
    )
    result = validate_workspace_prompt(malformed, workspace)
    assert any(item.code == "identity_picture_retention" for item in result.findings)

    anchor = image_asset_factory(role="storyboard_composition")
    anchor_workspace = workspace_factory(mode="Ref2VA", assets=[anchor])
    independent = valid_ref_prompt().replace(
        "retention_analysis:\n<Subject 1>",
        "retention_analysis:\n<Picture 1> (anchors [Shot 1]): fully_preserved - storyboard composition is retained.\n<Subject 1>",
    )
    accepted = validate_workspace_prompt(independent, anchor_workspace)
    assert not any(item.code == "identity_picture_retention" for item in accepted.findings)


def test_multiple_identity_pictures_may_define_one_subject_without_picture_retention(workspace_factory, image_asset_factory):
    first = image_asset_factory("identity-a", order=0)
    second = image_asset_factory("identity-b", order=1)
    workspace = workspace_factory(mode="Ref2VA", assets=[first, second])
    prompt = valid_ref_prompt().replace(
        "<Subject 1> is the woman in <Picture 1>.",
        "<Subject 1> is the woman shown in <Picture 1> and <Picture 2>.",
    )
    result = validate_workspace_prompt(prompt, workspace)
    assert result.valid, result.findings
