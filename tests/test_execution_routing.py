"""Master-capacity routing, effective labels, and Project sidecar regressions."""
import asyncio
import json
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from miniconstruct import execution as ex
from miniconstruct.app import app
from miniconstruct.execution_routing import (AssetRouting, GenerationRouting, effective_references,
                                           prompt_projection)
from miniconstruct.execution_state import SelectionWrite
from miniconstruct.h3.builder import build_h3_input_map, build_reference_manifest
from miniconstruct.models.workspace import ReferenceAsset
from miniconstruct.project_store import ProjectStore
from test_workflow_preparation import (_multi_hop_video_profile, _video_asset, VIDEO_PROMPT,
                                      select, prepared, store, offline, mapping)


def audio_asset(store, asset_id="voice"):
    async def chunks(): yield b"offline audio fixture"
    payload = asyncio.run(store.ingest_media(chunks(), "audio", "audio/wav"))
    return ReferenceAsset.model_validate({"id": asset_id, "kind": "audio", "filename": "voice.wav",
        "mimeType": "audio/wav", "role": "general_audio", "mediaPayload": payload.model_dump(by_alias=True)})


def master(tmp_path):
    profile = _multi_hop_video_profile(tmp_path)
    profile.workflow["voice"] = {"class_type": "LoadAudio", "inputs": {"audio": "test.wav"}}
    profile.workflow["136"]["inputs"]["ref_audios.ref_audio_0"] = ["voice", 0]
    profile.bindings.append(ex.Binding.model_validate({
        **mapping("reference_asset", "voice", "audio", assetKind="audio", ordinal=1),
        "routes": [{"channel": "audio", "nodeId": "136", "inputName": "ref_audios.ref_audio_0",
                    "expectedSourceNodeId": "voice", "expectedOutputIndex": 0}]}))
    return profile


@pytest.mark.parametrize("present,embedded,standalone,frames", [
    (True, False, False, True), (True, True, False, True), (False, False, False, True),
    (True, False, True, True), (True, True, True, True), (True, False, True, False)])
def test_one_master_all_route_combinations(store, tmp_path, workspace_factory, present, embedded, standalone, frames):
    profile = master(tmp_path); original = deepcopy(profile.workflow)
    assets = ([_video_asset(store)] if present else []) + ([audio_asset(store)] if standalone else [])
    project = store.create_project(workspace_factory(mode="Ref2VA" if assets else "T2VA", assets=assets))
    select(store, project, VIDEO_PROMPT if assets else __import__("test_validator").VALID_BASE)
    route = GenerationRouting(assets={"clip": AssetRouting(useFrames=frames, useEmbeddedAudio=embedded)} if present else {})
    store.set_execution_routing(project.project_id, project.active_generation_id, route)
    result = prepared(store, project, profile)
    inputs = result.graph["136"]["inputs"]
    assert ("ref_videos.ref_video_0" in inputs) == (present and frames)
    assert ("ref_video_audios.ref_video_audio_0" in inputs) == (present and embedded)
    assert ("ref_audios.ref_audio_0" in inputs) == standalone
    assert len(result.asset_requirements) == int(present and frames) + int(standalone)
    assert result.graph["171"]["inputs"]["video"] == ["170", 0]
    assert profile.workflow == original
    assert not result.transport_ready and not result.sent_to_comfyui
    if not present or not frames:
        assert result.graph["170"]["inputs"]["file"] == {"$inactiveReference": "Video 1"}
    assert [(r.label, r.channel) for r in result.reference_map if r.label.startswith("Audio")] == (
        ([("Audio 1", "embedded_audio")] if embedded else []) +
        ([(f"Audio {2 if embedded else 1}", "audio")] if standalone else []))
    if standalone:
        audio_resolution = next(item for item in result.resolutions if item.input_name == "audio")
        assert audio_resolution.label == f"Audio {2 if embedded else 1}"
        assert next(item for item in result.asset_requirements if item.input_name == "audio").semantic_label == audio_resolution.label


def test_required_video_without_route_capacity_fails_before_preparation(store, tmp_path, workspace_factory):
    profile = _multi_hop_video_profile(tmp_path)
    profile.bindings[1].presence = "required"
    profile.bindings[1].routes = []
    assert any("Configure/classify" in issue for issue in ex.profile_readiness_errors(profile.workflow, profile.bindings))
    project = store.create_project(workspace_factory(mode="Ref2VA", assets=[_video_asset(store)]))
    select(store, project, VIDEO_PROMPT)
    with pytest.raises(ex.InvalidBindings, match="Video 1: Configure/classify"):
        prepared(store, project, profile)


@pytest.mark.parametrize("embedded", [False, True])
def test_required_video_classified_routes_follow_generation_routing(store, tmp_path, workspace_factory, embedded):
    profile = _multi_hop_video_profile(tmp_path)
    profile.bindings[1].presence = "required"
    project = store.create_project(workspace_factory(mode="Ref2VA", assets=[_video_asset(store)]))
    select(store, project, VIDEO_PROMPT)
    store.set_execution_routing(project.project_id, project.active_generation_id,
                                GenerationRouting(assets={"clip": AssetRouting(useEmbeddedAudio=embedded)}))
    result = prepared(store, project, profile)
    assert len(result.asset_requirements) == 1
    assert result.graph["170"]["inputs"]["file"]["$assetRequirement"] == result.asset_requirements[0].id
    assert result.graph["171"]["inputs"]["video"] == ["170", 0]
    assert result.graph["136"]["inputs"]["ref_videos.ref_video_0"] == ["171", 0]
    assert ("ref_video_audios.ref_video_audio_0" in result.graph["136"]["inputs"]) is embedded


def test_required_audio_without_route_capacity_fails_before_preparation(store, tmp_path, workspace_factory):
    profile = master(tmp_path)
    profile.bindings[-1].presence = "required"
    profile.bindings[-1].routes = []
    assert any("Audio route capacity" in issue for issue in ex.profile_readiness_errors(profile.workflow, profile.bindings))
    project = store.create_project(workspace_factory(mode="Ref2VA", assets=[audio_asset(store)]))
    select(store, project, VIDEO_PROMPT)
    with pytest.raises(ex.InvalidBindings, match="Audio 1: Configure/classify"):
        prepared(store, project, profile)


@pytest.mark.parametrize("use_audio", [True, False])
def test_required_audio_classified_route_follows_generation_routing(store, tmp_path, workspace_factory, use_audio):
    profile = master(tmp_path)
    profile.bindings[-1].presence = "required"
    project = store.create_project(workspace_factory(mode="Ref2VA", assets=[audio_asset(store)]))
    select(store, project, VIDEO_PROMPT)
    store.set_execution_routing(project.project_id, project.active_generation_id,
                                GenerationRouting(assets={"voice": AssetRouting(useAudio=use_audio)}))
    result = prepared(store, project, profile)
    audio_requirements = [item for item in result.asset_requirements if item.asset_kind == "audio"]
    assert len(audio_requirements) == int(use_audio)
    if use_audio:
        assert result.graph["voice"]["inputs"]["audio"]["$assetRequirement"] == audio_requirements[0].id
        assert result.graph["136"]["inputs"]["ref_audios.ref_audio_0"] == ["voice", 0]
    else:
        assert result.graph["voice"]["inputs"]["audio"] == {"$inactiveReference": "Audio 1"}
        assert "ref_audios.ref_audio_0" not in result.graph["136"]["inputs"]


def test_mixed_video_effective_labels_and_prompt_input_map(store, workspace_factory):
    videos = [_video_asset(store).model_copy(update={"id": f"v{i}", "order": i}) for i in range(3)]
    workspace = workspace_factory(mode="Ref2VA", assets=videos + [audio_asset(store)])
    routing = GenerationRouting(assets={"v0": AssetRouting(useEmbeddedAudio=True), "v2": AssetRouting(useEmbeddedAudio=True)})
    refs = effective_references(workspace, routing)
    assert [(r.asset_id, r.label) for r in refs] == [
        ("v0", "Audio 1"), ("v0", "Video 1"), ("v1", "Video 2"),
        ("v2", "Audio 2"), ("v2", "Video 3"), ("voice", "Audio 3")]
    projection = prompt_projection(workspace, routing)
    inputs = build_h3_input_map(projection)
    assert [row["h3Label"] for row in inputs if row["kind"] == "audio"] == ["Audio 1", "Audio 2", "Audio 3"]
    assert next(row for row in inputs if row["id"] == "voice")["h3Label"] == "Audio 3"
    assert "Enabled embedded soundtrack" in json.dumps(build_reference_manifest(projection))
    assert len(workspace.assets) == 4
    routing.assets["v1"] = AssetRouting(useEmbeddedAudio=True)
    with pytest.raises(ValueError, match="at most 3"):
        effective_references(workspace, routing)


def test_sidecar_restart_isolation_duplicate_save_as_delete(store, tmp_path, workspace_factory):
    project = store.create_project(workspace_factory(mode="Ref2VA", assets=[_video_asset(store)]))
    select(store, project, VIDEO_PROMPT)
    directory = store.root / f"project-{project.project_id}"
    original_manifest = (directory / "project.json").read_bytes()
    before = store.execution_snapshot(project.project_id, project.active_generation_id)[1]
    routing = GenerationRouting(assets={"clip": AssetRouting(useEmbeddedAudio=True)})
    store.set_execution_routing(project.project_id, project.active_generation_id, routing)
    restarted = ProjectStore(store.root)
    assert restarted.execution_routing(project.project_id, project.active_generation_id) == routing
    assert (directory / "project.json").read_bytes() == original_manifest
    assert restarted.execution_snapshot(project.project_id, project.active_generation_id)[1] == before
    duplicate = store.duplicate_generation(project.project_id)
    assert store.execution_routing(project.project_id, duplicate.active_generation_id) == GenerationRouting()
    fork = store.duplicate_project(project.project_id, "Routing copy")
    # Generation IDs remap; durable asset IDs remain valid in copied workspaces.
    states = json.loads((store.root / f"project-{fork.project_id}" / "execution-state.json").read_text())
    assert project.active_generation_id not in states["routing"]
    assert list(states["routing"].values())[0]["assets"]["clip"]["useEmbeddedAudio"] is True
    store.activate_generation(project.project_id, project.active_generation_id)
    store.delete_generation(project.project_id, project.active_generation_id)
    assert project.active_generation_id not in json.loads((directory / "execution-state.json").read_text())["routing"]


def test_audio_label_change_blocks_prepare_without_clearing_selection(store, tmp_path, workspace_factory):
    profile = master(tmp_path)
    project = store.create_project(workspace_factory(mode="Ref2VA", assets=[_video_asset(store), audio_asset(store)]))
    prompt = VIDEO_PROMPT.replace("No supplied subjects.", "<Audio 1> is the sound reference.")
    select(store, project, prompt)
    store.set_execution_routing(project.project_id, project.active_generation_id,
        GenerationRouting(assets={"clip": AssetRouting(useEmbeddedAudio=True)}))
    with pytest.raises(ex.InvalidBindings, match="Audio 1 has a different source"):
        prepared(store, project, profile)
    status = store.execution_snapshot(project.project_id, project.active_generation_id)[1]
    assert not status.stale and status.selected.prompt == prompt


def test_v3_ambiguous_video_preserves_edges_until_classified(store, tmp_path, workspace_factory):
    profile = _multi_hop_video_profile(tmp_path)
    data = profile.model_dump(mode="json", by_alias=True)
    data["bindings"][1]["deactivationTargets"] = [
        {key: value for key, value in route.items() if key != "channel"} for route in data["bindings"][1].pop("routes")]
    config = ex.ExecutionConfig.model_validate({"schemaVersion": 3, "profiles": [data]})
    restored = config.profiles[0]
    assert ex.binding_deactivation_targets(restored.bindings[1])[1].expected_output_index == 1
    assert all(route.channel is None for route in ex.binding_routes(restored.bindings[1]))
    assert any("Classify" in issue for issue in ex.profile_readiness_errors(restored.workflow, restored.bindings))
    assert restored.workflow == profile.workflow


def test_unavailable_channel_and_audio_budget_fail_before_preparation(store, tmp_path, workspace_factory):
    profile = _multi_hop_video_profile(tmp_path, paired_audio=False)
    project = store.create_project(workspace_factory(mode="Ref2VA", assets=[_video_asset(store)]))
    select(store, project, VIDEO_PROMPT)
    store.set_execution_routing(project.project_id, project.active_generation_id,
        GenerationRouting(assets={"clip": AssetRouting(useEmbeddedAudio=True)}))
    with pytest.raises(ex.InvalidBindings, match="no capacity for enabled route embedded_audio"):
        prepared(store, project, profile)
    other = store.create_project(workspace_factory(mode="Ref2VA", assets=[_video_asset(store)] + [audio_asset(store, f"a{i}") for i in range(3)]))
    with pytest.raises(ex.InvalidBindings, match="at most 3"):
        store.set_execution_routing(other.project_id, other.active_generation_id,
            GenerationRouting(assets={"clip": AssetRouting(useEmbeddedAudio=True)}))


def test_routing_api_scope_and_unknown_asset(store, monkeypatch, workspace_factory):
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(store.root))
    project = store.create_project(workspace_factory(mode="Ref2VA", assets=[_video_asset(store)]))
    url = f"/api/execution/projects/{project.project_id}/generations/{project.active_generation_id}/routing"
    with TestClient(app) as client:
        assert client.get(url).json()["referenceMap"][0]["channel"] == "video_frames"
        response = client.put(url, json={"assets": {"clip": {"useEmbeddedAudio": True}}})
        assert response.status_code == 200, response.text
        assert [ref["label"] for ref in response.json()["referenceMap"]] == ["Audio 1", "Video 1"]
        assert client.put(url, json={"assets": {"wrong-generation-asset": {}}}).status_code == 422
        assert client.get(url).json()["routing"]["assets"]["clip"]["useEmbeddedAudio"]


@pytest.mark.asyncio
async def test_generation_pipeline_records_the_map_used_by_prompt_writer(store, tmp_path, workspace_factory):
    from miniconstruct.models.api import GenerationRequest
    from miniconstruct.api.routes import stream_generation_events
    from miniconstruct.generation import generate_workspace_prompts
    from miniconstruct.execution_authoring import execution_authoring_fingerprint
    from miniconstruct.llm.client import LLMStreamEvent
    from test_streaming_route import decode_event
    assets = await asyncio.to_thread(lambda: [_video_asset(store), audio_asset(store)])
    workspace = workspace_factory(mode="Ref2VA", assets=assets)
    prompt = VIDEO_PROMPT.replace("No supplied subjects.", "<Audio 1> is the embedded sound reference.\n<Audio 2> is the separate sound reference.")
    calls = []
    class Writer:
        def __init__(self, _): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def generate(self, messages): calls.append(messages); return prompt
        async def stream_events(self, messages): calls.append(messages); yield LLMStreamEvent("content", prompt)
    request = GenerationRequest.model_validate({"workspace": workspace.model_dump(by_alias=True),
        "llm": {"baseUrl": "http://unused.test/v1", "model": "offline"},
        "executionRouting": {"assets": {"clip": {"useEmbeddedAudio": True}}}})
    output = (await generate_workspace_prompts(request, Writer)).variations[0].generated
    assert output.validation.valid, output.validation
    assert output.source_fingerprint == execution_authoring_fingerprint(workspace)
    assert [(r.asset_id, r.label) for r in output.reference_map] == [("clip", "Audio 1"), ("clip", "Video 1"), ("voice", "Audio 2")]
    assert "Enabled embedded soundtrack" in json.dumps(calls)
    events = [decode_event(raw) async for raw in stream_generation_events(request, Writer)]
    complete = next(data for event, data in events if event == "complete")
    assert complete["referenceMap"] == [ref.model_dump(by_alias=True) for ref in output.reference_map]
    project = store.create_project(workspace)
    store.set_execution_routing(project.project_id, project.active_generation_id, request.execution_routing)
    store.select_execution_prompt(project.project_id, project.active_generation_id,
        SelectionWrite(prompt=prompt, sourceFingerprint=output.source_fingerprint, referenceMap=output.reference_map))
    assert prepared(store, project, master(tmp_path)).reference_map == output.reference_map


def test_disabled_standalone_audio_no_requirement_or_filename_fallback(store, tmp_path, workspace_factory):
    project = store.create_project(workspace_factory(mode="Ref2VA", assets=[_video_asset(store), audio_asset(store)]))
    select(store, project, VIDEO_PROMPT)
    store.set_execution_routing(project.project_id, project.active_generation_id,
        GenerationRouting(assets={"voice": AssetRouting(useAudio=False)}))
    result = prepared(store, project, master(tmp_path))
    assert [r.asset_kind for r in result.asset_requirements] == ["video"]
    assert result.graph["voice"]["inputs"]["audio"] == {"$inactiveReference": "Audio 1"}
    assert "ref_audios.ref_audio_0" not in result.graph["136"]["inputs"]


def test_named_h3_socket_order_cannot_silently_renumber(store, tmp_path, workspace_factory):
    profile = master(tmp_path)
    route = profile.bindings[-1].routes[0]
    profile.workflow["136"]["inputs"]["ref_audios.ref_audio_1"] = profile.workflow["136"]["inputs"].pop(route.input_name)
    route.input_name = "ref_audios.ref_audio_1"
    project = store.create_project(workspace_factory(mode="Ref2VA", assets=[_video_asset(store), audio_asset(store)]))
    select(store, project, VIDEO_PROMPT)
    with pytest.raises(ex.InvalidBindings, match="socket order"):
        prepared(store, project, profile)


def test_input_map_api_and_generation_budget_are_offline(store, workspace_factory):
    workspace = workspace_factory(mode="Ref2VA", assets=[_video_asset(store), audio_asset(store)])
    body = {"mode": "Ref2VA", "assets": [
        {"id": asset.id, "kind": asset.kind, "filename": asset.filename, "role": asset.role, "order": asset.order}
        for asset in workspace.assets], "executionRouting": {"assets": {"clip": {"useEmbeddedAudio": True}}}}
    with TestClient(app) as client:
        response = client.post("/api/reference-manifest", json=body)
        assert response.status_code == 200, response.text
        entries = response.json()["entries"]
        assert next(row for row in entries if row["id"] == "voice")["h3Label"] == "Audio 2"
        assert any(row["metadata"] == ["Embedded video soundtrack"] for row in entries)
        crowded = workspace.model_copy(update={"assets": workspace.assets + [audio_asset(store, "a2"), audio_asset(store, "a3")]})
        request = {"workspace": crowded.model_dump(by_alias=True), "llm": {"baseUrl": "http://forbidden.test", "model": "offline"},
                   "executionRouting": body["executionRouting"]}
        for route in ("generate", "generate-stream", "assemble"):
            result = client.post(f"/api/{route}", json=request)
            assert result.status_code == 422, result.text
            assert "at most 3" in result.text
