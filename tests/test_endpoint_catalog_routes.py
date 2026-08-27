from __future__ import annotations

import pytest

from miniconstruct.api import routes
from miniconstruct.models.api import LLMSettings


def endpoint_settings() -> LLMSettings:
    return LLMSettings.model_validate({
        "endpoint": {
            "id": "unsloth-local",
            "displayName": "Unsloth",
            "baseUrl": "http://127.0.0.1:8888/v1",
            "apiKey": "fake-secret",
            "source": "unsloth_studio",
        },
        "modelId": "",
    })


@pytest.mark.asyncio
async def test_models_and_connection_share_safe_catalog_response(monkeypatch):
    class FakeClient:
        def __init__(self, settings):
            self.settings = settings

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def list_models(self):
            return ["shared/model", "other/model"]

    monkeypatch.setattr(routes, "OpenAICompatibleClient", FakeClient)
    settings = endpoint_settings()
    discovered = await routes.models(settings)
    tested = await routes.test_connection(settings)
    assert discovered == tested
    assert discovered["message"] == "Connected; discovered 2 model(s)."
    assert discovered["models"] == [
        {"endpointId": "unsloth-local", "modelId": "shared/model", "displayName": "shared/model — Unsloth"},
        {"endpointId": "unsloth-local", "modelId": "other/model", "displayName": "other/model — Unsloth"},
    ]
    assert "fake-secret" not in str(discovered)
