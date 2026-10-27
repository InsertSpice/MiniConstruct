"""F4 uses real saved state and fake HTTP only; never a live ComfyUI instance."""
from copy import deepcopy
import base64
import hashlib
import io
import json
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from pydantic import ValidationError

from miniconstruct.app import app
from miniconstruct import execution as ex
from miniconstruct import execution_send as send
from miniconstruct.execution_routing import AssetRouting, GenerationRouting
from miniconstruct.project_store import ProjectStore, ProjectStoreError
from miniconstruct.models.takes import RendererJobState
from miniconstruct.execution_state import RendererSeedSettings
from miniconstruct.execution_state import ManualEditAdoptionWrite
from miniconstruct.workflow_preparation import prepare_workflow
from miniconstruct.h3.builder import assemble_prompt
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
        if request.url.path.endswith("/view"):
            return httpx.Response(404)
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
    assert set(wire) == {"prompt", "prompt_id", "client_id"}
    assert wire["client_id"] == wire["prompt_id"]
    assert UUID(wire["prompt_id"]) == result.prompt_id
    assert wire["prompt"]["h3"]["inputs"]["prompt"] == VALID_BASE
    assert wire["prompt"]["h3"]["inputs"]["duration"] == project.workspace.duration_seconds
    for key in ["model", "lora", "sampler", "sigma", "opaque", "context"]:
        assert wire["prompt"]["h3"]["inputs"][key] == before["h3"]["inputs"][key]
    assert wire["prompt"]["image-1"]["inputs"]["image"] == {"$inactiveReference": "Picture 1"}
    assert "image-1" not in wire["prompt"]["h3"]["inputs"]
    assert prepared.model_dump() == snapshot and profile.workflow == before
    assert result.submitted and result.queue_number == 7 and result.uploaded_inputs == []


def test_stale_six_second_prepare_cannot_send_after_current_duration_becomes_nine(env):
    store, config, profile, connection, project, make = env
    project = store.save_project(project.project_id,
        project.workspace.model_copy(update={"duration_seconds": 6}),
        expected_project_fingerprint=project.project_fingerprint)
    from test_workflow_preparation import select
    select(store, project)
    six_seconds = prepare_workflow(store, project.project_id, project.active_generation_id, profile)
    assert six_seconds.graph["h3"]["inputs"]["duration"] == 6

    store.sync_execution_duration(project.project_id, project.active_generation_id, 9,
                                  project.project_fingerprint)
    stale_body = make().model_copy(update={"expected_execution_fingerprint": six_seconds.execution_fingerprint})
    calls = []
    with pytest.raises(ex.InvalidBindings, match="Prepared execution is stale"):
        send.send_generation(stale_body, store=store, config=config, transport=recorder(calls))
    assert calls == []

    nine_seconds = prepare_workflow(store, project.project_id, project.active_generation_id, profile)
    assert nine_seconds.graph["h3"]["inputs"]["duration"] == 9
    current_body = make().model_copy(update={"expected_execution_fingerprint": nine_seconds.execution_fingerprint})
    result = send.send_generation(current_body, store=store, config=config, transport=recorder(calls))
    wire = json.loads(calls[-1].content)
    assert wire["prompt"]["h3"]["inputs"]["duration"] == 9
    submission = store.render_submission(project.project_id, project.active_generation_id, result.render_submission_id)
    assert submission.execution_fingerprint == nine_seconds.execution_fingerprint
    assert store.read_generation(project.project_id, project.active_generation_id).workspace.duration_seconds == 9


def test_manual_edit_selection_reaches_prepared_graph_and_mocked_comfy_prompt_unchanged(env):
    store, _, profile, _, project, _ = env
    edited = VALID_BASE.replace("Rain and distant traffic.", "Heavy rain against the shelter roof.")
    selected = store.adopt_manual_edit_execution_prompt(project.project_id, project.active_generation_id,
                                                        ManualEditAdoptionWrite(prompt=edited)).selected
    assert selected.prompt == edited and selected.source_kind == "manual_edit"
    prepared = prepare_workflow(store, project.project_id, project.active_generation_id, profile)
    assert prepared.selected_prompt.prompt == edited
    assert prepared.graph["h3"]["inputs"]["prompt"] == edited
    calls = []
    result = run(env, calls)
    wire = json.loads(calls[0].content)
    assert wire["prompt"]["h3"]["inputs"]["prompt"] == edited
    assert result.submitted


def test_renderer_seed_changes_per_explicit_send_and_is_recorded(env, monkeypatch):
    store, config, _, _, project, make = env
    draft = capacity_profile().model_dump(by_alias=True)
    graph = json.loads(draft["workflowJson"])
    graph["h3"]["inputs"]["seed"] = 44
    draft["workflowJson"] = json.dumps(graph)
    draft["bindings"].append(mapping("renderer_seed", "h3", "seed"))
    profile = config.save("profiles", ex.ProfileCreate(**draft))
    prepared = prepare_workflow(store, project.project_id, project.active_generation_id, profile)
    assert prepared.renderer_seed_controlled
    assert prepared.graph["h3"]["inputs"]["seed"] == 44
    values = iter([123, 456])
    monkeypatch.setattr(send.secrets, "randbelow", lambda bound: next(values))
    calls = []
    first = run(env, calls, make(f=profile))
    second = run(env, calls, make(f=profile))
    assert [json.loads(call.content)["prompt"]["h3"]["inputs"]["seed"] for call in calls] == [123, 456]
    assert [json.loads(call.content)["prompt"]["h3"]["inputs"]["prompt"] for call in calls] == [VALID_BASE, VALID_BASE]
    assert [first.renderer_seed, second.renderer_seed] == [123, 456]
    submissions = store.generation_outputs(project.project_id, project.active_generation_id)["renderSubmissions"]
    assert [item["rendererSeed"] for item in submissions] == [123, 456]
    updated = store.update_render_job(project.project_id, project.active_generation_id,
                                      first.render_submission_id, RendererJobState(status="completed"))
    assert updated.renderer_seed == 123
    assert profile.workflow["h3"]["inputs"]["seed"] == 44


def test_unmapped_workflow_seed_is_preserved(env):
    store, config, _, _, project, make = env
    draft = capacity_profile().model_dump(by_alias=True)
    graph = json.loads(draft["workflowJson"])
    graph["h3"]["inputs"]["seed"] = 44
    draft["workflowJson"] = json.dumps(graph)
    profile = config.save("profiles", ex.ProfileCreate(**draft))
    calls = []
    result = run(env, calls, make(f=profile))
    assert json.loads(calls[0].content)["prompt"]["h3"]["inputs"]["seed"] == 44
    assert result.renderer_seed is None


def test_renderer_workflow_default_and_fixed_round_trip_without_prompt_or_graph_changes(env):
    store, config, _, _, project, make = env
    draft = capacity_profile().model_dump(by_alias=True)
    graph = json.loads(draft["workflowJson"])
    graph["h3"]["inputs"]["seed"] = 44
    draft["workflowJson"] = json.dumps(graph)
    draft["bindings"].append(mapping("renderer_seed", "h3", "seed"))
    profile = config.save("profiles", ex.ProfileCreate(**draft))
    before_graph = deepcopy(profile.workflow)
    generation = project.active_generation_id
    original_prompt_fingerprint = store.execution_snapshot(project.project_id, generation)[1].current_fingerprint
    initial = prepare_workflow(store, project.project_id, generation, profile)
    assert initial.renderer_seed_settings.mode == "random"

    store.set_renderer_seed_settings(project.project_id, generation, RendererSeedSettings(mode="workflow_default"))
    default = prepare_workflow(store, project.project_id, generation, profile)
    assert default.execution_fingerprint != initial.execution_fingerprint
    calls = []
    default_receipt = run(env, calls, make(f=profile))
    assert json.loads(calls[-1].content)["prompt"]["h3"]["inputs"]["seed"] == 44
    assert default_receipt.renderer_seed is None

    store.set_renderer_seed_settings(project.project_id, generation, RendererSeedSettings(mode="fixed", fixedSeed=12345))
    reloaded = ProjectStore(store.root).renderer_seed_settings(project.project_id, generation)
    assert reloaded.mode == "fixed" and reloaded.fixed_seed == 12345
    fixed = prepare_workflow(store, project.project_id, generation, profile)
    assert fixed.execution_fingerprint != default.execution_fingerprint
    assert store.execution_snapshot(project.project_id, generation)[1].current_fingerprint == original_prompt_fingerprint
    assert not store.execution_snapshot(project.project_id, generation)[1].stale
    first = run(env, calls, make(f=profile))
    second = run(env, calls, make(f=profile))
    assert [json.loads(call.content)["prompt"]["h3"]["inputs"]["seed"] for call in calls] == [44, 12345, 12345]
    assert first.renderer_seed == second.renderer_seed == 12345
    submissions = store.generation_outputs(project.project_id, generation)["renderSubmissions"]
    assert [item["rendererSeed"] for item in submissions] == [None, 12345, 12345]
    assert [json.loads(call.content)["prompt"]["h3"]["inputs"]["prompt"] for call in calls] == [VALID_BASE] * 3
    assert config.get("profiles", profile.id).workflow == before_graph


def test_unmapped_profile_rejects_controlled_renderer_seed_and_keeps_default(env):
    store, config, profile, _, project, make = env
    store.set_renderer_seed_settings(project.project_id, project.active_generation_id,
                                     RendererSeedSettings(mode="fixed", fixedSeed=42))
    with pytest.raises(ex.InvalidBindings, match="renderer_seed binding"):
        prepare_workflow(store, project.project_id, project.active_generation_id, profile)
    calls = []
    with pytest.raises(ex.InvalidBindings, match="renderer_seed binding"):
        run(env, calls, make(f=profile))
    assert not calls


def test_renderer_seed_api_rejects_unbound_control_and_bad_values(env, monkeypatch):
    store, config, profile, _, project, _ = env
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(store.root))
    monkeypatch.setenv("MINICONSTRUCT_EXECUTION_ROOT", str(config.path.parent))
    client = TestClient(app)
    path = f"/api/execution/projects/{project.project_id}/generations/{project.active_generation_id}/renderer-seed"
    assert client.put(path, json={"profileId": str(profile.id), "settings": {"mode": "fixed", "fixedSeed": 42}}).status_code == 422
    assert client.put(path, json={"profileId": str(profile.id), "settings": {"mode": "fixed", "fixedSeed": -1}}).status_code == 422
    assert client.put(path, json={"profileId": str(profile.id), "settings": {"mode": "fixed", "fixedSeed": 2147483648}}).status_code == 422
    assert store.renderer_seed_settings(project.project_id, project.active_generation_id) is None
    okay = client.put(path, json={"profileId": str(profile.id), "settings": {"mode": "workflow_default", "fixedSeed": None}})
    assert okay.status_code == 200
    assert okay.json()["rendererSeedSettings"] == {"mode": "workflow_default", "fixedSeed": None}


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


def test_stale_selected_prompt_still_sends_exact_selection(env):
    store, _, _, _, project, _ = env
    selected = store.execution_snapshot(project.project_id, project.active_generation_id)[1].selected
    workspace = project.workspace.model_copy(deep=True)
    workspace.duration_seconds = 12
    store.save_project(project.project_id, workspace)
    calls = []
    result = run(env, calls)
    assert result.submitted and calls
    wire = json.loads(calls[-1].content)
    assert wire["prompt"]["h3"]["inputs"]["prompt"] == selected.prompt


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


def test_large_picture_prompt_derivative_never_replaces_canonical_comfy_upload(env, workspace_factory, image_asset_factory):
    store, _, profile, _, _, make = env
    asset = image_asset_factory("large-picture", role="general_visual")
    source = io.BytesIO()
    Image.new("RGB", (1536, 1536), (30, 90, 150)).save(source, "PNG")
    canonical = source.getvalue()
    asset.image.data_url = "data:image/png;base64," + base64.b64encode(canonical).decode()
    asset.image.width = asset.image.height = 1536
    project = store.create_project(workspace_factory(mode="Ref2VA", assets=[asset]))
    select(store, project, valid_ref_prompt())
    before = prepare_workflow(store, project.project_id, project.active_generation_id, profile)
    saved = store.read_generation(project.project_id, project.active_generation_id).workspace
    derivative = assemble_prompt(saved, True).messages[-1]["content"][-1]["image_url"]["url"]
    derivative_bytes = base64.b64decode(derivative.split(",", 1)[1])
    assert derivative_bytes != canonical
    assert hashlib.sha256(canonical).hexdigest() == before.asset_requirements[0].content_sha256
    after = prepare_workflow(store, project.project_id, project.active_generation_id, profile)
    assert after.asset_requirements == before.asset_requirements
    path, _ = store.execution_payload(project.project_id, project.active_generation_id,
        before.asset_requirements[0].asset_id, "image", before.asset_requirements[0].content_sha256)
    assert path.read_bytes() == canonical
    calls = []
    run(env, calls, make(project))
    assert canonical in calls[0].content
    assert derivative_bytes not in calls[0].content


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
    with pytest.raises(ex.InvalidBindings, match="no H3 prompt"):
        run(env, calls)
    assert not calls


def test_routing_reference_map_mismatch_does_not_block_textual_prompt(env, tmp_path, workspace_factory):
    store, config, _, connection, _, make = env
    profile = master(tmp_path)
    config.get = lambda category, id: profile if category == "profiles" else connection
    project = store.create_project(workspace_factory(mode="Ref2VA", assets=[_video_asset(store), audio_asset(store)]))
    select(store, project, VIDEO_PROMPT.replace("No supplied subjects.", "<Audio 1> is the sound reference."))
    store.set_execution_routing(project.project_id, project.active_generation_id,
                                GenerationRouting(assets={"clip": AssetRouting(useEmbeddedAudio=True)}))
    calls = []
    result = run(env, calls, make(project, profile))
    assert result.submitted and calls


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
    with pytest.raises(ex.InvalidBindings, match="payload.*corrupt"):
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
    data.pop("expectedExecutionFingerprint")
    response = TestClient(app).post("/api/execution/prepare", json=data)
    assert response.status_code == 200
    assert response.json()["transportReady"] is False


@pytest.mark.parametrize("mutation", ["renderer", "project_name", "reference", "renderer_seed"])
def test_prepared_send_api_revalidates_execution_without_reference_write(env, monkeypatch, tmp_path, mutation):
    """Exercise both real API boundaries with a saved Project and mocked transport."""
    import miniconstruct.api.execution_routes as routes
    from test_reference_authority import picture
    store, config, profile, _, project, make = env
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(store.root))
    monkeypatch.setenv("MINICONSTRUCT_EXECUTION_ROOT", str(tmp_path / "execution"))
    calls = []
    monkeypatch.setattr(routes, "send_generation", lambda body: run(env, calls, body))
    client = TestClient(app)
    body = make().model_dump(mode="json", by_alias=True)
    prepare_body = {key: body[key] for key in ("projectId", "generationId", "profileId")}
    initial = client.post("/api/execution/prepare", json=prepare_body)
    assert initial.status_code == 200
    body["expectedExecutionFingerprint"] = initial.json()["executionFingerprint"]
    p, g = project.project_id, project.active_generation_id
    if mutation == "renderer":
        first = run(env, calls)
        store.update_render_job(p, g, first.render_submission_id, RendererJobState(status="running"))
        calls.clear()
    elif mutation == "project_name":
        current = store.load_project(p)
        store.save_project(p, current.workspace.model_copy(update={"project_name": "Other tab rename"}),
                           expected_project_fingerprint=current.project_fingerprint)
    elif mutation == "reference":
        asset, _ = picture("blue")
        from miniconstruct.models.workspace import SubjectRecord
        store.sync_reference_assets(p, g, [asset.model_dump(mode="json", by_alias=True)],
                                    [SubjectRecord(id="subject-1", number=1)], store.load_project(p).project_fingerprint)
    else:
        store.set_renderer_seed_settings(p, g, RendererSeedSettings(mode="fixed", fixedSeed=42))

    # Neither endpoint may opportunistically save browser Reference/authoring state.
    monkeypatch.setattr(ProjectStore, "sync_reference_assets", lambda *a, **kw: pytest.fail("Send wrote References"))
    response = client.post("/api/execution/send", json=body)
    if mutation in {"reference", "renderer_seed"}:
        assert response.status_code == 422
        assert calls == []
        assert "Project changed since loading" not in response.text
        return
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["submitted"] is True
    assert len(calls) == 1 and calls[0].url.path.endswith("/prompt")
    submission = store.render_submission(p, g, result["renderSubmissionId"])
    assert submission.execution_fingerprint == body["expectedExecutionFingerprint"]
    assert str(submission.comfy_prompt_id) == result["promptId"]
    assert submission.prompt_snapshot.prompt == initial.json()["selectedPrompt"]["prompt"]


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


async def test_submission_and_take_provenance_use_exact_prepared_prompt(env):
    from miniconstruct.models.takes import TakeImport
    from test_takes import chunks
    store, _, profile, connection, project, _ = env
    calls = []
    result = run(env, calls)
    outputs = store.generation_outputs(project.project_id, project.active_generation_id)
    assert len(outputs["renderSubmissions"]) == 1 and outputs["takes"] == []
    submission = outputs["renderSubmissions"][0]
    assert submission["id"] == str(result.render_submission_id)
    assert submission["comfyPromptId"] == str(result.prompt_id)
    assert submission["workflowFingerprint"] == profile.fingerprint
    assert submission["connectionId"] == str(connection.id)
    assert len(calls) == 1 and calls[0].url.path.endswith("/prompt")
    edited = VALID_BASE.replace("Rain and distant traffic.", "Heavy rain against the shelter roof.")
    store.adopt_manual_edit_execution_prompt(project.project_id, project.active_generation_id,
                                            ManualEditAdoptionWrite(prompt=edited))
    store.clear_execution_prompt(project.project_id, project.active_generation_id)
    provenance = TakeImport(sourceKind="comfyui_submission", renderSubmissionId=result.render_submission_id)
    for _ in range(2):
        state = await store.import_take(project.project_id, project.active_generation_id, chunks(b"render"), "take.mp4", provenance)
    assert len(state["takes"]) == 2
    detail = store.take_detail(project.project_id, project.active_generation_id, state["takes"][0]["id"])
    assert detail["promptSnapshot"]["prompt"] == VALID_BASE
    assert detail["renderSubmissionId"] == str(result.render_submission_id)
    new = store.create_generation(project.project_id)
    with pytest.raises(ProjectStoreError, match="does not belong"):
        await store.import_take(project.project_id, new.active_generation_id, chunks(b"render"), "take.mp4", provenance)
    duplicated = store.duplicate_generation(project.project_id, project.active_generation_id)
    assert duplicated.outputs["renderSubmissions"] == [] and duplicated.outputs["takes"] == []
    store.activate_generation(project.project_id, project.active_generation_id)
    fork = store.duplicate_project(project.project_id, "Full provenance fork")
    assert fork.outputs["renderSubmissions"] == state["renderSubmissions"]
    assert store.take_detail(fork.project_id, fork.active_generation_id, detail["id"]) == detail


def test_rejected_send_creates_no_submission(env):
    calls = []
    with pytest.raises(ex.InvalidBindings):
        run(env, calls, failure=lambda request, calls: httpx.Response(400, json={"node_errors": {}}))
    store, _, _, _, project, _ = env
    assert store.generation_outputs(project.project_id, project.active_generation_id)["renderSubmissions"] == []
    assert len(calls) == 1


def test_accepted_send_with_local_write_failure_reports_acceptance(env, monkeypatch):
    store = env[0]
    def fail(*args, **kwargs): raise OSError("disk full")
    monkeypatch.setattr(store, "record_render_submission", fail)
    calls = []
    result = run(env, calls)
    assert result.submitted and result.prompt_id
    assert result.render_submission_id is None and "accepted" in result.provenance_warning
    assert len(calls) == 1


async def test_selection_changed_during_mock_send_cannot_rewrite_submission(env):
    from miniconstruct.models.takes import TakeImport
    from test_takes import chunks
    store, _, _, _, project, _ = env
    original = store.execution_snapshot(project.project_id, project.active_generation_id)[1].selected
    def change_selection(request, calls):
        store.clear_execution_prompt(project.project_id, project.active_generation_id)
    calls = []
    result = run(env, calls, failure=change_selection)
    assert store.execution_snapshot(project.project_id, project.active_generation_id)[1].selected is None
    state = await store.import_take(project.project_id, project.active_generation_id, chunks(b"video"), "take.mp4",
                                   TakeImport(sourceKind="comfyui_submission", renderSubmissionId=result.render_submission_id))
    detail = store.take_detail(project.project_id, project.active_generation_id, state["takes"][0]["id"])
    assert detail["promptSnapshot"]["selectionId"] == str(original.id)
    assert detail["promptSnapshot"]["prompt"] == json.loads(calls[0].content)["prompt"]["h3"]["inputs"]["prompt"]


def test_aspect_ratio_semantic_binding_reaches_mocked_send_graph(env):
    store, config, profile, connection, project, _ = env
    graph = deepcopy(profile.workflow)
    graph["h3"]["inputs"]["aspect_ratio"] = "auto"
    bindings = [item.model_dump(mode="json", by_alias=True) for item in profile.bindings]
    bindings.append({"source": {"kind": "aspect_ratio", "valueMap": {"16:9": "16:9 (Widescreen)"}},
                     "nodeId": "h3", "inputName": "aspect_ratio"})
    mapped = config.save("profiles", ex.ProfileCreate(name="Aspect", compatibleModes=list(ex.H3Mode),
        workflowJson=json.dumps(graph), bindings=bindings))
    store.save_project(project.project_id, project.workspace.model_copy(update={"aspect_ratio": "16:9"}))
    body = send.ExecutionSendRequest(projectId=project.project_id, generationId=project.active_generation_id,
                                     profileId=mapped.id, connectionId=connection.id)
    prepared = prepare_workflow(store, project.project_id, project.active_generation_id, mapped)
    assert prepared.graph["h3"]["inputs"]["aspect_ratio"] == "16:9 (Widescreen)"
    calls = []
    result = send.send_generation(body, store=store, config=config, transport=recorder(calls))
    wire = json.loads(calls[0].content)
    assert wire["prompt"]["h3"]["inputs"]["aspect_ratio"] == "16:9 (Widescreen)"
    assert result.submitted
