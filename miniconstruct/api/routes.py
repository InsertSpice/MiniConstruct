from __future__ import annotations

from copy import deepcopy
import asyncio
import json
import secrets
import time
from collections.abc import AsyncIterator, Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from miniconstruct.h3.builder import assemble_prompt, build_reference_manifest
from miniconstruct.h3.repair import assemble_repair_prompt
from miniconstruct.h3.revision import assemble_revision_prompt, splice_revision, validate_replacement
from miniconstruct.h3.validator import canonicalize_bare_reference_labels, normalize_comparison_scale_references, validate_prompt, validate_workspace_prompt
from miniconstruct.llm.client import LLMBackendError, LLMStreamEvent, OpenAICompatibleClient
from miniconstruct.llm.compatibility import build_generation_payload
from miniconstruct.llm.diagnostics import cache_input_fingerprint
from miniconstruct.llm.discovery import LocalEndpointDiscoveryService
from miniconstruct.llm.model_eject import ModelEjectError, eject_model
from miniconstruct.models.api import (
    DiscoveredModel,
    EndpointDiscoveryRequest,
    GeneratedVariation,
    GenerationRequest,
    GenerationResponse,
    LLMSettings,
    ProjectEnvelope,
    RepairRequest,
    RevisionRequest,
    SEED_MAX,
    SeedMode,
    ValidationRequest,
)


router = APIRouter(prefix="/api")


def _http_error(exc: LLMBackendError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


def _resolved_generation_seeds(request: GenerationRequest) -> list[int | None]:
    """Use the browser snapshot when available; retain sane API-call behavior."""
    if request.resolved_seeds:
        return request.resolved_seeds
    if request.llm.seed_mode == SeedMode.FIXED:
        return [request.llm.fixed_seed] * request.workspace.variations
    if request.llm.seed_mode == SeedMode.RANDOM:
        seeds: set[int] = set()
        while len(seeds) < request.workspace.variations:
            seeds.add(secrets.randbelow(SEED_MAX + 1))
        return list(seeds)
    return [None] * request.workspace.variations


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "application": "MiniConstruct", "version": "0.3.0"}


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


@router.post("/model-management/eject")
async def eject(settings: LLMSettings) -> dict:
    try:
        return (await eject_model(settings)).as_dict()
    except ModelEjectError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


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
    warnings = list(assembled.warnings)
    try:
        for seed in _resolved_generation_seeds(request):
            settings = request.llm.model_copy(update={"seed": seed})
            async with OpenAICompatibleClient(settings) as client:
                prompt = await client.generate(deepcopy(assembled.messages))
                generated.append(
                    GeneratedVariation(
                        prompt=prompt,
                        validation=validate_workspace_prompt(prompt, request.workspace),
                    )
                )
                if client.seed_unsupported:
                    warnings.append("This endpoint does not support the seed parameter; MiniConstruct retried with backend defaults.")
    except LLMBackendError as exc:
        raise _http_error(exc) from exc
    return GenerationResponse(variations=generated, warnings=list(dict.fromkeys(warnings)))


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
    preview_payload, _ = build_generation_payload(
        request.llm.model_copy(update={"seed": None}), assembled.messages, stream=True
    )
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
        for variation_index, seed in enumerate(_resolved_generation_seeds(request)):
            settings = request.llm.model_copy(update={"seed": seed})
            async with client_factory(settings) as client:
                yield _stream_event("variation_start", {"variation": variation_index, "seed": seed})
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
                        "seed": getattr(client, "effective_seed", seed),
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
                        elif upstream_event.kind == "seed_unsupported":
                            yield _stream_event("seed_unsupported", {"variation": variation_index, "seed": None})
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
                    "seed": getattr(client, "effective_seed", seed),
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


async def stream_revision_events(
    request: RevisionRequest,
    client_factory: Callable[[LLMSettings], OpenAICompatibleClient] = OpenAICompatibleClient,
) -> AsyncIterator[str]:
    assembled = assemble_revision_prompt(request)
    yield _stream_event("start", {"warnings": assembled.warnings, "outputIndex": request.output_index})
    replacement_parts: list[str] = []
    reasoning_chars = 0
    try:
        async with client_factory(request.llm) as client:
            try:
                async for upstream_event in client.stream_events(deepcopy(assembled.messages)):
                    if upstream_event.kind == "compatibility_fallback":
                        yield _stream_event("compatibility_fallback", {})
                    elif upstream_event.kind == "seed_unsupported":
                        yield _stream_event("seed_unsupported", {})
                    elif upstream_event.kind == "reasoning":
                        reasoning_chars += len(upstream_event.text)
                        yield _stream_event("reasoning", {"characterCount": reasoning_chars})
                    elif upstream_event.kind == "content":
                        replacement_parts.append(upstream_event.text)
                        yield _stream_event("delta", {"text": upstream_event.text})
            except LLMBackendError as exc:
                yield _stream_event("error", {"message": str(exc), "partial": bool(replacement_parts)})
                return
        replacement = "".join(replacement_parts)
        invalid = validate_replacement(request.selection, replacement)
        if invalid:
            yield _stream_event("error", {"message": invalid, "partial": bool(replacement)})
            return
        candidate = splice_revision(request.selection, replacement)
        validation = validate_workspace_prompt(candidate, request.workspace)
        original_validation = validate_workspace_prompt(request.selection.full_prompt, request.workspace)
        yield _stream_event("complete", {
            "replacement": replacement,
            "candidatePrompt": candidate,
            "validation": validation.model_dump(mode="json"),
            "originalValidation": original_validation.model_dump(mode="json"),
        })
        yield _stream_event("done", {})
    except asyncio.CancelledError:
        raise


@router.post("/revise-stream")
async def revise_stream(request: RevisionRequest) -> StreamingResponse:
    return StreamingResponse(
        stream_revision_events(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/repair", response_model=GeneratedVariation)
async def repair(request: RepairRequest) -> GeneratedVariation:
    current_validation = validate_workspace_prompt(request.prompt, request.workspace)
    structural_failures = [
        finding
        for finding in current_validation.findings
        if finding.severity == "ERROR" and finding.category == "structural"
    ]
    if not structural_failures:
        return GeneratedVariation(prompt=request.prompt, validation=current_validation)
    normalized_prompt = normalize_comparison_scale_references(
        canonicalize_bare_reference_labels(request.prompt), request.workspace,
    )
    normalized_validation = validate_workspace_prompt(normalized_prompt, request.workspace)
    remaining_failures = [
        finding
        for finding in normalized_validation.findings
        if finding.severity == "ERROR" and finding.category == "structural"
    ]
    if not remaining_failures:
        return GeneratedVariation(
            prompt=normalized_prompt,
            validation=normalized_validation,
        )
    assembled = assemble_repair_prompt(request.model_copy(update={"prompt": normalized_prompt}), remaining_failures)
    try:
        # Repair is deliberately syntax-focused: it neither resolves nor sends a creative seed.
        repair_settings = request.llm.model_copy(update={"seed": None})
        async with OpenAICompatibleClient(repair_settings) as client:
            prompt = await client.generate(assembled.messages)
    except LLMBackendError as exc:
        raise _http_error(exc) from exc
    prompt = normalize_comparison_scale_references(
        canonicalize_bare_reference_labels(prompt), request.workspace, normalized_prompt,
    )
    return GeneratedVariation(prompt=prompt, validation=validate_workspace_prompt(prompt, request.workspace))
