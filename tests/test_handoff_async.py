"""Controlled-gate F6 regressions; no elapsed-time performance assertions."""
import asyncio
from functools import partial
import json
import threading

import httpx
import pytest

from fastapi import FastAPI

# Exercise the actual API endpoints with a deterministic ASGI receive channel.
app = FastAPI()
from miniconstruct.api import routes
app.include_router(routes.router)
from miniconstruct.generation import resolve_narrative_request, resolve_narrative_request_async
from miniconstruct.h3.builder import assemble_prompt
from miniconstruct.handoff_async import materialize_off_loop, _materializations, _admission_limiter
from miniconstruct.handoff_media import WORKER_CAPACITY
from miniconstruct.models.api import GenerationRequest
from test_visual_handoff import env, offline, change
from test_streaming_route import FakeClient, decode_event


def request_for(env):
    _, _, project, *_ = env
    return GenerationRequest(workspace=project.workspace, filesystemProjectId=project.project_id,
        generationId=project.active_generation_id,
        llm={"baseUrl": "http://never.test/v1", "model": "offline"})


class Gate:
    def __init__(self):
        self.loop = asyncio.get_running_loop()
        self.entered = asyncio.Event()
        self.release = threading.Event()
        self.timed_out = False

    def wrap(self, operation):
        def gated(*args, **kwargs):
            self.loop.call_soon_threadsafe(self.entered.set)
            if not self.release.wait(10):
                self.timed_out = True
                raise ValueError("Deadlock guard released extraction")
            return operation(*args, **kwargs)
        return gated


class StreamConnection:
    def __init__(self, request):
        self.body = request.model_dump_json(by_alias=True).encode()
        self.incoming = asyncio.Queue()
        self.messages = []
        self.scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.0"},
            "method": "POST", "scheme": "http", "path": "/api/generate-stream",
            "raw_path": b"/api/generate-stream", "query_string": b"",
            "root_path": "", "headers": [(b"content-type", b"application/json")],
            "client": ("test", 1), "server": ("test", 80)}

    async def receive(self):
        if self.body is not None:
            body, self.body = self.body, None
            return {"type": "http.request", "body": body, "more_body": False}
        return await self.incoming.get()

    async def send(self, message):
        self.messages.append(message)

    def disconnect(self):
        self.incoming.put_nowait({"type": "http.disconnect"})

    async def run(self):
        await app(self.scope, self.receive, self.send)


async def drain_workers():
    if _materializations:
        await asyncio.wait_for(asyncio.gather(*list(_materializations), return_exceptions=True), 10)
        await asyncio.sleep(0)  # Flush callbacks for already-cancelled pool tasks.


@pytest.mark.parametrize("tail", [False, True])
async def test_real_stream_route_remains_responsive_and_preserves_result(env, monkeypatch, tail):
    import miniconstruct.visual_handoff as handoffs
    import miniconstruct.observation_store as observation
    store, _, project, *_ = env
    if tail:
        change(env, "continue_previous_shot")
        module, name = handoffs, "extract_tail"
    else:
        module, name = observation, "extract_evidence"
    monkeypatch.setattr(routes, "ProjectStore", lambda: store)
    monkeypatch.setattr("miniconstruct.project_store.default_projects_root", lambda: store.root)
    gate = Gate()
    monkeypatch.setattr(module, name, gate.wrap(getattr(module, name)))
    provider_calls = []
    original_stream = routes.stream_generation_events
    def factory(settings):
        assert gate.release.is_set()
        provider_calls.append(settings)
        return FakeClient(settings)
    async def stream(request):
        async for event in original_stream(request, factory):
            yield event
    monkeypatch.setattr(routes, "stream_generation_events", stream)
    connection = StreamConnection(request_for(env))
    task = asyncio.create_task(connection.run())
    try:
        await asyncio.wait_for(gate.entered.wait(), 10)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await asyncio.wait_for(client.get("/api/health"), 10)
        assert response.status_code == 200
        assert not gate.release.is_set() and not gate.timed_out
        assert not provider_calls and not task.done()
        # A Project operation can acquire the lock while extraction is gated.
        assert store.read_generation(project.project_id, project.active_generation_id)
    finally:
        gate.release.set()
        await asyncio.wait_for(task, 10)
        await drain_workers()
    assert provider_calls
    body = b"".join(m.get("body", b"") for m in connection.messages).decode()
    events = [decode_event(raw) for raw in body.split("\n\n") if raw.strip()]
    completed = next(data for kind, data in events if kind == "complete")
    sync = resolve_narrative_request(request_for(env), store)
    assert completed["sourceFingerprint"] == sync.source_fingerprint
    assert completed["referenceMap"] == [r.model_dump(by_alias=True) for r in sync.reference_map]
    assert any(kind == "done" for kind, _ in events)
    assert sync._visual_handoff.path.is_file()


async def test_disconnect_during_extraction_cancels_route_before_worker_finishes(env, monkeypatch):
    import miniconstruct.visual_handoff as handoffs
    store, _, project, *_ = env
    change(env, "continue_previous_shot")
    monkeypatch.setattr("miniconstruct.project_store.default_projects_root", lambda: store.root)
    gate = Gate()
    monkeypatch.setattr(handoffs, "extract_tail", gate.wrap(handoffs.extract_tail))
    provider_calls = []
    async def forbidden_stream(request):
        provider_calls.append(request)
        yield "unexpected"
    monkeypatch.setattr(routes, "stream_generation_events", forbidden_stream)
    directory = store._project_directory(project.project_id, must_exist=True)
    before = store._read_manifest(directory)
    connection = StreamConnection(request_for(env))
    task = asyncio.create_task(connection.run())
    try:
        await asyncio.wait_for(gate.entered.wait(), 10)
        connection.disconnect()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 10)
        assert not gate.release.is_set() and not gate.timed_out
        assert not provider_calls and not connection.messages
        assert store._read_manifest(directory) == before
    finally:
        gate.release.set()
        await drain_workers()
    assert not provider_calls and not connection.messages
    assert store._read_manifest(directory) == before
    # A still-owned disposable artifact may finish/cache after HTTP cancellation.
    def no_extract(*args):
        raise AssertionError("valid cancelled-owner cache should be reusable")
    monkeypatch.setattr(handoffs, "extract_tail", no_extract)
    assert (await resolve_narrative_request_async(request_for(env), store))._visual_handoff.path.is_file()


@pytest.mark.parametrize("tail", [False, True])
async def test_cache_hit_reuses_extraction_and_async_h3_matches_sync(env, monkeypatch, tail):
    import miniconstruct.visual_handoff as handoffs
    import miniconstruct.observation_store as observation
    store, *_ = env
    if tail:
        change(env, "continue_previous_shot")
    raw = request_for(env)
    expected = resolve_narrative_request(raw, store)
    def forbidden(*args):
        raise AssertionError("cache hit extracted again")
    monkeypatch.setattr(handoffs, "extract_tail", forbidden)
    monkeypatch.setattr(observation, "extract_evidence", forbidden)
    actual = await resolve_narrative_request_async(raw, store)
    assert actual._visual_handoff == expected._visual_handoff
    assert actual.source_fingerprint == expected.source_fingerprint
    def assembly(request):
        return assemble_prompt(request.prompt_workspace, True, request.narrative_subjects,
                               request.continuity_context, request.story_context)
    assert assembly(actual) == assembly(expected)


async def test_async_boundary_preserves_shared_two_decoder_limit(tmp_path, monkeypatch):
    import miniconstruct.handoff_media as media
    gate = Gate()
    two_running, three_attempts = asyncio.Event(), asyncio.Event()
    active = peak = attempts = 0
    lock = threading.Lock()
    original_workers = media.WORKERS
    class Workers:
        def acquire(self, **kwargs):
            nonlocal attempts
            with lock:
                attempts += 1
                if attempts == 3:
                    gate.loop.call_soon_threadsafe(three_attempts.set)
            return original_workers.acquire(**kwargs)
        def release(self):
            original_workers.release()
    def subprocess_run(command, **kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            if active == 2:
                gate.loop.call_soon_threadsafe(two_running.set)
        try:
            assert gate.release.wait(10)
            destination = command[-2]
            from pathlib import Path
            Path(destination).write_bytes(b"bounded")
            Path(destination + ".json").write_text(json.dumps({"effectiveTailSeconds": 1}))
        finally:
            with lock:
                active -= 1
    monkeypatch.setattr(media, "WORKERS", Workers())
    monkeypatch.setattr(media.subprocess, "run", subprocess_run)
    from starlette.concurrency import run_in_threadpool
    # Two admitted async callers plus a synchronous caller share decoder authority.
    tasks = [asyncio.create_task(materialize_off_loop(partial(media.extract_tail,
        tmp_path / "source", tmp_path / f"{i}.mp4", 1))) for i in range(WORKER_CAPACITY)]
    tasks.append(asyncio.create_task(run_in_threadpool(media.extract_tail,
        tmp_path / "source", tmp_path / "sync.mp4", 1)))
    try:
        await asyncio.wait_for(two_running.wait(), 10)
        await asyncio.wait_for(three_attempts.wait(), 10)
        assert active == peak == 2
    finally:
        gate.release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), 10)
    assert peak == 2


def watch_admission(monkeypatch, queued):
    limiter = _admission_limiter()
    original_acquire = type(limiter).acquire
    async def acquire(self):
        if self is limiter and self.borrowed_tokens == self.total_tokens:
            queued.set()
        await original_acquire(self)
    monkeypatch.setattr(type(limiter), "acquire", acquire)
    return limiter


async def test_cancellation_keeps_admission_until_started_worker_finishes(monkeypatch):
    gates = [Gate() for _ in range(WORKER_CAPACITY)]
    queued, next_gate = asyncio.Event(), Gate()
    limiter = watch_admission(monkeypatch, queued)
    tasks = [asyncio.create_task(materialize_off_loop(g.wrap(lambda: None))) for g in gates]
    replacement = None
    try:
        await asyncio.wait_for(asyncio.gather(*(g.entered.wait() for g in gates)), 10)
        tasks[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[0]
        replacement = asyncio.create_task(materialize_off_loop(next_gate.wrap(lambda: None)))
        await asyncio.wait_for(queued.wait(), 10)
        assert limiter.statistics().tasks_waiting == 1
        assert limiter.borrowed_tokens == WORKER_CAPACITY
        assert not next_gate.entered.is_set()
        gates[0].release.set()
        await asyncio.wait_for(next_gate.entered.wait(), 10)
        assert not gates[1].release.is_set()
    finally:
        for gate in [*gates, next_gate]:
            gate.release.set()
        await asyncio.wait_for(asyncio.gather(*tasks, *([replacement] if replacement else []),
                                            return_exceptions=True), 10)
        await drain_workers()
    assert limiter.borrowed_tokens == 0 and not _materializations


async def test_excess_handoffs_leave_shared_threadpool_responsive(monkeypatch):
    from anyio import to_thread
    from starlette.concurrency import run_in_threadpool
    from miniconstruct.handoff_media import WORKERS
    default = to_thread.current_default_thread_limiter()
    old_limit = default.total_tokens
    default.total_tokens = WORKER_CAPACITY + 1
    gate = Gate()
    two_running, extra_thread, queued = asyncio.Event(), asyncio.Event(), asyncio.Event()
    watch_admission(monkeypatch, queued)
    active = started = 0
    lock = threading.Lock()
    def operation():
        nonlocal active, started
        with lock:
            started += 1
            if started > WORKER_CAPACITY:
                gate.loop.call_soon_threadsafe(extra_thread.set)
        assert WORKERS.acquire(timeout=10)
        try:
            with lock:
                active += 1
                if active == WORKER_CAPACITY:
                    gate.loop.call_soon_threadsafe(two_running.set)
            assert gate.release.wait(10)
        finally:
            with lock:
                active -= 1
            WORKERS.release()
    tasks = [asyncio.create_task(materialize_off_loop(operation)) for _ in range(WORKER_CAPACITY + 1)]
    witnesses = [asyncio.create_task(event.wait()) for event in (queued, extra_thread)]
    try:
        await asyncio.wait_for(two_running.wait(), 10)
        # Revised code queues in admission; legacy code starts an extra thread
        # waiting for WORKERS. Either state is observed before probing the pool.
        await asyncio.wait_for(asyncio.wait(witnesses, return_when=asyncio.FIRST_COMPLETED), 10)
        assert await asyncio.wait_for(run_in_threadpool(lambda: "responsive"), 5) == "responsive"
        assert not gate.release.is_set()
        assert active == WORKER_CAPACITY
    finally:
        gate.release.set()
        for witness in witnesses:
            witness.cancel()
        await asyncio.gather(*witnesses, return_exceptions=True)
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 10)
        await drain_workers()
        default.total_tokens = old_limit


async def test_cancelled_admission_waiter_never_starts_later(monkeypatch):
    gate, queued = Gate(), asyncio.Event()
    limiter = watch_admission(monkeypatch, queued)
    tasks = [asyncio.create_task(materialize_off_loop(gate.wrap(lambda: None)))
             for _ in range(WORKER_CAPACITY)]
    started = threading.Event()
    waiter = None
    try:
        # Admission includes all submitted workers, even before thread entry.
        await asyncio.wait_for(gate.entered.wait(), 10)
        waiter = asyncio.create_task(materialize_off_loop(started.set))
        await asyncio.wait_for(queued.wait(), 10)
        assert limiter.statistics().tasks_waiting == 1
        assert not started.is_set()
        assert len(_materializations) == WORKER_CAPACITY
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert limiter.statistics().tasks_waiting == 0
    finally:
        gate.release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), 10)
        await drain_workers()
    assert not started.is_set()
    assert limiter.borrowed_tokens == 0 and not _materializations


async def test_cancelled_admitted_owner_waiting_for_default_pool_never_runs(monkeypatch):
    from anyio import to_thread
    from starlette.concurrency import run_in_threadpool
    default = to_thread.current_default_thread_limiter()
    old_limit = default.total_tokens
    default.total_tokens = 1
    gate = Gate()
    occupant = asyncio.create_task(run_in_threadpool(gate.wrap(lambda: None)))
    pool_wait, started = asyncio.Event(), threading.Event()
    original_run = to_thread.run_sync
    async def run_sync(*args, **kwargs):
        pool_wait.set()
        return await original_run(*args, **kwargs)
    owner = None
    try:
        await asyncio.wait_for(gate.entered.wait(), 10)
        monkeypatch.setattr(to_thread, "run_sync", run_sync)
        owner = asyncio.create_task(materialize_off_loop(started.set))
        await asyncio.wait_for(pool_wait.wait(), 10)
        assert _admission_limiter().borrowed_tokens == 1
        assert not started.is_set()
        owner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await owner
        await drain_workers()
        assert _admission_limiter().borrowed_tokens == 0
    finally:
        gate.release.set()
        await asyncio.wait_for(occupant, 10)
        await drain_workers()
        default.total_tokens = old_limit
    assert not started.is_set() and not _materializations


async def test_detached_worker_failure_is_retrieved_and_logged(caplog):
    gate = Gate()
    def failure():
        raise ValueError("controlled detached failure")
    owner = asyncio.create_task(materialize_off_loop(gate.wrap(failure)))
    try:
        await asyncio.wait_for(gate.entered.wait(), 10)
        owner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await owner
    finally:
        gate.release.set()
        await drain_workers()
    assert "materialization failed after request cancellation" in caplog.text
    assert _admission_limiter().borrowed_tokens == 0 and not _materializations


async def test_async_stale_strategy_keeps_existing_domain_error_translation(env, monkeypatch):
    import miniconstruct.visual_handoff as handoffs
    store, *_ = env
    change(env, "continue_previous_shot", strategy="tail_reference_continuation")
    monkeypatch.setattr("miniconstruct.project_store.default_projects_root", lambda: store.root)
    gate = Gate()
    monkeypatch.setattr(handoffs, "extract_tail", gate.wrap(handoffs.extract_tail))
    connection = StreamConnection(request_for(env))
    task = asyncio.create_task(connection.run())
    try:
        await asyncio.wait_for(gate.entered.wait(), 10)
        change(env, strategy="none")
    finally:
        gate.release.set()
        await asyncio.wait_for(task, 10)
        await drain_workers()
    status = next(m["status"] for m in connection.messages if m["type"] == "http.response.start")
    assert status == 400
    assert not any(b"event: complete" in m.get("body", b"") for m in connection.messages)


async def test_semantic_no_handoff_path_avoids_pool(env, monkeypatch):
    import miniconstruct.handoff_async as offload
    store, *_ = env
    change(env, strategy="none")
    async def forbidden(*args):
        raise AssertionError("semantic-only handoff should stay on the fast path")
    monkeypatch.setattr(offload, "materialize_off_loop", forbidden)
    actual = await resolve_narrative_request_async(request_for(env), store)
    assert actual._visual_handoff.asset is None


async def test_deleted_take_cannot_publish_cache_after_disconnect(env, monkeypatch):
    import miniconstruct.visual_handoff as handoffs
    store, first, project, take, *_ = env
    change(env, "continue_previous_shot")
    monkeypatch.setattr("miniconstruct.project_store.default_projects_root", lambda: store.root)
    gate = Gate()
    monkeypatch.setattr(handoffs, "extract_tail", gate.wrap(handoffs.extract_tail))
    connection = StreamConnection(request_for(env))
    task = asyncio.create_task(connection.run())
    directory = store._project_directory(project.project_id, must_exist=True)
    try:
        await asyncio.wait_for(gate.entered.wait(), 10)
        connection.disconnect()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 10)
        store.delete_take(first.project_id, first.active_generation_id, take)
        after_deletion = store._read_manifest(directory)
    finally:
        gate.release.set()
        await drain_workers()
    assert store._read_manifest(directory) == after_deletion
    assert not (directory / "outputs" / "handoffs" / take).exists()
    assert not connection.messages
