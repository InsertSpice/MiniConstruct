"""Filtered transfers, canonical reset, managed reachability; all bytes are offline."""
import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from miniconstruct.app import app
from miniconstruct.execution_state import ProjectExecutionState, SavedPromptWrite, SelectionWrite, RendererSeedSettings
from miniconstruct.execution_routing import GenerationRouting
from miniconstruct.models.observations import TakeObservation
from miniconstruct.models.takes import RenderSubmission, RendererJobState, RendererOutput, TakeImport, TakePromptSnapshot
from miniconstruct.native_continuation_models import ContinuationExecution, ContinuationSettings
from miniconstruct.project_store import ProjectStore, StaleProjectSnapshotError
from test_media_persistence import asset
from test_observations import observed
from test_validator import valid_ref_prompt


@pytest.fixture
def history(tmp_path, workspace_factory, image_asset_factory, monkeypatch):
    root = tmp_path / "projects"
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(root))
    store = ProjectStore(root)
    picture = image_asset_factory("picture")
    workspace = workspace_factory(mode="Ref2VA", assets=[picture, asset(store, "video"), asset(store, "audio")],
                                  subjects=[{"id": "hero", "number": 4}], nextSubjectNumber=9)
    project = store.create_project(workspace, story_idea="A saved premise", sequence_context="Planned context",
                                   incoming_state="Planned start", intended_outgoing_state="Planned end")
    store.duplicate_generation(project.project_id)
    project = store.duplicate_generation(project.project_id)
    directory = root / f"project-{project.project_id}"
    external = tmp_path / "comfy-output" / "external.mp4"
    external.parent.mkdir(); external.write_bytes(b"renderer owned")
    generations, active = store._generations(store._read_manifest(directory))
    for generation in generations:
        gid = generation.generation_id
        _, status = store.execution_snapshot(project.project_id, gid)
        selected = store.select_execution_prompt(project.project_id, gid,
            SelectionWrite(prompt=valid_ref_prompt(), sourceFingerprint=status.current_fingerprint)).selected
        store.save_prompt(project.project_id, gid, SavedPromptWrite(prompt="Saved text history"))
        submission = RenderSubmission(connectionId=uuid4(), workflowProfileId=uuid4(), comfyPromptId=uuid4(),
            workflowFingerprint="a" * 64, profileFingerprint="b" * 64,
            promptSnapshot=TakePromptSnapshot.capture(selected),
            job=RendererJobState(status="completed", outputs=[RendererOutput(id="e" * 64,
                filename=external.name, subfolder=str(external.parent), type="output")]))
        store.record_render_submission(project.project_id, gid, submission)
        async def chunks(): yield b"same shared Take bytes"
        for _ in range(2):
            result = asyncio.run(store.import_take(project.project_id, gid, chunks(), "take.mp4",
                TakeImport(sourceKind="comfyui_submission", renderSubmissionId=submission.id)))
        store.select_take(project.project_id, gid, result["takes"][0]["id"])
    _, _, manifest, generations, active = store._mutation_context(project.project_id)
    for generation in generations:
        generation.planning.visual_handoff.strategy = "tail_reference_continuation"
        generation.planning.visual_handoff.tail_seconds = 2
        take = generation.takes[0]
        generation.take_observations = [TakeObservation(takeId=take.id, takeSha256=take.sha256,
            analyzedAt="2026-01-01", analyzerModel="offline", offeredSubjectIds=[], status="user_edited",
            evidence={"sampledTimestamps": [0], "endpointTimestamp": 0, "endpointSha256": "a" * 64,
                      "contactSheetSha256": "b" * 64},
            modelObservedState=observed("Observed original"), effectiveObservedState=observed("User correction"))]
        for name in ("endpoint.jpg", "contact-sheet.jpg", "evidence.json"):
            path = store._evidence_path(directory, take, name)
            path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b"derived observation")
        cache = directory / f"outputs/handoffs/{take.id}/{take.sha256}/policy"
        cache.mkdir(parents=True)
        for name in ("endpoint.jpg", "tail.mp4", "metadata.json"):
            (cache / name).write_bytes(b"derived handoff")
    store._commit_sequence(directory, manifest, generations, active)
    state = store._read_execution_state(directory)
    for generation in generations:
        gid = generation.generation_id
        profile = generation.render_submissions[0].workflow_profile_id
        state.continuation_settings[gid] = ContinuationSettings(profileId=profile, contextFrames=5,
                                                               audioEnabled=True, sourceOutFrame=17)
        record = ContinuationExecution(projectId=project.project_id, generationId=gid, workflowProfileId=profile,
            profileFingerprint="a" * 64, workflowFingerprint="b" * 64, compatibilityFingerprint="c" * 64,
            mode="Ref2VA", chainId=f"mc-{project.project_id}",
            saveKey=f"mc-{project.project_id}-{gid}-{uuid4()}", segmentIndex=1,
            requestedContextFrames=5, contextFrames=5, audioSupported=True, audioEnabled=True,
            renderSubmissionId=generation.render_submissions[0].id,
            connectionId=generation.render_submissions[0].connection_id,
            sourceFingerprint="d" * 64, executionFingerprint="e" * 64, status="available", confirmed=True)
        state.continuation_executions.append(record); state.accepted_continuations[gid] = record.id
        state.routing[gid] = GenerationRouting(assets={"video": {"useFrames": True, "useEmbeddedAudio": True}})
        state.renderer_seed_settings[gid] = RendererSeedSettings(mode="fixed", fixedSeed=23)
        state.workflow_control_overrides[gid] = {str(profile): {"steps": 12}}
    state.workflow_control_defaults = {"profile": {"steps": 30}}
    state = ProjectExecutionState.model_validate(state.model_dump(by_alias=True))
    store._write_execution_state(directory, state)
    # Reserved directories and import remnants are never whole-folder copied.
    (directory / "outputs/.imports").mkdir(exist_ok=True)
    (directory / "outputs/.imports/orphan.tmp").write_bytes(b"old import")
    return store, store.load_project(project.project_id), directory, external


def authoring(generation):
    return {key: value for key, value in generation.model_dump(by_alias=True).items()
            if key not in {"generationId", "createdAt", "updatedAt", "takes", "selectedTakeId",
                           "takeObservations", "renderSubmissions"}}


def assert_reset(store, directory, *, keep):
    manifest = store._read_manifest(directory)
    for g in manifest.sequence.generations:
        assert not g.takes and not g.take_observations and not g.render_submissions
        assert g.selected_take_id is None
    state = store._read_execution_state(directory)
    assert not state.selections and not state.continuation_executions and not state.accepted_continuations
    assert bool(state.saved_prompts) is keep
    assert state.routing and state.renderer_seed_settings and state.workflow_control_defaults
    assert state.workflow_control_overrides
    assert all(s.profile_id and s.context_frames == 5 and s.audio_enabled and s.source_out_frame is None
               for s in state.continuation_settings.values())
    if not keep:
        for g in manifest.sequence.generations:
            status = store.saved_prompt_status(manifest.project_id, g.generation_id)
            assert status.prompts == [] and status.active_saved_prompt_id is None and status.next_save_number == 1


@pytest.mark.parametrize("keep", [False, True])
@pytest.mark.parametrize("active_override", [False, True])
def test_fresh_filtered_copy_never_touches_take_media_and_references_survive_source_delete(history, monkeypatch, keep, active_override):
    store, project, source, external = history
    before = store._read_manifest(source)
    source_state = store._read_execution_state(source)
    transfers = []
    original_copy = store._copy_media
    original_read = Path.read_bytes
    original_open = Path.open
    import os
    original_link = os.link
    before_files = {p: p.read_bytes() for p in source.rglob("*") if p.is_file()}
    def guard(path):
        path = Path(path)
        assert path != external
        assert "outputs" not in path.parts, f"Historical output touched: {path}"
    def copy(src, dst, **kwargs):
        guard(src); transfers.append(Path(src)); return original_copy(src, dst, **kwargs)
    def read(path):
        guard(path); return original_read(path)
    def open_file(path, *args, **kwargs):
        guard(path); return original_open(path, *args, **kwargs)
    def link(src, dst, **kwargs):
        guard(src); transfers.append(Path(src)); return original_link(src, dst, **kwargs)
    def forbid_take_transfer(*args, **kwargs):
        raise AssertionError("Fresh must bypass the Take transfer layer entirely")
    with monkeypatch.context() as guarded:
        guarded.setattr(store, "_fork_take_media", forbid_take_transfer)
        guarded.setattr(store, "_copy_media", copy)
        guarded.setattr(Path, "read_bytes", read)
        guarded.setattr(Path, "open", open_file)
        guarded.setattr(os, "link", link)
        fork = store.duplicate_project(project.project_id, "Fresh", copy_mode="fresh", keep_saved_prompts=keep,
                                       active_workspace=project.workspace if active_override else None)
    destination = store.root / f"project-{fork.project_id}"
    assert_reset(store, destination, keep=keep)
    after = store._read_manifest(destination)
    assert [authoring(g) for g in after.sequence.generations] == [authoring(g) for g in before.sequence.generations]
    assert after.sequence.subjects == before.sequence.subjects
    assert after.sequence.next_subject_number == before.sequence.next_subject_number
    assert fork.active_generation_id == after.sequence.generations[-1].generation_id
    assert set(g.generation_id for g in before.sequence.generations).isdisjoint(g.generation_id for g in after.sequence.generations)
    assert not [p for p in (destination / "outputs").rglob("*") if p.is_file()]
    assert {p: p.read_bytes() for p in source.rglob("*") if p.is_file()} == before_files
    assert transfers and all("outputs" not in p.parts for p in transfers)
    if keep:
        copied_state = store._read_execution_state(destination)
        assert list(copied_state.saved_prompts.values()) == list(source_state.saved_prompts.values())
    references = {entry.path: (source / entry.path).read_bytes()
                  for g in before.sequence.generations for entry in g.asset_files}
    assert {entry.kind for g in after.sequence.generations for entry in g.asset_files} == {"image", "video", "audio"}
    store.delete_project(project.project_id)
    assert all((destination / path).read_bytes() == data for path, data in references.items())
    store.load_project(fork.project_id)
    assert external.read_bytes() == b"renderer owned"


def test_full_copy_keeps_historical_semantics_and_media(history):
    store, project, source, external = history
    before = store._read_manifest(source)
    original_state = store._read_execution_state(source)
    fork = store.duplicate_project(project.project_id, "Full")
    destination = store.root / f"project-{fork.project_id}"
    after = store._read_manifest(destination)
    for a, b in zip(before.sequence.generations, after.sequence.generations):
        assert a.takes == b.takes and a.render_submissions == b.render_submissions
        assert a.take_observations == b.take_observations and a.selected_take_id == b.selected_take_id
        assert all((destination / t.managed_media_path).read_bytes() == b"same shared Take bytes" for t in b.takes)
    state = store._read_execution_state(destination)
    assert list(state.selections.values()) == list(original_state.selections.values())
    assert list(state.saved_prompts.values()) == list(original_state.saved_prompts.values())
    assert not state.continuation_executions and not state.accepted_continuations
    assert all(s.source_out_frame == 17 for s in state.continuation_settings.values())
    assert not (destination / "outputs/observations").exists() and not (destination / "outputs/handoffs").exists()
    assert external.read_bytes() == b"renderer owned"


@pytest.mark.parametrize("clear_prompts", [False, True])
def test_clear_commits_then_prunes_only_managed_reachable_files(history, monkeypatch, clear_prompts):
    store, project, directory, external = history
    before = store._read_manifest(directory)
    files = {p: p.read_bytes() for p in directory.rglob("*") if p.is_file() and "assets" in p.parts}
    original_unlink = Path.unlink
    original_open = Path.open
    def open_file(path, *args, **kwargs):
        assert path != external
        return original_open(path, *args, **kwargs)
    def unlink(path, *args, **kwargs):
        assert path != external and path.is_relative_to(directory)
        if "outputs" in path.parts:
            assert_reset(store, directory, keep=not clear_prompts)
        return original_unlink(path, *args, **kwargs)
    with monkeypatch.context() as guarded:
        guarded.setattr(Path, "unlink", unlink)
        guarded.setattr(Path, "open", open_file)
        result = store.clear_generated_state(project.project_id, project.project_fingerprint,
                                             clear_saved_prompts=clear_prompts)
    after = store._read_manifest(directory)
    assert [authoring(g) for g in before.sequence.generations] == [authoring(g) for g in after.sequence.generations]
    assert result.active_generation_id == project.active_generation_id and result.project_fingerprint != project.project_fingerprint
    assert not list((directory / "outputs/media").iterdir())
    assert not [p for p in (directory / "outputs/observations").rglob("*") if p.is_file()]
    assert not [p for p in (directory / "outputs/handoffs").rglob("*") if p.is_file()]
    assert all(path.read_bytes() == data for path, data in files.items())
    assert external.read_bytes() == b"renderer owned"


def test_clear_stale_and_write_failure_leave_metadata_and_files_untouched(history, monkeypatch):
    store, project, directory, _ = history
    before = {p: p.read_bytes() for p in directory.rglob("*") if p.is_file()}
    with pytest.raises(StaleProjectSnapshotError):
        store.clear_generated_state(project.project_id, "0" * 64)
    def fail(*args, **kwargs): raise OSError("disk full")
    monkeypatch.setattr(store, "_commit_sequence", fail)
    with pytest.raises(OSError): store.clear_generated_state(project.project_id, project.project_fingerprint)
    assert {p: p.read_bytes() for p in directory.rglob("*") if p.is_file()} == before


def test_busy_media_does_not_resurrect_metadata(history, monkeypatch):
    store, project, directory, _ = history
    original = Path.unlink
    def busy(path, *args, **kwargs):
        if "media" in path.parts: raise PermissionError("player holds file")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", busy)
    store.clear_generated_state(project.project_id, project.project_fingerprint)
    assert_reset(store, directory, keep=True)
    assert list((directory / "outputs/media").iterdir())


def test_clear_api_requires_fingerprint_and_options_are_explicit(history):
    store, project, directory, _ = history
    client = TestClient(app)
    path = f"/api/projects/{project.project_id}/clear-generated-state"
    assert client.post(path, json={}).status_code == 422
    assert client.post(path, json={"expectedProjectFingerprint": "0" * 64}).status_code == 409
    fresh = client.post(f"/api/projects/{project.project_id}/save-as", json={"name": "Fresh API", "copyMode": "fresh"})
    assert fresh.status_code == 201 and fresh.json()["outputs"]["takes"] == []
    response = client.post(path, json={"expectedProjectFingerprint": project.project_fingerprint})
    assert response.status_code == 200 and response.json()["outputs"]["takes"] == []
    assert_reset(store, directory, keep=True)


def test_reset_contract_audits_every_sidecar_field():
    # A new sidecar field requires an explicit configuration/history decision.
    assert set(ProjectExecutionState.model_fields) == {"schema_version", "continuation_settings",
        "continuation_executions", "accepted_continuations", "selections", "saved_prompts", "routing",
        "renderer_seed_settings", "workflow_control_defaults", "workflow_control_overrides"}


def test_clear_invalidates_in_flight_send_without_restoring_old_submission(history):
    store, project, directory, _ = history
    epoch = store.generated_state_epoch(project.project_id)
    submission = store._read_manifest(directory).sequence.generations[-1].render_submissions[0]
    store.clear_generated_state(project.project_id, project.project_fingerprint)
    from miniconstruct.project_store import ProjectStoreError
    with pytest.raises(ProjectStoreError, match="cleared"):
        store.record_render_submission(project.project_id, project.active_generation_id, submission,
                                       expected_generated_state_epoch=epoch)
    assert_reset(store, directory, keep=True)


def test_shared_blob_partial_gc_retains_remaining_record(history):
    store, project, directory, _ = history
    before = store._read_manifest(directory)
    take = before.sequence.generations[0].takes[0]
    blob = directory / take.managed_media_path
    for generation in before.sequence.generations:
        for record in generation.takes:
            if record.id != take.id:
                store.delete_take(project.project_id, generation.generation_id, str(record.id))
                assert blob.exists()
    store.delete_take(project.project_id, before.sequence.generations[0].generation_id, str(take.id))
    assert not blob.exists()
