from __future__ import annotations

from copy import deepcopy

from miniconstruct.h3.builder import AssembledPrompt, assemble_prompt
from miniconstruct.models.api import RepairRequest, ValidationFinding


REPAIR_POLICY = """You are performing one narrow MiniMax H3 format-repair pass.
Repair only the listed structural or syntactic H3 failures in the supplied current output.
Preserve the current prompt's creative and semantic content as strictly as possible: do not add, remove, merge, split, or renumber shots merely to match the workspace shot-count setting; do not rewrite the creative request; do not alter identities, reference roles, exact dialogue, music, or unrelated valid sections.
Workspace settings, including Creative Controls, and reference material remain context for correct H3 syntax, but they are not authority to reconcile an intentionally edited output.
Make the minimum textual changes required for the listed structural failures. Return only the repaired full H3 prompt as plain text, with no Markdown fences or commentary."""


def assemble_repair_prompt(
    request: RepairRequest,
    findings: list[ValidationFinding],
) -> AssembledPrompt:
    base = assemble_prompt(request.workspace, request.llm.supports_vision)
    messages = [
        deepcopy(message)
        for message in base.messages
        if message.get("role") == "system"
        and not str(message.get("content", "")).startswith("## Generation policy")
    ]
    messages.append({"role": "system", "content": f"## Format repair policy\n\n{REPAIR_POLICY}"})

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
        inspector_text="\n\n".join(str(message.get("content", "")) for message in messages),
        warnings=base.warnings,
    )
