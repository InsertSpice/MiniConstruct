"""Observation persistence and evidence lifecycle; expensive work runs outside the lock."""
import json

from miniconstruct.models.observations import ObservationEvidence, ObservedEndState
from miniconstruct.observation_media import extract_evidence, digest, MAX_IMAGE_BYTES
from miniconstruct.take_store import transaction, invalid


class ObservationStoreMixin:
    def observation_snapshot(self, project_id, generation_id, expected_fingerprint=None, take_id=None):
        with transaction():
            directory, manifest, _, _, generation = self._output_context(project_id, generation_id)
            self._check_observation_fingerprint(manifest, expected_fingerprint)
            if take_id is None:
                take_id = generation.selected_take_id
            if take_id is None:
                raise invalid("Select a Take before analyzing continuity")
            take = self._take(generation, take_id).model_copy(deep=True)
            source = self._output_path(directory, take.managed_media_path)
            previous = next((o for o in generation.take_observations if o.take_id == take.id), None)
            return directory, generation.model_copy(deep=True), take, source, previous.model_copy(deep=True) if previous else None

    def _check_observation_fingerprint(self, manifest, expected):
        if expected is not None and self._manifest_fingerprint(manifest) != expected:
            from miniconstruct.project_store import StaleProjectSnapshotError
            raise StaleProjectSnapshotError("Project changed; refresh before editing the observation")

    def _evidence_path(self, directory, take, name):
        if name not in {"endpoint.jpg", "contact-sheet.jpg", "evidence.json"}:
            raise invalid("Unknown evidence identity")
        return self._output_path(directory, f"outputs/observations/{take.id}/{take.sha256}/v1/{name}")

    def prepare_observation_evidence(self, directory, take, source):
        # The Take is authority: even a cache cannot authorize new analysis of missing/changed bytes.
        if not source.is_file():
            raise invalid("Take media is missing; reattach the original file before analysis")
        self._verify_media(source, take.sha256, take.byte_size)
        try:
            meta_path = self._evidence_path(directory, take, "evidence.json")
            if meta_path.stat().st_size > 4096:
                raise ValueError("Oversized cache metadata")
            cached = json.loads(meta_path.read_text(encoding="utf-8"))
            if cached["takeId"] != str(take.id) or cached["takeSha256"] != take.sha256:
                raise ValueError("Cache source identity mismatch")
            metadata = ObservationEvidence.model_validate(cached["evidence"])
            images = {}
            for name, sha in (("endpoint", metadata.endpoint_sha256), ("contact-sheet", metadata.contact_sheet_sha256)):
                path = self._evidence_path(directory, take, f"{name}.jpg")
                if path.stat().st_size > MAX_IMAGE_BYTES:
                    raise ValueError("Oversized cache")
                images[name] = path.read_bytes()
                if digest(images[name]) != sha:
                    raise ValueError("Cache digest mismatch")
            return metadata, images
        except (OSError, ValueError, KeyError, TypeError):
            try:
                return extract_evidence(source)
            except ValueError as exc:
                raise invalid(str(exc)) from exc

    def _publish_evidence(self, directory, take, evidence, images):
        metadata = {"takeId": str(take.id), "takeSha256": take.sha256,
                    "evidence": evidence.model_dump(mode="json", by_alias=True)}
        for name, data in {**{f"{k}.jpg": v for k, v in images.items()},
                           "evidence.json": json.dumps(metadata).encode()}.items():
            path = self._evidence_path(directory, take, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._atomic_write(path, data)

    def commit_observation(self, project_id, generation_id, observation, previous, images):
        with transaction():
            directory, manifest, generations, active, generation = self._output_context(project_id, generation_id)
            take = self._take(generation, observation.take_id)
            if take.sha256 != observation.take_sha256:
                raise invalid("Take content identity changed during analysis")
            current = next((o for o in generation.take_observations if o.take_id == take.id), None)
            if current != previous:
                raise invalid("Observation changed during analysis; refresh and explicitly retry to preserve corrections")
            self._publish_evidence(directory, take, observation.evidence, images)
            generation.take_observations = [o for o in generation.take_observations if o.take_id != take.id] + [observation]
            self._commit_sequence(directory, manifest, generations, active, hydrate_result=False)
            return self.generation_outputs(project_id, generation_id)

    def correct_observation(self, project_id, generation_id, take_id, expected_fingerprint, state=None, *, reset=False):
        with transaction():
            directory, manifest, generations, active, generation = self._output_context(project_id, generation_id)
            self._check_observation_fingerprint(manifest, expected_fingerprint)
            take = self._take(generation, take_id)
            observation = next((o for o in generation.take_observations if o.take_id == take.id), None)
            if observation is None:
                raise invalid("Analyze this Take before saving corrections")
            effective = observation.model_observed_state.model_copy(deep=True) if reset else ObservedEndState.model_validate(state)
            try:
                effective.validate_subjects(set(observation.offered_subject_ids))
            except ValueError as exc:
                raise invalid(str(exc)) from exc
            observation.effective_observed_state = effective
            observation.status = "model_observed" if reset else "user_edited"
            self._commit_sequence(directory, manifest, generations, active, hydrate_result=False)
            return self.generation_outputs(project_id, generation_id)

    def observation_evidence(self, project_id, generation_id, take_id, name):
        directory, _, take, source, observation = self.observation_snapshot(project_id, generation_id, take_id=take_id)
        if observation is None:
            raise invalid("No observation evidence exists for this Take")
        path = self._evidence_path(directory, take, f"{name}.jpg")
        expected = observation.evidence.endpoint_sha256 if name == "endpoint" else observation.evidence.contact_sheet_sha256
        if path.is_file() and path.stat().st_size <= MAX_IMAGE_BYTES and digest(path.read_bytes()) == expected:
            return path
        metadata, images = self.prepare_observation_evidence(directory, take, source)
        # Do not silently show a different extraction as the historical model's evidence.
        if metadata != observation.evidence:
            raise invalid("Regenerated evidence differs from this observation; explicitly re-analyze the Take")
        with transaction():
            current_directory, _, _, _, generation = self._output_context(project_id, generation_id)
            current_take = self._take(generation, take.id)
            if current_directory != directory or current_take.sha256 != take.sha256:
                raise invalid("Take changed while regenerating evidence")
            self._publish_evidence(directory, take, metadata, images)
        return path

    def _prune_observation_evidence(self, directory, previous, current):
        retained = {(t.id, t.sha256) for g in current for t in g.takes}
        for take in (t for g in previous for t in g.takes if (t.id, t.sha256) not in retained):
            for name in ("endpoint.jpg", "contact-sheet.jpg", "evidence.json"):
                try:
                    self._evidence_path(directory, take, name).unlink(missing_ok=True)
                except OSError:
                    pass
            # Empty directories have no authority; busy files can be reclaimed later.
            path = self._evidence_path(directory, take, "evidence.json").parent
            for _ in range(3):
                try:
                    path.rmdir()
                except OSError:
                    break
                path = path.parent
