"""Streamed project-owned outputs, under ProjectStore's canonical transaction lock."""
import hashlib
from datetime import datetime
import os
from pathlib import Path
import re
import time
from uuid import uuid4

from miniconstruct.models.takes import Take, TakePromptSnapshot

VIDEO_TYPES = {"mp4": "video/mp4", "webm": "video/webm", "mov": "video/quicktime",
               "mkv": "video/x-matroska", "avi": "video/x-msvideo", "m4v": "video/mp4",
               "mpeg": "video/mpeg", "mpg": "video/mpeg"}
MAX_TAKE_BYTES = 8 * 1024 ** 3
# Shared across ProjectStore instances; guarded by the Project transaction lock.
_ACTIVE_IMPORTS: dict[Path, int] = {}
# In-flight Sends cannot republish pre-reset history after a successful clear.
# Only needed within this process; no Send survives a server restart.
_GENERATED_STATE_EPOCHS: dict[Path, int] = {}


def transaction():
    from miniconstruct.project_store import _PROJECT_TRANSACTION_LOCK
    return _PROJECT_TRANSACTION_LOCK


def invalid(message):
    from miniconstruct.project_store import AssetPayloadError
    return AssetPayloadError(message)


class TakeStoreMixin:
    def generated_state_epoch(self, project_id):
        with transaction():
            directory = self._project_directory(project_id, must_exist=True)
            return _GENERATED_STATE_EPOCHS.get(directory, 0)

    def _advance_generated_state_epoch(self, directory):
        _GENERATED_STATE_EPOCHS[directory] = _GENERATED_STATE_EPOCHS.get(directory, 0) + 1

    def _ensure_no_take_import(self, directory):
        if _ACTIVE_IMPORTS.get(directory):
            raise invalid("Finish the current Take import before deleting this Project")

    def _output_context(self, project_id, generation_id):
        _, directory, manifest, generations, active = self._mutation_context(project_id)
        generation = generations[self._generation_index(generations, generation_id)]
        return directory, manifest, generations, active, generation

    @staticmethod
    def _take(generation, take_id):
        for take in generation.takes:
            if str(take.id) == str(take_id):
                return take
        from miniconstruct.project_store import GenerationNotFoundError
        raise GenerationNotFoundError("Take was not found in this Generation")

    def _output_path(self, directory, relative):
        # Reject even internal links: managed blobs have one project-local identity.
        path = self._safe_project_relative_path(directory, relative)
        for candidate in (directory, *path.relative_to(directory).parents):
            if candidate == Path("."):
                continue
            check = candidate if candidate.is_absolute() else directory / candidate
            if check.is_symlink() or check.is_junction():
                raise invalid("Managed output links are not supported")
        if path.is_symlink() or path.is_junction():
            raise invalid("Managed output links are not supported")
        return path

    def _output_summary(self, directory, generation):
        submissions = {s.id: s for s in generation.render_submissions}
        takes = []
        for take in generation.takes:
            row = take.model_dump(mode="json", by_alias=True, exclude={"prompt_snapshot", "managed_media_path"})
            row["availability"] = "available" if self._output_path(directory, take.managed_media_path).is_file() else "missing"
            row["promptProvenance"] = "known" if take.prompt_snapshot else "unknown"
            row["sourceFingerprint"] = take.prompt_snapshot.source_fingerprint if take.prompt_snapshot else None
            row["comfyPromptId"] = str(submissions[take.render_submission_id].comfy_prompt_id) if take.render_submission_id else None
            takes.append(row)
        return {"takeObservations": [o.model_dump(mode="json", by_alias=True) for o in generation.take_observations], "takes": takes, "selectedTakeId": str(generation.selected_take_id) if generation.selected_take_id else None,
                "renderSubmissions": [s.model_dump(mode="json", by_alias=True, exclude={"prompt_snapshot"})
                                      for s in generation.render_submissions]}

    def generation_outputs(self, project_id, generation_id):
        with transaction():
            directory, manifest, _, _, generation = self._output_context(project_id, generation_id)
            return {**self._output_summary(directory, generation), **self._revision_receipt(manifest)}

    def foreground_renders(self, project_id, generation_id=None, submission_id=None, *, include_revision=False):
        """Two bounded presentation rows; reads never reconcile or rewrite a Project.

        Return the best canonical active candidate and the exact watched record
        (including terminal truth). No graphs, outputs, credentials or Take state.
        """
        priority = {"running": 0, "queued": 1}
        candidate, candidate_rank, watched = None, None, None
        with transaction():
            _, _, manifest, generations, _ = self._mutation_context(project_id)
            revision = self._revision_receipt(manifest) if include_revision else {}
            for position, generation in enumerate(generations, 1):
                ordered = sorted(generation.render_submissions, key=lambda s: (s.created_at, str(s.id)))
                for send_number, submission in enumerate(ordered, 1):
                    is_watched = (str(generation.generation_id) == str(generation_id)
                                  and str(submission.id) == str(submission_id))
                    if submission.job.status not in priority and not is_watched:
                        continue
                    row = {"generationId": str(generation.generation_id), "generationNumber": position,
                           "id": str(submission.id), "sendNumber": send_number,
                           "comfyPromptId": str(submission.comfy_prompt_id), "createdAt": submission.created_at,
                           "job": {"status": submission.job.status}}
                    if is_watched:
                        watched = row
                    if submission.job.status in priority:
                        try:
                            created = datetime.fromisoformat(submission.created_at).timestamp()
                        except (ValueError, OverflowError, OSError):
                            created = 0
                        # Descending stable UUID after newest timestamp, matching the UI.
                        rank = (priority[submission.job.status], position, -created, -submission.id.int)
                        if candidate_rank is None or rank < candidate_rank:
                            candidate, candidate_rank = row, rank
        return {"candidate": candidate, "watched": watched, **revision}

    def active_render_submissions(self, project_id):
        """Detached exact identities for one bounded foreground discovery pass."""
        with transaction():
            _, _, _, generations, _ = self._mutation_context(project_id)
            return [(str(g.generation_id), s.model_copy(deep=True)) for g in generations
                    for s in g.render_submissions if s.job.status in {"running", "queued", "unknown"}]

    def project_revision(self, project_id):
        with transaction():
            _, _, manifest, _, _ = self._mutation_context(project_id)
            return self._revision_receipt(manifest)

    def update_foreground_jobs(self, project_id, observations):
        """Publish one queue snapshot in one commit, preserving newer exact reads."""
        with transaction():
            _, directory, manifest, generations, active = self._mutation_context(project_id)
            by_id = {(str(g.generation_id), s.id): (g, index, s) for g in generations
                     for index, s in enumerate(g.render_submissions)}
            changed = False
            for generation_id, previous, job in observations:
                location = by_id.get((generation_id, previous.id))
                if location is None:
                    continue
                generation, index, current = location
                if current.job != previous.job or current.job.status in {"completed", "failed", "cancelled"}:
                    continue
                generation.render_submissions[index] = current.model_copy(update={"job": job}, deep=True)
                changed = True
            if changed:
                self._commit_sequence(directory, manifest, generations, active, hydrate_result=False)

    def take_detail(self, project_id, generation_id, take_id):
        with transaction():
            *_, generation = self._output_context(project_id, generation_id)
            return self._take(generation, take_id).model_dump(mode="json", by_alias=True, exclude={"managed_media_path"})

    def take_media(self, project_id, generation_id, take_id):
        with transaction():
            directory, _, _, _, generation = self._output_context(project_id, generation_id)
            take = self._take(generation, take_id)
            path = self._output_path(directory, take.managed_media_path)
            if not path.is_file():
                from miniconstruct.project_store import GenerationNotFoundError
                raise GenerationNotFoundError("Take media is missing; reattach the original file")
            return path, take.mime_type

    def record_render_submission(self, project_id, generation_id, submission, *, expected_generated_state_epoch=None):
        with transaction():
            directory, manifest, generations, active, generation = self._output_context(project_id, generation_id)
            if expected_generated_state_epoch is not None and expected_generated_state_epoch != _GENERATED_STATE_EPOCHS.get(directory, 0):
                raise invalid("Generated state was cleared while this Send was in flight; old history was not restored")
            generation.render_submissions.append(submission.model_copy(deep=True))
            self._commit_sequence(directory, manifest, generations, active, hydrate_result=False)
            return self.generation_outputs(project_id, generation_id)

    def render_submission(self, project_id, generation_id, submission_id):
        with transaction():
            *_, generation = self._output_context(project_id, generation_id)
            return self._submission(generation, submission_id).model_copy(deep=True)

    @staticmethod
    def _submission(generation, submission_id):
        for submission in generation.render_submissions:
            if str(submission.id) == str(submission_id):
                return submission
        raise invalid("RenderSubmission does not belong to this Generation")

    def update_render_job(self, project_id, generation_id, submission_id, job, *, expected_job=None):
        with transaction():
            directory, manifest, generations, active, generation = self._output_context(project_id, generation_id)
            submission = self._submission(generation, submission_id)
            if expected_job is not None and submission.job != expected_job:
                return submission.model_copy(deep=True)
            # Concurrent older polls cannot undo a terminal result or imported associations.
            if submission.job.status not in {"completed", "failed", "cancelled"}:
                updated = submission.model_copy(update={"job": job}, deep=True)
                generation.render_submissions[generation.render_submissions.index(submission)] = updated
                self._commit_sequence(directory, manifest, generations, active, hydrate_result=False)
            return self.render_submission(project_id, generation_id, submission_id)

    def select_take(self, project_id, generation_id, take_id):
        with transaction():
            directory, manifest, generations, active, generation = self._output_context(project_id, generation_id)
            generation.selected_take_id = self._take(generation, take_id).id if take_id else None
            self._commit_sequence(directory, manifest, generations, active, hydrate_result=False)
            return self.generation_outputs(project_id, generation_id)

    def _prune_take_media(self, directory, previous_generations, generations):
        self._prune_observation_evidence(directory, previous_generations, generations)
        self._prune_handoff_cache(directory, previous_generations, generations)
        retained = {t.managed_media_path for g in generations for t in g.takes}
        retained.update(asset.path for g in generations for asset in g.asset_files)
        removed = {t.managed_media_path for g in previous_generations for t in g.takes} - retained
        for relative in removed:
            try:
                self._output_path(directory, relative).unlink(missing_ok=True)
            except OSError:
                # Committed metadata is authoritative; a busy player can defer reclamation.
                pass

    def delete_take(self, project_id, generation_id, take_id):
        with transaction():
            directory, manifest, generations, active, generation = self._output_context(project_id, generation_id)
            take = self._take(generation, take_id)
            generation.takes = [t for t in generation.takes if t.id != take.id]
            generation.take_observations = [o for o in generation.take_observations if o.take_id != take.id]
            if generation.selected_take_id == take.id:
                generation.selected_take_id = None
            self._commit_sequence(directory, manifest, generations, active, hydrate_result=False)
            self._prune_take_media(directory, self._generations(manifest)[0], generations)
            return self.generation_outputs(project_id, generation_id)

    def _import_snapshot(self, project_id, generation_id, provenance):
        # Capture before awaiting any bytes. Never consult current selection at completion.
        with transaction():
            *_, generation = self._output_context(project_id, generation_id)
            if provenance.source_kind == "comfyui_submission":
                submission = next((s for s in generation.render_submissions if s.id == provenance.render_submission_id), None)
                if submission is None:
                    raise invalid("Submission does not belong to this Generation")
                return submission.prompt_snapshot.model_copy(deep=True)
            if provenance.source_kind == "selected_prompt":
                document, status = self.execution_snapshot(project_id, generation_id)
                if status.selected is None or status.selected.id != provenance.selected_prompt_id:
                    raise invalid("Selected generated prompt changed; choose it again before importing")
                return TakePromptSnapshot.capture(status.selected, None if status.stale else document.workspace.mode)
            return None

    async def import_take(self, project_id, generation_id, chunks, filename, provenance=None, *, reattach_id=None, renderer_output_id=None):
        if not filename or len(filename) > 255 or re.search(r'[\x00-\x1f/\\]', filename) or filename in {".", ".."}:
            raise invalid("Use a filename without paths, traversal or control characters")
        extension = Path(filename).suffix.lower().lstrip(".")
        if extension not in VIDEO_TYPES:
            raise invalid("Choose an MP4, WebM, MOV, MKV, AVI, M4V or MPEG video")
        snapshot = self._import_snapshot(project_id, generation_id, provenance) if not reattach_id else None
        with transaction():
            directory, _, _, _, generation = self._output_context(project_id, generation_id)
            if renderer_output_id:
                candidate = self._renderer_candidate(generation, provenance, renderer_output_id)
                if candidate.imported_take_id:
                    return self.generation_outputs(project_id, generation_id)
            if reattach_id:
                self._take(generation, reattach_id)
            staging = self._output_path(directory, "outputs/.imports")
            staging.mkdir(parents=True, exist_ok=True)
            # Interrupted uploads have no canonical authority. Reclaim aged staging files.
            for old in staging.glob("*.tmp"):
                if not old.is_symlink() and old.is_file() and old.stat().st_mtime < time.time() - 86400:
                    old.unlink(missing_ok=True)
            temporary = staging / f"{uuid4().hex}.tmp"
            stream = temporary.open("xb")
            _ACTIVE_IMPORTS[directory] = _ACTIVE_IMPORTS.get(directory, 0) + 1
            import_directory = directory
        digest, size = hashlib.sha256(), 0
        try:
            with stream:
                async for chunk in chunks:
                    size += len(chunk)
                    if size > MAX_TAKE_BYTES:
                        raise invalid("Take upload exceeds 8 GiB")
                    stream.write(chunk)
                    digest.update(chunk)
                stream.flush()
                os.fsync(stream.fileno())
            if not size:
                raise invalid("The video file is empty")
            sha = digest.hexdigest()
            with transaction():
                directory, manifest, generations, active, generation = self._output_context(project_id, generation_id)
                if renderer_output_id:
                    candidate = self._renderer_candidate(generation, provenance, renderer_output_id)
                    if candidate.imported_take_id:
                        return self.generation_outputs(project_id, generation_id)
                if reattach_id:
                    take = self._take(generation, reattach_id)
                    if sha != take.sha256 or size != take.byte_size:
                        raise invalid("Replacement bytes differ. Add this video as a new Take instead.")
                    relative = take.managed_media_path
                else:
                    # Reuse a blob even if identical bytes arrived with a different extension.
                    existing_take = next((t for g in generations for t in g.takes if t.sha256 == sha), None)
                    relative = existing_take.managed_media_path if existing_take else f"outputs/media/{sha}.{extension}"
                    canonical_mime = existing_take.mime_type if existing_take else VIDEO_TYPES[extension]
                    take = Take(originalFilename=filename, mimeType=canonical_mime, byteSize=size,
                                sha256=sha, managedMediaPath=relative, sourceKind=provenance.source_kind,
                                promptSnapshot=snapshot, renderSubmissionId=provenance.render_submission_id)
                    generation.takes.append(take)
                    if renderer_output_id:
                        # Publish the idempotency receipt in the SAME atomic manifest as the Take.
                        # Retain it on deletion, so refresh does not resurrect a deleted Take.
                        candidate.imported_take_id = take.id
                target = self._output_path(directory, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                published = not target.exists()
                if not published:
                    self._verify_media(target, sha, size)
                else:
                    os.replace(temporary, target)
                try:
                    self._commit_sequence(directory, manifest, generations, active, hydrate_result=False)
                except BaseException:
                    # Remove only blobs not referenced by the last committed manifest.
                    retained = {t.managed_media_path for g in self._generations(self._read_manifest(directory))[0] for t in g.takes}
                    if published and relative not in retained:
                        target.unlink(missing_ok=True)
                    raise
                return self.generation_outputs(project_id, generation_id)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            finally:
                with transaction():
                    _ACTIVE_IMPORTS[import_directory] -= 1
                    if not _ACTIVE_IMPORTS[import_directory]:
                        del _ACTIVE_IMPORTS[import_directory]

    def _renderer_candidate(self, generation, provenance, output_id):
        if provenance is None or provenance.source_kind != "comfyui_submission":
            raise invalid("Renderer output requires exact submission provenance")
        submission = self._submission(generation, provenance.render_submission_id)
        candidate = next((o for o in submission.job.outputs if o.id == output_id), None)
        if submission.job.status != "completed" or candidate is None:
            raise invalid("Output does not belong to this completed RenderSubmission")
        return candidate

    def _fork_take_media(self, source_directory, destination, generations):
        for take in (t for g in generations for t in g.takes):
            source = self._output_path(source_directory, take.managed_media_path)
            target = self._output_path(destination, take.managed_media_path)
            if not source.is_file():
                continue  # Missing metadata survives a fork honestly.
            if target.exists():
                continue
            self._verify_media(source, take.sha256, take.byte_size)
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(source, target)
            except OSError:
                self._copy_media(source, target, digest=take.sha256)
