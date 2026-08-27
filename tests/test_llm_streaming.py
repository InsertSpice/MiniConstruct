from __future__ import annotations

import asyncio

import httpx
import pytest

from miniconstruct.llm.client import LLMBackendError, OpenAICompatibleClient
from miniconstruct.models.api import LLMSettings


def config() -> LLMSettings:
    return LLMSettings.model_validate(
        {"baseUrl": "http://local.test/v1", "model": "writer", "timeoutSeconds": 2}
    )


@pytest.mark.asyncio
async def test_standard_sse_multiple_deltas_done_and_harmless_chunks():
    observed = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["body"] = request.content
        observed["read_timeout"] = request.extensions["timeout"]["read"]
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                b'data: {"id":"x","choices":[{"delta":{"role":"assistant"}}]}\n\n'
                b'data: not-json\n\n'
                b'data: {"choices":[{"delta":{"content":"subject_"}}]}\n\n'
                b'data: {"choices":[{"delta":{"content":"definitions:"},"finish_reason":null}]}\n\n'
                b'data: [DONE]\n\n'
                b'data: {"choices":[{"delta":{"content":"ignored"}}]}\n\n'
            ),
        )

    async with OpenAICompatibleClient(config(), httpx.MockTransport(handler)) as client:
        deltas = [delta async for delta in client.stream_generate([{"role": "user", "content": "go"}])]

    assert deltas == ["subject_", "definitions:"]
    assert "".join(deltas) == "subject_definitions:"
    assert b'"stream":true' in observed["body"]
    assert observed["read_timeout"] is None


@pytest.mark.asyncio
async def test_stream_http_error_before_first_token():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="not ready")

    async with OpenAICompatibleClient(config(), httpx.MockTransport(handler)) as client:
        with pytest.raises(LLMBackendError, match="HTTP 503"):
            _ = [delta async for delta in client.stream_generate([{"role": "user", "content": "go"}])]


class FailingStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.closed = False

    async def __aiter__(self):
        yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
        raise httpx.ReadError("backend disconnected")

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_failure_after_partial_output_closes_stream():
    stream = FailingStream()

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    received = []
    async with OpenAICompatibleClient(config(), httpx.MockTransport(handler)) as client:
        with pytest.raises(LLMBackendError, match="ended unexpectedly"):
            async for delta in client.stream_generate([{"role": "user", "content": "go"}]):
                received.append(delta)
    assert received == ["partial"]
    assert stream.closed


class WaitingStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.first_sent = asyncio.Event()
        self.closed = False

    async def __aiter__(self):
        yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
        self.first_sent.set()
        await asyncio.Event().wait()

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_downstream_task_cancellation_closes_upstream_response():
    stream = WaitingStream()

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    async with OpenAICompatibleClient(config(), httpx.MockTransport(handler)) as client:
        async def consume() -> None:
            async for _ in client.stream_generate([{"role": "user", "content": "go"}]):
                pass

        task = asyncio.create_task(consume())
        await stream.first_sent.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert stream.closed
