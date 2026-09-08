"""Offline F3 compilation. This module has no transport or execution jobs."""
from copy import deepcopy
import hashlib
import re
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from miniconstruct.execution import (Profile, StrictModel, InvalidBindings, canonical_json,
                                    binding_errors, binding_deactivation_targets, optional_slot_errors,
                                    DeactivationTarget, binding_routes, route_order_errors)
from miniconstruct.execution_routing import (AssetRouting, effective_references, enabled, prompt_projection,
                                           routing_from_references, EffectiveReference)
from miniconstruct.execution_state import SelectedPrompt, validate_selected_prompt
from miniconstruct.h3.builder import numbered_assets
from miniconstruct.models.workspace import H3Mode
from miniconstruct.project_store import ProjectStore, _parse_image_data_url


class PrepareRequest(StrictModel):
    project_id: UUID = Field(alias="projectId")
    generation_id: UUID = Field(alias="generationId")
    profile_id: UUID = Field(alias="profileId")


class AssetRequirement(StrictModel):
    id: str
    asset_id: str = Field(alias="assetId")
    asset_kind: Literal["image", "audio", "video"] = Field(default="image", alias="assetKind")
    content_sha256: str = Field(alias="contentSha256")
    semantic_label: str = Field(alias="semanticLabel")
    filename: str
    node_id: str = Field(alias="nodeId")
    input_name: str = Field(alias="inputName")


class SemanticResolution(StrictModel):
    label: str
    value: str
    node_id: str = Field(alias="nodeId")
    input_name: str = Field(alias="inputName")
    status: Literal["resolved", "active", "inactive"]
    presence: Literal["optional", "required"] | None = None
    deactivation_targets: list[DeactivationTarget] = Field(default_factory=list, alias="deactivationTargets")
    routes: list[dict[str, Any]] = Field(default_factory=list)


class PreparedWorkflow(StrictModel):
    reference_map: list[EffectiveReference] = Field(default_factory=list, alias="referenceMap")
    format: Literal["MiniConstruct prepared workflow v1"] = "MiniConstruct prepared workflow v1"
    project_id: UUID = Field(alias="projectId")
    generation_id: UUID = Field(alias="generationId")
    mode: H3Mode
    profile_id: UUID = Field(alias="profileId")
    profile_name: str = Field(alias="profileName")
    workflow_fingerprint: str = Field(alias="workflowFingerprint")
    profile_fingerprint: str = Field(alias="profileFingerprint")
    current_fingerprint: str = Field(alias="currentFingerprint")
    selected_prompt: SelectedPrompt = Field(alias="selectedPrompt")
    graph: dict[str, Any]
    resolutions: list[SemanticResolution]
    asset_requirements: list[AssetRequirement] = Field(alias="assetRequirements")
    inactive_slots: list[str] = Field(alias="inactiveSlots")
    warnings: list[str]
    ready: Literal[True] = True
    transport_ready: Literal[False] = Field(default=False, alias="transportReady")
    sent_to_comfyui: Literal[False] = Field(default=False, alias="sentToComfyUI")


def _filename(value: str) -> str:
    return value.replace("\\", "/").split("/")[-1][:512]


def _safe_filename(value: str) -> str:
    name = value.replace("\\", "/").split("/")[-1]
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")[:512] or "reference.bin"


def _safe_view(value: Any, key: str = "") -> Any:
    """Response-only redaction; never edits the imported graph or its constants."""
    if re.search(r"secret|password|token|api[_-]?key|authorization", key, re.I):
        return "[redacted]"
    if isinstance(value, dict):
        return {k: _safe_view(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_safe_view(v) for v in value]
    if isinstance(value, str) and (";base64," in value or value.startswith(("/", "\\\\"))
                                  or re.match(r"^[A-Za-z]:[\\/]", value)):
        return "[private value omitted]"
    return value


def prepare_workflow(store: ProjectStore, project_id: str, generation_id: str, profile: Profile, *,
                     inspection: bool = True) -> PreparedWorkflow:
    generation, status = store.execution_snapshot(project_id, generation_id)
    workspace, selected = generation.workspace, status.selected
    if selected is None:
        raise InvalidBindings(["Select a generated H3 prompt with Use for execution first."])
    if status.stale:
        raise InvalidBindings(["Selected prompt is stale for the current saved Generation."])
    routing = store.execution_routing(project_id, generation_id)
    try:
        effective = effective_references(workspace, routing)
        original_map = selected.reference_map if selected.reference_map is not None else effective_references(workspace)
        prompt_workspace = prompt_projection(workspace, routing_from_references(workspace, original_map))
    except ValueError as exc:
        raise InvalidBindings([str(exc)]) from exc
    previous = {ref.label: (ref.asset_id, ref.channel) for ref in original_map}
    current = {ref.label: (ref.asset_id, ref.channel) for ref in effective}
    for kind, ordinal in re.findall(r"<(Picture|Video|Audio)\s+(\d+)>", selected.prompt):
        label = f"{kind} {ordinal}"
        if previous.get(label) != current.get(label) or label not in current:
            raise InvalidBindings([f"{label} has a different source under the current routing. Generate a prompt with these routing choices and select it; the existing prompt is preserved."])
    validate_selected_prompt(selected.prompt, prompt_workspace)
    if workspace.mode not in profile.compatible_modes:
        raise InvalidBindings(["Workflow Profile is not compatible with this Generation's H3 mode."])
    errors = binding_errors(profile.workflow, profile.bindings) + route_order_errors(profile.bindings)
    if not any(b.source.kind == "generated_prompt" for b in profile.bindings):
        errors.append("Workflow Profile needs a Generated H3 prompt binding.")
    if errors:
        raise InvalidBindings(errors)

    graph = deepcopy(profile.workflow)
    references = {label: asset for asset, label in numbered_assets(workspace)}
    requirements, resolutions, inactive, warnings = [], [], [], []
    covered = set()
    names = {"image": "Picture", "video": "Video", "audio": "Audio"}
    bound = {(b.node_id, b.input_name) for b in profile.bindings}
    for binding in profile.bindings:
        route_report = []
        source = binding.source
        target = graph[binding.node_id]["inputs"]
        if source.kind == "generated_prompt":
            label, value, display, state = "Generated H3 prompt", selected.prompt, "Selected and current", "resolved"
        elif source.kind == "duration":
            label, value, display, state = "Duration", workspace.duration_seconds, f"{workspace.duration_seconds:g} seconds", "resolved"
        else:
            label = f"{names[source.asset_kind]} {source.ordinal}"
            covered.add(label)
            asset = references.get(label)
            routes = binding_routes(binding)
            if source.asset_kind in {"video", "audio"} and not routes:
                kind = "Video" if source.asset_kind == "video" else "Audio"
                raise InvalidBindings([f"{label}: Configure/classify this Workflow Profile's {kind} route capacity once before preparation."])
            if any(route.channel is None for route in routes):
                raise InvalidBindings([f"{label}: Classify preserved Video routes once in the Workflow Profile as video frames or embedded audio."])
            preference = routing.assets.get(asset.id, AssetRouting()) if asset else AssetRouting()
            active_channels = {ref.channel for ref in effective if asset and ref.asset_id == asset.id}
            available = {route.channel for route in routes}
            if binding.routes and active_channels - available:
                raise InvalidBindings([f"{label}: Workflow Profile has no capacity for enabled route {channel}." for channel in sorted(active_channels - available)])
            # All configured edges must be safe, even while the source file is present.
            if routes and (binding.presence == "optional" or binding.routes):
                unsafe = optional_slot_errors(profile.workflow, binding)
                if unsafe:
                    raise InvalidBindings([f"{label}: {error}" for error in unsafe])
            for route in routes:
                active = asset is not None and enabled(route.channel, preference)
                route_report.append({"channel": route.channel, "status": "active" if active else "inactive",
                                     "target": route.model_dump(by_alias=True)})
                if not active:
                    graph[route.node_id]["inputs"].pop(route.input_name, None)
            if asset is None or not active_channels:
                if asset is None and binding.presence == "required":
                    raise InvalidBindings([f"{label} is required by this Workflow Profile but is not present."])
                unsafe = optional_slot_errors(profile.workflow, binding)
                # Sharing a loader between semantic slots cannot establish exclusive ownership.
                if any(b is not binding and b.node_id == binding.node_id for b in profile.bindings):
                    unsafe.append("An optional loader cannot be shared with another semantic binding.")
                if unsafe:
                    raise InvalidBindings([f"{label}: {error}" for error in unsafe])
                for deactivation in binding_deactivation_targets(binding):
                    # binding_errors already proves this exact relationship is
                    # present in the imported graph. Delete only the declared
                    # input link; never prune the source or adapter nodes.
                    graph[deactivation.node_id]["inputs"].pop(deactivation.input_name, None)
                value = {"$inactiveReference": label}
                display, state = "not present" if asset is None else "routes disabled", "inactive"
                inactive.append(label)
            else:
                effective_channel = {"image": "image", "video": "video_frames", "audio": "audio"}[asset.kind]
                label = next((ref.label for ref in effective
                              if ref.asset_id == asset.id and ref.channel == effective_channel), label)
                if asset.kind == "image":
                    if asset.image is None:
                        raise InvalidBindings([f"{label}: saved Picture payload is missing."])
                    _, payload = _parse_image_data_url(asset.image.data_url, asset.id)
                    digest = hashlib.sha256(payload).hexdigest()
                else:
                    if asset.media_payload is None:
                        raise InvalidBindings([f"{label}: metadata only; actual file payload unavailable. Reattach the file and save the Project."])
                    digest = asset.media_payload.sha256
                identity = {"projectId": project_id, "generationId": generation_id, "assetId": asset.id,
                            "contentSha256": digest, "nodeId": binding.node_id, "inputName": binding.input_name}
                requirement_id = hashlib.sha256(canonical_json(identity)).hexdigest()
                requirements.append(AssetRequirement(id=requirement_id, assetId=asset.id, assetKind=asset.kind, contentSha256=digest,
                    semanticLabel=label, filename=_safe_filename(asset.filename), nodeId=binding.node_id, inputName=binding.input_name))
                value = {"$assetRequirement": requirement_id}
                display, state = _filename(asset.filename), "active"
        target[binding.input_name] = value
        resolutions.append(SemanticResolution(label=label, value=display, nodeId=binding.node_id,
            inputName=binding.input_name, status=state,
            routes=route_report,
            presence=binding.presence if source.kind == "reference_asset" else None,
            deactivationTargets=(binding_deactivation_targets(binding)
                                 if source.kind == "reference_asset" else [])))
    active_ids = {ref.asset_id for ref in effective}
    missing = {label for label, asset in references.items() if asset.id in active_ids} - covered
    if missing:
        raise InvalidBindings([f"Workflow Profile has no binding for active {label}." for label in sorted(missing)])
    # Exact standard class/input pairs only; no substring/custom-node guessing.
    loaders = {"LoadImage": "image", "LoadAudio": "audio", "LoadVideo": "file"}
    for node_id, node in profile.workflow.items():
        input_name = loaders.get(node["class_type"])
        filename = node["inputs"].get(input_name)
        if input_name and (node_id, input_name) not in bound and isinstance(filename, str) and filename:
            safe_name = _safe_view(filename)
            warnings.append(f"Unbound {node['class_type']} node {node_id} retains imported file "
                            f"{_filename(safe_name)} as a workflow constant.")
    warnings.append("Prepared offline according to profile semantics. Live ComfyUI node availability and runtime "
                    "validation are not checked. Asset slots and inactive loader markers are not ComfyUI API values.")
    view = _safe_view(graph)
    if view != graph:
        warnings.append("Private constants are omitted from this inspection view; stored workflow constants are unchanged.")
    result = PreparedWorkflow(projectId=project_id, generationId=generation_id, mode=workspace.mode,
        profileId=profile.id, profileName=profile.name, workflowFingerprint=profile.fingerprint,
        profileFingerprint=hashlib.sha256(canonical_json(profile.model_dump(mode="json", by_alias=True))).hexdigest(),
        currentFingerprint=status.current_fingerprint, selectedPrompt=selected, graph=view if inspection else graph,
        resolutions=resolutions, assetRequirements=requirements, inactiveSlots=inactive, warnings=warnings,
        referenceMap=effective)
    return result
