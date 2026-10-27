"""Explicit handoff and immutable acceptance provenance. Reconciliation is separate."""
from contextlib import ExitStack
from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
import math
from pathlib import PurePosixPath
import re
import secrets
from threading import Lock
from typing import Any, Literal
from uuid import UUID, uuid4

import httpx
from pydantic import Field

from miniconstruct.execution import ExecutionStore, InvalidBindings, StrictModel, _LOCK as _CONFIG_LOCK
from miniconstruct.comfy_media import STANDARD_MEDIA_LOADERS
from miniconstruct.project_store import ProjectStore, ProjectStoreError, AssetPayloadError
from miniconstruct.models.takes import RenderSubmission, RendererJobState, TakePromptSnapshot
from miniconstruct.workflow_preparation import PrepareRequest, prepare_workflow
from miniconstruct.take_store import transaction
from miniconstruct.send_timing import count, profile_send, stage
from miniconstruct.local_staging import input_root, verify_local, FALLBACK

MAX_RESPONSE_BYTES = 64 * 1024

# Receipts are location hints, never authority. Bound memory and serialize only
# identical staging keys (striped locks also bound synchronization state).
_STAGED = OrderedDict()
_STAGED_LOCK = Lock()
_STAGING_LOCKS = [Lock() for _ in range(64)]
_MAX_STAGED = 512


class ExecutionSendRequest(PrepareRequest):
    expected_execution_fingerprint: str | None = Field(default=None, alias="expectedExecutionFingerprint", pattern=r"^[0-9a-f]{64}$")
    connection_id: UUID = Field(alias="connectionId")


class UploadedInputReceipt(StrictModel):
    requirement_id: str = Field(alias="requirementId")
    semantic_label: str = Field(alias="semanticLabel")
    filename: str


class ExecutionSendResult(ExecutionSendRequest):
    continuation_execution_id: UUID | None = Field(default=None, alias="continuationExecutionId")
    profile_name: str = Field(alias="profileName")
    connection_name: str = Field(alias="connectionName")
    prompt_id: UUID = Field(alias="promptId")
    queue_number: float | None = Field(default=None, alias="queueNumber")
    renderer_seed: int | None = Field(default=None, alias="rendererSeed")
    node_errors: dict[str, Any] = Field(default_factory=dict, alias="nodeErrors")
    uploaded_inputs: list[UploadedInputReceipt] = Field(alias="uploadedInputs")
    submitted: Literal[True] = True
    render_submission_id: UUID | None = Field(default=None, alias="renderSubmissionId")
    provenance_warning: str | None = Field(default=None, alias="provenanceWarning")
    staging_warning: str | None = Field(default=None, alias="stagingWarning")
    project_fingerprint: str | None = Field(default=None, alias="projectFingerprint")
    project_edit_fingerprint: str | None = Field(default=None, alias="projectEditFingerprint")
    project_updated_at: str | None = Field(default=None, alias="projectUpdatedAt")
    retry_of_submission_id: UUID | None = Field(default=None, alias="retryOfSubmissionId")


class SendError(Exception):
    """Safe public message; never includes a raw request, local path or response."""


def upload_filename(payload: dict) -> str:
    if not isinstance(payload, dict) or payload.get("type") != "input":
        raise SendError("Invalid ComfyUI upload response: expected input type.")
    name, folder = payload.get("name"), payload.get("subfolder")
    def component(value):
        return (isinstance(value, str) and 0 < len(value) <= 255
                and value not in {".", ".."} and value == value.strip(" .")
                and not re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?", value, re.I)
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
        self.connection_key = connection.model_dump_json()
        self.local_root, self.staging_warning = None, None
        try:
            self.local_root = input_root(connection.local_input_directory)
        except (OSError, ValueError, RuntimeError):
            self.staging_warning = FALLBACK
        self.client = httpx.Client(transport=transport, trust_env=False, follow_redirects=False,
                                   timeout=httpx.Timeout(120, connect=10))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.client.close()

    def _post(self, path, **kwargs):
        count("comfyRequests")
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
            count("uploads")
            count("uploadBytes", stream.seek(0, 2))
            stream.seek(0)
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

    def staged_input(self, stream, name, mime, digest):
        size = stream.seek(0, 2)
        stream.seek(0)
        key = (self.connection_key, digest, size, name, mime)
        with _STAGING_LOCKS[hash(key) % len(_STAGING_LOCKS)]:
            with _STAGED_LOCK:
                receipt = _STAGED.get(key)
            if receipt is not None:
                filename, locally_mapped = receipt
                usable, locally_mapped = self.verify_staged(filename, digest, size, locally_mapped)
                if usable:
                    with _STAGED_LOCK:
                        if key in _STAGED:
                            _STAGED[key] = (filename, locally_mapped)
                            _STAGED.move_to_end(key)
                    count("stagedReuses")
                    return filename
                with _STAGED_LOCK:
                    _STAGED.pop(key, None)
            filename = self.upload_input(stream, name, mime)
            mapped = False
            if self.local_root is not None:
                with stage("verifyLocal"):
                    mapped = verify_local(self.local_root, filename, digest, size)
                if mapped is not True:
                    self.staging_warning = ("Uploaded input could not be verified under the configured local ComfyUI input root. "
                                            "Check the Connection mapping; verified HTTP staging remains available.")
                    with stage("verifyRemote"):
                        if not self.verify_input(filename, digest, size):
                            raise SendError("Uploaded ComfyUI media could not be verified; no prompt was submitted.")
            with _STAGED_LOCK:
                _STAGED[key] = (filename, mapped is True)
                _STAGED.move_to_end(key)
                while len(_STAGED) > _MAX_STAGED:
                    _STAGED.popitem(last=False)
            return filename

    def verify_staged(self, filename, digest, size, locally_mapped):
        folder, _, name = filename.rpartition("/")
        upload_filename({"name": name, "subfolder": folder, "type": "input"})
        if self.local_root is not None:
            with stage("verifyLocal"):
                usable = verify_local(self.local_root, filename, digest, size)
            if usable is True:
                return True, True
            if usable is False and locally_mapped:
                return False, True
            # A root that never proved this location may be a wrong mapping.
            # Keep HTTP verification, but allow exact local recovery later.
            self.staging_warning = FALLBACK
        with stage("verifyRemote"):
            return self.verify_input(filename, digest, size), locally_mapped

    def verify_input(self, filename, digest, size):
        """Standard /view serves original bytes without preview/channel options.

        HEAD, ETag and size alone do not prove identity. Never use conditional GET
        or HTTP caches; stream and hash the complete representation every Send.
        This establishes current bytes, not immutability until renderer execution.
        """
        folder, _, name = filename.rpartition("/")
        count("comfyRequests")
        count("remoteVerificationRequests")
        try:
            with self.client.stream("GET", self.base + "/view",
                    params={"filename": name, "subfolder": folder, "type": "input",
                            "mc_verify": uuid4().hex},
                    headers={"Cache-Control": "no-cache, no-store", "Accept-Encoding": "identity"}) as response:
                if response.status_code != 200:
                    return False
                received, hasher = 0, hashlib.sha256()
                for chunk in response.iter_bytes(chunk_size=64 * 1024):
                    received += len(chunk)
                    count("remoteVerificationBytes", len(chunk))
                    if received > size:
                        return False
                    hasher.update(chunk)
                return received == size and hasher.hexdigest() == digest
        except httpx.RequestError:
            raise SendError("Could not verify staged ComfyUI media; no prompt was submitted. Check the Connection and try again.") from None

    def queue_prompt(self, graph, prompt_id):
        count("promptRequests")
        unknown = (f"Submission status is unknown. Expected prompt ID: {prompt_id}. "
                   "Check ComfyUI before sending again. No automatic retry was made.")
        try:
            status, payload = self._post("/prompt", json={"prompt": graph, "prompt_id": str(prompt_id),
                                                       "client_id": str(prompt_id)})
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
        widget = STANDARD_MEDIA_LOADERS.get(node["class_type"])
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


_RETRY_LOCK = Lock()
_ACTIVE_RETRIES: set[tuple[str, str, str]] = set()


def _retry_compatible(prepared, source):
    if prepared.workflow_fingerprint != source.workflow_fingerprint:
        raise InvalidBindings(["The Workflow Profile's imported workflow changed after this render. Use normal Prepare / Send."])
    if prepared.profile_fingerprint != source.profile_fingerprint:
        raise InvalidBindings(["The Workflow Profile changed after this render. Use normal Prepare / Send."])
    if source.workflow_control_fingerprint and prepared.workflow_control_fingerprint != source.workflow_control_fingerprint:
        raise InvalidBindings(["The Workflow Control declaration changed after this render. Use normal Prepare / Send."])
    snapshot = TakePromptSnapshot.capture(prepared.selected_prompt, prepared.mode)
    if (snapshot.prompt != source.prompt_snapshot.prompt or snapshot.sha256 != source.prompt_snapshot.sha256
            or snapshot.mode != source.prompt_snapshot.mode or snapshot.source_fingerprint != source.prompt_snapshot.source_fingerprint
            or snapshot.reference_map != source.prompt_snapshot.reference_map):
        raise InvalidBindings(["This rendering attempt no longer matches the selected execution prompt. Use normal Prepare / Send."])
    if source.execution_fingerprint and prepared.execution_fingerprint != source.execution_fingerprint:
        raise InvalidBindings(["This rendering attempt no longer matches the current execution setup. Use normal Prepare / Send."])
    if source.renderer_seed is not None and not prepared.renderer_seed_controlled:
        raise InvalidBindings(["The Workflow Profile no longer controls the recorded renderer seed. Use normal Prepare / Send."])


def retry_generation(project_id, generation_id, submission_id, *, store=None, config=None, transport=None):
    store, config = store or ProjectStore(), config or ExecutionStore()
    key = (str(project_id), str(generation_id), str(submission_id))
    with _RETRY_LOCK:
        if key in _ACTIVE_RETRIES:
            raise InvalidBindings(["Retry is already being submitted."])
        _ACTIVE_RETRIES.add(key)
    try:
        source = store.render_submission(project_id, generation_id, submission_id)
        if source.job.status not in {"failed", "cancelled"}:
            raise InvalidBindings(["Retry exact attempt is available only for a failed or cancelled exact job. Recover this job first if its status is unknown."])
        if any(item["retryOfSubmissionId"] == str(source.id) for item in store.generation_outputs(project_id, generation_id)["renderSubmissions"]):
            raise InvalidBindings(["This attempt was already retried. Use its new submission for further recovery or retry."])
        from miniconstruct.execution import ConfigNotFound
        try:
            profile = config.get("profiles", source.workflow_profile_id)
            config.get("connections", source.connection_id)
        except ConfigNotFound:
            raise InvalidBindings(["The recorded Connection or Workflow Profile is unavailable. Use normal Prepare / Send."]) from None
        prepared = prepare_workflow(store, str(project_id), str(generation_id), profile, inspection=False,
                                    retry_control_values=source.workflow_control_values if source.workflow_control_fingerprint else None)
        _retry_compatible(prepared, source)
        if source.execution_fingerprint is None:
            original_native = next((record for record in store.continuation_status(
                str(project_id), str(generation_id))["executions"]
                if record.render_submission_id == source.id), None)
            if profile.native_continuation and original_native is None:
                raise InvalidBindings(["The original native continuation attempt is unavailable. Use normal Prepare / Send."])
            if original_native and original_native.execution_fingerprint != prepared.execution_fingerprint:
                raise InvalidBindings(["The native predecessor or execution setup changed after this render. Use normal Prepare / Send."])
        body = ExecutionSendRequest(projectId=project_id, generationId=generation_id,
                                    profileId=source.workflow_profile_id, connectionId=source.connection_id,
                                    expectedExecutionFingerprint=prepared.execution_fingerprint)
        return send_generation(body, store=store, config=config, transport=transport, _retry_source=source)
    finally:
        with _RETRY_LOCK:
            _ACTIVE_RETRIES.discard(key)


def _execution_change(prepared, fresh):
    """Bounded explanations only; never echo prompt text, media or native keys."""
    if fresh.selected_prompt != prepared.selected_prompt:
        return "the selected H3 prompt or its provenance changed"
    if fresh.profile_fingerprint != prepared.profile_fingerprint:
        return "the Workflow Profile changed"
    if fresh.asset_requirements != prepared.asset_requirements:
        return "Reference payload authority or loader mapping changed"
    if fresh.renderer_seed_settings != prepared.renderer_seed_settings:
        return "renderer seed settings changed"
    controls = lambda value: {item["id"]: item["value"] for item in value.workflow_controls}
    if controls(fresh) != controls(prepared):
        return "effective Workflow Controls changed"
    native = lambda value: value.native_continuation.model_dump(
        exclude={"id", "save_key", "storage_namespace"}) if value.native_continuation else None
    if native(fresh) != native(prepared):
        return "native predecessor authority or continuation settings changed"
    if fresh.current_fingerprint != prepared.current_fingerprint:
        return "saved execution authoring or Reference authority changed"
    return "Sequence order or visual handoff authority changed"


@profile_send
def send_generation(body: ExecutionSendRequest, *, store=None, config=None, transport=None, _retry_source=None):
    store, config = store or ProjectStore(), config or ExecutionStore()
    with stage("loadConfiguration"):
        generated_state_epoch = store.generated_state_epoch(str(body.project_id))
        profile = config.get("profiles", body.profile_id)
        connection = config.get("connections", body.connection_id)
    try:
        with stage("reconstruct"):
            prepared = prepare_workflow(store, str(body.project_id), str(body.generation_id), profile, inspection=False,
                                        verify_payloads=False,
                                        retry_control_values=_retry_source.workflow_control_values if _retry_source and _retry_source.workflow_control_fingerprint else None)
    except OSError:
        raise InvalidBindings(["Saved Project payloads could not be read. Reattach missing files and save."]) from None
    prompt_snapshot = TakePromptSnapshot.capture(prepared.selected_prompt, prepared.mode)
    if _retry_source is not None:
        _retry_compatible(prepared, _retry_source)
    if body.expected_execution_fingerprint and body.expected_execution_fingerprint != prepared.execution_fingerprint:
        raise InvalidBindings(["Prepared execution is stale; Prepare again to review the current continuation lineage and settings."])
    if prepared.native_continuation and prepared.native_continuation.consume:
        from miniconstruct.native_continuation import resolve_native
        source = resolve_native(store, str(body.project_id), str(body.generation_id), profile)["record"]
        if source is None or source.connection_id != connection.id:
            raise InvalidBindings(["Native predecessor state belongs to another ComfyUI Connection. Choose its Connection or use a fallback strategy."])
    with stage("graphPreflight"):
        graph = transport_preflight(prepared, profile)
    receipts, uploaded = [], {}
    with ExitStack() as stack:
        payloads = []
        # Verify every canonical payload before the first external side effect.
        # Hold the verified handles through upload so path replacement cannot swap files.
        try:
            with stage("verifyBeforeUpload"):
                verified_payloads = stack.enter_context(store.open_execution_payloads(
                    str(body.project_id), str(body.generation_id), prepared.asset_requirements))
        except OSError:
            raise InvalidBindings(["Saved payload unavailable. Reattach and save."]) from None
        except AssetPayloadError:
            raise InvalidBindings(["Saved payload unavailable, changed or corrupt. Reattach it and Prepare again."]) from None
        for r, stream, mime in verified_payloads:
            payloads.append((r, stream, mime, deterministic_name(r, mime)))
        client = stack.enter_context(ComfyClient(connection, transport=transport))
        for r, stream, mime, name in payloads:
            key = (r.content_sha256, name)
            if key not in uploaded:
                try:
                    with stage("upload"):
                        uploaded[key] = client.staged_input(stream, name, mime, r.content_sha256)
                except SendError as exc:
                    raise SendError(f"{r.semantic_label}: {exc} No prompt was submitted. Earlier uploaded inputs may remain for reuse.") from None
            filename = uploaded[key]
            graph[r.node_id]["inputs"][r.input_name] = filename
            receipts.append(UploadedInputReceipt(requirementId=r.id, semanticLabel=r.semantic_label, filename=filename))
        prompt_id = uuid4()
        # Reopen and hash current canonical paths, including same-size tampering
        # or path replacement during uploads. Bulk I/O stays outside the lock.
        with stage("verifyAfterUpload"):
            with store.open_execution_payloads(str(body.project_id), str(body.generation_id), prepared.asset_requirements):
                pass
        # Metadata-only compiler under both canonical locks closes the previous
        # check/queue gap. Project -> configuration is the existing native resolver
        # lock order. Only /prompt acceptance holds the locks, never file uploads.
        with stage("authorityAndQueue"), transaction(), _CONFIG_LOCK:
            current_profile = config.get("profiles", body.profile_id)
            current_connection = config.get("connections", body.connection_id)
            if current_connection != connection:
                raise InvalidBindings(["ComfyUI Connection changed during upload; Prepare again. No prompt was submitted."])
            with stage("revalidate"):
                fresh = prepare_workflow(store, str(body.project_id), str(body.generation_id), current_profile, inspection=False,
                                         verify_payloads=False, revalidate=prepared,
                                         retry_control_values=_retry_source.workflow_control_values if _retry_source and _retry_source.workflow_control_fingerprint else None)
            if fresh.execution_fingerprint != prepared.execution_fingerprint or fresh.selected_prompt != prepared.selected_prompt:
                raise InvalidBindings([f"Execution changed during upload: {_execution_change(prepared, fresh)}. "
                                       "Prepare again. No prompt was submitted."])
            if _retry_source is not None:
                _retry_compatible(fresh, _retry_source)
            with stage("finalizeGraph"):
                seed_binding = next((binding for binding in current_profile.bindings
                                     if binding.source.kind == "renderer_seed"), None)
                seed_settings = fresh.renderer_seed_settings
                renderer_seed = (_retry_source.renderer_seed if _retry_source is not None
                                 else secrets.randbelow(2**31) if seed_settings.mode == "random"
                                 else seed_settings.fixed_seed if seed_settings.mode == "fixed" else None)
                if renderer_seed is not None:
                    graph[seed_binding.node_id]["inputs"][seed_binding.input_name] = renderer_seed
            with stage("queueAcceptance"):
                number, errors = client.queue_prompt(graph, prompt_id)
            count("accepted")
    submission = RenderSubmission(connectionId=body.connection_id, workflowProfileId=body.profile_id,
                                  workflowFingerprint=prepared.workflow_fingerprint,
                                  profileFingerprint=prepared.profile_fingerprint, comfyPromptId=prompt_id,
                                  rendererSeed=renderer_seed,
                                  executionFingerprint=prepared.execution_fingerprint,
                                  workflowControlValues={item["id"]: item["value"] for item in prepared.workflow_controls},
                                  workflowControlFingerprint=prepared.workflow_control_fingerprint,
                                  retryOfSubmissionId=_retry_source.id if _retry_source else None,
                                  promptSnapshot=prompt_snapshot, job=RendererJobState(status="queued"))
    warning, fingerprint, submission_id, continuation_id = None, None, None, None
    revision = {}
    try:
        with stage("persist"):
            output_state = store.record_render_submission(str(body.project_id), str(body.generation_id), submission,
                                                         expected_generated_state_epoch=generated_state_epoch)
            fingerprint, submission_id = output_state["projectFingerprint"], submission.id
            revision = {key: output_state[key] for key in ("projectEditFingerprint", "projectUpdatedAt")}
            if prepared.native_continuation:
                record = store.record_continuation(str(body.project_id), str(body.generation_id), prepared.native_continuation,
                                                   submission, prepared.current_fingerprint, prepared.execution_fingerprint)
                continuation_id = record.id
    except (OSError, ValueError, ProjectStoreError):
        # Acceptance cannot be rolled back. Do not present a persistence failure as a failed Send.
        warning = "ComfyUI accepted this job, but its Project provenance could not be saved. Keep the Prompt ID; do not resend automatically."
    return ExecutionSendResult(continuationExecutionId=continuation_id, renderSubmissionId=submission_id, provenanceWarning=warning, projectFingerprint=fingerprint,
                               stagingWarning=client.staging_warning,
                               **revision,
                               retryOfSubmissionId=_retry_source.id if _retry_source else None,
                               **body.model_dump(), profileName=profile.name, connectionName=connection.name,
                               promptId=prompt_id, queueNumber=number, rendererSeed=renderer_seed,
                               nodeErrors=errors, uploadedInputs=receipts)
