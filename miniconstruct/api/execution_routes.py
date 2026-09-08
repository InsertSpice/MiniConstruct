"""Explicit application-local execution configuration API."""
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from miniconstruct.execution import (
    ConfigNotFound, Connection, ConnectionWrite, ExecutionError, ExecutionStore,
    InvalidBindings, MAX_WORKFLOW_BYTES, Profile, ProfileCreate, ProfileUpdate,
    WorkflowImport, binding_errors, parse_workflow, test_connection, workflow_fingerprint,
    profile_readiness_errors,
)
from miniconstruct.models.workspace import AssetKind, H3Mode
from miniconstruct.project_store import ProjectStore, ProjectStoreError, ProjectNotFoundError, GenerationNotFoundError
from miniconstruct.execution_state import SelectionWrite, SelectionStatus
from miniconstruct.workflow_preparation import PrepareRequest, PreparedWorkflow, prepare_workflow
from miniconstruct.execution_routing import GenerationRouting, effective_references
from miniconstruct.h3.builder import numbered_assets
from miniconstruct.execution_send import ExecutionSendRequest, ExecutionSendResult, SendError, send_generation


class ExecutionRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def bounded(request: Request):
            # Bound before JSON decoding, including chunked requests. The envelope
            # gets room for JSON escaping; the graph itself is separately bounded.
            raw = bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > 4 * MAX_WORKFLOW_BYTES:
                    raise HTTPException(413, "Execution request exceeds 8 MiB")
            request._body = bytes(raw)
            try:
                return await handler(request)
            except RequestValidationError as exc:
                # Default Pydantic errors echo inputs, potentially entire workflows.
                raise HTTPException(422, [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()]) from exc
            except RecursionError as exc:
                raise HTTPException(422, "Execution request JSON is too deeply nested") from exc
            except ConfigNotFound as exc:
                raise HTTPException(404, str(exc)) from exc
            except (ProjectNotFoundError, GenerationNotFoundError) as exc:
                raise HTTPException(404, str(exc)) from exc
            except ProjectStoreError as exc:
                raise HTTPException(422, str(exc)) from exc
            except InvalidBindings as exc:
                raise HTTPException(422, str(exc)) from exc
            except ExecutionError as exc:
                raise HTTPException(500, str(exc)) from exc
        return bounded


router = APIRouter(prefix="/api/execution", route_class=ExecutionRoute)


@router.post("/send", response_model=ExecutionSendResult)
def send(body: ExecutionSendRequest):
    try:
        return send_generation(body)
    except SendError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/capabilities")
def capabilities():
    return {"compatibleModes": list(H3Mode), "assetKinds": list(AssetKind), "maxWorkflowBytes": MAX_WORKFLOW_BYTES}


@router.get("/connections", response_model=list[Connection])
def connections():
    return ExecutionStore().list("connections")


@router.post("/connections", response_model=Connection, status_code=201)
def create_connection(body: ConnectionWrite):
    return ExecutionStore().save("connections", body)


@router.get("/connections/{record_id}", response_model=Connection)
def connection(record_id: UUID):
    return ExecutionStore().get("connections", record_id)


@router.put("/connections/{record_id}", response_model=Connection)
def update_connection(record_id: UUID, body: ConnectionWrite):
    return ExecutionStore().save("connections", body, record_id)


@router.delete("/connections/{record_id}", status_code=204)
def delete_connection(record_id: UUID):
    ExecutionStore().delete("connections", record_id)
    return Response(status_code=204)


@router.post("/connections/{record_id}/test")
async def probe_connection(record_id: UUID):
    return await test_connection(ExecutionStore().get("connections", record_id))


@router.get("/profiles")
def profiles():
    # List summaries omit potentially large graphs. Get supplies the source graph.
    return [{**profile.model_dump(mode="json", by_alias=True, exclude={"workflow", "bindings"}),
             "preparationIssues": profile_readiness_errors(profile.workflow, profile.bindings)}
            for profile in ExecutionStore().list("profiles")]


@router.post("/profiles/validate")
def validate_draft(body: ProfileCreate):
    graph = parse_workflow(body.workflow_json)
    errors = profile_readiness_errors(graph, body.bindings)
    return {"valid": not errors, "errors": errors, "fingerprint": workflow_fingerprint(graph)}


@router.post("/profiles", response_model=Profile, status_code=201)
def create_profile(body: ProfileCreate):
    return ExecutionStore().save("profiles", body)


@router.get("/profiles/{record_id}", response_model=Profile)
def profile(record_id: UUID):
    return ExecutionStore().get("profiles", record_id)


@router.put("/profiles/{record_id}", response_model=Profile)
def update_profile(record_id: UUID, body: ProfileUpdate):
    return ExecutionStore().save("profiles", body, record_id)


@router.put("/profiles/{record_id}/workflow", response_model=Profile)
def replace_workflow(record_id: UUID, body: WorkflowImport):
    return ExecutionStore().save("profiles", body, record_id)


@router.post("/profiles/{record_id}/validate")
def validate_profile(record_id: UUID, body: ProfileUpdate | None = None):
    saved = ExecutionStore().get("profiles", record_id)
    graph = parse_workflow(body.workflow_json) if body and body.workflow_json is not None else saved.workflow
    errors = profile_readiness_errors(graph, body.bindings if body else saved.bindings)
    return {"valid": not errors, "errors": errors, "fingerprint": workflow_fingerprint(graph)}


@router.delete("/profiles/{record_id}", status_code=204)
def delete_profile(record_id: UUID):
    ExecutionStore().delete("profiles", record_id)
    return Response(status_code=204)


@router.get("/projects/{project_id}/generations/{generation_id}/selection", response_model=SelectionStatus)
def selection_status(project_id: UUID, generation_id: UUID):
    return ProjectStore().execution_snapshot(str(project_id), str(generation_id))[1]


@router.put("/projects/{project_id}/generations/{generation_id}/selection", response_model=SelectionStatus)
def select_prompt(project_id: UUID, generation_id: UUID, body: SelectionWrite):
    return ProjectStore().select_execution_prompt(str(project_id), str(generation_id), body)


@router.delete("/projects/{project_id}/generations/{generation_id}/selection", response_model=SelectionStatus)
def clear_selection(project_id: UUID, generation_id: UUID):
    return ProjectStore().clear_execution_prompt(str(project_id), str(generation_id))


@router.post("/prepare", response_model=PreparedWorkflow)
def prepare(body: PrepareRequest):
    profile = ExecutionStore().get("profiles", body.profile_id)
    return prepare_workflow(ProjectStore(), str(body.project_id), str(body.generation_id), profile)


@router.get("/projects/{project_id}/generations/{generation_id}/routing")
def routing_status(project_id: UUID, generation_id: UUID):
    store = ProjectStore()
    workspace = store.read_generation(str(project_id), str(generation_id)).workspace
    routing = store.execution_routing(str(project_id), str(generation_id))
    try:
        references = effective_references(workspace, routing)
    except ValueError as exc:
        raise InvalidBindings([str(exc)]) from exc
    return {"routing": routing, "referenceMap": references,
            "assets": [{"id": asset.id, "kind": asset.kind, "label": label, "filename": asset.filename}
                       for asset, label in numbered_assets(workspace)]}


@router.put("/projects/{project_id}/generations/{generation_id}/routing")
def update_routing(project_id: UUID, generation_id: UUID, body: GenerationRouting):
    ProjectStore().set_execution_routing(str(project_id), str(generation_id), body)
    return routing_status(project_id, generation_id)
