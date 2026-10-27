"""F7 counts real full-file passes over synthetic Reference bytes, offline."""
import hashlib
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from miniconstruct.app import app
from miniconstruct import execution as ex
from miniconstruct.execution_send import ExecutionSendRequest, send_generation, transport_preflight
from miniconstruct.project_store import ProjectStore, AssetPayloadError, StaleProjectSnapshotError, _PROJECT_TRANSACTION_LOCK
from miniconstruct.execution_authoring import execution_authoring_fingerprint
from miniconstruct.execution_state import SavedPromptWrite
from test_execution_send import recorder
from test_media_persistence import asset, media_bytes, upload
from test_reference_recovery import sync, saved
from test_reference_authority import picture
from test_workflow_preparation import capacity_profile, select, prepared
from test_validator import valid_ref_prompt


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("F7 must never call a live provider or ComfyUI")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)


@pytest.fixture
def hash_passes(monkeypatch):
    """Count actual digest streams by file, including SHA-256 during copy."""
    passes = []
    expected = hashlib.sha256(media_bytes("video")).hexdigest()
    original_file_digest, original_sha256 = hashlib.file_digest, hashlib.sha256
    original_copy = ProjectStore._copy_media
    active_source = None

    def counted(stream, *args, **kwargs):
        result = original_file_digest(stream, *args, **kwargs)
        path = Path(stream.name)
        if result.hexdigest() == expected or "videos" in path.parts:
            passes.append((path, _PROJECT_TRANSACTION_LOCK._is_owned(), "verify"))
        return result

    class CopyDigest:
        def __init__(self, content, source):
            self.content, self.source = content, source
        def update(self, chunk): self.content.update(chunk)
        def hexdigest(self):
            passes.append((self.source, _PROJECT_TRANSACTION_LOCK._is_owned(), "copy"))
            return self.content.hexdigest()

    def sha256(*args, **kwargs):
        content = original_sha256(*args, **kwargs)
        return CopyDigest(content, active_source) if active_source is not None else content

    def copy(source, destination, **kwargs):
        nonlocal active_source
        previous = active_source
        active_source = source if kwargs["digest"] == expected else None
        try:
            return original_copy(source, destination, **kwargs)
        finally:
            active_source = previous

    monkeypatch.setattr(hashlib, "file_digest", counted)
    monkeypatch.setattr(hashlib, "sha256", sha256)
    monkeypatch.setattr(ProjectStore, "_copy_media", staticmethod(copy))
    return passes


COUNTS = {"load": (1, 0), "read": (1, 0), "sync": (1, 1), "save": (1, 1),
    "full": (2, 2), "fresh": (2, 2), "full_override": (2, 2), "fresh_override": (2, 2),
    "selection": (0, 0), "saved_prompt": (0, 0), "refresh": (0, 0), "prepare": (1, 0),
    "preflight": (0, 0), "send": (2, 0), "payload": (1, 0), "missing": (0, 0), "corrupt": (1, 0)}


@pytest.mark.parametrize("operation", ["load", "read", "sync", "save", "full", "fresh",
    "full_override", "fresh_override", "selection", "saved_prompt", "refresh", "prepare",
    "preflight", "send", "payload", "missing", "corrupt"])
def test_reference_hash_counts(tmp_path, workspace_factory, monkeypatch, hash_passes, operation):
    store = ProjectStore(tmp_path / "projects")
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(store.root))
    config = ex.ExecutionStore(tmp_path / "execution")
    profile = config.save("profiles", capacity_profile(av=True))
    connection = config.save("connections", ex.ConnectionWrite(name="Offline", baseUrl="http://never.test"))
    project = store.create_project(workspace_factory(mode="Ref2VA", assets=[asset(store, "video")]))
    select(store, project, valid_ref_prompt())
    store.save_prompt(project.project_id, project.active_generation_id, SavedPromptWrite(prompt=valid_ref_prompt()))
    baseline = prepared(store, project, profile)
    directory, _, entries = saved(store, project)
    path = directory / entries[0]["path"]
    digest = hashlib.sha256(media_bytes("video")).hexdigest()
    hash_passes.clear()
    if operation == "load": store.load_project(project.project_id)
    elif operation == "read": store.read_generation(project.project_id, project.active_generation_id)
    elif operation == "sync": sync(store, project, project.workspace)
    elif operation == "save":
        project.workspace.creative_request += " An unrelated edit."
        store.save_project(project.project_id, project.workspace, expected_project_fingerprint=project.project_fingerprint)
    elif operation.startswith(("full", "fresh")):
        fork = store.duplicate_project(project.project_id, "Fork", copy_mode=operation.split("_")[0],
            active_workspace=project.workspace if operation.endswith("override") else None)
        # Every source copy is mandatory streaming; no source/destination reread
        # solely to hash the transfer. The returned recovery projection is one pass.
        assert hash_passes[0] == (path, True, "copy")
        assert hash_passes[1][0].is_relative_to(store.root / f"project-{fork.project_id}")
    elif operation in {"selection", "saved_prompt", "refresh"}:
        with TestClient(app) as client:
            prefix = f"/api/execution/projects/{project.project_id}/generations/{project.active_generation_id}"
            if operation in {"selection", "refresh"}:
                response = client.get(prefix + "/selection")
                assert response.status_code == 200, response.text
            if operation == "saved_prompt":
                response = client.get(prefix + "/saved-prompt")
                assert response.status_code == 200, response.text
                assert not response.json()["prompts"][0]["stale"]
            if operation == "refresh":
                store.generation_outputs(project.project_id, project.active_generation_id)
                store.foreground_renders(project.project_id)
    elif operation == "prepare": prepared(store, project, profile)
    elif operation == "preflight": transport_preflight(baseline, profile)
    elif operation == "send":
        calls = []
        body = ExecutionSendRequest(projectId=project.project_id, generationId=project.active_generation_id,
            profileId=profile.id, connectionId=connection.id, expectedExecutionFingerprint=baseline.execution_fingerprint)
        send_generation(body, store=store, config=config, transport=recorder(calls))
        assert len(calls) == 2
    elif operation == "payload":
        store.execution_payload(project.project_id, project.active_generation_id, "video", "video", digest)
    elif operation in {"missing", "corrupt"}:
        if operation == "missing": path.unlink()
        else: path.write_bytes(b"x" * path.stat().st_size)
        assert store.load_project(project.project_id).workspace.assets[0].payload_availability == operation
    assert (len(hash_passes), sum(locked for _, locked, _ in hash_passes)) == COUNTS[operation], hash_passes
    if operation not in {"full", "fresh", "full_override", "fresh_override"}:
        assert all(file == path for file, _, _ in hash_passes)


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
@pytest.mark.parametrize("operation", ["sync", "save", "full", "fresh", "full_override", "fresh_override"])
def test_stale_descriptor_never_repairs_storage(tmp_path, workspace_factory, hash_passes, damage, operation):
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory(mode="Ref2VA", assets=[asset(store, "video")]))
    directory, _, entries = saved(store, project)
    path = directory / entries[0]["path"]
    if damage == "missing": path.unlink()
    else: path.write_bytes(b"x" * path.stat().st_size)
    hash_passes.clear()
    if operation == "sync":
        result = sync(store, project, project.workspace)
        assert not result["changed"]
        assert result["payloadStates"]["video"]["payloadAvailability"] == damage
    elif operation == "save":
        project.workspace.creative_request += " An unrelated edit."
        result = store.save_project(project.project_id, project.workspace)
        assert result.workspace.assets[0].payload_availability == damage
    else:
        result = store.duplicate_project(project.project_id, "Unavailable copy", copy_mode=operation.split("_")[0],
                                         active_workspace=project.workspace if operation.endswith("override") else None)
        assert result.workspace.assets[0].payload_availability == "missing"
        assert saved(store, result)[2] == entries
    assert len(hash_passes) == (1 if damage == "corrupt" else 0)
    assert all(file == path for file, _, _ in hash_passes)
    loaded = store.load_project(project.project_id)
    assert loaded.workspace.assets[0].payload_availability == damage
    assert path.exists() == (damage == "corrupt")
    with pytest.raises(AssetPayloadError):
        store.execution_payload(project.project_id, project.active_generation_id, "video", "video", entries[0]["contentSha256"])


@pytest.mark.parametrize("phase", ["after_prepare", "before_open", "during_upload"])
def test_send_same_size_corruption_cannot_use_prior_verification(tmp_path, workspace_factory, monkeypatch, phase):
    store = ProjectStore(tmp_path / "projects")
    config = ex.ExecutionStore(tmp_path / "execution")
    profile = config.save("profiles", capacity_profile(av=True))
    connection = config.save("connections", ex.ConnectionWrite(name="Offline", baseUrl="http://never.test"))
    project = store.create_project(workspace_factory(mode="Ref2VA", assets=[asset(store, "video")]))
    select(store, project, valid_ref_prompt())
    baseline = prepared(store, project, profile)
    directory, _, entries = saved(store, project)
    path = directory / entries[0]["path"]
    def corrupt(): path.write_bytes(b"x" * entries[0]["byteLength"])
    if phase == "after_prepare": corrupt()
    if phase == "before_open":
        original = store._execution_payload_identity
        def identity(*args, **kwargs):
            result = original(*args, **kwargs)
            corrupt()  # Initial Prepare saw valid bytes; the upload handle must recheck.
            return result
        monkeypatch.setattr(store, "_execution_payload_identity", identity)
    def during_upload(request, calls):
        if phase == "during_upload" and request.url.path.endswith("/upload/image"): corrupt()
    calls = []
    body = ExecutionSendRequest(projectId=project.project_id, generationId=project.active_generation_id,
        profileId=profile.id, connectionId=connection.id, expectedExecutionFingerprint=baseline.execution_fingerprint)
    with pytest.raises((ex.InvalidBindings, AssetPayloadError), match="corrupt|changed"):
        send_generation(body, store=store, config=config, transport=recorder(calls, during_upload))
    assert len(calls) == (1 if phase == "during_upload" else 0)
    assert all(call.url.path.endswith("/upload/image") for call in calls)
    assert store.generation_outputs(project.project_id, project.active_generation_id)["renderSubmissions"] == []


@pytest.mark.parametrize("copy_mode", ["full", "fresh"])
@pytest.mark.parametrize("override", [False, True])
def test_copy_hashes_stream_once_and_survives_source_deletion(tmp_path, workspace_factory, hash_passes, copy_mode, override):
    store = ProjectStore(tmp_path)
    first = asset(store, "video")
    second = first.model_copy(update={"id": "second"})
    project = store.create_project(workspace_factory(mode="Ref2VA", assets=[first, second]))
    project = store.duplicate_generation(project.project_id, project.active_generation_id)
    hash_passes.clear()
    fork = store.duplicate_project(project.project_id, "Portable", copy_mode=copy_mode,
                                  active_workspace=project.workspace if override else None)
    assert len(hash_passes) == 2
    assert [mode for _, _, mode in hash_passes] == ["copy", "verify"]
    store.delete_project(project.project_id)
    directory, _, entries = saved(store, fork)
    destination = directory / entries[0]["path"]
    hash_passes.clear()
    store._verify_media(destination, entries[0]["contentSha256"], entries[0]["byteLength"])
    assert hash_passes == [(destination, False, "verify")]
    hash_passes.clear()
    assert len(store.load_project(fork.project_id).workspace.assets) == 2
    assert len(hash_passes) == 1  # Two slots share one exact canonical file.


@pytest.mark.parametrize("field", ["sha256", "byte_length", "project_id", "mime_type"])
def test_direct_canonical_descriptor_forgery_is_nonmutating(tmp_path, workspace_factory, field):
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory(mode="Ref2VA", assets=[asset(store, "video")]))
    directory, _, entries = saved(store, project)
    before = (directory / "project.json").read_bytes()
    incoming = project.workspace.model_copy(deep=True)
    reference = incoming.assets[0]
    if field == "mime_type": reference.mime_type = "video/forged"
    elif field == "sha256": reference.media_payload.sha256 = "0" * 64
    elif field == "byte_length": reference.media_payload.byte_length += 1
    else: reference.media_payload.project_id = "00000000-0000-0000-0000-000000000000"
    with pytest.raises(AssetPayloadError): sync(store, project, incoming)
    assert (directory / "project.json").read_bytes() == before
    assert (directory / entries[0]["path"]).read_bytes() == media_bytes("video")
    with pytest.raises(StaleProjectSnapshotError):
        store.sync_reference_assets(project.project_id, project.active_generation_id, [], [], "0" * 64)


def test_metadata_fingerprints_and_prompt_staleness_match_healthy_hydration(tmp_path, workspace_factory, hash_passes):
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory(mode="Ref2VA",
        assets=[picture("red")[0], asset(store, "video"), asset(store, "audio")]))
    project = store.save_project(project.project_id, project.workspace, planning={"scenePlan": "A cyclist arrives."})
    p, g = project.project_id, project.active_generation_id
    continuity = store.h3_continuity_context(p, g)
    narrative = store.generation_narrative_subjects(p, project.workspace)
    handoff = store.visual_handoff(p, g, project.workspace, strict=False).identity
    expected = {"one_shot": execution_authoring_fingerprint(project.workspace, narrative, continuity, handoff),
        "story": execution_authoring_fingerprint(project.workspace, narrative, continuity, handoff,
            authoring_path="story", story_context=store.story_h3_context(p, g))}
    hash_passes.clear()
    generation, status = store.execution_snapshot(p, g)
    assert status.current_fingerprints == expected
    assert all(a.image is None and a.media_payload is None for a in generation.workspace.assets)
    assert hash_passes == []
    select(store, project, valid_ref_prompt())
    store.save_prompt(p, g, SavedPromptWrite(prompt=valid_ref_prompt()))
    assert not store.execution_snapshot(p, g)[1].stale
    assert not store.saved_prompt_status(p, g).prompts[0].stale
    project.workspace.creative_request += " New authored text."
    store.save_project(p, project.workspace)
    hash_passes.clear()
    assert store.execution_snapshot(p, g)[1].stale
    assert store.saved_prompt_status(p, g).prompts[0].stale
    assert hash_passes == []


def test_incremental_copy_checks_exact_length_and_digest_before_publication(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.write_bytes(media_bytes("video"))
    digest = hashlib.sha256(media_bytes("video")).hexdigest()
    with pytest.raises(AssetPayloadError, match="length"):
        ProjectStore._copy_media(source, destination, digest=digest, size=source.stat().st_size + 1)
    source.write_bytes(b"x" * source.stat().st_size)
    with pytest.raises(AssetPayloadError, match="content"):
        ProjectStore._copy_media(source, destination, digest=digest, size=source.stat().st_size)
    assert not destination.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_prepare_revalidates_manifest_after_unlocked_hash(tmp_path, workspace_factory, monkeypatch):
    import json
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory(mode="Ref2VA", assets=[asset(store, "video")]))
    select(store, project, valid_ref_prompt())
    original = store._hydrate_workspace
    def hydrate(*args, **kwargs):
        assert not _PROJECT_TRANSACTION_LOCK._is_owned()
        result = original(*args, **kwargs)
        manifest = store.root / f"project-{project.project_id}" / "project.json"
        data = json.loads(manifest.read_text())
        data["sequence"]["generations"][0]["workspace"]["creativeRequest"] = "Changed concurrently."
        manifest.write_text(json.dumps(data))
        return result
    monkeypatch.setattr(store, "_hydrate_workspace", hydrate)
    profile = ex.ExecutionStore(tmp_path / "execution").save("profiles", capacity_profile(av=True))
    with pytest.raises(StaleProjectSnapshotError, match="changed during payload verification"):
        prepared(store, project, profile)


@pytest.mark.parametrize("copy_mode", ["full", "fresh"])
def test_save_as_unreadable_source_keeps_recovery_membership(tmp_path, workspace_factory, monkeypatch, copy_mode):
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory(mode="Ref2VA", assets=[asset(store, "video")]))
    directory, _, entries = saved(store, project)
    source = directory / entries[0]["path"]
    original = Path.open
    def unreadable(path, *args, **kwargs):
        if path == source: raise PermissionError("Injected unreadable source")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", unreadable)
    fork = store.duplicate_project(project.project_id, "Recovery copy", copy_mode=copy_mode)
    assert fork.workspace.assets[0].payload_availability == "missing"
    assert saved(store, fork)[2] == entries


@pytest.mark.parametrize("copy_mode", ["full", "fresh"])
def test_save_as_destination_failure_rolls_back(tmp_path, workspace_factory, monkeypatch, copy_mode):
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory(mode="Ref2VA", assets=[asset(store, "video")]))
    original = Path.open
    def failed_destination(path, *args, **kwargs):
        if path.suffix == ".tmp": raise PermissionError("Injected destination failure")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", failed_destination)
    with pytest.raises(PermissionError, match="destination failure"):
        store.duplicate_project(project.project_id, "Failed copy", copy_mode=copy_mode)
    assert [p.name for p in store.root.glob("project-*")] == [f"project-{project.project_id}"]
    assert not list(store.root.glob("*.creating"))


def test_transport_batch_verifies_shared_file_once_and_validates_every_slot(tmp_path, workspace_factory, hash_passes):
    from miniconstruct.workflow_preparation import AssetRequirement
    store = ProjectStore(tmp_path)
    first = asset(store, "video")
    project = store.create_project(workspace_factory(mode="Ref2VA",
        assets=[first, first.model_copy(update={"id": "second"})]))
    directory, _, entries = saved(store, project)
    path = directory / entries[0]["path"]
    digest = entries[0]["contentSha256"]
    requirements = [AssetRequirement(id=ident, assetId=ident, assetKind="video", contentSha256=digest,
        semanticLabel=ident, filename="video.bin", nodeId=ident, inputName="file") for ident in ["video", "second"]]
    hash_passes.clear()
    with store.open_execution_payloads(project.project_id, project.active_generation_id, requirements) as payloads:
        assert payloads[0][1] is payloads[1][1]
        assert not payloads[0][1].closed
        assert hash_passes == [(path, False, "verify")]
    assert payloads[0][1].closed
    requirements[1].asset_id = "forged"
    with pytest.raises(AssetPayloadError):
        with store.open_execution_payloads(project.project_id, project.active_generation_id, requirements):
            pytest.fail("Every slot must resolve from the current server manifest")
