from __future__ import annotations

import json

import httpx
import pytest

from miniconstruct.api.routes import stream_generation_events
from miniconstruct.llm.client import LLMBackendError, OpenAICompatibleClient
from miniconstruct.llm.compatibility import build_generation_payload
from miniconstruct.models.api import GenerationRequest, LLMSettings


def settings(**overrides) -> LLMSettings:
    value = {"baseUrl": "http://seed.test/v1", "model": "writer", "timeoutSeconds": 2}
    value.update(overrides)
    return LLMSettings.model_validate(value)


def messages() -> list[dict]:
    return [{"role": "system", "content": "same prefix"}, {"role": "user", "content": "same request"}]


def test_seed_payload_omits_backend_default_and_preserves_messages():
    default, _ = build_generation_payload(settings(), messages(), stream=False)
    fixed, _ = build_generation_payload(settings(seed=3407), messages(), stream=False)
    assert "seed" not in default
    assert fixed["seed"] == 3407
    assert default["messages"] == fixed["messages"]


def test_seed_schema_validation_and_fixed_mode_requirement():
    assert settings(seedMode="fixed", fixedSeed=0).fixed_seed == 0
    with pytest.raises(ValueError):
        settings(seedMode="fixed")
    with pytest.raises(ValueError):
        settings(seed=2_147_483_648)


@pytest.mark.asyncio
async def test_supported_non_stream_seed_remains_effective():
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["seed"] == 3407
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    async with OpenAICompatibleClient(settings(seed=3407), httpx.MockTransport(handler)) as client:
        assert await client.generate(messages()) == "ok"
        assert client.effective_seed == 3407


@pytest.mark.asyncio
async def test_unsupported_seed_retries_once_without_seed():
    OpenAICompatibleClient._unsupported_seed_endpoints.clear()
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        if "seed" in body:
            return httpx.Response(400, json={"error": {"message": "unknown parameter: seed"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    async with OpenAICompatibleClient(settings(seed=7), httpx.MockTransport(handler)) as client:
        assert await client.generate(messages()) == "ok"
        assert client.seed_unsupported
        assert client.effective_seed is None
    assert ["seed" in body for body in seen] == [True, False]
    assert seen[0]["messages"] == seen[1]["messages"]


@pytest.mark.asyncio
async def test_non_stream_optional_only_rejection_retries_once():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        if "chat_template_kwargs" in body:
            return httpx.Response(422, text="unknown field")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    async with OpenAICompatibleClient(settings(baseUrl="http://127.0.0.1:8888/v1"), httpx.MockTransport(handler)) as client:
        assert await client.generate(messages()) == "ok"
        assert client.effective_seed is None
    assert ["chat_template_kwargs" in body for body in seen] == [True, False]


@pytest.mark.asyncio
async def test_non_stream_retries_seed_then_optional_control():
    OpenAICompatibleClient._unsupported_seed_endpoints.clear()
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        if len(seen) == 1:
            return httpx.Response(400, text="unknown parameter: seed")
        if len(seen) == 2:
            return httpx.Response(422, text="unknown parameter: chat_template_kwargs")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    async with OpenAICompatibleClient(settings(baseUrl="http://127.0.0.1:8888/v1", seed=7), httpx.MockTransport(handler)) as client:
        assert await client.generate(messages()) == "ok"
        assert client.effective_seed is None
    assert [("seed" in body, "chat_template_kwargs" in body) for body in seen] == [(True, True), (False, True), (False, False)]
    assert seen[0]["messages"] == seen[1]["messages"] == seen[2]["messages"]


@pytest.mark.asyncio
async def test_non_stream_retries_optional_then_seed():
    OpenAICompatibleClient._unsupported_seed_endpoints.clear()
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        if len(seen) == 1:
            return httpx.Response(422, text="unknown parameter: chat_template_kwargs")
        if len(seen) == 2:
            return httpx.Response(400, text="unknown parameter: seed")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    async with OpenAICompatibleClient(settings(baseUrl="http://127.0.0.1:8888/v1", seed=7), httpx.MockTransport(handler)) as client:
        assert await client.generate(messages()) == "ok"
        assert client.effective_seed is None
    assert [("seed" in body, "chat_template_kwargs" in body) for body in seen] == [(True, True), (True, False), (False, False)]


def _sse_success() -> bytes:
    return b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n'


@pytest.mark.asyncio
async def test_stream_seed_only_rejection_retries_without_seed_and_reports_no_effective_seed():
    OpenAICompatibleClient._unsupported_seed_endpoints.clear()
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        if "seed" in body:
            return httpx.Response(400, text="unknown parameter: seed")
        return httpx.Response(200, content=_sse_success())

    async with OpenAICompatibleClient(settings(seed=7), httpx.MockTransport(handler)) as client:
        kinds = [event.kind async for event in client.stream_events(messages())]
        assert client.effective_seed is None
    assert kinds == ["seed_unsupported", "content"]
    assert ["seed" in body for body in seen] == [True, False]


@pytest.mark.asyncio
async def test_stream_optional_only_rejection_retries_once_without_stream_options():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        if "stream_options" in body:
            return httpx.Response(400, text="unknown parameter: stream_options")
        return httpx.Response(200, content=_sse_success())

    async with OpenAICompatibleClient(settings(baseUrl="http://stream-options.test/v1"), httpx.MockTransport(handler)) as client:
        kinds = [event.kind async for event in client.stream_events(messages())]
        assert client.effective_seed is None
    assert kinds == ["compatibility_fallback", "content"]
    assert ["stream_options" in body for body in seen] == [True, False]


@pytest.mark.asyncio
async def test_stream_retries_seed_then_optional_control_with_messages_unchanged():
    OpenAICompatibleClient._unsupported_seed_endpoints.clear()
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        if len(seen) == 1:
            return httpx.Response(400, text="unknown parameter: seed")
        if len(seen) == 2:
            return httpx.Response(400, text="unknown parameter: stream_options")
        return httpx.Response(200, content=_sse_success())

    async with OpenAICompatibleClient(settings(baseUrl="http://two-step.test/v1", seed=7), httpx.MockTransport(handler)) as client:
        kinds = [event.kind async for event in client.stream_events(messages())]
        assert client.effective_seed is None
    assert kinds == ["seed_unsupported", "compatibility_fallback", "content"]
    assert [("seed" in body, "stream_options" in body) for body in seen] == [(True, True), (False, True), (False, False)]
    assert seen[0]["messages"] == seen[1]["messages"] == seen[2]["messages"]


@pytest.mark.asyncio
async def test_stream_unrelated_400_does_not_retry():
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400, text="bad model")

    async with OpenAICompatibleClient(settings(baseUrl="http://stream-unrelated.test/v1", seed=7), httpx.MockTransport(handler)) as client:
        with pytest.raises(LLMBackendError, match="HTTP 400"):
            _ = [event async for event in client.stream_events(messages())]
    assert calls == 1


@pytest.mark.asyncio
async def test_unrelated_400_never_retries_seed_request():
    OpenAICompatibleClient._unsupported_seed_endpoints.clear()
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400, text="bad model")

    async with OpenAICompatibleClient(settings(seed=7), httpx.MockTransport(handler)) as client:
        with pytest.raises(LLMBackendError, match="HTTP 400"):
            await client.generate(messages())
    assert calls == 1


class SeedStreamClient:
    def __init__(self, settings: LLMSettings, observed: list[int | None]) -> None:
        self.settings = settings
        self.observed = observed

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def stream_generate(self, _):
        self.observed.append(self.settings.seed)
        yield "prompt"


class FallbackSeedStreamClient(SeedStreamClient):
    effective_seed = None

    async def stream_events(self, _):
        yield type("Event", (), {"kind": "seed_unsupported", "text": "", "usage": {}})()
        yield type("Event", (), {"kind": "content", "text": "prompt", "usage": {}})()


@pytest.mark.asyncio
async def test_streaming_variations_keep_resolved_seeds_stable(workspace_factory):
    workspace = workspace_factory().model_copy(update={"variations": 2})
    request = GenerationRequest.model_validate({
        "workspace": workspace.model_dump(mode="json", by_alias=True),
        "llm": {"baseUrl": "http://route-seed.test/v1", "model": "writer", "seedMode": "random"},
        "resolvedSeeds": [101, 202],
    })
    observed: list[int | None] = []
    events = [raw async for raw in stream_generation_events(request, lambda setting: SeedStreamClient(setting, observed))]
    assert observed == [101, 202]
    completed = [json.loads(raw.splitlines()[1][6:]) for raw in events if raw.startswith("event: complete")]
    assert [item["seed"] for item in completed] == [101, 202]


@pytest.mark.asyncio
async def test_streaming_seed_fallback_reports_backend_default(workspace_factory):
    request = GenerationRequest.model_validate({
        "workspace": workspace_factory().model_dump(mode="json", by_alias=True),
        "llm": {"baseUrl": "http://route-fallback.test/v1", "model": "writer"},
        "resolvedSeeds": [101],
    })
    events = [raw async for raw in stream_generation_events(request, lambda setting: FallbackSeedStreamClient(setting, []))]
    complete = next(json.loads(raw.splitlines()[1][6:]) for raw in events if raw.startswith("event: complete"))
    assert complete["seed"] is None
    assert complete["metrics"]["seed"] is None
