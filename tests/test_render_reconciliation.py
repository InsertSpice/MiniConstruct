"""Exact recorded renderer authority, fake transport only (including API tests)."""
import asyncio
import hashlib
import json
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from miniconstruct.app import app
from miniconstruct.execution import ExecutionStore, ConnectionWrite
from miniconstruct.models.takes import RenderSubmission, TakePromptSnapshot, RendererJobState, RendererOutput
from miniconstruct.native_continuation_models import ContinuationDecision
from miniconstruct.project_store import ProjectStore, ProjectStoreError
from miniconstruct import render_reconciliation as rr, take_store
from test_takes import select_prompt
from test_native_continuation import env as native_env, choose, send, confirm


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Real HTTP is forbidden")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)


@pytest.fixture
def env(tmp_path, monkeypatch, workspace_factory):
    monkeypatch.setenv("MINICONSTRUCT_EXECUTION_ROOT", str(tmp_path / "execution"))
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(tmp_path / "projects"))
    store, config = ProjectStore(), ExecutionStore()
    connection = config.save("connections", ConnectionWrite(name="Fake", baseUrl="http://localhost:8188/comfy"))
    project = store.create_project(workspace_factory())
    snapshot = TakePromptSnapshot.capture(select_prompt(store, project))
    submission = RenderSubmission(connectionId=connection.id, workflowProfileId=uuid4(),
        workflowFingerprint="a" * 64, profileFingerprint="b" * 64, comfyPromptId=uuid4(), promptSnapshot=snapshot)
    store.record_render_submission(project.project_id, project.active_generation_id, submission)
    return store, config, project, submission


def video(filename="exact.mp4", **extra):
    return {"filename": filename, "subfolder": "nested/folder", "type": "output", **extra}


def completed(env, outputs=None, **extra):
    return {"id": str(env[3].comfy_prompt_id), "status": "completed", "outputs": outputs if outputs is not None else {"7": {"gifs": [video()]}}, **extra}


async def reconcile(env, handler, **kwargs):
    store, config, p, submission = env
    return await rr.reconcile_submission(p.project_id, p.active_generation_id, submission.id,
        store=store, config=config, transport=httpx.MockTransport(handler), **kwargs)


def test_explicit_recover_route_uses_only_recorded_ids(env, monkeypatch):
    calls = []
    async def fake(project_id, generation_id, submission_id):
        calls.append((project_id, generation_id, submission_id))
        return env[0].generation_outputs(project_id, generation_id)
    monkeypatch.setattr(rr, "reconcile_submission", fake)
    _, _, project, submission = env
    path = (f"/api/projects/{project.project_id}/generations/{project.active_generation_id}"
            f"/render-submissions/{submission.id}/recover")
    with TestClient(app) as client:
        response = client.post(path)
        rejected = client.post(path, json={"comfyPromptId": str(uuid4())})
    assert response.status_code == 200 and response.json()["newJobSubmitted"] is False
    assert response.json()["recoveredSubmissionId"] == str(submission.id)
    assert rejected.status_code == 422
    assert calls == [(str(project.project_id), str(project.active_generation_id), submission.id)]


def transport(env, payload, calls, content=b"exact video bytes"):
    def handle(request):
        calls.append(request)
        if request.url.path == "/comfy/view":
            return httpx.Response(200, content=content, headers={"Content-Type": "video/mp4"})
        assert request.url.path == f"/comfy/api/jobs/{env[3].comfy_prompt_id}"
        assert request.method == "GET"
        return httpx.Response(200, json=payload)
    return handle


async def test_exact_lifecycle_auto_import_provenance_idempotency_and_reopen(env):
    store, _, p, submission = env
    calls = []
    for upstream, expected in [("pending", "queued"), ("in_progress", "running"), ("completed", "completed")]:
        result = await reconcile(env, transport(env, completed(env, status=upstream,
            execution_start_time=1000, execution_end_time=2000), calls))
        assert result["renderSubmissions"][0]["job"]["status"] == expected
        assert len(result["takes"]) == int(expected == "completed")
    assert [r.url.path for r in calls] == [f"/comfy/api/jobs/{submission.comfy_prompt_id}"] * 3 + ["/comfy/view"]
    assert dict(calls[-1].url.params) == {k: video()[k] for k in ("filename", "subfolder", "type")}
    take = result["takes"][0]
    assert take["sourceKind"] == "comfyui_submission" and take["renderSubmissionId"] == str(submission.id)
    assert take["sha256"] == hashlib.sha256(b"exact video bytes").hexdigest()
    assert store.take_detail(p.project_id, p.active_generation_id, take["id"])["promptSnapshot"] == submission.prompt_snapshot.model_dump(mode="json", by_alias=True)
    assert result["selectedTakeId"] is None
    assert result["renderSubmissions"][0]["job"]["outputs"][0]["importedTakeId"] == take["id"]
    again = await reconcile(env, lambda _: pytest.fail("No terminal job or imported output refetch"))
    assert len(again["takes"]) == 1
    assert len(ProjectStore().load_project(p.project_id).outputs["takes"]) == 1
    assert store.take_media(p.project_id, p.active_generation_id, take["id"])[0].read_bytes() == b"exact video bytes"
    store.delete_take(p.project_id, p.active_generation_id, take["id"])
    assert (await reconcile(env, lambda _: pytest.fail("Deleted Take must not resurrect")))["takes"] == []


async def test_failed_is_terminal_safe_and_never_imports(env):
    calls = []
    result = await reconcile(env, transport(env, completed(env, status="failed", execution_error={
        "node_id": "7", "exception_message": "SECRET C:/private/ prompt contents", "traceback": ["SECRET"]}), calls))
    assert result["takes"] == []
    assert result["renderSubmissions"][0]["job"]["failedNodeId"] == "7"
    assert "SECRET" not in json.dumps(result)
    assert (await reconcile(env, lambda _: pytest.fail("Terminal failure must not be retried")))["renderSubmissions"][0]["job"]["status"] == "failed"


@pytest.mark.parametrize("unsupported", [404, 405, 501])
async def test_exact_history_fallback_only(env, unsupported):
    calls = []
    def handle(request):
        calls.append(request.url.path)
        if "/api/jobs/" in request.url.path:
            return httpx.Response(unsupported, text="Unsupported")
        if "/history/" in request.url.path:
            return httpx.Response(200, json={str(env[3].comfy_prompt_id): {
                "status": {"completed": True, "status_str": "success"}, "outputs": {"2": {"files": [video()]}}},
                str(uuid4()): {"status": {"completed": True, "status_str": "error"}}})
        assert request.url.path == "/comfy/view"
        return httpx.Response(200, content=b"video", headers={"content-type": "application/octet-stream"})
    result = await reconcile(env, handle)
    assert len(result["takes"]) == 1
    assert calls == [f"/comfy/api/jobs/{env[3].comfy_prompt_id}", f"/comfy/history/{env[3].comfy_prompt_id}", "/comfy/view"]


@pytest.mark.parametrize("history,expected", [({}, "unknown"),
    ({"status": {"status_str": "error", "completed": False}}, "failed"),
    ({"status": {"status_str": "success", "completed": False}}, "unknown")])
async def test_history_missing_or_failed_never_infers_completion(env, history, expected):
    def handle(request):
        if request.url.path == "/comfy/queue":
            return httpx.Response(200, json={"queue_running": [], "queue_pending": []})
        if "/api/jobs/" in request.url.path:
            return httpx.Response(404)
        assert request.url.path == f"/comfy/history/{env[3].comfy_prompt_id}"
        return httpx.Response(200, json={str(env[3].comfy_prompt_id): history} if history else {str(uuid4()): completed(env)})
    result = await reconcile(env, handle)
    assert result["takes"] == []
    assert result["renderSubmissions"][0]["job"]["status"] == expected


async def test_zero_multiple_and_explicit_output_choice(env):
    outputs = {"9": {"custom": {"videos": [video(), video("second.mp4")]}}}
    calls = []
    result = await reconcile(env, transport(env, completed(env, outputs), calls))
    assert len(calls) == 1 and result["takes"] == []
    candidates = result["renderSubmissions"][0]["job"]["outputs"]
    assert len(candidates) == 2 and candidates[0]["nodeId"] == "9"
    for candidate in reversed(candidates):
        result = await reconcile(env, transport(env, {}, calls), output_id=candidate["id"])
    assert len(result["takes"]) == 2 and result["selectedTakeId"] is None
    assert calls[1].url.params["filename"] == "second.mp4"
    invalid = await reconcile(env, lambda _: pytest.fail("Unowned candidate must never download"), output_id="a" * 64)
    assert invalid["reconciliationError"] and len(invalid["takes"]) == 2


async def test_zero_video_completed_without_take(env):
    result = await reconcile(env, transport(env, completed(env, {"3": {"images": [video("frame.png")]}}), []))
    assert result["takes"] == []
    assert result["renderSubmissions"][0]["job"]["status"] == "completed"
    assert result["renderSubmissions"][0]["job"]["outputs"] == []


async def test_input_reference_is_excluded_and_output_is_auto_imported(env):
    mixed = {"some-node": {"video": [
        {"filename": "reference.mp4", "subfolder": "", "type": "input"},
        {"filename": "render.mp4", "subfolder": "", "type": "output"},
    ]}}
    result = await reconcile(env, transport(env, completed(env, mixed), []))
    assert len(result["renderSubmissions"][0]["job"]["outputs"]) == 1
    assert result["renderSubmissions"][0]["job"]["outputs"][0]["filename"] == "render.mp4"
    assert len(result["takes"]) == 1 and result["takes"][0]["originalFilename"] == "render.mp4"


async def test_input_only_terminal_job_cannot_auto_import(env):
    input_only = {"some-node": {"video": [{"filename": "reference.mp4", "subfolder": "", "type": "input"}]}}
    result = await reconcile(env, transport(env, completed(env, input_only), []))
    assert result["renderSubmissions"][0]["job"]["outputs"] == [] and result["takes"] == []


@pytest.mark.parametrize("changes", [{"filename": "../bad.mp4"}, {"filename": "/absolute.mp4"},
    {"filename": "C:\\video.mp4"}, {"filename": "bad%2f.mp4"}, {"filename": "https://evil/video.mp4"},
    {"subfolder": "../bad"}, {"subfolder": "/abs"}, {"subfolder": "C:/abs"},
    {"subfolder": "foo\\..\\bad"}, {"subfolder": "a//b"}, {"subfolder": "a/./b"},
    {"filename": "a\x00.mp4"}, {"type": "evil"}, {"type": []}, {"subfolder": None}, {"filename": []}])
def test_reject_unsafe_descriptors(changes):
    assert rr.extract_outputs({"7": {"files": [video(**changes)]}}, uuid4()) == []


def test_generic_nested_extraction_dedup_and_complete_set_limits():
    sid = uuid4()
    outputs = {"7": {"gifs": [video()], "anything": {"files": [video()]}}, "8": {"videos": [video()]}}
    extracted = rr.extract_outputs(outputs, sid)
    assert len(extracted) == 1
    assert extracted[0].id == rr.extract_outputs({"changed-node": video()}, sid)[0].id
    assert extracted[0].id != rr.extract_outputs(outputs, uuid4())[0].id
    for output in [{"files": [video(f"{i}.mp4") for i in range(129)]}, {"files": [0] * 10001}]:
        with pytest.raises(rr.ReconciliationError): rr.extract_outputs(output, sid)
    deep = video()
    for _ in range(18): deep = [deep]
    with pytest.raises(rr.ReconciliationError): rr.extract_outputs([video(), deep], sid)
    assert {o.filename for o in rr.extract_outputs([video(f"x.{ext}") for ext in take_store.VIDEO_TYPES], sid)} == {f"x.{ext}" for ext in take_store.VIDEO_TYPES}


def test_input_reference_video_is_never_a_renderer_candidate_and_mixed_set_is_unique():
    sid = uuid4()
    input_only = {"some-node": {"video": [{"filename": "reference.mp4", "subfolder": "", "type": "input"}]}}
    assert rr.extract_outputs(input_only, sid) == []
    with pytest.raises(Exception):
        RendererOutput(id="a" * 64, filename="reference.mp4", subfolder="", type="input")
    mixed = {"some-node": {"video": [
        {"filename": "reference.mp4", "subfolder": "", "type": "input"},
        {"filename": "render.mp4", "subfolder": "", "type": "output"},
    ]}}
    candidates = rr.extract_outputs(mixed, sid)
    assert len(candidates) == 1 and candidates[0].filename == "render.mp4" and candidates[0].type == "output"


def test_temp_video_remains_a_renderer_candidate():
    candidate = rr.extract_outputs({"node": {"video": [{"filename": "preview.mp4", "subfolder": "", "type": "temp"}]}}, uuid4())
    assert len(candidate) == 1 and candidate[0].type == "temp"


async def test_discovery_limit_never_auto_imports_partial_unique_set(env):
    result = await reconcile(env, transport(env, completed(env, [video(), [0] * 10001]), []))
    assert result["takes"] == [] and result["renderSubmissions"][0]["job"]["outputs"] == []
    assert "limits" in result["renderSubmissions"][0]["job"]["message"]


@pytest.mark.parametrize("failure", ["large", "redirect", "server", "timeout", "wrong-id", "malformed", "compressed"])
async def test_job_transport_safety_no_global_fallback(env, failure):
    calls = []
    def handle(request):
        calls.append(request)
        if failure == "timeout": raise httpx.ReadTimeout("SECRET")
        return {"large": lambda: httpx.Response(200, content=b" " * (rr.MAX_JOB_BYTES + 1)),
            "redirect": lambda: httpx.Response(302, headers={"location": "http://evil/"}, text="SECRET"),
            "server": lambda: httpx.Response(500, text="SECRET"),
            "wrong-id": lambda: httpx.Response(200, json=completed(env, id=str(uuid4()))),
            "malformed": lambda: httpx.Response(200, content=b"["),
            "compressed": lambda: httpx.Response(200, headers={"content-encoding": "br"})}[failure]()
    result = await reconcile(env, handle)
    assert len(calls) == 1 and result["takes"] == []
    assert result["renderSubmissions"][0]["job"]["status"] == "unknown"
    assert result["reconciliationError"] and "SECRET" not in json.dumps(result)


@pytest.mark.parametrize("failure", ["size-header", "size-stream", "redirect", "timeout", "error", "mime", "truncated", "empty"])
async def test_download_limits_and_cleanup(env, monkeypatch, failure):
    monkeypatch.setattr(take_store, "MAX_TAKE_BYTES", 10)
    calls = []
    def handle(request):
        calls.append(request.url.path)
        if "/api/jobs/" in request.url.path: return httpx.Response(200, json=completed(env))
        assert request.url.path == "/comfy/view"
        if failure == "timeout": raise httpx.ReadTimeout("SECRET")
        return {"size-header": lambda: httpx.Response(200, headers={"content-length": "11"}),
            "size-stream": lambda: httpx.Response(200, content=b"x" * 11, headers={"content-length": "1"}),
            "redirect": lambda: httpx.Response(302, headers={"location": "http://evil/"}),
            "error": lambda: httpx.Response(500, content=b"SECRET"),
            "mime": lambda: httpx.Response(200, content=b"html", headers={"content-type": "text/html"}),
            "truncated": lambda: httpx.Response(200, content=b"x", headers={"content-length": "9"}),
            "empty": lambda: httpx.Response(200, content=b"")}[failure]()
    try:
        result = await reconcile(env, handle)
        assert result["reconciliationError"]
    except ProjectStoreError:
        assert failure in {"size-stream", "empty"}
    store, _, p, _ = env
    state = store.generation_outputs(p.project_id, p.active_generation_id)
    assert state["takes"] == [] and state["renderSubmissions"][0]["job"]["status"] == "completed"
    assert len(calls) == 2
    assert not list(store.root.glob("**/outputs/.imports/*.tmp"))
    assert not list(store.root.glob("**/outputs/media/*"))


async def test_concurrent_imports_atomic_receipt_and_stale_poll(env):
    barrier = asyncio.Event()
    arrivals = 0
    class Bytes(httpx.AsyncByteStream):
        async def __aiter__(self):
            nonlocal arrivals
            arrivals += 1
            if arrivals == 2: barrier.set()
            await barrier.wait()
            yield b"same video"
    def handle(request):
        if "/api/jobs/" in request.url.path: return httpx.Response(200, json=completed(env))
        return httpx.Response(200, stream=Bytes())
    await asyncio.wait_for(asyncio.gather(reconcile(env, handle), reconcile(env, handle)), 5)
    store, _, p, submission = env
    store.update_render_job(p.project_id, p.active_generation_id, submission.id, RendererJobState(status="running"))
    state = store.generation_outputs(p.project_id, p.active_generation_id)
    assert len(state["takes"]) == 1
    assert state["renderSubmissions"][0]["job"]["status"] == "completed"
    assert state["renderSubmissions"][0]["job"]["outputs"][0]["importedTakeId"] == state["takes"][0]["id"]


async def test_publication_failure_rolls_back_receipt_and_media(env, monkeypatch):
    calls = []
    await reconcile(env, transport(env, completed(env, {"7": [video(), video("other.mp4")]}), calls))
    store, _, p, submission = env
    candidate = store.render_submission(p.project_id, p.active_generation_id, submission.id).job.outputs[0]
    def fail(*args, **kwargs): raise OSError("injected commit failure")
    with monkeypatch.context() as patch:
        patch.setattr(store, "_commit_sequence", fail)
        with pytest.raises(OSError): await reconcile(env, transport(env, {}, calls), output_id=candidate.id)
    assert store.render_submission(p.project_id, p.active_generation_id, submission.id).job.outputs[0].imported_take_id is None
    assert not list(store.root.glob("**/outputs/media/*"))
    assert not list(store.root.glob("**/outputs/.imports/*.tmp"))
    assert len((await reconcile(env, transport(env, {}, calls), output_id=candidate.id))["takes"]) == 1


def test_id_only_api_and_connection_authority(env, monkeypatch):
    original = rr.ComfyJobClient.__init__
    calls = []
    monkeypatch.setattr(rr.ComfyJobClient, "__init__", lambda self, connection, **kwargs:
        original(self, connection, transport=httpx.MockTransport(transport(env, completed(env), calls))))
    store, config, p, submission = env
    config.save("connections", ConnectionWrite(name="Unrelated", baseUrl="http://unrelated:9999"))
    path = f"/api/projects/{p.project_id}/generations/{p.active_generation_id}/render-submissions/{submission.id}/reconcile"
    client = TestClient(app)
    assert client.post(path, json={"promptId": str(uuid4()), "url": "http://evil"}).status_code == 422
    assert client.post(path + "?filename=evil.mp4").status_code == 422
    assert not calls
    assert client.post(path.replace(str(submission.id), str(uuid4()))).status_code == 422
    response = client.post(path)
    assert response.status_code == 200 and len(response.json()["takes"]) == 1
    assert all(r.url.host == "localhost" and r.url.port == 8188 for r in calls)


async def test_old_submission_defaults_without_schema_bump_or_read_rewrite(env):
    store, _, p, _ = env
    manifest = next(store.root.glob("*/project.json"))
    raw = json.loads(manifest.read_bytes())
    raw["sequence"]["generations"][0]["renderSubmissions"][0].pop("job")
    manifest.write_text(json.dumps(raw), encoding="utf-8")
    before = manifest.read_bytes()
    assert store.load_project(p.project_id).outputs["renderSubmissions"][0]["job"]["status"] == "unknown"
    assert manifest.read_bytes() == before
    await reconcile(env, transport(env, completed(env, {}), []))
    assert json.loads(manifest.read_bytes())["schemaVersion"] == 17


def test_new_render_submission_constructor_starts_queued_and_legacy_json_is_unknown(env):
    store, _, p, submission = env
    assert submission.job.status == "queued"
    raw = submission.model_dump(mode="json", by_alias=True)
    raw.pop("job")
    assert RenderSubmission.model_validate(raw).job.status == "unknown"


async def test_native_exact_failure_and_success_keep_accepted_authority(native_env):
    store, config, _, _, p = native_env
    choose(store, p)
    accepted, _ = send(native_env)
    confirm(native_env, p, accepted)
    for state, expected in [("failed", "unavailable"), ("cancelled", "unavailable"), ("completed", "available")]:
        receipt, _ = send(native_env)
        def handle(request):
            assert request.url.path == f"/api/jobs/{receipt.prompt_id}"
            return httpx.Response(200, json={"id": str(receipt.prompt_id), "status": state, "outputs": {}})
        await rr.reconcile_submission(p.project_id, p.active_generation_id, receipt.render_submission_id,
            store=store, config=config, transport=httpx.MockTransport(handle))
        status = store.continuation_status(p.project_id, p.active_generation_id)
        assert next(r for r in status["executions"] if r.id == receipt.continuation_execution_id).status == expected
        assert status["acceptedId"] == accepted.continuation_execution_id


@pytest.mark.parametrize("renderer_status,native_status", [
    ("pending", "pending"), ("in_progress", "pending"), ("mystery", "pending"),
    ("completed", "available"), ("failed", "unavailable"), ("cancelled", "unavailable"),
])
async def test_native_pending_follows_only_exact_renderer_terminal_state(native_env, renderer_status, native_status):
    store, config, _, _, project = native_env
    choose(store, project)
    receipt, _ = send(native_env)
    def handle(request):
        assert request.url.path == f"/api/jobs/{receipt.prompt_id}"
        return httpx.Response(200, json={"id": str(receipt.prompt_id), "status": renderer_status, "outputs": {}})
    for _ in range(2):
        await rr.reconcile_submission(project.project_id, project.active_generation_id, receipt.render_submission_id,
            store=store, config=config, transport=httpx.MockTransport(handle))
        state = store.continuation_status(project.project_id, project.active_generation_id)
        assert next(record for record in state["executions"] if record.id == receipt.continuation_execution_id).status == native_status
        assert state["acceptedId"] is None


async def test_native_explicit_unavailable_stays_unavailable_after_completion(native_env):
    store, config, _, _, project = native_env
    choose(store, project)
    receipt, _ = send(native_env)
    store.decide_continuation(project.project_id, project.active_generation_id, receipt.continuation_execution_id,
        ContinuationDecision(status="unavailable"))
    def handle(request):
        return httpx.Response(200, json={"id": str(receipt.prompt_id), "status": "completed", "outputs": {}})
    await rr.reconcile_submission(project.project_id, project.active_generation_id, receipt.render_submission_id,
        store=store, config=config, transport=httpx.MockTransport(handle))
    state = store.continuation_status(project.project_id, project.active_generation_id)
    assert state["executions"][0].status == "unavailable"
    assert state["acceptedId"] is None


@pytest.mark.parametrize("output_mode", ["download_error", "multiple"])
async def test_native_completion_is_available_without_automatic_take_import(native_env, output_mode):
    store, config, _, _, project = native_env
    choose(store, project)
    receipt, _ = send(native_env)
    outputs = {"7": {"gifs": [video("one.mp4")]}}
    if output_mode == "multiple":
        outputs["7"]["gifs"].append(video("two.mp4"))
    def handle(request):
        if request.url.path == "/view":
            return httpx.Response(503)
        return httpx.Response(200, json={"id": str(receipt.prompt_id), "status": "completed", "outputs": outputs})
    result = await rr.reconcile_submission(project.project_id, project.active_generation_id, receipt.render_submission_id,
        store=store, config=config, transport=httpx.MockTransport(handle))
    state = store.continuation_status(project.project_id, project.active_generation_id)
    assert state["executions"][0].status == "available"
    assert state["acceptedId"] is None
    assert result["takes"] == []
    assert bool(result["reconciliationError"]) == (output_mode == "download_error")


async def test_native_completion_does_not_promote_another_submission_or_override_acceptance(native_env):
    store, config, _, _, project = native_env
    choose(store, project)
    accepted, _ = send(native_env)
    confirm(native_env, project, accepted)
    completed, _ = send(native_env)
    other, _ = send(native_env)
    def handle(request):
        return httpx.Response(200, json={"id": str(completed.prompt_id), "status": "completed", "outputs": {}})
    await rr.reconcile_submission(project.project_id, project.active_generation_id, completed.render_submission_id,
        store=store, config=config, transport=httpx.MockTransport(handle))
    state = store.continuation_status(project.project_id, project.active_generation_id)
    statuses = {record.id: record.status for record in state["executions"]}
    assert statuses == {accepted.continuation_execution_id: "available",
                        completed.continuation_execution_id: "available",
                        other.continuation_execution_id: "pending"}
    assert state["acceptedId"] == accepted.continuation_execution_id


def test_native_reconciliation_requires_recorded_connection(native_env):
    store, _, _, _, project = native_env
    choose(store, project)
    receipt, _ = send(native_env)
    submission = store.render_submission(project.project_id, project.active_generation_id, receipt.render_submission_id)
    changed_connection = submission.model_copy(deep=True, update={"connection_id": uuid4(),
        "job": RendererJobState(status="completed")})
    store.reconcile_native_status(project.project_id, project.active_generation_id, changed_connection)
    state = store.continuation_status(project.project_id, project.active_generation_id)
    assert state["executions"][0].status == "pending"


def test_native_renderer_summary_uses_persisted_output_filenames(native_env):
    store, _, _, _, project = native_env
    choose(store, project)
    receipt, _ = send(native_env)
    outputs = [RendererOutput(id=f"{index:064x}", filename=name, subfolder="", type="output")
               for index, name in enumerate(["one.mp4", "one.mp4", "two.mp4", "three.mp4", "four.mp4"], 1)]
    store.update_render_job(project.project_id, project.active_generation_id, receipt.render_submission_id,
        RendererJobState(status="completed", outputs=outputs))
    summary = store.continuation_status(project.project_id, project.active_generation_id, renderer_summary=True)
    submission = summary["executions"][0]["renderSubmission"]
    assert submission["jobStatus"] == "completed"
    assert submission["outputFilenames"] == ["one.mp4", "two.mp4", "three.mp4", "four.mp4"]
    assert "subfolder" not in submission and "nodeId" not in submission


@pytest.mark.parametrize("phase", ["job", "download"])
async def test_total_deadlines_close_streams_and_remove_staging(env, monkeypatch, phase):
    monkeypatch.setattr(rr, "JOB_TIMEOUT" if phase == "job" else "DOWNLOAD_TIMEOUT", 0.02)
    closed = []
    class Slow(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"first bytes"
            await asyncio.Event().wait()
        async def aclose(self): closed.append(True)
    def handle(request):
        if phase == "download" and "/api/jobs/" in request.url.path:
            return httpx.Response(200, json=completed(env))
        return httpx.Response(200, stream=Slow())
    result = await reconcile(env, handle)
    assert closed and "timed out" in result["reconciliationError"]
    assert result["takes"] == []
    assert not list(env[0].root.glob("**/outputs/.imports/*.tmp"))


def test_transport_explicitly_disables_proxy_redirects_and_bounds_timeout(env, monkeypatch):
    original, options = httpx.AsyncClient, []
    def capture(**kwargs):
        options.append(kwargs)
        return original(**kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", capture)
    monkeypatch.setenv("HTTP_PROXY", "http://untrusted.invalid:8080")
    asyncio.run(reconcile(env, transport(env, completed(env, {}), [])))
    assert options[0]["trust_env"] is False and options[0]["follow_redirects"] is False
    assert options[0]["timeout"].connect == 10 and options[0]["timeout"].read == 30


async def test_wrong_generation_or_project_never_contacts_renderer(env, workspace_factory):
    store, config, p, submission = env
    other_generation = store.create_generation(p.project_id, workspace=workspace_factory())
    other_project = store.create_project(workspace_factory())
    for target in (other_generation, other_project):
        with pytest.raises(ProjectStoreError):
            await rr.reconcile_submission(target.project_id, target.active_generation_id, submission.id,
                store=store, config=config, transport=httpx.MockTransport(lambda _: pytest.fail("Ownership first")))


async def test_rejected_connection_never_uses_another_saved_connection(env, monkeypatch):
    from miniconstruct.execution import ConfigNotFound
    def missing(kind, record_id):
        assert record_id == env[3].connection_id
        raise ConfigNotFound()
    monkeypatch.setattr(env[1], "get", missing)
    result = await reconcile(env, lambda _: pytest.fail("No substitute connection"))
    assert result["renderSubmissions"][0]["job"]["status"] == "unknown"
    assert result["reconciliationError"] and result["takes"] == []


@pytest.mark.parametrize("running", [True, False])
async def test_legacy_exact_queue_identity_recovers_running_and_queued_status(env, running):
    prompt = str(env[3].comfy_prompt_id)
    def handle(request):
        assert request.method == "GET"
        if "/api/jobs/" in request.url.path:
            return httpx.Response(404)
        if "/history/" in request.url.path:
            return httpx.Response(200, json={str(uuid4()): {"status": {"status_str": "success", "completed": True}}})
        assert request.url.path == "/comfy/queue"
        return httpx.Response(200, json={"queue_running": [[0, prompt]] if running else [[0, str(uuid4())]],
                                        "queue_pending": [] if running else [[1, prompt]]})
    result = await reconcile(env, handle)
    assert result["renderSubmissions"][0]["job"]["status"] == ("running" if running else "queued")
    assert result["takes"] == [] and result["reconciliationError"] is None


async def test_late_nonterminal_http_response_cannot_regress_a_newer_exact_status(env):
    store, _, project, submission = env
    def handle(request):
        store.update_render_job(project.project_id, project.active_generation_id, submission.id,
                                RendererJobState(status="running", lastCheckedAt="2026-10-17T01:00:00Z"))
        return httpx.Response(200, json={"id": str(submission.comfy_prompt_id), "status": "pending"})
    result = await reconcile(env, handle)
    assert result["renderSubmissions"][0]["job"]["status"] == "running"
