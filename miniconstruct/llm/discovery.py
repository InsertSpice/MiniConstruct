from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from urllib.parse import urlparse, urlunparse

import httpx

from miniconstruct.llm.client import LLMBackendError, OpenAICompatibleClient
from miniconstruct.models.api import (
    DiscoveredModel,
    EndpointDiscoveryResponse,
    EndpointDiscoveryResult,
    EndpointProfile,
    EndpointSource,
    LLMSettings,
)


KNOWN_LOOPBACK_ENDPOINTS = (
    EndpointProfile(
        id="lm-studio-loopback",
        displayName="LM Studio",
        baseUrl="http://127.0.0.1:1234/v1",
        source=EndpointSource.LM_STUDIO,
    ),
    EndpointProfile(
        id="ollama-loopback",
        displayName="Ollama",
        baseUrl="http://127.0.0.1:11434/v1",
        source=EndpointSource.OLLAMA,
    ),
    # Current Unsloth Studio exposes its OpenAI-compatible API beneath /v1 on port 8888.
    EndpointProfile(
        id="unsloth-studio-loopback",
        displayName="Unsloth Studio",
        baseUrl="http://127.0.0.1:8888/v1",
        source=EndpointSource.UNSLOTH_STUDIO,
    ),
)

ModelProbe = Callable[[EndpointProfile], Awaitable[list[str]]]
HealthProbe = Callable[[EndpointProfile], Awaitable[bool]]


async def _probe_models(profile: EndpointProfile) -> list[str]:
    settings = LLMSettings(endpoint=profile, modelId="", timeoutSeconds=1.5)
    async with OpenAICompatibleClient(settings) as client:
        return await client.list_models()


async def _probe_unsloth_health(profile: EndpointProfile) -> bool:
    parsed = urlparse(profile.base_url)
    root = urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            response = await client.get(f"{root}/api/health")
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    service = payload.get("service")
    status = payload.get("status")
    return isinstance(service, str) and "unsloth" in service.lower() and status == "healthy"


def _same_endpoint(left: str, right: str) -> bool:
    return left.rstrip("/").lower() == right.rstrip("/").lower()


class LocalEndpointDiscoveryService:
    """Concurrently probes a small fixed set of loopback OpenAI-compatible servers."""

    def __init__(
        self,
        probe_models: ModelProbe = _probe_models,
        probe_unsloth_health: HealthProbe = _probe_unsloth_health,
    ) -> None:
        self._probe_models = probe_models
        self._probe_unsloth_health = probe_unsloth_health

    @staticmethod
    def _model_result(profile: EndpointProfile, model_ids: list[str]) -> EndpointDiscoveryResult:
        return EndpointDiscoveryResult(
            endpoint=profile.public_copy(),
            connected=True,
            discoveryState="catalog_available",
            message=f"Connected; discovered {len(model_ids)} model(s).",
            models=[
                DiscoveredModel(
                    endpointId=profile.id,
                    modelId=model_id,
                    displayName=f"{model_id} — {profile.display_name}",
                )
                for model_id in model_ids
            ],
        )

    async def _discover_one(self, profile: EndpointProfile) -> EndpointDiscoveryResult:
        if profile.source == EndpointSource.UNSLOTH_STUDIO:
            if not await self._probe_unsloth_health(profile):
                return EndpointDiscoveryResult(endpoint=profile.public_copy(), connected=False)
            try:
                return self._model_result(profile, await self._probe_models(profile))
            except LLMBackendError as exc:
                if exc.upstream_status == 401:
                    has_key = bool(profile.api_key and profile.api_key.get_secret_value())
                    return EndpointDiscoveryResult(
                        endpoint=profile.public_copy(),
                        connected=False,
                        discoveryState="authentication_failed" if has_key else "api_key_required",
                        message=(
                            "Unsloth Studio detected — authentication failed."
                            if has_key else "Unsloth Studio detected — API key required to discover models."
                        ),
                    )
                return EndpointDiscoveryResult(
                    endpoint=profile.public_copy(),
                    connected=False,
                    discoveryState="catalog_unavailable",
                    message="Unsloth Studio detected — model catalog unavailable.",
                )
        try:
            model_ids = await self._probe_models(profile)
        except LLMBackendError:
            return EndpointDiscoveryResult(endpoint=profile.public_copy(), connected=False)
        return self._model_result(profile, model_ids)

    async def discover(self, manual_endpoint: EndpointProfile | None = None) -> EndpointDiscoveryResponse:
        candidates: list[EndpointProfile] = list(KNOWN_LOOPBACK_ENDPOINTS)
        matching_known = next(
            (item for item in candidates if manual_endpoint and _same_endpoint(item.base_url, manual_endpoint.base_url)),
            None,
        )
        if matching_known and manual_endpoint:
            replacement = manual_endpoint.model_copy(update={"source": matching_known.source})
            candidates[candidates.index(matching_known)] = replacement
        manual_is_distinct = manual_endpoint and matching_known is None
        manual_is_loopback = manual_endpoint and urlparse(manual_endpoint.base_url).hostname in {"127.0.0.1", "localhost", "::1"}
        if manual_is_distinct and manual_is_loopback:
            candidates.append(manual_endpoint)
        results = await asyncio.gather(*(self._discover_one(profile) for profile in candidates))
        visible = [
            result for result in results
            if result.connected
            or result.discovery_state != "unavailable"
            or (manual_endpoint and result.endpoint.id == manual_endpoint.id)
        ]
        if manual_is_distinct and not manual_is_loopback:
            visible.append(EndpointDiscoveryResult(endpoint=manual_endpoint.public_copy(), connected=False))
        return EndpointDiscoveryResponse(endpoints=visible)
