"""Bounded exact-job reads and backend-only output import; never queues a workflow."""
import asyncio
import hashlib
import json
from pathlib import PurePosixPath
import re

import httpx

from miniconstruct.execution import ExecutionStore, ConfigNotFound, canonical_json
from miniconstruct.models.takes import RendererJobState, RendererOutput, TakeImport, timestamp
from miniconstruct.project_store import ProjectStore
from miniconstruct import take_store

MAX_JOB_BYTES = 2 * 1024 * 1024
MAX_OUTPUT_VALUES = 10000
MAX_OUTPUT_DEPTH = 16
MAX_OUTPUTS = 128
JOB_TIMEOUT = 30
DOWNLOAD_TIMEOUT = 600
TERMINAL = {"completed", "failed", "cancelled"}


class ReconciliationError(Exception):
    """Static public messages only, never raw renderer responses or exception text."""


def safe_component(value):
    return (isinstance(value, str) and 0 < len(value) <= 255
            and value not in {".", ".."} and value == value.strip(" .")
            and not re.search(r'[<>:"/\\|?*\[\]%\x00-\x1f\x7f]', value))


def descriptor(value):
    if not isinstance(value, dict):
        return None
    filename, folder, kind = value.get("filename"), value.get("subfolder"), value.get("type")
    if (not safe_component(filename) or not isinstance(folder, str) or len(folder) > 1024
            or not isinstance(kind, str) or kind not in {"output", "temp"}
            or (folder and not all(safe_component(part) for part in folder.split("/")))):
        return None
    if PurePosixPath(filename).suffix.lower().lstrip(".") not in take_store.VIDEO_TYPES:
        return None
    return {"filename": filename, "subfolder": folder, "type": kind}


def safe_node(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", value) else None


def extract_outputs(outputs, submission_id):
    """Walk only terminal outputs; deduplicate descriptors even across output nodes.

    Fail closed on traversal limits: a truncated set must never look like one video.
    Arbitrary metadata, URLs, strings and unsafe descriptors are not candidates.
    """
    found, visited = {}, 0

    def walk(value, depth, node=None):
        nonlocal visited
        visited += 1
        if visited > MAX_OUTPUT_VALUES or depth > MAX_OUTPUT_DEPTH:
            raise ReconciliationError("Output metadata exceeds discovery limits; use manual Take import.")
        if isinstance(value, dict):
            item = descriptor(value)
            if item:
                identity = hashlib.sha256(canonical_json([str(submission_id), item])).hexdigest()
                found.setdefault(identity, RendererOutput(id=identity, nodeId=node, **item))
                if len(found) > MAX_OUTPUTS:
                    raise ReconciliationError("Too many video outputs; use manual Take import.")
            for name, child in value.items():
                walk(child, depth + 1, safe_node(name) if depth == 0 else node)
        elif isinstance(value, list):
            for child in value:
                walk(child, depth + 1, node)

    walk(outputs, 0)
    return list(found.values())


def safe_time(value):
    return value if type(value) is int and 0 <= value <= 9999999999999 else None


def normalize_job(payload, submission, *, legacy=False):
    if legacy:
        payload = payload.get(str(submission.comfy_prompt_id))
        if not isinstance(payload, dict):
            return RendererJobState(status="unknown", lastCheckedAt=timestamp(),
                message="Exact prompt history is not available yet; queued/running state is unavailable on this server.")
        info = payload.get("status")
        info = info if isinstance(info, dict) else {}
        status = "failed" if info.get("status_str") == "error" else (
            "completed" if info.get("status_str") == "success" and info.get("completed") is True else "unknown")
    else:
        if payload.get("id") != str(submission.comfy_prompt_id):
            raise ReconciliationError("ComfyUI returned a different job identity; response ignored.")
        status = {"pending": "queued", "in_progress": "running", "completed": "completed",
                  "failed": "failed", "cancelled": "cancelled"}.get(str(payload.get("status")), "unknown")
    job = RendererJobState(status=status, lastCheckedAt=timestamp(),
        startedAt=safe_time(payload.get("execution_start_time")),
        completedAt=safe_time(payload.get("execution_end_time")))
    if status == "failed":
        job.message = "ComfyUI execution failed or was interrupted. No automatic retry was made."
        error = payload.get("execution_error")
        if isinstance(error, dict):
            job.failed_node_id = safe_node(error.get("node_id"))
    if status == "cancelled":
        job.message = "ComfyUI confirms this exact render was cancelled."
    if status == "completed":
        try:
            job.outputs = extract_outputs(payload.get("outputs", {}), submission.id)
        except ReconciliationError as exc:
            job.message = str(exc)
    return job


class ComfyJobClient:
    def __init__(self, connection, *, transport=None):
        self.base = connection.base_url
        # Same saved-Connection trust boundary as Send; no environment proxy or redirects.
        self.client = httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False,
            timeout=httpx.Timeout(30, connect=10), headers={"Accept-Encoding": "identity"})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.client.aclose()

    async def read_json(self, path):
        async with self.client.stream("GET", self.base + path) as response:
            # Do not consume error/redirect bodies, which may contain private data.
            if response.status_code != 200:
                return response.status_code, {}
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                raise ReconciliationError("Compressed ComfyUI job responses are unsupported.")
            raw = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=8192):
                raw.extend(chunk)
                if len(raw) > MAX_JOB_BYTES:
                    raise ReconciliationError("ComfyUI job response exceeds 2 MiB.")
            try:
                value = json.loads(raw)
            except (ValueError, UnicodeError, RecursionError):
                raise ReconciliationError("ComfyUI returned malformed job metadata.") from None
            if not isinstance(value, dict):
                raise ReconciliationError("ComfyUI returned invalid job metadata.")
            return 200, value

    async def job(self, submission):
        async with asyncio.timeout(JOB_TIMEOUT):
            status, payload = await self.read_json(f"/api/jobs/{submission.comfy_prompt_id}")
            if status in {404, 405, 501}:
                status, payload = await self.read_json(f"/history/{submission.comfy_prompt_id}")
                if status == 200:
                    job = normalize_job(payload, submission, legacy=True)
                    if job.status == "unknown":
                        queue = await self.queue_statuses()
                        job.status = queue.get(str(submission.comfy_prompt_id), "unknown")
                    return job
            elif status == 200:
                return normalize_job(payload, submission)
            raise ReconciliationError(f"ComfyUI exact job lookup unavailable (HTTP {status}).")

    async def queue_statuses(self):
        """Positive exact prompt identities only; absence never proves terminal."""
        status, payload = await self.read_json("/queue")
        if status != 200 or not all(isinstance(payload.get(key), list) for key in ("queue_running", "queue_pending")):
            raise ReconciliationError("ComfyUI queue discovery is unavailable.")
        result = {}
        for key, state in (("queue_pending", "queued"), ("queue_running", "running")):
            for row in payload[key]:
                if not isinstance(row, list) or len(row) < 2 or not isinstance(row[1], str):
                    raise ReconciliationError("ComfyUI returned invalid queue identities.")
                result[row[1]] = state
        return result

    async def import_output(self, store, project_id, generation_id, submission, candidate):
        fields = descriptor(candidate.model_dump())
        if fields is None:
            raise ReconciliationError("Stored output descriptor is unsafe; use manual Take import.")
        async with asyncio.timeout(DOWNLOAD_TIMEOUT):
            async with self.client.stream("GET", self.base + "/view", params=fields) as response:
                if response.status_code != 200:
                    raise ReconciliationError(f"ComfyUI output retrieval failed (HTTP {response.status_code}).")
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise ReconciliationError("Compressed ComfyUI video responses are unsupported.")
                extension = PurePosixPath(candidate.filename).suffix.lower().lstrip(".")
                mime = response.headers.get("content-type", "application/octet-stream").split(";")[0].strip().lower()
                allowed = {take_store.VIDEO_TYPES[extension], "application/octet-stream"}
                allowed.update({"m4v": {"video/x-m4v"}, "avi": {"video/avi"},
                                "mkv": {"video/matroska"}}.get(extension, set()))
                if mime not in allowed:
                    raise ReconciliationError("ComfyUI output MIME type does not match the video extension.")
                length = response.headers.get("content-length")
                if length is not None and (not re.fullmatch(r"[0-9]{1,20}", length)
                        or int(length) > take_store.MAX_TAKE_BYTES):
                    raise ReconciliationError("ComfyUI output size is invalid or exceeds 8 GiB.")
                provenance = TakeImport(sourceKind="comfyui_submission", renderSubmissionId=submission.id)
                async def chunks():
                    size = 0
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        size += len(chunk)
                        yield chunk
                    if length is not None and size != int(length):
                        raise ReconciliationError("ComfyUI output download was incomplete; no Take was imported.")
                return await store.import_take(project_id, generation_id, chunks(),
                    candidate.filename, provenance, renderer_output_id=candidate.id)


async def discover_foreground(project_id, generation_id=None, submission_id=None, *, cursor=0,
                              store=None, config=None, transport=None):
    """One foreground pass: <=4 recorded Connections and <=3 exact job reads.

    Queue metadata is bounded by the normal response limit. Only exact recorded
    prompt IDs are persisted; no output downloads or renderer actions occur here.
    Rotate Connections so a large Project cannot permanently hide older work.
    """
    store, config = store or ProjectStore(), config or ExecutionStore()
    records = store.active_render_submissions(project_id)
    connections = list(dict.fromkeys(s.connection_id for _, s in records))
    selected = [connections[(cursor + i) % len(connections)] for i in range(min(4, len(connections)))]
    errors, observations, queue_active = [], [], set()
    async def discover(connection_id):
        try:
            connection = config.get("connections", connection_id)
            async with ComfyJobClient(connection, transport=transport) as client:
                async with asyncio.timeout(10):
                    statuses = await client.queue_statuses()
                for gid, submission in records:
                    if submission.connection_id != connection_id:
                        continue
                    status = statuses.get(str(submission.comfy_prompt_id), "unknown")
                    if status in {"running", "queued"}:
                        queue_active.add((gid, str(submission.id)))
                    if status != submission.job.status:
                        observations.append((gid, submission, RendererJobState(status=status, lastCheckedAt=timestamp())))
        except (ConfigNotFound, httpx.RequestError, TimeoutError, ReconciliationError):
            errors.append("ComfyUI foreground discovery is temporarily unavailable; recorded job status is retained.")
    await asyncio.gather(*(discover(identity) for identity in selected))
    if observations:
        store.update_foreground_jobs(project_id, observations)
    summary = store.foreground_renders(project_id, generation_id, submission_id, include_revision=True)
    # Fresh positive queue identity already establishes active status. Do not put
    # its preview behind redundant job/history reads. Absence still requires exact
    # confirmation, including terminal truth for a retained explicit watcher.
    checked = set()
    # Unresolved records stay eligible for exact background checks even
    # though they are no longer automatic live-preview candidates.
    unresolved = [{"generationId": gid, "id": str(s.id), "job": {"status": s.job.status}}
                  for gid, s in records if s.connection_id in selected]
    for _ in range(3):
        rows = [summary["watched"], summary["candidate"], *unresolved]
        row = next((r for r in rows if r and r["job"]["status"] not in TERMINAL
                    and (r["generationId"], r["id"]) not in checked | queue_active), None)
        if row is None:
            break
        checked.add((row["generationId"], row["id"]))
        submission = store.render_submission(project_id, row["generationId"], row["id"])
        try:
            connection = config.get("connections", submission.connection_id)
            async with ComfyJobClient(connection, transport=transport) as client:
                async with asyncio.timeout(10):
                    job = await client.job(submission)
            current = store.update_render_job(project_id, row["generationId"], submission.id, job, expected_job=submission.job)
            store.reconcile_native_status(project_id, row["generationId"], current)
        except (ConfigNotFound, httpx.RequestError, TimeoutError, ReconciliationError):
            errors.append("Exact ComfyUI foreground status is temporarily unavailable; no terminal state was inferred.")
        summary = store.foreground_renders(project_id, generation_id, submission_id, include_revision=True)
    return {**summary, "discoveryCursor": (cursor + len(selected)) % max(1, len(connections)),
            "discoveryError": errors[0] if errors else None}


async def reconcile_submission(project_id, generation_id, submission_id, *, output_id=None,
                               store=None, config=None, transport=None):
    store, config = store or ProjectStore(), config or ExecutionStore()
    submission = store.render_submission(project_id, generation_id, submission_id)
    store.reconcile_native_status(project_id, generation_id, submission)
    error = None
    try:
        # Resolve only the recorded Connection, even after browser restart.
        connection = config.get("connections", submission.connection_id)
        async with ComfyJobClient(connection, transport=transport) as client:
            if submission.job.status not in TERMINAL:
                job = await client.job(submission)
                submission = store.update_render_job(project_id, generation_id, submission.id, job, expected_job=submission.job)
            store.reconcile_native_status(project_id, generation_id, submission)
            if output_id is not None:
                candidate = next((o for o in submission.job.outputs if o.id == output_id), None)
                if candidate is None or submission.job.status != "completed":
                    raise ReconciliationError("Output does not belong to this completed RenderSubmission.")
            else:
                candidate = submission.job.outputs[0] if submission.job.status == "completed" and len(submission.job.outputs) == 1 else None
            if candidate and not candidate.imported_take_id:
                await client.import_output(store, project_id, generation_id, submission, candidate)
    except ConfigNotFound:
        error = "The recorded ComfyUI Connection is unavailable; restore it to reconcile this job."
    except (httpx.TimeoutException, TimeoutError):
        error = "ComfyUI reconciliation timed out. Refresh render status to check again."
    except httpx.RequestError:
        error = "Could not read from the recorded ComfyUI Connection. Refresh render status to check again."
    except ReconciliationError as exc:
        error = str(exc)
    if error:
        store.update_render_job(project_id, generation_id, submission.id,
            RendererJobState(status="unknown", lastCheckedAt=timestamp(), message=error), expected_job=submission.job)
    return {**store.generation_outputs(project_id, generation_id), "reconciliationError": error}
