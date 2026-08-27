from __future__ import annotations

import httpx
import pytest

from miniconstruct.llm.client import LLMBackendError, OpenAICompatibleClient
from miniconstruct.models.api import LLMSettings


def config(**overrides):
    data = {"baseUrl": "http://local.test/v1", "model": "writer", "timeoutSeconds": 2}
    data.update(overrides)
    return LLMSettings.model_validate(data)


@pytest.mark.asyncio
async def test_model_discovery_and_generation():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "z"}, {"id": "a"}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "clean prompt"}}]})
    async with OpenAICompatibleClient(config(), httpx.MockTransport(handler)) as client:
        assert await client.list_models() == ["a", "z"]
        assert await client.generate([{"role": "user", "content": "go"}]) == "clean prompt"


@pytest.mark.asyncio
async def test_connection_error():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="backend broke")
    async with OpenAICompatibleClient(config(), httpx.MockTransport(handler)) as client:
        with pytest.raises(LLMBackendError, match="HTTP 500"):
            await client.list_models()


@pytest.mark.asyncio
async def test_timeout():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("late", request=request)
    async with OpenAICompatibleClient(config(), httpx.MockTransport(handler)) as client:
        with pytest.raises(LLMBackendError, match="timed out"):
            await client.list_models()


@pytest.mark.asyncio
async def test_malformed_backend_response():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})
    async with OpenAICompatibleClient(config(), httpx.MockTransport(handler)) as client:
        with pytest.raises(LLMBackendError, match="Malformed"):
            await client.generate([{"role": "user", "content": "go"}])


@pytest.mark.asyncio
async def test_optional_api_key_header():
    observed = {}
    def handler(request: httpx.Request) -> httpx.Response:
        observed["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"data": []})
    async with OpenAICompatibleClient(config(apiKey="secret"), httpx.MockTransport(handler)) as client:
        await client.list_models()
    assert observed["auth"] == "Bearer secret"
