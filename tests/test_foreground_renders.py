"""Provider-free, read-only Project foreground discovery and exact identity."""
import asyncio
import json
from uuid import UUID, uuid4

import httpx
import pytest

from fastapi.testclient import TestClient

from miniconstruct.app import app
from miniconstruct.models.takes import RendererJobState
from miniconstruct.render_reconciliation import discover_foreground
from test_render_reconciliation import env, offline  # noqa: F401 - shared fixtures
from test_native_continuation import env as native_env, choose, send


def add(store, project, generation, template, status="queued", created="2026-10-04T00:00:00Z", identity=None):
    submission = template.model_copy(update={"id": identity or uuid4(), "comfy_prompt_id": uuid4(),
        "created_at": created, "job": RendererJobState(status=status)})
    store.record_render_submission(project, generation, submission)
    return submission


def test_foreground_summary_is_bounded_minimal_read_only_and_project_scoped(env):
    store, _, project, first = env
    second_generation = store.create_generation(project.project_id).active_generation_id
    third_generation = store.create_generation(project.project_id).active_generation_id
    second = add(store, project.project_id, second_generation, first)
    add(store, project.project_id, third_generation, first)
    store.update_render_job(project.project_id, project.active_generation_id, first.id, RendererJobState(status="running"))
    path = next(store.root.glob("*/project.json"))
    before = path.read_bytes()
    with TestClient(app) as client:
        result = client.get(f"/api/projects/{project.project_id}/foreground-renders", params={
            "generationId": second_generation, "submissionId": str(second.id)})
    assert result.status_code == 200
    summary = result.json()
    assert set(summary) == {"candidate", "watched"}
    assert summary["candidate"]["id"] == str(first.id)
    assert summary["candidate"]["generationId"] == project.active_generation_id
    assert summary["candidate"]["generationNumber"] == 1
    assert summary["watched"]["id"] == str(second.id)
    assert summary["watched"]["generationNumber"] == 2
    for row in summary.values():
        assert set(row) == {"generationId", "generationNumber", "id", "sendNumber", "comfyPromptId", "createdAt", "job"}
        assert set(row["job"]) == {"status"}
    assert path.read_bytes() == before
    manifest = json.loads(before)
    assert manifest["schemaVersion"] == 17
    assert manifest["sequence"]["activeGenerationId"] == third_generation
    assert all(g["selectedTakeId"] is None for g in manifest["sequence"]["generations"])


def test_terminal_g1_g2_g3_transfer_uses_sequence_order_and_returns_exact_watched_terminal(env):
    store, _, project, first = env
    p, g1 = project.project_id, project.active_generation_id
    g2 = store.create_generation(p).active_generation_id
    g3 = store.create_generation(p).active_generation_id
    second = add(store, p, g2, first, created="2026-10-04T02:00:00Z")
    third = add(store, p, g3, first, created="2026-10-04T03:00:00Z")
    store.update_render_job(p, g1, first.id, RendererJobState(status="running"))
    assert store.foreground_renders(p)["candidate"]["id"] == str(first.id)
    store.update_render_job(p, g1, first.id, RendererJobState(status="completed"))
    summary = store.foreground_renders(p, g1, first.id)
    assert summary["watched"]["job"]["status"] == "completed"
    assert summary["candidate"]["id"] == str(second.id)  # G2 before newer G3
    store.update_render_job(p, g2, second.id, RendererJobState(status="cancelled"))
    assert store.foreground_renders(p, g2, second.id)["candidate"]["id"] == str(third.id)
    store.update_render_job(p, g3, third.id, RendererJobState(status="failed"))
    assert store.foreground_renders(p) == {"candidate": None, "watched": None}


def test_status_then_sequence_then_newest_and_stable_id_with_complete_send_labels(env):
    store, _, project, first = env
    p, g1 = project.project_id, project.active_generation_id
    g2 = store.create_generation(p).active_generation_id
    store.update_render_job(p, g1, first.id, RendererJobState(status="unknown"))
    running = add(store, p, g2, first, status="running")
    assert store.foreground_renders(p)["candidate"]["id"] == str(running.id)
    a = add(store, p, g1, first, status="running", identity=UUID(int=1), created="2026-10-04T01:00:00Z")
    b = add(store, p, g1, first, status="running", identity=UUID(int=2), created=a.created_at)
    # Terminal history still contributes to stable human send numbering.
    add(store, p, g1, first, status="completed", created="2026-10-03T01:00:00Z")
    candidate = store.foreground_renders(p)["candidate"]
    assert candidate["id"] == str(b.id)
    history = store.generation_outputs(p, g1)["renderSubmissions"]
    chronological = sorted(history, key=lambda row: (row["createdAt"], row["id"]))
    assert candidate["sendNumber"] == next(i for i, row in enumerate(chronological, 1) if row["id"] == str(b.id))
    assert candidate["sendNumber"] > 1
    store.reorder_generations(p, [g2, g1])
    assert store.foreground_renders(p)["candidate"]["id"] == str(running.id)


def test_foreground_route_validates_exact_watch_pair_and_cannot_resolve_another_project(env):
    store, _, project, submission = env
    p, g = project.project_id, project.active_generation_id
    with TestClient(app) as client:
        path = f"/api/projects/{p}/foreground-renders"
        assert client.get(path, params={"generationId": g}).status_code == 422
        assert client.get(path, params={"submissionId": str(submission.id)}).status_code == 422
        assert client.get(path, params={"generationId": "invalid", "submissionId": str(submission.id)}).status_code == 422
        assert client.get(f"/api/projects/{uuid4()}/foreground-renders").status_code == 404
        assert client.post(path).status_code == 405
        foreign = client.get(path, params={"generationId": str(uuid4()), "submissionId": str(submission.id)})
        assert foreign.status_code == 200 and foreign.json()["watched"] is None
        missing = client.get(path, params={"generationId": g, "submissionId": str(uuid4())})
        assert missing.status_code == 200 and missing.json()["watched"] is None
        exact = client.get(path, params={"generationId": g, "submissionId": str(submission.id)})
        assert exact.json()["watched"]["comfyPromptId"] == str(submission.comfy_prompt_id)


async def test_bounded_discovery_finds_older_running_job_then_hands_off_across_generations(env):
    store, config, project, a = env
    p, g1 = project.project_id, project.active_generation_id
    g2 = store.create_generation(p).active_generation_id
    b = add(store, p, g2, a, created="2099-10-05T01:00:00Z")
    c = add(store, p, g1, a, created="2099-10-06T01:00:00Z")
    assert store.foreground_renders(p)["candidate"]["id"] == str(c.id), "persisted queued chronology initially hides A"
    statuses = {str(a.comfy_prompt_id): "in_progress", str(b.comfy_prompt_id): "pending", str(c.comfy_prompt_id): "pending"}
    requests = []
    def handle(request):
        requests.append(request)
        assert request.method == "GET"
        if request.url.path == "/comfy/queue":
            return httpx.Response(200, json={
                "queue_running": [[0, identity] for identity, status in statuses.items() if status == "in_progress"],
                "queue_pending": [[0, identity] for identity, status in statuses.items() if status == "pending"]})
        identity = request.url.path.rsplit("/", 1)[-1]
        assert identity in statuses, "only exact recorded prompts are queried"
        return httpx.Response(200, json={"id": identity, "status": statuses[identity], "outputs": {}})
    async def discover(g=None, s=None):
        requests.clear()
        result = await discover_foreground(p, g, s, store=store, config=config, transport=httpx.MockTransport(handle))
        assert len(requests) <= 4, "one Connection read plus at most three exact reads"
        assert all("/view" not in str(request.url) for request in requests)
        assert store.generation_outputs(p, g1)["selectedTakeId"] is None
        assert store.load_project(p).active_generation_id == g2
        return result
    first = await discover()
    assert first["candidate"]["id"] == str(a.id)
    assert first["candidate"]["job"]["status"] == "running"
    assert (await discover(g1, a.id))["candidate"]["id"] == str(a.id), "newer queued C cannot steal A"
    statuses[str(a.comfy_prompt_id)] = "completed"
    statuses[str(b.comfy_prompt_id)] = "in_progress"
    next_job = await discover(g1, a.id)
    assert next_job["watched"]["job"]["status"] == "completed"
    assert next_job["candidate"]["id"] == str(b.id)
    assert next_job["candidate"]["generationId"] == g2


async def test_discovery_outage_retains_active_truth_and_returns_safe_transient_error(env):
    store, config, project, submission = env
    p, g = project.project_id, project.active_generation_id
    store.update_render_job(p, g, submission.id, RendererJobState(status="running"))
    def unavailable(request):
        raise httpx.ConnectError("secret renderer address", request=request)
    result = await discover_foreground(p, g, submission.id, store=store, config=config,
                                       transport=httpx.MockTransport(unavailable))
    assert result["candidate"]["job"]["status"] == "running"
    assert "temporarily unavailable" in result["discoveryError"]
    assert "secret" not in str(result)
    assert store.generation_outputs(p, g)["takes"] == []


async def test_queue_discovery_batches_canonical_status_persistence(env, monkeypatch):
    store, config, project, first = env
    p, g = project.project_id, project.active_generation_id
    records = [first, *(add(store, p, g, first, status="unknown") for _ in range(20))]
    commits, original = [], store._commit_sequence
    def commit(*args, **kwargs):
        commits.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(store, "_commit_sequence", commit)
    def handle(request):
        assert request.method == "GET"
        if request.url.path == "/comfy/queue":
            return httpx.Response(200, json={"queue_running": [[0, str(first.comfy_prompt_id)]],
                "queue_pending": [[i, str(record.comfy_prompt_id)] for i, record in enumerate(records[1:], 1)]})
        pytest.fail("fresh positive queue identities need no exact confirmation")
    result = await discover_foreground(p, store=store, config=config, transport=httpx.MockTransport(handle))
    assert result["candidate"]["id"] == str(first.id)
    assert len(commits) == 1, "one queue snapshot commit; active queue identity needs no redundant exact confirmation"
    assert all(s["job"]["status"] in {"queued", "running"} for s in store.generation_outputs(p, g)["renderSubmissions"])


async def test_discovery_rotates_bounded_recorded_connections_without_starvation(env):
    from miniconstruct.execution import ConnectionWrite
    store, config, project, original = env
    p, g = project.project_id, project.active_generation_id
    for index in range(6):
        connection = config.save("connections", ConnectionWrite(name=f"Recorded {index}", baseUrl=f"http://renderer-{index}.test"))
        submission = original.model_copy(update={"id": uuid4(), "comfy_prompt_id": uuid4(), "connection_id": connection.id})
        store.record_render_submission(p, g, submission)
    seen, cursor = set(), 0
    def handle(request):
        assert request.method == "GET"
        if request.url.path.endswith("/queue"):
            seen.add(request.url.host)
            return httpx.Response(200, json={"queue_running": [], "queue_pending": []})
        return httpx.Response(200, json={"id": request.url.path.rsplit("/", 1)[-1], "status": "pending"})
    for _ in range(2):
        result = await discover_foreground(p, cursor=cursor, store=store, config=config, transport=httpx.MockTransport(handle))
        cursor = result["discoveryCursor"]
    assert seen == {"localhost", *(f"renderer-{i}.test" for i in range(6))}


def test_foreground_refresh_route_uses_only_recorded_ids_and_bounded_discovery(env, monkeypatch):
    from miniconstruct import render_reconciliation as rr
    store, _, project, submission = env
    calls = []
    async def fake(p, g, s, **kwargs):
        calls.append((p, g, s, kwargs))
        return store.foreground_renders(p, g, s)
    monkeypatch.setattr(rr, "discover_foreground", fake)
    with TestClient(app) as client:
        path = f"/api/projects/{project.project_id}/foreground-renders"
        assert client.get(path, params={"refresh": "true", "cursor": -1}).status_code == 422
        assert client.get(path, params={"refresh": "true", "generationId": project.active_generation_id,
                                     "submissionId": str(submission.id)}).status_code == 200
    assert len(calls) == 1 and calls[0][0] == project.project_id and calls[0][-1] == {"cursor": 0}


@pytest.mark.parametrize("terminal,expected", [("completed", "available"), ("failed", "unavailable"), ("cancelled", "unavailable")])
async def test_discovery_retains_exact_native_lifecycle_without_accepting_or_importing(native_env, terminal, expected):
    store, config, _, _, project = native_env
    choose(store, project)
    receipt, _ = send(native_env)
    def handle(request):
        assert request.method == "GET"
        if request.url.path == "/queue":
            return httpx.Response(200, json={"queue_running": [], "queue_pending": []})
        assert request.url.path == f"/api/jobs/{receipt.prompt_id}"
        return httpx.Response(200, json={"id": str(receipt.prompt_id), "status": terminal, "outputs": {}})
    await discover_foreground(project.project_id, store=store, config=config, transport=httpx.MockTransport(handle))
    state = store.continuation_status(project.project_id, project.active_generation_id)
    assert next(record for record in state["executions"] if record.id == receipt.continuation_execution_id).status == expected
    assert state["acceptedId"] is None
    assert store.generation_outputs(project.project_id, project.active_generation_id)["takes"] == []


async def test_running_queue_identity_returns_while_unrelated_history_is_stalled_and_import_still_finishes(env):
    from miniconstruct.render_reconciliation import reconcile_submission
    store, config, project, a = env
    p, g = project.project_id, project.active_generation_id
    queued = [add(store, p, g, a, created=f"2099-10-0{i}T00:00:00Z") for i in range(2, 6)]
    b = queued[0]
    entered, release = asyncio.Event(), asyncio.Event()
    requests = []
    async def handle(request):
        requests.append((request.method, request.url.path))
        assert request.method == "GET"
        if request.url.path == f"/comfy/api/jobs/{b.comfy_prompt_id}":
            assert not entered.is_set(), "discovery cannot redundantly wait on queued B's exact history"
            entered.set(); await release.wait()
            return httpx.Response(200, json={"id": str(b.comfy_prompt_id), "status": "completed",
                "outputs": {"7": {"gifs": [{"filename": "completed-b.mp4", "subfolder": "", "type": "output"}]}}})
        if request.url.path == "/comfy/queue":
            return httpx.Response(200, json={"queue_running": [[0, str(a.comfy_prompt_id)]],
                "queue_pending": [[i, str(record.comfy_prompt_id)] for i, record in enumerate(queued, 1)]})
        if request.url.path == "/comfy/view":
            return httpx.Response(200, content=b"completed video", headers={"content-type": "video/mp4"})
        pytest.fail("queue-established running A must not wait for an exact-job lookup")
    transport = httpx.MockTransport(handle)
    history = asyncio.create_task(reconcile_submission(p, g, b.id, store=store, config=config, transport=transport))
    try:
        await entered.wait()
        summary = await discover_foreground(p, g, b.id, store=store, config=config, transport=transport)
        assert not history.done() and not release.is_set()
        assert summary["candidate"]["id"] == str(a.id)
        assert summary["candidate"]["job"]["status"] == "running"
        assert summary["watched"]["id"] == str(b.id) and summary["watched"]["job"]["status"] == "queued"
        assert len(requests) == 2, "one pending history request and one bounded queue read"
        release.set()
        outputs = await history
        assert len(outputs["takes"]) == 1
        take = outputs["takes"][0]
        assert take["renderSubmissionId"] == str(b.id) and take["comfyPromptId"] == str(b.comfy_prompt_id)
        assert take["originalFilename"] == "completed-b.mp4"
        assert outputs["selectedTakeId"] is None and outputs["takeObservations"] == []
        assert store.load_project(p).active_generation_id == g
    finally:
        release.set(); await history


@pytest.mark.parametrize("status,expected", [("completed", "completed"), ("failed", "failed"),
                                           ("cancelled", "cancelled"), ("pending", "queued")])
async def test_absent_watched_job_still_requires_exact_truth_without_confirming_running_successor(env, status, expected):
    store, config, project, a = env
    p, g = project.project_id, project.active_generation_id
    b = add(store, p, g, a)
    requests = []
    def handle(request):
        assert request.method == "GET"
        requests.append(request.url.path)
        if request.url.path == "/comfy/queue":
            return httpx.Response(200, json={"queue_running": [[0, str(b.comfy_prompt_id)]], "queue_pending": []})
        assert request.url.path == f"/comfy/api/jobs/{a.comfy_prompt_id}", "B's established running identity must not trigger another confirmation"
        return httpx.Response(200, json={"id": str(a.comfy_prompt_id), "status": status, "outputs": {}})
    result = await discover_foreground(p, g, a.id, store=store, config=config, transport=httpx.MockTransport(handle))
    assert result["candidate"]["id"] == str(b.id) and result["candidate"]["job"]["status"] == "running"
    assert result["watched"]["job"]["status"] == expected
    assert len(requests) == 2
    assert store.generation_outputs(p, g)["takes"] == []


def test_unknown_history_is_not_automatic_foreground_but_exact_watch_and_discovery_remain(env):
    store, _, project, submission = env
    p, g = project.project_id, project.active_generation_id
    store.update_render_job(p, g, submission.id, RendererJobState(status="unknown"))
    before = store.generation_outputs(p, g)
    assert store.foreground_renders(p) == {"candidate": None, "watched": None}
    exact = store.foreground_renders(p, g, submission.id)
    assert exact["candidate"] is None
    assert exact["watched"]["id"] == str(submission.id)
    assert exact["watched"]["job"]["status"] == "unknown"
    assert len(store.active_render_submissions(p)) == 1
    assert store.generation_outputs(p, g) == before


async def test_unknown_background_discovery_can_confirm_running_or_completed_without_a_watcher(env):
    store, config, project, submission = env
    p, g = project.project_id, project.active_generation_id
    store.update_render_job(p, g, submission.id, RendererJobState(status="unknown"))
    requests = []
    status = "in_progress"
    def handle(request):
        requests.append(request)
        assert request.method == "GET"
        if request.url.path == "/comfy/queue":
            return httpx.Response(200, json={"queue_running": [], "queue_pending": []})
        assert str(submission.comfy_prompt_id) in request.url.path
        return httpx.Response(200, json={"id": str(submission.comfy_prompt_id), "status": status, "outputs": {}})
    result = await discover_foreground(p, store=store, config=config, transport=httpx.MockTransport(handle))
    assert result["candidate"]["id"] == str(submission.id)
    assert result["candidate"]["job"]["status"] == "running"
    status = "completed"
    result = await discover_foreground(p, store=store, config=config, transport=httpx.MockTransport(handle))
    assert result["candidate"] is None
    outputs = store.generation_outputs(p, g)
    assert outputs["renderSubmissions"][0]["job"]["status"] == "completed"
    assert outputs["selectedTakeId"] is None
    assert outputs["takes"] == []
