"""Execution lineage and opaque-key adapter. Never discovers renderer files."""
import hashlib
from uuid import uuid4

from miniconstruct.execution import ExecutionStore, ConfigNotFound, InvalidBindings, canonical_json, native_mapping_errors, binding_routes
from miniconstruct.native_continuation_models import ContinuationSettings, ContinuationExecution, NativePreparation
from miniconstruct.take_store import transaction


def digest(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def profile_identity(profile):
    data = profile.model_dump(mode="json", by_alias=True)
    data.pop("workflowControlPresets", None)
    if profile.native_continuation and profile.native_continuation.picture_free_i2va is None:
        data["nativeContinuation"].pop("pictureFreeI2va", None)
    # An old Profile with no exposed controls retains its historical identity.
    # Nonempty declarations participate in the full Profile fingerprint.
    if not profile.exposed_controls:
        data.pop("exposedControls", None)
    # Legacy reference wire spellings normalize on save; identity is semantic.
    for value, binding in zip(data["bindings"], profile.bindings):
        if binding.source.kind == "aspect_ratio" and binding.source.enum_choices is None:
            value["source"].pop("enumChoices", None)
        if binding.source.kind == "reference_asset":
            value.pop("controlledConsumers", None)
            value.pop("deactivationTargets", None)
            value["routes"] = [r.model_dump(mode="json", by_alias=True) for r in binding_routes(binding)]
        elif binding.source.kind == "aspect_ratio" and not binding.source.value_map:
            # Empty v7 maps preserve v6 identity and accepted native lineage.
            value["source"].pop("valueMap", None)
    return digest(data)


def compatibility_identity(profile):
    cap = profile.native_continuation
    contract = cap.model_dump(mode="json", by_alias=True, exclude={
        "context_frames", "context_grid", "extension_grid", "frames_per_second", "audio_enabled"})
    if cap.picture_free_i2va is None:
        contract.pop("pictureFreeI2va", None)
    return digest({"profileId": str(profile.id), "workflow": profile.fingerprint,
                   "contract": contract})


def predecessor_native_records(store, project_id, predecessor, state):
    """Exact user authority and ownership, independent of consumer capabilities.

    Availability/confirmation and mode compatibility are checked by consumers.
    Without a Selected Take, only the explicit default pointer grants authority.
    """
    records = [r for r in state.continuation_executions
               if str(r.project_id) == str(project_id) and str(r.generation_id) == predecessor.generation_id]
    if predecessor.selected_take_id:
        take = store._take(predecessor, predecessor.selected_take_id)
        records = [r for r in records if take.render_submission_id is not None
                   and r.render_submission_id == take.render_submission_id]
    else:
        accepted = state.accepted_continuations.get(predecessor.generation_id)
        records = [r for r in records if r.id == accepted]
    submissions = {s.id for s in predecessor.render_submissions}
    return [r for r in records if r.render_submission_id in submissions]


def resolve_native(store, project_id, generation_id, profile=None, workspace=None, settings_override=None):
    """Read a single canonical predecessor. Chronology is never selection authority."""
    with transaction():
        directory, _, generations, _, current = store._output_context(project_id, generation_id)
        state = store._read_execution_state(directory)
        index = next(i for i, g in enumerate(generations) if g.generation_id == current.generation_id)
        predecessor = generations[index - 1] if index else None
        settings = settings_override or state.continuation_settings.get(str(current.generation_id))
        if settings is None:
            # A new Story Generation has no execution sidecar yet. Follow the
            # nearest explicit predecessor profile preference, never its artifact.
            for earlier in reversed(generations[:index]):
                inherited = state.continuation_settings.get(earlier.generation_id)
                if inherited is not None:
                    settings = ContinuationSettings(profileId=inherited.profile_id)
                    break
        settings = settings or ContinuationSettings()
        if profile is None and settings.profile_id:
            try:
                profile = ExecutionStore().get("profiles", settings.profile_id)
            except ConfigNotFound:
                pass
        workspace = workspace or store.read_generation(project_id, generation_id).workspace
        cap = profile.native_continuation if profile and workspace.mode in profile.compatible_modes else None
        picture_free = current.planning.visual_handoff.strategy == "native_i2va_continuation"
        result = {"supported": cap is not None, "available": False, "consume": False,
                  "profileId": str(profile.id) if profile else None, "profileName": profile.name if profile else None,
                  "sourceGenerationId": predecessor.generation_id if predecessor else None,
                  "sourceGenerationNumber": index if predecessor else None, "segmentIndex": index + 1,
                  "sequence": [g.generation_id for g in generations], "record": None, "warnings": [],
                  "pictureFreeI2va": picture_free, "pictureFreeI2vaSupported": bool(cap and cap.picture_free_i2va),
                  "profile": profile, "settings": settings, "reason": "Active Workflow Profile does not declare compatible native H3 continuation."}
        if cap is None:
            return result
        errors = native_mapping_errors(profile.workflow, profile.bindings, cap)
        if errors:
            raise InvalidBindings(errors)
        if picture_free:
            if workspace.mode.value != "I2VA" or not cap.picture_free_i2va:
                result["reason"] = "Choose I2VA and an explicitly declared picture-free successor Workflow Profile."
                return result
            if current.planning.relationship_to_previous != "continue_previous_shot":
                result["reason"] = "Picture-free I2VA requires Continue previous shot and an accepted immediate predecessor."
                return result
            if workspace.assets:
                result["reason"] = "Picture-free I2VA expects no new Picture, Video or Audio. Remove the new references or choose ordinary first-frame I2VA."
                return result
        try:
            requested = settings.context_frames if settings.context_frames is not None else cap.context_frames
            frames = cap.context_grid.resolve(requested)
            audio = settings.audio_enabled if settings.audio_enabled is not None else cap.audio_enabled
            if audio and not cap.audio_continuity:
                raise ValueError("This profile does not support audio continuity")
            if settings.source_out_frame is not None and "source_out_frame" not in cap.inputs:
                raise ValueError("This profile does not expose a source out-point")
            source_out = (cap.source_out_grid.resolve(settings.source_out_frame)
                          if cap.source_out_grid and settings.source_out_frame is not None else settings.source_out_frame)
            if settings.context_frames is not None and "context_frames" not in cap.inputs and frames != cap.context_grid.resolve(cap.context_frames):
                raise ValueError("This profile uses fixed context; map context_frames to change it")
            extension = cap.extension_grid.resolve(round(workspace.duration_seconds * cap.frames_per_second)) if "extension_frames" in cap.inputs else None
        except ValueError as exc:
            raise InvalidBindings([str(exc)]) from exc
        result.update(contextFrames=frames, requestedContextFrames=requested, extensionFrames=extension,
                      audioSupported=cap.audio_continuity, audioEnabled=audio, sourceOutFrame=source_out,
                      requiresTailReference=cap.requires_tail_reference)
        if predecessor is None:
            result["reason"] = "First Generation: save current native state only; no predecessor."
            return result
        if predecessor.selected_take_id:
            take = store._take(predecessor, predecessor.selected_take_id)
            if take.render_submission_id is None:
                result["reason"] = "Previous Selected Take has external/unknown execution provenance; use tail or textual continuity."
                return result
        # Take provenance narrows lineage; only an explicit user decision grants
        # trust. A different default accepted pointer cannot substitute for it.
        records = predecessor_native_records(store, project_id, predecessor, state)
        if any(r.status == "available" and not r.confirmed and r.mode == workspace.mode.value for r in records):
            result["reason"] = ("Matching native state is Available but has not been Accepted. "
                                "Use Accept native state on the previous Generation, or choose another handoff strategy.")
            return result
        records = [r for r in records if r.status == "available" and r.confirmed]
        mode_records = [r for r in records if r.mode == workspace.mode.value]
        if len(mode_records) != 1:
            result["reason"] = "No compatible confirmed native state for the previous accepted execution. Confirm its saved artifact, or use the fallback."
            return result
        source = mode_records[0]
        if picture_free:
            from miniconstruct.i2va_contract import state_identity
            submission = next(s for s in predecessor.render_submissions if s.id == source.render_submission_id)
            if submission.job.status != "completed":
                result["reason"] = "Native-only I2VA requires the exact predecessor render to be completed as well as explicitly accepted. Recover its renderer status first."
                return result
            identity = state_identity(profile.workflow, cap)
            if not source.i2va_state_identity or source.i2va_state_identity != identity:
                result["reason"] = "Accepted native state has no matching immutable model/codec/format contract. Use a compatible Profile and a newly recorded, explicitly accepted execution."
                return result
        if (source.workflow_profile_id != profile.id
                or source.workflow_fingerprint != profile.fingerprint
                or source.compatibility_fingerprint != compatibility_identity(profile)):
            result["warnings"].append(
                "Accepted predecessor native state was created with a different Workflow Profile graph; "
                "it remains eligible because it was explicitly accepted. Current Profile mappings are used."
            )
        if cap.requires_tail_reference and (not predecessor.selected_take_id or workspace.mode.value != "Ref2VA"):
            result["reason"] = "This profile requires both native state and a real Ref2VA tail from a Selected Take."
            return result
        result.update(available=True, record=source, reason="Native state belongs to the accepted predecessor execution.")
        policy = current.planning.visual_handoff.strategy
        relation = current.planning.relationship_to_previous
        result["consume"] = relation != "independent" and (
            policy in {"native_h3_continuation", "native_i2va_continuation"} or (policy == "auto" and relation == "continue_previous_shot"))
        from miniconstruct.execution_routing import effective_references
        try:
            active_ids = {r.asset_id for r in effective_references(workspace, state.routing.get(current.generation_id))}
        except ValueError as exc:
            raise InvalidBindings([str(exc)]) from exc
        active_assets = [a for a in workspace.assets if a.id in active_ids]
        if result["consume"] and not cap.requires_tail_reference and any(
                a.kind == "video" and a.role in {"continuation_source", "tail_reference_continuation"} for a in active_assets):
            result.update(consume=False, available=False, reason="Native continuation conflicts with a Video continuation source. Remove that source or choose a profile explicitly requiring both.")
        if result["consume"] and not cap.allow_endpoint_anchor and any(
                a.kind == "image" and a.role == "first_frame_anchor" for a in active_assets):
            result.update(consume=False, available=False, reason="Native continuation conflicts with the current first-frame anchor. Remove it or use a profile that explicitly allows both.")
        return result


def native_summary(resolved):
    return {key: value for key, value in resolved.items() if key not in {"profile", "settings", "record", "sequence"}}


def prepare_native(store, project_id, generation_id, profile, workspace):
    resolved = resolve_native(store, project_id, generation_id, profile, workspace)
    if resolved["pictureFreeI2va"] and not resolved["consume"]:
        raise InvalidBindings(["Native-only I2VA unavailable: " + resolved["reason"]])
    if profile.native_continuation and profile.native_continuation.picture_free_i2va and not resolved["pictureFreeI2va"]:
        raise InvalidBindings(["This is a picture-free I2VA successor Profile. Explicitly select Native-only I2VA continuation; it cannot execute an initial clip or Auto fallback."])
    if not resolved["supported"]:
        return None, resolved
    cap = profile.native_continuation
    source = resolved["record"] if resolved["consume"] else None
    attempt = uuid4()
    namespace = f"mc/{project_id}/{generation_id}/{attempt}"
    prepared = NativePreparation(id=attempt, projectId=project_id, generationId=generation_id,
        workflowProfileId=profile.id, profileFingerprint=profile_identity(profile), workflowFingerprint=profile.fingerprint,
        compatibilityFingerprint=compatibility_identity(profile), mode=workspace.mode.value,
        chainId=f"mc-{project_id}", storageNamespace=namespace, saveKey=f"{namespace}/clip",
        sourceId=source.id if source else None, sourceGenerationId=source.generation_id if source else None,
        loadKey=(source.storage_namespace or source.save_key) if source else "", segmentIndex=resolved["segmentIndex"],
        saveSegmentIndex=resolved["segmentIndex"],
        loadSegmentIndex=source.save_segment_index if source else None, consume=bool(source),
        requestedContextFrames=resolved["requestedContextFrames"], contextFrames=resolved["contextFrames"],
        extensionFrames=resolved["extensionFrames"], audioSupported=cap.audio_continuity,
        audioEnabled=resolved["audioEnabled"], sourceOutFrame=resolved["sourceOutFrame"] if source else None)
    return prepared, resolved


def inject_native(graph, capability, prepared, duration):
    values = {"enable": prepared.consume, "chain_id": prepared.chain_id,
              "storage_namespace": prepared.storage_namespace, "generation_id": str(prepared.generation_id),
              "predecessor_generation_id": str(prepared.source_generation_id) if prepared.source_generation_id else "",
              "segment_index": prepared.segment_index,
              "save_segment_index": prepared.save_segment_index,
              "load_segment_index": prepared.load_segment_index,
              "context_frames": prepared.context_frames,
              "extension_frames": prepared.extension_frames, "extension_seconds": duration,
              "save_enabled": True, "save_key": prepared.save_key, "load_key": prepared.load_key,
              "audio_enabled": prepared.consume and prepared.audio_enabled,
              "source_out_frame": prepared.source_out_frame}
    for semantic, mapping in capability.inputs.items():
        value = values[semantic]
        if semantic in {"enable", "save_enabled", "audio_enabled"}:
            value = mapping.enabled_value if value else mapping.disabled_value
        if semantic in {"source_out_frame", "load_segment_index"} and value is None:
            value = mapping.unset_value
        graph[mapping.node_id]["inputs"][mapping.input_name] = value
    if not prepared.consume:
        # A disabled widget does not stop ComfyUI from evaluating an upstream
        # graph dependency. Profile-declared source links are the only edges
        # owned by predecessor consumption, so remove just those inputs.
        for link in capability.source_links:
            graph[link.node_id]["inputs"].pop(link.input_name, None)


class NativeContinuationStoreMixin:
    def reconcile_native_status(self, project_id, generation_id, submission):
        """Apply exact renderer terminal status to its still-pending native attempt."""
        status = {"completed": "available", "failed": "unavailable",
                  "cancelled": "unavailable"}.get(submission.job.status)
        if status is None:
            return
        with transaction():
            directory, *_ = self._output_context(project_id, generation_id)
            state = self._read_execution_state(directory)
            changed = False
            for record in state.continuation_executions:
                if (str(record.project_id) == str(project_id) and str(record.generation_id) == str(generation_id)
                        and record.render_submission_id == submission.id
                        and record.connection_id == submission.connection_id and record.status == "pending"):
                    record.status = status
                    changed = True
            if changed:
                self._write_execution_state(directory, state)

    def continuation_status(self, project_id, generation_id, *, renderer_summary=False):
        with transaction():
            directory, _, _, _, current = self._output_context(project_id, generation_id)
            state = self._read_execution_state(directory)
            records = [r for r in state.continuation_executions if str(r.generation_id) == current.generation_id]
            if renderer_summary:
                def summary(submission):
                    return {"id": str(submission.id), "createdAt": submission.created_at,
                            "comfyPromptId": str(submission.comfy_prompt_id),
                            "jobStatus": submission.job.status,
                            "outputFilenames": list(dict.fromkeys(
                                [output.filename for output in submission.job.outputs]
                                + [take.original_filename for take in current.takes
                                   if take.render_submission_id == submission.id]))}

                render_submissions = [summary(submission) for submission in current.render_submissions]
                submissions = {submission.id: submission for submission in current.render_submissions}
                executions = []
                for record in records:
                    row = record.model_dump(mode="json", by_alias=True)
                    submission = submissions.get(record.render_submission_id)
                    row["renderSubmission"] = None if submission is None else summary(submission)
                    executions.append(row)
            else:
                executions = records
                render_submissions = None
            resolved = resolve_native(self, project_id, generation_id)
            result = {"settings": resolved["settings"],
                    "rendererSeedSettings": state.renderer_seed_settings.get(current.generation_id),
                    "acceptedId": state.accepted_continuations.get(generation_id),
                    "executions": executions,
                    "resolution": native_summary(resolved)}
            if render_submissions is not None:
                result["renderSubmissions"] = render_submissions
            return result

    def set_continuation_settings(self, project_id, generation_id, settings):
        with transaction():
            directory, *_ = self._output_context(project_id, generation_id)
            resolve_native(self, project_id, generation_id, settings_override=settings)
            state = self._read_execution_state(directory)
            state.continuation_settings[generation_id] = settings
            self._write_execution_state(directory, state)
        return self.continuation_status(project_id, generation_id)

    def record_continuation(self, project_id, generation_id, prepared, submission, fingerprint, execution_fingerprint):
        with transaction():
            directory, _, _, _, current = self._output_context(project_id, generation_id)
            if submission.id not in {s.id for s in current.render_submissions}:
                raise InvalidBindings(["Continuation must belong to a recorded RenderSubmission"])
            state = self._read_execution_state(directory)
            record = ContinuationExecution(**prepared.model_dump(), renderSubmissionId=submission.id,
                connectionId=submission.connection_id, sourceFingerprint=fingerprint, executionFingerprint=execution_fingerprint)
            state.continuation_executions.append(record)
            self._write_execution_state(directory, state)
            return record

    def decide_continuation(self, project_id, generation_id, record_id, decision):
        with transaction():
            directory, _, _, _, current = self._output_context(project_id, generation_id)
            state = self._read_execution_state(directory)
            record = next((r for r in state.continuation_executions if str(r.id) == str(record_id)
                           and str(r.generation_id) == generation_id and str(r.project_id) == project_id), None)
            if record is None or record.render_submission_id not in {s.id for s in current.render_submissions}:
                raise InvalidBindings(["Continuation execution was not found in this Generation"])
            record.status = decision.status
            if decision.status == "available" and decision.accept:
                if current.selected_take_id and self._take(current, current.selected_take_id).render_submission_id != record.render_submission_id:
                    raise InvalidBindings(["Selected Take belongs to a different/unknown execution; select the matching Take or clear selection first"])
                state.accepted_continuations[generation_id] = record.id
                record.confirmed = True
            else:
                record.confirmed = False
                if state.accepted_continuations.get(generation_id) == record.id:
                    state.accepted_continuations.pop(generation_id, None)
            self._write_execution_state(directory, state)
        return self.continuation_status(project_id, generation_id)

    def remove_continuation(self, project_id, generation_id, record_id):
        """Remove only this Generation's native bookkeeping; renderer provenance survives."""
        with transaction():
            directory, _, _, _, current = self._output_context(project_id, generation_id)
            state = self._read_execution_state(directory)
            record = next((r for r in state.continuation_executions
                           if str(r.id) == str(record_id) and str(r.project_id) == str(project_id)
                           and str(r.generation_id) == current.generation_id), None)
            if record is None:
                raise InvalidBindings(["Native attempt was not found in this Generation."])
            if record.confirmed or state.accepted_continuations.get(current.generation_id) == record.id:
                raise InvalidBindings(["Revoke acceptance of this native attempt before removing it."])
            state.continuation_executions.remove(record)
            self._write_execution_state(directory, state)
        return self.continuation_status(project_id, generation_id, renderer_summary=True)

    def clear_unavailable_continuations(self, project_id, generation_id):
        with transaction():
            directory, _, _, _, current = self._output_context(project_id, generation_id)
            state = self._read_execution_state(directory)
            accepted = state.accepted_continuations.get(current.generation_id)
            retained = [r for r in state.continuation_executions if not (
                str(r.project_id) == str(project_id) and str(r.generation_id) == current.generation_id
                and r.status == "unavailable" and r.id != accepted)]
            removed = len(state.continuation_executions) - len(retained)
            if removed:
                state.continuation_executions = retained
                self._write_execution_state(directory, state)
        result = self.continuation_status(project_id, generation_id, renderer_summary=True)
        result["removedCount"] = removed
        return result
