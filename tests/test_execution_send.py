"""F4 uses real saved state and fake HTTP only; never a live ComfyUI instance."""
from copy import deepcopy
import io
import json
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from miniconstruct.app import app
from miniconstruct import execution as ex
from miniconstruct import execution_send as send
from miniconstruct.execution_routing import AssetRouting, GenerationRouting
from miniconstruct.project_store import ProjectStore, ProjectStoreError
from miniconstruct.workflow_preparation import prepare_workflow
from test_workflow_preparation import (capacity_profile, select, ref_workspace, _video_asset,
                                      VIDEO_PROMPT, mapping)
from test_execution_routing import master, audio_asset
from test_validator import VALID_BASE, valid_ref_prompt


@pytest.fixture(autouse=True)
def no_live(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Live HTTP is forbidden in F4 tests")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)


@pytest.fixture
def env(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path / "projects")
    config = ex.ExecutionStore(tmp_path / "execution")
    profile = config.save("profiles", capacity_profile())
    connection = config.save("connections", ex.ConnectionWrite(name="Local", baseUrl="http://localhost:8188/proxy/comfy/"))
    project = store.create_project(workspace_factory())
    select(store, project)
    def body(p=project, f=profile):
        return send.ExecutionSendRequest(projectId=p.project_id, generationId=p.active_generation_id,
                                         profileId=f.id, connectionId=connection.id)
    return store, config, profile, connection, project, body


def recorder(calls, failure=None):
    def handle(request):
        calls.append(request)
        if failure:
            response = failure(request, calls)
            if response is not None:
                return response
        if request.url.path.endswith("/upload/image"):
            return httpx.Response(200, json={"name": f"returned ({len(calls)}).bin", "subfolder": "miniconstruct", "type": "input"})
        body = json.loads(request.content)
        return httpx.Response(200, json={"prompt_id": body["prompt_id"], "number": 7, "node_errors": {}})
    return httpx.MockTransport(handle)


def run(env, calls, body=None, failure=None):
    store, config, _, _, _, make = env
    return send.send_generation(body or make(), store=store, config=config, transport=recorder(calls, failure))


def test_saved_authority_offline_prepare_and_transport_copy(env):
    store, _, profile, _, project, _ = env
    before = deepcopy(profile.workflow)
    prepared = prepare_workflow(store, project.project_id, project.active_generation_id, profile)
    snapshot = prepared.model_dump()
    calls = []
    result = run(env, calls)
    assert len(calls) == 1
    assert str(calls[0].url) == "http://localhost:8188/proxy/comfy/prompt"
    wire = json.loads(calls[0].content)
    assert set(wire) == {"prompt", "prompt_id"}
    assert UUID(wire["prompt_id"]) == result.prompt_id
    assert wire["prompt"]["h3"]["inputs"]["prompt"] == VALID_BASE
    assert wire["prompt"]["h3"]["inputs"]["duration"] == project.workspace.duration_seconds
    for key in ["model", "lora", "sampler", "sigma", "opaque", "context"]:
        assert wire["prompt"]["h3"]["inputs"][key] == before["h3"]["inputs"][key]
    assert wire["prompt"]["image-1"]["inputs"]["image"] == {"$inactiveReference": "Picture 1"}
    assert "image-1" not in wire["prompt"]["h3"]["inputs"]
    assert prepared.model_dump() == snapshot and profile.workflow == before
    assert result.submitted and result.queue_number == 7 and result.uploaded_inputs == []


@pytest.mark.parametrize("extra", ["graph", "prompt", "path", "bytes", "baseUrl", "preparedWorkflow"])
def test_request_only_ids(env, extra):
    with pytest.raises(ValidationError):
        send.ExecutionSendRequest(**env[-1]().model_dump(by_alias=True), **{extra: "untrusted"})


@pytest.mark.parametrize("field", ["connection_id", "profile_id", "project_id", "generation_id"])
def test_missing_authority_blocks_before_network(env, field):
    calls = []
    with pytest.raises((ex.ExecutionError, ProjectStoreError)):
        run(env, calls, env[-1]().model_copy(update={field: uuid4()}))
    assert calls == []


def test_stale_selected_prompt_blocks(env):
    store, _, _, _, project, _ = env
    workspace = project.workspace.model_copy(deep=True)
    workspace.duration_seconds = 12
    store.save_project(project.project_id, workspace)
    calls = []
    with pytest.raises(ex.InvalidBindings, match="stale"):
        run(env, calls)
    assert calls == []


@pytest.mark.parametrize("kind,widget", [("LoadImage", "image"), ("LoadVideo", "file"), ("LoadAudio", "audio")])
def test_unbound_standard_placeholder_blocks_offline_prepare_does_not(env, kind, widget):
    store, config, profile, _, project, _ = env
    profile.workflow["unbound"] = {"class_type": kind, "inputs": {widget: "placeholder.file"}}
    # Use the saved store's ordinary get seam, without changing the preparation authority.
    config.get = lambda category, id: profile if category == "profiles" else env[3]
    result = prepare_workflow(store, project.project_id, project.active_generation_id, profile)
    assert any("Unbound" in w for w in result.warnings)
    calls = []
    with pytest.raises(ex.InvalidBindings, match="Bind it in the Workflow Profile"):
        run(env, calls)
    assert calls == []


def test_picture_upload_bytes_deterministic_names_and_dedup(env, workspace_factory, image_asset_factory):
    store, config, profile, _, _, make = env
    project = store.create_project(ref_workspace(workspace_factory, image_asset_factory, 2))
    select(store, project, valid_ref_prompt())
    requirements = prepare_workflow(store, project.project_id, project.active_generation_id, profile).asset_requirements
    assert requirements[0].content_sha256 == requirements[1].content_sha256
    calls = []
    result = run(env, calls, make(project))
    assert len(calls) == 2 and len(result.uploaded_inputs) == 2
    upload = calls[0]
    assert upload.url.path.endswith("/upload/image")
    content = upload.content
    path, _ = store.execution_payload(project.project_id, project.active_generation_id,
                                     requirements[0].asset_id, "image", requirements[0].content_sha256)
    assert path.read_bytes() in content
    assert b'name="image"; filename="mc-' in content
    assert (requirements[0].content_sha256 + ".png").encode() in content
    for field, value in [("type", "input"), ("subfolder", "miniconstruct"), ("overwrite", "false")]:
        assert f'name="{field}"\r\n\r\n{value}\r\n'.encode() in content
    graph = json.loads(calls[-1].content)["prompt"]
    assert graph["image-1"]["inputs"]["image"] == graph["image-2"]["inputs"]["image"] == "miniconstruct/returned (1).bin"
    assert "$assetRequirement" not in json.dumps(graph)


@pytest.mark.parametrize("present,frames,embedded,audio", [
    (True, True, False, True), (True, True, True, True), (True, True, False, False),
    (False, True, False, False), (True, False, False, False)])
def test_video_audio_streaming_and_routes(env, tmp_path, workspace_factory, monkeypatch, present, frames, embedded, audio):
    store, config, _, connection, _, make = env
    profile = master(tmp_path)
    config.get = lambda category, id: profile if category == "profiles" else connection
    assets = ([_video_asset(store)] if present else []) + [audio_asset(store)]
    project = store.create_project(workspace_factory(mode="Ref2VA", assets=assets))
    select(store, project, VIDEO_PROMPT)
    store.set_execution_routing(project.project_id, project.active_generation_id, GenerationRouting(assets={
        "clip": AssetRouting(useFrames=frames, useEmbeddedAudio=embedded),
        "voice": AssetRouting(useAudio=audio)} if present else {"voice": AssetRouting(useAudio=audio)}))
    original = Path.read_bytes
    def no_media_read_bytes(path):
        if path.suffix != ".json":
            raise AssertionError("A/V payloads must stream")
        return original(path)
    monkeypatch.setattr(Path, "read_bytes", no_media_read_bytes)
    calls = []
    result = run(env, calls, make(project, profile))
    assert len(calls) == int(present and frames) + int(audio) + 1
    wire = json.loads(calls[-1].content)["prompt"]
    inputs = wire["136"]["inputs"]
    assert ("ref_video_audios.ref_video_audio_0" in inputs) == embedded
    assert ("ref_videos.ref_video_0" in inputs) == (present and frames)
    assert ("ref_audios.ref_audio_0" in inputs) == audio
    assert wire["171"]["inputs"]["video"] == ["170", 0]
    if present and frames:
        assert b"synthetic video bytes" in calls[0].content
    if audio:
        assert b"offline audio fixture" in calls[-2].content
    assert len(result.uploaded_inputs) == len(calls) - 1


@pytest.mark.parametrize("value", [
    {"name": "../evil", "subfolder": "", "type": "input"},
    {"name": "/absolute", "subfolder": "", "type": "input"},
    {"name": "C:\\evil", "subfolder": "", "type": "input"},
    {"name": "x", "subfolder": "../evil", "type": "input"},
    {"name": "x", "subfolder": "/absolute", "type": "input"},
    {"name": "x", "subfolder": "C:\\absolute", "type": "input"},
    {"name": "x", "subfolder": "a//b", "type": "input"},
    {"name": "x [output]", "subfolder": "", "type": "input"},
    {"name": "x", "subfolder": "", "type": "output"},
    {"name": 123, "subfolder": "", "type": "input"},
    {"subfolder": "", "type": "input"}, {}, [],
])
def test_upload_response_security(value):
    with pytest.raises(send.SendError):
        send.upload_filename(value)


@pytest.mark.parametrize("folder,expected", [("", "x.png"), ("miniconstruct", "miniconstruct/x.png"),
                                            ("miniconstruct\\safe", "miniconstruct/safe/x.png")])
def test_valid_upload_response(folder, expected):
    assert send.upload_filename({"name": "x.png", "subfolder": folder, "type": "input"}) == expected


@pytest.mark.parametrize("failure", ["timeout", "disconnect", "malformed", "missing_id", "wrong_id", "oversized", "invalid_number", "invalid_errors", "server_error"])
def test_prompt_unknown_status_exactly_one_attempt(env, failure):
    calls = []
    def fail(request, calls):
        if failure == "timeout": raise httpx.ReadTimeout("private detail")
        if failure == "disconnect": raise httpx.ReadError("private detail")
        if failure == "malformed": return httpx.Response(200, text="not json")
        if failure == "oversized": return httpx.Response(200, text=" " * (send.MAX_RESPONSE_BYTES + 1))
        if failure == "server_error": return httpx.Response(500, json={})
        value = {"prompt_id": json.loads(request.content)["prompt_id"], "number": 1, "node_errors": {}}
        if failure == "missing_id": value.pop("prompt_id")
        if failure == "wrong_id": value["prompt_id"] = str(uuid4())
        if failure == "invalid_number": value["number"] = True
        if failure == "invalid_errors": value["node_errors"] = []
        return httpx.Response(200, json=value)
    with pytest.raises(send.SendError, match="Submission status is unknown") as error:
        run(env, calls, failure=fail)
    assert len(calls) == 1
    assert json.loads(calls[0].content)["prompt_id"] in str(error.value)
    assert "Check ComfyUI" in str(error.value)
    assert "private detail" not in str(error.value)


def test_prompt_validation_error_bounded_and_safe(env):
    calls = []
    failure = lambda *_: httpx.Response(400, json={"error": {"message": "private C:\\path"},
        "node_errors": {"164": {"errors": [{"type": "custom_validation_failed", "details": "secret"}],
                                "traceback": "private path"}}})
    with pytest.raises(ex.InvalidBindings, match="164") as error:
        run(env, calls, failure=failure)
    assert "custom_validation_failed" in str(error.value)
    assert "secret" not in str(error.value) and "private" not in str(error.value)
    assert len(calls) == 1


@pytest.mark.parametrize("index", [1, 2])
@pytest.mark.parametrize("failure", ["413", "redirect", "timeout", "refused", "malformed", "oversized"])
def test_upload_failure_stops_before_prompt(env, tmp_path, workspace_factory, index, failure):
    store, config, _, connection, _, make = env
    profile = master(tmp_path)
    config.get = lambda category, id: profile if category == "profiles" else connection
    project = store.create_project(workspace_factory(mode="Ref2VA", assets=[_video_asset(store), audio_asset(store)]))
    select(store, project, VIDEO_PROMPT)
    calls = []
    def fail(request, calls):
        if len(calls) != index: return None
        if failure == "timeout": raise httpx.WriteTimeout("private")
        if failure == "refused": raise httpx.ConnectError("private")
        if failure == "413": return httpx.Response(413, text="too large")
        if failure == "redirect": return httpx.Response(307, headers={"Location": "http://other/upload/image"})
        if failure == "malformed": return httpx.Response(200, text="oops")
        return httpx.Response(200, text=" " * (send.MAX_RESPONSE_BYTES + 1))
    with pytest.raises(send.SendError, match="No prompt was submitted") as error:
        run(env, calls, make(project, profile), fail)
    assert ("Video 1" if index == 1 else "Audio 1") in str(error.value)
    assert len(calls) == index and all(r.url.path.endswith("/upload/image") for r in calls)
    if failure == "413": assert "size limit" in str(error.value)


def test_client_security_and_file_handle_streaming(env, monkeypatch):
    original = httpx.Client
    options = []
    def factory(**kwargs):
        options.append(kwargs)
        return original(**kwargs)
    monkeypatch.setattr(httpx, "Client", factory)
    class File(io.BytesIO):
        def read(self, size=-1):
            assert 0 < size <= 65536
            return super().read(size)
    calls = []
    with send.ComfyClient(env[3], transport=recorder(calls)) as client:
        client.upload_input(File(b"x" * 200000), "mc-test.mp4", "video/mp4")
    assert options[0]["trust_env"] is False and options[0]["follow_redirects"] is False
    assert len(calls) == 1


def test_api_ids_only_and_error_mapping(env, monkeypatch):
    import miniconstruct.api.execution_routes as routes
    calls = []
    monkeypatch.setattr(routes, "send_generation", lambda body: run(env, calls, body))
    client = TestClient(app)
    body = env[-1]().model_dump(mode="json", by_alias=True)
    assert client.post("/api/execution/send", json={**body, "graph": {}}).status_code == 422
    assert calls == []
    response = client.post("/api/execution/send", json=body)
    assert response.status_code == 200 and response.json()["submitted"]
    assert "graph" not in response.json() and "promptId" in response.json()
    def unknown(body): raise send.SendError("Submission status is unknown. Check ComfyUI.")
    monkeypatch.setattr(routes, "send_generation", unknown)
    assert client.post("/api/execution/send", json=body).status_code == 502


@pytest.mark.parametrize("value", ["C:\\private\\file", "data:video/mp4;base64,c2VjcmV0"])
def test_private_constants_block_before_network(env, value):
    _, config, profile, connection, _, _ = env
    profile.workflow["h3"]["inputs"]["private"] = value
    config.get = lambda category, id: profile if category == "profiles" else connection
    calls = []
    with pytest.raises(ex.InvalidBindings, match="private path"):
        run(env, calls)
    assert not calls


def test_unresolved_markers_block_before_network(env):
    _, config, profile, connection, _, _ = env
    profile.workflow["h3"]["inputs"]["opaque"] = {"$assetRequirement": "unknown"}
    config.get = lambda category, id: profile if category == "profiles" else connection
    calls = []
    with pytest.raises(ex.InvalidBindings, match="unexpected active"):
        run(env, calls)
    assert not calls


def test_profile_mode_mismatch_and_missing_selection(env):
    store, config, profile, connection, project, _ = env
    calls = []
    profile.compatible_modes = [ex.H3Mode.REF2VA]
    config.get = lambda category, id: profile if category == "profiles" else connection
    with pytest.raises(ex.InvalidBindings, match="not compatible"):
        run(env, calls)
    store.clear_execution_prompt(project.project_id, project.active_generation_id)
    with pytest.raises(ex.InvalidBindings, match="Select a generated"):
        run(env, calls)
    assert not calls


def test_routing_reference_map_mismatch_blocks_send(env, tmp_path, workspace_factory):
    store, config, _, connection, _, make = env
    profile = master(tmp_path)
    config.get = lambda category, id: profile if category == "profiles" else connection
    project = store.create_project(workspace_factory(mode="Ref2VA", assets=[_video_asset(store), audio_asset(store)]))
    select(store, project, VIDEO_PROMPT.replace("No supplied subjects.", "<Audio 1> is the sound reference."))
    store.set_execution_routing(project.project_id, project.active_generation_id,
                                GenerationRouting(assets={"clip": AssetRouting(useEmbeddedAudio=True)}))
    calls = []
    with pytest.raises(ex.InvalidBindings, match="Audio 1 has a different source"):
        run(env, calls, make(project, profile))
    assert not calls


def test_missing_second_payload_blocks_all_network(env, tmp_path, workspace_factory):
    store, config, _, connection, _, make = env
    profile = master(tmp_path)
    config.get = lambda category, id: profile if category == "profiles" else connection
    project = store.create_project(workspace_factory(mode="Ref2VA", assets=[_video_asset(store), audio_asset(store)]))
    select(store, project, VIDEO_PROMPT)
    prepared = prepare_workflow(store, project.project_id, project.active_generation_id, profile)
    r = prepared.asset_requirements[-1]
    path, _ = store.execution_payload(project.project_id, project.active_generation_id, r.asset_id, r.asset_kind, r.content_sha256)
    path.write_bytes(b"corrupt")
    calls = []
    with pytest.raises(ProjectStoreError, match="payload"):
        run(env, calls, make(project, profile))
    assert not calls


def test_manifest_payload_identity_cannot_be_substituted(env, workspace_factory, image_asset_factory):
    store = env[0]
    project = store.create_project(ref_workspace(workspace_factory, image_asset_factory))
    with pytest.raises(ProjectStoreError, match="verification"):
        store.execution_payload(project.project_id, project.active_generation_id, "pic-1", "image", "0" * 64)
    with pytest.raises(ProjectStoreError, match="identity"):
        store.execution_payload(project.project_id, project.active_generation_id, "unknown", "image", "0" * 64)


def test_success_node_errors_are_bounded_and_omit_private_values(env):
    calls = []
    def response(request, _):
        return httpx.Response(200, json={"prompt_id": json.loads(request.content)["prompt_id"],
            "node_errors": {str(i): {"errors": [{"type": "invalid_input_type", "details": "C:\\private"}]} for i in range(50)}})
    result = run(env, calls, failure=response)
    assert result.queue_number is None and len(result.node_errors) == 32
    assert "private" not in result.model_dump_json()


def test_prepare_api_is_still_zero_network(env, monkeypatch, tmp_path):
    store, config, _, _, _, make = env
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(store.root))
    monkeypatch.setenv("MINICONSTRUCT_EXECUTION_ROOT", str(tmp_path / "execution"))
    data = make().model_dump(mode="json", by_alias=True)
    data.pop("connectionId")
    response = TestClient(app).post("/api/execution/prepare", json=data)
    assert response.status_code == 200
    assert response.json()["transportReady"] is False


def test_safe_extension_fallback_and_determinism():
    from miniconstruct.workflow_preparation import AssetRequirement
    r = AssetRequirement(id="r", assetId="a", contentSha256="f" * 64, semanticLabel="Video 1",
                         assetKind="video", filename="../../odd.exe", nodeId="1", inputName="file")
    assert send.deterministic_name(r, "video/mp4") == "mc-" + "f" * 64 + ".mp4"
    r.filename = "CLIP.MP4"
    assert send.deterministic_name(r, "video/mp4") == "mc-" + "f" * 64 + ".mp4"


def test_transport_preserves_normal_constants_omitted_by_inspection(env):
    store, config, profile, connection, project, _ = env
    profile.workflow["h3"]["inputs"]["max_tokens"] = 1024
    profile.workflow["h3"]["inputs"]["tokenizer"] = "normal-tokenizer"
    config.get = lambda category, id: profile if category == "profiles" else connection
    inspection = prepare_workflow(store, project.project_id, project.active_generation_id, profile)
    assert inspection.graph["h3"]["inputs"]["max_tokens"] == "[redacted]"
    calls = []; run(env, calls)
    inputs = json.loads(calls[0].content)["prompt"]["h3"]["inputs"]
    assert inputs["max_tokens"] == 1024 and inputs["tokenizer"] == "normal-tokenizer"
