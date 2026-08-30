from __future__ import annotations

from copy import deepcopy
import re

from miniconstruct.h3.builder import AssembledPrompt, assemble_prompt
from miniconstruct.models.api import RevisionRequest, RevisionSelection


REVISION_POLICY = """You are performing one selective edit inside an existing MiniMax H3 prompt.
Return ONLY replacement text for the selected excerpt. Do not return the full prompt, explanations, a response heading such as "Replacement:", or Markdown fences.
Text outside <MINICONSTRUCT_SELECTION> is immutable and is supplied only for continuity context. The selection markers exist only in this request: do not return them.
Preserve existing Subject, Picture, Video, Audio, speaker, shot, and timeline identities unless the selected text itself requires a local correction.
The Exact Dialogue field is authoritative: preserve its wording and punctuation verbatim, including every selected <d>...</d> passage.
The explicit Revision Request is authoritative within the selected excerpt and may intentionally override workspace Creative Controls there.
Fit the replacement naturally between the immutable surrounding text. Do not create unrelated top-level sections or renumber unrelated shots."""


def splice_revision(selection: RevisionSelection, replacement: str) -> str:
    return selection.before_selection + replacement + selection.after_selection


def validate_replacement(selection: RevisionSelection, replacement: str) -> str | None:
    if not replacement.strip():
        return "The model returned an empty replacement."
    if "```" in replacement:
        return "The model returned Markdown fences instead of replacement text."
    if "MINICONSTRUCT_SELECTION" in replacement:
        return "The model returned internal selection markers instead of only replacement text."
    if re.match(r"^\s*(?:here(?:'s| is) (?:the )?(?:revision|replacement)|replacement(?: text)?)\s*:", replacement, re.IGNORECASE):
        return "The model returned commentary instead of only replacement text."
    if replacement == selection.full_prompt or selection.full_prompt in replacement:
        return "The model returned the entire prompt instead of replacement text."
    return None


def assemble_revision_prompt(request: RevisionRequest) -> AssembledPrompt:
    base = assemble_prompt(request.workspace, request.llm.supports_vision)
    messages = [
        deepcopy(message)
        for message in base.messages
        if message.get("role") == "system"
        and not str(message.get("content", "")).startswith("## Generation policy")
    ]
    messages.append({"role": "system", "content": f"## Selective revision policy\n\n{REVISION_POLICY}"})

    original_user = base.messages[-1].get("content")
    if isinstance(original_user, list):
        original_user_text = str(original_user[0].get("text", ""))
    else:
        original_user_text = str(original_user)

    selection = request.selection
    user_text = (
        "ORIGINAL AUTHORITATIVE WORKSPACE MATERIAL:\n"
        f"{original_user_text}\n\n"
        "REVISION REQUEST (authoritative):\n"
        f"{request.instruction}\n\n"
        "CURRENT H3 PROMPT (selection markers are request-only and must not be returned):\n"
        f"{selection.before_selection}<MINICONSTRUCT_SELECTION>\n"
        f"{selection.selected_text}\n"
        f"</MINICONSTRUCT_SELECTION>{selection.after_selection}\n\n"
        "Return only the exact replacement text inside MINICONSTRUCT_SELECTION."
    )
    if isinstance(original_user, list):
        image_parts = deepcopy(original_user[1:])
        messages.append({"role": "user", "content": [{"type": "text", "text": user_text}, *image_parts]})
    else:
        messages.append({"role": "user", "content": user_text})
    return AssembledPrompt(
        messages=messages,
        inspector_text="\n\n".join(str(message.get("content", "")) for message in messages),
        warnings=base.warnings,
    )
