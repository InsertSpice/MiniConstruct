from __future__ import annotations

import asyncio
import json

import pytest

from miniconstruct.api.routes import stream_generation_events
from miniconstruct.llm.client import LLMBackendError
from miniconstruct.llm.client import LLMStreamEvent
from miniconstruct.models.api import GenerationRequest


def request_for(workspace, variations: int = 1) -> GenerationRequest:
    workspace = workspace.model_copy(update={"variations": variations})
    return GenerationRequest.model_validate({
        "workspace": workspace.model_dump(mode="json", by_alias=True),
        "llm": {"baseUrl": "http://local.test/v1", "model": "writer"},
    })


def decode_event(raw: str) -> tuple[str, dict]:
    lines = raw.strip().splitlines()
    return lines[0].split(":", 1)[1].strip(), json.loads(lines[1].split(":", 1)[1].strip())


class FakeClient:
    def __init__(self, _, scripts=None) -> None:
        self.scripts = scripts or [["subject_", "definitions:"]]
        self.calls = 0
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.closed = True

    async def stream_generate(self, _):
        script = self.scripts[self.calls]
        self.calls += 1
        for item in script:
            if isinstance(item, Exception):
                raise item
            yield item


@pytest.mark.asyncio
async def test_route_distinguishes_variations_and_validates_completions(workspace_factory):
    fake = FakeClient(None, [["one"], ["two", "!"]])
    events = [decode_event(raw) async for raw in stream_generation_events(
        request_for(workspace_factory(), 2), lambda _: fake
    )]
    deltas = [data for event, data in events if event == "delta"]
    completes = [data for event, data in events if event == "complete"]
    assert deltas == [
        {"variation": 0, "text": "one"},
        {"variation": 1, "text": "two"},
        {"variation": 1, "text": "!"},
    ]
    assert [item["prompt"] for item in completes] == ["one", "two!"]
    assert all("validation" in item for item in completes)
    assert events[-1] == ("done", {"variations": 2})
    assert fake.closed


@pytest.mark.asyncio
async def test_route_preserves_partial_and_does_not_complete_on_error(workspace_factory):
    fake = FakeClient(None, [["partial", LLMBackendError("lost stream")]])
    events = [decode_event(raw) async for raw in stream_generation_events(
        request_for(workspace_factory()), lambda _: fake
    )]
    assert any(event == "delta" and data["text"] == "partial" for event, data in events)
    assert any(event == "error" and data["partial"] for event, data in events)
    assert not any(event in {"complete", "done"} for event, _ in events)
    assert fake.closed


class CancellableClient(FakeClient):
    async def stream_generate(self, _):
        self.calls += 1
        try:
            yield "partial"
            await asyncio.Event().wait()
        finally:
            self.upstream_closed = True


@pytest.mark.asyncio
async def test_route_cancellation_stops_remaining_variations_and_cleans_up(workspace_factory):
    fake = CancellableClient(None)
    generator = stream_generation_events(request_for(workspace_factory(), 2), lambda _: fake)
    while True:
        event, _ = decode_event(await anext(generator))
        if event == "delta":
            break
    pending = asyncio.create_task(anext(generator))
    await asyncio.sleep(0)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert fake.calls == 1
    assert fake.upstream_closed
    assert fake.closed


class ReasoningClient(FakeClient):
    async def stream_events(self, _):
        yield LLMStreamEvent("metadata")
        yield LLMStreamEvent("reasoning", "private analysis")
        yield LLMStreamEvent("content", "final prompt")
        yield LLMStreamEvent("usage", usage={"prompt_tokens": 77, "completion_tokens": 4})


@pytest.mark.asyncio
async def test_route_reports_reasoning_timing_without_putting_it_in_prompt(workspace_factory):
    fake = ReasoningClient(None)
    events = [decode_event(raw) async for raw in stream_generation_events(
        request_for(workspace_factory()), lambda _: fake
    )]
    reasoning = [data for event, data in events if event == "reasoning"]
    deltas = [data["text"] for event, data in events if event == "delta"]
    complete = next(data for event, data in events if event == "complete")
    start = next(data for event, data in events if event == "start")
    assert reasoning == [{"variation": 0, "characterCount": len("private analysis")}]
    assert deltas == ["final prompt"]
    assert complete["prompt"] == "final prompt"
    assert "private analysis" not in json.dumps(events)
    assert complete["metrics"]["firstReasoningMs"] is not None
    assert complete["metrics"]["firstContentMs"] is not None
    assert complete["metrics"]["usage"]["prompt_tokens"] == 77
    assert len(start["diagnostics"]["fingerprint"]) == 64


class CancellableReasoningClient(FakeClient):
    async def stream_events(self, _):
        try:
            yield LLMStreamEvent("reasoning", "private partial thought")
            await asyncio.Event().wait()
        finally:
            self.upstream_closed = True


@pytest.mark.asyncio
async def test_cancellation_during_reasoning_never_creates_prompt_completion(workspace_factory):
    fake = CancellableReasoningClient(None)
    generator = stream_generation_events(request_for(workspace_factory(), 2), lambda _: fake)
    observed = []
    while True:
        decoded = decode_event(await anext(generator))
        observed.append(decoded)
        if decoded[0] == "reasoning":
            break
    observed.append(decode_event(await anext(generator)))  # reasoning milestone metrics
    pending = asyncio.create_task(anext(generator))
    await asyncio.sleep(0)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert fake.upstream_closed and fake.closed
    assert not any(event in {"delta", "complete", "done"} for event, _ in observed)
