"""Provider-free exact live observation and cancellation checks."""
import base64
import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from miniconstruct import comfy_live as live
from miniconstruct.execution import workflow_fingerprint
from miniconstruct.models.takes import RendererJobState
from test_render_reconciliation import env  # noqa: F401 - shared saved Project fixture


def ids(env):
    store, config, project, submission = env
    return (project.project_id, project.active_generation_id, submission.id), str(submission.comfy_prompt_id)


def frame(kind, prefix, payload):
    return kind.to_bytes(4, "big") + prefix + payload


def test_exact_identity_and_base_path(env):
    args, prompt = ids(env)
    submission, connection = live.exact_submission(*args, store=env[0], config=env[1])
    assert str(submission.comfy_prompt_id) == prompt
    assert live.websocket_url(connection, prompt) == f"ws://localhost:8188/comfy/ws?clientId={prompt}"
    redirect = RuntimeError("redirect")
    assert live.NoRedirectConnect.process_redirect(None, redirect) is redirect
    with pytest.raises(Exception):
        live.exact_submission(args[0], args[1], uuid4(), store=env[0], config=env[1])
    with pytest.raises(Exception):
        live.exact_submission(uuid4(), args[1], args[2], store=env[0], config=env[1])


def test_json_filter_and_progress(env):
    _, prompt = ids(env)
    packet = lambda kind, data: json.dumps({"type": kind, "data": data})
    assert live.normalize_text(packet("executing", {"prompt_id": str(uuid4()), "node": "105"}), prompt) is None
    assert live.normalize_text(packet("progress_state", {"prompt_id": str(uuid4()), "nodes": {"105": {"state": "running"}}}), prompt) is None
    assert live.normalize_text(packet("executing", {"prompt_id": prompt, "node": "105"}), prompt) == {"type": "executing", "nodeId": "105"}
    assert live.normalize_text(packet("progress", {"prompt_id": prompt, "node": "105", "value": 11, "max": 20}), prompt)["value"] == 11
    assert live.normalize_text(packet("progress_state", {"prompt_id": prompt, "nodes": {"105": {"state": "running", "value": 3, "max": 9}}}), prompt)["max"] == 9
    assert live.normalize_text(packet("execution_error", {"prompt_id": prompt, "traceback": "secret"}), prompt) == {"type": "terminal", "status": "failed"}


def test_node_type_lookup_requires_the_recorded_workflow_fingerprint(env):
    submission = env[3]
    graph = {"105": {"class_type": "SamplerCustomAdvanced", "inputs": {}}}
    fingerprint = workflow_fingerprint(graph)
    profile = SimpleNamespace(fingerprint=fingerprint, workflow=graph)
    config = SimpleNamespace(get=lambda collection, profile_id: profile)
    exact = submission.model_copy(update={"workflow_fingerprint": fingerprint})
    assert live.exact_workflow_node_types(exact, config) == {"105": "SamplerCustomAdvanced"}
    changed = submission.model_copy(update={"workflow_fingerprint": "f" * 64})
    assert live.exact_workflow_node_types(changed, config) == {}


@pytest.mark.parametrize("kind", ["execution_success", "execution_error", "execution_interrupted"])
def test_terminal_events_require_exact_prompt_or_request_reconciliation(env, kind):
    _, prompt = ids(env)
    packet = lambda data: json.dumps({"type": kind, "data": data})
    assert live.normalize_text(packet({"prompt_id": prompt}), prompt) == {
        "type": "terminal", "status": live.TERMINAL_EVENTS[kind]}
    assert live.normalize_text(packet({"prompt_id": "prompt-B"}), prompt) is None
    assert live.normalize_text(packet({}), prompt) == {"type": "terminal-check"}


def test_standard_binary_previews(env):
    _, prompt = ids(env)
    jpeg = b"\xff\xd8\xffjpeg"
    png = b"\x89PNG\r\n\x1a\npng"
    old = frame(1, (1).to_bytes(4, "big"), jpeg)
    assert live.parse_binary(old, prompt) == ({"type": "preview-meta", "source": "comfyui", "mime": "image/jpeg"}, jpeg)
    meta = json.dumps({"prompt_id": prompt, "node_id": "105", "image_type": "image/png"}).encode()
    modern = frame(4, len(meta).to_bytes(4, "big") + meta, png)
    assert live.parse_binary(modern, prompt)[0]["nodeId"] == "105"
    wrong = json.dumps({"prompt_id": str(uuid4()), "image_type": "image/png"}).encode()
    assert live.parse_binary(frame(4, len(wrong).to_bytes(4, "big") + wrong, b"png"), prompt) is None
    assert live.parse_binary(b"\x00", prompt) is None
    assert live.parse_binary(frame(1, (1).to_bytes(4, "big"), b"x" * (live.MAX_PREVIEW + 1)), prompt) is None


@pytest.mark.parametrize("mime", ["image/jpeg", "image/webp", "video/mp4"])
def test_kj_preview_boundaries(env, mime):
    _, prompt = ids(env)
    sample = {"image/jpeg": b"\xff\xd8\xffpreview", "image/webp": b"RIFFxxxxWEBPpreview",
              "video/mp4": b"\x00\x00\x00\x18ftypisom"}[mime]
    packet = json.dumps({"type": "kj_preview_override", "data": {"image": base64.b64encode(sample).decode(),
                         "mime": mime, "node_id": "105", "step": 11, "total": 20}})
    meta, payload = live.normalize_text(packet, prompt)
    assert payload == sample and meta["mime"] == mime and meta["step"] == 11 and meta["total"] == 20
    assert live.normalize_text(packet.replace(base64.b64encode(sample).decode(), "@@@"), prompt) is None


@pytest.mark.asyncio
async def test_exact_cancel_and_idempotent_terminal(env):
    args, prompt = ids(env)
    paths = []
    def handle(request):
        paths.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(200, json={"cancelled": True})
        return httpx.Response(200, json={"id": prompt, "status": "cancelled"})
    result = await live.cancel_submission(*args, store=env[0], config=env[1], transport=httpx.MockTransport(handle))
    assert paths == [("POST", f"/comfy/api/jobs/{prompt}/cancel"), ("GET", f"/comfy/api/jobs/{prompt}")]
    assert result["renderSubmissions"][0]["job"]["status"] == "cancelled"
    assert (await live.cancel_submission(*args, store=env[0], config=env[1]))["alreadyTerminal"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [405, 501])
async def test_unsupported_cancel_never_interrupts(env, status):
    args, prompt = ids(env)
    paths = []
    def handle(request):
        paths.append(request.url.path)
        return httpx.Response(status) if request.method == "POST" else httpx.Response(404)
    result = await live.cancel_submission(*args, store=env[0], config=env[1], transport=httpx.MockTransport(handle))
    assert result["cancelSupported"] is False
    assert paths == [f"/comfy/api/jobs/{prompt}/cancel"]


@pytest.mark.asyncio
async def test_cancel_404_modern_missing_job_is_not_unsupported(env):
    args, prompt = ids(env)
    paths = []
    def handle(request):
        paths.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(404)
        if request.url.path == "/comfy/api/jobs":
            return httpx.Response(200, json={"jobs": []})
        return httpx.Response(404)
    result = await live.cancel_submission(*args, store=env[0], config=env[1], transport=httpx.MockTransport(handle))
    assert result["cancelSupported"] is None
    assert "absent" in result["cancelDiagnosis"]
    assert result["renderSubmissions"][0]["job"]["status"] == "unknown"
    assert not any("interrupt" in path for _, path in paths)


@pytest.mark.asyncio
async def test_cancel_404_legacy_history_proves_unsupported(env):
    args, prompt = ids(env)
    def handle(request):
        if request.url.path == f"/comfy/history/{prompt}":
            return httpx.Response(200, json={})
        return httpx.Response(404)
    result = await live.cancel_submission(*args, store=env[0], config=env[1], transport=httpx.MockTransport(handle))
    assert result["cancelSupported"] is None
    assert "uncertain" in result["cancelDiagnosis"]


@pytest.mark.asyncio
async def test_cancel_404_exact_get_shows_job_but_capability_is_uncertain(env):
    args, prompt = ids(env)
    paths = []
    def handle(request):
        paths.append(request.url.path)
        if request.method == "POST": return httpx.Response(404)
        if request.url.path == f"/comfy/api/jobs/{prompt}": return httpx.Response(200, json={"id": prompt})
        return httpx.Response(404)
    result = await live.cancel_submission(*args, store=env[0], config=env[1], transport=httpx.MockTransport(handle))
    assert result["cancelSupported"] is None
    assert "uncertain" in result["cancelDiagnosis"]
    assert not any("interrupt" in path for path in paths)


@pytest.mark.asyncio
async def test_cancel_404_without_route_evidence_is_uncertain(env):
    args, _ = ids(env)
    paths = []
    def handle(request):
        paths.append(request.url.path)
        return httpx.Response(404)
    result = await live.cancel_submission(*args, store=env[0], config=env[1], transport=httpx.MockTransport(handle))
    assert result["cancelSupported"] is None
    assert "uncertain" in result["cancelDiagnosis"]
    assert not any("interrupt" in path for path in paths)


@pytest.mark.asyncio
async def test_live_bridge_filters_and_disconnect_is_not_failure(env, monkeypatch):
    args, prompt = ids(env)
    class Browser:
        def __init__(self): self.events = []; self.bytes = []
        async def receive(self): await asyncio.Future()
        async def accept(self): pass
        async def send_json(self, value): self.events.append(value)
        async def send_bytes(self, value): self.bytes.append(value)
        async def close(self): pass
    class Upstream:
        async def send(self, value): self.sent = value
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        def __aiter__(self): return self
        async def __anext__(self):
            if not messages: raise OSError("offline")
            return messages.pop(0)
    messages = [json.dumps({"type": "executing", "data": {"prompt_id": str(uuid4()), "node": "wrong"}}),
                frame(1, (1).to_bytes(4, "big"), b"x" * (live.MAX_PREVIEW + 1)),
                json.dumps({"type": "executing", "data": {"prompt_id": prompt, "node": "105"}}),
                frame(1, (1).to_bytes(4, "big"), b"\xff\xd8\xffjpeg")]
    called = []
    def connector(url, **kwargs): called.append((url, kwargs)); return Upstream()
    async def fake_reconcile(*_, **__): return {}
    monkeypatch.setattr(live, "reconcile_submission", fake_reconcile)
    browser = Browser()
    await live.observe(browser, *args, store=env[0], config=env[1], connector=connector)
    assert called[0][0].endswith(f"clientId={prompt}") and called[0][1]["proxy"] is None
    assert browser.bytes == [b"\xff\xd8\xffjpeg"]
    assert [e["type"] for e in browser.events] == ["connected", "state", "executing", "preview-meta", "disconnected", "reconcile"]
    assert browser.events[0]["nodeTypes"] == {}
    assert env[0].render_submission(*args).job.status == "queued"


@pytest.mark.asyncio
async def test_terminal_event_invokes_exact_reconciliation(env, monkeypatch):
    args, prompt = ids(env)
    seen = []
    class Browser:
        async def receive(self): await asyncio.Future()
        async def accept(self): pass
        async def send_json(self, value): seen.append(value)
        async def close(self): pass
    class Upstream:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def send(self, value): pass
        def __aiter__(self): return self
        async def __anext__(self):
            if seen[-1].get("type") == "terminal": raise StopAsyncIteration
            return json.dumps({"type": "execution_success", "data": {"prompt_id": prompt}})
    calls = []
    async def fake_reconcile(*values, **kwargs):
        calls.append(values)
        env[0].update_render_job(*values, RendererJobState(status="completed"))
    monkeypatch.setattr(live, "reconcile_submission", fake_reconcile)
    await live.observe(Browser(), *args, store=env[0], config=env[1], connector=lambda *a, **k: Upstream())
    assert calls == [args]
    assert [event["type"] for event in seen][-2:] == ["terminal", "reconcile"]


@pytest.mark.asyncio
async def test_unscoped_interruption_reconciles_exact_job_and_keeps_observer_if_active(env, monkeypatch):
    args, prompt = ids(env)
    seen = []
    class Browser:
        async def receive(self): await asyncio.Future()
        async def accept(self): pass
        async def send_json(self, value): seen.append(value)
        async def close(self): pass
    class Upstream:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def send(self, value): pass
        def __aiter__(self): return self
        async def __anext__(self):
            if not messages: raise OSError("offline")
            return messages.pop(0)
    messages = [json.dumps({"type": "execution_interrupted", "data": {}}),
                json.dumps({"type": "executing", "data": {"prompt_id": prompt, "node": "105"}})]
    calls = []
    async def fake_reconcile(*values, **kwargs): calls.append(values)
    monkeypatch.setattr(live, "reconcile_submission", fake_reconcile)
    await live.observe(Browser(), *args, store=env[0], config=env[1], connector=lambda *a, **k: Upstream())
    assert calls == [args, args]  # ambiguous terminal check, then ordinary disconnect reconciliation
    assert {"type": "state", "status": "queued"} in seen
    assert {"type": "executing", "nodeId": "105"} in seen
    assert not any(event.get("type") == "terminal" for event in seen)


async def test_browser_disconnect_closes_idle_upstream_without_waiting_for_renderer_frames(env, monkeypatch):
    args, _ = ids(env)
    closed = asyncio.Event()
    class Browser:
        async def accept(self): pass
        async def send_json(self, value): pass
        async def receive(self): return {"type": "websocket.disconnect"}
        async def close(self): pass
    class Upstream:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): closed.set()
        async def send(self, value): pass
        def __aiter__(self): return self
        async def __anext__(self): await asyncio.Future()
    async def reconcile(*values, **kwargs): return {}
    monkeypatch.setattr(live, "reconcile_submission", reconcile)
    await asyncio.wait_for(live.observe(Browser(), *args, store=env[0], config=env[1],
                                       connector=lambda *a, **k: Upstream()), 1)
    assert closed.is_set()


async def test_failed_upstream_open_never_claims_connected_or_changes_render_status(env, monkeypatch):
    args, _ = ids(env)
    events = []
    class Browser:
        async def accept(self): pass
        async def send_json(self, value): events.append(value)
        async def close(self): pass
    class Failed:
        async def __aenter__(self): raise OSError("offline")
        async def __aexit__(self, *_): pass
    async def reconcile(*values, **kwargs): return {}
    monkeypatch.setattr(live, "reconcile_submission", reconcile)
    await live.observe(Browser(), *args, store=env[0], config=env[1], connector=lambda *a, **k: Failed())
    assert [event["type"] for event in events] == ["disconnected", "reconcile"]
    assert env[0].render_submission(*args).job.status == "queued"
