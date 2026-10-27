from __future__ import annotations

from copy import deepcopy
from uuid import UUID
import asyncio
import json
import logging

import time
from collections.abc import AsyncIterator, Callable

from fastapi import APIRouter, HTTPException, Response, Request, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from miniconstruct.models.workspace import SubjectRecord
from miniconstruct.api.request_lifetime import run_until_disconnect

from miniconstruct.h3.builder import assemble_prompt, build_h3_input_map, build_reference_manifest
from miniconstruct.generation import resolve_narrative_request_async, generate_workspace_prompts, resolved_generation_seeds
from miniconstruct.continuation_drafting import (
    DraftContinuationRequest, ContinuationAuthority, draft_continuation, validate_continuation_authority,
)
from miniconstruct.h3.repair import assemble_repair_prompt, repair_identity_provenance
from miniconstruct.h3.revision import assemble_revision_prompt, splice_revision, validate_replacement
from miniconstruct.h3.validator import canonicalize_bare_reference_labels, normalize_comparison_scale_references, validate_prompt, validate_workspace_prompt
from miniconstruct.llm.client import LLMBackendError, LLMStreamEvent, OpenAICompatibleClient
from miniconstruct.llm.compatibility import build_generation_payload
from miniconstruct.llm.compatibility import prepare_messages
from miniconstruct.context_capacity import preflight_request_capacity, request_capacity_diagnostics
from miniconstruct.llm.diagnostics import cache_input_fingerprint
from miniconstruct.h3.vision_input import model_vision_stats, VISION_TRANSFORM_VERSION
from miniconstruct.llm.discovery import LocalEndpointDiscoveryService
from miniconstruct.llm.model_eject import ModelEjectError, eject_model
from miniconstruct.planning import infer_plan, revise_plan
from miniconstruct.models.story_operations import (
    StoryNextRequest, StoryNextProposal, StoryNextApplyRequest,
    StoryInsertRequest, StoryInsertProposal, StoryInsertApplyRequest,
    StoryReplanRequest, StoryReplanProposal, StoryReplanApplyRequest,
    StoryInitialRangeRequest, StoryInitialRangeProposal, StoryInitialRangeApplyRequest,
)
from miniconstruct.story_operations import build_initial_range, plan_next, plan_insert, plan_replan
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
    DialogueResolutionRequest, DialogueResolutionResponse,
    GenerationRequest,
    GenerationResponse,
    FilesystemProjectListResponse,
    FilesystemProjectResponse,
    FilesystemProjectSummary,
    FilesystemProjectWriteRequest,
    GenerationCreateRequest,
    GenerationOrderRequest,
    GenerationActivationRequest,
    LLMSettings,
    PlanningInferenceRequest,
    PlanningRevisionRequest,
    ProjectEnvelope,
    ProjectSaveAsRequest,
    ReferenceManifestRequest,
    RepairRequest,
    RevisionRequest,
    SequenceSubjectDeleteRequest, SequenceSubjectNameRequest,
    ValidationRequest,
)
from miniconstruct.models.workspace import Workspace, DialogueLanguage
from miniconstruct.dialogue_language import resolve_dialogue, workspace_for_h3, resolution_is_current


router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)


@router.get("/projects/{project_id}/foreground-renders")
async def foreground_renders(project_id: UUID, generationId: UUID | None = None, submissionId: UUID | None = None,
                             refresh: bool = False, cursor: int = Query(default=0, ge=0, le=1000000)):
    if (generationId is None) != (submissionId is None):
        raise HTTPException(422, "Watching requires both exact Generation and RenderSubmission IDs")
    try:
        if refresh:
            from miniconstruct.render_reconciliation import discover_foreground
            return await discover_foreground(str(project_id), generationId, submissionId, cursor=cursor)
        return ProjectStore().foreground_renders(str(project_id), generationId, submissionId)
    except ProjectStoreError as exc:
        raise _filesystem_project_error(exc) from exc


def _http_error(exc: LLMBackendError) -> HTTPException:
    if exc.raw_response is not None:
        detail = {"message": str(exc), "category": exc.category}
        if exc.raw_response_visible:
            detail["rawResponse"] = exc.raw_response
        else:
            detail["rawResponseWithheld"] = True
        return HTTPException(status_code=exc.status_code, detail=detail)
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
    return {"status": "ok", "application": "MiniConstruct", "version": "0.5.0"}


def _filesystem_project_response(project) -> FilesystemProjectResponse:
    return FilesystemProjectResponse(
        id=project.project_id,
        name=project.name,
        createdAt=project.created_at,
        updatedAt=project.updated_at,
        workspace=project.workspace,
        outputs=project.outputs,
        storyIdea=project.story_idea,
        storyCreativeDirection=project.story_creative_direction,
        storyPlanningStyle=project.story_planning_style,
        storyMemoryAfter=project.story_memory_after,
        projectFingerprint=project.project_fingerprint,
        projectEditFingerprint=project.project_edit_fingerprint,
        dialogueLanguage=project.dialogue_language,
        sequence={
            "activeGenerationId": project.sequence.active_generation_id,
            "subjects": list(project.sequence.subjects),
            "nextSubjectNumber": project.sequence.next_subject_number,
            "generations": [
                {"generationId": item.generation_id, "createdAt": item.created_at, "updatedAt": item.updated_at,
                 "referenceAssets": list(item.reference_assets), "storyMemoryStatus": item.story_memory_status}
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
        detail = ({"category": exc.category, "message": str(exc)} if exc.category else str(exc))
        return HTTPException(status_code=409, detail=detail)
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
                dialogue_language=request.dialogue_language,
                sequence_subjects=request.sequence_subjects,
                sequence_next_subject_number=request.sequence_next_subject_number,
                **_continuity_kwargs(request.continuity),
                planning=_planning_kwargs(request.planning),
                character_profiles=request.character_profiles,
                story_memory=request.story_memory,
                **({"story_idea": request.story_idea} if request.story_idea is not None else {}),
                **({"story_creative_direction": request.story_creative_direction}
                   if request.story_creative_direction is not None else {}),
                **({"story_planning_style": request.story_planning_style}
                   if request.story_planning_style is not None else {}),
            )
        )
    except ProjectStoreError as exc:
        raise _filesystem_project_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid Story Memory or Project authoring state") from exc


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
    if request.story_memory is not None and (request.story_memory_generation_id is None or request.expected_project_fingerprint is None):
        raise HTTPException(400, "Story Memory Save requires the selected Generation ID and loaded Project fingerprint")
    try:
        return _filesystem_project_response(
            ProjectStore().save_project(
                project_id, request.workspace, **_continuity_kwargs(request.continuity),
                dialogue_language=request.dialogue_language,
                planning=_planning_kwargs(request.planning),
                story_memory=request.story_memory,
                story_memory_generation_id=request.story_memory_generation_id,
                expected_project_fingerprint=request.expected_project_fingerprint,
                character_profiles=request.character_profiles,
                **({"story_idea": request.story_idea} if request.story_idea is not None else {}),
                **({"story_creative_direction": request.story_creative_direction}
                   if request.story_creative_direction is not None else {}),
                **({"story_planning_style": request.story_planning_style}
                   if request.story_planning_style is not None else {}),
            )
        )
    except ProjectStoreError as exc:
        raise _filesystem_project_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid Story Memory or Project authoring state") from exc


class ReferenceAssetsWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    assets: list[dict] = Field(max_length=1000)
    subjects: list[SubjectRecord] = Field(default_factory=list, max_length=1000)
    expected_project_fingerprint: str = Field(alias="expectedProjectFingerprint", pattern=r"^[0-9a-f]{64}$")
    expected_project_edit_fingerprint: str | None = Field(default=None, alias="expectedProjectEditFingerprint", pattern=r"^[0-9a-f]{64}$")


@router.put("/projects/{project_id}/generations/{generation_id}/reference-assets")
async def sync_reference_assets(project_id: UUID, generation_id: UUID, request: ReferenceAssetsWrite):
    try:
        return ProjectStore().sync_reference_assets(str(project_id), str(generation_id), request.assets,
                                                    request.subjects, request.expected_project_fingerprint,
                                                    request.expected_project_edit_fingerprint)
    except ProjectStoreError as exc:
        raise _filesystem_project_error(exc) from exc
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(422, "Invalid Reference Assets") from exc


@router.post("/projects/{project_id}/generations", response_model=FilesystemProjectResponse)
async def create_generation(project_id: str, request: GenerationCreateRequest) -> FilesystemProjectResponse:
    try:
        project = ProjectStore().create_generation(
            project_id, insert_index=request.insert_index, workspace=request.workspace,
        )
        return _filesystem_project_response(project)
    except ProjectStoreError as exc:
        raise _filesystem_project_error(exc) from exc


@router.post("/projects/{project_id}/generations/{generation_id}/draft-continuation")
async def continuation_draft(project_id: UUID, generation_id: UUID, request: DraftContinuationRequest,
                             http_request: Request):
    try:
        return await run_until_disconnect(http_request, draft_continuation(str(project_id), str(generation_id), request))
    except ProjectStoreError as exc:
        raise _filesystem_project_error(exc) from exc
    except LLMBackendError as exc:
        raise _http_error(exc) from exc


@router.post("/projects/{project_id}/generations/{generation_id}/draft-continuation/validate")
async def continuation_draft_validate(project_id: UUID, generation_id: UUID, request: ContinuationAuthority):
    try:
        validate_continuation_authority(ProjectStore(), str(project_id), str(generation_id), request)
        return {"valid": True}
    except ProjectStoreError as exc:
        raise _filesystem_project_error(exc) from exc


class ContinueSelectedTakeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    expected_project_fingerprint: str = Field(alias="expectedProjectFingerprint", pattern=r"^[0-9a-f]{64}$")
    take_id: UUID = Field(alias="takeId")
    take_sha256: str = Field(alias="takeSha256", pattern=r"^[0-9a-f]{64}$")


@router.post("/projects/{project_id}/generations/{generation_id}/continue-selected-take", response_model=FilesystemProjectResponse)
async def continue_selected_take(project_id: UUID, generation_id: UUID, request: ContinueSelectedTakeRequest):
    try:
        project = ProjectStore().continue_selected_take(str(project_id), str(generation_id),
            expected_project_fingerprint=request.expected_project_fingerprint,
            take_id=str(request.take_id), take_sha256=request.take_sha256)
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
async def activate_generation(project_id: str, generation_id: str,
                              request: GenerationActivationRequest | None = None) -> FilesystemProjectResponse:
    try:
        return _filesystem_project_response(ProjectStore().activate_generation(
            project_id, generation_id,
            request.expected_project_fingerprint if request is not None else None,
        ))
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


@router.delete("/projects/{project_id}/subjects", response_model=FilesystemProjectResponse)
async def delete_sequence_subject(project_id: str, request: SequenceSubjectDeleteRequest) -> FilesystemProjectResponse:
    try:
        return _filesystem_project_response(ProjectStore().delete_sequence_subject(project_id, request.subject_id))
    except ProjectStoreError as exc:
        raise _filesystem_project_error(exc) from exc


@router.patch("/projects/{project_id}/subjects", response_model=FilesystemProjectResponse)
async def name_sequence_subject(project_id: str, request: SequenceSubjectNameRequest) -> FilesystemProjectResponse:
    try:
        return _filesystem_project_response(ProjectStore().set_subject_narrative_name(
            project_id, request.subject_id, request.narrative_name))
    except ProjectStoreError as exc:
        raise _filesystem_project_error(exc) from exc


@router.post("/projects/{project_id}/save-as", response_model=FilesystemProjectResponse, status_code=201)
async def save_project_as(project_id: str, request: ProjectSaveAsRequest) -> FilesystemProjectResponse:
    if request.story_memory is not None and (request.story_memory_generation_id is None or request.expected_project_fingerprint is None):
        raise HTTPException(400, "Story Memory Save As requires the selected Generation ID and loaded Project fingerprint")
    try:
        values = _continuity_kwargs(request.continuity)
        return _filesystem_project_response(
            ProjectStore().duplicate_project(
                project_id,
                request.name,
                copy_mode=request.copy_mode,
                keep_saved_prompts=request.keep_saved_prompts,
                active_workspace=request.workspace,
                character_profiles=request.character_profiles,
                story_idea=request.story_idea,
                story_creative_direction=request.story_creative_direction,
                story_planning_style=request.story_planning_style,
                story_memory=request.story_memory,
                story_memory_generation_id=request.story_memory_generation_id,
                expected_project_fingerprint=request.expected_project_fingerprint,
                dialogue_language=request.dialogue_language,
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
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid Story Memory or Project authoring state") from exc


class ClearGeneratedStateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    expected_project_fingerprint: str = Field(alias="expectedProjectFingerprint", pattern=r"^[0-9a-f]{64}$")
    clear_saved_prompts: bool = Field(default=False, alias="clearSavedPrompts", strict=True)


@router.post("/projects/{project_id}/clear-generated-state", response_model=FilesystemProjectResponse)
async def clear_generated_state(project_id: UUID, request: ClearGeneratedStateRequest):
    try:
        return _filesystem_project_response(ProjectStore().clear_generated_state(
            str(project_id), request.expected_project_fingerprint,
            clear_saved_prompts=request.clear_saved_prompts))
    except ProjectStoreError as exc:
        raise _filesystem_project_error(exc) from exc


@router.post(
    "/projects/{project_id}/generations/{generation_id}/infer-plan",
    response_model=AssistedGenerationPlan,
)
async def infer_generation_plan(project_id: str, generation_id: str, request: PlanningInferenceRequest) -> AssistedGenerationPlan:
    try:
        store = ProjectStore()
        previous = store.read_predecessor_generation(project_id, generation_id)
        if previous is None:
            raise GenerationNotFoundError("Generation 1 has no predecessor to infer from")
        request._narrative_subjects = store.generation_narrative_subjects(project_id, request.workspace)
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
        store = ProjectStore()
        store.read_generation(project_id, generation_id)
        request._narrative_subjects = store.generation_narrative_subjects(project_id, request.workspace)
        return await revise_plan(request)
    except ProjectStoreError as exc:
        raise _filesystem_project_error(exc) from exc
    except LLMBackendError as exc:
        raise _http_error(exc) from exc


@router.post("/projects/{project_id}/story-plan/next", response_model=StoryNextProposal)
async def propose_story_next(project_id: str, request: StoryNextRequest, http_request: Request):
    try:
        return await run_until_disconnect(http_request, plan_next(ProjectStore(), project_id, request))
    except ProjectStoreError as exc:
        raise _filesystem_project_error(exc) from exc
    except LLMBackendError as exc:
        raise _http_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/projects/{project_id}/story-plan/next/apply", response_model=FilesystemProjectResponse)
async def apply_story_next(project_id: str, request: StoryNextApplyRequest):
    try:
        return _filesystem_project_response(ProjectStore().apply_story_plan_next(project_id, request.draft))
    except ProjectStoreError as exc:
        raise _filesystem_project_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/projects/{project_id}/story-plan/initial-range", response_model=StoryInitialRangeProposal)
async def propose_story_initial_range(project_id: str, request: StoryInitialRangeRequest, http_request: Request):
    try:
        return await run_until_disconnect(http_request, build_initial_range(ProjectStore(), project_id, request))
    except StaleProjectSnapshotError as exc:
        logger.warning("Initial Range failed: category=story_snapshot_stale status=409 detail=%s", str(exc))
        raise _filesystem_project_error(exc) from exc
    except ProjectStoreError as exc:
        raise _filesystem_project_error(exc) from exc
    except LLMBackendError as exc:
        logger.warning("Initial Range failed: category=%s status=%s upstream_status=%s detail=%s",
                       exc.category, exc.status_code, exc.upstream_status, exc.safe_message)
        raise _http_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/projects/{project_id}/story-plan/initial-range/apply", response_model=FilesystemProjectResponse)
async def apply_story_initial_range(project_id: str, request: StoryInitialRangeApplyRequest):
    try:
        return _filesystem_project_response(ProjectStore().apply_story_initial_range(project_id, request.draft))
    except ProjectStoreError as exc:
        raise _filesystem_project_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/projects/{project_id}/story-plan/insert", response_model=StoryInsertProposal)
async def propose_story_insert(project_id: str, request: StoryInsertRequest, http_request: Request):
    try:
        return await run_until_disconnect(http_request, plan_insert(ProjectStore(), project_id, request))
    except ProjectStoreError as exc:
        raise _filesystem_project_error(exc) from exc
    except LLMBackendError as exc:
        raise _http_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/projects/{project_id}/story-plan/insert/apply", response_model=FilesystemProjectResponse)
async def apply_story_insert(project_id: str, request: StoryInsertApplyRequest):
    try:
        return _filesystem_project_response(ProjectStore().apply_story_plan_insert(project_id, request.draft))
    except ProjectStoreError as exc:
        raise _filesystem_project_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/projects/{project_id}/story-plan/replan", response_model=StoryReplanProposal)
async def propose_story_replan(project_id: str, request: StoryReplanRequest, http_request: Request):
    try:
        return await run_until_disconnect(http_request, plan_replan(ProjectStore(), project_id, request))
    except ProjectStoreError as exc:
        raise _filesystem_project_error(exc) from exc
    except LLMBackendError as exc:
        raise _http_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/projects/{project_id}/story-plan/replan/apply", response_model=FilesystemProjectResponse)
async def apply_story_replan(project_id: str, request: StoryReplanApplyRequest):
    try:
        return _filesystem_project_response(ProjectStore().apply_story_plan_replan(project_id, request.draft))
    except ProjectStoreError as exc:
        raise _filesystem_project_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/projects/{project_id}/story-plan/build", response_model=StoryPlanProposal)
async def build_project_story_plan(project_id: str, request: StoryBuildRequest, http_request: Request):
    try:
        return await run_until_disconnect(http_request, build_story_plan(ProjectStore(), project_id, request))
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
async def revise_project_story_plan(project_id: str, request: StoryRevisionRequest, http_request: Request):
    try:
        return await run_until_disconnect(http_request, revise_story_plan(ProjectStore(), project_id, request))
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


async def _resolve_narrative_request(request):
    try:
        return await resolve_narrative_request_async(request)
    except ProjectStoreError as exc:
        raise _filesystem_project_error(exc) from exc


@router.post("/assemble")
async def assemble(request: GenerationRequest, http_request: Request = None) -> dict:
    request = (await run_until_disconnect(http_request, _resolve_narrative_request(request))
               if http_request is not None else await _resolve_narrative_request(request))
    if request.workspace.dialogue.strip():
        initial = assemble_prompt(workspace_for_h3(request.prompt_workspace), request.llm.supports_vision,
                                  request.narrative_subjects, request.continuity_context, request.story_context)
        try:
            preflight_request_capacity(prepare_messages(initial.messages, request.llm.reasoning_mode), request.llm, purpose="H3 generation")
        except LLMBackendError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    resolution = await resolve_dialogue(request.workspace, request.llm)
    request = request.model_copy(update={"workspace": request.workspace.model_copy(update={"dialogue_resolution": resolution})})
    assembled = assemble_prompt(workspace_for_h3(request.prompt_workspace), request.llm.supports_vision, request.narrative_subjects, request.continuity_context, request.story_context)
    capacity = request_capacity_diagnostics(prepare_messages(assembled.messages, request.llm.reasoning_mode), request.llm)
    return {
        "instructions": assembled.inspector_text,
        "warnings": assembled.warnings,
        "manifest": build_reference_manifest(request.prompt_workspace, request.narrative_subjects),
        "capacity": capacity,
    }


@router.post("/dialogue/resolve", response_model=DialogueResolutionResponse)
async def resolve_exact_dialogue(request: DialogueResolutionRequest) -> DialogueResolutionResponse:
    try:
        native_request = None
        if request.filesystem_project_id:
            native_request = await _resolve_narrative_request(GenerationRequest.model_validate({
                "filesystemProjectId": request.filesystem_project_id, "generationId": request.generation_id,
                "authoringPath": request.authoring_path,
                "workspace": request.workspace.model_dump(mode="json", by_alias=True),
                "llm": request.llm}))
        resolution = await resolve_dialogue(request.workspace, request.llm)
        if native_request is not None:
            from miniconstruct.generation import revalidate_native_authoring
            revalidate_native_authoring(native_request)
        return DialogueResolutionResponse(resolution=resolution)
    except ProjectStoreError as exc:
        raise _filesystem_project_error(exc) from exc
    except LLMBackendError as exc:
        raise _http_error(exc) from exc


@router.post("/reference-manifest")
async def reference_manifest(request: ReferenceManifestRequest) -> dict:
    """Return canonical H3 labels for the live authoring state."""
    projection = Workspace.model_construct(mode=request.mode, assets=request.assets, subjects=request.subjects)
    from miniconstruct.execution_routing import prompt_projection
    try:
        handoff = None
        if request.filesystem_project_id:
            from miniconstruct.visual_handoff import handoff_asset
            handoff = ProjectStore().visual_handoff(request.filesystem_project_id, request.generation_id,
                                                  projection, strict=False)
            if handoff.policy and not handoff.warnings:
                handoff.asset = handoff_asset(handoff.policy)
                projection = handoff.workspace(projection)
        return {"entries": build_h3_input_map(prompt_projection(projection, request.execution_routing)),
                "visualHandoff": handoff.summary() if handoff else None}
    except ProjectStoreError as exc:
        raise _filesystem_project_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/validate-project")
async def validate_project(project: ProjectEnvelope) -> dict:
    return project.model_dump(mode="json", by_alias=True)


@router.post("/validate")
async def validate(request: ValidationRequest) -> dict:
    from miniconstruct.execution_routing import prompt_projection
    try:
        dialogue_workspace = (ProjectStore().read_generation(request.filesystem_project_id, request.generation_id).workspace
            if request.filesystem_project_id else Workspace.model_construct(dialogue=request.dialogue,
                dialogue_language=request.dialogue_language, dialogue_resolution=request.dialogue_resolution))
        projection = Workspace.model_construct(mode=request.mode, assets=request.assets)
        if request.filesystem_project_id:
            handoff = await ProjectStore().visual_handoff_async(request.filesystem_project_id, request.generation_id,
                                                               projection, materialize=True)
            projection = handoff.workspace(projection)
        projection = prompt_projection(projection, request.execution_routing)
    except ProjectStoreError as exc:
        raise _filesystem_project_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    has_dialogue = bool(dialogue_workspace.dialogue.strip())
    resolution_required = has_dialogue and dialogue_workspace.dialogue_language != DialogueLanguage.AS_WRITTEN
    dialogue_current = not resolution_required or resolution_is_current(dialogue_workspace)
    dialogue = workspace_for_h3(dialogue_workspace).dialogue if has_dialogue and dialogue_current else ""
    result = validate_prompt(
        request.prompt,
        request.mode,
        request.duration_seconds,
        projection.assets,
        dialogue,
        request.shots,
        native_i2va=projection._native_i2va,
    )
    if resolution_required and not dialogue_current:
        from miniconstruct.models.api import ValidationFinding
        result.findings.append(ValidationFinding(severity="WARNING", code="spoken_output_unavailable",
            message="Current resolved Spoken Output is missing or stale; exact translated-dialogue comparison was skipped.",
            category="workspace_consistency"))
    return result.model_dump()


@router.post("/generate", response_model=GenerationResponse)
async def generate(request: GenerationRequest, http_request: Request = None) -> GenerationResponse:
    try:
        result = (await run_until_disconnect(http_request, generate_workspace_prompts(request))
                  if http_request is not None else await generate_workspace_prompts(request))
    except ProjectStoreError as exc:
        raise _filesystem_project_error(exc) from exc
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
    request = await _resolve_narrative_request(request)
    resolution = await resolve_dialogue(request.workspace, request.llm)
    authoring_workspace = request.workspace.model_copy(update={"dialogue_resolution": resolution})
    request = request.model_copy(update={"workspace": authoring_workspace})
    prompt_workspace = workspace_for_h3(request.prompt_workspace)
    assembled = assemble_prompt(prompt_workspace, request.llm.supports_vision, request.narrative_subjects, request.continuity_context, request.story_context)
    assembly_ms = (time.perf_counter() - assembly_started) * 1000
    preview_payload, _ = build_generation_payload(
        request.llm.model_copy(update={"seed": None}), assembled.messages, stream=True
    )
    try:
        capacity = preflight_request_capacity(preview_payload["messages"], request.llm, purpose="H3 generation")
    except LLMBackendError as exc:
        yield _stream_event("error", {"variation": 0, "message": str(exc), "partial": False})
        return
    fingerprint = cache_input_fingerprint(preview_payload)
    image_dimensions = [
        {"width": asset.image.width, "height": asset.image.height}
        for asset in prompt_workspace.assets
        if asset.image is not None
    ]
    sent_vision_images = model_vision_stats(preview_payload["messages"])
    yield _stream_event("start", {
        "variations": request.workspace.variations,
        "warnings": assembled.warnings + ([capacity["completionBudgetWarning"]] if capacity["completionBudgetWarning"] else []),
        "diagnostics": {
            "authoringPath": request.authoring_path,
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
            "estimatedInputTokens": capacity["estimatedInputTokens"],
            "contextWindow": capacity["contextWindow"],
            "maxOutputTokens": capacity["requestedMaxOutputTokens"],
            "safetyTokens": capacity["safetyTokens"],
            "estimatedCompletionHeadroom": capacity["estimatedCompletionHeadroom"],
            "completionBudgetFits": capacity["completionBudgetFits"],
            "completionBudgetWarning": capacity["completionBudgetWarning"],
            "imageCount": len(image_dimensions),
            "imageDimensions": image_dimensions,
            "sentVisionImages": sent_vision_images,
            "sentVisionEncodedBytes": sum(item["encodedBytes"] for item in sent_vision_images),
            "visionTransformVersion": VISION_TRANSFORM_VERSION if sent_vision_images else None,
            "fingerprint": fingerprint,
            "reasoningMode": request.llm.reasoning_mode.value,
            "backend": request.llm.endpoint.display_name,
            "model": request.llm.selected_model_id,
        },
    })
    try:
        for variation_index, seed in enumerate(_resolved_generation_seeds(request)):
            from miniconstruct.generation import revalidate_native_authoring
            revalidate_native_authoring(request)
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
                from miniconstruct.generation import revalidate_native_authoring
                revalidate_native_authoring(request)
                validation = validate_workspace_prompt(prompt, prompt_workspace)
                completed_at = time.perf_counter()
                yield _stream_event("complete", {
                    "variation": variation_index,
                    "prompt": prompt,
                    "validation": validation.model_dump(mode="json"),
                    "referenceMap": [ref.model_dump(by_alias=True) for ref in request.reference_map],
                    "sourceFingerprint": request.source_fingerprint,
                    "authoringPath": request.authoring_path,
                    "metrics": metrics(completed_at),
                    "seed": getattr(client, "effective_seed", seed),
                })
        yield _stream_event("done", {"variations": request.workspace.variations})
    except ProjectStoreError as exc:
        yield _stream_event("error", {"message": str(exc), "partial": True})
    except asyncio.CancelledError:
        # Normal browser Stop/disconnect. Context managers close upstream promptly.
        raise


@router.post("/generate-stream")
async def generate_stream(request: GenerationRequest, http_request: Request = None) -> StreamingResponse:
    request = (await run_until_disconnect(http_request, _resolve_narrative_request(request))
               if http_request is not None else await _resolve_narrative_request(request))
    return StreamingResponse(
        stream_generation_events(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def stream_revision_events(
    request: RevisionRequest,
    client_factory: Callable[[LLMSettings], OpenAICompatibleClient] = OpenAICompatibleClient,
) -> AsyncIterator[str]:
    request = await _resolve_narrative_request(request)
    assembled = assemble_revision_prompt(request)
    try:
        capacity = preflight_request_capacity(prepare_messages(assembled.messages, request.llm.reasoning_mode), request.llm, purpose="H3 revision")
    except LLMBackendError as exc:
        yield _stream_event("error", {"message": str(exc), "partial": False})
        return
    yield _stream_event("start", {"warnings": assembled.warnings + ([capacity["completionBudgetWarning"]] if capacity["completionBudgetWarning"] else []),
                                  "capacity": capacity, "outputIndex": request.output_index})
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
            "sourceFingerprint": request.source_fingerprint,
            "authoringPath": request.authoring_path,
            "originalValidation": original_validation.model_dump(mode="json"),
        })
        yield _stream_event("done", {})
    except asyncio.CancelledError:
        raise


@router.post("/revise-stream")
async def revise_stream(request: RevisionRequest, http_request: Request = None) -> StreamingResponse:
    request = (await run_until_disconnect(http_request, _resolve_narrative_request(request))
               if http_request is not None else await _resolve_narrative_request(request))
    return StreamingResponse(
        stream_revision_events(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/repair", response_model=GeneratedVariation)
async def repair(repair_request: RepairRequest, http_request: Request = None) -> GeneratedVariation:
    request = (await run_until_disconnect(http_request, _resolve_narrative_request(repair_request))
               if http_request is not None else await _resolve_narrative_request(repair_request))
    current_validation = validate_workspace_prompt(request.prompt, request.prompt_workspace)
    structural_failures = [
        finding
        for finding in current_validation.findings
        if finding.severity == "ERROR" and finding.category == "structural"
    ]
    if not structural_failures:
        return GeneratedVariation(referenceMap=request.reference_map, sourceFingerprint=request.source_fingerprint, authoringPath=request.authoring_path, prompt=request.prompt, validation=current_validation)
    normalized_prompt = normalize_comparison_scale_references(
        canonicalize_bare_reference_labels(request.prompt, request.workspace.mode), request.workspace,
    )
    normalized_prompt = repair_identity_provenance(
        normalized_prompt, request.prompt_workspace, current_validation.findings,
    )
    normalized_validation = validate_workspace_prompt(normalized_prompt, request.prompt_workspace)
    remaining_failures = [
        finding
        for finding in normalized_validation.findings
        if finding.severity == "ERROR" and finding.category == "structural"
    ]
    if not remaining_failures:
        return GeneratedVariation(
            referenceMap=request.reference_map, sourceFingerprint=request.source_fingerprint,
            authoringPath=request.authoring_path,
            prompt=normalized_prompt,
            validation=normalized_validation,
        )
    assembled = assemble_repair_prompt(request.model_copy(update={"prompt": normalized_prompt}), remaining_failures)
    try:
        # Repair is deliberately syntax-focused: it neither resolves nor sends a creative seed.
        repair_settings = request.llm.model_copy(update={"seed": None})
        capacity = preflight_request_capacity(prepare_messages(assembled.messages, repair_settings.reasoning_mode), repair_settings, purpose="H3 repair")
        async def model_repair() -> str:
            async with OpenAICompatibleClient(repair_settings) as client:
                return await client.generate(assembled.messages)

        prompt = await run_until_disconnect(http_request, model_repair()) if http_request is not None else await model_repair()
    except LLMBackendError as exc:
        raise _http_error(exc) from exc
    prompt = normalize_comparison_scale_references(
        canonicalize_bare_reference_labels(prompt, request.workspace.mode), request.workspace, normalized_prompt,
    )
    return GeneratedVariation(referenceMap=request.reference_map, sourceFingerprint=request.source_fingerprint, authoringPath=request.authoring_path, prompt=prompt,
                              validation=validate_workspace_prompt(prompt, request.prompt_workspace),
                              warnings=[capacity["completionBudgetWarning"]] if capacity["completionBudgetWarning"] else [])
