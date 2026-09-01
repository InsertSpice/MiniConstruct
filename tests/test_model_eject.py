from __future__ import annotations

import httpx
import pytest

from miniconstruct.app import app
from miniconstruct.llm.model_eject import ModelEjectError, eject_model
from miniconstruct.models.api import LLMSettings

ASYNC_CLIENT = httpx.AsyncClient

def settings(model: str = "chosen-model") -> LLMSettings:
    return LLMSettings.model_validate({
        "endpoint": {"id": "manual", "displayName": "Local", "baseUrl": "http://server.test/v1", "source": "manual"},
        "modelId": model,
    })


def client_for(handler):
    return lambda **kwargs: ASYNC_CLIENT(transport=httpx.MockTransport(handler), **kwargs)


@pytest.mark.asyncio
async def test_lm_studio_unloads_exact_loaded_instance():
    def handler(request):
        if request.url.path == "/api/v1/models":
            return httpx.Response(200, json={"models": [{"key": "chosen-model", "loaded_instances": [{"id": "instance-42"}]}]})
        assert request.url.path == "/api/v1/models/unload"
        assert request.content == b'{"instance_id":"instance-42"}'
        return httpx.Response(200, json={"instance_id": "instance-42"})

    assert (await eject_model(settings(), client_for(handler))).provider == "lm_studio"


@pytest.mark.asyncio
async def test_lm_studio_refuses_ambiguous_instances_and_recognizes_already_unloaded():
    def ambiguous(request):
        return httpx.Response(200, json={"models": [{"key": "chosen-model", "loaded_instances": [{"id": "one"}, {"id": "two"}]}]})
    with pytest.raises(ModelEjectError, match="multiple loaded instances"):
        await eject_model(settings(), client_for(ambiguous))

    def unloaded(request):
        return httpx.Response(200, json={"models": [{"key": "chosen-model", "loaded_instances": []}]})
    assert "already unloaded" in (await eject_model(settings(), client_for(unloaded))).message


@pytest.mark.asyncio
async def test_ollama_unloads_with_keep_alive_zero_and_recognizes_absence():
    def running(request):
        if request.url.path == "/api/v1/models": return httpx.Response(404)
        if request.url.path == "/api/ps": return httpx.Response(200, json={"models": [{"name": "chosen-model"}]})
        assert request.url.path == "/api/generate"
        assert request.content == b'{"model":"chosen-model","keep_alive":0,"stream":false}'
        return httpx.Response(200, json={"done": True})
    assert (await eject_model(settings(), client_for(running))).provider == "ollama"

    def absent(request):
        if request.url.path == "/api/v1/models": return httpx.Response(404)
        return httpx.Response(200, json={"models": []})
    assert "already unloaded" in (await eject_model(settings(), client_for(absent))).message


@pytest.mark.asyncio
async def test_unsloth_uses_model_path_and_verifies_success_and_failure():
    def success(request):
        if request.url.path == "/api/v1/models": return httpx.Response(404)
        if request.url.path == "/api/ps": return httpx.Response(404)
        if request.url.path == "/api/health": return httpx.Response(200, json={"service": "Unsloth Studio", "status": "healthy"})
        assert request.url.path == "/api/inference/unload"
        assert request.content == b'{"model_path":"chosen-model","force_cancel_active":false}'
        return httpx.Response(200, json={"success": True})
    assert (await eject_model(settings(), client_for(success))).provider == "unsloth_studio"

    def failed(request):
        if request.url.path == "/api/v1/models": return httpx.Response(404)
        if request.url.path == "/api/ps": return httpx.Response(404)
        if request.url.path == "/api/health": return httpx.Response(200, json={"service": "Unsloth Studio", "status": "healthy"})
        return httpx.Response(200, json={"success": False})
    with pytest.raises(ModelEjectError, match="reported"):
        await eject_model(settings(), client_for(failed))


@pytest.mark.asyncio
async def test_unsupported_endpoint_sends_no_destructive_post():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(404)
    with pytest.raises(ModelEjectError, match="could not identify"):
        await eject_model(settings(), client_for(handler))
    assert all(request.method == "GET" for request in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["lm", "ollama", "unsloth", "unsupported"])
async def test_eject_route_runs_full_manual_endpoint_path(monkeypatch, kind):
    from miniconstruct.llm import model_eject
    calls = []
    def handler(request):
        calls.append(request)
        if kind == "lm" and request.url.path == "/api/v1/models":
            return httpx.Response(200, json={"models": [{"key": "chosen-model", "loaded_instances": [{"id": "inst"}]}]})
        if kind == "lm": return httpx.Response(200, json={})
        if kind == "ollama" and request.url.path == "/api/ps": return httpx.Response(200, json={"models": [{"name": "chosen-model"}]})
        if kind == "ollama" and request.url.path == "/api/generate": return httpx.Response(200, json={"done": True})
        if kind == "unsloth" and request.url.path == "/api/health": return httpx.Response(200, json={"service": "Unsloth Studio", "status": "healthy"})
        if kind == "unsloth" and request.url.path == "/api/inference/unload": return httpx.Response(200, json={"success": True})
        return httpx.Response(404)
    monkeypatch.setattr(model_eject.httpx, "AsyncClient", client_for(handler))
    async with ASYNC_CLIENT(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/model-management/eject", json=settings().model_dump(mode="json", by_alias=True))
    assert response.status_code == (422 if kind == "unsupported" else 200)
    if kind == "unsupported": assert all(call.method == "GET" for call in calls)
