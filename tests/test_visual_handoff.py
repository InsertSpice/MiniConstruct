"""Phase D: real synthetic media, canonical identity, no external transport."""
import hashlib
from io import BytesIO
import json
from pathlib import Path

import av
import httpx
from PIL import Image
import pytest
from pydantic import ValidationError

from miniconstruct.models.visual_handoff import ResolvedVisualHandoff, VisualHandoffPolicy
from miniconstruct.models.workspace import H3Mode, validate_authoring_workspace
from miniconstruct.models.takes import TakeImport
from miniconstruct.project_store import ProjectStore, ProjectStoreError
from miniconstruct.visual_handoff import AUTO, compatibility, resolved_strategy
from miniconstruct.handoff_media import extract_tail
from miniconstruct.generation import resolve_narrative_request
from miniconstruct.models.api import GenerationRequest
from miniconstruct.h3.builder import assemble_prompt, build_h3_input_map, numbered_assets
from miniconstruct.execution_routing import effective_references
from miniconstruct.execution_state import SelectionWrite
from miniconstruct.workflow_preparation import prepare_workflow
from miniconstruct import execution as ex
from miniconstruct.execution_send import send_generation, ExecutionSendRequest
from test_workflow_preparation import capacity_profile, VIDEO_PROMPT
from test_execution_send import recorder
from test_validator import valid_ref_prompt, VALID_BASE


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Phase D must not call live HTTP")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)


def video(path, frames=40, audio=True, rate=10):
    with av.open(str(path), "w") as container:
        stream = container.add_stream("mpeg4", rate=rate)
        stream.width = 96; stream.height = 64; stream.pix_fmt = "yuv420p"
        sound = container.add_stream("aac", rate=48000) if audio else None
        for index in range(frames):
            # Color records time, so the tail's first decoded image proves the interval.
            frame = av.VideoFrame.from_image(Image.new("RGB", (96, 64), (index * 5, 240 if index == frames - 1 else 0, 0)))
            for packet in stream.encode(frame): container.mux(packet)
        for packet in stream.encode(): container.mux(packet)
        if sound:
            for i in range(max(1, frames * 4800 // 1024)):
                frame = av.AudioFrame(format="fltp", layout="stereo", samples=1024)
                for plane in frame.planes: plane.update(bytes(plane.buffer_size))
                frame.sample_rate = 48000; frame.pts = i * 1024
                for packet in sound.encode(frame): container.mux(packet)
            for packet in sound.encode(): container.mux(packet)
    return path.read_bytes()


async def add_take(store, project, data, select=True):
    async def chunks(): yield data
    result = await store.import_take(project.project_id, project.active_generation_id, chunks(), "synthetic.mp4",
                                     TakeImport(sourceKind="external_unknown"))
    take = result["takes"][-1]["id"]
    if select: store.select_take(project.project_id, project.active_generation_id, take)
    return take


@pytest.fixture
async def env(tmp_path, workspace_factory, image_asset_factory):
    store = ProjectStore(tmp_path / "projects")
    first = store.create_project(workspace_factory())
    data = video(tmp_path / "source.mp4")
    take = await add_take(store, first, data)
    workspace = workspace_factory(mode="Ref2VA", assets=[image_asset_factory(role="environment")])
    second = store.create_generation(first.project_id, workspace=workspace)
    second = store.save_project(second.project_id, second.workspace,
        planning={"relationshipToPrevious": "new_shot_preserve_continuity"})
    return store, first, second, take, data


def resolve(env, materialize=True, **kwargs):
    store, _, p, *_ = env
    return store.visual_handoff(p.project_id, p.active_generation_id, materialize=materialize, **kwargs)


def change(env, relationship=None, strategy="auto", **values):
    store, _, p, *_ = env
    planning = {"visualHandoff": {"strategy": strategy, **values}}
    if relationship: planning["relationshipToPrevious"] = relationship
    return store.save_project(p.project_id, p.workspace, planning=planning)


def fingerprint(env, generation=None):
    store, _, p, *_ = env
    return store.execution_snapshot(p.project_id, generation or p.active_generation_id)[1].current_fingerprint


@pytest.mark.parametrize("relation,strategy", AUTO.items())
def test_auto_and_override(relation, strategy):
    assert resolved_strategy(VisualHandoffPolicy(), relation) == strategy
    assert resolved_strategy(VisualHandoffPolicy(strategy="endpoint_frame"), relation) == "endpoint_frame"


def test_legacy_tail_name_and_overlap_metadata_migrate_without_preserving_old_claims():
    policy = VisualHandoffPolicy.model_validate({"strategy": "short_tail_prefix", "tailSeconds": 1.5})
    assert policy.strategy == "tail_reference_continuation"
    resolved = ResolvedVisualHandoff.model_validate({
        "strategy": "short_tail_prefix", "sourceGenerationId": "source", "takeId": "take",
        "takeSha256": "a" * 64, "artifactIdentity": "source:take", "mediaKind": "video",
        "tailSeconds": 1.5, "overlapSeconds": 0.5,
    })
    assert resolved.strategy == "tail_reference_continuation"
    assert "overlapSeconds" not in resolved.model_dump(by_alias=True)


@pytest.mark.parametrize("seconds", [.49, 2.01, 100, float("inf")])
def test_bounded_policy_and_worker(tmp_path, seconds):
    with pytest.raises(ValidationError): VisualHandoffPolicy(tailSeconds=seconds)
    with pytest.raises(ValueError): extract_tail(tmp_path / "absent.mp4", tmp_path / "out.mp4", seconds)


@pytest.mark.parametrize("frames,seconds", [(40, 1.5), (40, 2), (7, 1.5), (1, 1.5)])
def test_real_tail_interval_no_audio_and_source_untouched(tmp_path, frames, seconds):
    source = tmp_path / "source.mp4"; before = video(source, frames)
    dest = tmp_path / "tail.mp4"
    meta = extract_tail(source, dest, seconds)
    assert source.read_bytes() == before
    with av.open(str(source)) as container:
        assert container.streams.audio
    assert meta["effectiveTailSeconds"] == pytest.approx(min(seconds, frames / 10), abs=.001)
    assert meta["startSeconds"] == pytest.approx(max(0, frames / 10 - seconds), abs=.1)
    with av.open(str(dest)) as container:
        assert len(container.streams.video) == 1 and not container.streams.audio
        images = list(container.decode(video=0))
        assert len(images) == min(frames, round(seconds * 10))
        red = images[0].to_image().getpixel((40, 30))[0]
        assert abs(red - round(meta["startSeconds"] * 10) * 5) < 10
        assert images[-1].to_image().getpixel((40, 30))[1] > 200


def test_low_frame_rate_tail_keeps_final_frame_display_duration(tmp_path):
    source = tmp_path / "slow.mp4"
    video(source, frames=3, audio=False, rate=1)
    target = tmp_path / "tail.mp4"
    metadata = extract_tail(source, target, 1.5)
    assert metadata["sourceDurationSeconds"] == 3
    assert metadata["startSeconds"] == 1.5 and metadata["effectiveTailSeconds"] == 1.5
    with av.open(str(target)) as container:
        assert float(container.duration / av.time_base) == pytest.approx(1.5, abs=.02)


async def test_endpoint_cache_reuse_nonmutation_and_recovery(env, monkeypatch):
    store, first, second, take, data = env
    directory = store.root / f"project-{first.project_id}"
    before = (directory / "project.json").read_bytes()
    workspace = second.workspace.model_dump_json()
    a = resolve(env)
    assert a.policy.strategy == "endpoint_then_immediate_cut"
    assert Image.open(a.path).getpixel((40, 30))[1] > 200
    assert second.workspace.model_dump_json() == workspace
    assert (directory / "project.json").read_bytes() == before
    assert store.generation_outputs(first.project_id, first.active_generation_id)["takeObservations"] == []
    assert store.take_media(first.project_id, first.active_generation_id, take)[0].read_bytes() == data
    stamp = a.path.stat().st_mtime_ns; old_fp = fingerprint(env)
    b = resolve(env)
    assert b.path == a.path and b.path.stat().st_mtime_ns == stamp
    cached_bytes = a.path.read_bytes()
    a.path.unlink()
    assert not a.path.exists()
    c = resolve(env)
    assert c.path.read_bytes() == cached_bytes and fingerprint(env) == old_fp
    # Existing Phase C endpoint cache is independently reusable without analysis.
    d, _, source_take, source, _ = store.observation_snapshot(first.project_id, first.active_generation_id)
    evidence, images = store.prepare_observation_evidence(d, source_take, source)
    store._publish_evidence(d, source_take, evidence, images)
    c.path.unlink()
    monkeypatch.setattr("miniconstruct.observation_store.extract_evidence", lambda *_: pytest.fail("Must reuse valid Phase C endpoint"))
    assert resolve(env).path.read_bytes() == images["endpoint"]


async def test_tail_cache_distinct_and_missing_source(env):
    store, first, second, take, _ = env
    change(env, "continue_previous_shot")
    a = resolve(env); assert a.policy.tail_seconds == pytest.approx(1.5)
    change(env, tailSeconds=1.0)
    b = resolve(env); assert a.path != b.path and b.policy.tail_seconds == pytest.approx(1)
    source, _ = store.take_media(first.project_id, first.active_generation_id, take)
    source.unlink()
    assert resolve(env).asset is None
    change(env, strategy="tail_reference_continuation")
    with pytest.raises(ProjectStoreError, match="reattach source Take"): resolve(env)


@pytest.mark.parametrize("mode", list(H3Mode))
@pytest.mark.parametrize("strategy", ["endpoint_frame", "endpoint_then_immediate_cut", "tail_reference_continuation"])
async def test_real_mode_capabilities_and_explicit_error(env, mode, strategy):
    store, _, p, *_ = env
    workspace = validate_authoring_workspace({**p.workspace.model_dump(by_alias=True), "mode": mode})
    change(env, strategy=strategy)
    error = compatibility(workspace, strategy)
    from miniconstruct.visual_handoff import handoff_asset
    metadata = store.visual_handoff(p.project_id, p.active_generation_id, workspace, strict=False)
    candidate = handoff_asset(metadata.policy)
    compiler_accepts = any(a.id == candidate.id for a, _ in numbered_assets(
        workspace.model_copy(update={"assets": [*workspace.assets, candidate]})))
    assert (error is None) == compiler_accepts
    if error:
        with pytest.raises(ProjectStoreError): store.visual_handoff(p.project_id, p.active_generation_id, workspace, materialize=True)
    else:
        handoff = store.visual_handoff(p.project_id, p.active_generation_id, workspace, materialize=True)
        assert handoff.asset.id in {a.id for a, _ in numbered_assets(handoff.workspace(workspace))}
    relation = "continue_previous_shot" if strategy == "tail_reference_continuation" else "match_previous_ending_frame"
    change(env, relation)
    auto = store.visual_handoff(p.project_id, p.active_generation_id, workspace, materialize=True)
    assert (auto.asset is None) == bool(error)
    if error: assert auto.warnings


async def test_existing_frame_anchor_is_never_replaced(env, image_asset_factory):
    store, _, p, *_ = env
    workspace = p.workspace.model_copy(update={"mode": H3Mode.I2VA, "assets": [image_asset_factory(role="first_frame_anchor")]})
    result = store.visual_handoff(p.project_id, p.active_generation_id, workspace, materialize=True)
    assert result.asset is None and "already owns" in result.warnings[0]


@pytest.mark.parametrize("relation,terms", [
    ("continue_previous_shot", ["actual final segment of the predecessor", "from the END", "continuation reference", "Do not restart"]),
    ("new_shot_preserve_continuity", ["exact predecessor endpoint", "100 ms", "Do not linger", "through the cut"]),
    ("match_previous_ending_frame", ["strong opening/initial-frame", "no forced immediate camera cut"]),
])
async def test_transient_numbering_model_policy_and_fingerprint(env, relation, terms):
    store, _, p, *_ = env
    change(env, relation)
    raw = p.workspace.model_dump_json()
    request = GenerationRequest(workspace=p.workspace, filesystemProjectId=p.project_id, generationId=p.active_generation_id,
        llm={"baseUrl": "http://never.test/v1", "model": "offline"})
    request = resolve_narrative_request(request, store)
    assembled = assemble_prompt(request.prompt_workspace, True, request.narrative_subjects, request.continuity_context)
    for term in [*terms, "follow its rendered pose", "Identity Pictures", "Environment references"]:
        assert term in assembled.inspector_text
    assert request.workspace.model_dump_json() == raw
    assert request.source_fingerprint == fingerprint(env)
    entries = build_h3_input_map(request.prompt_workspace)
    assert len({e["id"] for e in entries}) == 2
    assert entries[0]["id"] == "img-1"
    assert entries[-1]["h3Label"] == ("Video 1" if relation == "continue_previous_shot" else "Picture 2")
    assert "Continuity handoff" in entries[-1]["metadata"][0]
    assert request.reference_map == effective_references(request.effective_workspace)


async def test_authority_switch_metadata_independent_reorder_and_delete(env, tmp_path):
    store, first, second, take, _ = env
    source_fp = fingerprint(env, first.active_generation_id)
    before = fingerprint(env)
    other = await add_take(store, first, video(tmp_path / "other.mp4", 30), select=False)
    assert fingerprint(env) == before
    store.select_take(first.project_id, first.active_generation_id, other)
    assert fingerprint(env) != before and resolve(env).policy.take_id == other
    assert fingerprint(env, first.active_generation_id) == source_fp
    for relation, a, b in [("continue_previous_shot", {"tailSeconds": 1.5}, {"tailSeconds": 1}),
                           ("new_shot_preserve_continuity", {"immediateCutMilliseconds": 100}, {"immediateCutMilliseconds": 250})]:
        change(env, relation, **a); previous = fingerprint(env)
        change(env, **b); assert fingerprint(env) != previous
    previous = fingerprint(env); change(env, "match_previous_ending_frame"); assert fingerprint(env) != previous
    change(env, "independent"); independent = fingerprint(env)
    store.select_take(first.project_id, first.active_generation_id, take)
    assert fingerprint(env) == independent and resolve(env).identity is None and resolve(env).warnings == []
    # A new immediate predecessor with no selection may not fall back to distant media.
    third = store.create_generation(first.project_id, workspace=second.workspace)
    store.save_project(third.project_id, third.workspace, planning={"relationshipToPrevious": "match_previous_ending_frame"})
    assert store.visual_handoff(third.project_id, third.active_generation_id).status == "no_selected_take"


async def test_schema_v14_read_defaults_then_mutate_and_duplicate(env):
    store, _, p, *_ = env
    path = store.root / f"project-{p.project_id}" / "project.json"
    raw = json.loads(path.read_text()); raw["schemaVersion"] = 14
    for g in raw["sequence"]["generations"]: g["planning"].pop("visualHandoff")
    path.write_text(json.dumps(raw)); before = path.read_bytes()
    loaded = store.load_project(p.project_id)
    assert loaded.planning.visual_handoff == VisualHandoffPolicy()
    assert path.read_bytes() == before
    change(env, strategy="endpoint_frame")
    assert json.loads(path.read_text())["schemaVersion"] == 17
    duplicate = store.duplicate_generation(p.project_id, p.active_generation_id)
    assert duplicate.planning.visual_handoff.strategy == "endpoint_frame"
    assert duplicate.outputs["takes"] == []


@pytest.mark.parametrize("relation", ["new_shot_preserve_continuity", "continue_previous_shot"])
@pytest.mark.parametrize("local", [False, True])
async def test_comfy_mocked_send_uploads_transient_and_stales_on_switch(env, tmp_path, relation, local):
    store, first, p, take, _ = env
    change(env, relation)
    handoff = resolve(env); workspace = handoff.workspace(p.workspace)
    prompt = VIDEO_PROMPT.replace("[reference generation]", "[video continuation]") if relation == "continue_previous_shot" else valid_ref_prompt().replace("[reference generation]", "[keyframe completion]")
    selected = store.select_execution_prompt(p.project_id, p.active_generation_id,
        SelectionWrite(prompt=prompt, sourceFingerprint=fingerprint(env), referenceMap=effective_references(workspace)))
    assert not selected.stale
    config = ex.ExecutionStore(tmp_path / "execution")
    profile = config.save("profiles", capacity_profile(av=True))
    root = tmp_path / "comfy-input"
    root.mkdir()
    connection = config.save("connections", ex.ConnectionWrite(name="Mock", baseUrl="http://never.test",
        localInputDirectory=str(root) if local else None))
    prepared = prepare_workflow(store, p.project_id, p.active_generation_id, profile)
    assert handoff.asset.id in {r.asset_id for r in prepared.asset_requirements}
    assert len(prepared.reference_map) == 2
    before = store.read_generation(p.project_id, p.active_generation_id).workspace.model_dump_json()
    calls = []
    from test_staged_media import Remote
    remote = Remote(root) if local else None
    transport = httpx.MockTransport(remote.handle) if local else recorder(calls)
    result = send_generation(ExecutionSendRequest(projectId=p.project_id, generationId=p.active_generation_id,
        profileId=profile.id, connectionId=connection.id), store=store, config=config, transport=transport)
    assert result.submitted and len(result.uploaded_inputs) == 2
    assert remote.counts() == (2, 0, 1) if local else len(calls) == 3
    if local:
        second = send_generation(ExecutionSendRequest(projectId=p.project_id, generationId=p.active_generation_id,
            profileId=profile.id, connectionId=connection.id), store=store, config=config, transport=transport)
        assert remote.counts() == (2, 0, 2)
        assert second.uploaded_inputs == result.uploaded_inputs
        assert second.render_submission_id != result.render_submission_id
    assert store.read_generation(p.project_id, p.active_generation_id).workspace.model_dump_json() == before
    assert store.execution_snapshot(p.project_id, p.active_generation_id)[1].selected.prompt == prompt
    await add_take(store, first, video(tmp_path / "new.mp4", 20))
    assert store.execution_snapshot(p.project_id, p.active_generation_id)[1].stale
    assert prepare_workflow(store, p.project_id, p.active_generation_id, profile).graph["h3"]["inputs"]["prompt"] == prompt


async def test_actual_endpoint_strategy_change_stales_selected_prompt(env, tmp_path):
    store, _first, second, _take, _data = env
    handoff = resolve(env)
    assert handoff.asset is not None and handoff.asset.kind.value == "image"
    workspace = handoff.workspace(second.workspace)
    prompt = valid_ref_prompt().replace("[reference generation]", "[keyframe completion]")
    store.select_execution_prompt(second.project_id, second.active_generation_id,
        SelectionWrite(prompt=prompt, sourceFingerprint=fingerprint(env),
                       referenceMap=effective_references(workspace)))
    profile = ex.ExecutionStore(tmp_path / "execution").save("profiles", capacity_profile(av=True))
    assert prepare_workflow(store, second.project_id, second.active_generation_id, profile).ready
    change(env, strategy="none")
    assert store.execution_snapshot(second.project_id, second.active_generation_id)[1].stale
    assert prepare_workflow(store, second.project_id, second.active_generation_id, profile).graph["h3"]["inputs"]["prompt"] == prompt


async def test_save_as_delete_and_reorder_cache_lifecycle(env):
    store, first, second, take, _ = env
    change(env, strategy="endpoint_frame")
    original = resolve(env); before = fingerprint(env)
    fork = store.duplicate_project(second.project_id, "Independent copy")
    assert fork.planning.visual_handoff.strategy == "endpoint_frame"
    destination = store.root / f"project-{fork.project_id}"
    assert not (destination / "outputs/handoffs").exists()
    copied = store.visual_handoff(fork.project_id, fork.active_generation_id, materialize=True)
    assert copied.path.read_bytes() == original.path.read_bytes()
    assert copied.policy.take_id == take
    # New immediate predecessor is empty; neither the older Take nor a future Take can substitute.
    blank = store.create_generation(first.project_id, insert_index=1)
    assert fingerprint(env) != before
    assert resolve(env, strict=False).status == "no_selected_take"
    store.delete_generation(first.project_id, blank.active_generation_id)
    assert fingerprint(env) == before
    store.reorder_generations(first.project_id, [second.active_generation_id, first.active_generation_id])
    assert resolve(env, strict=False).status == "no_selected_take"
    store.reorder_generations(first.project_id, [first.active_generation_id, second.active_generation_id])
    assert resolve(env).policy.take_id == take
    store.delete_generation(first.project_id, first.active_generation_id)
    assert not original.path.exists()
    assert copied.path.exists()  # Save As is independently usable.
    assert resolve(env, strict=False).status == "no_selected_take"


async def test_delete_take_prunes_only_its_cache_and_raw_missing_never_reuses_cache(env):
    store, first, second, take, _ = env
    cache = resolve(env).path
    store.delete_take(first.project_id, first.active_generation_id, take)
    assert not cache.exists()
    assert resolve(env).asset is None
    change(env, strategy="endpoint_frame")
    with pytest.raises(ProjectStoreError, match="Endpoint handoff needs the previous rendered endpoint"):
        resolve(env)


async def test_no_phantom_upload_on_auto_fallback_and_explicit_blocks(env, tmp_path):
    store, first, p, take, _ = env
    workspace = p.workspace.model_copy(update={"mode": H3Mode.T2VA})
    store.save_project(p.project_id, workspace)
    status = store.execution_snapshot(p.project_id, p.active_generation_id)[1]
    store.select_execution_prompt(p.project_id, p.active_generation_id,
        SelectionWrite(prompt=VALID_BASE, sourceFingerprint=status.current_fingerprint, referenceMap=[]))
    profile = ex.ExecutionStore(tmp_path / "execution").save("profiles", capacity_profile())
    prepared = prepare_workflow(store, p.project_id, p.active_generation_id, profile)
    assert prepared.asset_requirements == [] and prepared.reference_map == []
    assert any("incompatible" in warning for warning in prepared.warnings)
    # Missing Auto source does not invent a reference in Ref2VA either.
    store.select_take(first.project_id, first.active_generation_id, None)
    store.save_project(p.project_id, p.workspace)
    store.select_execution_prompt(p.project_id, p.active_generation_id,
        SelectionWrite(prompt=valid_ref_prompt(), sourceFingerprint=fingerprint(env)))
    prepared = prepare_workflow(store, p.project_id, p.active_generation_id, profile)
    assert [r.asset_id for r in prepared.asset_requirements] == ["img-1"]
    change(env, strategy="endpoint_frame")
    with pytest.raises(ProjectStoreError, match="Endpoint handoff needs the previous rendered endpoint"):
        prepare_workflow(store, p.project_id, p.active_generation_id, profile)


async def test_input_map_routing_preview_and_reserved_collision(env, monkeypatch):
    from fastapi.testclient import TestClient
    from miniconstruct.app import app
    store, _, p, *_ = env
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(store.root))
    with TestClient(app) as client:
        response = client.post("/api/reference-manifest", json={"mode": "Ref2VA", "assets": [],
            "filesystemProjectId": p.project_id, "generationId": p.active_generation_id})
        assert response.status_code == 200, response.text
        data = response.json(); assert data["entries"][0]["h3Label"] == "Picture 1"
        assert data["visualHandoff"]["status"] == "available"
        assert "outputs/" not in response.text and "base64" not in response.text
        # Map resolution is metadata only, so page load has not decoded the Take.
        assert not (store.root / f"project-{p.project_id}" / "outputs/handoffs").exists()
        preview = client.get(f"/api/projects/{p.project_id}/generations/{p.active_generation_id}/visual-handoff/preview")
        assert preview.status_code == 200 and preview.headers["content-type"] == "image/jpeg"
        assert Image.open(BytesIO(preview.content)).getpixel((40, 30))[1] > 200
        invalid = client.post("/api/reference-manifest", json={"mode": "Ref2VA", "path": "arbitrary"})
        assert invalid.status_code == 422
    handoff = resolve(env)
    workspace = p.workspace.model_copy(update={"assets": [p.workspace.assets[0].model_copy(update={"id": handoff.asset.id})]})
    with pytest.raises(ProjectStoreError, match="conflicts"):
        handoff.workspace(workspace)


async def test_unanalyzed_endpoint_fills_free_i2va_anchor_before_request_validation(env):
    store, _, p, *_ = env
    store.sync_reference_assets(p.project_id, p.active_generation_id, [], [],
                                store.load_project(p.project_id).project_fingerprint)
    workspace = {**p.workspace.model_dump(by_alias=True), "mode": "I2VA", "assets": []}
    request = GenerationRequest(workspace=workspace, filesystemProjectId=p.project_id, generationId=p.active_generation_id,
        llm={"baseUrl": "http://never.test", "model": "offline"})
    resolved = resolve_narrative_request(request, store)
    assert [(a.role, label) for a, label in numbered_assets(resolved.prompt_workspace)] == [("first_frame_anchor", "Picture 1")]
    assert resolved.workspace.assets == []


def test_opt_in_suite_fixtures_resolve_without_model_or_renderer(tmp_path):
    from miniconstruct.benchmark.runner import _manual_render_inputs, load_suite, load_case, resolve_h3_fixture
    root = Path(__file__).resolve().parents[1] / "benchmarks"
    suite = load_suite(root, "h3-visual-handoff")
    assert len(suite.cases) == 4
    for case_id in suite.cases:
        case, workspace, _, _ = load_case(root, case_id)
        files = {}
        request = resolve_h3_fixture(GenerationRequest(workspace=workspace,
            llm={"baseUrl": "http://never.test", "model": "offline"}), case.h3_project, files)
        text = assemble_prompt(request.prompt_workspace, True, request.narrative_subjects, request.continuity_context).inspector_text
        assert all(term in text for term in case.h3_project.input_contains)
        assert all(term not in text for term in case.h3_project.input_absent)
        assert case.human_review and files["predecessor.mp4"]
        assert len(files) == (1 if case_id.endswith("independent") else 2)
        bundle_files, manifest = _manual_render_inputs(case, root / "cases" / case_id, None, request, files)
        assert {reference["h3Label"] for reference in manifest["references"]} == {
            label for _asset, label in numbered_assets(request.prompt_workspace)
        }
        assert {reference["path"] for reference in manifest["references"]} == set(bundle_files)
        assert all(not Path(reference["path"]).is_absolute() for reference in manifest["references"])
        assert all(reference["sha256"] == hashlib.sha256(bundle_files[reference["path"]]).hexdigest()
                   for reference in manifest["references"])


def test_benchmark_media_export_is_separate_from_json(tmp_path):
    from miniconstruct.benchmark.runner import write_run
    result = {"runId": "offline-export-check", "profile": {"id": "offline"}, "cases": [{
        "caseId": "synthetic-export", "status": "complete", "humanReview": ["Inspect continuity manually."],
        "rawModelOutput": "[keyframe completion]", "_manualRenderFiles": {"references/picture-1.jpg": b"endpoint"},
        "_manualRenderManifest": {"schemaVersion": 1, "h3Mode": "Ref2VA", "promptFile": "prompt.txt",
                                  "references": [{"h3Label": "Picture 1", "path": "references/picture-1.jpg", "kind": "image", "mimeType": "image/jpeg", "origin": "ordinary_reference", "sha256": hashlib.sha256(b"endpoint").hexdigest(), "byteSize": 8}]},
    }]}
    run = write_run(tmp_path, result)
    case_path = run.parent / "cases" / "synthetic-export.json" if run.is_file() else run / "cases" / "synthetic-export.json"
    saved = json.loads(case_path.read_text())
    assert "_manualRenderFiles" not in saved and saved["manualRenderBundle"]["referenceCount"] == 1
    import zipfile
    with zipfile.ZipFile(case_path.parent / saved["manualRenderBundle"]["path"]) as bundle:
        manifest = json.loads(bundle.read("manual-render-manifest.json"))
        assert bundle.read("prompt.txt") == b"[keyframe completion]"
        assert bundle.read(manifest["references"][0]["path"]) == b"endpoint"
