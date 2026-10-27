from __future__ import annotations

from copy import deepcopy
from typing import Any
from urllib.parse import urlparse

from miniconstruct.models.api import EndpointSource, LLMSettings, ReasoningMode


DIRECT_OUTPUT_INSTRUCTION = (
    "Do not use extended reasoning. Output the finished H3 prompt directly."
)


def supports_thinking_kwargs(settings: LLMSettings) -> bool:
    """Return true only for an explicitly identified/local Unsloth Studio endpoint."""
    if settings.endpoint.source == EndpointSource.UNSLOTH_STUDIO:
        return True
    parsed = urlparse(settings.endpoint.base_url)
    return parsed.hostname in {"127.0.0.1", "localhost", "::1"} and parsed.port == 8888


def prepare_messages(
    messages: list[dict[str, Any]],
    mode: ReasoningMode,
    response_instruction: str | None = DIRECT_OUTPUT_INSTRUCTION,
) -> list[dict[str, Any]]:
    prepared = deepcopy(messages)
    if mode != ReasoningMode.OFF or response_instruction is None:
        return prepared
    for message in prepared:
        if message.get("role") == "system" and isinstance(message.get("content"), str):
            message["content"] += f"\n\n## Response behavior\n\n{response_instruction}"
            break
    return prepared


def build_generation_payload(
    settings: LLMSettings,
    messages: list[dict[str, Any]],
    *,
    stream: bool,
    include_optional: bool = True,
    include_seed: bool = True,
    response_instruction: str | None = DIRECT_OUTPUT_INSTRUCTION,
    response_format: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], set[str]]:
    payload: dict[str, Any] = {
        "model": settings.selected_model_id,
        "messages": prepare_messages(messages, settings.reasoning_mode, response_instruction),
        "temperature": settings.temperature,
        "max_tokens": settings.max_tokens,
        "stream": stream,
    }
    if response_format is not None:
        payload["response_format"] = deepcopy(response_format)
    optional_fields: set[str] = set()
    if include_seed and settings.seed is not None:
        payload["seed"] = settings.seed
        optional_fields.add("seed")
    if stream and include_optional:
        payload["stream_options"] = {"include_usage": True}
        optional_fields.add("stream_options")
    if (
        include_optional
        and settings.reasoning_mode != ReasoningMode.DEFAULT
        and supports_thinking_kwargs(settings)
    ):
        enabled = settings.reasoning_mode == ReasoningMode.ON
        payload["chat_template_kwargs"] = {
            "thinking": enabled,
            "enable_thinking": enabled,
        }
        optional_fields.add("chat_template_kwargs")
    return payload, optional_fields
