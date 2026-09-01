"""Small, click-triggered native model unload adapters for local providers."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

import httpx

from miniconstruct.models.api import EndpointSource, LLMSettings


class ModelEjectError(Exception):
    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class EjectResult:
    provider: str
    model: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"provider": self.provider, "model": self.model, "message": self.message}


def _origin(base_url: str) -> str:
    parsed = urlparse(base_url)
    return urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))


def _headers(settings: LLMSettings) -> dict[str, str]:
    key = settings.endpoint.api_key
    if key and key.get_secret_value():
        return {"Authorization": f"Bearer {key.get_secret_value()}"}
    return {}


async def _json_get(client: httpx.AsyncClient, path: str) -> dict | None:
    try:
        response = await client.get(path)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _lm_instance(payload: dict, selected_model: str) -> str | None:
    models = payload.get("models")
    if not isinstance(models, list):
        return None
    matching = [item for item in models if isinstance(item, dict) and item.get("key") == selected_model]
    if not matching:
        return None
    instances = [
        instance.get("id")
        for item in matching
        for instance in (item.get("loaded_instances") or [])
        if isinstance(instance, dict) and isinstance(instance.get("id"), str)
    ]
    if not instances:
        return ""
    if len(instances) != 1:
        raise ModelEjectError("LM Studio has multiple loaded instances for the selected model; unload it in LM Studio or select an unambiguous model.")
    return instances[0]


async def _eject_lm_studio(client: httpx.AsyncClient, root: str, selected_model: str) -> EjectResult | None:
    payload = await _json_get(client, f"{root}/api/v1/models")
    if payload is None or not isinstance(payload.get("models"), list):
        return None
    instance_id = _lm_instance(payload, selected_model)
    if instance_id is None:
        raise ModelEjectError("LM Studio identified this server, but the selected model was not found in its native model catalogue.")
    if not instance_id:
        return EjectResult("lm_studio", selected_model, "The selected LM Studio model is already unloaded.")
    try:
        response = await client.post(f"{root}/api/v1/models/unload", json={"instance_id": instance_id})
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ModelEjectError("LM Studio could not unload the selected model.", 502) from exc
    return EjectResult("lm_studio", selected_model, "Model unloaded from LM Studio.")


async def _eject_ollama(client: httpx.AsyncClient, root: str, selected_model: str) -> EjectResult | None:
    payload = await _json_get(client, f"{root}/api/ps")
    if payload is None or not isinstance(payload.get("models"), list):
        return None
    running = {
        item.get("name") or item.get("model")
        for item in payload["models"] if isinstance(item, dict)
    }
    if selected_model not in running:
        return EjectResult("ollama", selected_model, "The selected Ollama model is already unloaded.")
    try:
        response = await client.post(
            f"{root}/api/generate", json={"model": selected_model, "keep_alive": 0, "stream": False}
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ModelEjectError("Ollama could not unload the selected model.", 502) from exc
    return EjectResult("ollama", selected_model, "Model unloaded from Ollama.")


async def _eject_unsloth(client: httpx.AsyncClient, root: str, selected_model: str) -> EjectResult | None:
    health = await _json_get(client, f"{root}/api/health")
    if not (
        health and isinstance(health.get("service"), str) and "unsloth" in health["service"].lower()
        and health.get("status") == "healthy"
    ):
        return None
    try:
        response = await client.post(
            f"{root}/api/inference/unload",
            json={"model_path": selected_model, "force_cancel_active": False},
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ModelEjectError("Unsloth Studio could not unload the selected model.", 502) from exc
    if not isinstance(payload, dict) or not (payload.get("success") is True or payload.get("status") in {"success", "unloaded"}):
        raise ModelEjectError("Unsloth Studio reported that it could not unload the selected model.")
    return EjectResult("unsloth_studio", selected_model, "Model unloaded from Unsloth Studio.")


async def eject_model(settings: LLMSettings, client_factory=None) -> EjectResult:
    """Identify a native API only for this request, then perform one unload."""
    selected_model = settings.selected_model_id.strip()
    if not selected_model:
        raise ModelEjectError("Select a model before ejecting it.")
    root = _origin(settings.endpoint.base_url)
    adapters = {
        EndpointSource.LM_STUDIO: (_eject_lm_studio, _eject_ollama, _eject_unsloth),
        EndpointSource.OLLAMA: (_eject_ollama, _eject_lm_studio, _eject_unsloth),
        EndpointSource.UNSLOTH_STUDIO: (_eject_unsloth, _eject_lm_studio, _eject_ollama),
    }.get(settings.endpoint.source, (_eject_lm_studio, _eject_ollama, _eject_unsloth))
    client_factory = client_factory or httpx.AsyncClient
    try:
        async with client_factory(headers=_headers(settings), timeout=httpx.Timeout(45.0, read=180.0)) as client:
            for adapter in adapters:
                result = await adapter(client, root, selected_model)
                if result is not None:
                    return result
    except httpx.HTTPError as exc:
        raise ModelEjectError("MiniConstruct could not contact this endpoint's model-unload API.", 502) from exc
    raise ModelEjectError("MiniConstruct could not identify a supported model-unload API for this endpoint.")
