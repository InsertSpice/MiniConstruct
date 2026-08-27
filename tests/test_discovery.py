from __future__ import annotations

import pytest

from miniconstruct.llm.client import LLMBackendError
from miniconstruct.llm.discovery import LocalEndpointDiscoveryService
from miniconstruct.models.api import EndpointProfile


async def unsloth_healthy(profile: EndpointProfile) -> bool:
    return profile.source == "unsloth_studio"


@pytest.mark.asyncio
async def test_local_discovery_pools_models_with_endpoint_identity():
    async def probe(profile: EndpointProfile) -> list[str]:
        return {
            "lm-studio-loopback": ["shared-model", "gemma"],
            "ollama-loopback": ["shared-model", "qwen"],
            "unsloth-studio-loopback": ["vision-model"],
        }.get(profile.id, [])

    result = await LocalEndpointDiscoveryService(probe, unsloth_healthy).discover()
    pooled = [model for endpoint in result.endpoints for model in endpoint.models]
    shared = [model for model in pooled if model.model_id == "shared-model"]
    assert len(shared) == 2
    assert {model.endpoint_id for model in shared} == {"lm-studio-loopback", "ollama-loopback"}
    assert {endpoint.endpoint.display_name for endpoint in result.endpoints} == {"LM Studio", "Ollama", "Unsloth Studio"}


@pytest.mark.asyncio
async def test_unsloth_health_retains_detected_endpoint_when_api_key_is_required():
    async def rejected(_: EndpointProfile) -> list[str]:
        raise LLMBackendError("authentication required", upstream_status=401)

    result = await LocalEndpointDiscoveryService(rejected, unsloth_healthy).discover()
    unsloth = next(item for item in result.endpoints if item.endpoint.source == "unsloth_studio")
    assert not unsloth.connected
    assert unsloth.discovery_state == "api_key_required"
    assert unsloth.message == "Unsloth Studio detected — API key required to discover models."


@pytest.mark.asyncio
async def test_matching_manual_unsloth_credential_is_reused_only_for_that_endpoint():
    observed: dict[str, str | None] = {}

    async def probe(profile: EndpointProfile) -> list[str]:
        observed[profile.base_url] = profile.api_key.get_secret_value() if profile.api_key else None
        if profile.source == "unsloth_studio":
            return ["real-model", "other-model"]
        raise LLMBackendError("absent")

    manual = EndpointProfile(
        id="manual:http://127.0.0.1:8888/v1",
        displayName="Unsloth",
        baseUrl="http://127.0.0.1:8888/v1/",
        apiKey="fake-unsloth-key",
    )
    result = await LocalEndpointDiscoveryService(probe, unsloth_healthy).discover(manual)
    unsloth = next(item for item in result.endpoints if item.endpoint.id == manual.id)
    assert observed["http://127.0.0.1:8888/v1"] == "fake-unsloth-key"
    assert unsloth.connected
    assert [model.model_id for model in unsloth.models] == ["real-model", "other-model"]
    assert unsloth.endpoint.api_key is None


@pytest.mark.asyncio
async def test_credential_from_a_different_endpoint_is_not_used_for_unsloth():
    observed: dict[str, str | None] = {}

    async def probe(profile: EndpointProfile) -> list[str]:
        if profile.source == "unsloth_studio":
            observed[profile.id] = profile.api_key.get_secret_value() if profile.api_key else None
            raise LLMBackendError("authentication required", upstream_status=401)
        raise LLMBackendError("absent")

    other = EndpointProfile(
        id="other",
        displayName="Other local endpoint",
        baseUrl="http://127.0.0.1:8890/v1",
        apiKey="fake-other-key",
    )
    await LocalEndpointDiscoveryService(probe, unsloth_healthy).discover(other)
    assert observed["unsloth-studio-loopback"] is None


@pytest.mark.asyncio
async def test_absent_servers_are_ignored_but_manual_endpoint_is_retained():
    async def absent(_: EndpointProfile) -> list[str]:
        raise LLMBackendError("connection refused")

    async def no_health(_: EndpointProfile) -> bool:
        return False

    manual = EndpointProfile(id="my-server", displayName="My server", baseUrl="http://127.0.0.1:9876/v1")
    result = await LocalEndpointDiscoveryService(absent, no_health).discover(manual)
    assert len(result.endpoints) == 1
    assert result.endpoints[0].endpoint.id == "my-server"
    assert not result.endpoints[0].connected


@pytest.mark.asyncio
async def test_manual_endpoint_can_discover_models_without_provider_branching():
    async def probe(profile: EndpointProfile) -> list[str]:
        if profile.id == "manual-custom":
            return ["manual-model"]
        raise LLMBackendError("absent")

    async def no_health(_: EndpointProfile) -> bool:
        return False

    manual = EndpointProfile(id="manual-custom", displayName="Custom API", baseUrl="http://127.0.0.1:9876/v1")
    result = await LocalEndpointDiscoveryService(probe, no_health).discover(manual)
    manual_result = next(item for item in result.endpoints if item.endpoint.id == "manual-custom")
    model = manual_result.models[0]
    assert model.endpoint_id == "manual-custom"
    assert model.model_id == "manual-model"
    assert model.display_name == "manual-model — Custom API"


@pytest.mark.asyncio
async def test_remote_manual_endpoint_is_preserved_without_discovery_probe():
    probed: list[str] = []

    async def probe(profile: EndpointProfile) -> list[str]:
        probed.append(profile.id)
        raise LLMBackendError("absent")

    result = await LocalEndpointDiscoveryService(probe, unsloth_healthy).discover(
        EndpointProfile(id="remote", displayName="Remote custom", baseUrl="http://192.0.2.20:9000/v1")
    )
    assert "remote" not in probed
    assert result.endpoints[-1].endpoint.id == "remote"
