"""Real orchestration, exact saved authority and barrier-controlled fake transport."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import json
from threading import Barrier, Event, Lock
from uuid import UUID

import httpx
import pytest

from miniconstruct import execution as ex, execution_send as send
from miniconstruct.execution_state import ManualEditAdoptionWrite, RendererSeedSettings
from miniconstruct.models.takes import TakeImport, RendererJobState
from miniconstruct.project_store import ProjectStore, ProjectStoreError, _PROJECT_TRANSACTION_LOCK
from miniconstruct.workflow_preparation import prepare_workflow
from test_execution_send import env, no_live, run, recorder
from test_reference_authority import picture
from test_takes import chunks
from test_validator import valid_ref_prompt
from test_workflow_preparation import select


@pytest.fixture
def media_env(env, workspace_factory):
    store, config, profile, connection, _, make = env
    project = store.create_project(workspace_factory(mode="Ref2VA", assets=[picture("red")[0]]))
    select(store, project, valid_ref_prompt())
    return store, config, profile, connection, project, lambda: make(project)


def expected_body(env):
    store, _, profile, _, project, make = env
    prepared = prepare_workflow(store, project.project_id, project.active_generation_id, profile)
    return make().model_copy(update={"expected_execution_fingerprint": prepared.execution_fingerprint})


def mutate(env, kind, previous=None):
    store, config, profile, connection, project, _ = env
    p, g = project.project_id, project.active_generation_id
    if kind == "status":
        store.update_render_job(p, g, previous.render_submission_id, RendererJobState(status="running"))
    elif kind == "submission":
        # A separate explicit Send records exact independent provenance.
        run(env, [])
    elif kind == "import":
        asyncio.run(store.import_take(p, g, chunks(b"unrelated output"), "output.mp4", TakeImport(sourceKind="external_unknown")))
    elif kind == "metadata":
        current = store.load_project(p)
        store.save_project(p, current.workspace.model_copy(update={"project_name": "Unrelated rename"}))
    elif kind == "prompt":
        store.adopt_manual_edit_execution_prompt(p, g, ManualEditAdoptionWrite(prompt=valid_ref_prompt().replace("Rain", "Heavy rain")))
    elif kind == "profile":
        config.save("profiles", ex.ProfileUpdate(name="Changed Profile", compatibleModes=profile.compatible_modes,
            bindings=profile.bindings), profile.id)
    elif kind == "connection":
        config.save("connections", ex.ConnectionWrite(name=connection.name, baseUrl="http://changed.test"), connection.id)
    elif kind == "seed":
        store.set_renderer_seed_settings(p, g, RendererSeedSettings(mode="random"))
    elif kind == "reference":
        new = picture("blue")[0]
        current = store.load_project(p)
        store.sync_reference_assets(p, g, [new.model_dump(mode="json", by_alias=True)],
            current.workspace.subjects, current.project_fingerprint)
    elif kind in {"canonical_hash", "bytes"}:
        directory = store.root / f"project-{p}"
        manifest = store._read_manifest(directory)
        entry = store._generations(manifest)[0][0].asset_files[0]
        if kind == "bytes":
            path = directory / entry.path
            path.write_bytes(b"x" * path.stat().st_size)
        else:
            entry.path = entry.path.replace(entry.path.split("/")[-1].split(".")[0], "0" * 64)
            # Publish valid canonical descriptors pointing at different/missing bytes.
            store._atomic_write(directory / "project.json", manifest.model_dump_json(by_alias=True).encode())


@pytest.mark.parametrize("phase", [1, 2], ids=["before-upload", "after-upload"])
@pytest.mark.parametrize("kind", ["status", "submission", "import", "metadata"])
def test_irrelevant_mutations_during_real_payload_hash_succeed(media_env, monkeypatch, phase, kind):
    previous = run(media_env, [])
    body = expected_body(media_env)
    original = hashlib.file_digest
    entered, release = Event(), Event()
    passes = 0
    def digest(*args, **kwargs):
        nonlocal passes
        assert not _PROJECT_TRANSACTION_LOCK._is_owned()
        passes += 1
        if passes == phase:
            entered.set()
            assert release.wait(10), "verification was not released"
        return original(*args, **kwargs)
    monkeypatch.setattr(hashlib, "file_digest", digest)
    calls = []
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(run, media_env, calls, body)
        try:
            assert entered.wait(10), "Send did not reach verification"
            mutate(media_env, kind, previous)
        finally:
            release.set()
        result = pending.result(10)
    assert result.submitted
    assert sum(request.url.path.endswith("/prompt") for request in calls) == 1
    store, _, _, _, project, _ = media_env
    submission = store.render_submission(project.project_id, project.active_generation_id, result.render_submission_id)
    assert submission.execution_fingerprint == body.expected_execution_fingerprint


@pytest.mark.parametrize("kind", ["prompt", "profile", "connection", "seed", "reference", "canonical_hash", "bytes"])
def test_relevant_change_during_verification_never_queues(media_env, monkeypatch, kind):
    body = expected_body(media_env)
    original = hashlib.file_digest
    entered, release = Event(), Event()
    def digest(*args, **kwargs):
        if not entered.is_set():
            entered.set()
            assert release.wait(10)
        return original(*args, **kwargs)
    monkeypatch.setattr(hashlib, "file_digest", digest)
    calls = []
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(run, media_env, calls, body)
        try:
            assert entered.wait(10)
            mutate(media_env, kind)
        finally:
            release.set()
        with pytest.raises((ex.InvalidBindings, ProjectStoreError), match="changed|Changed|renderer_seed|authority|unavailable"):
            pending.result(10)
    assert all(request.url.path.endswith("/upload/image") for request in calls)


@pytest.mark.parametrize("kind", ["status", "submission", "import", "metadata"])
def test_prepare_hydration_revalidation_ignores_renderer_history(media_env, monkeypatch, kind):
    previous = run(media_env, [])
    store, _, profile, _, project, _ = media_env
    original = store._hydrate_workspace
    entered, release = Event(), Event()
    def hydrate(*args, **kwargs):
        result = original(*args, **kwargs)
        if not entered.is_set():
            assert not _PROJECT_TRANSACTION_LOCK._is_owned()
            entered.set()
            assert release.wait(10)
        return result
    monkeypatch.setattr(store, "_hydrate_workspace", hydrate)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(prepare_workflow, store, project.project_id, project.active_generation_id, profile)
        try:
            assert entered.wait(10)
            mutate(media_env, kind, previous)
        finally:
            release.set()
        prepared = pending.result(10)
    result = run(media_env, [], media_env[-1]().model_copy(update={"expected_execution_fingerprint": prepared.execution_fingerprint}))
    assert result.submitted


def test_three_immediate_prepare_send_cycles_keep_distinct_provenance(media_env):
    results, calls = [], []
    for _ in range(3):
        results.append(run(media_env, calls, expected_body(media_env)))
    assert len({r.prompt_id for r in results}) == len({r.render_submission_id for r in results}) == 3
    assert sum(r.url.path.endswith("/prompt") for r in calls) == 3


def test_effective_controls_changed_during_upload_never_queue(media_env):
    from test_workflow_controls import control
    store, config, profile, connection, project, make = media_env
    profile = config.save("profiles", ex.ProfileUpdate(name=profile.name, compatibleModes=profile.compatible_modes,
        bindings=profile.bindings, exposedControls=[control("sigma", "h3", "sigma", 1.5, "float")]), profile.id)
    body = expected_body((store, config, profile, connection, project, make))
    calls = []
    def change(request, _):
        if request.url.path.endswith("/upload/image"):
            assert not _PROJECT_TRANSACTION_LOCK._is_owned()
            store.set_workflow_control_values(project.project_id, project.active_generation_id, str(profile.id), "generation", {"sigma": 2.0})
    with pytest.raises(ex.InvalidBindings, match="Execution changed"):
        run(media_env, calls, body, change)
    assert all(r.url.path.endswith("/upload/image") for r in calls)


def test_random_seed_is_allocated_once_after_authority_validation(media_env, monkeypatch):
    store, config, profile, connection, project, make = media_env
    graph = profile.workflow.copy()
    graph["h3"] = {**graph["h3"], "inputs": {**graph["h3"]["inputs"], "seed": 10}}
    profile = config.save("profiles", ex.ProfileCreate(name="Seed", compatibleModes=profile.compatible_modes,
        workflowJson=json.dumps(graph), bindings=[*profile.bindings,
            {"source": {"kind": "renderer_seed"}, "nodeId": "h3", "inputName": "seed"}]))
    store.set_renderer_seed_settings(project.project_id, project.active_generation_id, RendererSeedSettings(mode="random"))
    body = expected_body((store, config, profile, connection, project, lambda: make().model_copy(update={"profile_id": profile.id})))
    seeds = iter([31, 42, 53])
    monkeypatch.setattr(send.secrets, "randbelow", lambda _: next(seeds))
    results = [run(media_env, [], body) for _ in range(3)]
    assert [result.renderer_seed for result in results] == [31, 42, 53]


@pytest.mark.parametrize("different_generation", [False, True])
def test_two_intentional_concurrent_sends_keep_distinct_receipts(media_env, monkeypatch, different_generation):
    body = expected_body(media_env)
    store = media_env[0]
    bodies = [body, body]
    if different_generation:
        project = store.create_generation(media_env[4].project_id, workspace=media_env[4].workspace)
        select(store, project, valid_ref_prompt())
        prepared = prepare_workflow(store, project.project_id, project.active_generation_id, media_env[2])
        bodies[0] = expected_body(media_env)
        bodies[1] = body.model_copy(update={"generation_id": UUID(project.active_generation_id),
                                           "expected_execution_fingerprint": prepared.execution_fingerprint})
    original = store.open_execution_payloads
    rendezvous, guard = Barrier(2), Lock()
    entered = 0
    @contextmanager
    def opened(*args, **kwargs):
        nonlocal entered
        with original(*args, **kwargs) as values:
            with guard:
                entered += 1
                first_pass = entered <= 2
            if first_pass:
                rendezvous.wait(10)
            yield values
    monkeypatch.setattr(store, "open_execution_payloads", opened)
    calls = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run, media_env, calls, current) for current in bodies]
        results = [f.result(10) for f in futures]
    assert len({result.prompt_id for result in results}) == 2
    assert len({result.render_submission_id for result in results}) == 2
    assert sum(request.url.path.endswith("/prompt") for request in calls) == 2


def test_deferred_upload_allows_unrelated_import_and_sends_one_prompt(media_env):
    body = expected_body(media_env)
    entered, release = Event(), Event()
    calls = []
    def upload(request, _):
        if request.url.path.endswith("/upload/image"):
            assert not _PROJECT_TRANSACTION_LOCK._is_owned()
            entered.set()
            assert release.wait(10)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(run, media_env, calls, body, upload)
        try:
            assert entered.wait(10)
            mutate(media_env, "import")
            assert not any(request.url.path.endswith("/prompt") for request in calls)
        finally:
            release.set()
        assert pending.result(10).submitted
    assert sum(request.url.path.endswith("/prompt") for request in calls) == 1


def test_final_authority_check_and_queue_share_the_canonical_locks(media_env, monkeypatch):
    from miniconstruct.execution import _LOCK as configuration_lock
    original = send.prepare_workflow
    def compile(*args, **kwargs):
        if kwargs.get("revalidate"):
            assert _PROJECT_TRANSACTION_LOCK._is_owned()
            assert configuration_lock._is_owned()
        return original(*args, **kwargs)
    monkeypatch.setattr(send, "prepare_workflow", compile)
    def queue(request, _):
        if request.url.path.endswith("/prompt"):
            assert _PROJECT_TRANSACTION_LOCK._is_owned()
            assert configuration_lock._is_owned()
    assert run(media_env, [], expected_body(media_env), queue).submitted


@pytest.mark.parametrize("remote", ["available", "missing", "changed"])
def test_unavailable_remote_verification_falls_back_to_upload(media_env, remote):
    # This legacy transport returns 404 for /view. A receipt alone never
    # authorizes reuse, regardless of the simulated server's storage state.
    calls, remote_files = [], {}
    def upload(request, _):
        if request.url.path.endswith("/upload/image"):
            assert b"mc-" in request.content and b"overwrite" in request.content
            remote_files["payload"] = request.content
            return httpx.Response(200, json={"name": "exact.png", "subfolder": "verified", "type": "input"})
    first = run(media_env, calls, expected_body(media_env), upload)
    if remote == "missing": remote_files.clear()
    elif remote == "changed": remote_files["payload"] = b"foreign remote bytes"
    second = run(media_env, calls, expected_body(media_env), upload)
    assert first.submitted and second.submitted
    assert remote_files["payload"] != b"foreign remote bytes"
    assert sum(r.url.path.endswith("/upload/image") for r in calls) == 2


def test_opt_in_timing_is_bounded_and_excludes_private_inputs(media_env, monkeypatch, caplog):
    monkeypatch.setenv("MINICONSTRUCT_SEND_TIMING", "1")
    with caplog.at_level("INFO", logger="uvicorn.error"):
        result = run(media_env, [])
    assert result.submitted
    record = json.loads(caplog.records[-1].message.removeprefix("Send timing "))
    assert record["counts"]["promptRequests"] == record["counts"]["accepted"] == 1
    assert record["counts"]["mediaHashPasses"] == 2
    assert {"reconstruct", "verifyBeforeUpload", "upload", "verifyAfterUpload", "revalidate", "queueAcceptance", "persist"} <= record["stagesMs"].keys()
    assert valid_ref_prompt() not in caplog.text
    assert str(media_env[4].project_id) not in caplog.text
    assert str(media_env[0].root) not in caplog.text
