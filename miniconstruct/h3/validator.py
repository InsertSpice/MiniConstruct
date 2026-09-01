from __future__ import annotations

import re
from collections import defaultdict

from miniconstruct.models.api import ValidationFinding, ValidationResult
from miniconstruct.models.workspace import AssetKind, H3Mode, ReferenceAsset, Workspace


BASE_FIELDS = ["integrated_multimodal_description", "overall_soundscape", "non_diegetic_music"]
REF_FIELDS = [
    "subject_definitions", "summary", "retention_analysis", "detailed_description",
    "overall_soundscape", "non_diegetic_music",
]
FIELD_RE = re.compile(r"(?m)^(?P<name>[a-z_]+):")
SHOT_RE = re.compile(r"\[Shot\s+(\d+)\](?:\s+At\s+(\d{2}):(\d{2})\.(\d{3}),)?", re.IGNORECASE)
LABEL_RE = re.compile(r"<(Subject|Picture|Video|Audio)\s+(\d+)>")
BARE_LABEL_RE = re.compile(r"(?<![<\w])(Subject|Picture|Video|Audio)\s+(\d+)(?![\w>])")
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
    return bool(re.search(rf"(?m)^<Picture\s+{picture}>(?=\s|:)", _mask_literal_text(definitions)))


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
    return bool(re.search(rf"(?m)^<Picture\s+{picture}>(?=\s|:)", _mask_literal_text(retention)))


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
        lines.append(text.strip() if separator else raw)
    return [line for line in lines if line]


def _mask_literal_text(text: str) -> str:
    """Mask literal dialogue and quoted scene text before structural label checks."""
    masked = re.sub(r"(?is)<d>.*?</d>", lambda match: " " * len(match.group()), text)
    return re.sub(r'"(?:\\.|[^"\\])*"', lambda match: " " * len(match.group()), masked)


def canonicalize_bare_reference_labels(text: str) -> str:
    """Bracket bare labels while preserving dialogue and quoted literal text."""
    protected = _mask_literal_text(text)
    parts: list[str] = []
    cursor = 0
    for match in BARE_LABEL_RE.finditer(protected):
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
) -> ValidationResult:
    assets = assets or []
    findings: list[ValidationFinding] = []
    text = prompt.strip()
    if not text:
        return ValidationResult(valid=False, findings=[_finding("ERROR", "empty", "The generated prompt is empty.")])
    if text.startswith("```") or text.endswith("```"):
        findings.append(_finding("ERROR", "markdown_fence", "Remove Markdown fences from the H3 prompt."))

    for kind, number in BARE_LABEL_RE.findall(_mask_literal_text(text)):
        token = f"{kind} {number}"
        findings.append(_finding(
            "ERROR", "bare_reference_label",
            f'Bare reference label "{token}"; use canonical H3 label <{token}>.',
        ))

    expected = REF_FIELDS if mode == H3Mode.REF2VA else BASE_FIELDS
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
    if mode == H3Mode.I2VA:
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
            elif re.search(rf"(?im)^<Picture\s+{picture}>\s*\(\s*appears\s+in\s+\[", _mask_literal_text(retention)):
                findings.append(_finding(
                    "ERROR", "comparison_scale_picture_appears_in_shot",
                    f"Character Comparison / Scale {label} retains a relationship; it should not use Subject-style appears in [Shot ...] retention syntax.",
                ))
        if retention:
            findings.extend(_retention_visibility_findings(retention, shot_blocks))
        summary_match = re.search(r"(?ms)^summary:\s*(.*?)(?=^retention_analysis:)", text)
        summary = summary_match.group(1).lower() if summary_match else ""
        roles = {asset.role for asset in assets}
        if roles & {"continuation_source", "seamless_overlap_continuation"} and "video continuation" not in summary:
            findings.append(_finding("ERROR", "missing_task_relationship", "A continuation Video requires the official video continuation task relationship."))
        if "editing_source" in roles and "video editing" not in summary:
            findings.append(_finding("ERROR", "missing_task_relationship", "An editing-source Video requires the official video editing task relationship."))
        if roles & {"full_reuse", "partial_reuse"} and "audio reuse" not in summary:
            findings.append(_finding("ERROR", "missing_task_relationship", "Direct Audio reuse requires the official audio reuse task relationship."))
        if roles & {"music_beat_rhythm", "voice_timbre", "dialogue_spoken_content", "sound_audio_style", "general_audio"} and "audio reference" not in summary:
            findings.append(_finding("WARNING", "missing_task_relationship", "Guidance-only Audio normally requires the official audio reference task relationship."))
        if roles & {"first_frame_anchor", "keyframe_anchor", "last_frame_anchor", "storyboard_composition"} and "keyframe completion" not in summary:
            findings.append(_finding("WARNING", "missing_task_relationship", "A concrete Picture anchor normally requires keyframe completion."))
        if "seamless_overlap_continuation" in roles:
            normalized = main_text.lower()
            if "no replay" not in normalized or not any(phrase in normalized for phrase in ("continues forward", "continue forward", "advances forward", "monotonically forward")):
                findings.append(_finding("WARNING", "weak_overlap_handoff", "Seamless overlap should explicitly prohibit replay and continue forward beyond the overlap."))
        subject_speakers: defaultdict[int, set[int]] = defaultdict(set)
        for subject, speaker in re.findall(r"<Subject\s+(\d+)>\s*\(S(\d+)\)", text):
            subject_speakers[int(subject)].add(int(speaker))
        for subject, speakers in subject_speakers.items():
            if len(speakers) > 1:
                findings.append(_finding("WARNING", "speaker_consistency", f"Subject {subject} uses multiple speaker IDs: {sorted(speakers)}."))

    for line in _dialogue_lines(dialogue):
        dialogue_blocks = re.findall(r"<d>(?:\[[^\]]+\]\s*)?(.*?)</d>", text, flags=re.DOTALL)
        if not any(line in block for block in dialogue_blocks):
            findings.append(_finding("ERROR", "dialogue_changed", f"Exact dialogue was not preserved: {line}"))

    if not findings:
        findings.append(_finding("INFO", "structure_ok", "Required H3 structure and explicit invariants passed."))
    return ValidationResult(valid=not any(item.severity == "ERROR" for item in findings), findings=findings)


def validate_workspace_prompt(prompt: str, workspace: Workspace) -> ValidationResult:
    result = validate_prompt(prompt, workspace.mode, workspace.duration_seconds, workspace.assets, workspace.dialogue, workspace.shots)
    if workspace.mode != H3Mode.REF2VA:
        return result
    definitions_match = re.search(r"(?ms)^subject_definitions:\s*(.*?)(?=^summary:)", prompt)
    definitions = _mask_literal_text(definitions_match.group(1)) if definitions_match else ""
    subject_numbers = {subject.id: subject.number for subject in workspace.subjects}
    pictures = sorted((asset for asset in workspace.assets if asset.kind == AssetKind.IMAGE), key=lambda asset: (asset.order, asset.id))
    for picture_number, asset in enumerate(pictures, 1):
        if asset.role != "subject_identity":
            continue
        subject_number = subject_numbers.get(asset.subject_identity.subject_id)
        if subject_number is None:
            continue
        subject_match = re.search(
            rf"(?ms)^<Subject\s+{subject_number}>\s+is\b(.*?)(?=^<Subject\s+\d+>\s+is\b|^<Picture\s+\d+>|\Z)",
            definitions,
        )
        label = f"<Picture {picture_number}>"
        if subject_match is None or label not in subject_match.group(0):
            result.findings.append(_finding(
                "ERROR", "identity_reference_missing",
                f"<Subject {subject_number}> is missing identity provenance {label} in subject_definitions.",
            ))
    if any(item.severity == "ERROR" for item in result.findings):
        result.valid = False
        result.findings[:] = [item for item in result.findings if item.code != "structure_ok"]
    return result
