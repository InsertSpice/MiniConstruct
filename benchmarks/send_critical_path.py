"""Provider-free Send measurements. Run with .venv/Scripts/python benchmarks/send_critical_path.py.

Only synthetic temporary Projects and MockTransport are used. No production
Project, provider, renderer, credentials, prompt text or paths are reported.
Run the identical script on the baseline and revised working trees for comparison.
"""
from collections import defaultdict
from contextlib import contextmanager
import argparse
import asyncio
import base64
import hashlib
import io
import json
from pathlib import Path
import random
import subprocess
import sys
import tarfile
import tempfile
from threading import Event, Timer
from time import perf_counter
from unittest.mock import patch

repository = Path(__file__).resolve().parents[1]
baseline_tree = None
if "--baseline" in sys.argv:
    baseline_tree = tempfile.TemporaryDirectory(prefix="mc-send-baseline-")
    archive = subprocess.run(["git", "archive", "2d4e0e090cb5bfcad50bb4b618a765bd1b748dfe"],
                             cwd=repository, check=True, capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        bundle.extractall(baseline_tree.name, filter="data")
source = Path(baseline_tree.name) if baseline_tree else repository
sys.path[:0] = [str(source), str(source / "tests")]

import httpx
from PIL import Image
from fastapi.testclient import TestClient
from miniconstruct.app import app
import miniconstruct.api.execution_routes as routes
from miniconstruct import execution as ex, execution_send as send
from miniconstruct.models.workspace import Workspace, ReferenceAsset
from miniconstruct.models.takes import RendererJobState
from miniconstruct.project_store import ProjectStore
from miniconstruct.workflow_preparation import prepare_workflow
from test_workflow_preparation import capacity_profile, select
from test_validator import valid_ref_prompt


def measure(root, case, repeats):
    store, config = ProjectStore(root / "projects"), ex.ExecutionStore(root / "execution")
    remote_files = {}
    assets = []
    if case in {"pictures", "five_pictures_local", "five_pictures", "renderer_mutation", "slow_transport"}:
        rng = random.Random(17)
        for index in range(5 if case in {"five_pictures", "five_pictures_local"} else 3):
            image = Image.frombytes("RGB", (1024, 1024), rng.randbytes(1024 * 1024 * 3))
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            assets.append(ReferenceAsset(id=f"pic-{index}", kind="image", filename=f"pic-{index}.png",
                mimeType="image/png", role="general_visual", order=index,
                image={"data_url": "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode(),
                       "width": 1024, "height": 1024}))
    if case == "large_video":
        data = b"synthetic-media-" * (64 * 1024 * 1024 // 16)
        async def chunks():
            yield data
        media = asyncio.run(store.ingest_media(chunks(), "video", "video/mp4"))
        assets.append(ReferenceAsset(id="video", kind="video", filename="large.mp4", mimeType="video/mp4",
                                    role="general_video", mediaPayload=media))
    profile = config.save("profiles", capacity_profile(count=5 if case in {"five_pictures", "five_pictures_local"} else 3, av=True))
    local_root = root / "comfy-input" if case == "five_pictures_local" else None
    if local_root is not None:
        local_root.mkdir(parents=True)
    connection = config.save("connections", ex.ConnectionWrite(name="Offline", baseUrl="http://never.test",
        **({"localInputDirectory": str(local_root)} if local_root is not None else {})))
    project = store.create_project(Workspace(projectName="Synthetic benchmark", mode="Ref2VA" if assets else "T2VA",
        creativeRequest="Synthetic benchmark", assets=assets))
    select(store, project, valid_ref_prompt() if assets else __import__("test_validator").VALID_BASE)
    prepared = prepare_workflow(store, project.project_id, project.active_generation_id, profile)
    request = send.ExecutionSendRequest(projectId=project.project_id, generationId=project.active_generation_id,
        profileId=profile.id, connectionId=connection.id, expectedExecutionFingerprint=prepared.execution_fingerprint)
    previous = None
    if case == "renderer_mutation":
        def accepted(request):
            if request.url.path == "/upload/image":
                return httpx.Response(200, json={"name": "initial.png", "subfolder": "miniconstruct", "type": "input"})
            return httpx.Response(200, json={"prompt_id": json.loads(request.content)["prompt_id"]})
        previous = send.send_generation(request, store=store, config=config, transport=httpx.MockTransport(accepted))
    rows = []
    api_client = TestClient(app)
    for _ in range(repeats):
        stages, counts = defaultdict(float), defaultdict(int)
        original_prepare, original_reference = send.prepare_workflow, store._reference_payload
        original_open, original_record = store.open_execution_payloads, store.record_render_submission
        original_digest, original_sha, original_read = hashlib.file_digest, hashlib.sha256, Path.read_bytes
        original_text = Path.read_text
        directory = store.root / f"project-{project.project_id}"
        manifest = store._read_manifest(directory)
        media_paths = {directory / entry.path for generation in store._generations(manifest)[0]
                       for entry in generation.asset_files}
        media_lengths = {p.stat().st_size for p in media_paths}
        passes = 0
        mutated = False
        def mutate_during_verification():
            nonlocal mutated
            if case == "renderer_mutation" and not mutated:
                mutated = True
                store.update_render_job(project.project_id, project.active_generation_id,
                    previous.render_submission_id, RendererJobState(status="running"))
        def timed_prepare(*args, **kwargs):
            nonlocal passes
            stage = "B_reconstruct" if passes == 0 else "E_revalidate"
            passes += 1
            started = perf_counter()
            try: return original_prepare(*args, **kwargs)
            finally: stages[stage] += perf_counter() - started
        def reference(*args, **kwargs):
            started = perf_counter()
            try: return original_reference(*args, **kwargs)
            finally: stages["C_hydration_nested"] += perf_counter() - started
        def read(path):
            result = original_read(path)
            if path in media_paths:
                counts["media_reads"] += 1
                counts["media_read_bytes"] += len(result)
            else:
                counts["metadata_reads"] += 1
            return result
        def read_text(path, *args, **kwargs):
            result = original_text(path, *args, **kwargs)
            if path.name == "project.json":
                counts["manifest_reads"] += 1
                counts["metadata_reads"] += 1
            return result
        def digest(stream, *args, **kwargs):
            if Path(stream.name) in media_paths:
                counts["media_hashes"] += 1
                counts["media_reads"] += 1
                counts["media_hash_bytes"] += Path(stream.name).stat().st_size
                counts["media_read_bytes"] += Path(stream.name).stat().st_size
                mutate_during_verification()
            return original_digest(stream, *args, **kwargs)
        def sha(data=b"", *args, **kwargs):
            if len(data) in media_lengths:
                counts["media_hashes"] += 1
                counts["media_hash_bytes"] += len(data)
                mutate_during_verification()
            return original_sha(data, *args, **kwargs)
        @contextmanager
        def opened(*args, **kwargs):
            started = perf_counter()
            with original_open(*args, **kwargs) as values:
                stages["C_open_verify"] += perf_counter() - started
                yield values
        def record(*args, **kwargs):
            started = perf_counter()
            try: return original_record(*args, **kwargs)
            finally: stages["H_persist"] += perf_counter() - started
        def transport_impl(request):
            counts["comfy_requests"] += 1
            if case == "slow_transport":
                # Explicit controlled transport fixture: defer each response by
                # 250 ms. This is measured injected latency, not a live estimate
                # and never an operational Send retry/grace period.
                release = Event()
                timer = Timer(0.25, release.set)
                timer.start()
                assert release.wait(10)
                timer.join()
            if request.url.path == "/upload/image":
                counts["uploads"] += 1
                # Actual multipart bytes, excluding the small framing overhead below.
                counts["upload_wire_bytes"] += len(request.content)
                name = request.content.split(b'filename="', 1)[1].split(b'"', 1)[0].decode()
                from email.parser import BytesParser
                from email.policy import default
                message = BytesParser(policy=default).parsebytes(
                    b'Content-Type: ' + request.headers['content-type'].encode() + b'\r\n\r\n' + request.content)
                remote_files[name] = next(p.get_payload(decode=True) for p in message.iter_parts() if p.get_filename())
                if local_root is not None:
                    target = local_root / "miniconstruct" / name
                    target.parent.mkdir(exist_ok=True)
                    target.write_bytes(remote_files[name])
                return httpx.Response(200, json={"name": name, "subfolder": "miniconstruct", "type": "input"})
            if request.url.path == "/view":
                counts["remote_verification_requests"] += 1
                data = remote_files.get(request.url.params["filename"])
                if data is None:
                    return httpx.Response(404)
                counts["remote_verification_bytes"] += len(data)
                return httpx.Response(200, content=data)
            counts["prompt_requests"] += 1
            wire = json.loads(request.content)
            return httpx.Response(200, json={"prompt_id": wire["prompt_id"], "number": 1, "node_errors": {}})
        def transport(request):
            counts["http_request_body_bytes"] += len(request.content)
            response = transport_impl(request)
            counts["http_response_body_bytes"] += len(response.content)
            return response
        def timed(method, stage):
            def call(*args, **kwargs):
                begin = perf_counter()
                try: return method(*args, **kwargs)
                finally:
                    stages[stage] += perf_counter() - begin
                    if stage == "G_prompt":
                        stages["queue_ack"] = perf_counter() - started
            return call
        def upload(method):
            def call(self, stream, *args, **kwargs):
                read = stream.read
                def counted_read(*args, **kwargs):
                    data = read(*args, **kwargs)
                    counts["upload_source_read_bytes"] += len(data)
                    return data
                counts["upload_source_read_passes"] += 1
                with patch.object(stream, "read", counted_read):
                    return timed(method, "D_upload")(self, stream, *args, **kwargs)
            return call
        started = perf_counter()
        send_finished = None
        def dispatch(body):
            nonlocal send_finished
            stages["A_inprocess_validation"] = perf_counter() - started
            try:
                from miniconstruct.send_timing import _active
            except ImportError:  # Historical --baseline predates stage timings.
                _active = None
            timing = {"stagesMs": {}, "counts": {}}
            token = _active.set(timing) if _active is not None else None
            try:
                return send.send_generation(body, store=store, config=config, transport=httpx.MockTransport(transport))
            finally:
                if _active is not None:
                    _active.reset(token)
                counts["local_verification_bytes"] = timing["counts"].get("localVerificationBytes", 0)
                stages["C_local_staged_verify"] = timing["stagesMs"].get("verifyLocal", 0) / 1000
                send_finished = perf_counter()
        with patch.object(send, "prepare_workflow", timed_prepare), patch.object(store, "_reference_payload", reference), \
             patch.object(store, "open_execution_payloads", opened), patch.object(store, "record_render_submission", record), \
             patch.object(hashlib, "file_digest", digest), patch.object(hashlib, "sha256", sha), patch.object(Path, "read_bytes", read), \
             patch.object(Path, "read_text", read_text), \
             patch.object(send.ComfyClient, "upload_input", upload(send.ComfyClient.upload_input)), \
             patch.object(send.ComfyClient, "queue_prompt", timed(send.ComfyClient.queue_prompt, "G_prompt")), \
             patch.object(send, "transport_preflight", timed(send.transport_preflight, "F_graph_preflight")), \
             patch.object(routes, "send_generation", dispatch):
            response = api_client.post("/api/execution/send", json=request.model_dump(mode="json", by_alias=True))
            outcome = "accepted" if response.status_code == 200 else response.json()["detail"]
        stages["I_api_receipt"] = perf_counter() - send_finished
        stages["total"] = perf_counter() - started
        for metric in ("uploads", "upload_wire_bytes", "remote_verification_requests", "remote_verification_bytes",
                       "local_verification_bytes", "prompt_requests", "comfy_requests"):
            counts.setdefault(metric, 0)
        rows.append({"outcome": outcome, "milliseconds": {key: round(value * 1000, 3) for key, value in stages.items()}, "counts": dict(counts)})
    return {"case": case, "repeats": repeats, "media_bytes": sum(p.stat().st_size for p in media_paths),
            "backend_http_requests_per_run": 1, "send_calls_per_run": 1, "runs": rows}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--baseline", action="store_true", help="Use the original clean source revision in a temporary directory")
    parser.add_argument("--local-comparison", action="store_true", help="Compare five Pictures with HTTP and local verification, without injected delays")
    args = parser.parse_args()
    if args.baseline and args.local_comparison:
        parser.error("--local-comparison uses current HTTP and local modes; omit --baseline")
    with tempfile.TemporaryDirectory(prefix="mc-send-benchmark-") as temporary:
        print(json.dumps([measure(Path(temporary) / case, case, args.repeats)
                          for case in (("five_pictures", "five_pictures_local") if args.local_comparison else ("prepared_repeat", "pictures", "five_pictures", "large_video", "renderer_mutation", "slow_transport"))], indent=2))
    if baseline_tree:
        baseline_tree.cleanup()

