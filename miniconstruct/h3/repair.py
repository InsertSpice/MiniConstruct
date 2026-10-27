from __future__ import annotations

from copy import deepcopy
import re

from miniconstruct.h3.builder import (
    AssembledPrompt,
    assemble_prompt,
    inspector_text,
    instruction_layers,
    system_message_content,
)
from miniconstruct.models.api import RepairRequest, ValidationFinding
from miniconstruct.models.workspace import AssetKind, H3Mode, Workspace
from miniconstruct.models.subject_numbers import subject_numbers as technical_subject_numbers
from miniconstruct.h3.prompt_text import mask_literal_text


REPAIR_POLICY = """You are performing one narrow MiniMax H3 format-repair pass.
Repair only the listed structural or syntactic H3 failures in the supplied current output.
Preserve the current prompt's creative and semantic content as strictly as possible: do not add, remove, merge, split, or renumber shots merely to match the workspace shot-count setting; do not rewrite the creative request; do not alter stable Subject identity sets, reference-sheet meaning, Character Comparison / Scale relationships, reference roles, exact spoken payload, music, or unrelated valid sections. Each utterance uses <d>[Language] exact spoken payload</d>; the one leading supported [Language] tag is required H3 metadata and is not spoken. You may add or correct that syntax tag without changing the exact payload. Never repair changed words, punctuation, quote characters, translations, or audible annotations by rewriting the dialogue. The canonical current Workspace media allowlist outranks labels in the supplied H3 text: a nonexistent Picture, Video or Audio label is invalid even when already present. Remove or correct it when repairing that structural finding; never invent a replacement label.
Workspace settings, including Creative Interpretation and Creative Controls, and reference material remain context for correct H3 syntax, but they are not authority to reconcile an intentionally edited output.
Make the minimum textual changes required for the listed structural failures. Return only the repaired full H3 prompt as plain text, with no Markdown fences or commentary."""

REFERENCE_RECORD_START_RE = re.compile(
    r"(?m)^<(?P<kind>Subject|Picture|Video|Audio)\s+(?P<number>\d+)>(?=\s|:|$)"
)


def repair_identity_provenance(prompt: str, workspace: Workspace, findings: list[ValidationFinding]) -> str:
    """Normalize identity-only provenance when canonical Workspace authority is unique."""
    if workspace.mode != H3Mode.REF2VA or not any(
        item.code in {"identity_reference_missing", "identity_picture_definition", "identity_picture_retention"}
        and item.severity == "ERROR" for item in findings
    ):
        return prompt
    sections = list(re.finditer(r"(?m)^(subject_definitions|summary):", prompt))
    if len([match for match in sections if match.group(1) == "subject_definitions"]) != 1:
        return prompt
    if len([match for match in sections if match.group(1) == "summary"]) != 1:
        return prompt
    definitions_start = next(match.end() for match in sections if match.group(1) == "subject_definitions")
    definitions_end = next(match.start() for match in sections if match.group(1) == "summary")
    if definitions_start >= definitions_end:
        return prompt
    definitions = prompt[definitions_start:definitions_end]
    definitions_structure = mask_literal_text(definitions)
    headers = list(REFERENCE_RECORD_START_RE.finditer(definitions_structure))
    subjects_by_id = technical_subject_numbers(workspace.subjects)
    if len(subjects_by_id) != len(workspace.subjects) or len(set(subjects_by_id.values())) != len(subjects_by_id):
        return prompt
    pictures = sorted((asset for asset in workspace.assets if asset.kind == AssetKind.IMAGE), key=lambda asset: (asset.order, asset.id))
    edits: list[tuple[int, int, str]] = []
    remove_definition_spans: list[tuple[int, int]] = []
    for subject_id, subject_number in subjects_by_id.items():
        identity_pictures = [
            (number, asset) for number, asset in enumerate(pictures, 1)
            if asset.role == "subject_identity" and asset.subject_identity.subject_id == subject_id
        ]
        if not identity_pictures:
            continue
        matching = [header for header in headers if header.group("kind") == "Subject" and int(header.group("number")) == subject_number]
        if len(matching) != 1:
            continue
        header = matching[0]
        next_header = next((item for item in headers if item.start() > header.start()), None)
        block_end = next_header.start() if next_header else len(definitions)
        block = definitions[header.start():block_end]
        definition_start = re.match(rf"<Subject\s+{subject_number}>\s+is\b", block)
        if definition_start is None or not block[definition_start.end():].strip():
            continue
        masked = mask_literal_text(block)
        # Presence follows validation's logical-record scope. Insertion below
        # still uses the opening sentence as the preferred canonical location.
        missing = [(number, asset) for number, asset in identity_pictures if f"<Picture {number}>" not in masked]
        # A separate Subject-like line inside the block makes its ownership unclear.
        if re.search(r"(?m)^<Subject\s+\d+>", block[len(header.group()):]):
            continue
        # Provenance is part of the Subject definition sentence. Prefer an existing
        # narrative name clause; otherwise append a short source clause to its final
        # sentence. Do not create a second definition record.
        if missing:
            unique = len(missing) == 1
            if not unique:
                # Multiple sources jointly defining one Subject need model judgment
                # unless every source is already mentioned in the definition.
                continue
            number, _asset = missing[0]
            raw = block.rstrip()
            sentence_end = re.search(r"[.!?](?=\s|$)", raw)
            if sentence_end:
                new_block = raw[:sentence_end.start()] + f", shown in <Picture {number}>" + raw[sentence_end.start():]
            else:
                new_block = raw + f", shown in <Picture {number}>."
            edits.append((definitions_start + header.start(), definitions_start + header.start() + len(block.rstrip()), new_block))

    # Strip only standalone records/retention lines for configured identity-only
    # Pictures. A current asset has one canonical role, so these cannot be frame or
    # composition anchors at the same time.
    identity_numbers = {
        number for number, asset in enumerate(pictures, 1) if asset.role == "subject_identity"
    }
    def identity_record_spans(section: str, base: int) -> list[tuple[int, int]]:
        lines = list(re.finditer(r"(?m)^.*(?:\n|$)", section))
        structural_section = mask_literal_text(section)
        spans: list[tuple[int, int]] = []
        index = 0
        while index < len(lines):
            line = lines[index]
            structural_line = structural_section[line.start():line.end()]
            match = REFERENCE_RECORD_START_RE.match(structural_line)
            if match and match.group("kind") == "Picture" and int(match.group("number")) in identity_numbers:
                end_index = index + 1
                while end_index < len(lines):
                    following = lines[end_index].group()
                    structural_following = structural_section[lines[end_index].start():lines[end_index].end()]
                    if not following.strip() or REFERENCE_RECORD_START_RE.match(structural_following):
                        break
                    end_index += 1
                end = lines[end_index - 1].end()
                spans.append((base + line.start(), base + end))
                index = end_index
            else:
                index += 1
        return spans

    definition_section = prompt[definitions_start:definitions_end]
    remove_definition_spans.extend(identity_record_spans(definition_section, definitions_start))
    retention_match = re.search(r"(?ms)^retention_analysis:\s*(.*?)(?=^detailed_description:)", prompt)
    if retention_match:
        body_start = retention_match.start(1)
        remove_definition_spans.extend(identity_record_spans(retention_match.group(1), body_start))

    actions = [(start, end, "") for start, end in remove_definition_spans]
    actions.extend(edits)
    for start, end, replacement in sorted(actions, reverse=True):
        prompt = prompt[:start] + replacement + prompt[end:]
    return prompt


def assemble_repair_prompt(
    request: RepairRequest,
    findings: list[ValidationFinding],
) -> AssembledPrompt:
    base = assemble_prompt(request.prompt_workspace, request.llm.supports_vision, request.narrative_subjects, request.continuity_context)
    layers = [
        layer for layer in instruction_layers(request.prompt_workspace, request.llm.supports_vision, request.narrative_subjects, request.continuity_context)
        if layer[0] != "Generation policy"
    ]
    layers.append(("Format repair policy", REPAIR_POLICY))
    messages = [{"role": "system", "content": system_message_content(layers)}]

    original_user = base.messages[-1].get("content")
    if isinstance(original_user, list):
        original_user_text = str(original_user[0].get("text", ""))
    else:
        original_user_text = str(original_user)
    failure_text = "\n".join(
        f"- {item.code}: {item.message}" for item in findings
    ) or "- No structural error was supplied. Preserve the current prompt."
    user_text = (
        "ORIGINAL AUTHORITATIVE WORKSPACE MATERIAL:\n"
        f"{original_user_text}\n\n"
        "STRUCTURAL FAILURES TO REPAIR:\n"
        f"{failure_text}\n\n"
        "CURRENT H3 PROMPT TO REPAIR:\n"
        f"{request.prompt}"
    )
    if isinstance(original_user, list):
        messages.append({"role": "user", "content": [{"type": "text", "text": user_text}, *deepcopy(original_user[1:])]})
    else:
        messages.append({"role": "user", "content": user_text})
    return AssembledPrompt(
        messages=messages,
        inspector_text=inspector_text(layers, user_text),
        warnings=base.warnings,
    )
