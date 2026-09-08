from __future__ import annotations

from copy import deepcopy
import asyncio
import json
import logging

from miniconstruct.execution_authoring import execution_authoring_fingerprint
import time
from collections.abc import AsyncIterator, Callable

from fastapi import APIRouter, HTTPException, Response, Request
from fastapi.responses import StreamingResponse

from miniconstruct.h3.builder import assemble_prompt, build_h3_input_map, build_reference_manifest
from miniconstruct.generation import generate_workspace_prompts, resolved_generation_seeds
from miniconstruct.h3.repair import assemble_repair_prompt
from miniconstruct.h3.revision import assemble_revision_prompt, splice_revision, validate_replacement
from miniconstruct.h3.validator import canonicalize_bare_reference_labels, normalize_comparison_scale_references, validate_prompt, validate_workspace_prompt
from miniconstruct.llm.client import LLMBackendError, LLMStreamEvent, OpenAICompatibleClient
from miniconstruct.llm.compatibility import build_generation_payload
from miniconstruct.llm.diagnostics import cache_input_fingerprint
from miniconstruct.llm.discovery import LocalEndpointDiscoveryService
from miniconstruct.llm.model_eject import ModelEjectError, eject_model
from miniconstruct.planning import infer_plan, revise_plan
from miniconstruct.story_planning import build_story_plan, revise_story_plan
from miniconstruct.models.story_planning import StoryApplyRequest, StoryBuildRequest, StoryPlanProposal, StoryRevisionRequest
from miniconstruct.project_store import (
    GenerationNotFoundError,
    ProjectNotFoundError,
    ProjectStore,
    ProjectStoreError,
    StaleProjectSnapshotError,
    UnsupportedProjectSchemaError,
)
from miniconstruct.models.api import (
    AssistedGenerationPlan,
    DiscoveredModel,
    EndpointDiscoveryRequest,
    GeneratedVariation,
    GenerationRequest,
    GenerationResponse,
    FilesystemProjectListResponse,
    FilesystemProjectResponse,
    FilesystemProjectSummary,
    FilesystemProjectWriteRequest,
    GenerationCreateRequest,
    GenerationOrderRequest,
    LLMSettings,
    PlanningInferenceRequest,
    PlanningRevisionRequest,
    ProjectEnvelope,
    ProjectSaveAsRequest,
    ReferenceManifestRequest,
    RepairRequest,
    RevisionRequest,
    ValidationRequest,
)
from miniconstruct.models.workspace import Workspace


router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)


def _http_error(exc: LLMBackendError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


def _resolved_generation_seeds(request: GenerationRequest) -> list[int | None]:
    """Compatibility alias for route tests and existing internal callers."""
    return resolved_generation_seeds(request)


@router.post("/media/{kind}")
async def upload_reference_media(kind: str, request: Request):
    store = ProjectStore()
    if kind not in store.MEDIA_LIMITS:
        raise HTTPException(400, "Only Audio and Video payloads are supported.")
    length = request.headers.get("content-length")
    if length is not None:
        try:
            size = int(length)
        except ValueError:
            raise HTTPException(400, "Invalid file length")
        if size > store.MEDIA_LIMITS[kind]:
            raise HTTPException(413, f"{kind.title()} file exceeds {store.MEDIA_LIMITS[kind] // (1024 * 1024)} MiB limit.")
    try:
        payload = await store.ingest_media(request.stream(), kind, request.headers.get("content-type", "").split(";", 1)[0].lower())
        return payload.model_dump(mode="json", by_alias=True, exclude_none=True)
    except ProjectStoreError as exc:
        raise HTTPException(413 if "exceeds" in str(exc) else 400, str(exc)) from exc


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "application": "MiniConstruct", "version": "0.4.0"}


def _filesystem_project_response(project) -> FilesystemProjectResponse:
    return FilesystemProjectResponse(
        id=project.project_id,
        name=project.name,
        createdAt=project.created_at,
        updatedAt=project.updated_at,
        workspace=project.workspace,
        storyIdea=project.story_idea,
        sequence={
            "activeGenerationId": project.sequence.active_generation_id,
            "generations": [
                {"generationId": item.generation_id, "createdAt": item.created_at, "updatedAt": item.updated_at,
                 "referenceAssets": list(item.reference_assets)}
                for item in project.sequence.generations
            ],
        },
        continuity={
            "sequenceContext": project.sequence_context,
            "incomingState": project.incoming_state,
            "intendedOutgoingState": project.intended_outgoing_state,
        },
        planning=project.planning,
    )


def _continuity_kwargs(continuity) -> dict:
    return continuity.model_dump(exclude_unset=True) if continuity is not None else {}


def _planning_kwargs(planning) -> dict:
    return planning.model_dump(exclude_unset=True, by_alias=True) if planning is not None else None


def _filesystem_project_error(exc: ProjectStoreError) -> HTTPException:
    if isinstance(exc, StaleProjectSnapshotError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ProjectNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, UnsupportedProjectSchemaError):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, GenerationNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/projects", response_model=FilesystemProjectListResponse)
async def list_filesystem_projects() -> FilesystemProjectListResponse:
    try:
        projects = ProjectStore().list_projects()
    except ProjectStoreError as exc:
        raise _filesystem_project_error(exc) from exc
    return FilesystemProjectListResponse(projects=[
        FilesystemProjectSummary(
            id=project.project_id,
            name=project.name,
            createdAt=project.created_at,
            updatedAt=project.updated_at,
        )
        for project in projects
    ])


@router.post("/projects", response_model=FilesystemProjectResponse, status_code=201)
async def create_filesystem_project(request: FilesystemProjectWriteRequest) -> FilesystemProjectResponse:
    try:
        return _filesystem_project_response(
            ProjectStore().create_project(
                request.workspace,
                **_continuity_kwargs(request.continuity),
                planning=_planning_kwargs(request.planning),
                **({"story_idea": request.story_idea} if request.story_idea is not None else {}),
            )
        )
    except ProjectStoreError as exc:
        raise _filesystem_project_error(exc) from exc


@router.get("/projects/{project_id}", response_model=FilesystemProjectResponse)
async def load_filesystem_project(project_id: str) -> FilesystemProjectResponse:
    try:
        return _filesystem_project_response(ProjectStore().load_project(project_id))
    except ProjectStoreError as exc:
        raise _filesystem_project_error(exc) from exc


@router.delete("/projects/{project_id}", status_code=204)
async def delete_filesystem_project(project_id: str) -> Response:
    try:
        ProjectStore().delete_project(project_id)
    except ProjectStoreError as exc:
        raise _filesystem_project_error(exc) from exc
    return Response(status_code=204)


@router.put("/projects/{project_id}", response_model=FilesystemProjectResponse)
async def save_filesystem_project(project_id: str, request: FilesystemProjectWriteRequest) -> FilesystemProjectResponse:
    try:
        return _filesystem_project_response(
            ProjectStore().save_project(
                project_id, request.workspace, **_continuity_kwargs(request.continuity),
                planning=_planning_kwargs(request.planning),
                **({"story_idea": request.story_idea} if request.story_idea is not None else {}),
            )
        )
    except ProjectStoreError as exc:
        raise _filesystem_project_error(exc) from exc


@router.post("/projects/{project_id}/generations", response_model=FilesystemProjectResponse)
async def create_generation(project_id: str, request: GenerationCreateRequest) -> FilesystemProjectResponse:
    try:
        project = ProjectStore().create_generation(
            project_id, insert_index=request.insert_index, workspace=request.workspace,
        )
        return _filesystem_project_response(project)
    except ProjectStoreError as exc:
        raise _filesystem_project_error(exc) from exc


@router.post("/projects/{project_id}/generations/{generation_id}/duplicate", response_model=FilesystemProjectResponse)
async def duplicate_generation(project_id: str, generation_id: str) -> FilesystemProjectResponse:
    try:
        return _filesystem_project_response(ProjectStore().duplicate_generation(project_id, generation_id))
    except ProjectStoreError as exc:
        raise _filesystem_project_error(exc) from exc


@router.put("/projects/{project_id}/active-generation/{generation_id}", response_model=FilesystemProjectResponse)
async def activate_generation(project_id: str, generation_id: str) -> FilesystemProjectResponse:
    try:
        return _filesystem_project_response(ProjectStore().activate_generation(project_id, generation_id))
    except ProjectStoreError as exc:
        raise _filesystem_project_error(exc) from exc


@router.put("/projects/{project_id}/generation-order", response_model=FilesystemProjectResponse)
async def reorder_generations(project_id: str, request: GenerationOrderRequest) -> FilesystemProjectResponse:
    try:
        return _filesystem_project_response(ProjectStore().reorder_generations(project_id, request.generation_ids))
    except ProjectStoreError as exc:
        raise _filesystem_project_error(exc) from exc


@router.delete("/projects/{project_id}/generations/{generation_id}", response_model=FilesystemProjectResponse)
async def delete_generation(project_id: str, generation_id: str) -> FilesystemProjectResponse:
    try:
        return _filesystem_project_response(ProjectStore().delete_generation(project_id, generation_id))
    except ProjectStoreError as exc:
        raise _filesystem_project_error(exc) from exc


@router.post("/projects/{project_id}/save-as", response_model=FilesystemProjectResponse, status_code=201)
async def save_project_as(project_id: str, request: ProjectSaveAsRequest) -> FilesystemProjectResponse:
    try:
        values = _continuity_kwargs(request.continuity)
        return _filesystem_project_response(
            ProjectStore().duplicate_project(
                project_id,
                request.name,
                active_workspace=request.workspace,
                story_idea=request.story_idea,
                **{
                    "sequence_context": values.get("sequence_context"),
                    "active_incoming_state": values.get("incoming_state"),
                    "active_intended_outgoing_state": values.get("intended_outgoing_state"),
                    "active_planning": _planning_kwargs(request.planning),
                },
            )
        )
    except ProjectStoreError as exc:
        raise _filesystem_project_error(exc) from exc


@router.post(
    "/projects/{project_id}/generations/{generation_id}/infer-plan",
    response_model=AssistedGenerationPlan,
)
async def infer_generation_plan(project_id: str, generation_id: str, request: PlanningInferenceRequest) -> AssistedGenerationPlan:
    try:
        previous = ProjectStore().read_predecessor_generation(project_id, generation_id)
        if previous is None:
            raise GenerationNotFoundError("Generation 1 has no predecessor to infer from")
        return await infer_plan(previous, request)
    except ProjectStoreError as exc:
        raise _filesystem_project_error(exc) from exc
    except LLMBackendError as exc:
        raise _http_error(exc) from exc


@router.post(
    "/projects/{project_id}/generations/{generation_id}/revise-plan",
    response_model=AssistedGenerationPlan,
)
async def revise_generation_plan(project_id: str, generation_id: str, request: PlanningRevisionRequest) -> AssistedGenerationPlan:
    try:
        # The path identifies the target and keeps the endpoint consistent with
        # inference. The request is deliberately draft-based and never persisted.
        ProjectStore().read_generation(project_id, generation_id)
        return await revise_plan(request)
    except ProjectStoreError as exc:
        raise _filesystem_project_error(exc) from exc
    except LLMBackendError as exc:
        raise _http_error(exc) from exc


@router.post("/projects/{project_id}/story-plan/build", response_model=StoryPlanProposal)
async def build_project_story_plan(project_id: str, request: StoryBuildRequest):
    try:
        return await build_story_plan(ProjectStore(), project_id, request)
    except ProjectStoreError as exc:
        logger.warning("Story Build failed: category=%s status=%s",
                       "stale_snapshot" if isinstance(exc, StaleProjectSnapshotError) else "project_store",
                       _filesystem_project_error(exc).status_code)
        raise _filesystem_project_error(exc) from exc
    except LLMBackendError as exc:
        logger.warning("Story Build failed: category=%s status=%s upstream_status=%s detail=%s",
                       exc.category, exc.status_code, exc.upstream_status, exc.safe_message)
        raise _http_error(exc) from exc
    except ValueError as exc:
        logger.warning("Story Build failed: category=request status=400")
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/projects/{project_id}/story-plan/revise", response_model=StoryPlanProposal)
async def revise_project_story_plan(project_id: str, request: StoryRevisionRequest):
    try:
        return await revise_story_plan(ProjectStore(), project_id, request)
    except ProjectStoreError as exc:
        raise _filesystem_project_error(exc) from exc
    except LLMBackendError as exc:
        raise _http_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/projects/{project_id}/story-plan/apply", response_model=FilesystemProjectResponse)
async def apply_project_story_plan(project_id: str, request: StoryApplyRequest):
    try:
        return _filesystem_project_response(ProjectStore().apply_story_plan(project_id, request.draft))
    except ProjectStoreError as exc:
        raise _filesystem_project_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
    assembled = assemble_prompt(request.prompt_workspace, request.llm.supports_vision)
    return {
        "instructions": assembled.inspector_text,
        "warnings": assembled.warnings,
        "manifest": build_reference_manifest(request.prompt_workspace),
    }


@router.post("/reference-manifest")
async def reference_manifest(request: ReferenceManifestRequest) -> dict:
    """Return canonical H3 labels for the live authoring state."""
    projection = Workspace.model_construct(mode=request.mode, assets=request.assets, subjects=request.subjects)
    from miniconstruct.execution_routing import prompt_projection
    try:
        return {"entries": build_h3_input_map(prompt_projection(projection, request.execution_routing))}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/validate-project")
async def validate_project(project: ProjectEnvelope) -> dict:
    return project.model_dump(mode="json", by_alias=True)


@router.post("/validate")
async def validate(request: ValidationRequest) -> dict:
    from miniconstruct.execution_routing import prompt_projection
    try:
        projection = prompt_projection(Workspace.model_construct(mode=request.mode, assets=request.assets), request.execution_routing)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return validate_prompt(
        request.prompt,
        request.mode,
        request.duration_seconds,
        projection.assets,
        request.dialogue,
        request.shots,
    ).model_dump()


@router.post("/generate", response_model=GenerationResponse)
async def generate(request: GenerationRequest) -> GenerationResponse:
    try:
        result = await generate_workspace_prompts(request)
    except LLMBackendError as exc:
        raise _http_error(exc) from exc
    return GenerationResponse(variations=[item.generated for item in result.variations], warnings=result.warnings)


def _stream_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


async def stream_generation_events(
    request: GenerationRequest,
    client_factory: Callable[[LLMSettings], OpenAICompatibleClient] = OpenAICompatibleClient,
) -> AsyncIterator[str]:
    """Stream one immutable request snapshot; cancellation closes its client."""
    assembly_started = time.perf_counter()
    assembled = assemble_prompt(request.prompt_workspace, request.llm.supports_vision)
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
                validation = validate_workspace_prompt(prompt, request.prompt_workspace)
                completed_at = time.perf_counter()
                yield _stream_event("complete", {
                    "variation": variation_index,
                    "prompt": prompt,
                    "validation": validation.model_dump(mode="json"),
                    "referenceMap": [ref.model_dump(by_alias=True) for ref in request.reference_map],
                    "sourceFingerprint": execution_authoring_fingerprint(request.workspace),
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
        validation = validate_workspace_prompt(candidate, request.prompt_workspace)
        original_validation = validate_workspace_prompt(request.selection.full_prompt, request.prompt_workspace)
        yield _stream_event("complete", {
            "replacement": replacement,
            "candidatePrompt": candidate,
            "validation": validation.model_dump(mode="json"),
            "referenceMap": [ref.model_dump(by_alias=True) for ref in request.reference_map],
            "sourceFingerprint": execution_authoring_fingerprint(request.workspace),
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
    current_validation = validate_workspace_prompt(request.prompt, request.prompt_workspace)
    structural_failures = [
        finding
        for finding in current_validation.findings
        if finding.severity == "ERROR" and finding.category == "structural"
    ]
    if not structural_failures:
        return GeneratedVariation(referenceMap=request.reference_map, sourceFingerprint=execution_authoring_fingerprint(request.workspace), prompt=request.prompt, validation=current_validation)
    normalized_prompt = normalize_comparison_scale_references(
        canonicalize_bare_reference_labels(request.prompt), request.workspace,
    )
    normalized_validation = validate_workspace_prompt(normalized_prompt, request.prompt_workspace)
    remaining_failures = [
        finding
        for finding in normalized_validation.findings
        if finding.severity == "ERROR" and finding.category == "structural"
    ]
    if not remaining_failures:
        return GeneratedVariation(
            referenceMap=request.reference_map, sourceFingerprint=execution_authoring_fingerprint(request.workspace),
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
    return GeneratedVariation(referenceMap=request.reference_map, sourceFingerprint=execution_authoring_fingerprint(request.workspace), prompt=prompt, validation=validate_workspace_prompt(prompt, request.prompt_workspace))
