"""Offline F3 regression matrix: actual ProjectStore, synthetic graphs/payloads."""
from copy import deepcopy
import asyncio
import json
import shutil
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from miniconstruct.app import app
from miniconstruct import execution as ex
from miniconstruct.execution_authoring import execution_authoring_fingerprint as fingerprint
from miniconstruct.execution_state import SelectionWrite
from miniconstruct.h3.builder import numbered_assets
from miniconstruct.h3.validator import validate_workspace_prompt
from miniconstruct.models.workspace import ReferenceAsset, validate_authoring_workspace
from miniconstruct.project_store import ProjectStore, ProjectStoreError
from miniconstruct.workflow_preparation import prepare_workflow
from test_validator import VALID_BASE, valid_ref_prompt


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("F3 must never invoke real HTTP transport")
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)


def mapping(kind, node, name, **source):
    return {"source": {"kind": kind, **source}, "nodeId": node, "inputName": name}


def capacity_profile(count=3, *, av=False):
    graph = {"h3": {"class_type": "SyntheticH3", "inputs": {"prompt": "old prompt", "duration": 99,
                "model": "model.safetensors", "lora": "detail.safetensors", "sampler": "euler",
                "sigma": 1.5, "opaque": {"do": ["not", "interpret"]}, "context": ["context", 0]}},
             "context": {"class_type": "PromptSource", "inputs": {}}}
    bindings = [mapping("generated_prompt", "h3", "prompt"), mapping("duration", "h3", "duration")]
    slots = [("image", n, "LoadImage", "image") for n in range(1, count + 1)]
    if av:
        slots += [("audio", 1, "LoadAudio", "audio"), ("video", 1, "LoadVideo", "file")]
    for kind, ordinal, loader, name in slots:
        node = f"{kind}-{ordinal}"
        graph[node] = {"class_type": loader, "inputs": {name: f"old_{node}.test"}}
        graph["h3"]["inputs"][node] = [node, 0]
        bindings.append({**mapping("reference_asset", node, name, assetKind=kind, ordinal=ordinal),
                         "controlledConsumers": [{"nodeId": "h3", "inputName": node, "outputIndex": 0}]})
        if kind == "video":
            bindings[-1].pop("controlledConsumers")
            bindings[-1]["routes"] = [{"nodeId": "h3", "inputName": node, "expectedSourceNodeId": node,
                                      "expectedOutputIndex": 0, "channel": "video_frames"}]
    return ex.ProfileCreate(name="Capacity", compatibleModes=list(ex.H3Mode), workflowJson=json.dumps(graph), bindings=bindings)


@pytest.fixture
def store(tmp_path):
    return ProjectStore(tmp_path / "projects")


@pytest.fixture
def profile(tmp_path):
    return ex.ExecutionStore(tmp_path / "execution").save("profiles", capacity_profile())


def select(store, project, prompt=VALID_BASE, **overrides):
    return store.select_execution_prompt(project.project_id, project.active_generation_id,
        SelectionWrite(prompt=prompt, sourceFingerprint=fingerprint(project.workspace), **overrides))


def prepared(store, project, profile):
    return prepare_workflow(store, project.project_id, project.active_generation_id, profile)


def ref_workspace(workspace_factory, image_asset_factory, count=1):
    return workspace_factory(mode="Ref2VA", assets=[image_asset_factory(f"pic-{n}", role="general_visual", order=n,
        filename=f"reference-{n}.png") for n in range(1, count + 1)])


def test_none_by_default_and_restart(store, workspace_factory):
    p = store.create_project(workspace_factory())
    assert store.execution_snapshot(p.project_id, p.active_generation_id)[1].selected is None
    chosen = select(store, p)
    assert chosen.selected.prompt == VALID_BASE and not chosen.stale
    restored = ProjectStore(store.root).execution_snapshot(p.project_id, p.active_generation_id)[1]
    assert restored == chosen
    assert "schemaVersion" in json.loads((store.root / f"project-{p.project_id}" / "execution-state.json").read_text())


@pytest.mark.parametrize("prompt", ["", "   ", "not an H3 prompt", "Hello\n" + VALID_BASE])
def test_bad_selection_rejected(store, workspace_factory, prompt):
    p = store.create_project(workspace_factory())
    with pytest.raises((ValidationError, ex.InvalidBindings)):
        select(store, p, prompt)
    assert store.execution_snapshot(p.project_id, p.active_generation_id)[1].selected is None


def test_snapshot_mismatch_rejected(store, workspace_factory):
    p = store.create_project(workspace_factory())
    with pytest.raises(ex.InvalidBindings, match="different Generation state"):
        store.select_execution_prompt(p.project_id, p.active_generation_id, SelectionWrite(prompt=VALID_BASE, sourceFingerprint="f" * 64))


def test_replace_clear_scoped_and_story_unchanged(store, workspace_factory):
    p = store.create_project(workspace_factory())
    other = store.create_project(workspace_factory())
    story = store.read_story_snapshot(p.project_id)
    first = select(store, p).selected
    second = select(store, p, VALID_BASE.replace("Rain", "Wind")).selected
    assert second.id != first.id and second.prompt != first.prompt
    assert store.execution_snapshot(other.project_id, other.active_generation_id)[1].selected is None
    after = store.read_story_snapshot(p.project_id)
    assert story.fingerprint == after.fingerprint and story.manifest.updated_at == after.manifest.updated_at
    assert after.manifest.schema_version == 5
    assert store.clear_execution_prompt(p.project_id, p.active_generation_id).selected is None
    assert store.read_story_snapshot(p.project_id).fingerprint == story.fingerprint


def test_new_duplicate_delete_generation(store, workspace_factory):
    p = store.create_project(workspace_factory())
    original = select(store, p).selected
    duplicate = store.duplicate_generation(p.project_id)
    assert store.execution_snapshot(p.project_id, duplicate.active_generation_id)[1].selected is None
    chosen = select(store, duplicate).selected
    store.delete_generation(p.project_id, duplicate.active_generation_id)
    assert store.execution_snapshot(p.project_id, p.active_generation_id)[1].selected == original
    sidecar = json.loads((store.root / f"project-{p.project_id}" / "execution-state.json").read_text())
    assert duplicate.active_generation_id not in sidecar["selections"]
    fresh = store.create_generation(p.project_id)
    assert store.execution_snapshot(p.project_id, fresh.active_generation_id)[1].selected is None


@pytest.mark.parametrize("changed", [False, True])
def test_save_as_preserves_provenance_and_remaps_ids(store, workspace_factory, changed):
    p = store.create_project(workspace_factory())
    selected = select(store, p).selected
    fork = store.duplicate_project(p.project_id, "Fork", active_workspace=workspace_factory(durationSeconds=9) if changed else None)
    result = store.execution_snapshot(fork.project_id, fork.active_generation_id)[1]
    assert fork.active_generation_id != p.active_generation_id
    assert result.selected == selected and result.stale == changed
    assert store.execution_snapshot(p.project_id, p.active_generation_id)[1].selected == selected


def test_directory_copy_and_project_delete(store, workspace_factory, tmp_path):
    p = store.create_project(workspace_factory())
    chosen = select(store, p)
    copied = tmp_path / "backup"
    shutil.copytree(store.root, copied)
    assert ProjectStore(copied).execution_snapshot(p.project_id, p.active_generation_id)[1] == chosen
    store.delete_project(p.project_id)
    assert not (store.root / f"project-{p.project_id}").exists()


def test_stale_save_blocks_prepare_and_revert_restores(store, profile, workspace_factory):
    p = store.create_project(workspace_factory())
    chosen = select(store, p)
    store.save_project(p.project_id, workspace_factory(durationSeconds=9))
    state = store.execution_snapshot(p.project_id, p.active_generation_id)[1]
    assert state.stale and state.selected == chosen.selected
    with pytest.raises(ex.InvalidBindings, match="stale"):
        prepared(store, p, profile)
    store.save_project(p.project_id, workspace_factory())
    assert not store.execution_snapshot(p.project_id, p.active_generation_id)[1].stale
    assert prepared(store, p, profile).ready


@pytest.mark.parametrize("change", [
    {"duration_seconds": 9}, {"creative_request": "Changed"}, {"dialogue": "Hello"},
    {"shots": 3}, {"aspect_ratio": "9:16"}, {"reference_labels": "Changed"},
    {"creative_interpretation": "expansive"}, {"variations": 2},
])
def test_fingerprint_authoring_fields(workspace_factory, change):
    w = workspace_factory()
    assert fingerprint(w) != fingerprint(w.model_copy(update=change))


@pytest.mark.parametrize("field,value", [("role", "environment"), ("notes", "different"), ("options", {"crop": True}),
                                         ("filename", "renamed.png")])
def test_reference_metadata_affects_fingerprint(workspace_factory, image_asset_factory, field, value):
    w = ref_workspace(workspace_factory, image_asset_factory)
    changed = w.model_copy(deep=True)
    setattr(changed.assets[0], field, value)
    assert fingerprint(changed) != fingerprint(w)


def test_payload_storage_invariant_and_content_change(store, workspace_factory, image_asset_factory):
    w = ref_workspace(workspace_factory, image_asset_factory)
    p = store.create_project(w)
    hydrated = store.load_project(p.project_id).workspace
    assert fingerprint(w) == fingerprint(hydrated) == fingerprint(deepcopy(w))
    changed = deepcopy(w)
    changed.assets[0].image.data_url = "data:image/png;base64,AQ=="
    assert fingerprint(changed) != fingerprint(w)
    assert fingerprint(w.model_copy(update={"project_name": "Renamed", "project_id": str(uuid4())})) == fingerprint(w)


def test_subject_relationship_changes(workspace_factory, image_asset_factory):
    w = workspace_factory(mode="Ref2VA", assets=[image_asset_factory()])
    changed = deepcopy(w)
    changed.assets[0].subject_identity.focus = "face"
    assert fingerprint(changed) != fingerprint(w)
    changed = deepcopy(w)
    changed.subjects[0].number = 3
    assert fingerprint(changed) != fingerprint(w)


@pytest.mark.parametrize("mode,roles", [
    ("T2VA", []), ("I2VA", ["first_frame_anchor"]),
    ("FL2VA", ["last_frame_anchor", "first_frame_anchor"]),
    ("L2VA", ["last_frame_anchor"]), ("Ref2VA", ["general_visual"]),
])
def test_all_modes_and_canonical_anchors(store, profile, workspace_factory, image_asset_factory, mode, roles):
    w = workspace_factory(mode=mode, assets=[image_asset_factory(f"a-{i}", role=role, order=i) for i, role in enumerate(roles)])
    prompt = valid_ref_prompt() if mode == "Ref2VA" else VALID_BASE
    if mode == "I2VA":
        prompt = "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.\n\n" + prompt
    if mode == "FL2VA":
        prompt = "How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark; Picture 2 (from Shot 2) aligns with the 8.00-second mark.\n\n" + prompt
    if mode == "L2VA":
        prompt = "How the reference pictures align with the target video — <Picture 1> (from [Shot 2]) aligns with the 8.00-second mark.\n\n" + prompt
    p = store.create_project(w)
    select(store, p, prompt)
    result = prepared(store, p, profile)
    assert result.mode == mode and len(result.asset_requirements) == len(roles)
    assert result.graph["h3"]["inputs"]["prompt"] == prompt
    assert result.graph["h3"]["inputs"]["duration"] == 8
    if mode == "FL2VA":
        assert result.asset_requirements[0].asset_id == "a-1"


def test_compiler_copy_determinism_and_no_connection(store, profile, workspace_factory):
    p = store.create_project(workspace_factory())
    select(store, p)
    original = profile.model_dump(mode="json")
    a, b = prepared(store, p, profile), prepared(store, p, profile)
    assert a == b and not a.sent_to_comfyui and not a.transport_ready
    assert profile.model_dump(mode="json") == original
    a.graph["h3"]["inputs"]["model"] = "mutated"
    assert b.graph["h3"]["inputs"]["model"] == "model.safetensors"
    assert profile.model_dump(mode="json") == original


@pytest.mark.parametrize("failure", ["missing_selection", "mode", "missing_prompt_binding", "missing_reference_binding"])
def test_prepare_rejections(store, profile, workspace_factory, image_asset_factory, failure):
    w = ref_workspace(workspace_factory, image_asset_factory) if failure == "missing_reference_binding" else workspace_factory()
    p = store.create_project(w)
    if failure != "missing_selection": select(store, p, valid_ref_prompt() if failure == "missing_reference_binding" else VALID_BASE)
    if failure == "mode": profile.compatible_modes = [ex.H3Mode.I2VA]
    if failure == "missing_prompt_binding": profile.bindings = profile.bindings[1:]
    if failure == "missing_reference_binding": profile.bindings = profile.bindings[:2]
    with pytest.raises(ex.InvalidBindings): prepared(store, p, profile)


@pytest.mark.parametrize("count", [1, 2, 3])
def test_same_profile_variable_capacity(store, profile, workspace_factory, image_asset_factory, count):
    p = store.create_project(ref_workspace(workspace_factory, image_asset_factory, count))
    select(store, p, valid_ref_prompt())
    before = deepcopy(profile.workflow)
    result = prepared(store, p, profile)
    assert [a.semantic_label for a in result.asset_requirements] == [f"Picture {n}" for n in range(1, count + 1)]
    assert result.inactive_slots == [f"Picture {n}" for n in range(count + 1, 4)]
    for n in range(count + 1, 4):
        assert f"image-{n}" not in result.graph["h3"]["inputs"]
        assert result.graph[f"image-{n}"]["inputs"]["image"] == {"$inactiveReference": f"Picture {n}"}
    assert profile.workflow == before
    dumped = result.model_dump_json(by_alias=True)
    assert "base64" not in dumped and str(store.root) not in dumped
    for requirement in result.asset_requirements:
        assert len(requirement.content_sha256) == 64 and len(requirement.id) == 64
        assert result.graph[requirement.node_id]["inputs"][requirement.input_name] == {"$assetRequirement": requirement.id}
        assert requirement.filename == f"reference-{requirement.semantic_label[-1]}.png"


def test_missing_ordinal_never_substitutes_or_compacts(store, profile, workspace_factory, image_asset_factory):
    p = store.create_project(ref_workspace(workspace_factory, image_asset_factory, 1))
    select(store, p, valid_ref_prompt())
    profile.bindings[3].source.ordinal = 4
    result = prepared(store, p, profile)
    assert "Picture 4" in result.inactive_slots and len(result.asset_requirements) == 1
    assert result.asset_requirements[0].semantic_label == "Picture 1"


@pytest.mark.parametrize("failure", ["required", "unconfigured", "undeclared_consumer", "shared_loader"])
def test_absent_optional_safety(store, profile, workspace_factory, failure):
    p = store.create_project(workspace_factory()); select(store, p)
    before = deepcopy(profile.workflow)
    if failure == "required": profile.bindings[2].presence = "required"
    if failure == "unconfigured": profile.bindings[2].controlled_consumers = []
    if failure == "undeclared_consumer":
        profile.workflow["sizing"] = {"class_type": "Sizing", "inputs": {"image": ["image-1", 0]}}
        before = deepcopy(profile.workflow)
    if failure == "shared_loader":
        profile.workflow["image-1"]["inputs"]["another"] = "test.png"
        profile.bindings.append(ex.Binding.model_validate(mapping("reference_asset", "image-1", "another", assetKind="image", ordinal=2)))
        before = deepcopy(profile.workflow)
    with pytest.raises(ex.InvalidBindings, match="required|safely disabled|shared"):
        prepared(store, p, profile)
    assert profile.workflow == before


def test_multi_consumer_explicit_contract_and_required_present(store, profile, workspace_factory, image_asset_factory):
    profile.workflow["sizing"] = {"class_type": "Sizing", "inputs": {
        "image": ["image-1", 1], "scale": 2, "context": ["context", 0]}}
    p = store.create_project(ref_workspace(workspace_factory, image_asset_factory))
    select(store, p, valid_ref_prompt())
    profile.bindings[2].presence = "required"
    assert prepared(store, p, profile).graph["sizing"]["inputs"]["image"] == ["image-1", 1]
    profile.bindings[2].presence = "optional"
    profile.bindings[2].controlled_consumers.append(ex.ControlledConsumer(nodeId="sizing", inputName="image", outputIndex=1))
    blank = store.create_project(workspace_factory()); select(store, blank)
    result = prepared(store, blank, profile)
    assert result.graph["sizing"]["inputs"] == {"scale": 2, "context": ["context", 0]}
    assert "image-1" not in result.graph["h3"]["inputs"]
    assert profile.workflow["sizing"]["inputs"]["image"] == ["image-1", 1]


@pytest.mark.parametrize("kind", ["audio", "video"])
def test_av_legacy_metadata_persistence_and_missing_payload(store, tmp_path, workspace_factory, image_asset_factory, kind):
    w = ref_workspace(workspace_factory, image_asset_factory)
    w.assets.append(ReferenceAsset.model_validate({"id": kind, "kind": kind, "filename": f"test.{kind}",
        "mimeType": f"{kind}/synthetic", "role": f"general_{kind}", "durationSeconds": 4}))
    p = store.create_project(w)
    reloaded = store.load_project(p.project_id)
    assert reloaded.workspace.assets[-1] == w.assets[-1]
    # Active Workspace envelope preserves metadata only; no A/V bytes exist.
    from miniconstruct.models.api import ProjectEnvelope
    envelope = ProjectEnvelope.model_validate({"format": "MiniConstruct Project", "schemaVersion": 1,
        "workspace": reloaded.workspace.model_dump(mode="json", by_alias=True)})
    assert envelope.workspace.assets[-1].image is None
    profile = ex.ExecutionStore(tmp_path / "ex").save("profiles", capacity_profile(av=True))
    select(store, p, valid_ref_prompt())
    with pytest.raises(ex.InvalidBindings, match="metadata only"):
        prepared(store, p, profile)


def test_missing_picture_payload_fails(store, workspace_factory, image_asset_factory, profile):
    p = store.create_project(ref_workspace(workspace_factory, image_asset_factory)); select(store, p, valid_ref_prompt())
    payload = next((store.root / f"project-{p.project_id}" / "assets" / "pictures").iterdir())
    payload.unlink()
    with pytest.raises(ProjectStoreError, match="missing"): prepared(store, p, profile)


def _video_asset(store):
    async def chunks():
        yield b"synthetic video bytes"
    payload = asyncio.run(store.ingest_media(chunks(), "video", "video/mp4"))
    return ReferenceAsset.model_validate({"id": "clip", "kind": "video", "filename": "clip.mp4",
        "mimeType": "video/mp4", "role": "general_video", "durationSeconds": 1,
        "mediaPayload": payload.model_dump(by_alias=True)})


VIDEO_PROMPT = """subject_definitions:
No supplied subjects.

summary:
[reference generation] A cyclist crosses a station.

retention_analysis:
The scene is fully_preserved.

detailed_description:
The target uses live action.
[Shot 1] A cyclist crosses a station.

overall_soundscape:
Station ambience.

non_diegetic_music:
N/A"""


def _multi_hop_video_profile(tmp_path, *, paired_audio=True, unrelated=False):
    graph = {
        "h3": {"class_type": "SyntheticH3", "inputs": {"prompt": "old"}},
        "170": {"class_type": "LoadVideo", "inputs": {"file": "old.mp4"}},
        "171": {"class_type": "GetVideoComponents", "inputs": {"video": ["170", 0]}},
        "136": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {
            "ref_videos.ref_video_0": ["171", 0],
            **({"ref_video_audios.ref_video_audio_0": ["171", 1]} if paired_audio else {}),
            "context": ["prompt-source", 0]}},
        "prompt-source": {"class_type": "PromptSource", "inputs": {}},
    }
    targets = [{"channel": "video_frames", "nodeId": "136", "inputName": "ref_videos.ref_video_0",
                "expectedSourceNodeId": "171", "expectedOutputIndex": 0}]
    if paired_audio:
        targets.append({"channel": "embedded_audio", "nodeId": "136", "inputName": "ref_video_audios.ref_video_audio_0",
                        "expectedSourceNodeId": "171", "expectedOutputIndex": 1})
    if unrelated:
        graph["other"] = {"class_type": "UnrelatedOutput", "inputs": {"media": ["171", 1]}}
    data = ex.ProfileCreate(name="Multi-hop", compatibleModes=list(ex.H3Mode), workflowJson=json.dumps(graph),
        bindings=[mapping("generated_prompt", "h3", "prompt"),
                  {**mapping("reference_asset", "170", "file", assetKind="video", ordinal=1),
                   "routes": targets}])
    return ex.ExecutionStore(tmp_path / "execution").save("profiles", data)


def test_multihop_video_absence_cuts_only_declared_terminals(store, tmp_path, workspace_factory):
    profile = _multi_hop_video_profile(tmp_path)
    project = store.create_project(workspace_factory(mode="T2VA")); select(store, project)
    result = prepared(store, project, profile)
    assert result.graph["170"]["inputs"]["file"] == {"$inactiveReference": "Video 1"}
    assert result.graph["171"]["inputs"]["video"] == ["170", 0]
    assert "ref_videos.ref_video_0" not in result.graph["136"]["inputs"]
    assert "ref_video_audios.ref_video_audio_0" not in result.graph["136"]["inputs"]
    assert result.inactive_slots == ["Video 1"] and result.asset_requirements == []


def test_multihop_video_present_uses_one_requirement_for_paired_audio(store, tmp_path, workspace_factory):
    profile = _multi_hop_video_profile(tmp_path)
    project = store.create_project(workspace_factory(mode="Ref2VA", assets=[_video_asset(store)])); select(store, project, VIDEO_PROMPT)
    from miniconstruct.execution_routing import GenerationRouting, AssetRouting
    store.set_execution_routing(project.project_id, project.active_generation_id,
                                GenerationRouting(assets={"clip": AssetRouting(useEmbeddedAudio=True)}))
    result = prepared(store, project, profile)
    assert len(result.asset_requirements) == 1 and result.asset_requirements[0].asset_kind == "video"
    assert result.graph["170"]["inputs"]["file"]["$assetRequirement"] == result.asset_requirements[0].id
    assert result.graph["171"]["inputs"]["video"] == ["170", 0]
    assert result.graph["136"]["inputs"]["ref_videos.ref_video_0"] == ["171", 0]
    assert result.graph["136"]["inputs"]["ref_video_audios.ref_video_audio_0"] == ["171", 1]


def test_multihop_shared_undeclared_path_fails_without_pruning(store, tmp_path, workspace_factory):
    profile = _multi_hop_video_profile(tmp_path, unrelated=True)
    project = store.create_project(workspace_factory(mode="T2VA")); select(store, project)
    with pytest.raises(ex.InvalidBindings, match="still reaches executable workflow output"):
        prepared(store, project, profile)
    assert profile.workflow["other"]["inputs"]["media"] == ["171", 1]


def test_multihop_intermediate_only_cut_is_rejected(store, tmp_path, workspace_factory):
    profile = _multi_hop_video_profile(tmp_path)
    profile.bindings[1].routes = [ex.ReferenceRoute(channel="video_frames",
        nodeId="171", inputName="video", expectedSourceNodeId="170", expectedOutputIndex=0)]
    project = store.create_project(workspace_factory(mode="T2VA")); select(store, project)
    with pytest.raises(ex.InvalidBindings, match="outside the safe reference-branch boundary"):
        prepared(store, project, profile)


def test_multihop_post_boundary_cut_is_rejected(store, tmp_path, workspace_factory):
    profile = _multi_hop_video_profile(tmp_path, paired_audio=False)
    profile.workflow["outside"] = {"class_type": "PromptSource", "inputs": {}}
    profile.workflow["136"]["inputs"]["prompt"] = ["outside", 0]
    profile.workflow["downstream"] = {"class_type": "SaveVideo", "inputs": {"video": ["136", 0]}}
    profile.bindings[1].routes = [ex.ReferenceRoute(channel="video_frames",
        nodeId="downstream", inputName="video", expectedSourceNodeId="136", expectedOutputIndex=0)]
    project = store.create_project(workspace_factory(mode="T2VA")); select(store, project)
    with pytest.raises(ex.InvalidBindings, match="outside the safe reference-branch boundary"):
        prepared(store, project, profile)


def test_ambiguous_cycle_uses_conservative_boundary_failure(store, tmp_path, workspace_factory):
    profile = _multi_hop_video_profile(tmp_path, paired_audio=False)
    profile.workflow["136"]["inputs"].pop("context")
    profile.workflow["136"]["inputs"]["loop"] = ["172", 0]
    profile.workflow["172"] = {"class_type": "LoopAdapter", "inputs": {"back": ["136", 0]}}
    project = store.create_project(workspace_factory(mode="T2VA")); select(store, project)
    with pytest.raises(ex.InvalidBindings, match="safe optional-reference boundary"):
        prepared(store, project, profile)


def test_sink_without_merge_is_not_a_safe_boundary(store, tmp_path, workspace_factory):
    profile = _multi_hop_video_profile(tmp_path, paired_audio=False)
    profile.workflow["136"]["inputs"].pop("context")
    profile.workflow["downstream"] = {"class_type": "SaveVideo", "inputs": {"video": ["136", 0]}}
    profile.bindings[1].routes = [ex.ReferenceRoute(channel="video_frames",
        nodeId="downstream", inputName="video", expectedSourceNodeId="136", expectedOutputIndex=0)]
    project = store.create_project(workspace_factory(mode="T2VA")); select(store, project)
    with pytest.raises(ex.InvalidBindings, match="safe optional-reference boundary"):
        prepared(store, project, profile)


def test_unbound_constants_and_known_loader_warnings(store, profile, workspace_factory):
    for node, cls, name in [("unused-image", "LoadImage", "image"), ("unused-audio", "LoadAudio", "audio"),
                            ("unused-video", "LoadVideo", "file"), ("custom", "MyLoadImage", "image")]:
        profile.workflow[node] = {"class_type": cls, "inputs": {name: "<img onerror=evil>.test", "other": 42}}
    p = store.create_project(workspace_factory()); select(store, p)
    result = prepared(store, p, profile)
    assert result.graph["h3"]["inputs"]["opaque"] == {"do": ["not", "interpret"]}
    assert result.graph["custom"] == profile.workflow["custom"]
    warnings = "\n".join(result.warnings)
    for cls in ("LoadImage", "LoadAudio", "LoadVideo"): assert f"Unbound {cls}" in warnings
    assert "MyLoadImage" not in warnings and "<img onerror=evil>.test" in warnings


def test_safe_inspection_redacts_payload_paths_secrets(store, profile, workspace_factory):
    profile.workflow["h3"]["inputs"].update({"file": "C:\\private\\model", "api_key": "secret-value", "blob": "data:image/png;base64,AA=="})
    p = store.create_project(workspace_factory()); select(store, p)
    result = prepared(store, p, profile)
    text = result.model_dump_json()
    assert "secret-value" not in text and "base64" not in text and "C:" not in text
    assert profile.workflow["h3"]["inputs"]["api_key"] == "secret-value"


def test_legacy_v1_migration_no_consumer_invention(tmp_path, store, workspace_factory):
    execution = ex.ExecutionStore(tmp_path / "ex")
    profile = execution.save("profiles", capacity_profile())
    raw = json.loads(execution.path.read_text()); raw["schemaVersion"] = 1
    for binding in raw["profiles"][0]["bindings"]:
        binding.pop("presence", None); binding.pop("controlledConsumers", None)
    execution.path.write_text(json.dumps(raw))
    restored = ex.ExecutionStore(tmp_path / "ex").get("profiles", profile.id)
    assert restored.bindings[0].source.kind == "generated_prompt"
    assert restored.bindings[2].presence == "optional" and not restored.bindings[2].controlled_consumers
    assert ex.profile_readiness_errors(restored.workflow, restored.bindings)
    p = store.create_project(workspace_factory()); select(store, p)
    with pytest.raises(ex.InvalidBindings, match="safely disabled"): prepared(store, p, restored)
    execution.save("connections", ex.ConnectionWrite(name="unused", baseUrl="http://not-contacted.test"))
    assert json.loads(execution.path.read_text())["schemaVersion"] == 3


def test_v2_direct_consumer_migrates_to_exact_v3_target(tmp_path):
    execution = ex.ExecutionStore(tmp_path / "ex")
    profile = execution.save("profiles", capacity_profile(1))
    raw = json.loads(execution.path.read_text()); raw["schemaVersion"] = 2
    raw["profiles"][0]["bindings"][2].pop("deactivationTargets", None)
    raw["profiles"][0]["bindings"][2]["controlledConsumers"] = [
        {"nodeId": "h3", "inputName": "image-1", "outputIndex": 0}]
    execution.path.write_text(json.dumps(raw))
    restored = execution.get("profiles", profile.id)
    assert restored.bindings[2].deactivation_targets[0].model_dump(by_alias=True) == {
        "nodeId": "h3", "inputName": "image-1", "expectedSourceNodeId": "image-1", "expectedOutputIndex": 0}
    execution.save("connections", ex.ConnectionWrite(name="migrate", baseUrl="http://migration.test"))
    persisted = json.loads(execution.path.read_text())
    assert persisted["schemaVersion"] == 3
    migrated = persisted["profiles"][0]["bindings"][2]
    assert "controlledConsumers" not in migrated and migrated["deactivationTargets"][0]["expectedSourceNodeId"] == "image-1"


@pytest.mark.parametrize("change", ["wrong_link", "duplicate_control", "non_reference"])
def test_typed_contract_validation(tmp_path, change):
    data = capacity_profile()
    if change == "wrong_link": data.bindings[2].controlled_consumers[0].output_index = 2
    if change == "duplicate_control": data.bindings[2].controlled_consumers *= 2
    if change == "non_reference":
        with pytest.raises(ValidationError):
            ex.Binding.model_validate({**mapping("duration", "h3", "duration"), "presence": "required"})
        return
    with pytest.raises(ex.InvalidBindings): ex.ExecutionStore(tmp_path).save("profiles", data)


@pytest.mark.parametrize("mode", ["I2VA", "FL2VA", "L2VA", "Ref2VA"])
def test_authoring_drafts_cannot_bypass_mode_readiness(store, workspace_factory, mode):
    w = workspace_factory().model_dump(mode="json", by_alias=True); w["mode"] = mode
    p = store.create_project(validate_authoring_workspace(w))
    with pytest.raises(ex.InvalidBindings, match="not ready for H3"): select(store, p)


def test_api_saved_authority_restart_and_workspace_export_boundary(tmp_path, monkeypatch, workspace_factory):
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("MINICONSTRUCT_EXECUTION_ROOT", str(tmp_path / "execution"))
    p = ProjectStore().create_project(workspace_factory())
    profile = ex.ExecutionStore().save("profiles", capacity_profile(0))
    path = f"/api/execution/projects/{p.project_id}/generations/{p.active_generation_id}/selection"
    with TestClient(app) as client:
        assert client.get(path).json()["selected"] is None
        assert client.put(path, json={"prompt": VALID_BASE}).status_code == 422
        assert client.put(path, json={"prompt": VALID_BASE, "sourceFingerprint": fingerprint(p.workspace)}).status_code == 200
    # Fresh client, no generation outputs, no History and no connection.
    with TestClient(app) as client:
        assert client.get(path).json()["selected"]["prompt"] == VALID_BASE
        body = {"projectId": p.project_id, "generationId": p.active_generation_id, "profileId": str(profile.id)}
        result = client.post("/api/execution/prepare", json=body)
        assert result.status_code == 200, result.text
        assert result.json()["sentToComfyUI"] is False
        assert client.post("/api/execution/prepare", json={**body, "workspace": {}}).status_code == 422
        assert client.post("/api/execution/prepare", json={**body, "prompt": VALID_BASE}).status_code == 422
        assert client.get(path.replace(p.active_generation_id, str(uuid4()))).status_code == 404
        assert client.get(path.replace(p.project_id, str(uuid4()))).status_code == 404
        assert client.delete(path).json()["selected"] is None
        assert client.post("/api/execution/prepare", json=body).status_code == 422


@pytest.mark.parametrize("pictures,kinds,works", [(2, [], True), (1, ["video"], False), (3, ["video", "audio"], False)])
def test_same_full_capacity_profile_legacy_av_missing_payload(store, tmp_path, workspace_factory, image_asset_factory, pictures, kinds, works):
    profile = ex.ExecutionStore(tmp_path / "execution").save("profiles", capacity_profile(av=True))
    w = ref_workspace(workspace_factory, image_asset_factory, pictures)
    for kind in kinds:
        w.assets.append(ReferenceAsset.model_validate({"id": kind, "kind": kind, "filename": f"test.{kind}",
            "mimeType": f"{kind}/synthetic", "role": f"general_{kind}", "durationSeconds": 4}))
    p = store.create_project(w); select(store, p, valid_ref_prompt())
    if works:
        result = prepared(store, p, profile)
        assert result.inactive_slots == ["Picture 3", "Audio 1", "Video 1"]
        assert len(result.asset_requirements) == 2
    else:
        with pytest.raises(ex.InvalidBindings, match="file payload unavailable"): prepared(store, p, profile)
