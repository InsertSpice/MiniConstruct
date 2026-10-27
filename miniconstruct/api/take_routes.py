"""Explicit actual-video association. Upload body is streamed, never JSON/base64."""
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, WebSocket
from fastapi.responses import FileResponse
from fastapi.routing import APIRoute
from pydantic import ValidationError

from miniconstruct.models.takes import TakeImport
from miniconstruct.project_store import ProjectStore, ProjectStoreError, ProjectNotFoundError, GenerationNotFoundError, StaleProjectSnapshotError


class TakeRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe(request):
            try:
                return await handler(request)
            except (ProjectNotFoundError, GenerationNotFoundError) as exc:
                raise HTTPException(404, "Project, Generation or Take media was not found") from exc
            except StaleProjectSnapshotError as exc:
                raise HTTPException(409, str(exc)) from exc
            except ValidationError as exc:
                raise HTTPException(422, [{"loc": e["loc"], "msg": e["msg"]} for e in exc.errors(include_input=False)]) from exc
            except ProjectStoreError as exc:
                raise HTTPException(413 if "exceeds" in str(exc) else 422, str(exc)) from exc
            except OSError as exc:
                raise HTTPException(500, "Output storage could not be accessed; existing Project metadata is preserved") from exc
        return safe


router = APIRouter(prefix="/api/projects/{project_id}/generations/{generation_id}", route_class=TakeRoute)


async def has_payload(request):
    if request.query_params:
        return True
    async for chunk in request.stream():
        if chunk:
            return True
    return False


@router.post("/render-submissions/{submission_id}/reconcile")
async def reconcile(project_id: UUID, generation_id: UUID, submission_id: UUID, request: Request):
    if await has_payload(request):
        raise HTTPException(422, "Reconciliation accepts only recorded Project, Generation and submission IDs")
    from miniconstruct.render_reconciliation import reconcile_submission
    return await reconcile_submission(str(project_id), str(generation_id), submission_id)


@router.post("/render-submissions/{submission_id}/recover")
async def recover(project_id: UUID, generation_id: UUID, submission_id: UUID, request: Request):
    if await has_payload(request):
        raise HTTPException(422, "Recover accepts only recorded Project, Generation and submission IDs")
    from miniconstruct.render_reconciliation import reconcile_submission

    result = await reconcile_submission(str(project_id), str(generation_id), submission_id)
    return {**result, "recoveredSubmissionId": str(submission_id), "newJobSubmitted": False}


@router.post("/render-submissions/{submission_id}/retry")
async def retry_render(project_id: UUID, generation_id: UUID, submission_id: UUID, request: Request):
    from miniconstruct.execution import InvalidBindings
    from miniconstruct.execution_send import SendError, retry_generation
    from starlette.concurrency import run_in_threadpool

    if await has_payload(request):
        raise HTTPException(422, "Retry accepts only recorded Project, Generation and submission IDs")

    try:
        return await run_in_threadpool(retry_generation, project_id, generation_id, submission_id)
    except InvalidBindings as exc:
        raise HTTPException(409, str(exc)) from exc
    except SendError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post("/render-submissions/{submission_id}/cancel")
async def cancel_render(project_id: UUID, generation_id: UUID, submission_id: UUID, request: Request):
    if await has_payload(request):
        raise HTTPException(422, "Cancellation accepts only recorded Project, Generation and submission IDs")
    from miniconstruct.comfy_live import cancel_submission
    from miniconstruct.execution import ConfigNotFound
    try:
        return await cancel_submission(str(project_id), str(generation_id), submission_id)
    except ConfigNotFound:
        raise HTTPException(409, "The recorded ComfyUI Connection is unavailable") from None
    except httpx.RequestError:
        raise HTTPException(502, "Could not reach the recorded ComfyUI Connection for exact cancellation") from None
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from None


@router.websocket("/render-submissions/{submission_id}/live")
async def live_render(websocket: WebSocket, project_id: UUID, generation_id: UUID, submission_id: UUID):
    from miniconstruct.comfy_live import observe
    from miniconstruct.execution import ConfigNotFound
    origin = websocket.headers.get("origin")
    if origin and urlsplit(origin).netloc.lower() != websocket.headers.get("host", "").lower():
        await websocket.close(code=1008)
        return
    try:
        await observe(websocket, str(project_id), str(generation_id), submission_id)
    except (ProjectNotFoundError, GenerationNotFoundError, ProjectStoreError, ConfigNotFound):
        await websocket.close(code=1008)


@router.post("/render-submissions/{submission_id}/outputs/{output_id}/import")
async def import_output(project_id: UUID, generation_id: UUID, submission_id: UUID, output_id: str, request: Request):
    import re
    if await has_payload(request) or not re.fullmatch(r"[0-9a-f]{64}", output_id):
        raise HTTPException(422, "Choose a recorded output candidate; arbitrary output metadata is not accepted")
    from miniconstruct.render_reconciliation import reconcile_submission
    return await reconcile_submission(str(project_id), str(generation_id), submission_id, output_id=output_id)


@router.get("/outputs")
def outputs(project_id: UUID, generation_id: UUID):
    return ProjectStore().generation_outputs(str(project_id), str(generation_id))


@router.post("/takes", status_code=201)
async def import_take(project_id: UUID, generation_id: UUID, request: Request,
                      filename: str = Query(min_length=1, max_length=255),
                      sourceKind: Literal["comfyui_submission", "selected_prompt", "external_unknown"] = Query(),
                      renderSubmissionId: UUID | None = None, selectedPromptId: UUID | None = None):
    # A small allowlist prevents fake client prompt metadata from appearing accepted.
    if set(request.query_params) - {"filename", "sourceKind", "renderSubmissionId", "selectedPromptId"}:
        raise HTTPException(422, "Unexpected import metadata; provenance is captured from server-owned records")
    provenance = TakeImport(sourceKind=sourceKind, renderSubmissionId=renderSubmissionId, selectedPromptId=selectedPromptId)
    return await ProjectStore().import_take(str(project_id), str(generation_id), request.stream(), filename, provenance)


@router.get("/takes/{take_id}")
def detail(project_id: UUID, generation_id: UUID, take_id: UUID):
    return ProjectStore().take_detail(str(project_id), str(generation_id), take_id)


@router.get("/takes/{take_id}/media")
def media(project_id: UUID, generation_id: UUID, take_id: UUID):
    path, mime = ProjectStore().take_media(str(project_id), str(generation_id), take_id)
    return FileResponse(path, media_type=mime, headers={"X-Content-Type-Options": "nosniff"})


@router.put("/selected-take/{take_id}")
def select(project_id: UUID, generation_id: UUID, take_id: UUID):
    return ProjectStore().select_take(str(project_id), str(generation_id), take_id)


@router.delete("/selected-take")
def clear(project_id: UUID, generation_id: UUID):
    return ProjectStore().select_take(str(project_id), str(generation_id), None)


@router.delete("/takes/{take_id}")
def delete(project_id: UUID, generation_id: UUID, take_id: UUID):
    return ProjectStore().delete_take(str(project_id), str(generation_id), take_id)


@router.put("/takes/{take_id}/media")
async def reattach(project_id: UUID, generation_id: UUID, take_id: UUID, request: Request,
                   filename: str = Query(min_length=1, max_length=255)):
    return await ProjectStore().import_take(str(project_id), str(generation_id), request.stream(), filename, reattach_id=take_id)
