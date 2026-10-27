"""Continuation setup uses real Project mutations, without providers or renders."""
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from miniconstruct.app import app
from miniconstruct.models.takes import TakeImport
from miniconstruct.project_store import ProjectStore, ProjectStoreError, StaleProjectSnapshotError
from miniconstruct.models.observations import TakeObservation
from miniconstruct import execution as ex
from miniconstruct.native_continuation_models import ContinuationSettings
from test_visual_handoff import video
from test_observations import observed
from test_workflow_preparation import capacity_profile


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Continuation setup must not call providers or renderers")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)


@pytest.fixture
async def source(tmp_path, monkeypatch, workspace_factory):
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("MINICONSTRUCT_EXECUTION_ROOT", str(tmp_path / "execution"))
    store = ProjectStore()
    project = store.create_project(workspace_factory(), intended_outgoing_state="Planned ending")
    async def chunks(): yield video(tmp_path / "source.mp4")
    outputs = await store.import_take(project.project_id, project.active_generation_id, chunks(), "external.mp4",
                                      TakeImport(sourceKind="external_unknown"))
    take = outputs["takes"][0]
    store.select_take(project.project_id, project.active_generation_id, take["id"])
    return store, project, take


def authority(store, project, take):
    return dict(expected_project_fingerprint=store.load_project(project.project_id).project_fingerprint,
                take_id=take["id"], take_sha256=take["sha256"])


def raw(store, p):
    return json.loads((store.root / f"project-{p.project_id}" / "project.json").read_text())


async def test_create_fresh_after_source_external_tail_and_no_source_changes(source):
    store, p, take = source
    before = raw(store, p)["sequence"]["generations"][0]
    new = store.continue_selected_take(p.project_id, p.active_generation_id, **authority(store, p, take))
    assert [g.generation_id for g in new.sequence.generations] == [p.active_generation_id, new.active_generation_id]
    assert new.workspace.mode == "Ref2VA"
    assert new.workspace.creative_request == new.workspace.dialogue == ""
    assert new.workspace.aspect_ratio == p.workspace.aspect_ratio
    assert new.workspace.creative_controls.music.mode == "off"
    assert new.planning.relationship_to_previous == "continue_previous_shot"
    assert new.planning.visual_handoff.strategy == "auto"
    assert new.planning.visual_handoff.tail_seconds == 1.5
    assert new.outputs == {"takes": [], "selectedTakeId": None, "takeObservations": [], "renderSubmissions": []}
    assert store.saved_prompt_status(p.project_id, new.active_generation_id).prompts == []
    assert store.execution_snapshot(p.project_id, new.active_generation_id)[1].selected is None
    assert raw(store, p)["sequence"]["generations"][0] == before
    handoff = store.visual_handoff(p.project_id, new.active_generation_id, materialize=True)
    assert not handoff.native["consume"]
    assert handoff.policy.take_id == take["id"] and handoff.policy.take_sha256 == take["sha256"]
    assert handoff.policy.tail_seconds == 1.5
    assert handoff.asset.kind == "video" and handoff.asset.role == "tail_reference_continuation"
    assert handoff.path.stat().st_size > 0
    assert new.workspace.assets == []  # Tail is a transient handoff only.
    context = store.h3_continuity_context(p.project_id, new.active_generation_id)
    assert context.predecessor_observation is None
    assert context.predecessor_intended_outgoing_state == "Planned ending"
    assert new.incoming_state == ""


async def test_insert_keeps_later_generations_and_shares_only_identity_pictures(source, image_asset_factory, workspace_factory):
    store, p, take = source
    roles = ["subject_identity", "style_appearance", "environment", "first_frame_anchor", "last_frame_anchor",
             "continuity_state", "keyframe_anchor", "general_visual"]
    assets = [image_asset_factory(asset_id=f"pic-{i}", role=role, order=i) for i, role in enumerate(roles)]
    assets.append(image_asset_factory(asset_id="detached-style", role="style_appearance").model_copy(update={"attached": False}))
    assets.extend([
        {"id": "prior-tail", "kind": "video", "filename": "tail.mp4", "mimeType": "video/mp4",
         "role": "tail_reference_continuation", "attached": False},
        {"id": "audio", "kind": "audio", "filename": "music.wav", "mimeType": "audio/wav",
         "role": "full_reuse", "attached": False},
    ])
    p = store.save_project(p.project_id, workspace_factory(mode="Ref2VA", assets=assets,
        subjects=[{"id": "subject-1", "number": 1}, {"id": "subject-2", "number": 2}], nextSubjectNumber=3,
        creativeControls={"visualStyle": {"preset": "watercolor"}, "music": {"mode": "on", "description": "Source score"}}))
    later = store.create_generation(p.project_id)
    store.activate_generation(p.project_id, p.active_generation_id)
    before = raw(store, p)
    new = store.continue_selected_take(p.project_id, p.active_generation_id, **authority(store, p, take))
    assert [g.generation_id for g in new.sequence.generations] == [p.active_generation_id, new.active_generation_id, later.active_generation_id]
    assert [a.role for a in new.workspace.assets] == roles[:3]
    assert new.workspace.subjects == p.workspace.subjects
    assert new.sequence.subjects == p.sequence.subjects
    assert new.workspace.next_subject_number == p.workspace.next_subject_number
    assert new.workspace.creative_controls.visual_style == p.workspace.creative_controls.visual_style
    assert new.workspace.creative_controls.music.mode == "off"
    after = raw(store, p)["sequence"]["generations"]
    assert after[0] == before["sequence"]["generations"][0]
    assert after[1]["assetFiles"] == after[0]["assetFiles"][:3]
    assert after[2]["generationId"] == later.active_generation_id


async def test_exact_authority_missing_selection_media_and_duplicate_requests(source):
    store, p, take = source
    request = authority(store, p, take)
    with pytest.raises(StaleProjectSnapshotError):
        store.continue_selected_take(p.project_id, p.active_generation_id, **{**request, "take_id": str(uuid4())})
    with pytest.raises(StaleProjectSnapshotError):
        store.continue_selected_take(p.project_id, p.active_generation_id, **{**request, "take_sha256": "0" * 64})
    store.select_take(p.project_id, p.active_generation_id, None)
    with pytest.raises(ProjectStoreError, match="Select a Take"):
        store.continue_selected_take(p.project_id, p.active_generation_id, **authority(store, p, take))
    store.select_take(p.project_id, p.active_generation_id, take["id"])
    # Two requests carry the same captured authority; only one may commit.
    request = authority(store, p, take)
    def create():
        try: return store.continue_selected_take(p.project_id, p.active_generation_id, **request)
        except StaleProjectSnapshotError: return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: create(), range(2)))
    assert sum(r is not None for r in results) == 1
    assert len(store.load_project(p.project_id).sequence.generations) == 2
    store.activate_generation(p.project_id, p.active_generation_id)
    path, _ = store.take_media(p.project_id, p.active_generation_id, take["id"])
    path.unlink()
    before = raw(store, p)
    with pytest.raises(ProjectStoreError, match="missing"):
        store.continue_selected_take(p.project_id, p.active_generation_id, **authority(store, p, take))
    assert raw(store, p) == before


async def test_delayed_selection_change_and_sequence_change_reject_stale_action(source):
    store, p, take = source
    captured = authority(store, p, take)
    async def chunks(): yield b"another source"
    outputs = await store.import_take(p.project_id, p.active_generation_id, chunks(), "other.mp4", TakeImport(sourceKind="external_unknown"))
    store.select_take(p.project_id, p.active_generation_id, outputs["takes"][-1]["id"])
    before = raw(store, p)
    with pytest.raises(StaleProjectSnapshotError):
        store.continue_selected_take(p.project_id, p.active_generation_id, **captured)
    assert raw(store, p) == before
    store.select_take(p.project_id, p.active_generation_id, take["id"])
    captured = authority(store, p, take)
    store.create_generation(p.project_id)
    store.activate_generation(p.project_id, p.active_generation_id)
    with pytest.raises(StaleProjectSnapshotError):
        store.continue_selected_take(p.project_id, p.active_generation_id, **captured)


@pytest.mark.parametrize("status", ["user_edited", "model_observed"])
async def test_observation_resolves_from_predecessor_without_copy(source, status):
    store, p, take = source
    directory, manifest, generations, active, g = store._output_context(p.project_id, p.active_generation_id)
    model = observed("Model ending")
    effective = observed("Corrected ending") if status == "user_edited" else model
    g.take_observations.append(TakeObservation(takeId=take["id"], takeSha256=take["sha256"], analyzedAt="2026-10-20",
        analyzerModel="offline", offeredSubjectIds=[], status=status, modelObservedState=model, effectiveObservedState=effective,
        evidence={"sampledTimestamps": [3.9], "endpointTimestamp": 3.9, "endpointSha256": "a" * 64, "contactSheetSha256": "b" * 64}))
    store._commit_sequence(directory, manifest, generations, active)
    before = raw(store, p)["sequence"]["generations"][0]
    new = store.continue_selected_take(p.project_id, p.active_generation_id, **authority(store, p, take))
    context = store.h3_continuity_context(p.project_id, new.active_generation_id)
    assert context.predecessor_observation["effectiveObservedState"]["summary"] == effective.summary
    assert context.predecessor_observation["status"] == status
    assert not new.outputs["takeObservations"] and new.incoming_state == ""
    assert raw(store, p)["sequence"]["generations"][0] == before


async def test_http_mutation_and_replay(source):
    store, p, take = source
    request = authority(store, p, take)
    body = {"expectedProjectFingerprint": request["expected_project_fingerprint"], "takeId": take["id"], "takeSha256": take["sha256"]}
    client = TestClient(app)
    url = f"/api/projects/{p.project_id}/generations/{p.active_generation_id}/continue-selected-take"
    response = client.post(url, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["sequence"]["generations"][0]["generationId"] == p.active_generation_id
    assert client.post(url, json=body).status_code == 409


async def test_incompatible_profile_retains_existing_textual_fallback(source):
    store, p, take = source
    config = ex.ExecutionStore()
    profile = config.save("profiles", capacity_profile(av=False))
    store.set_continuation_settings(p.project_id, p.active_generation_id, ContinuationSettings(profileId=profile.id))
    before_profile = config.path.read_bytes()
    new = store.continue_selected_take(p.project_id, p.active_generation_id, **authority(store, p, take))
    handoff = store.visual_handoff(p.project_id, new.active_generation_id)
    assert handoff.status == "textual_fallback" and handoff.asset is None
    assert "no tail Video capacity" in " ".join(handoff.warnings)
    assert not handoff.native["consume"]
    assert config.path.read_bytes() == before_profile


async def test_failed_commit_and_corrupt_media_leave_sequence_unchanged(source, monkeypatch):
    store, p, take = source
    request = authority(store, p, take)
    before = raw(store, p)
    def fail(*args, **kwargs): raise OSError("simulated manifest write failure")
    with monkeypatch.context() as patch:
        patch.setattr(store, "_write_manifest", fail)
        with pytest.raises(OSError, match="simulated"):
            store.continue_selected_take(p.project_id, p.active_generation_id, **request)
    assert raw(store, p) == before
    path, _ = store.take_media(p.project_id, p.active_generation_id, take["id"])
    data = path.read_bytes()
    path.write_bytes(bytes([data[0] ^ 1]) + data[1:])
    with pytest.raises(ProjectStoreError, match="verification failed"):
        store.continue_selected_take(p.project_id, p.active_generation_id, **request)
    assert raw(store, p) == before
