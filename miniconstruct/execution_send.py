"""Explicit F4 handoff. No retries, job store, progress or output retrieval."""
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
import math
from pathlib import PurePosixPath
import re
from typing import Any, Literal
from uuid import UUID, uuid4

import httpx
from pydantic import Field

from miniconstruct.execution import ExecutionStore, InvalidBindings, StrictModel
from miniconstruct.project_store import ProjectStore
from miniconstruct.workflow_preparation import PrepareRequest, prepare_workflow

MAX_RESPONSE_BYTES = 64 * 1024


class ExecutionSendRequest(PrepareRequest):
    connection_id: UUID = Field(alias="connectionId")


class UploadedInputReceipt(StrictModel):
    requirement_id: str = Field(alias="requirementId")
    semantic_label: str = Field(alias="semanticLabel")
    filename: str


class ExecutionSendResult(ExecutionSendRequest):
    profile_name: str = Field(alias="profileName")
    connection_name: str = Field(alias="connectionName")
    prompt_id: UUID = Field(alias="promptId")
    queue_number: float | None = Field(default=None, alias="queueNumber")
    node_errors: dict[str, Any] = Field(default_factory=dict, alias="nodeErrors")
    uploaded_inputs: list[UploadedInputReceipt] = Field(alias="uploadedInputs")
    submitted: Literal[True] = True


class SendError(Exception):
    """Safe public message; never includes a raw request, local path or response."""


def upload_filename(payload: dict) -> str:
    if not isinstance(payload, dict) or payload.get("type") != "input":
        raise SendError("Invalid ComfyUI upload response: expected input type.")
    name, folder = payload.get("name"), payload.get("subfolder")
    def component(value):
        return (isinstance(value, str) and 0 < len(value) <= 255
                and value not in {".", ".."} and value == value.strip(" .")
                and not re.search(r'[<>:"/\\|?*\[\]%\x00-\x1f\x7f]', value))
    if not component(name) or not isinstance(folder, str) or len(folder) > 1024:
        raise SendError("Invalid ComfyUI upload filename or subfolder.")
    folder = folder.replace("\\", "/")
    if folder and not all(component(part) for part in folder.split("/")):
        raise SendError("Unsafe ComfyUI upload subfolder.")
    return f"{folder}/{name}" if folder else name


def deterministic_name(requirement, mime: str) -> str:
    extensions = {
        "image": {"png", "jpg", "jpeg", "webp", "gif", "bmp", "tif", "tiff", "avif"},
        "audio": {"wav", "mp3", "flac", "ogg", "opus", "m4a", "aac", "aiff", "wma"},
        "video": {"mp4", "webm", "mov", "mkv", "avi", "m4v", "mpeg", "mpg"},
    }
    extension = PurePosixPath(requirement.filename).suffix.lower().lstrip(".")
    if extension not in extensions[requirement.asset_kind]:
        extension = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp",
                     "audio/mpeg": "mp3", "audio/wav": "wav", "audio/x-wav": "wav",
                     "video/mp4": "mp4", "video/quicktime": "mov", "video/webm": "webm"}.get(mime, "bin")
    return f"mc-{requirement.content_sha256}.{extension}"


def node_error_summary(value) -> dict:
    """Keep node IDs and bounded validation categories, excluding echoed inputs/tracebacks."""
    if not isinstance(value, dict):
        raise SendError("Invalid ComfyUI node_errors response.")
    result = {}
    for node, details in list(value.items())[:32]:
        if not re.fullmatch(r"[\w.-]{1,80}", node) or not isinstance(details, dict):
            continue
        errors = details.get("errors", [])
        if not isinstance(errors, list):
            continue
        categories = [e.get("type") for e in errors[:8] if isinstance(e, dict)]
        result[node] = {"errors": [{"type": c} for c in categories
                                    if isinstance(c, str) and re.fullmatch(r"[a-z_]{1,80}", c)]}
    return result


class ComfyClient:
    """Sync HTTPX multipart streams file handles; FastAPI runs Send in its worker pool."""
    def __init__(self, connection, *, transport=None):
        self.base = connection.base_url
        self.client = httpx.Client(transport=transport, trust_env=False, follow_redirects=False,
                                   timeout=httpx.Timeout(120, connect=10))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.client.close()

    def _post(self, path, **kwargs):
        with self.client.stream("POST", self.base + path, **kwargs) as response:
            raw = bytearray()
            for chunk in response.iter_bytes(chunk_size=8192):
                raw.extend(chunk)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise SendError("ComfyUI response exceeds 64 KiB.")
            if path == "/upload/image" and response.status_code != 200:
                return response.status_code, {}
            try:
                payload = json.loads(raw)
            except (ValueError, UnicodeError, RecursionError):
                raise SendError(f"ComfyUI returned non-JSON or malformed JSON (HTTP {response.status_code}).") from None
            if not isinstance(payload, dict):
                raise SendError("ComfyUI returned an invalid response object.")
            return response.status_code, payload

    def upload_input(self, stream, name, mime):
        try:
            status, payload = self._post("/upload/image", files={"image": (name, stream, mime)},
                                         data={"type": "input", "subfolder": "miniconstruct", "overwrite": "false"})
            if status != 200:
                raise SendError(f"ComfyUI upload rejected (HTTP {status})."
                                + (" Increase ComfyUI's upload size limit or use a smaller file." if status == 413 else ""))
            return upload_filename(payload)
        except httpx.TimeoutException:
            raise SendError("ComfyUI upload timed out; no prompt was submitted.") from None
        except httpx.RequestError:
            raise SendError("Could not upload to ComfyUI; check the Connection and server. No prompt was submitted.") from None
        except OSError:
            raise SendError("Could not read the saved input file during upload. Reattach it and save.") from None

    def queue_prompt(self, graph, prompt_id):
        unknown = (f"Submission status is unknown. Expected prompt ID: {prompt_id}. "
                   "Check ComfyUI before sending again. No automatic retry was made.")
        try:
            status, payload = self._post("/prompt", json={"prompt": graph, "prompt_id": str(prompt_id)})
            if status == 400:
                summary = node_error_summary(payload.get("node_errors", {}))
                raise InvalidBindings(["ComfyUI rejected prompt validation. Check the Workflow Profile and installed nodes. "
                                       + json.dumps(summary)])
            if 400 <= status < 500:
                raise InvalidBindings([f"ComfyUI rejected submission (HTTP {status})."])
            if status != 200 or payload.get("prompt_id") != str(prompt_id):
                raise SendError("Invalid queue acceptance response.")
            number = payload.get("number")
            if number is not None and (type(number) not in (int, float) or not math.isfinite(number)):
                raise SendError("Invalid queue number.")
            return number, node_error_summary(payload.get("node_errors", {}))
        except (httpx.RequestError, SendError, ValueError, OverflowError):
            raise SendError(unknown) from None


def transport_preflight(prepared, profile):
    bound = {(b.node_id, b.input_name) for b in profile.bindings if b.source.kind == "reference_asset"}
    for node_id, node in profile.workflow.items():
        widget = {"LoadImage": "image", "LoadVideo": "file", "LoadAudio": "audio"}.get(node["class_type"])
        if widget and (node_id, widget) not in bound and node["inputs"].get(widget):
            raise InvalidBindings([f"{node['class_type']} node {node_id} still uses an imported workflow file "
                                   "and is not configured as MiniConstruct capacity. Bind it in the Workflow Profile "
                                   "or remove it before sending."])
    graph = deepcopy(prepared.graph)
    markers = []
    def walk(value, key=""):
        # Transport rejects actual private values, while numeric token counts and
        # arbitrary ordinary constants remain unchanged (inspection redacts more).
        if (isinstance(value, str) and (";base64," in value or value.startswith(("/", "\\\\"))
                or re.match(r"^[A-Za-z]:[\\/]", value)
                or (value and re.fullmatch(r"(?:api[_-]?key|authorization|password|secret|(?:access[_-]?|auth[_-]?)?token)", key, re.I)))):
            raise InvalidBindings(["Workflow contains a private path, encoded payload or secret constant. "
                                   "Remove it from the Workflow Profile before sending."])
        if isinstance(value, dict):
            if key in {"mediaPayload", "media_payload"} or {"sha256", "byteLength"} <= value.keys():
                raise InvalidBindings(["Workflow contains a Project payload descriptor. Remove it before sending."])
            if "$assetRequirement" in value:
                markers.append(value)
            for child_key, child in value.items():
                walk(child, child_key)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(graph)
    expected = [{"$assetRequirement": r.id} for r in prepared.asset_requirements]
    if sorted(markers, key=str) != sorted(expected, key=str):
        raise InvalidBindings(["Unresolved or unexpected active asset requirement. Prepare again."])
    for requirement in prepared.asset_requirements:
        if graph[requirement.node_id]["inputs"].get(requirement.input_name) != {"$assetRequirement": requirement.id}:
            raise InvalidBindings(["Active asset requirement has an invalid loader target."])
    return graph


def send_generation(body: ExecutionSendRequest, *, store=None, config=None, transport=None):
    store, config = store or ProjectStore(), config or ExecutionStore()
    profile = config.get("profiles", body.profile_id)
    connection = config.get("connections", body.connection_id)
    try:
        prepared = prepare_workflow(store, str(body.project_id), str(body.generation_id), profile, inspection=False)
    except OSError:
        raise InvalidBindings(["Saved Project payloads could not be read. Reattach missing files and save."]) from None
    graph = transport_preflight(prepared, profile)
    receipts, uploaded = [], {}
    with ExitStack() as stack:
        payloads = []
        # Verify every canonical payload before the first external side effect.
        # Hold the verified handles through upload so path replacement cannot swap files.
        for r in prepared.asset_requirements:
            try:
                path, mime = store.execution_payload(str(body.project_id), str(body.generation_id),
                                                      r.asset_id, r.asset_kind, r.content_sha256)
                stream = stack.enter_context(path.open("rb"))
                if hashlib.file_digest(stream, "sha256").hexdigest() != r.content_sha256:
                    raise InvalidBindings([f"{r.semantic_label}: saved payload changed. Prepare again."])
                stream.seek(0)
            except OSError:
                raise InvalidBindings([f"{r.semantic_label}: saved payload unavailable. Reattach and save."]) from None
            payloads.append((r, stream, mime, deterministic_name(r, mime)))
        client = stack.enter_context(ComfyClient(connection, transport=transport))
        for r, stream, mime, name in payloads:
            key = (r.content_sha256, name)
            if key not in uploaded:
                try:
                    uploaded[key] = client.upload_input(stream, name, mime)
                except SendError as exc:
                    raise SendError(f"{r.semantic_label}: {exc} No prompt was submitted. Earlier uploaded inputs may remain for reuse.") from None
            filename = uploaded[key]
            graph[r.node_id]["inputs"][r.input_name] = filename
            receipts.append(UploadedInputReceipt(requirementId=r.id, semanticLabel=r.semantic_label, filename=filename))
        prompt_id = uuid4()
        number, errors = client.queue_prompt(graph, prompt_id)
    return ExecutionSendResult(**body.model_dump(), profileName=profile.name, connectionName=connection.name,
                               promptId=prompt_id, queueNumber=number, nodeErrors=errors, uploadedInputs=receipts)
