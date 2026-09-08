"""F3.1: synthetic local bytes; no model or Comfy transport."""
import asyncio
import hashlib
import io
import json
import shutil
import wave

import pytest
from fastapi.testclient import TestClient

from miniconstruct.app import app
from miniconstruct.models.workspace import ReferenceAsset
from miniconstruct.project_store import ProjectStore, AssetPayloadError
from miniconstruct.execution_authoring import execution_authoring_fingerprint as fingerprint
from miniconstruct.planning import _asset_metadata
from test_workflow_preparation import capacity_profile, select, prepared, ref_workspace
from miniconstruct import execution as ex
from test_validator import valid_ref_prompt


def media_bytes(kind):
    if kind == "audio":
        out = io.BytesIO()
        with wave.open(out, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
            w.writeframes(b"\x00\x01" * 80)
        return out.getvalue()
    # ISO BMFF synthetic container boxes; no codec/probing dependency.
    return b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00\x00\x00\x0cmdat\x01\x02\x03\x04"


def upload(store, kind, data=None):
    async def chunks():
        raw = media_bytes(kind) if data is None else data
        for i in range(0, len(raw), 7):
            yield raw[i:i+7]
    return asyncio.run(store.ingest_media(chunks(), kind, f"{kind}/" + ("wav" if kind == "audio" else "mp4")))


def asset(store, kind, data=None, ident=None):
    return ReferenceAsset.model_validate({"id": ident or kind, "kind": kind,
        "filename": "clip.wav" if kind == "audio" else "clip.mp4",
        "mimeType": f"{kind}/" + ("wav" if kind == "audio" else "mp4"),
        "durationSeconds": 0.01, "role": f"general_{kind}", "notes": "Keep", "options": {"custom": True},
        "mediaPayload": upload(store, kind, data).model_dump(by_alias=True)})


def manifest(store, project):
    return json.loads((store.root / f"project-{project.project_id}" / "project.json").read_text())


@pytest.fixture(params=["audio", "video"])
def kind(request):
    return request.param


@pytest.fixture
def store(tmp_path):
    return ProjectStore(tmp_path / "projects")


def test_persist_restart_rename_change_and_fingerprint(store, kind, workspace_factory):
    a = asset(store, kind)
    w = workspace_factory(mode="Ref2VA", assets=[a])
    before = fingerprint(w)
    p = store.create_project(w)
    loaded = ProjectStore(store.root).load_project(p.project_id)
    assert before == fingerprint(loaded.workspace) == fingerprint(p.workspace)
    restored = loaded.workspace.assets[0]
    assert restored.media_payload.sha256 == hashlib.sha256(media_bytes(kind)).hexdigest()
    assert restored.notes == a.notes and restored.options == a.options and restored.duration_seconds == a.duration_seconds
    assert restored.media_payload.project_id == p.project_id and restored.media_payload.upload_id is None
    raw = manifest(store, p)
    assert raw["schemaVersion"] == 5
    entry = raw["sequence"]["generations"][0]["assetFiles"][0]
    assert entry["contentSha256"] == restored.media_payload.sha256
    assert "mediaPayload" not in raw["sequence"]["generations"][0]["workspace"]["assets"][0]
    directory = store.root / f"project-{p.project_id}"
    path = directory / entry["path"]
    assert path.read_bytes() == media_bytes(kind)
    mtime = path.stat().st_mtime_ns
    restored.filename = "renamed.file"
    store.save_project(p.project_id, loaded.workspace)
    assert path.stat().st_mtime_ns == mtime
    assert len(list(path.parent.iterdir())) == 1
    assert restored.media_payload.sha256 == a.media_payload.sha256
    changed = loaded.workspace.model_copy(deep=True)
    changed.assets[0].media_payload = upload(store, kind, media_bytes(kind) + b"changed")
    assert fingerprint(changed) != fingerprint(loaded.workspace)
    store.save_project(p.project_id, changed)
    assert len(list(path.parent.iterdir())) == 2
    assert path.exists()  # Existing Picture orphan policy retained.
    response = loaded.workspace.model_dump_json(by_alias=True)
    assert "base64" not in response and str(store.root) not in response


def test_duplicate_delete_save_as_directory_copy(store, kind, workspace_factory, tmp_path):
    a = asset(store, kind)
    b = a.model_copy(update={"id": "second", "filename": "other.name"})
    p = store.create_project(workspace_factory(mode="Ref2VA", assets=[a, b]))
    assert len(list((store.root / f"project-{p.project_id}" / "assets").rglob("*.bin"))) == 1
    duplicated = store.duplicate_generation(p.project_id, p.active_generation_id)
    store.delete_generation(p.project_id, duplicated.active_generation_id)
    fork = store.duplicate_project(p.project_id, "Fork")
    override = store.duplicate_project(p.project_id, "Override", active_workspace=p.workspace)
    store.delete_project(p.project_id)
    for copied in (fork, override):
        loaded = store.load_project(copied.project_id)
        assert loaded.workspace.assets[0].media_payload.project_id == copied.project_id
        store.save_project(copied.project_id, loaded.workspace)
    backup = tmp_path / "backup"
    backup.mkdir()
    shutil.copytree(store.root / f"project-{fork.project_id}", backup / f"project-{fork.project_id}")
    store.delete_project(fork.project_id)
    assert ProjectStore(backup).load_project(fork.project_id).workspace.assets[0].media_payload.sha256 == a.media_payload.sha256


def test_transaction_failure_preserves_committed_payload(store, kind, workspace_factory, monkeypatch):
    p = store.create_project(workspace_factory(mode="Ref2VA", assets=[asset(store, kind)]))
    old = manifest(store, p)
    changed = p.workspace.model_copy(deep=True)
    changed.assets[0].media_payload = upload(store, kind, media_bytes(kind) + b"new")
    original = store._atomic_write
    def fail(path, content):
        if path.name == "project.json":
            raise OSError("injected commit failure")
        return original(path, content)
    monkeypatch.setattr(store, "_atomic_write", fail)
    with pytest.raises(OSError, match="injected"):
        store.save_project(p.project_id, changed)
    assert manifest(store, p) == old
    assert store.load_project(p.project_id).workspace.assets[0].media_payload == p.workspace.assets[0].media_payload
    assert not list(store.root.rglob("*.tmp"))


def test_f3_requirements_and_legacy_repair(store, kind, workspace_factory, image_asset_factory, tmp_path):
    w = ref_workspace(workspace_factory, image_asset_factory)
    a = asset(store, kind); w.assets.append(a)
    profile = ex.ExecutionStore(tmp_path / "execution").save("profiles", capacity_profile(av=True))
    p = store.create_project(w); select(store, p, valid_ref_prompt())
    result = prepared(store, p, profile)
    requirement = next(r for r in result.asset_requirements if r.asset_kind == kind)
    assert requirement.content_sha256 == a.media_payload.sha256
    assert requirement.semantic_label == f"{kind.title()} 1"
    assert result.transport_ready is False and result.sent_to_comfyui is False
    assert prepared(store, p, profile) == result
    assert ("Video 1" if kind == "audio" else "Audio 1") in result.inactive_slots
    w.assets[-1].media_payload = None
    legacy = store.create_project(w)
    assert store.load_project(legacy.project_id).workspace.assets[-1].media_payload is None
    select(store, legacy, valid_ref_prompt())
    with pytest.raises(ex.InvalidBindings, match="Reattach"):
        prepared(store, legacy, profile)
    w.assets[-1].media_payload = upload(store, kind)
    repaired = store.save_project(legacy.project_id, w); select(store, repaired, valid_ref_prompt())
    assert any(r.asset_kind == kind for r in prepared(store, repaired, profile).asset_requirements)


def test_planner_metadata_excludes_payload_authority(store, kind, workspace_factory):
    w = workspace_factory(mode="Ref2VA", assets=[asset(store, kind)])
    before = _asset_metadata(w)
    w.assets[0].media_payload = upload(store, kind, b"different bytes")
    assert _asset_metadata(w) == before
    assert "mediaPayload" not in json.dumps(before)
    assert before[0]["contentAnalysis"] is None


def test_tampered_descriptor_rejected(store, kind, workspace_factory):
    a = asset(store, kind)
    a.media_payload.sha256 = "0" * 64
    with pytest.raises(AssetPayloadError, match="identity mismatch"):
        store.create_project(workspace_factory(mode="Ref2VA", assets=[a]))
    assert not list(store.root.glob("*.creating"))


def test_missing_and_corrupt_saved_file_rejected(store, kind, workspace_factory):
    p = store.create_project(workspace_factory(mode="Ref2VA", assets=[asset(store, kind)]))
    entry = manifest(store, p)["sequence"]["generations"][0]["assetFiles"][0]
    path = store.root / f"project-{p.project_id}" / entry["path"]
    path.write_bytes(b"x" * entry["byteLength"])
    with pytest.raises(AssetPayloadError, match="verification failed"):
        store.load_project(p.project_id)
    path.unlink()
    with pytest.raises(AssetPayloadError, match="unavailable"):
        store.load_project(p.project_id)


def test_upload_size_and_disconnect_cleanup(store, kind, monkeypatch):
    monkeypatch.setattr(store, "MEDIA_LIMITS", {"audio": 8, "video": 8})
    with pytest.raises(AssetPayloadError, match="exceeds"):
        upload(store, kind, b"a" * 9)
    assert not list((store.root / ".media-uploads").iterdir())
    async def interrupted():
        yield b"a"
        raise RuntimeError("disconnect")
    with pytest.raises(RuntimeError):
        asyncio.run(store.ingest_media(interrupted(), kind, f"{kind}/test"))
    assert not list((store.root / ".media-uploads").iterdir())


def test_binary_api_create_reopen(store, kind, workspace_factory, monkeypatch):
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(store.root))
    with TestClient(app) as client:
        response = client.post(f"/api/media/{kind}", content=media_bytes(kind),
            headers={"content-type": f"{kind}/test"})
        assert response.status_code == 200
        descriptor = response.json()
        assert descriptor["sha256"] == hashlib.sha256(media_bytes(kind)).hexdigest()
        a = {"id": "a", "kind": kind, "filename": "../../display.file", "mimeType": f"{kind}/test",
            "role": f"general_{kind}", "mediaPayload": descriptor}
        w = workspace_factory(mode="Ref2VA", assets=[a])
        created = client.post("/api/projects", json={"workspace": w.model_dump(mode="json", by_alias=True)})
        assert created.status_code == 201, created.text
        loaded = client.get(f"/api/projects/{created.json()['id']}")
        payload = loaded.json()["workspace"]["assets"][0]["mediaPayload"]
        assert payload["sha256"] == descriptor["sha256"] and payload["uploadId"] is None
        assert str(store.root) not in loaded.text and "base64" not in loaded.text
        assert client.post(f"/api/media/{kind}", content=b"", headers={"content-type": f"{kind}/test"}).status_code == 400
        assert client.post(f"/api/media/{kind}", content=b"x", headers={"content-type": "image/png"}).status_code == 400
        assert client.post(f"/api/media/{kind}", content=b"x", headers={"content-length": str(1024**3), "content-type": f"{kind}/test"}).status_code == 413

@pytest.mark.parametrize("pictures,kinds", [(2, []), (1, ["video"]), (1, ["audio", "video"])])
def test_same_profile_present_and_optional_av_capacity(store, workspace_factory, image_asset_factory, tmp_path, pictures, kinds):
    profile = ex.ExecutionStore(tmp_path / "ex").save("profiles", capacity_profile(av=True))
    w = ref_workspace(workspace_factory, image_asset_factory, pictures)
    w.assets.extend(asset(store, kind) for kind in kinds)
    p = store.create_project(w); select(store, p, valid_ref_prompt())
    result = prepared(store, p, profile)
    assert len(result.asset_requirements) == pictures + len(kinds)
    for kind in ["audio", "video"]:
        assert (f"{kind.title()} 1" in result.inactive_slots) == (kind not in kinds)
    assert profile.workflow["h3"]["inputs"]["audio-1"] == ["audio-1", 0]


def test_storage_layout_independent_and_streamed_copies(store, kind, workspace_factory, monkeypatch):
    from pathlib import Path
    p = store.create_project(workspace_factory(mode="Ref2VA", assets=[asset(store, kind)]))
    before = fingerprint(p.workspace)
    directory = store.root / f"project-{p.project_id}"
    raw = manifest(store, p)
    entry = raw["sequence"]["generations"][0]["assetFiles"][0]
    source = directory / entry["path"]
    moved = source.with_name("relocated.bin")
    source.rename(moved)
    entry["path"] = moved.relative_to(directory).as_posix()
    (directory / "project.json").write_text(json.dumps(raw))
    original = Path.read_bytes
    def no_large_read(path):
        if path.suffix == ".bin":
            raise AssertionError("AV must use bounded reads")
        return original(path)
    monkeypatch.setattr(Path, "read_bytes", no_large_read)
    loaded = store.load_project(p.project_id)
    assert fingerprint(loaded.workspace) == before
    store.save_project(p.project_id, loaded.workspace)
    fork = store.duplicate_project(p.project_id, "Copy")
    assert fingerprint(fork.workspace) == before


def test_manifest_path_traversal_rejected(store, kind, workspace_factory):
    from miniconstruct.project_store import MalformedProjectError
    p = store.create_project(workspace_factory(mode="Ref2VA", assets=[asset(store, kind)]))
    raw = manifest(store, p)
    raw["sequence"]["generations"][0]["assetFiles"][0]["path"] = "../../secret.bin"
    (store.root / f"project-{p.project_id}" / "project.json").write_text(json.dumps(raw))
    with pytest.raises(MalformedProjectError, match="escapes"):
        store.load_project(p.project_id)


def test_temporary_upload_expiry_does_not_affect_saved_project(store, kind, workspace_factory):
    import os
    import time
    a = asset(store, kind)
    p = store.create_project(workspace_factory(mode="Ref2VA", assets=[a]))
    staged = list((store.root / ".media-uploads").iterdir())
    for path in staged:
        os.utime(path, (time.time() - 90000, time.time() - 90000))
    upload(store, kind)
    assert all(not path.exists() for path in staged)
    assert store.load_project(p.project_id).workspace.assets[0].media_payload.sha256 == a.media_payload.sha256
    with pytest.raises(AssetPayloadError, match="upload unavailable"):
        store.create_project(workspace_factory(mode="Ref2VA", assets=[a]))


def test_picture_manifest_serialization_unchanged():
    from miniconstruct.project_store import AssetFileManifest
    entry = AssetFileManifest(assetId="a", kind="image", path="assets/pictures/a.png", mimeType="image/png")
    assert entry.model_dump(by_alias=True) == {"assetId": "a", "kind": "image", "path": "assets/pictures/a.png",
        "mimeType": "image/png", "width": None, "height": None}
