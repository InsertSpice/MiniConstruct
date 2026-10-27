"""Bounded, exact-submission ComfyUI observation and cancellation."""
import base64
import binascii
import asyncio
from contextlib import suppress, asynccontextmanager
import json
import math
import re
from urllib.parse import urlsplit, urlunsplit

import httpx
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from miniconstruct.execution import ConfigNotFound, ExecutionStore
from miniconstruct.project_store import ProjectStore
from miniconstruct.render_reconciliation import ComfyJobClient, reconcile_submission, safe_node

MAX_TEXT = 64 * 1024
MAX_META = 8 * 1024
MAX_PREVIEW = 12 * 1024 * 1024
MAX_KJ_TEXT = MAX_PREVIEW * 4 // 3 + MAX_META
MIMES = {"image/jpeg", "image/png", "image/webp", "video/mp4"}
TERMINAL_EVENTS = {"execution_success": "completed", "execution_error": "failed",
                   "execution_interrupted": "cancelled"}


class NoRedirectConnect(connect):
    def process_redirect(self, exc):
        return exc


def exact_submission(project_id, generation_id, submission_id, *, store=None, config=None):
    store, config = store or ProjectStore(), config or ExecutionStore()
    submission = store.render_submission(project_id, generation_id, submission_id)
    connection = config.get("connections", submission.connection_id)
    return submission, connection


def exact_workflow_node_types(submission, config):
    """Return node labels only when the referenced profile still has the exact submitted graph."""
    try:
        profile = config.get("profiles", submission.workflow_profile_id)
    except ConfigNotFound:
        return {}
    if profile.fingerprint != submission.workflow_fingerprint:
        return {}
    return {
        node_id: node["class_type"]
        for node_id, node in profile.workflow.items()
        if safe_node(node_id) and isinstance(node, dict)
        and isinstance(node.get("class_type"), str) and 0 < len(node["class_type"]) <= 128
    }


def websocket_url(connection, prompt_id):
    parts = urlsplit(connection.base_url)
    return urlunsplit(("wss" if parts.scheme == "https" else "ws", parts.netloc,
                       parts.path.rstrip("/") + "/ws", f"clientId={prompt_id}", ""))


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 10**9 else None


def metadata(value, source, mime):
    result = {"type": "preview-meta", "source": source, "mime": mime}
    for name in ("w", "h", "step", "total", "sigma", "delta", "step_ms", "avg_step_ms", "fps"):
        safe = number(value.get(name))
        if safe is not None:
            result[{"w": "width", "h": "height"}.get(name, name)] = safe
    node = safe_node(value.get("node_id"))
    if node:
        result["nodeId"] = node
    return result


def valid_media(mime, payload):
    return ((mime == "image/jpeg" and payload.startswith(b"\xff\xd8\xff"))
            or (mime == "image/png" and payload.startswith(b"\x89PNG\r\n\x1a\n"))
            or (mime == "image/webp" and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP")
            or (mime == "video/mp4" and payload[4:8] == b"ftyp"))


def parse_binary(frame, prompt_id):
    if len(frame) < 8 or len(frame) > MAX_PREVIEW + MAX_META + 8:
        return None
    kind = int.from_bytes(frame[:4], "big")
    if kind == 1:
        mime = {1: "image/jpeg", 2: "image/png"}.get(int.from_bytes(frame[4:8], "big"))
        data, info = frame[8:], {}
    elif kind == 4:
        length = int.from_bytes(frame[4:8], "big")
        if not 0 < length <= MAX_META or len(frame) < 8 + length:
            return None
        try:
            info = json.loads(frame[8:8 + length])
        except (ValueError, UnicodeError, RecursionError):
            return None
        if not isinstance(info, dict) or info.get("prompt_id") != prompt_id:
            return None
        mime = info.get("image_type")
        data = frame[8 + length:]
    else:
        return None
    if mime not in {"image/jpeg", "image/png"} or not 0 < len(data) <= MAX_PREVIEW or not valid_media(mime, data):
        return None
    return metadata(info, "comfyui", mime), data


def normalize_text(raw, prompt_id):
    if len(raw) > MAX_KJ_TEXT or (len(raw) > MAX_TEXT and '"kj_preview_override"' not in raw[:256]):
        return None
    try:
        message = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        return None
    if not isinstance(message, dict) or not isinstance(message.get("data"), dict):
        return None
    kind, data = message.get("type"), message["data"]
    if kind == "kj_preview_override":
        if data.get("prompt_id", prompt_id) != prompt_id or data.get("mime") not in MIMES:
            return None
        encoded = data.get("image")
        if not isinstance(encoded, str) or len(encoded) > (MAX_PREVIEW * 4 // 3 + 8):
            return None
        if encoded.startswith("data:"):
            prefix, sep, encoded = encoded.partition(",")
            if not sep or not re.fullmatch(r"data:(image/jpeg|image/webp|video/mp4);base64", prefix):
                return None
        try:
            payload = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            return None
        if not 0 < len(payload) <= MAX_PREVIEW or not valid_media(data["mime"], payload):
            return None
        return metadata(data, "kj", data["mime"]), payload
    # A client socket also receives global status updates. They cannot identify a job.
    if kind == "status":
        return None
    if kind == "progress_state" and data.get("prompt_id") != prompt_id:
        return None
    if kind in TERMINAL_EVENTS:
        if "prompt_id" not in data:
            return {"type": "terminal-check"}
        if data["prompt_id"] != prompt_id:
            return None
        return {"type": "terminal", "status": TERMINAL_EVENTS[kind]}
    if data.get("prompt_id", prompt_id) != prompt_id:
        return None
    if kind == "execution_start":
        return {"type": "state", "status": "running"}
    if kind == "executing":
        node = safe_node(data.get("node"))
        return {"type": "executing", "nodeId": node} if node else None
    if kind == "progress":
        value, maximum = number(data.get("value")), number(data.get("max"))
        if value is None or maximum is None:
            return None
        return {"type": "progress", "value": value, "max": maximum,
                "nodeId": safe_node(data.get("node"))}
    if kind == "progress_state":
        nodes = data.get("nodes")
        if not isinstance(nodes, dict):
            return None
        for node_id, item in list(nodes.items())[:128]:
            if isinstance(item, dict) and item.get("state") == "running" and safe_node(node_id):
                value, maximum = number(item.get("value")), number(item.get("max"))
                return {"type": "progress", "nodeId": node_id, "value": value, "max": maximum}
        return None
    return None


async def cancel_submission(project_id, generation_id, submission_id, *, store=None, config=None, transport=None):
    store, config = store or ProjectStore(), config or ExecutionStore()
    submission, connection = exact_submission(project_id, generation_id, submission_id, store=store, config=config)
    if submission.job.status in {"completed", "failed", "cancelled"}:
        return {"cancelDispatched": False, "alreadyTerminal": True,
                **store.generation_outputs(project_id, generation_id)}
    async with ComfyJobClient(connection, transport=transport) as client:
        async with client.client.stream("POST", client.base + f"/api/jobs/{submission.comfy_prompt_id}/cancel") as response:
            if response.status_code in {405, 501}:
                return {"cancelSupported": False, "cancelDispatched": False,
                        **store.generation_outputs(project_id, generation_id)}
            if response.status_code == 404:
                # A 404 alone does not identify whether cancellation is absent,
                # the exact job is absent, or the endpoint is ambiguous. Probe
                # only read-only job routes; never use the global interrupt.
                async def probe(path):
                    async with client.client.stream("GET", client.base + path) as checked:
                        return checked.status_code, await checked.aread()
                exact_status, _ = await probe(f"/api/jobs/{submission.comfy_prompt_id}")
                if exact_status == 200:
                    supported = None
                    diagnosis = "Exact cancellation returned 404 while the recorded job is visible. Cancellation capability is uncertain. Recover this job."
                else:
                    list_status, list_body = await probe("/api/jobs")
                    if list_status == 200 and exact_status == 404:
                        try:
                            jobs = json.loads(list_body)
                            records = jobs.get("jobs", jobs) if isinstance(jobs, dict) else jobs
                            ids = {str(item.get("id", item.get("prompt_id", ""))) for item in records if isinstance(item, dict)} if isinstance(records, list) else None
                        except (ValueError, UnicodeError, AttributeError, TypeError):
                            ids = None
                        if ids is not None and str(submission.comfy_prompt_id) not in ids:
                            supported = None
                            diagnosis = "The exact job is absent from ComfyUI's job API. Recover this job."
                        else:
                            supported = None
                            diagnosis = "Exact cancellation returned 404; cancellation capability is uncertain. Recover this job."
                    else:
                        supported = None
                        diagnosis = "Exact cancellation returned 404; cancellation capability is uncertain. Recover this job."
                result = await reconcile_submission(project_id, generation_id, submission_id,
                    store=store, config=config, transport=transport)
                return {**result, "cancelSupported": supported, "cancelDispatched": False,
                        "cancelDiagnosis": diagnosis}
            if response.status_code != 200:
                raise RuntimeError("Exact ComfyUI cancellation was unavailable.")
            raw = await response.aread()
            if len(raw) > MAX_TEXT:
                raise RuntimeError("Invalid exact cancellation response.")
            try:
                payload = json.loads(raw)
            except (ValueError, UnicodeError):
                raise RuntimeError("Invalid exact cancellation response.") from None
            if not isinstance(payload, dict) or type(payload.get("cancelled")) is not bool:
                raise RuntimeError("Invalid exact cancellation response.")
    result = await reconcile_submission(project_id, generation_id, submission_id, store=store, config=config, transport=transport)
    return {**result, "cancelSupported": True, "cancelDispatched": payload["cancelled"]}


@asynccontextmanager
async def live_frames(websocket, upstream):
    """Close an idle upstream promptly when its foreground browser disconnects."""
    async def browser_closed():
        while (await websocket.receive())["type"] != "websocket.disconnect":
            pass
    closed = asyncio.create_task(browser_closed())
    async def frames():
        iterator = aiter(upstream)
        while True:
            pending = asyncio.create_task(anext(iterator))
            try:
                done, _ = await asyncio.wait((pending, closed), return_when=asyncio.FIRST_COMPLETED)
                if closed in done:
                    await closed
                    raise WebSocketDisconnect()
                try:
                    raw = pending.result()
                except StopAsyncIteration:
                    return
                yield raw
            finally:
                if not pending.done():
                    pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
    try:
        yield frames()
    finally:
        closed.cancel()
        with suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
            await closed


async def observe(websocket, project_id, generation_id, submission_id, *, store=None, config=None, connector=NoRedirectConnect):
    store, config = store or ProjectStore(), config or ExecutionStore()
    submission, connection = exact_submission(project_id, generation_id, submission_id, store=store, config=config)
    prompt_id = str(submission.comfy_prompt_id)
    await websocket.accept()
    terminal = False
    try:
        async with connector(websocket_url(connection, prompt_id), proxy=None, max_size=32 * 1024 * 1024,
                             max_queue=4, open_timeout=10) as upstream, live_frames(websocket, upstream) as frames:
            await websocket.send_json({"type": "connected", "submissionId": str(submission.id),
                                       "nodeTypes": exact_workflow_node_types(submission, config)})
            await upstream.send(json.dumps({"type": "feature_flags", "data": {"supports_preview_metadata": True}}))
            current = store.render_submission(project_id, generation_id, submission_id)
            await websocket.send_json({"type": "state", "status": current.job.status})
            async for raw in frames:
                event = parse_binary(raw, prompt_id) if isinstance(raw, bytes) else normalize_text(raw, prompt_id)
                if event is None:
                    continue
                if isinstance(event, dict) and event["type"] in {"terminal", "terminal-check"}:
                    # A terminal signal is only a reason to reconcile the exact
                    # watched job. The persisted exact status decides whether its
                    # observer can close; unscoped events never assume ownership.
                    await reconcile_submission(project_id, generation_id, submission_id, store=store, config=config)
                    current = store.render_submission(project_id, generation_id, submission_id)
                    if current.job.status in {"completed", "failed", "cancelled"}:
                        await websocket.send_json({"type": "terminal", "status": current.job.status})
                        await websocket.send_json({"type": "reconcile"})
                        terminal = True
                        break
                    await websocket.send_json({"type": "state", "status": current.job.status})
                    continue
                if isinstance(event, tuple):
                    await websocket.send_json(event[0])
                    await websocket.send_bytes(event[1])
                else:
                    await websocket.send_json(event)
    except (WebSocketDisconnect, RuntimeError):
        return
    except (WebSocketException, OSError, TimeoutError, httpx.RequestError):
        pass
    finally:
        if not terminal:
            with suppress(WebSocketDisconnect, RuntimeError):
                await websocket.send_json({"type": "disconnected"})
                await reconcile_submission(project_id, generation_id, submission_id, store=store, config=config)
                await websocket.send_json({"type": "reconcile"})
        with suppress(WebSocketDisconnect, RuntimeError):
            await websocket.close()
