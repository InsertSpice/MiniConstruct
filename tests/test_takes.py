"""Provider-free output persistence; opaque bytes are never decoded as video."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from miniconstruct.app import app
from miniconstruct.models.takes import TakeImport
from miniconstruct.project_store import ProjectStore, ProjectStoreError
from miniconstruct.execution_state import SelectionWrite
from test_validator import VALID_BASE


@pytest.fixture
def env(tmp_path, workspace_factory, monkeypatch):
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(tmp_path))
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory())
    return store, project, tmp_path / f"project-{project.project_id}"


async def chunks(data):
    for i in range(0, len(data), 3):
        yield data[i:i + 3]


async def put(env, data=b"opaque video bytes", **kwargs):
    store, p, _ = env
    return await store.import_take(p.project_id, p.active_generation_id, chunks(data), kwargs.pop("filename", "video.mp4"),
                                   kwargs.pop("provenance", TakeImport(sourceKind="external_unknown")), **kwargs)


def manifest(env):
    return json.loads((env[2] / "project.json").read_text(encoding="utf-8"))


def select_prompt(store, project):
    _, status = store.execution_snapshot(project.project_id, project.active_generation_id)
    return store.select_execution_prompt(project.project_id, project.active_generation_id,
        SelectionWrite(prompt=VALID_BASE, sourceFingerprint=status.current_fingerprint)).selected


async def test_v12_nonmutating_and_import_upgrade(env):
    store, p, directory = env
    raw = manifest(env); raw["schemaVersion"] = 12
    for g in raw["sequence"]["generations"]:
        for key in ("takes", "renderSubmissions", "selectedTakeId"):
            g.pop(key)
    path = directory / "project.json"; path.write_text(json.dumps(raw), encoding="utf-8")
    before = path.read_bytes()
    loaded = store.load_project(p.project_id)
    assert loaded.outputs == {"takes": [], "renderSubmissions": [], "selectedTakeId": None, "takeObservations": []}
    assert path.read_bytes() == before
    result = await put(env)
    assert manifest(env)["schemaVersion"] == 17
    assert len(ProjectStore(store.root).load_project(p.project_id).outputs["takes"]) == 1
    assert result["selectedTakeId"] is None
    assert "promptSnapshot" not in result["takes"][0]
    assert "managedMediaPath" not in result["takes"][0]
    assert not list((directory / "outputs/.imports").iterdir())


async def test_exact_streaming_dedup_selection_immutability_and_gc(env):
    store, p, directory = env
    data = b"\x00unchanged original bytes" * 30
    first = await put(env, data); a = first["takes"][0]["id"]
    second = await put(env, data, filename="renamed.webm"); b = second["takes"][1]["id"]
    assert a != b and all(t["sha256"] == hashlib.sha256(data).hexdigest() for t in second["takes"])
    details_a = store.take_detail(p.project_id, p.active_generation_id, a)
    details_b = store.take_detail(p.project_id, p.active_generation_id, b)
    _, _, _, _, generation = store._output_context(p.project_id, p.active_generation_id)
    managed_paths = {str(t.id): t.managed_media_path for t in generation.takes}
    assert managed_paths[a] == managed_paths[b]
    assert details_a["mimeType"] == details_b["mimeType"] == "video/mp4"
    assert details_a["originalFilename"] == "video.mp4" and details_b["originalFilename"] == "renamed.webm"
    paths = list((directory / "outputs/media").iterdir())
    assert len(paths) == 1 and paths[0].read_bytes() == data
    _, fingerprint_before = store.execution_snapshot(p.project_id, p.active_generation_id)
    original = manifest(env)["sequence"]["generations"][0]
    store.select_take(p.project_id, p.active_generation_id, a)
    switched = store.select_take(p.project_id, p.active_generation_id, b)
    assert switched["selectedTakeId"] == b
    updated = manifest(env)["sequence"]["generations"][0]
    assert {k:v for k,v in updated.items() if k != "selectedTakeId"} == {k:v for k,v in original.items() if k != "selectedTakeId"}
    _, after = store.execution_snapshot(p.project_id, p.active_generation_id)
    assert after.current_fingerprint == fingerprint_before.current_fingerprint
    assert store.select_take(p.project_id, p.active_generation_id, None)["selectedTakeId"] is None
    store.select_take(p.project_id, p.active_generation_id, b)
    remaining = store.delete_take(p.project_id, p.active_generation_id, a)
    assert remaining["selectedTakeId"] == b and paths[0].exists()
    assert store.delete_take(p.project_id, p.active_generation_id, b)["selectedTakeId"] is None
    assert not paths[0].exists()


async def test_distinct_content_keeps_extension_mime_and_media_response_type(env):
    store, p, _ = env
    first = await put(env, b"mp4 bytes", filename="one.mp4")
    second = await put(env, b"webm bytes", filename="two.webm")
    a = store.take_detail(p.project_id, p.active_generation_id, first["takes"][0]["id"])
    b = store.take_detail(p.project_id, p.active_generation_id, second["takes"][-1]["id"])
    _, _, _, _, generation = store._output_context(p.project_id, p.active_generation_id)
    managed_paths = {str(t.id): t.managed_media_path for t in generation.takes}
    assert managed_paths[first["takes"][0]["id"]] != managed_paths[second["takes"][-1]["id"]]
    assert a["mimeType"] == "video/mp4" and b["mimeType"] == "video/webm"
    client = TestClient(app)
    base = f"/api/projects/{p.project_id}/generations/{p.active_generation_id}/takes"
    assert client.get(base + f'/{first["takes"][0]["id"]}/media').headers["content-type"] == "video/mp4"
    assert client.get(base + f'/{second["takes"][-1]["id"]}/media').headers["content-type"] == "video/webm"


async def test_missing_reattach_same_identity_and_hash(env):
    store, p, _ = env
    result = await put(env); take = result["takes"][0]
    path, _ = store.take_media(p.project_id, p.active_generation_id, take["id"])
    before = store.take_detail(p.project_id, p.active_generation_id, take["id"])
    path.unlink()
    assert store.load_project(p.project_id).outputs["takes"][0]["availability"] == "missing"
    with pytest.raises(ProjectStoreError, match="new Take"):
        await put(env, b"different", reattach_id=take["id"])
    assert not path.exists()
    restored = await put(env, reattach_id=take["id"])
    assert restored["takes"][0]["availability"] == "available"
    assert store.take_detail(p.project_id, p.active_generation_id, take["id"]) == before


async def test_prompt_snapshot_captured_before_stream_and_historical(env):
    store, p, _ = env
    selected = select_prompt(store, p)
    provenance = TakeImport(sourceKind="selected_prompt", selectedPromptId=selected.id)

    async def changing_chunks():
        store.clear_execution_prompt(p.project_id, p.active_generation_id)
        workspace = p.workspace.model_copy(deep=True); workspace.creative_request = "Later authoring"
        store.save_project(p.project_id, workspace)
        yield b"video"

    result = await store.import_take(p.project_id, p.active_generation_id, changing_chunks(), "take.mp4", provenance)
    take = store.take_detail(p.project_id, p.active_generation_id, result["takes"][0]["id"])
    snapshot = take["promptSnapshot"]
    assert snapshot["prompt"] == VALID_BASE
    assert snapshot["sha256"] == hashlib.sha256(VALID_BASE.encode()).hexdigest()
    assert snapshot["selectionId"] == str(selected.id) and snapshot["sourceFingerprint"] == selected.source_fingerprint
    assert snapshot["mode"] == "T2VA" and "historyId" not in snapshot
    with pytest.raises(ProjectStoreError, match="changed"):
        await put(env, provenance=provenance)
    unknown = await put(env)
    assert store.take_detail(p.project_id, p.active_generation_id, unknown["takes"][-1]["id"])["promptSnapshot"] is None
    _, current = store.execution_snapshot(p.project_id, p.active_generation_id)
    store.select_execution_prompt(p.project_id, p.active_generation_id,
        SelectionWrite(prompt=VALID_BASE + "\n", sourceFingerprint=current.current_fingerprint))
    store.save_project(p.project_id, p.workspace)
    assert store.take_detail(p.project_id, p.active_generation_id, take["id"])["promptSnapshot"] == snapshot


async def test_generation_lifecycle_save_and_fork(env):
    store, p, directory = env
    result = await put(env); take_id = result["takes"][0]["id"]
    store.select_take(p.project_id, p.active_generation_id, take_id)
    saved = store.save_project(p.project_id, p.workspace)
    assert saved.outputs["selectedTakeId"] == take_id
    duplicate = store.duplicate_generation(p.project_id)
    assert duplicate.outputs == {"takes": [], "renderSubmissions": [], "selectedTakeId": None, "takeObservations": []}
    blank = store.create_generation(p.project_id, insert_index=1)
    assert blank.outputs == duplicate.outputs
    ids = [g.generation_id for g in blank.sequence.generations]
    store.reorder_generations(p.project_id, ids[::-1])
    active = store.activate_generation(p.project_id, p.active_generation_id)
    assert active.outputs["selectedTakeId"] == take_id
    fork = store.duplicate_project(p.project_id, "Fork")
    assert fork.project_id != p.project_id and fork.active_generation_id != p.active_generation_id
    assert fork.outputs == active.outputs
    fork_path, _ = store.take_media(fork.project_id, fork.active_generation_id, take_id)
    assert not fork_path.is_symlink()
    store.delete_generation(p.project_id, p.active_generation_id)
    assert not list((directory / "outputs/media").iterdir())
    store.delete_project(p.project_id)
    assert not directory.exists() and fork_path.read_bytes() == b"opaque video bytes"


async def test_fork_copy_fallback_and_missing(env, monkeypatch):
    store, p, _ = env
    result = await put(env); take_id = result["takes"][0]["id"]
    def fail(*args): raise OSError("No hard links")
    monkeypatch.setattr("miniconstruct.take_store.os.link", fail)
    fork = store.duplicate_project(p.project_id, "Copy fallback")
    assert store.take_media(fork.project_id, fork.active_generation_id, take_id)[0].read_bytes() == b"opaque video bytes"
    store.take_media(p.project_id, p.active_generation_id, take_id)[0].unlink()
    missing = store.duplicate_project(p.project_id, "Missing copy")
    assert missing.outputs["takes"][0]["availability"] == "missing"


@pytest.mark.parametrize("filename", ["../escape.mp4", "..\\escape.mp4", "C:\\video.mp4", "/video.mp4", "bad\x00.mp4", "video.html"])
async def test_unsafe_filename_rejected(env, filename):
    with pytest.raises(ProjectStoreError): await put(env, filename=filename)
    assert not list((env[2] / "outputs").rglob("*.mp4"))


async def test_failure_cleanup_and_bounded_stream(env, monkeypatch):
    store, p, directory = env
    before = (directory / "project.json").read_bytes()
    monkeypatch.setattr("miniconstruct.take_store.MAX_TAKE_BYTES", 4)
    with pytest.raises(ProjectStoreError, match="exceeds"): await put(env)
    assert not list((directory / "outputs/.imports").iterdir())
    monkeypatch.setattr("miniconstruct.take_store.MAX_TAKE_BYTES", 100)
    def fail(*args, **kwargs): raise OSError("Disk full")
    monkeypatch.setattr(store, "_commit_sequence", fail)
    with pytest.raises(OSError): await put(env)
    assert (directory / "project.json").read_bytes() == before
    assert not list((directory / "outputs/media").iterdir())
    assert not list((directory / "outputs/.imports").iterdir())


async def test_concurrent_import_select_delete_valid(env):
    store, p, _ = env
    await asyncio.gather(put(env, b"first"), put(env, b"second"))
    ids = [t["id"] for t in store.generation_outputs(p.project_id, p.active_generation_id)["takes"]]
    def operate(i):
        try:
            if i % 3: store.select_take(p.project_id, p.active_generation_id, ids[i % 2])
            else: store.delete_take(p.project_id, p.active_generation_id, ids[i % 2])
        except ProjectStoreError: pass
    with ThreadPoolExecutor(max_workers=4) as pool: list(pool.map(operate, range(20)))
    output = store.load_project(p.project_id).outputs
    assert output["selectedTakeId"] is None or output["selectedTakeId"] in [t["id"] for t in output["takes"]]


def test_api_import_detail_range_selection_and_unknown(env):
    store, p, directory = env
    client = TestClient(app)
    base = f"/api/projects/{p.project_id}/generations/{p.active_generation_id}"
    result = client.post(base + "/takes", params={"filename": "video.mp4", "sourceKind": "external_unknown"}, content=b"0123456789")
    assert result.status_code == 201, result.text
    take_id = result.json()["takes"][0]["id"]
    assert client.get(base + f"/takes/{take_id}").json()["promptSnapshot"] is None
    response = client.get(base + f"/takes/{take_id}/media", headers={"Range": "bytes=2-5"})
    assert response.status_code == 206 and response.content == b"2345"
    assert response.headers["content-range"] == "bytes 2-5/10"
    assert client.put(base + f"/selected-take/{take_id}").json()["selectedTakeId"] == take_id
    assert client.delete(base + "/selected-take").json()["selectedTakeId"] is None
    assert "outputs" in client.get(f"/api/projects/{p.project_id}").json()
    assert "takes" not in client.get("/api/projects").text
    store.take_media(p.project_id, p.active_generation_id, take_id)[0].unlink()
    assert client.get(base + "/outputs").json()["takes"][0]["availability"] == "missing"
    assert client.get(base + f"/takes/{take_id}/media").status_code == 404
    bad = client.put(base + f"/takes/{take_id}/media", params={"filename": "video.mp4"}, content=b"different")
    assert bad.status_code == 422 and "new Take" in bad.text
    recovered = client.put(base + f"/takes/{take_id}/media", params={"filename": "video.mp4"}, content=b"0123456789")
    assert recovered.json()["takes"][0]["availability"] == "available"
    # Reattaching the original bytes keeps the canonical MP4 serving MIME.
    assert client.get(base + f"/takes/{take_id}/media").headers["content-type"] == "video/mp4"
    for path in [base + f"/takes/{uuid4()}/media", base.replace(p.active_generation_id, str(uuid4())) + f"/takes/{take_id}/media",
                 base.replace(p.project_id, str(uuid4())) + f"/takes/{take_id}/media"]:
        response = client.get(path)
        assert response.status_code == 404 and str(directory) not in response.text
    assert client.delete(base + f"/takes/{take_id}").json()["takes"] == []


def test_api_provenance_cannot_be_supplied_by_client(env):
    _, p, _ = env
    client = TestClient(app)
    base = f"/api/projects/{p.project_id}/generations/{p.active_generation_id}/takes"
    for metadata in [{}, {"sourceKind": "comfyui_submission"}, {"sourceKind": "external_unknown", "promptSnapshot": "fake"},
                     {"sourceKind": "external_unknown", "renderSubmissionId": str(uuid4())}]:
        assert client.post(base, params={"filename": "video.mp4", **metadata}, content=b"video").status_code == 422


async def test_stream_is_staged_incrementally_before_manifest_publication(env):
    store, p, directory = env
    before = (directory / "project.json").read_bytes()

    async def streaming():
        # Larger than Python's buffered file writes; still a bounded chunk.
        yield b"a" * 32768
        pending = list((directory / "outputs/.imports").glob("*.tmp"))
        assert len(pending) == 1 and pending[0].stat().st_size == 32768
        assert (directory / "project.json").read_bytes() == before
        assert not (directory / "outputs/media").exists()
        with pytest.raises(ProjectStoreError, match="Finish the current Take import"):
            ProjectStore(store.root).delete_project(p.project_id)
        assert (directory / "project.json").read_bytes() == before
        yield b"b" * 32768

    state = await store.import_take(p.project_id, p.active_generation_id, streaming(), "video.mp4", TakeImport(sourceKind="external_unknown"))
    assert state["takes"][0]["byteSize"] == 65536
    assert state["takes"][0]["sha256"] == hashlib.sha256(b"a" * 32768 + b"b" * 32768).hexdigest()


async def test_disconnect_and_deleted_generation_during_import_cleanup(env):
    store, p, directory = env

    async def disconnected():
        yield b"partial"
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await store.import_take(p.project_id, p.active_generation_id, disconnected(), "video.mp4", TakeImport(sourceKind="external_unknown"))
    assert not list((directory / "outputs/.imports").iterdir())

    store.create_generation(p.project_id)
    store.activate_generation(p.project_id, p.active_generation_id)

    async def deleted():
        yield b"partial"
        store.delete_generation(p.project_id, p.active_generation_id)
        yield b"last bytes"
    with pytest.raises(ProjectStoreError):
        await store.import_take(p.project_id, p.active_generation_id, deleted(), "video.mp4", TakeImport(sourceKind="external_unknown"))
    assert not list((directory / "outputs/.imports").iterdir())


async def test_shared_blob_across_generations_survives_generation_delete(env):
    store, p, _ = env
    state = await put(env)
    first_id = state["takes"][0]["id"]
    new = store.create_generation(p.project_id)
    second = await put((store, new, env[2]))
    second_id = second["takes"][0]["id"]
    first_path, _ = store.take_media(p.project_id, p.active_generation_id, first_id)
    second_path, _ = store.take_media(p.project_id, new.active_generation_id, second_id)
    assert first_path == second_path
    with pytest.raises(ProjectStoreError): store.select_take(p.project_id, new.active_generation_id, first_id)
    store.delete_generation(p.project_id, new.active_generation_id)
    assert first_path.exists()
    store.delete_take(p.project_id, p.active_generation_id, first_id)
    assert not first_path.exists()


async def test_story_created_generations_start_empty_and_h3_projection_unchanged(env):
    from miniconstruct.story_planning import initial_story_draft
    store, p, _ = env
    store.save_project(p.project_id, p.workspace, incoming_state="Wearing a red coat", intended_outgoing_state="Coat removed")
    context_before = store.h3_continuity_context(p.project_id, p.active_generation_id)
    _, status_before = store.execution_snapshot(p.project_id, p.active_generation_id)
    result = await put(env)
    store.select_take(p.project_id, p.active_generation_id, result["takes"][0]["id"])
    assert store.h3_continuity_context(p.project_id, p.active_generation_id) == context_before
    assert store.execution_snapshot(p.project_id, p.active_generation_id)[1].current_fingerprint == status_before.current_fingerprint
    draft = initial_story_draft(store.read_story_snapshot(p.project_id), 3)
    applied = store.apply_story_plan(p.project_id, draft)
    for generation in applied.sequence.generations[1:]:
        output = store.generation_outputs(p.project_id, generation.generation_id)
        assert output["takes"] == output["renderSubmissions"] == [] and output["selectedTakeId"] is None
    assert store.generation_outputs(p.project_id, p.active_generation_id)["selectedTakeId"] == result["takes"][0]["id"]


async def test_concurrent_uploads_revalidate_latest_manifest(env):
    store, p, _ = env
    entered, release = asyncio.Event(), asyncio.Event()
    async def slow():
        entered.set()
        await release.wait()
        yield b"shared bytes"
    pending = asyncio.create_task(store.import_take(p.project_id, p.active_generation_id, slow(), "slow.mp4", TakeImport(sourceKind="external_unknown")))
    await entered.wait()
    result = await put(env, b"shared bytes")
    selected_id = result["takes"][0]["id"]
    store.select_take(p.project_id, p.active_generation_id, selected_id)
    release.set()
    final = await pending
    assert len(final["takes"]) == 2 and final["selectedTakeId"] == selected_id
    assert len(list((env[2] / "outputs/media").iterdir())) == 1
