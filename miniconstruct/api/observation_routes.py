"""ID-scoped observation commands; derived paths are never client inputs."""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import Field

from miniconstruct.api.take_routes import TakeRoute
from miniconstruct.api.request_lifetime import run_until_disconnect
from miniconstruct.execution import StrictModel
from miniconstruct.llm.client import LLMBackendError
from miniconstruct.models.api import LLMSettings
from miniconstruct.models.observations import ObservedEndState
from miniconstruct.observation_analysis import analyze_selected_take
from miniconstruct.project_store import ProjectStore


class AnalyzeRequest(StrictModel):
    llm: LLMSettings
    expected_project_fingerprint: str = Field(alias="expectedProjectFingerprint", pattern=r"^[0-9a-f]{64}$")
    replace_corrections: bool = Field(default=False, alias="replaceCorrections")


class CorrectionRequest(StrictModel):
    expected_project_fingerprint: str = Field(alias="expectedProjectFingerprint", pattern=r"^[0-9a-f]{64}$")
    effective_observed_state: ObservedEndState | None = Field(default=None, alias="effectiveObservedState")
    reset: bool = False


router = APIRouter(prefix="/api/projects/{project_id}/generations/{generation_id}", route_class=TakeRoute)


@router.get("/visual-handoff/preview")
def handoff_preview(project_id: UUID, generation_id: UUID):
    handoff = ProjectStore().visual_handoff(str(project_id), str(generation_id), materialize=True)
    if handoff.path is None:
        raise HTTPException(422, "; ".join(handoff.warnings) or "No Visual Handoff for this Generation")
    return FileResponse(handoff.path, media_type=handoff.asset.mime_type,
                        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-cache"})


@router.post("/selected-take/analyze")
async def analyze(project_id: UUID, generation_id: UUID, request: AnalyzeRequest, http_request: Request):
    try:
        return await run_until_disconnect(
            http_request,
            analyze_selected_take(ProjectStore(), str(project_id), str(generation_id), request),
        )
    except LLMBackendError as exc:
        raise HTTPException(exc.status_code, exc.safe_message) from exc


@router.put("/takes/{take_id}/observation")
def correct(project_id: UUID, generation_id: UUID, take_id: UUID, request: CorrectionRequest):
    if (request.effective_observed_state is None) != request.reset:
        raise HTTPException(422, "Choose either corrected state or Reset to Model")
    return ProjectStore().correct_observation(str(project_id), str(generation_id), take_id,
        request.expected_project_fingerprint, request.effective_observed_state, reset=request.reset)


@router.get("/takes/{take_id}/evidence/{identity}")
def evidence(project_id: UUID, generation_id: UUID, take_id: UUID, identity: Literal["endpoint", "contact-sheet"]):
    path = ProjectStore().observation_evidence(str(project_id), str(generation_id), take_id, identity)
    return FileResponse(path, media_type="image/jpeg", headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-cache"})
