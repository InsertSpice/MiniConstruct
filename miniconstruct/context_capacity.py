"""Conservative, tokenizer-free request capacity protection."""
from __future__ import annotations

import math
from typing import Any

from miniconstruct.llm.client import LLMBackendError
from miniconstruct.models.api import LLMSettings

CHARS_PER_ESTIMATED_TOKEN = 3
VISION_IMAGE_TOKEN_RESERVE = 2048
CAPACITY_SAFETY_FRACTION = 0.05
CAPACITY_MINIMUM_SAFETY_TOKENS = 512
# The smallest allowed Max output tokens value, not the requested ceiling.
H3_MINIMUM_COMPLETION_HEADROOM_TOKENS = 128
H3_COMPLETION_BUDGET_WARNING = (
    "Requested Max output tokens exceeds the estimated remaining context. "
    "The backend may limit or truncate the completion."
)
STORY_CAPACITY_WARNING = (
    "Estimated Story request size exceeds the configured Backend context capacity. "
    "This estimate is approximate; the request was sent and the provider remains authoritative."
)


def estimate_request_tokens(messages: list[dict[str, Any]]) -> tuple[int, int, int]:
    text_chars = 0
    image_count = 0

    def visit(value: Any) -> None:
        nonlocal text_chars, image_count
        if isinstance(value, str):
            text_chars += len(value)
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            if value.get("type") == "image_url":
                image_count += 1
            for key, item in value.items():
                if key == "url" and isinstance(item, str) and item.startswith("data:"):
                    continue
                visit(item)

    visit(messages)
    return text_chars, image_count, math.ceil(text_chars / CHARS_PER_ESTIMATED_TOKEN) + image_count * VISION_IMAGE_TOKEN_RESERVE


def request_capacity_diagnostics(messages: list[dict[str, Any]], settings: LLMSettings) -> dict[str, int | bool | str | None]:
    """Estimate the already-loaded backend's capacity without contacting it."""
    text_chars, image_count, estimated_input = estimate_request_tokens(messages)
    if settings.context_window is None:
        return {"contextWindow": None, "estimatedInputTokens": estimated_input,
                "reservedCompletionTokens": settings.max_tokens, "textCharacters": text_chars,
                "imageCount": image_count, "requestedMaxOutputTokens": settings.max_tokens,
                "safetyTokens": None, "estimatedCompletionHeadroom": None,
                "completionBudgetFits": None, "completionBudgetWarning": None}
    safety = max(CAPACITY_MINIMUM_SAFETY_TOKENS, math.ceil(settings.context_window * CAPACITY_SAFETY_FRACTION))
    headroom = max(0, settings.context_window - estimated_input - safety)
    budget_fits = settings.max_tokens <= headroom
    return {"contextWindow": settings.context_window, "estimatedInputTokens": estimated_input,
            "reservedCompletionTokens": settings.max_tokens, "textCharacters": text_chars,
            "imageCount": image_count, "safetyTokens": safety,
            "requiredTokens": estimated_input + settings.max_tokens + safety,
            "requestedMaxOutputTokens": settings.max_tokens,
            "estimatedCompletionHeadroom": headroom, "completionBudgetFits": budget_fits,
            "completionBudgetWarning": None if budget_fits else H3_COMPLETION_BUDGET_WARNING}


def preflight_request_capacity(messages: list[dict[str, Any]], settings: LLMSettings, *, purpose: str = "Story planning") -> dict[str, int | bool | str | None]:
    """Return diagnostics; only H3's minimum completion headroom is locally enforced."""
    diagnostic = request_capacity_diagnostics(messages, settings)
    if settings.context_window is None:
        return diagnostic
    if purpose == "Story planning" and diagnostic["requiredTokens"] > settings.context_window:
        diagnostic["completionBudgetWarning"] = (
            f"{STORY_CAPACITY_WARNING} Estimated input {diagnostic['estimatedInputTokens']:,} + "
            f"requested output {settings.max_tokens:,} + safety {diagnostic['safetyTokens']:,} > "
            f"configured capacity {settings.context_window:,} tokens."
        )
    if purpose != "Story planning" and diagnostic["estimatedCompletionHeadroom"] < H3_MINIMUM_COMPLETION_HEADROOM_TOKENS:
        raise LLMBackendError(
            f"The configured backend context capacity is too small for this {purpose} request "
            f"(estimated input {diagnostic['estimatedInputTokens']:,} tokens + {diagnostic['safetyTokens']:,} safety tokens "
            f"leaves approximately {diagnostic['estimatedCompletionHeadroom']:,} tokens for completion; "
            f"at least {H3_MINIMUM_COMPLETION_HEADROOM_TOKENS:,} estimated tokens are needed). "
            "Set the capacity to the backend's actual loaded context size or reduce request content; "
            "MiniConstruct does not resize the backend.", 400,
            category="h3_context_capacity",
            safe_message="The estimated H3 input leaves too little room for completion in the configured backend context. "
                         "Check the backend's loaded size or reduce request content.")
    return diagnostic
