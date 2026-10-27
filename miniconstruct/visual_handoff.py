"""Rendered endpoint authority, disposable media and transient H3 projection."""
from dataclasses import dataclass, field
from functools import partial
import hashlib
import json
from pathlib import Path
import tempfile

from miniconstruct.handoff_media import CONTRACT, MAX_BYTES, extract_tail
from miniconstruct.models.visual_handoff import ResolvedVisualHandoff
from miniconstruct.models.workspace import H3Mode, ReferenceAsset, ImagePayload, MediaPayload
from miniconstruct.take_store import transaction, invalid

AUTO = {"continue_previous_shot": "tail_reference_continuation", "new_shot_preserve_continuity": "endpoint_then_immediate_cut",
        "match_previous_ending_frame": "endpoint_frame", "independent": "none"}


def resolved_strategy(policy, relationship):
    return AUTO.get(relationship, "none") if policy.strategy == "auto" else policy.strategy


def compatibility(workspace, strategy):
    if strategy == "none":
        return None
    if workspace.mode == H3Mode.REF2VA:
        return None
    if strategy != "tail_reference_continuation" and workspace.mode in {H3Mode.I2VA, H3Mode.FL2VA}:
        if not any(a.kind == "image" and a.role == "first_frame_anchor" for a in workspace.assets):
            return None
        return "The current first-frame Picture already owns the opening anchor; remove it to use the predecessor endpoint, or choose Ref2VA."
    return (f"{strategy.replace('_', ' ')} is incompatible with {workspace.mode.value}. "
            + ("Choose Ref2VA to use a bounded tail continuation Video." if strategy == "tail_reference_continuation" else
               "Choose Ref2VA, or I2VA/FL2VA with a free first-frame anchor."))


@dataclass
class HandoffProjection:
    source_authority: str | None = None
    native: dict | None = None
    identity: dict | None = None
    policy: ResolvedVisualHandoff | None = None
    asset: ReferenceAsset | None = None
    path: Path | None = None
    warnings: list[str] = field(default_factory=list)
    status: str = "none"
    compatible: bool = True
    native_i2va_context: str = ""

    def workspace(self, workspace):
        if self.native and self.native.get("pictureFreeI2va") and self.native.get("consume"):
            result = workspace.model_copy(deep=True)
            result._native_i2va = True
            result._native_i2va_context = self.native_i2va_context
            return result
        if self.asset is None:
            return workspace
        if any(a.id == self.asset.id for a in workspace.assets):
            raise invalid("A reference ID conflicts with reserved continuity handoff evidence")
        asset = self.asset.model_copy(update={"order": max((a.order for a in workspace.assets), default=-1) + 1})
        return workspace.model_copy(update={"assets": [*workspace.assets, asset]})

    def summary(self):
        return {"status": self.status, "compatible": self.compatible, "warnings": self.warnings, "native": self.native,
                "sourceAuthority": self.source_authority,
                "resolved": self.policy.model_dump(mode="json", by_alias=True) if self.policy else None}


class VisualHandoffStoreMixin:
    def _previous_rendered_endpoint_source(self, project_id, directory, predecessor):
        """Resolve media only; never select a Take or load a native artifact.

        Called inside the canonical snapshot transaction. Only exact, available,
        explicitly accepted default render provenance can replace null selection.
        """
        if predecessor and predecessor.selected_take_id:
            return self._take(predecessor, predecessor.selected_take_id), {"sourceAuthority": "selected_take"}, None
        if predecessor:
            from miniconstruct.native_continuation import predecessor_native_records
            records = predecessor_native_records(self, project_id, predecessor, self._read_execution_state(directory))
            records = [r for r in records if r.status == "available" and r.confirmed]
            if len(records) == 1:
                record = records[0]
                provenance = {"sourceAuthority": "accepted_render", "nativeAttemptId": str(record.id),
                              "renderSubmissionId": str(record.render_submission_id)}
                takes = [t for t in predecessor.takes if t.source_kind == "comfyui_submission"
                         and t.render_submission_id == record.render_submission_id
                         and self._output_path(directory, t.managed_media_path).is_file()]
                if len(takes) == 1:
                    return takes[0], provenance, None
                if takes:
                    return None, provenance, "The accepted previous render has multiple available Takes for endpoint handoff; explicitly select a Take in the previous Generation."
                return None, provenance, "The accepted previous render has no available managed Take for endpoint handoff. Recover/import its output or explicitly select another Take."
        return None, {}, "Endpoint handoff needs the previous rendered endpoint. Select a Take in the immediately preceding Generation, or recover the output associated with its accepted default render."

    def visual_handoff(self, project_id, generation_id=None, workspace=None, *, materialize=False,
                       strict=True, profile=None, read_only=False):
        result, materialize = self._visual_handoff_snapshot(project_id, generation_id, workspace,
            materialize=materialize, strict=strict, profile=profile, read_only=read_only)
        return materialize() if materialize else result

    async def visual_handoff_async(self, project_id, generation_id=None, workspace=None, *, materialize=False,
                                   strict=True, profile=None, read_only=False):
        from miniconstruct.handoff_async import materialize_off_loop
        result, materialize = self._visual_handoff_snapshot(project_id, generation_id, workspace,
            materialize=materialize, strict=strict, profile=profile, read_only=read_only)
        return await materialize_off_loop(materialize) if materialize else result

    def _visual_handoff_snapshot(self, project_id, generation_id=None, workspace=None, *, materialize=False, strict=True, profile=None, read_only=False):
        with transaction():
            directory = self._project_directory(project_id, must_exist=True)
            manifest = self._read_manifest(directory)
            generations, active = self._generations(manifest)
            index = self._generation_index(generations, generation_id or active)
            current = generations[index]
            workspace = workspace if workspace is not None else self.read_generation(project_id, current.generation_id).workspace
            saved = current.planning.visual_handoff
            relation = current.planning.relationship_to_previous
            strategy = resolved_strategy(saved, relation)
            result = HandoffProjection()
            from miniconstruct.native_continuation import resolve_native, native_summary
            native = resolve_native(self, project_id, current.generation_id, profile, workspace)
            result.native = native_summary(native)
            if native["consume"]:
                if not native.get("requiresTailReference"):
                    # Native state is execution-only; it adds no prompt-visible media.
                    result.status = "native_available"
                    if native["pictureFreeI2va"]:
                        source = native["record"]
                        predecessor = generations[index - 1]
                        submission = next(s for s in predecessor.render_submissions if s.id == source.render_submission_id)
                        # Historical accepted authoring, never a replacement for
                        # native opening state. Its old anchor preamble is not a
                        # successor first-frame instruction.
                        text = submission.prompt_snapshot.prompt
                        if text.startswith("For the target video, at 0.00 seconds into the target video,"):
                            text = text.partition("\n")[2].lstrip()
                        result.native_i2va_context = text
                        result.identity = {"strategy": "native_i2va_continuation", "version": 1,
                            "sourceId": str(source.id), "executionFingerprint": source.execution_fingerprint,
                            "profileId": native["profileId"], "stateIdentity": source.i2va_state_identity}
                    return result, None
                strategy = "tail_reference_continuation"
            elif saved.strategy in {"native_h3_continuation", "native_i2va_continuation"}:
                problem = ("Independent Generations cannot consume predecessor native state." if relation == "independent" else native["reason"])
                if strict:
                    raise invalid("Native H3 continuation unavailable: " + problem)
                result.status = "native_unavailable"
                result.warnings.append("Native H3 continuation unavailable: " + problem)
                result.identity = {"strategy": saved.strategy, "relationship": relation}
                return result, None
            if (strategy == "tail_reference_continuation" and native.get("profile") is not None
                    and (native["supported"] or native["settings"].profile_id == native["profile"].id)):
                active_profile = native["profile"]
                from miniconstruct.execution import binding_routes
                video_ordinal = 1 + sum(a.kind == "video" for a in workspace.assets)
                capacity = any(b.source.kind == "reference_asset" and b.source.asset_kind == "video"
                               and b.source.ordinal == video_ordinal and any(r.channel == "video_frames" for r in binding_routes(b))
                               for b in active_profile.bindings)
                if not capacity:
                    problem = "Active Workflow Profile has no tail Video capacity; using textual Observed/Planned continuity."
                    if saved.strategy != "auto" or native["consume"]:
                        if strict:
                            raise invalid(problem)
                    result.warnings.append(problem)
                    result.status = "textual_fallback"
                    return result, None
            if strategy == "none":
                return result, None
            identity = {"strategy": strategy, "requestedStrategy": saved.strategy, "relationship": relation,
                        "contract": CONTRACT if strategy == "tail_reference_continuation" else "endpoint-v1"}
            if strategy == "tail_reference_continuation":
                identity["tailSeconds"] = saved.tail_seconds
            if strategy == "endpoint_then_immediate_cut":
                identity["immediateCutMilliseconds"] = saved.immediate_cut_milliseconds
            result.identity = identity
            problem = compatibility(workspace, strategy)
            result.compatible = problem is None
            predecessor = generations[index - 1] if index else None
            if strategy in {"endpoint_frame", "endpoint_then_immediate_cut"}:
                take, provenance, source_problem = self._previous_rendered_endpoint_source(project_id, directory, predecessor)
                identity.update(provenance)
                result.source_authority = provenance.get("sourceAuthority")
            else:
                take = self._take(predecessor, predecessor.selected_take_id) if predecessor and predecessor.selected_take_id else None
                source_problem = "No previous Selected Take; select a Take in the immediately preceding Generation."
                result.source_authority = "selected_take" if take else None
            if take is None:
                result.status = "no_selected_take"
                problem = source_problem
                if saved.strategy == "auto":
                    # Auto without media has the same prompt-visible evidence as
                    # pure native continuation. The relationship is fingerprinted
                    # separately through planned continuity.
                    result.identity = None
            else:
                identity.update(sourceGenerationId=predecessor.generation_id, takeId=str(take.id), takeSha256=take.sha256)
                source = self._output_path(directory, take.managed_media_path)
                result.status = "available" if source.is_file() else "missing"
                if result.status == "missing":
                    problem = "Previous Selected Take media is missing; reattach source Take to use Visual Handoff."
                artifact_key = {"takeId": str(take.id), "takeSha256": take.sha256, "contract": identity["contract"]}
                if strategy == "tail_reference_continuation":
                    artifact_key["tailSeconds"] = saved.tail_seconds
                key = hashlib.sha256(json.dumps(artifact_key, sort_keys=True).encode()).hexdigest()
                identity["artifactIdentity"] = key
                result.policy = ResolvedVisualHandoff(relationship=relation, strategy=strategy,
                    sourceGenerationId=predecessor.generation_id, takeId=str(take.id), takeSha256=take.sha256,
                    artifactIdentity=key, mediaKind="video" if strategy == "tail_reference_continuation" else "image",
                    tailSeconds=saved.tail_seconds if strategy == "tail_reference_continuation" else None,
                    immediateCutMilliseconds=saved.immediate_cut_milliseconds if strategy == "endpoint_then_immediate_cut" else None)
            # Availability participates; disposable cache presence never does.
            identity["available"] = result.status == "available"
            identity["compatible"] = result.compatible
        if problem:
            result.warnings.append("Visual handoff omitted: " + problem)
            if saved.strategy != "auto" and strict:
                raise invalid(problem)
            return result, None
        if materialize:
            return result, partial(self._materialize_visual_handoff, project_id, generation_id,
                workspace, strict, profile, read_only, result, saved, strategy, directory, take, source)
        return result, None

    def _materialize_visual_handoff(self, project_id, generation_id, workspace, strict, profile,
                                    read_only, result, saved, strategy, directory, take, source):
        from miniconstruct.project_store import ProjectStoreError
        try:
            if read_only:
                self._verify_media(source, take.sha256, take.byte_size)
                with tempfile.TemporaryDirectory(prefix="miniconstruct-handoff-preview-") as temporary:
                    path = Path(temporary) / ("endpoint.jpg" if result.policy.media_kind == "image" else "tail.mp4")
                    if result.policy.media_kind == "image":
                        from miniconstruct.observation_media import extract_evidence
                        _, images = extract_evidence(source)
                        path.write_bytes(images["endpoint"])
                        metadata = {}
                    else:
                        metadata = extract_tail(source, path, result.policy.tail_seconds)
                    if result.policy.media_kind == "video":
                        with path.open("rb") as stream:
                            metadata.update(sha256=hashlib.file_digest(stream, "sha256").hexdigest(),
                                            byteSize=path.stat().st_size)
                    result.asset = handoff_asset(result.policy, path, metadata)
            else:
                path, metadata = self._handoff_artifact(directory, take, source, result.policy)
            # Publication may outlast a selection/reorder/delete; never serve stale authority.
            latest = self.visual_handoff(project_id, generation_id, workspace, strict=False, profile=profile)
            if latest.identity != result.identity:
                raise invalid("Visual handoff source changed during extraction; refresh and retry")
            result.path = None if read_only else path
            if strategy == "tail_reference_continuation":
                result.policy.tail_seconds = metadata["effectiveTailSeconds"]
            if not read_only:
                result.asset = handoff_asset(result.policy, path, metadata)
        except (OSError, ValueError, ProjectStoreError) as exc:
            if saved.strategy != "auto" and strict:
                raise invalid("Visual handoff unavailable; check or reattach source Take.") from exc
            result.status = "unavailable"
            result.warnings.append("Visual handoff omitted: extraction unavailable; check or reattach source Take.")
        return result

    def _handoff_artifact(self, directory, take, source, policy):
        from miniconstruct.project_store import ProjectStoreError
        self._verify_media(source, take.sha256, take.byte_size)
        relative = f"outputs/handoffs/{take.id}/{take.sha256}/{policy.artifact_identity}"
        name = "tail.mp4" if policy.media_kind == "video" else "endpoint.jpg"
        path = self._output_path(directory, f"{relative}/{name}")
        meta_path = self._output_path(directory, f"{relative}/metadata.json")
        try:
            if meta_path.stat().st_size > 4096 or path.stat().st_size > MAX_BYTES:
                raise ValueError("Oversized handoff cache")
            metadata = json.loads(meta_path.read_text())
            if metadata["artifactIdentity"] != policy.artifact_identity:
                raise ValueError("Wrong cache identity")
            if policy.media_kind == "video" and not 0 < metadata["effectiveTailSeconds"] <= policy.tail_seconds:
                raise ValueError("Invalid cached tail duration")
            self._verify_media(path, metadata["sha256"], metadata["byteSize"])
            return path, metadata
        except (OSError, ValueError, KeyError, TypeError, ProjectStoreError):
            pass
        with tempfile.TemporaryDirectory(prefix="miniconstruct-handoff-") as temporary:
            generated = Path(temporary) / name
            if policy.media_kind == "image":
                # Phase C's exact endpoint-v1 extraction contract, with UUID/SHA/hash validation.
                evidence, images = self.prepare_observation_evidence(directory, take, source)
                generated.write_bytes(images["endpoint"])
                metadata = {"endpointTimestamp": evidence.endpoint_timestamp}
            else:
                metadata = extract_tail(source, generated, policy.tail_seconds)
            with generated.open("rb") as stream:
                sha = hashlib.file_digest(stream, "sha256").hexdigest()
            metadata.update(artifactIdentity=policy.artifact_identity, sha256=sha, byteSize=generated.stat().st_size)
            self._verify_media(source, take.sha256, take.byte_size)
            with transaction():
                # Deletion during extraction must not resurrect a deleted Take's cache.
                current, _ = self._generations(self._read_manifest(directory))
                if not any(t.id == take.id and t.sha256 == take.sha256 for g in current for t in g.takes):
                    raise invalid("Source Take was deleted during extraction")
                path = self._output_path(directory, f"{relative}/{name}")
                if not path.exists():
                    self._copy_media(generated, path, digest=sha)
                else:
                    self._atomic_write(path, generated.read_bytes())
                self._atomic_write(meta_path, json.dumps(metadata).encode())
        return path, metadata

    def _prune_handoff_cache(self, directory, previous, current):
        retained = {t.id for g in current for t in g.takes}
        for take in (t for g in previous for t in g.takes if t.id not in retained):
            root = self._output_path(directory, f"outputs/handoffs/{take.id}")
            if not root.is_dir():
                continue
            # Only known disposable leaves; never recurse through filesystem links.
            for sha_dir in root.iterdir():
                if sha_dir.name != take.sha256 or sha_dir.is_symlink() or sha_dir.is_junction():
                    continue
                for entry in sha_dir.iterdir():
                    if entry.is_symlink() or entry.is_junction() or not entry.is_dir():
                        continue
                    for name in ("endpoint.jpg", "tail.mp4", "metadata.json"):
                        try:
                            self._output_path(directory, str((entry / name).relative_to(directory))).unlink(missing_ok=True)
                        except OSError:
                            pass
                    try:
                        entry.rmdir()
                    except OSError:
                        pass
                for folder in (sha_dir, root):
                    try:
                        folder.rmdir()
                    except OSError:
                        pass


def handoff_asset(policy, path=None, metadata=None):
    from miniconstruct.project_store import _data_url
    metadata = metadata or {}
    label = "Continuity handoff — predecessor endpoint" if policy.media_kind == "image" else f"Continuity handoff — predecessor {policy.tail_seconds:g}s tail continuation reference"
    data = dict(id="handoff:" + policy.artifact_identity, kind=policy.media_kind, filename=label,
                mimeType="image/jpeg" if policy.media_kind == "image" else "video/mp4",
                role="first_frame_anchor" if policy.media_kind == "image" else "tail_reference_continuation",
                options={"visualHandoff": policy.model_dump(mode="json", by_alias=True)},
                notes="Actual predecessor-output visual state; current explicit authoring overrides it.")
    if policy.media_kind == "image":
        data["image"] = ImagePayload(data_url=_data_url("image/jpeg", path.read_bytes())) if path else None
    else:
        data["durationSeconds"] = policy.tail_seconds
        if path:
            data["mediaPayload"] = MediaPayload(sha256=metadata["sha256"], byteLength=metadata["byteSize"])
    return ReferenceAsset.model_validate(data, context={"authoring": True})


def handoff_layers(workspace):
    from miniconstruct.h3.builder import numbered_assets
    layers = []
    for asset, label in numbered_assets(workspace):
        raw = asset.options.get("visualHandoff")
        if not raw:
            continue
        policy = ResolvedVisualHandoff.model_validate(raw)
        common = (f"<{label}> is ACTUAL recent predecessor-output continuity evidence, not canonical identity. "
            "For exact frame-zero physical state, follow its rendered pose, orientation, placement, framing, object placement and ongoing motion over a textual approximation in Incoming State. Incoming State remains semantic/planned guidance. "
            "Honor an explicit current request to change or reset the opening state. "
            "Apply those resets even to the opening anchor. Identity Pictures define canonical Subject identity; Environment references define environment identity. ")
        if policy.strategy == "tail_reference_continuation":
            intent = ("The supplied short Video is the actual final segment of the predecessor. Begin the new generated action from the END of this source Video. "
                "Preserve visible state, action direction, body momentum, object movement, environment and camera momentum where compatible with current authoring. "
                "Do not restart an earlier stage of the action. This is a continuation reference, not a frame-identical opening or guaranteed seamless overlap. ")
        elif policy.strategy == "endpoint_then_immediate_cut":
            intent = ("The opening frame must match the exact predecessor endpoint as a literal opening continuity anchor, subject to explicit current resets. "
                f"Immediately afterward, request a deliberate new-shot cut at {policy.immediate_cut_milliseconds} ms "
                "to the current Camera / Shot Intent. Preserve visible world/Subject/object state through the cut. "
                "Do not linger on the predecessor composition or let it dominate the rest of the shot's framing. "
                "This is requested authoring timing, not guaranteed frame-perfect milliseconds. ")
        else:
            intent = ("Use the actual predecessor endpoint as a strong opening/initial-frame keyframe anchor, subject to current resets. "
                      "Continue from that visual endpoint; there is no forced immediate camera cut. ")
        layers.append(("Visual Handoff policy", common + intent + "\n" + policy.model_dump_json(by_alias=True)))
    return layers
