"""Explicit application-local execution configuration API."""
from uuid import UUID
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from miniconstruct.execution import (
    ConfigNotFound, Connection, ConnectionWrite, ExecutionError, ExecutionStore,
    InvalidBindings, MAX_WORKFLOW_BYTES, Profile, ProfileCreate, ProfileUpdate,
    WorkflowImport, binding_errors, parse_workflow, test_connection, workflow_fingerprint,
    profile_readiness_errors,
    native_mapping_errors,
    control_errors, control_value,
)
from miniconstruct.models.workspace import AssetKind, H3Mode
from miniconstruct.project_store import ProjectStore, ProjectStoreError, ProjectNotFoundError, GenerationNotFoundError
from miniconstruct.execution_state import HistoryAdoptionWrite, ManualEditAdoptionWrite, SelectionWrite, SelectionStatus, SavedPromptWrite, SavedPromptStatus
from miniconstruct.execution_state import RendererSeedSettings
from miniconstruct.execution import StrictModel
from pydantic import Field
from miniconstruct.workflow_preparation import PrepareRequest, PreparedWorkflow, prepare_workflow
from miniconstruct.sequence_render_readiness import sequence_render_readiness
from miniconstruct.execution_routing import GenerationRouting, effective_references
from miniconstruct.h3.builder import numbered_assets
from miniconstruct.execution_send import ExecutionSendRequest, ExecutionSendResult, SendError, send_generation
from miniconstruct.native_continuation_models import ContinuationSettings, ContinuationDecision


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


class ExecutionDurationWrite(StrictModel):
    duration_seconds: float = Field(alias="durationSeconds", ge=4, le=15)
    expected_project_fingerprint: str = Field(alias="expectedProjectFingerprint", pattern=r"^[0-9a-f]{64}$")


class ExecutionSetupWrite(ExecutionDurationWrite):
    aspect_ratio: str = Field(alias="aspectRatio", min_length=1, max_length=40, strict=True)
    expected_project_edit_fingerprint: str | None = Field(default=None, alias="expectedProjectEditFingerprint", pattern=r"^[0-9a-f]{64}$")


@router.put("/projects/{project_id}/generations/{generation_id}/execution-setup")
def sync_execution_setup(project_id: UUID, generation_id: UUID, body: ExecutionSetupWrite):
    return ProjectStore().sync_execution_setup(str(project_id), str(generation_id), body.duration_seconds,
                                              body.expected_project_fingerprint, body.aspect_ratio,
                                              body.expected_project_edit_fingerprint)


@router.put("/projects/{project_id}/generations/{generation_id}/execution-duration")
def sync_execution_duration(project_id: UUID, generation_id: UUID, body: ExecutionDurationWrite):
    return ProjectStore().sync_execution_duration(str(project_id), str(generation_id), body.duration_seconds,
                                                   body.expected_project_fingerprint)


class RendererSeedSettingsWrite(StrictModel):
    profile_id: UUID = Field(alias="profileId")
    settings: RendererSeedSettings


class WorkflowControlValuesWrite(StrictModel):
    values: dict[str, object] = Field(max_length=128)


def control_status(project_id: UUID, generation_id: UUID, profile_id: UUID):
    from miniconstruct.workflow_controls import resolve_controls
    store, profile = ProjectStore(), ExecutionStore().get("profiles", profile_id)
    project, generation = store.workflow_control_values(str(project_id), str(generation_id), str(profile_id))
    from miniconstruct.preparation_diagnostics import PreparationDiagnostics
    diagnostics = PreparationDiagnostics()
    effective, warnings = resolve_controls(store, str(project_id), str(generation_id), profile, diagnostics=diagnostics)
    from miniconstruct.legacy_aspect_ratio import legacy_aspect_ratio_control
    legacy_ratio = legacy_aspect_ratio_control(profile)
    def valid_saved(control, values):
        if control.id not in values:
            return None, False
        try:
            return control_value(control, values[control.id]), True
        except ValueError:
            return None, False
    return {"profileId": profile_id, "controls": [
        {**item, "type": control.type, "minimum": control.minimum, "maximum": control.maximum,
         "h3SetupControlled": legacy_ratio is not None and control.id == legacy_ratio.id,
         "step": control.step, "choices": control.choices,
         "projectOverride": valid_saved(control, project)[0], "generationOverride": valid_saved(control, generation)[0],
         "hasProjectOverride": valid_saved(control, project)[1], "hasGenerationOverride": valid_saved(control, generation)[1]}
        for control, item in zip(profile.exposed_controls, effective)], "warnings": warnings,
        "information": diagnostics.messages("info"), "diagnostics": diagnostics.items}


@router.get("/projects/{project_id}/generations/{generation_id}/profiles/{profile_id}/controls")
def get_controls(project_id: UUID, generation_id: UUID, profile_id: UUID):
    return control_status(project_id, generation_id, profile_id)


@router.put("/projects/{project_id}/generations/{generation_id}/profiles/{profile_id}/controls/{scope}")
def save_controls(project_id: UUID, generation_id: UUID, profile_id: UUID, scope: Literal["project", "generation"], body: WorkflowControlValuesWrite):
    profile = ExecutionStore().get("profiles", profile_id)
    declared = {control.id: control for control in profile.exposed_controls}
    if body.values.keys() - declared.keys():
        raise InvalidBindings(["Unknown Workflow Control ID"])
    try:
        values = {key: control_value(declared[key], value) for key, value in body.values.items()}
    except ValueError as exc:
        raise InvalidBindings([str(exc)]) from exc
    ProjectStore().set_workflow_control_values(str(project_id), str(generation_id), str(profile_id), scope, values)
    return control_status(project_id, generation_id, profile_id)


@router.delete("/projects/{project_id}/generations/{generation_id}/profiles/{profile_id}/controls/{scope}")
def clear_controls(project_id: UUID, generation_id: UUID, profile_id: UUID, scope: Literal["project", "generation"]):
    ExecutionStore().get("profiles", profile_id)
    ProjectStore().set_workflow_control_values(str(project_id), str(generation_id), str(profile_id), scope, {})
    return control_status(project_id, generation_id, profile_id)


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


@router.get("/connections/{record_id}/local-input-status")
def local_input_status(record_id: UUID):
    from miniconstruct.local_staging import input_status
    return input_status(ExecutionStore().get("connections", record_id).local_input_directory)


@router.get("/profiles")
def profiles():
    # List summaries omit potentially large graphs. Get supplies the source graph.
    return [{**profile.model_dump(mode="json", by_alias=True, exclude={"workflow", "bindings"}),
             "rendererSeedBinding": any(binding.source.kind == "renderer_seed" for binding in profile.bindings),
             "preparationIssues": profile_readiness_errors(profile.workflow, profile.bindings) + native_mapping_errors(profile.workflow, profile.bindings, profile.native_continuation) + control_errors(profile.workflow, profile.exposed_controls, profile.bindings, profile.native_continuation)}
            for profile in ExecutionStore().list("profiles")]


@router.post("/profiles/validate")
def validate_draft(body: ProfileCreate):
    graph = parse_workflow(body.workflow_json)
    errors = profile_readiness_errors(graph, body.bindings) + native_mapping_errors(graph, body.bindings, body.native_continuation) + control_errors(graph, body.exposed_controls, body.bindings, body.native_continuation)
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


@router.get("/profiles/{record_id}/duplicate-draft", response_model=ProfileCreate)
def duplicate_profile_draft(record_id: UUID):
    return ExecutionStore().duplicate_profile_draft(record_id)


class EnumCapabilitiesRequest(StrictModel):
    connection_id: UUID = Field(alias="connectionId")


class AspectRatioCapabilitiesRequest(EnumCapabilitiesRequest):
    node_id: str = Field(alias="nodeId", min_length=1, max_length=80)
    input_name: str = Field(alias="inputName", min_length=1, max_length=160)


@router.post("/profiles/{record_id}/aspect-ratio-capabilities")
async def inspect_aspect_ratio_capabilities(record_id: UUID, body: AspectRatioCapabilitiesRequest):
    from miniconstruct.enum_capabilities import aspect_ratio_capabilities
    from miniconstruct.execution import ControlTarget
    store = ExecutionStore()
    return await aspect_ratio_capabilities(store.get("profiles", record_id),
        ControlTarget(nodeId=body.node_id, inputName=body.input_name),
        store.get("connections", body.connection_id))


@router.post("/profiles/{record_id}/controls/{control_id}/enum-capabilities")
async def inspect_enum_capabilities(record_id: UUID, control_id: str, body: EnumCapabilitiesRequest):
    from miniconstruct.enum_capabilities import enum_capabilities
    store = ExecutionStore()
    return await enum_capabilities(store.get("profiles", record_id), control_id,
                                   store.get("connections", body.connection_id))


@router.put("/profiles/{record_id}/workflow", response_model=Profile)
def replace_workflow(record_id: UUID, body: WorkflowImport):
    return ExecutionStore().save("profiles", body, record_id)


@router.post("/profiles/{record_id}/validate")
def validate_profile(record_id: UUID, body: ProfileUpdate | None = None):
    saved = ExecutionStore().get("profiles", record_id)
    graph = parse_workflow(body.workflow_json) if body and body.workflow_json is not None else saved.workflow
    metadata = body or saved
    errors = profile_readiness_errors(graph, metadata.bindings) + native_mapping_errors(graph, metadata.bindings, metadata.native_continuation) + control_errors(graph, metadata.exposed_controls, metadata.bindings, metadata.native_continuation)
    return {"valid": not errors, "errors": errors, "fingerprint": workflow_fingerprint(graph)}


@router.delete("/profiles/{record_id}", status_code=204)
def delete_profile(record_id: UUID):
    ExecutionStore().delete("profiles", record_id)
    return Response(status_code=204)


@router.get("/projects/{project_id}/generations/{generation_id}/selection", response_model=SelectionStatus)
def selection_status(project_id: UUID, generation_id: UUID):
    return ProjectStore().execution_snapshot(str(project_id), str(generation_id))[1]


@router.get("/projects/{project_id}/generations/{generation_id}/saved-prompt", response_model=SavedPromptStatus)
def saved_prompt_status(project_id: UUID, generation_id: UUID):
    return ProjectStore().saved_prompt_status(str(project_id), str(generation_id))


@router.put("/projects/{project_id}/generations/{generation_id}/saved-prompt", response_model=SavedPromptStatus)
def save_prompt(project_id: UUID, generation_id: UUID, body: SavedPromptWrite):
    return ProjectStore().save_prompt(str(project_id), str(generation_id), body)


@router.post("/projects/{project_id}/generations/{generation_id}/saved-prompt/{prompt_id}/load", response_model=SavedPromptStatus)
def activate_saved_prompt(project_id: UUID, generation_id: UUID, prompt_id: UUID):
    return ProjectStore().activate_saved_prompt(str(project_id), str(generation_id), str(prompt_id))


@router.delete("/projects/{project_id}/generations/{generation_id}/saved-prompt/{prompt_id}", response_model=SavedPromptStatus)
def delete_saved_prompt(project_id: UUID, generation_id: UUID, prompt_id: UUID):
    return ProjectStore().delete_saved_prompt(str(project_id), str(generation_id), str(prompt_id))


@router.put("/projects/{project_id}/generations/{generation_id}/selection", response_model=SelectionStatus)
def select_prompt(project_id: UUID, generation_id: UUID, body: SelectionWrite):
    return ProjectStore().select_execution_prompt(str(project_id), str(generation_id), body)


@router.post("/projects/{project_id}/generations/{generation_id}/selection/adopt-history", response_model=SelectionStatus)
def adopt_history_prompt(project_id: UUID, generation_id: UUID, body: HistoryAdoptionWrite):
    return ProjectStore().adopt_history_execution_prompt(str(project_id), str(generation_id), body)


@router.post("/projects/{project_id}/generations/{generation_id}/selection/adopt-manual-edit", response_model=SelectionStatus)
def adopt_manual_edit_prompt(project_id: UUID, generation_id: UUID, body: ManualEditAdoptionWrite):
    return ProjectStore().adopt_manual_edit_execution_prompt(str(project_id), str(generation_id), body)


@router.delete("/projects/{project_id}/generations/{generation_id}/selection", response_model=SelectionStatus)
def clear_selection(project_id: UUID, generation_id: UUID):
    return ProjectStore().clear_execution_prompt(str(project_id), str(generation_id))


@router.post("/prepare", response_model=PreparedWorkflow)
def prepare(body: PrepareRequest):
    profile = ExecutionStore().get("profiles", body.profile_id)
    return prepare_workflow(ProjectStore(), str(body.project_id), str(body.generation_id), profile)


@router.get("/projects/{project_id}/sequence-render-readiness")
def sequence_readiness(project_id: UUID):
    return sequence_render_readiness(str(project_id))


@router.get("/projects/{project_id}/generations/{generation_id}/routing")
def routing_status(project_id: UUID, generation_id: UUID):
    store = ProjectStore()
    workspace = store.read_generation(str(project_id), str(generation_id)).workspace
    from miniconstruct.visual_handoff import handoff_asset
    handoff = store.visual_handoff(str(project_id), str(generation_id), workspace, strict=False)
    if handoff.policy and not handoff.warnings:
        handoff.asset = handoff_asset(handoff.policy)
        workspace = handoff.workspace(workspace)
    routing = store.execution_routing(str(project_id), str(generation_id))
    try:
        references = effective_references(workspace, routing)
    except ValueError as exc:
        raise InvalidBindings([str(exc)]) from exc
    return {"routing": routing, "referenceMap": references, "visualHandoff": handoff.summary(),
            "assets": [{"id": asset.id, "kind": asset.kind, "label": label, "filename": asset.filename,
                        "origin": "visual_handoff" if asset.options.get("visualHandoff") else "workspace"}
                       for asset, label in numbered_assets(workspace)]}


@router.put("/projects/{project_id}/generations/{generation_id}/routing")
def update_routing(project_id: UUID, generation_id: UUID, body: GenerationRouting):
    ProjectStore().set_execution_routing(str(project_id), str(generation_id), body)
    return routing_status(project_id, generation_id)


@router.get("/projects/{project_id}/generations/{generation_id}/continuation")
def continuation_status(project_id: UUID, generation_id: UUID):
    return ProjectStore().continuation_status(str(project_id), str(generation_id), renderer_summary=True)


@router.put("/projects/{project_id}/generations/{generation_id}/renderer-seed")
def save_renderer_seed_settings(project_id: UUID, generation_id: UUID, body: RendererSeedSettingsWrite):
    profile = ExecutionStore().get("profiles", body.profile_id)
    if body.settings.mode != "workflow_default" and not any(
            binding.source.kind == "renderer_seed" for binding in profile.bindings):
        raise InvalidBindings(["Add a renderer_seed binding to this Workflow Profile to control renderer seeds."])
    store = ProjectStore()
    store.set_renderer_seed_settings(str(project_id), str(generation_id), body.settings)
    return store.continuation_status(str(project_id), str(generation_id), renderer_summary=True)


@router.delete("/projects/{project_id}/generations/{generation_id}/continuation/unavailable")
def clear_unavailable_continuations(project_id: UUID, generation_id: UUID):
    return ProjectStore().clear_unavailable_continuations(str(project_id), str(generation_id))


@router.delete("/projects/{project_id}/generations/{generation_id}/continuation/{record_id}")
def remove_continuation(project_id: UUID, generation_id: UUID, record_id: UUID):
    return ProjectStore().remove_continuation(str(project_id), str(generation_id), record_id)


@router.put("/projects/{project_id}/generations/{generation_id}/continuation/settings")
def continuation_settings(project_id: UUID, generation_id: UUID, body: ContinuationSettings):
    if body.profile_id:
        ExecutionStore().get("profiles", body.profile_id)
    store = ProjectStore()
    store.set_continuation_settings(str(project_id), str(generation_id), body)
    return store.continuation_status(str(project_id), str(generation_id), renderer_summary=True)


@router.put("/projects/{project_id}/generations/{generation_id}/continuation/{record_id}")
def continuation_decision(project_id: UUID, generation_id: UUID, record_id: UUID, body: ContinuationDecision):
    store = ProjectStore()
    store.decide_continuation(str(project_id), str(generation_id), record_id, body)
    return store.continuation_status(str(project_id), str(generation_id), renderer_summary=True)
