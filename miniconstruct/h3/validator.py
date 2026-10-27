from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from miniconstruct.models.subject_numbers import subject_numbers as technical_subject_numbers
from miniconstruct.h3.prompt_text import mask_literal_text
from miniconstruct.models.api import ValidationFinding, ValidationResult
from miniconstruct.models.workspace import (
    DIALOGUE_LANGUAGE_LABELS, AssetKind, DialogueLanguage, H3Mode,
    ReferenceAsset, Workspace,
)


BASE_FIELDS = ["integrated_multimodal_description", "overall_soundscape", "non_diegetic_music"]
REF_FIELDS = [
    "subject_definitions", "summary", "retention_analysis", "detailed_description",
    "overall_soundscape", "non_diegetic_music",
]
FIELD_RE = re.compile(r"(?m)^(?P<name>[a-z_]+):")
SHOT_RE = re.compile(r"\[Shot\s+(\d+)\](?:\s+At\s+(\d{2}):(\d{2})\.(\d{3}),)?", re.IGNORECASE)
LABEL_RE = re.compile(r"<(Subject|Picture|Video|Audio)\s+(\d+)>")
BARE_LABEL_RE = re.compile(r"(?<![<\w])(Subject|Picture|Video|Audio)\s+(\d+)(?![\w>])")
H3_DIALOGUE_LANGUAGES = {
    label for language, label in DIALOGUE_LANGUAGE_LABELS.items()
    if language != DialogueLanguage.AS_WRITTEN
}
H3_DIALOGUE_BLOCK_RE = re.compile(r"(?is)<d>(.*?)</d>")
H3_LEADING_LANGUAGE_RE = re.compile(
    r"^\[(" + "|".join(re.escape(label) for label in sorted(H3_DIALOGUE_LANGUAGES, key=len, reverse=True))
    + r")\](?:\s+|$)", re.IGNORECASE,
)
H3_LEADING_BRACKET_TOKEN_RE = re.compile(r"^\[([^\]\r\n]+)\](?:\s+|$)")
RETENTION_SUBJECT_RE = re.compile(
    r"(?ms)^<Subject\s+(?P<subject>\d+)>\s*\(appears\s+in\s+\[(?P<shots>.*?)\]\)"
)
RETENTION_SHOT_RE = re.compile(r"\bShot\s+(\d+)\b", re.IGNORECASE)


def _finding(
    severity: str,
    code: str,
    message: str,
    category: str = "structural",
) -> ValidationFinding:
    return ValidationFinding(severity=severity, code=code, message=message, category=category)  # type: ignore[arg-type]


def _asset_label_limits(assets: list[ReferenceAsset]) -> dict[str, int]:
    return {
        "Picture": sum(asset.kind == AssetKind.IMAGE for asset in assets),
        "Video": sum(asset.kind == AssetKind.VIDEO for asset in assets),
        "Audio": sum(asset.kind == AssetKind.AUDIO for asset in assets),
    }


def _comparison_scale_picture_numbers(assets: list[ReferenceAsset]) -> list[int]:
    pictures = sorted((asset for asset in assets if asset.kind == AssetKind.IMAGE), key=lambda asset: (asset.order, asset.id))
    return [
        number for number, asset in enumerate(pictures, 1)
        if asset.role == "character_comparison_scale"
    ]


def _section_text(text: str, name: str, following_name: str) -> str:
    match = re.search(rf"(?ms)^{name}:\s*(.*?)(?=^{following_name}:)", text)
    return match.group(1) if match else ""


def _has_comparison_definition(definitions: str, picture: int) -> bool:
    return bool(re.search(rf"(?m)^<Picture\s+{picture}>(?=\s|:)", mask_literal_text(definitions)))


def _comparison_definition_entry(definitions: str, picture: int) -> str | None:
    match = re.search(
        rf"(?ms)^<Picture\s+{picture}>(?=\s|:).*?(?=^<Subject\s+\d+>\s+is\b|^<Picture\s+\d+>(?=\s|:)|\Z)",
        definitions,
    )
    return match.group(0).rstrip() if match else None


def _comparison_subject_labels(workspace: Workspace, asset: ReferenceAsset) -> str:
    numbers = {subject.id: subject.number for subject in workspace.subjects}
    labels = [f"<Subject {numbers[subject_id]}>" for subject_id in asset.comparison_subject_ids]
    if len(labels) == 2:
        return " and ".join(labels)
    return ", ".join(labels[:-1]) + f", and {labels[-1]}"


def _comparison_default_definition(workspace: Workspace, picture: int, asset: ReferenceAsset) -> str:
    subjects = _comparison_subject_labels(workspace, asset)
    return (
        f"<Picture {picture}> is the Character Comparison / Scale reference for {subjects}, "
        "establishing their relative height, body-size contrast, and broad body-proportion relationship."
    )


def _has_comparison_retention(retention: str, picture: int) -> bool:
    return bool(re.search(rf"(?m)^<Picture\s+{picture}>(?=\s|:)", mask_literal_text(retention)))


def _comparison_retention_entry(retention: str, picture: int) -> str | None:
    match = re.search(
        rf"(?ms)^<Picture\s+{picture}>(?=\s|:).*?(?=^<(?:Subject|Picture|Video|Audio)\s+\d+>|\Z)",
        retention,
    )
    return match.group(0).rstrip() if match else None


def _comparison_default_retention(picture: int) -> str:
    return (
        f"<Picture {picture}> (relative scale relationship): fully_preserved - "
        "the configured relative height, body-size contrast, and broad body-proportion relationship "
        "is retained when the linked Subjects are co-visible."
    )


def _normalize_comparison_appears_in(retention: str, picture: int) -> str:
    return re.sub(
        rf"(?im)(^<Picture\s+{picture}>\s*)\(\s*appears\s+in\s+\[[^\n]*\)",
        r"\1(relative scale relationship)",
        retention,
    )


def normalize_comparison_scale_references(
    prompt: str,
    workspace: Workspace,
    preserve_definitions_from: str | None = None,
) -> str:
    """Repair comparison bookkeeping only; callers keep this inside explicit Repair."""
    if workspace.mode != H3Mode.REF2VA:
        return prompt
    pictures = sorted(
        (asset for asset in workspace.assets if asset.kind == AssetKind.IMAGE),
        key=lambda asset: (asset.order, asset.id),
    )
    comparisons = [
        (number, asset)
        for number, asset in enumerate(pictures, 1)
        if asset.role == "character_comparison_scale"
    ]
    if not comparisons:
        return prompt

    definitions = _section_text(prompt, "subject_definitions", "summary")
    source_definitions = _section_text(preserve_definitions_from or "", "subject_definitions", "summary")
    additions: list[str] = []
    for picture, asset in comparisons:
        if _has_comparison_definition(definitions, picture):
            continue
        additions.append(
            _comparison_definition_entry(source_definitions, picture)
            or _comparison_default_definition(workspace, picture, asset)
        )
    if additions:
        match = re.search(r"(?m)^summary:", prompt)
        if match:
            prefix = prompt[:match.start()].rstrip()
            added_definitions = "\n".join(additions)
            prompt = f"{prefix}\n{added_definitions}\n\n{prompt[match.start():]}"

    retention_match = re.search(r"(?ms)^retention_analysis:\s*(.*?)(?=^detailed_description:)", prompt)
    if not retention_match:
        return prompt
    retention = retention_match.group(1)
    source_retention = _section_text(preserve_definitions_from or "", "retention_analysis", "detailed_description")
    for picture, _ in comparisons:
        retention = _normalize_comparison_appears_in(retention, picture)
    additions = [
        _comparison_retention_entry(source_retention, picture)
        or _comparison_default_retention(picture)
        for picture, _ in comparisons
        if not _has_comparison_retention(retention, picture)
    ]
    if additions:
        added_retentions = "\n".join(additions)
        retention = f"{retention.rstrip()}\n{added_retentions}\n\n"
    return f"{prompt[:retention_match.start(1)]}{retention}{prompt[retention_match.end(1):]}"


def _dialogue_lines(dialogue: str) -> list[str]:
    lines: list[str] = []
    for raw in dialogue.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        _, separator, text = raw.partition(":")
        spoken = text.strip() if separator else raw
        # Authoring quotes delimit the line; they are not spoken punctuation.
        spoken = _strip_outer_dialogue_quotes(spoken)
        lines.append(spoken)
    return [line for line in lines if line]


def _strip_outer_dialogue_quotes(spoken: str) -> str:
    """Remove one matching authoring wrapper, leaving spoken punctuation intact."""
    pairs = {'"': '"', '“': '”', '「': '」', '『': '』', '‘': '’'}
    if len(spoken) >= 2 and pairs.get(spoken[0]) == spoken[-1]:
        return spoken[1:-1]
    return spoken


def _spoken_text(text: str) -> str:
    """Normalize transport and typography, while keeping spoken punctuation exact."""
    text = re.sub(r"\\u([0-9a-fA-F]{4})", lambda match: chr(int(match.group(1), 16)), text)
    text = text.replace(r'\"', '"').replace("…", "...")
    return re.sub(r"\s+", " ", text)


@dataclass(frozen=True)
class _DialogueBlock:
    language: str | None
    tag_state: str  # "recognized", "missing", or "unsupported"
    payload: str


def _dialogue_blocks(main_text: str) -> list[_DialogueBlock]:
    blocks = []
    for match in H3_DIALOGUE_BLOCK_RE.finditer(main_text):
        content = match.group(1).strip()
        language = None
        tag_state = "missing"
        tag = H3_LEADING_BRACKET_TOKEN_RE.match(content)
        if tag:
            supported = H3_LEADING_LANGUAGE_RE.match(content)
            if supported:
                language = next((name for name in H3_DIALOGUE_LANGUAGES if name.casefold() == supported.group(1).casefold()), None)
                tag_state = "recognized"
                content = content[supported.end():]
            else:
                tag_state = "unsupported"
                content = content[tag.end():]
        blocks.append(_DialogueBlock(language, tag_state, content))
    return blocks


def _contains_exact_dialogue(main_text: str, line: str) -> bool:
    tagged = _dialogue_blocks(main_text)
    # Only one recognized leading H3 language token is syntax. Everything
    # after it remains exact spoken payload, including literal brackets/quotes.
    return any(block.tag_state == "recognized" and block.payload.strip() == line.strip() for block in tagged)


def _contains_dialogue_outside_blocks(main_text: str, line: str) -> bool:
    outside = H3_DIALOGUE_BLOCK_RE.sub(lambda match: " " * len(match.group()), main_text)
    spoken = _spoken_text(outside)
    expected = _spoken_text(line.strip())
    # This detects exact canonical text in prose only so the caller can report
    # missing H3 dialogue syntax. Prose never satisfies exact dialogue authority.
    return bool(re.search(r"(?<!\w)" + re.escape(expected) + r"(?![\w.!?…])", spoken))


def _mask_fl_alignment_labels(text: str) -> str:
    """Protect only the bare Picture tokens required by the existing FL2VA rule."""
    first = re.match(
        r"\AHow the reference pictures align with the target video — (Picture 1) "
        r"\(from Shot 1\) aligns with the 0\.00-second mark", text,
    )
    if first is None:
        return text
    line_end = text.find("\n")
    line_end = len(text) if line_end < 0 else line_end
    last = re.search(r"(Picture 2) \(from Shot \d+\) aligns with the \d+\.\d{2}-second mark", text[:line_end])
    for match in (first, last):
        if match is not None:
            start, end = match.span(1)
            text = text[:start] + " " * (end - start) + text[end:]
    return text


def canonicalize_bare_reference_labels(text: str, mode: H3Mode | None = None) -> str:
    """Bracket supported bare labels while preserving protected literal text.

    A missing mode retains the historical standalone-helper behavior. Production
    callers should pass the active Workspace mode.
    """
    protected = _mask_fl_alignment_labels(mask_literal_text(text))
    parts: list[str] = []
    cursor = 0
    for match in BARE_LABEL_RE.finditer(protected):
        if match.group(1) == "Subject" and mode is not None and mode != H3Mode.REF2VA:
            continue
        parts.extend((text[cursor:match.start()], f"<{match.group(1)} {match.group(2)}>"))
        cursor = match.end()
    parts.append(text[cursor:])
    return "".join(parts)


def _subject_is_explicitly_offscreen(shot_text: str, subject: int) -> bool:
    """Return true only when every local reference labels the Subject as off-screen."""
    label = rf"<Subject\s+{subject}>"
    occurrences = list(re.finditer(label, shot_text, re.IGNORECASE))
    if not occurrences:
        return False
    marker = r"(?:explicitly\s+)?off[- ]screen"
    return all(
        re.search(
            rf"{label}[^.\n]{{0,80}}\b(?:who\s+)?(?:is|remains|stays|kept|shown)\s+{marker}\b",
            shot_text[max(0, match.start() - 12):match.end() + 100], re.IGNORECASE,
        )
        or re.search(rf"{label}\s*(?:,\s*|\(\s*)?{marker}\b", shot_text[match.start():match.end() + 32], re.IGNORECASE)
        or re.search(rf"\b{marker}\s+{label}", shot_text[max(0, match.start() - 24):match.end() + 12], re.IGNORECASE)
        for match in occurrences
    )


def _retention_visibility_findings(retention_text: str, shot_blocks: dict[int, str]) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for match in RETENTION_SUBJECT_RE.finditer(retention_text):
        subject = int(match.group("subject"))
        for shot_text in RETENTION_SHOT_RE.findall(match.group("shots")):
            shot = int(shot_text)
            block = shot_blocks.get(shot)
            if block is None:
                findings.append(_finding(
                    "ERROR", "retention_unknown_shot",
                    f"Retention analysis lists Subject {subject} in Shot {shot}, but Shot {shot} does not exist.",
                ))
                continue
            if not re.search(rf"<Subject\s+{subject}>", block, re.IGNORECASE):
                findings.append(_finding(
                    "ERROR", "retention_subject_not_in_shot",
                    f"Retention analysis lists Subject {subject} in Shot {shot}, but that shot has no <Subject {subject}> reference.",
                ))
            elif _subject_is_explicitly_offscreen(block, subject):
                findings.append(_finding(
                    "ERROR", "retention_subject_offscreen",
                    f"Retention analysis lists Subject {subject} in Shot {shot}, but that Subject is explicitly off-screen there.",
                ))
    return findings


def validate_prompt(
    prompt: str,
    mode: H3Mode,
    duration_seconds: float,
    assets: list[ReferenceAsset] | None = None,
    dialogue: str = "",
    expected_shots: int | None = None,
    dialogue_language: str = "as_written",
    *, native_i2va: bool = False,
) -> ValidationResult:
    assets = assets or []
    findings: list[ValidationFinding] = []
    text = prompt.strip()
    if not text:
        return ValidationResult(valid=False, findings=[_finding("ERROR", "empty", "The generated prompt is empty.")])
    if "```" in text:
        findings.append(_finding("ERROR", "markdown_fence", "Remove Markdown fences from the H3 prompt."))

    label_text = mask_literal_text(text)
    if mode == H3Mode.FL2VA:
        label_text = _mask_fl_alignment_labels(label_text)
    for kind, number in BARE_LABEL_RE.findall(label_text):
        if kind == "Subject" and mode != H3Mode.REF2VA:
            continue
        token = f"{kind} {number}"
        findings.append(_finding(
            "ERROR", "bare_reference_label",
            f'Bare reference label "{token}"; use canonical H3 label <{token}>.',
        ))

    if mode != H3Mode.REF2VA:
        for kind, number in LABEL_RE.findall(label_text):
            if kind == "Subject":
                findings.append(_finding(
                    "ERROR", "unsupported_reference_mode",
                    f"<{kind} {number}> references are only valid in Ref2VA.",
                ))

    expected = REF_FIELDS if mode == H3Mode.REF2VA else BASE_FIELDS
    if (mode in {H3Mode.T2VA, H3Mode.REF2VA} or native_i2va) and not text.startswith(f"{expected[0]}:"):
        findings.append(_finding(
            "ERROR", "extraneous_preamble",
            f"The H3 prompt must begin directly with {expected[0]}: without commentary or a heading.",
        ))
    observed = [match.group("name") for match in FIELD_RE.finditer(text)]
    known_observed = [field for field in observed if field in set(BASE_FIELDS + REF_FIELDS)]
    for field in expected:
        if field not in observed:
            findings.append(_finding("ERROR", "missing_section", f"Missing required section: {field}."))
    if known_observed and known_observed != expected:
        findings.append(
            _finding("ERROR", "section_order", f"Required section order is: {', '.join(expected)}.")
        )
    unknown = [field for field in observed if field not in set(BASE_FIELDS + REF_FIELDS)]
    for field in unknown:
        findings.append(_finding("WARNING", "unknown_section", f"Unrecognized top-level section: {field}."))

    first_line = text.splitlines()[0].strip()
    if mode == H3Mode.I2VA and native_i2va:
        if assets or re.search(r"<(?:Picture|Video|Audio)\s+\d+>|(?:supplied|attached|new)\s+first[- ]frame\s+(?:Picture|image)", text, re.I):
            findings.append(_finding("ERROR", "native_i2va_media", "Native-only I2VA has no new first-frame Picture or media references."))
    elif mode == H3Mode.I2VA:
        exact = "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced."
        if first_line != exact:
            findings.append(_finding("ERROR", "first_frame_alignment", "I2VA requires the exact official first-frame instruction."))
    elif mode == H3Mode.FL2VA:
        if not first_line.startswith("How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark"):
            findings.append(_finding("ERROR", "frame_alignment", "FL2VA requires the official first/last alignment instruction."))
        if f"{duration_seconds:.2f}-second mark" not in first_line or "Picture 2" not in first_line:
            findings.append(_finding("ERROR", "last_frame_time", "FL2VA last-frame alignment must use the effective duration and Picture 2."))
    elif mode == H3Mode.L2VA:
        if not first_line.startswith("How the reference pictures align with the target video — <Picture 1> (from [Shot"):
            findings.append(_finding("ERROR", "last_frame_alignment", "L2VA requires the official last-frame alignment instruction."))
        if f"{duration_seconds:.2f}-second mark" not in first_line:
            findings.append(_finding("ERROR", "last_frame_time", "L2VA alignment must use the effective duration."))
    elif mode == H3Mode.T2VA and first_line.startswith("How the reference pictures align"):
        findings.append(_finding("ERROR", "unexpected_alignment", "T2VA must not include a keyframe alignment instruction."))

    main_field = "detailed_description" if mode == H3Mode.REF2VA else "integrated_multimodal_description"
    main_start_match = re.search(rf"(?m)^{main_field}:\s*", text)
    main_text = ""
    if main_start_match:
        main_end = len(text)
        for field in expected[expected.index(main_field) + 1:]:
            match = re.search(rf"(?m)^{field}:\s*", text[main_start_match.end():])
            if match:
                main_end = main_start_match.end() + match.start()
                break
        main_text = text[main_start_match.end():main_end]
    shots = list(SHOT_RE.finditer(main_text))
    shot_blocks = {
        int(match.group(1)): main_text[match.end():shots[index + 1].start() if index + 1 < len(shots) else len(main_text)]
        for index, match in enumerate(shots)
    }
    if not shots or int(shots[0].group(1)) != 1:
        findings.append(_finding("ERROR", "missing_shot_1", "The main description must begin with [Shot 1]."))
    else:
        if shots[0].group(2) is not None:
            findings.append(_finding("ERROR", "shot_1_timestamp", "Shot 1 must not have a timestamp."))
        numbers = [int(match.group(1)) for match in shots]
        if numbers != list(range(1, len(numbers) + 1)):
            findings.append(_finding("ERROR", "shot_sequence", "Shot numbers must be sequential from Shot 1."))
        if expected_shots is not None and len(shots) != expected_shots:
            findings.append(
                _finding(
                    "ERROR",
                    "shot_count",
                    f"Requested {expected_shots} shots, but the output contains {len(shots)}.",
                    "workspace_consistency",
                )
            )
        if mode == H3Mode.FL2VA and f"Picture 2 (from Shot {len(shots)})" not in first_line:
            findings.append(_finding("ERROR", "final_shot_alignment", "FL2VA Picture 2 must belong to the actual final shot."))
        if mode == H3Mode.L2VA and f"<Picture 1> (from [Shot {len(shots)}])" not in first_line:
            findings.append(_finding("ERROR", "final_shot_alignment", "L2VA Picture 1 must belong to the actual final shot."))
        previous = -1.0
        for index, match in enumerate(shots[1:], 2):
            if match.group(2) is None:
                findings.append(_finding("ERROR", "missing_shot_time", f"Shot {index} requires an MM:SS.mmm cut time."))
                continue
            timestamp = int(match.group(2)) * 60 + int(match.group(3)) + int(match.group(4)) / 1000
            if timestamp <= previous:
                findings.append(_finding("ERROR", "shot_time_order", "Later-shot timestamps must be strictly increasing."))
            if timestamp >= duration_seconds:
                findings.append(_finding("ERROR", "shot_outside_duration", f"Shot {index} begins outside the {duration_seconds:.2f}s target duration."))
            previous = timestamp

    limits = _asset_label_limits(assets)
    defined_subjects = {
        int(match.group(1))
        for match in re.finditer(r"(?m)^<Subject\s+(\d+)>\s+is\b", text)
    }
    seen_dangling: set[tuple[str, int]] = set()
    for kind, number_text in LABEL_RE.findall(text):
        number = int(number_text)
        if kind == "Subject":
            if mode == H3Mode.REF2VA and number not in defined_subjects and (kind, number) not in seen_dangling:
                findings.append(_finding("ERROR", "dangling_subject", f"<Subject {number}> has no definition."))
                seen_dangling.add((kind, number))
        elif (number < 1 or number > limits[kind]) and (kind, number) not in seen_dangling:
            findings.append(_finding("ERROR", "dangling_asset", f"<{kind} {number}> does not resolve to a supplied asset."))
            seen_dangling.add((kind, number))

    if mode == H3Mode.REF2VA:
        definitions = _section_text(text, "subject_definitions", "summary")
        retention = _section_text(text, "retention_analysis", "detailed_description")
        for picture in _comparison_scale_picture_numbers(assets):
            label = f"<Picture {picture}>"
            if not _has_comparison_definition(definitions, picture):
                findings.append(_finding(
                    "ERROR", "comparison_scale_reference_missing",
                    f"Character Comparison / Scale {label} requires a relationship definition in subject_definitions.",
                ))
            if not _has_comparison_retention(retention, picture):
                findings.append(_finding(
                    "ERROR", "comparison_scale_retention_missing",
                    f"Character Comparison / Scale {label} requires a relationship retention entry in retention_analysis.",
                ))
            elif re.search(rf"(?im)^<Picture\s+{picture}>\s*\(\s*appears\s+in\s+\[", mask_literal_text(retention)):
                findings.append(_finding(
                    "ERROR", "comparison_scale_picture_appears_in_shot",
                    f"Character Comparison / Scale {label} retains a relationship; it should not use Subject-style appears in [Shot ...] retention syntax.",
                ))
        if retention:
            findings.extend(_retention_visibility_findings(retention, shot_blocks))
        summary_match = re.search(r"(?ms)^summary:\s*(.*?)(?=^retention_analysis:)", text)
        summary = summary_match.group(1).lower() if summary_match else ""
        roles = {asset.role for asset in assets}
        if roles & {"continuation_source", "tail_reference_continuation"} and "video continuation" not in summary:
            findings.append(_finding("ERROR", "missing_task_relationship", "A continuation Video requires the official video continuation task relationship."))
        if "editing_source" in roles and "video editing" not in summary:
            findings.append(_finding("ERROR", "missing_task_relationship", "An editing-source Video requires the official video editing task relationship."))
        if roles & {"full_reuse", "partial_reuse"} and "audio reuse" not in summary:
            findings.append(_finding("ERROR", "missing_task_relationship", "Direct Audio reuse requires the official audio reuse task relationship."))
        if roles & {"music_beat_rhythm", "voice_timbre", "dialogue_spoken_content", "sound_audio_style", "general_audio"} and "audio reference" not in summary:
            findings.append(_finding("WARNING", "missing_task_relationship", "Guidance-only Audio normally requires the official audio reference task relationship."))
        handoff_endpoint = any(asset.kind == "image" and asset.options.get("visualHandoff", {}).get("strategy") in {"endpoint_frame", "endpoint_then_immediate_cut"} for asset in assets)
        if roles & {"first_frame_anchor", "keyframe_anchor", "last_frame_anchor", "storyboard_composition"} and "keyframe completion" not in summary:
            findings.append(_finding("WARNING", "missing_task_relationship", "A concrete Picture anchor normally requires keyframe completion."))
        if handoff_endpoint and "keyframe completion" not in summary:
            findings.append(_finding("ERROR", "endpoint_handoff_requires_keyframe_completion", "A predecessor endpoint Picture requires the official keyframe completion task relationship."))
        if handoff_endpoint and "video continuation" in summary and not any(asset.kind == "video" and asset.role in {"continuation_source", "tail_reference_continuation"} for asset in assets):
            findings.append(_finding("ERROR", "endpoint_handoff_rejects_video_continuation", "Endpoint-only handoff must not claim video continuation without a continuation Video."))
        subject_speakers: defaultdict[int, set[int]] = defaultdict(set)
        for subject, speaker in re.findall(r"<Subject\s+(\d+)>\s*\(S(\d+)\)", text):
            subject_speakers[int(subject)].add(int(speaker))
        for subject, speakers in subject_speakers.items():
            if len(speakers) > 1:
                findings.append(_finding("WARNING", "speaker_consistency", f"Subject {subject} uses multiple speaker IDs: {sorted(speakers)}."))

    try:
        selected_language = DialogueLanguage(str(dialogue_language).split(".")[-1].lower())
    except ValueError:
        selected_language = DialogueLanguage.AS_WRITTEN
    required_language = (
        DIALOGUE_LANGUAGE_LABELS[selected_language]
        if selected_language != DialogueLanguage.AS_WRITTEN else None
    )
    for line in _dialogue_lines(dialogue):
        blocks = _dialogue_blocks(main_text)
        matching = [block for block in blocks if block.payload.strip() == line.strip()]
        if matching and any(block.tag_state == "unsupported" for block in matching):
            findings.append(_finding("ERROR", "dialogue_language_tag_invalid", "The leading H3 dialogue language tag is unsupported; use one of the official supported language names."))
        if matching and any(block.tag_state == "missing" for block in matching):
            findings.append(_finding("ERROR", "dialogue_language_tag_missing", "Exact spoken text must use H3 dialogue syntax <d>[Language] ...</d>; its required supported leading language tag is missing."))
        if required_language and any(block.language is not None and block.language != required_language for block in matching):
            findings.append(_finding("ERROR", "dialogue_language_tag_mismatch", f"H3 dialogue language tag must be {required_language} for this Sequence."))
        if matching:
            continue
        if _contains_dialogue_outside_blocks(main_text, line):
            findings.append(_finding("ERROR", "dialogue_language_tag_missing", "Exact spoken text appears outside an H3 dialogue block; use <d>[Language] exact spoken payload</d>."))
        elif not _contains_exact_dialogue(main_text, line):
            findings.append(_finding("ERROR", "dialogue_changed", f"Exact dialogue was not preserved: {line}"))

    if not findings:
        findings.append(_finding("INFO", "structure_ok", "Required H3 structure and explicit invariants passed."))
    return ValidationResult(valid=not any(item.severity == "ERROR" for item in findings), findings=findings)


def validate_workspace_prompt(prompt: str, workspace: Workspace) -> ValidationResult:
    result = validate_prompt(prompt, workspace.mode, workspace.duration_seconds, workspace.assets, workspace.dialogue, workspace.shots, workspace.dialogue_language.value,
                             native_i2va=workspace._native_i2va)
    if workspace.mode != H3Mode.REF2VA:
        return result
    definitions_match = re.search(r"(?ms)^subject_definitions:\s*(.*?)(?=^summary:)", prompt)
    definitions = mask_literal_text(definitions_match.group(1)) if definitions_match else ""
    subject_numbers = technical_subject_numbers(workspace.subjects)
    pictures = sorted((asset for asset in workspace.assets if asset.kind == AssetKind.IMAGE), key=lambda asset: (asset.order, asset.id))
    identity_only_numbers = {
        number for number, asset in enumerate(pictures, 1) if asset.role == "subject_identity"
    }
    identity_subjects: dict[int, int] = {}
    for picture_number, asset in enumerate(pictures, 1):
        if asset.role != "subject_identity":
            continue
        subject_number = subject_numbers.get(asset.subject_identity.subject_id)
        if subject_number is None:
            continue
        identity_subjects[picture_number] = subject_number
        subject_match = re.search(
            rf"(?ms)^<Subject\s+{subject_number}>\s+is\b(.*?)(?=^<(?:Subject|Picture|Video|Audio)\s+\d+>(?=\s|:|$)|\Z)",
            definitions,
        )
        label = f"<Picture {picture_number}>"
        subject_record = subject_match.group(0) if subject_match else ""
        if subject_match is None or label not in subject_record:
            result.findings.append(_finding(
                "ERROR", "identity_reference_missing",
                f"<Subject {subject_number}> must cite identity provenance {label} inside its definition.",
            ))
    if definitions:
        for match in re.finditer(r"(?m)^<Picture\s+(\d+)>[^\n]*", definitions):
            number = int(match.group(1))
            if number in identity_only_numbers:
                subject_number = identity_subjects.get(number)
                target = f"Subject {subject_number}" if subject_number is not None else "its mapped Subject"
                result.findings.append(_finding(
                    "ERROR", "identity_picture_definition",
                    f"Picture {number} is identity-only provenance for {target}; cite <Picture {number}> inside that Subject's definition instead of defining it separately.",
                ))
    retention_match = re.search(r"(?ms)^retention_analysis:\s*(.*?)(?=^detailed_description:)", prompt)
    if retention_match:
        for match in re.finditer(r"(?m)^<Picture\s+(\d+)>[^\n]*", retention_match.group(1)):
            number = int(match.group(1))
            if number in identity_only_numbers:
                result.findings.append(_finding(
                    "ERROR", "identity_picture_retention",
                    f"Picture {number} is only Subject identity provenance and must not have a standalone retention_analysis entry; retain its mapped Subject instead.",
                ))
    if any(item.severity == "ERROR" for item in result.findings):
        result.valid = False
        result.findings[:] = [item for item in result.findings if item.code != "structure_ok"]
    return result
