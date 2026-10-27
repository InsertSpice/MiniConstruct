"""Offline F3 compilation. This module has no transport or execution jobs."""
from copy import deepcopy
import hashlib
import re
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, PrivateAttr

from miniconstruct.execution import (Profile, StrictModel, InvalidBindings, canonical_json,
                                    binding_errors, binding_deactivation_targets, optional_slot_errors,
                                    DeactivationTarget, binding_routes, route_order_errors)
from miniconstruct.execution_routing import AssetRouting, effective_references, enabled, EffectiveReference
from miniconstruct.execution_state import SelectedPrompt, require_execution_prompt
from miniconstruct.h3.builder import numbered_assets
from miniconstruct.models.workspace import H3Mode
from miniconstruct.project_store import ProjectStore, _parse_image_data_url
from miniconstruct.native_continuation import prepare_native, inject_native, profile_identity, digest as execution_digest
from miniconstruct.native_continuation_models import NativePreparation
from miniconstruct.execution_state import RendererSeedSettings, effective_renderer_seed_settings
from miniconstruct.legacy_aspect_ratio import legacy_aspect_ratio_control, legacy_renderer_value
from miniconstruct.aspect_ratios import renderer_ratio as resolve_renderer_ratio, ratio_token
from miniconstruct.preparation_diagnostics import PreparationDiagnostic, PreparationDiagnostics
from miniconstruct.workflow_controls import resolve_controls, apply_controls, definitions_fingerprint
from miniconstruct.comfy_media import STANDARD_MEDIA_LOADERS


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
    source_value: str | None = Field(default=None, alias="sourceValue")
    renderer_value: str | None = Field(default=None, alias="rendererValue")
    node_id: str = Field(alias="nodeId")
    input_name: str = Field(alias="inputName")
    status: Literal["resolved", "active", "inactive"]
    presence: Literal["optional", "required"] | None = None
    deactivation_targets: list[DeactivationTarget] = Field(default_factory=list, alias="deactivationTargets")
    routes: list[dict[str, Any]] = Field(default_factory=list)


class PreparedWorkflow(StrictModel):
    # Operation-local materialization; never serialized, persisted or reused by
    # another Send. Final compilation re-resolves its canonical source identity.
    _handoff: Any = PrivateAttr(default=None)
    diagnostics: list[PreparationDiagnostic] = Field(default_factory=list)
    information: list[str] = Field(default_factory=list)
    workflow_controls: list[dict[str, Any]] = Field(default_factory=list, alias="workflowControls")
    workflow_control_fingerprint: str = Field(default="", alias="workflowControlFingerprint")
    native_continuation: NativePreparation | None = Field(default=None, alias="nativeContinuation")
    renderer_seed_controlled: bool = Field(default=False, alias="rendererSeedControlled")
    renderer_seed_settings: RendererSeedSettings = Field(alias="rendererSeedSettings")
    execution_fingerprint: str = Field(default="", alias="executionFingerprint")
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


def reference_map_warnings(prompt: str, recorded: list[EffectiveReference] | None,
                           effective: list[EffectiveReference], *,
                           diagnostics: PreparationDiagnostics | None = None) -> list[str]:
    report = diagnostics if diagnostics is not None else PreparationDiagnostics()
    if recorded is None:
        if re.search(r"<(?:Picture|Video|Audio)\s+\d+>", prompt, re.IGNORECASE):
            report.add("reference.history_unavailable",
                       "Original reference-label mapping is unavailable; inspect current routing before Send.")
        return report.messages("warning")
    old = {ref.label: (ref.asset_id, ref.channel) for ref in recorded}
    current = {ref.label: (ref.asset_id, ref.channel) for ref in effective}
    changed = {label for label in old | current if old.get(label) != current.get(label)}
    referenced_change = False
    for label in sorted(changed):
        if not re.search(rf"<{re.escape(label)}>", prompt, re.IGNORECASE):
            continue
        referenced_change = True
        if label not in current:
            report.add("reference.unresolved", f"Reference <{label}> in the selected prompt no longer resolves in current routing.")
        elif label in old:
            report.add("reference.source_changed", f"Reference <{label}> now routes to a different source than when the prompt was created.")
        else:
            report.add("reference.source_unrecorded", f"Reference <{label}> has no recorded source in the selected prompt's original mapping.")
    if changed and not referenced_change:
        report.add("reference.unused_routing_changed",
                   "Reference routing changed since this prompt was created; its referenced labels do not collide.", "info")
    return report.messages("warning")


def require_current_picture_map(recorded, effective):
    """Historical Picture labels cannot silently adopt another current slot."""
    current = {ref.label: ref.asset_id for ref in effective if ref.label.startswith("Picture ")}
    if recorded is not None and any(ref.label.startswith("Picture ") and not ref.asset_id.startswith("handoff:")
                                    and current.get(ref.label) != ref.asset_id
                                    for ref in recorded):
        raise InvalidBindings(["Picture authority changed since this prompt was generated. Generate a new H3 prompt and explicitly Use for execution."])


def prepare_workflow(store: ProjectStore, project_id: str, generation_id: str, profile: Profile, *,
                     inspection: bool = True, retry_control_values: dict[str, Any] | None = None,
                     read_only_handoff: bool = False, verify_payloads: bool = True,
                     revalidate: PreparedWorkflow | None = None) -> PreparedWorkflow:
    generation, status = store.execution_preparation_snapshot(project_id, generation_id, verify_payloads=verify_payloads)
    workspace, selected = generation.workspace, status.selected
    native, native_resolution = prepare_native(store, project_id, generation_id, profile, workspace)
    authoring_handoff = store.visual_handoff(project_id, generation_id, workspace, strict=False)
    if revalidate is None:
        handoff = store.visual_handoff(project_id, generation_id, workspace, materialize=True, profile=profile,
                                       read_only=read_only_handoff)
    else:
        current_handoff = store.visual_handoff(project_id, generation_id, workspace, profile=profile)
        handoff = revalidate._handoff
        if handoff is None or current_handoff.identity != handoff.identity:
            raise InvalidBindings(["Visual handoff source changed during upload; Prepare again. No prompt was submitted."])
    if native and native.consume and profile.native_continuation.requires_tail_reference and handoff.asset is None:
        raise InvalidBindings(["This profile requires a real tail reference alongside native state. Restore the predecessor Selected Take media or choose another profile."])
    workspace = handoff.workspace(workspace)
    if selected is None:
        raise InvalidBindings(["There is no H3 prompt to prepare."])
    routing = store.execution_routing(project_id, generation_id)
    try:
        effective = effective_references(workspace, routing)
    except ValueError as exc:
        raise InvalidBindings([str(exc)]) from exc
    require_current_picture_map(selected.reference_map, effective)
    require_execution_prompt(selected.prompt)
    if workspace.mode not in profile.compatible_modes:
        raise InvalidBindings(["Workflow Profile is not compatible with this Generation's H3 mode."])
    # Editable drafts and explicit prompt selection may be incomplete. Neither
    # an Optional loader nor native Save/Load declares a different I2VA mode.
    # Check the effective workspace after Visual Handoff can supply an anchor.
    if workspace.mode == H3Mode.I2VA and not workspace._native_i2va and sum(
            asset.kind == "image" and asset.role == "first_frame_anchor" for asset in workspace.assets) != 1:
        raise InvalidBindings(["I2VA requires exactly one first-frame Picture. Attach it with the first-frame anchor role; "
                               "an Optional Profile slot or native Save/Load mapping does not make I2VA Picture-free."])
    if workspace._native_i2va:
        from miniconstruct.dialogue_language import workspace_for_h3
        from miniconstruct.h3.validator import validate_workspace_prompt
        from miniconstruct.llm.client import LLMBackendError
        try:
            validation = validate_workspace_prompt(selected.prompt, workspace_for_h3(workspace))
        except LLMBackendError as exc:
            raise InvalidBindings(["Native-only I2VA Spoken Output: " + str(exc)]) from exc
        if not validation.valid:
            raise InvalidBindings(["Native-only I2VA prompt: " + item.message for item in validation.findings if item.severity == "ERROR"])
    errors = binding_errors(profile.workflow, profile.bindings) + route_order_errors(profile.bindings)
    if not any(b.source.kind == "generated_prompt" for b in profile.bindings):
        errors.append("Workflow Profile needs a Generated H3 prompt binding.")
    if errors:
        raise InvalidBindings(errors)

    graph = deepcopy(profile.workflow)
    if native:
        inject_native(graph, profile.native_continuation, native, workspace.duration_seconds)
    references = {label: asset for asset, label in numbered_assets(workspace)}
    requirements, resolutions, inactive = [], [], []
    diagnostics = PreparationDiagnostics()
    controls, _ = resolve_controls(store, project_id, generation_id, profile, retry_control_values, diagnostics=diagnostics)
    # Controls and semantic mappings are disjoint by Profile validation. Apply to
    # the copied graph before semantic bindings, in one shared Prepare compiler.
    apply_controls(graph, profile, controls)
    legacy_ratio = legacy_aspect_ratio_control(profile)
    if legacy_ratio:
        renderer_ratio = legacy_renderer_value(legacy_ratio, workspace.aspect_ratio, profile)
        target = legacy_ratio.target
        graph[target.node_id]["inputs"][target.input_name] = renderer_ratio
        # Inspection and execution provenance report the projected authority, not
        # a pretend saved Workflow Control selection. Persisted values are untouched.
        for item in controls:
            if item["id"] == legacy_ratio.id:
                item.update(value=renderer_ratio, source="h3_setup_compatibility")
        resolutions.append(SemanticResolution(label="Aspect Ratio (H3 Setup, legacy compatibility)",
            value=renderer_ratio, sourceValue=workspace.aspect_ratio, rendererValue=renderer_ratio,
            nodeId=target.node_id, inputName=target.input_name, status="resolved"))
        diagnostics.add("aspect_ratio.legacy_authority", f'Legacy Aspect Ratio Workflow Control "{legacy_ratio.label}" at '
                           f'{target.node_id} / {target.input_name} is driven by H3 Setup: '
                           f'{workspace.aspect_ratio} → {renderer_ratio}. Saved/default control values are ignored '
                           'for this target; change H3 Setup to change its aspect ratio.', "info")
    elif not any(binding.source.kind == "aspect_ratio" for binding in profile.bindings):
        declared_ratio_targets = [control for control in profile.exposed_controls
                                  if control.target.input_name in {"aspect_ratio", "aspectRatio"}]
        if declared_ratio_targets:
            raise InvalidBindings(["H3 Setup Aspect Ratio has no safe authority for the declared ratio Workflow Control. "
                                   "Use an explicit aspect_ratio binding or one canonical aspect_ratio enum control."])
        # No declared resolution capability exists; never infer it from graph node
        # class/title alone or reject otherwise valid ratio-independent workflows.
        diagnostics.add("aspect_ratio.no_authority", "This Workflow Profile has no Aspect Ratio authority. H3 Setup aspect ratio does not control "
                        "renderer dimensions. Add a modern aspect_ratio binding if this workflow requires it.")
    diagnostics.extend("native.compatibility", native_resolution.get("warnings", []))
    diagnostics.extend("handoff.uncertain", handoff.warnings)
    reference_map_warnings(selected.prompt,
        selected.reference_map if selected.source_kind == "generated_prompt" else None, effective, diagnostics=diagnostics)
    if authoring_handoff.identity != handoff.identity:
        diagnostics.add("handoff.projection_changed", "Selected Workflow Profile changes visual handoff evidence from the prompt's authoring projection; execution uses the prepared handoff.")
    if status.stale:
        diagnostics.add("prompt.stale", "Selected prompt was created for different saved authoring; execution uses its exact text.")
    seed_controlled = any(binding.source.kind == "renderer_seed" for binding in profile.bindings)
    renderer_seed_settings = effective_renderer_seed_settings(
        store.renderer_seed_settings(project_id, generation_id), seed_controlled)
    if renderer_seed_settings.mode != "workflow_default" and not seed_controlled:
        raise InvalidBindings(["Add a renderer_seed binding to this Workflow Profile to control renderer seeds."])
    if renderer_seed_settings.mode == "workflow_default":
        diagnostics.add("seed.workflow_default", "Renderer seed: Workflow default; MiniConstruct leaves the imported seed unchanged.", "info")
    if native:
        if native.consume:
            diagnostics.add("native.source", f"Native H3 continuation · Source: Generation {native_resolution['sourceGenerationNumber']} ({native.source_generation_id}) · Profile: {profile.name} · Context: {native.context_frames} frames · Audio continuity: {'enabled' if native.audio_enabled else 'disabled/unsupported'}.", "info")
        else:
            diagnostics.add("native.save_only", "Native H3 state: save current execution only; no predecessor state consumed.", "info")
        diagnostics.add("native.artifact_lifecycle", "Native state is expected only after execution. Queue acceptance does not confirm the artifact exists.", "info")
    covered = set()
    names = {"image": "Picture", "video": "Video", "audio": "Audio"}
    bound = {(b.node_id, b.input_name) for b in profile.bindings}
    for binding in profile.bindings:
        route_report = []
        source = binding.source
        target = graph[binding.node_id]["inputs"]
        if source.kind == "generated_prompt":
            label, value, display, state = "Generated H3 prompt", selected.prompt, "Selected for execution", "resolved"
        elif source.kind == "duration":
            label, value, display, state = "Duration", workspace.duration_seconds, f"{workspace.duration_seconds:g} seconds", "resolved"
        elif source.kind == "aspect_ratio":
            label, value = "Aspect Ratio (H3 Setup)", workspace.aspect_ratio
            renderer_value = resolve_renderer_ratio(source, value, f"{binding.node_id} / {binding.input_name}")
            if source.enum_choices is not None and ratio_token(value) and not ratio_token(renderer_value):
                diagnostics.add("aspect_ratio.manual_mapping", f'Aspect Ratio mapping for "{value}" at {binding.node_id} / {binding.input_name} '
                                f'uses declared renderer-specific label "{renderer_value}". '
                                'Numeric ratio equivalence cannot be verified; review this manual mapping.')
            if value not in source.value_map and source.enum_choices is None:
                diagnostics.add("aspect_ratio.unverified_raw", f'No renderer Aspect Ratio mapping exists for "{value}"; raw value will be sent. '
                                'This input has no saved verified enum declaration; raw-value support is unverified. '
                                'Use Populate mappings from ComfyUI for a closed enum.')
            value = renderer_value
            display, state = renderer_value, "resolved"
        elif source.kind == "renderer_seed":
            seed_display = {"workflow_default": "Imported workflow value", "random": "Fresh random seed on each Send",
                            "fixed": f"Fixed seed {renderer_seed_settings.fixed_seed}"}[renderer_seed_settings.mode]
            label, value, display, state = "Renderer seed", target[binding.input_name], seed_display, "resolved"
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
            # Optional slots must be safe even while present. Required Pictures
            # are always active and may also own dimensions or other required
            # inputs; they never need a removable branch. Video/Audio routes can
            # still be disabled by Generation routing even on Required slots.
            if routes and (binding.presence == "optional" or binding.routes):
                unsafe = optional_slot_errors(profile.workflow, binding,
                    require_deactivation=binding.presence == "optional" or source.asset_kind != "image")
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
                    if not verify_payloads and asset.payload_sha256:
                        digest = asset.payload_sha256
                    elif asset.image is None:
                        raise InvalidBindings([f"{label}: saved Picture payload is {asset.payload_availability or 'missing'}. Replace Picture and save the Project."])
                    else:
                        _, payload = _parse_image_data_url(asset.image.data_url, asset.id)
                        digest = hashlib.sha256(payload).hexdigest()
                else:
                    if asset.media_payload is None:
                        raise InvalidBindings([f"{label}: actual file payload {asset.payload_availability or 'unavailable (metadata only)'}. Reattach the file and save the Project."])
                    digest = asset.media_payload.sha256
                identity = {"projectId": project_id, "generationId": generation_id, "assetId": asset.id,
                            "contentSha256": digest, "nodeId": binding.node_id, "inputName": binding.input_name}
                requirement_id = hashlib.sha256(canonical_json(identity)).hexdigest()
                requirements.append(AssetRequirement(id=requirement_id, assetId=asset.id, assetKind=asset.kind, contentSha256=digest,
                    semanticLabel=label, filename=_safe_filename(asset.filename), nodeId=binding.node_id, inputName=binding.input_name))
                value = {"$assetRequirement": requirement_id}
                display, state = _filename(asset.filename), "active"
        target[binding.input_name] = value
        resolutions.append(SemanticResolution(label=label, value=display,
            sourceValue=workspace.aspect_ratio if source.kind == "aspect_ratio" else None,
            rendererValue=value if source.kind == "aspect_ratio" else None, nodeId=binding.node_id,
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
    for node_id, node in profile.workflow.items():
        input_name = STANDARD_MEDIA_LOADERS.get(node["class_type"])
        filename = node["inputs"].get(input_name)
        if input_name and (node_id, input_name) not in bound and isinstance(filename, str) and filename:
            safe_name = _safe_view(filename)
            diagnostics.add("workflow.imported_media", f"Unbound {node['class_type']} node {node_id} retains imported file "
                            f"{_filename(safe_name)} as a workflow constant.")
    if native and workspace.mode == H3Mode.I2VA:
        from miniconstruct.i2va_contract import picture_free_errors, state_identity
        errors = picture_free_errors(graph, profile.bindings, profile.native_continuation)
        if errors:
            raise InvalidBindings(errors)
        native.i2va_state_identity = state_identity(graph, profile.native_continuation)
        if workspace._native_i2va and native.i2va_state_identity != native_resolution["record"].i2va_state_identity:
            raise InvalidBindings(["Effective model/codec controls differ from the accepted native source. Choose a compatible successor Profile."])
    diagnostics.add("preparation.offline", "Prepared offline according to profile semantics. Live ComfyUI node availability and runtime "
                    "validation are not checked. Asset slots and inactive loader markers are not ComfyUI API values.", "info")
    view = _safe_view(graph)
    if view != graph:
        diagnostics.add("inspection.private_constants", "Private constants are omitted from this inspection view; stored workflow constants are unchanged.", "info")
    native_identity = native.model_dump(mode="json", by_alias=True,
        exclude={"id", "save_key", "storage_namespace"}) if native else None
    if native_identity is not None and native.i2va_state_identity is None:
        native_identity.pop("i2vaStateIdentity", None)
    execution_identity = {"authoring": status.current_fingerprint, "durationSeconds": workspace.duration_seconds,
                          "h3AspectRatio": workspace.aspect_ratio,
                          "profile": profile_identity(profile),
                          "rendererSeed": renderer_seed_settings.model_dump(mode="json", by_alias=True),
                          "routing": routing.model_dump(mode="json", by_alias=True),
                          "selectionId": str(selected.id), "sequence": native_resolution["sequence"],
                          "native": native_identity,
                          "handoff": handoff.identity}
    if any(binding.source.kind == "aspect_ratio" for binding in profile.bindings):
        execution_identity["aspectRatio"] = {
            "source": workspace.aspect_ratio,
            "renderer": [resolve_renderer_ratio(binding.source, workspace.aspect_ratio)
                         for binding in profile.bindings if binding.source.kind == "aspect_ratio"],
        }
    if legacy_ratio:
        execution_identity["aspectRatio"] = {"source": workspace.aspect_ratio, "renderer": [renderer_ratio],
                                             "legacyControlId": legacy_ratio.id}
    if profile.exposed_controls:
        execution_identity["workflowControls"] = {item["id"]: item["value"] for item in controls}
    result = PreparedWorkflow(projectId=project_id, generationId=generation_id, mode=workspace.mode,
        nativeContinuation=native, rendererSeedControlled=seed_controlled,
        rendererSeedSettings=renderer_seed_settings,
        executionFingerprint=execution_digest(execution_identity),
        profileId=profile.id, profileName=profile.name, workflowFingerprint=profile.fingerprint,
        profileFingerprint=profile_identity(profile),
        currentFingerprint=status.current_fingerprint, selectedPrompt=selected, graph=view if inspection else graph,
        resolutions=resolutions, assetRequirements=requirements, inactiveSlots=inactive, diagnostics=diagnostics.items,
        warnings=diagnostics.messages("warning"), information=diagnostics.messages("info"),
        referenceMap=effective, workflowControls=controls,
        workflowControlFingerprint=definitions_fingerprint(profile))
    result._handoff = handoff
    return result
