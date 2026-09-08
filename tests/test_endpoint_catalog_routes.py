from __future__ import annotations

import pytest

from miniconstruct.api import routes
from miniconstruct.h3.builder import numbered_assets
from miniconstruct.models.api import LLMSettings, ReferenceManifestRequest
from miniconstruct.models.workspace import Workspace


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


@pytest.mark.asyncio
async def test_reference_manifest_returns_canonical_labels_for_identity_and_video():
    request = ReferenceManifestRequest.model_validate({
        "mode": "Ref2VA",
        "subjects": [{"id": "subject-1", "number": 1}],
        "assets": [
            {
                "id": "identity", "kind": "image", "filename": "alice.png",
                "role": "subject_identity", "order": 0,
                "subjectIdentity": {"subjectId": "subject-1", "focus": "face"},
            },
            {
                "id": "motion", "kind": "video", "filename": "movement.mp4",
                "role": "motion_action", "order": 1,
            },
        ],
    })
    response = await routes.reference_manifest(request)
    projection = Workspace.model_construct(mode=request.mode, assets=request.assets, subjects=request.subjects)
    expected = {asset.id: label for asset, label in numbered_assets(projection)}
    assert {entry["id"]: entry["h3Label"] for entry in response["entries"]} == expected
    assert response["entries"][0]["metadata"] == ["Subject 1", "Identity", "Facial"]
