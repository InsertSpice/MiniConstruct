"""One-shot Sequence inspection uses saved authority without transport or writes."""
from uuid import uuid4
import json

import pytest

from miniconstruct import execution as ex
from miniconstruct.execution_state import SelectionWrite
from miniconstruct.models.takes import RenderSubmission, RendererJobState, TakePromptSnapshot
from miniconstruct.native_continuation_models import ContinuationSettings
from miniconstruct.project_store import ProjectStore
from miniconstruct.sequence_render_readiness import sequence_render_readiness
from test_workflow_preparation import capacity_profile
from test_native_continuation import native_draft, send, confirm, next_generation, choose as choose_native
from test_validator import VALID_BASE


@pytest.fixture
def scene(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path / "projects")
    config = ex.ExecutionStore(tmp_path / "execution")
    profile = config.save("profiles", capacity_profile())
    connection = config.save("connections", ex.ConnectionWrite(name="Local", baseUrl="http://localhost:8188"))
    project = store.create_project(workspace_factory())
    store.set_continuation_settings(project.project_id, project.active_generation_id,
                                    ContinuationSettings(profileId=profile.id))
    return store, config, profile, connection, project


def choose(store, project_id, generation_id):
    status = store.execution_snapshot(project_id, generation_id)[1]
    return store.select_execution_prompt(project_id, generation_id,
        SelectionWrite(prompt=VALID_BASE, sourceFingerprint=status.current_fingerprint)).selected


def attempt(scene, status, generation_id=None):
    store, _, profile, connection, project = scene
    gid = generation_id or project.active_generation_id
    selected = store.execution_snapshot(project.project_id, gid)[1].selected
    submission = RenderSubmission(connectionId=connection.id, workflowProfileId=profile.id,
        workflowFingerprint=profile.fingerprint, profileFingerprint="a" * 64,
        comfyPromptId=uuid4(), promptSnapshot=TakePromptSnapshot.capture(selected),
        job=RendererJobState(status=status))
    store.record_render_submission(project.project_id, gid, submission)
    return submission


def inspect(scene):
    store, config, _, _, project = scene
    return sequence_render_readiness(project.project_id, store, config)


def test_first_candidate_prepares_without_mutating(scene):
    store, _, profile, connection, project = scene
    choose(store, project.project_id, project.active_generation_id)
    directory = store._project_directory(project.project_id, must_exist=True)
    before = [(p.name, p.read_bytes()) for p in directory.rglob("*") if p.is_file()]
    result = inspect(scene)
    assert result["generationId"] == project.active_generation_id
    assert result["candidate"] and result["prepareReady"]
    assert result["profileId"] == str(profile.id) and result["connectionId"] == str(connection.id)
    assert [(p.name, p.read_bytes()) for p in directory.rglob("*") if p.is_file()] == before


@pytest.mark.parametrize("status,fragment", [
    ("queued", "active render"), ("running", "active render"),
    ("unknown", "Recover"), ("completed", "completed")])
def test_prior_attempt_stops_scan(scene, status, fragment):
    store, _, _, _, project = scene
    choose(store, project.project_id, project.active_generation_id)
    attempt(scene, status)
    second = store.create_generation(project.project_id)
    choose(store, project.project_id, second.active_generation_id)
    result = inspect(scene)
    assert result["generationId"] == project.active_generation_id
    assert not result["candidate"] and fragment in result["reason"]


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_terminal_failure_allows_ordinary_new_prepare(scene, status):
    store, _, _, _, project = scene
    choose(store, project.project_id, project.active_generation_id)
    attempt(scene, status)
    result = inspect(scene)
    assert result["candidate"] and result["prepareReady"]
    assert result["rendererStatus"] == "failed_or_cancelled"


def test_missing_selection_does_not_invent_prompt(scene):
    result = inspect(scene)
    assert not result["candidate"] and "explicit H3 execution selection" in result["reason"]
    assert not result["prepareAttempted"]


def test_missing_connection_is_reported_without_guessing(scene):
    store, config, _, connection, project = scene
    choose(store, project.project_id, project.active_generation_id)
    config.delete("connections", connection.id)
    result = inspect(scene)
    assert result["prepareReady"] and result["connectionRequired"]


def test_mechanical_prepare_failure_stays_at_first_generation(scene):
    store, config, profile, _, project = scene
    choose(store, project.project_id, project.active_generation_id)
    # A saved Profile can be chosen but mechanically incompatible with the mode.
    incompatible = config.save("profiles", ex.ProfileCreate(**{
        **capacity_profile().model_dump(mode="json", by_alias=True),
        "compatibleModes": ["I2VA"]}))
    store.set_continuation_settings(project.project_id, project.active_generation_id,
                                    ContinuationSettings(profileId=incompatible.id))
    result = inspect(scene)
    assert not result["candidate"] and result["prepareAttempted"]
    assert "not compatible" in result["reason"]


def test_accepted_native_survives_profile_drift_and_pending_reroll(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path / "projects")
    config = ex.ExecutionStore(tmp_path / "execution")
    profile = config.save("profiles", ex.ProfileCreate(**native_draft()))
    connection = config.save("connections", ex.ConnectionWrite(name="Local", baseUrl="http://localhost:8188"))
    first = store.create_project(workspace_factory())
    store.set_continuation_settings(first.project_id, first.active_generation_id,
                                    ContinuationSettings(profileId=profile.id))
    chain = (store, config, profile, connection, first)
    choose_native(store, first)
    accepted, _ = send(chain)
    confirm(chain, first, accepted)
    send(chain)  # Pending reroll must not replace the accepted execution.
    second = next_generation(chain, workspace_factory)
    choose_native(store, second)
    draft = native_draft()
    graph = json.loads(draft["workflowJson"])
    graph["h3"]["inputs"]["readiness_marker"] = "changed graph"
    draft["workflowJson"] = json.dumps(graph)
    config.save("profiles", ex.ProfileCreate(**draft), profile.id)
    result = sequence_render_readiness(first.project_id, store, config)
    assert result["candidate"] and result["prepareReady"]
    assert result["generationId"] == second.active_generation_id
    assert str(accepted.continuation_execution_id) in result["continuitySource"]
    assert any("different Workflow Profile graph" in warning for warning in result["warnings"])


def test_sequence_candidate_prepare_naturally_honors_aspect_ratio_binding(tmp_path, workspace_factory, monkeypatch):
    import importlib
    from copy import deepcopy

    readiness_module = importlib.import_module("miniconstruct.sequence_render_readiness")
    store = ProjectStore(tmp_path / "projects")
    config = ex.ExecutionStore(tmp_path / "execution")
    profile = config.save("profiles", capacity_profile(0))
    graph = deepcopy(profile.workflow)
    graph["h3"]["inputs"]["aspect_ratio"] = "auto"
    bindings = [item.model_dump(mode="json", by_alias=True) for item in profile.bindings]
    bindings.append({"source": {"kind": "aspect_ratio"}, "nodeId": "h3", "inputName": "aspect_ratio"})
    profile = config.save("profiles", ex.ProfileCreate(name="Aspect", compatibleModes=list(ex.H3Mode),
        workflowJson=json.dumps(graph), bindings=bindings), profile.id)
    project = store.create_project(workspace_factory(aspectRatio="16:9"))
    store.set_continuation_settings(project.project_id, project.active_generation_id,
                                    ContinuationSettings(profileId=profile.id))
    choose(store, project.project_id, project.active_generation_id)
    actual_prepare = readiness_module.prepare_workflow
    observed = []

    def capture(*args, **kwargs):
        prepared = actual_prepare(*args, **kwargs)
        observed.append(prepared.graph["h3"]["inputs"]["aspect_ratio"])
        return prepared

    monkeypatch.setattr(readiness_module, "prepare_workflow", capture)
    result = sequence_render_readiness(project.project_id, store, config)
    assert result["candidate"] and result["prepareReady"]
    assert observed == ["16:9"]
