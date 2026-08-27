from __future__ import annotations

from copy import deepcopy
import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from miniconstruct.h3.builder import assemble_prompt, build_reference_manifest
from miniconstruct.h3.validator import validate_prompt, validate_workspace_prompt
from miniconstruct.llm.client import LLMBackendError, LLMStreamEvent, OpenAICompatibleClient
from miniconstruct.llm.compatibility import build_generation_payload
from miniconstruct.llm.diagnostics import cache_input_fingerprint
from miniconstruct.llm.discovery import LocalEndpointDiscoveryService
from miniconstruct.models.api import (
    DiscoveredModel,
    EndpointDiscoveryRequest,
    GeneratedVariation,
    GenerationRequest,
    GenerationResponse,
    LLMSettings,
    ProjectEnvelope,
    RepairRequest,
    ValidationRequest,
)


router = APIRouter(prefix="/api")


def _http_error(exc: LLMBackendError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "application": "MiniConstruct", "version": "0.1.0"}


async def enumerate_endpoint_models(settings: LLMSettings) -> dict:
    async with OpenAICompatibleClient(settings) as client:
        models = [
            DiscoveredModel(
                endpointId=settings.endpoint.id,
                modelId=model_id,
                displayName=f"{model_id} — {settings.endpoint.display_name}",
            ).model_dump(by_alias=True)
            for model_id in await client.list_models()
        ]
    return {"models": models, "message": f"Connected; discovered {len(models)} model(s)."}


@router.post("/models")
async def models(settings: LLMSettings) -> dict:
    try:
        return await enumerate_endpoint_models(settings)
    except LLMBackendError as exc:
        raise _http_error(exc) from exc


@router.post("/test-connection")
async def test_connection(settings: LLMSettings) -> dict:
    try:
        return await enumerate_endpoint_models(settings)
    except LLMBackendError as exc:
        raise _http_error(exc) from exc


@router.post("/discover-endpoints")
async def discover_endpoints(request: EndpointDiscoveryRequest) -> dict:
    discovery = LocalEndpointDiscoveryService()
    return (await discovery.discover(request.manual_endpoint)).model_dump(mode="json", by_alias=True)


@router.post("/assemble")
async def assemble(request: GenerationRequest) -> dict:
    assembled = assemble_prompt(request.workspace, request.llm.supports_vision)
    return {
        "instructions": assembled.inspector_text,
        "warnings": assembled.warnings,
        "manifest": build_reference_manifest(request.workspace),
    }


@router.post("/validate-project")
async def validate_project(project: ProjectEnvelope) -> dict:
    return project.model_dump(mode="json", by_alias=True)


@router.post("/validate")
async def validate(request: ValidationRequest) -> dict:
    return validate_prompt(
        request.prompt,
        request.mode,
        request.duration_seconds,
        request.assets,
        request.dialogue,
        request.shots,
    ).model_dump()


@router.post("/generate", response_model=GenerationResponse)
async def generate(request: GenerationRequest) -> GenerationResponse:
    assembled = assemble_prompt(request.workspace, request.llm.supports_vision)
    generated: list[GeneratedVariation] = []
    try:
        async with OpenAICompatibleClient(request.llm) as client:
            for _ in range(request.workspace.variations):
                prompt = await client.generate(deepcopy(assembled.messages))
                generated.append(
                    GeneratedVariation(
                        prompt=prompt,
                        validation=validate_workspace_prompt(prompt, request.workspace),
                    )
                )
    except LLMBackendError as exc:
        raise _http_error(exc) from exc
    return GenerationResponse(variations=generated, warnings=assembled.warnings)


def _stream_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


async def stream_generation_events(
    request: GenerationRequest,
    client_factory: Callable[[LLMSettings], OpenAICompatibleClient] = OpenAICompatibleClient,
) -> AsyncIterator[str]:
    """Stream one immutable request snapshot; cancellation closes its client."""
    assembly_started = time.perf_counter()
    assembled = assemble_prompt(request.workspace, request.llm.supports_vision)
    assembly_ms = (time.perf_counter() - assembly_started) * 1000
    preview_payload, _ = build_generation_payload(request.llm, assembled.messages, stream=True)
    fingerprint = cache_input_fingerprint(preview_payload)
    image_dimensions = [
        {"width": asset.image.width, "height": asset.image.height}
        for asset in request.workspace.assets
        if asset.image is not None
    ]
    yield _stream_event("start", {
        "variations": request.workspace.variations,
        "warnings": assembled.warnings,
        "diagnostics": {
            "assemblyMs": round(assembly_ms, 2),
            "assembledTextChars": sum(
                len(part.get("text", "")) if isinstance(part, dict) else 0
                for message in preview_payload["messages"]
                for part in (
                    message.get("content", [])
                    if isinstance(message.get("content"), list)
                    else [{"text": message.get("content", "")}]
                )
            ),
            "imageCount": len(image_dimensions),
            "imageDimensions": image_dimensions,
            "fingerprint": fingerprint,
            "reasoningMode": request.llm.reasoning_mode.value,
            "backend": request.llm.endpoint.display_name,
            "model": request.llm.selected_model_id,
        },
    })
    try:
        async with client_factory(request.llm) as client:
            for variation_index in range(request.workspace.variations):
                yield _stream_event("variation_start", {"variation": variation_index})
                parts: list[str] = []
                upstream_started = time.perf_counter()
                first_event_at: float | None = None
                first_reasoning_at: float | None = None
                first_content_at: float | None = None
                reasoning_chars = 0
                usage: dict[str, int] = {}
                compatibility_fallback = False

                def metrics(completed_at: float | None = None) -> dict:
                    now = completed_at or time.perf_counter()
                    ms = lambda moment: round((moment - upstream_started) * 1000, 2) if moment else None
                    return {
                        "variation": variation_index,
                        "assemblyMs": round(assembly_ms, 2),
                        "firstEventMs": ms(first_event_at),
                        "firstReasoningMs": ms(first_reasoning_at),
                        "firstContentMs": ms(first_content_at),
                        "reasoningMs": (
                            round((first_content_at - first_reasoning_at) * 1000, 2)
                            if first_reasoning_at and first_content_at else None
                        ),
                        "finalContentMs": (
                            round((now - first_content_at) * 1000, 2)
                            if completed_at and first_content_at else None
                        ),
                        "totalMs": round((now - upstream_started) * 1000, 2) if completed_at else None,
                        "reasoningChars": reasoning_chars,
                        "usage": usage,
                        "compatibilityFallback": compatibility_fallback,
                    }
                try:
                    if hasattr(client, "stream_events"):
                        stream = client.stream_events(deepcopy(assembled.messages))
                    else:
                        async def legacy_stream() -> AsyncIterator[LLMStreamEvent]:
                            async for delta in client.stream_generate(deepcopy(assembled.messages)):
                                yield LLMStreamEvent("content", delta)
                        stream = legacy_stream()
                    async for upstream_event in stream:
                        now = time.perf_counter()
                        if upstream_event.kind != "compatibility_fallback" and first_event_at is None:
                            first_event_at = now
                            yield _stream_event("metrics", metrics())
                        if upstream_event.kind == "compatibility_fallback":
                            compatibility_fallback = True
                            yield _stream_event("compatibility_fallback", {"variation": variation_index})
                        elif upstream_event.kind == "usage":
                            usage.update(upstream_event.usage)
                        elif upstream_event.kind == "reasoning":
                            reasoning_chars += len(upstream_event.text)
                            if first_reasoning_at is None:
                                first_reasoning_at = now
                                yield _stream_event("reasoning", {
                                    "variation": variation_index,
                                    "characterCount": reasoning_chars,
                                })
                                yield _stream_event("metrics", metrics())
                        elif upstream_event.kind == "content":
                            if first_content_at is None:
                                first_content_at = now
                                yield _stream_event("metrics", metrics())
                            parts.append(upstream_event.text)
                            yield _stream_event("delta", {"variation": variation_index, "text": upstream_event.text})
                except LLMBackendError as exc:
                    yield _stream_event("error", {
                        "variation": variation_index,
                        "message": str(exc),
                        "partial": bool(parts),
                    })
                    return
                prompt = "".join(parts)
                validation = validate_workspace_prompt(prompt, request.workspace)
                completed_at = time.perf_counter()
                yield _stream_event("complete", {
                    "variation": variation_index,
                    "prompt": prompt,
                    "validation": validation.model_dump(mode="json"),
                    "metrics": metrics(completed_at),
                })
        yield _stream_event("done", {"variations": request.workspace.variations})
    except asyncio.CancelledError:
        # Normal browser Stop/disconnect. Context managers close upstream promptly.
        raise


@router.post("/generate-stream")
async def generate_stream(request: GenerationRequest) -> StreamingResponse:
    return StreamingResponse(
        stream_generation_events(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/repair", response_model=GeneratedVariation)
async def repair(request: RepairRequest) -> GeneratedVariation:
    assembled = assemble_prompt(request.workspace, request.llm.supports_vision)
    messages = deepcopy(assembled.messages)
    failures = "\n".join(
        f"- {item.get('severity', 'ERROR')} {item.get('code', '')}: {item.get('message', '')}"
        for item in request.findings
    ) or "- Repair any structural violation of the selected official guide."
    messages.append(
        {
            "role": "system",
            "content": (
                "Perform one format-repair pass. Repair only H3 grammar and the listed validation failures. "
                "Preserve creative intent, reference relationships, and all verbatim dialogue. Return only clean plain text.\n\n"
                f"Validator findings:\n{failures}"
            ),
        }
    )
    messages.append({"role": "user", "content": f"INVALID H3 PROMPT TO REPAIR:\n\n{request.prompt}"})
    try:
        async with OpenAICompatibleClient(request.llm) as client:
            prompt = await client.generate(messages)
    except LLMBackendError as exc:
        raise _http_error(exc) from exc
    return GeneratedVariation(prompt=prompt, validation=validate_workspace_prompt(prompt, request.workspace))
