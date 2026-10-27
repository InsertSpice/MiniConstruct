"""Offline capacity and provider-wire checks for ordinary H3 calls."""
import json

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from miniconstruct.app import app
from miniconstruct.api.routes import stream_generation_events
from miniconstruct.api import routes
from miniconstruct import context_capacity
from miniconstruct.context_capacity import preflight_request_capacity
from miniconstruct.generation import generate_workspace_prompts
from miniconstruct.llm.client import LLMBackendError, OpenAICompatibleClient
from miniconstruct.models.api import GenerationRequest, RepairRequest
from test_revision import revision_request


def request_for(workspace, *, capacity=8192, output=8192):
    return GenerationRequest.model_validate({"workspace": workspace.model_dump(mode="json", by_alias=True),
        "llm": {"baseUrl": "http://127.0.0.1:8888/v1", "model": "offline",
                "contextWindow": capacity, "maxTokens": output, "reasoningMode": "off"}})


@pytest.mark.asyncio
async def test_known_undersized_h3_capacity_stops_stream_and_nonstream_before_client(workspace_factory, monkeypatch):
    request = request_for(workspace_factory())
    monkeypatch.setattr(context_capacity, "estimate_request_tokens", lambda _: (48_000, 0, 16_000))
    called = 0
    def forbidden(_):
        nonlocal called
        called += 1
        raise AssertionError("provider client must not be constructed")
    events = [item async for item in stream_generation_events(request, forbidden)]
    assert called == 0
    assert len(events) == 1 and events[0].startswith("event: error")
    assert "estimated input 16,000 tokens" in events[0]
    with pytest.raises(LLMBackendError, match="backend context capacity") as error:
        await generate_workspace_prompts(request, forbidden)
    assert error.value.category == "h3_context_capacity" and called == 0


@pytest.mark.parametrize("capacity, output, budget_fits", [(65536, 2048, True), (16384, 8192, False)])
@pytest.mark.asyncio
async def test_h3_provider_wire_uses_completion_budget_and_reasoning_off_not_context_size(
    workspace_factory, monkeypatch, capacity, output, budget_fits,
):
    monkeypatch.setattr(context_capacity, "estimate_request_tokens", lambda _: (31_260, 0, 10_420))
    request = request_for(workspace_factory(), capacity=capacity, output=output)
    captured = []
    def handler(http_request):
        captured.append(json.loads(http_request.content))
        return httpx.Response(200, content=b'data: {"choices":[{"delta":{"content":"done"}}]}\n\ndata: [DONE]\n\n')
    transport = httpx.MockTransport(handler)
    events = [item async for item in stream_generation_events(request,
        lambda settings: OpenAICompatibleClient(settings, transport))]
    assert any(item.startswith("event: complete") for item in events)
    assert len(captured) == 1
    payload = captured[0]
    assert payload["max_tokens"] == output and payload["stream"] is True
    assert payload["chat_template_kwargs"] == {"thinking": False, "enable_thinking": False}
    assert "contextWindow" not in payload and "context_window" not in payload
    assert "Do not use extended reasoning" in payload["messages"][0]["content"]
    start = next(json.loads(item.split("data: ", 1)[1]) for item in events if item.startswith("event: start"))
    assert start["diagnostics"]["contextWindow"] == capacity
    assert start["diagnostics"]["maxOutputTokens"] == output
    assert start["diagnostics"]["estimatedInputTokens"] == 10_420
    assert start["diagnostics"]["authoringPath"] == "one_shot"
    assert start["diagnostics"]["reasoningMode"] == "off"
    assert start["diagnostics"]["completionBudgetFits"] is budget_fits
    if budget_fits:
        assert start["diagnostics"]["completionBudgetWarning"] is None
    else:
        assert start["diagnostics"]["estimatedCompletionHeadroom"] > 512
        assert "may limit or truncate" in start["diagnostics"]["completionBudgetWarning"]
        assert any("may limit or truncate" in warning for warning in start["warnings"])


def test_h3_capacity_distinguishes_input_room_budget_and_unknown_capacity(monkeypatch, workspace_factory):
    estimated_input = 10_420
    monkeypatch.setattr(context_capacity, "estimate_request_tokens", lambda _: (31_260, 0, estimated_input))
    settings = request_for(workspace_factory(), capacity=16384, output=8192).llm
    diagnostic = preflight_request_capacity([], settings, purpose="H3 generation")
    assert diagnostic["estimatedCompletionHeadroom"] == 16384 - 10420 - diagnostic["safetyTokens"]
    assert diagnostic["completionBudgetFits"] is False
    diagnostic = preflight_request_capacity([], settings.model_copy(update={"max_tokens": 2048}), purpose="H3 generation")
    assert diagnostic["completionBudgetFits"] is True
    assert diagnostic["completionBudgetWarning"] is None
    estimated_input = 16384 - diagnostic["safetyTokens"] - 128
    assert preflight_request_capacity([], settings, purpose="H3 generation")["estimatedCompletionHeadroom"] == 128
    estimated_input += 1
    with pytest.raises(LLMBackendError, match="too small"):
        preflight_request_capacity([], settings, purpose="H3 generation")
    estimated_input = 16_000
    with pytest.raises(LLMBackendError, match="too little|too small") as error:
        preflight_request_capacity([], settings, purpose="H3 generation")
    assert error.value.category == "h3_context_capacity"
    assert preflight_request_capacity([], settings.model_copy(update={"context_window": None}), purpose="H3 generation")["completionBudgetFits"] is None


def test_story_full_completion_estimate_is_advisory(monkeypatch, workspace_factory):
    monkeypatch.setattr(context_capacity, "estimate_request_tokens", lambda _: (31_260, 0, 10_420))
    settings = request_for(workspace_factory(), capacity=16384, output=8192).llm
    diagnostic = preflight_request_capacity([], settings)
    assert diagnostic["requiredTokens"] > settings.context_window
    assert "estimate is approximate" in diagnostic["completionBudgetWarning"]


@pytest.mark.asyncio
async def test_nonstream_revision_and_repair_allow_oversized_completion_ceiling(monkeypatch, workspace_factory):
    monkeypatch.setattr(context_capacity, "estimate_request_tokens", lambda _: (31_260, 0, 10_420))
    calls = []
    class OfflineClient:
        def __init__(self, settings):
            calls.append(settings.max_tokens)
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            return None
        async def generate(self, _):
            return "invalid H3"
        async def stream_events(self, _):
            if False:
                yield None

    workspace = workspace_factory()
    generated = await generate_workspace_prompts(request_for(workspace, capacity=16384), OfflineClient)
    assert calls == [8192]
    assert any("may limit or truncate" in warning for warning in generated.warnings)

    revision = revision_request(workspace)
    revision.llm = revision.llm.model_copy(update={"context_window": 16384, "max_tokens": 8192})
    events = [item async for item in routes.stream_revision_events(revision, OfflineClient)]
    assert calls == [8192, 8192]
    start = next(json.loads(item.split("data: ", 1)[1]) for item in events if item.startswith("event: start"))
    assert start["capacity"]["completionBudgetFits"] is False
    assert any("may limit or truncate" in warning for warning in start["warnings"])

    repair = RepairRequest.model_validate({**request_for(workspace, capacity=16384).model_dump(by_alias=True),
        "prompt": "invalid H3", "findings": []})
    monkeypatch.setattr(routes, "OpenAICompatibleClient", OfflineClient)
    repaired = await routes.repair(repair)
    assert calls == [8192, 8192, 8192]
    assert any("may limit or truncate" in warning for warning in repaired.warnings)


def test_assemble_preview_reports_capacity_without_reconfiguring_backend(workspace_factory):
    request = request_for(workspace_factory())
    with TestClient(app) as client:
        result = client.post("/api/assemble", json=request.model_dump(mode="json", by_alias=True))
    assert result.status_code == 200
    capacity = result.json()["capacity"]
    assert capacity["contextWindow"] == 8192 and capacity["reservedCompletionTokens"] == 8192
    assert capacity["requiredTokens"] > 8192
    assert capacity["completionBudgetFits"] is False


@pytest.mark.asyncio
async def test_assemble_preview_rejects_oversized_h3_before_dialogue_provider(workspace_factory, monkeypatch):
    monkeypatch.setattr(context_capacity, "estimate_request_tokens", lambda _: (48_000, 0, 16_000))
    workspace = workspace_factory()
    workspace.dialogue = "A short line of dialogue."
    async def forbidden(*_):
        raise AssertionError("dialogue provider must not be called before H3 capacity preflight")
    monkeypatch.setattr(routes, "resolve_dialogue", forbidden)
    with pytest.raises(HTTPException, match="backend context capacity") as error:
        await routes.assemble(request_for(workspace))
    assert error.value.status_code == 400


@pytest.mark.asyncio
async def test_revision_and_repair_preflight_before_provider(workspace_factory, monkeypatch):
    monkeypatch.setattr(context_capacity, "estimate_request_tokens", lambda _: (48_000, 0, 16_000))
    def forbidden(_):
        raise AssertionError("undersized H3 request must not construct a provider client")
    revision = revision_request(workspace_factory())
    revision.llm = revision.llm.model_copy(update={"context_window": 8192, "max_tokens": 8192})
    events = [item async for item in routes.stream_revision_events(revision, forbidden)]
    assert len(events) == 1 and events[0].startswith("event: error")
    assert "H3 revision" in events[0]
    repair = RepairRequest.model_validate({**request_for(workspace_factory()).model_dump(by_alias=True),
        "prompt": "invalid H3", "findings": []})
    monkeypatch.setattr(routes, "OpenAICompatibleClient", forbidden)
    with pytest.raises(HTTPException, match="backend context capacity"):
        await routes.repair(repair)
