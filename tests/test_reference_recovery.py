"""F5 recovery uses real filesystem authority and synthetic media, offline."""
import json

import pytest

from miniconstruct import execution as ex
from miniconstruct.h3.builder import assemble_prompt
from miniconstruct.planning import _asset_metadata, _image_parts
from miniconstruct.project_store import ProjectStore, AssetPayloadError, StaleProjectSnapshotError
from test_media_persistence import asset, upload, media_bytes
from test_reference_authority import picture
from test_workflow_preparation import capacity_profile, select, prepared
from test_validator import valid_ref_prompt


def saved(store, project):
    directory = store.root / f"project-{project.project_id}"
    data = json.loads((directory / "project.json").read_text())
    return directory, data, data["sequence"]["generations"][0]["assetFiles"]


def sync(store, project, workspace):
    assets = [a.model_dump(mode="json", by_alias=True) for a in workspace.assets]
    for a in assets:
        if a["kind"] == "image" and not a.get("image"):
            a["imageSha256"] = a["payloadSha256"]
    return store.sync_reference_assets(project.project_id, project.active_generation_id,
        assets, workspace.subjects, project.project_fingerprint)


@pytest.mark.parametrize("kind", ["image", "audio", "video"])
@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_load_remove_and_delete_preserve_semantics(tmp_path, workspace_factory, kind, damage, monkeypatch):
    store = ProjectStore(tmp_path)
    reference = picture("red")[0] if kind == "image" else asset(store, kind)
    p = store.create_project(workspace_factory(mode="Ref2VA", assets=[reference]))
    directory, raw, entries = saved(store, p)
    path = directory / entries[0]["path"]
    if damage == "missing": path.unlink()
    else: path.write_bytes(picture("blue")[1] if kind == "image" else b"bad")
    before = (directory / "project.json").read_bytes()
    loaded = store.load_project(p.project_id)
    broken = loaded.workspace.assets[0]
    assert broken.payload_availability == damage
    assert broken.attached is False
    assert broken.image is None and broken.media_payload is None
    assert broken.id == reference.id and broken.role == reference.role
    assert broken.notes == reference.notes and broken.options == reference.options
    assert loaded.workspace.subjects == p.workspace.subjects
    assert loaded.project_fingerprint == p.project_fingerprint
    assert (directory / "project.json").read_bytes() == before
    digest = store._reference_digest(store._read_manifest(directory).sequence.generations[0].asset_files[0])
    with pytest.raises(AssetPayloadError):
        store.execution_payload(p.project_id, p.active_generation_id, reference.id, kind, digest)
    if kind == "image":
        with pytest.raises(ValueError, match="Replace Picture"):
            assemble_prompt(loaded.workspace, True)
        assert _asset_metadata(loaded.workspace)[0]["imageAvailableForVision"] is False
        with pytest.raises(ValueError, match="Story Planner Vision"):
            _image_parts(loaded.workspace, "source")
        assert assemble_prompt(loaded.workspace, False).messages
    # Removal must not even attempt to inspect the eliminated payload.
    original = store._reference_payload
    def no_removed(directory, entry):
        assert entry.asset_id != reference.id
        return original(directory, entry)
    monkeypatch.setattr(store, "_reference_payload", no_removed)
    loaded.workspace.assets = []
    result = sync(store, loaded, loaded.workspace)
    assert result["changed"]
    assert store.load_project(p.project_id).workspace.assets == []
    assert store.load_project(p.project_id).sequence.subjects == p.sequence.subjects
    store.delete_project(p.project_id)
    assert not directory.exists()


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_picture_replace_without_old_bytes(tmp_path, workspace_factory, damage):
    store = ProjectStore(tmp_path)
    old = picture("red")[0]
    p = store.create_project(workspace_factory(mode="Ref2VA", assets=[old]))
    directory, _, entries = saved(store, p)
    path = directory / entries[0]["path"]
    if damage == "missing": path.unlink()
    else: path.write_bytes(b"corruption")
    loaded = store.load_project(p.project_id)
    new = picture("blue", old.id)[0]
    loaded.workspace.assets = [new]
    sync(store, loaded, loaded.workspace)
    restored = store.load_project(p.project_id).workspace.assets[0]
    assert restored.image == new.image and restored.payload_availability is None
    assert restored.subject_identity == old.subject_identity
    assert restored.id == old.id


@pytest.mark.parametrize("kind", ["audio", "video"])
def test_reattach_missing_exact_and_corrupt_replacement(tmp_path, workspace_factory, kind):
    store = ProjectStore(tmp_path)
    p = store.create_project(workspace_factory(mode="Ref2VA", assets=[asset(store, kind)]))
    directory, _, entries = saved(store, p)
    path = directory / entries[0]["path"]
    path.unlink()
    loaded = store.load_project(p.project_id)
    loaded.workspace.assets[0].media_payload = upload(store, kind)
    sync(store, loaded, loaded.workspace)
    assert path.read_bytes() == media_bytes(kind)
    path.write_bytes(b"corrupt")
    loaded = store.load_project(p.project_id)
    before = (directory / "project.json").read_bytes()
    loaded.workspace.assets[0].media_payload = upload(store, kind)
    with pytest.raises(AssetPayloadError): sync(store, loaded, loaded.workspace)
    assert (directory / "project.json").read_bytes() == before
    assert path.read_bytes() == b"corrupt"
    loaded.workspace.assets[0].media_payload = upload(store, kind, media_bytes(kind) + b"replacement")
    sync(store, loaded, loaded.workspace)
    assert store.load_project(p.project_id).workspace.assets[0].media_payload is not None
    assert path.read_bytes() == b"corrupt"  # immutable orphan retention


@pytest.mark.parametrize("copy_mode", ["full", "fresh"])
def test_save_and_save_as_preserve_unavailable_references(tmp_path, workspace_factory, copy_mode):
    store = ProjectStore(tmp_path)
    p = store.create_project(workspace_factory(mode="Ref2VA", assets=[picture("red")[0], asset(store, "video")]))
    directory, _, entries = saved(store, p)
    for e in entries: (directory / e["path"]).unlink()
    loaded = store.load_project(p.project_id)
    loaded.workspace.creative_request = "Unrelated authoring edit"
    loaded = store.save_project(p.project_id, loaded.workspace, expected_project_fingerprint=loaded.project_fingerprint)
    for override in [None, loaded.workspace]:
        fork = store.duplicate_project(p.project_id, "Recoverable fork", copy_mode=copy_mode, active_workspace=override)
        assert [a.id for a in fork.workspace.assets] == [a.id for a in loaded.workspace.assets]
        assert all(a.payload_availability == "missing" for a in fork.workspace.assets)
        _, raw, copied = saved(store, fork)
        assert copied == entries
        assert "payloadAvailability" not in json.dumps(raw)
        assert all(a["attached"] for a in raw["sequence"]["generations"][0]["workspace"]["assets"])
    duplicated = store.duplicate_generation(p.project_id, p.active_generation_id)
    store.delete_generation(p.project_id, duplicated.active_generation_id)
    assert len(store.load_project(p.project_id).workspace.assets) == 2


def test_mixed_remove_preserves_bytes_and_compact_unavailable_identity(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path)
    p = store.create_project(workspace_factory(mode="Ref2VA", assets=[picture("red")[0], asset(store, "video"), asset(store, "audio")]))
    directory, _, entries = saved(store, p)
    paths = {e["kind"]: directory / e["path"] for e in entries}
    mtimes = {kind: path.stat().st_mtime_ns for kind, path in paths.items()}
    paths["video"].unlink()
    loaded = store.load_project(p.project_id)
    loaded.workspace.assets = [a for a in loaded.workspace.assets if a.kind != "video"]
    sync(store, loaded, loaded.workspace)
    loaded = store.load_project(p.project_id)
    assert all(a.payload_availability is None for a in loaded.workspace.assets)
    assert all(paths[k].stat().st_mtime_ns == mtimes[k] for k in ["image", "audio"])
    old_pixels = loaded.workspace.model_copy(deep=True)
    paths["image"].unlink()
    # A stale full data URL cannot republish a removed canonical file.
    sync(store, loaded, old_pixels)
    assert not paths["image"].exists()
    broken = store.load_project(p.project_id)
    result = sync(store, broken, broken.workspace)
    assert result["changed"] is False
    assert result["payloadStates"]["outfit"]["payloadAvailability"] == "missing"
    assert not paths["image"].exists()
    with pytest.raises(StaleProjectSnapshotError):
        store.sync_reference_assets(p.project_id, p.active_generation_id, [], [], "0" * 64)
    assert len(store.load_project(p.project_id).workspace.assets) == 2


@pytest.mark.parametrize("kind", ["image", "audio", "video"])
@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_prepare_unavailable_fails_closed_and_replacement_recovers(tmp_path, workspace_factory, kind, damage):
    store = ProjectStore(tmp_path / "projects")
    refs = [picture("red")[0]]
    if kind != "image": refs.append(asset(store, kind))
    p = store.create_project(workspace_factory(mode="Ref2VA", assets=refs))
    profile = ex.ExecutionStore(tmp_path / "execution").save("profiles", capacity_profile(av=True))
    directory, _, entries = saved(store, p)
    entry = next(e for e in entries if e["kind"] == kind)
    select(store, p, valid_ref_prompt())
    baseline = prepared(store, p, profile)
    path = directory / entry["path"]
    if damage == "missing": path.unlink()
    else: path.write_bytes(b"corrupt")
    from miniconstruct.execution_send import send_generation, ExecutionSendRequest
    import httpx
    config = ex.ExecutionStore(tmp_path / "execution")
    connection = config.save("connections", ex.ConnectionWrite(name="Offline", baseUrl="http://never.test"))
    calls = []
    request = ExecutionSendRequest(projectId=p.project_id, generationId=p.active_generation_id,
        profileId=profile.id, connectionId=connection.id, expectedExecutionFingerprint=baseline.execution_fingerprint)
    with pytest.raises(ex.InvalidBindings):
        send_generation(request, store=store, config=config, transport=httpx.MockTransport(lambda r: calls.append(r)))
    assert calls == []
    loaded = store.load_project(p.project_id)
    select(store, loaded, valid_ref_prompt())
    with pytest.raises(ex.InvalidBindings, match="missing|corrupt|unavailable"):
        prepared(store, loaded, profile)
    broken = next(a for a in loaded.workspace.assets if a.kind == kind)
    if kind == "image": broken.image = picture("blue")[0].image
    else: broken.media_payload = upload(store, kind, media_bytes(kind) + b"new")
    sync(store, loaded, loaded.workspace)
    loaded = store.load_project(p.project_id)
    select(store, loaded, valid_ref_prompt())
    assert prepared(store, loaded, profile).asset_requirements


def test_api_load_and_failed_replacement_are_nonmutating(tmp_path, workspace_factory, monkeypatch):
    from fastapi.testclient import TestClient
    from miniconstruct.app import app
    store = ProjectStore(tmp_path)
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(tmp_path))
    p = store.create_project(workspace_factory(mode="Ref2VA", assets=[picture("red")[0], asset(store, "video")]))
    directory, _, entries = saved(store, p)
    for e in entries: (directory / e["path"]).unlink()
    before = (directory / "project.json").read_bytes()
    with TestClient(app) as client:
        result = client.get(f"/api/projects/{p.project_id}")
        assert result.status_code == 200, result.text
        assert result.json()["workspace"]["assets"][0]["image"] is None
        assert result.json()["workspace"]["assets"][1]["payloadAvailability"] == "missing"
    loaded = store.load_project(p.project_id)
    loaded.workspace.assets[0].image = picture("blue")[0].image
    loaded.workspace.assets[0].image.data_url = "data:image/png;base64,invalid"
    with pytest.raises(AssetPayloadError): sync(store, loaded, loaded.workspace)
    assert (directory / "project.json").read_bytes() == before
    assert store.load_project(p.project_id).workspace.assets[0].payload_availability == "missing"
