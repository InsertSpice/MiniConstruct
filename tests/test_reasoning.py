from __future__ import annotations

import json

import httpx
import pytest

from miniconstruct.llm.client import OpenAICompatibleClient
from miniconstruct.llm.compatibility import DIRECT_OUTPUT_INSTRUCTION, build_generation_payload
from miniconstruct.models.api import LLMSettings


def config(mode: str = "off", *, base_url: str = "http://127.0.0.1:8888/v1", source: str = "manual") -> LLMSettings:
    return LLMSettings.model_validate({
        "endpoint": {
            "id": "test", "displayName": "Test", "baseUrl": base_url, "source": source,
        },
        "modelId": "writer", "reasoningMode": mode,
    })


def messages() -> list[dict]:
    return [{"role": "system", "content": "H3 rules"}, {"role": "user", "content": "Write it"}]


def test_reasoning_modes_and_provider_isolation():
    off, _ = build_generation_payload(config("off"), messages(), stream=True)
    assert off["chat_template_kwargs"] == {"thinking": False, "enable_thinking": False}
    assert DIRECT_OUTPUT_INSTRUCTION in off["messages"][0]["content"]

    default, _ = build_generation_payload(config("default"), messages(), stream=True)
    assert "chat_template_kwargs" not in default
    assert DIRECT_OUTPUT_INSTRUCTION not in default["messages"][0]["content"]

    on, _ = build_generation_payload(config("on", source="unsloth_studio"), messages(), stream=True)
    assert on["chat_template_kwargs"] == {"thinking": True, "enable_thinking": True}

    unrelated, _ = build_generation_payload(
        config("off", base_url="http://127.0.0.1:1234/v1"), messages(), stream=True
    )
    assert "chat_template_kwargs" not in unrelated


@pytest.mark.asyncio
async def test_reasoning_deltas_and_inline_tags_never_enter_final_content():
    body = (
        b'data: {"choices":[{"delta":{"reasoning_content":"hidden one"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"<thi"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"nk>hidden two</think>final "}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"prompt"}}]}\n\n'
        b'data: {"choices":[],"usage":{"prompt_tokens":123,"completion_tokens":9,"total_tokens":132}}\n\n'
        b'data: [DONE]\n\n'
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    async with OpenAICompatibleClient(config(), httpx.MockTransport(handler)) as client:
        events = [event async for event in client.stream_events(messages())]
    assert "".join(event.text for event in events if event.kind == "reasoning") == "hidden onehidden two"
    assert "".join(event.text for event in events if event.kind == "content") == "final prompt"
    assert next(event.usage for event in events if event.kind == "usage") == {
        "prompt_tokens": 123, "completion_tokens": 9, "total_tokens": 132,
    }


@pytest.mark.asyncio
async def test_optional_reasoning_rejection_retries_without_optional_fields():
    payloads = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        payloads.append(payload)
        if "chat_template_kwargs" in payload:
            return httpx.Response(422, text="unknown field")
        return httpx.Response(
            200,
            content=b'data: {"choices":[{"delta":{"content":"final"}}]}\n\ndata: [DONE]\n\n',
        )

    async with OpenAICompatibleClient(config(), httpx.MockTransport(handler)) as client:
        events = [event async for event in client.stream_events(messages())]
    assert len(payloads) == 2
    assert "chat_template_kwargs" in payloads[0]
    assert "chat_template_kwargs" not in payloads[1]
    assert "stream_options" not in payloads[1]
    assert any(event.kind == "compatibility_fallback" for event in events)
    assert "".join(event.text for event in events if event.kind == "content") == "final"
