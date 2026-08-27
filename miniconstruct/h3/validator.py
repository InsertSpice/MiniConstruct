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


def _finding(severity: str, code: str, message: str) -> ValidationFinding:
    return ValidationFinding(severity=severity, code=code, message=message)  # type: ignore[arg-type]


def _asset_label_limits(assets: list[ReferenceAsset]) -> dict[str, int]:
    return {
        "Picture": sum(asset.kind == AssetKind.IMAGE for asset in assets),
        "Video": sum(asset.kind == AssetKind.VIDEO for asset in assets),
        "Audio": sum(asset.kind == AssetKind.AUDIO for asset in assets),
    }


def _dialogue_lines(dialogue: str) -> list[str]:
    lines: list[str] = []
    for raw in dialogue.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        _, separator, text = raw.partition(":")
        lines.append(text.strip() if separator else raw)
    return [line for line in lines if line]


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
    if not shots or int(shots[0].group(1)) != 1:
        findings.append(_finding("ERROR", "missing_shot_1", "The main description must begin with [Shot 1]."))
    else:
        if shots[0].group(2) is not None:
            findings.append(_finding("ERROR", "shot_1_timestamp", "Shot 1 must not have a timestamp."))
        numbers = [int(match.group(1)) for match in shots]
        if numbers != list(range(1, len(numbers) + 1)):
            findings.append(_finding("ERROR", "shot_sequence", "Shot numbers must be sequential from Shot 1."))
        if expected_shots is not None and len(shots) != expected_shots:
            findings.append(_finding("ERROR", "shot_count", f"Requested {expected_shots} shots, but the output contains {len(shots)}."))
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
    return validate_prompt(prompt, workspace.mode, workspace.duration_seconds, workspace.assets, workspace.dialogue, workspace.shots)
