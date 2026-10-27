from __future__ import annotations

import json
from pathlib import Path
import uuid
import warnings

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from miniconstruct.app import app
from miniconstruct.project_store import (
    FinalGenerationDeletionError,
    GenerationNotActiveError,
    InvalidGenerationInsertionError,
    InvalidGenerationOrderError,
    MalformedProjectError,
    ProjectNotFoundError,
    ProjectStore,
    ProjectStoreError,
    UnsupportedProjectSchemaError,
)
from miniconstruct.models.workspace import ImagePayload
from miniconstruct.models.story_memory import StoryContinuityMemory
from miniconstruct.models.api import (
    EndpointProfile,
    GenerationCreateRequest,
    GenerationRequest,
    LLMSettings,
    ProjectSaveAsRequest,
)


def test_create_load_and_atomic_manifest_round_trip(tmp_path: Path, workspace_factory, image_asset_factory):
    workspace = workspace_factory(projectName="Portable Project", assets=[image_asset_factory()])
    store = ProjectStore(tmp_path)
    created = store.create_project(workspace)

    assert created.project_id
    assert created.project_id != "Portable Project"
    project_dir = tmp_path / f"project-{created.project_id}"
    manifest_path = project_dir / "project.json"
    manifest_text = manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    assert manifest["schemaVersion"] == 17
    assert manifest["projectId"] == created.project_id
    assert manifest["name"] == "Portable Project"
    sequence = manifest["sequence"]
    assert len(sequence["generations"]) == 1
    assert sequence["activeGenerationId"] == sequence["generations"][0]["generationId"]
    assert uuid.UUID(sequence["generations"][0]["generationId"]).version == 4
    assert "projectId" not in sequence["generations"][0]["workspace"]
    assert "projectName" not in sequence["generations"][0]["workspace"]
    assert ";base64," not in manifest_text
    assert not list(project_dir.glob(".project.json.*.tmp"))
    assert (project_dir / "assets" / "pictures").is_dir()
    assert (project_dir / "outputs").is_dir()

    loaded = store.load_project(created.project_id)
    assert loaded.workspace.model_dump(mode="json", by_alias=True) == created.workspace.model_dump(mode="json", by_alias=True)
    assert loaded.workspace.assets[0].image is not None
    saved = store.save_project(
        created.project_id,
        workspace_factory(projectName="Renamed", creativeRequest="Updated request"),
    )
    assert saved.name == "Renamed"
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["name"] == "Renamed"
    assert not list(project_dir.glob(".project.json.*.tmp"))
    assert store.load_project(created.project_id).workspace.project_name == "Renamed"


def test_display_name_is_not_directory_identity(tmp_path: Path, workspace_factory):
    store = ProjectStore(tmp_path)
    created = store.create_project(workspace_factory(projectName="One"))
    saved = store.save_project(created.project_id, workspace_factory(projectName="Two"))
    assert saved.project_id == created.project_id
    assert (tmp_path / f"project-{created.project_id}").is_dir()
    assert len(store.list_projects()) == 1
    assert store.list_projects()[0].name == "Two"


def test_delete_project_removes_owned_tree_and_preserves_siblings(tmp_path: Path, workspace_factory, image_asset_factory):
    store = ProjectStore(tmp_path)
    deleted = store.create_project(workspace_factory(projectName="Delete Me", assets=[image_asset_factory()]))
    sibling = store.create_project(workspace_factory(projectName="Keep Me"))
    deleted_dir = tmp_path / f"project-{deleted.project_id}"
    (deleted_dir / "outputs" / "nested").mkdir(parents=True)
    (deleted_dir / "outputs" / "nested" / "owned.txt").write_text("owned", encoding="utf-8")
    (deleted_dir / "workflows" / "workflow.json").write_text("{}", encoding="utf-8")

    store.delete_project(deleted.project_id)

    assert not deleted_dir.exists()
    with pytest.raises(ProjectNotFoundError):
        store.load_project(deleted.project_id)
    assert deleted.project_id not in {project.project_id for project in store.list_projects()}
    assert store.load_project(sibling.project_id).name == "Keep Me"
    assert (tmp_path / f"project-{sibling.project_id}").is_dir()


def test_delete_project_rejects_missing_invalid_and_traversal_ids(tmp_path: Path):
    store = ProjectStore(tmp_path)
    with pytest.raises(ProjectNotFoundError):
        store.delete_project(str(uuid.uuid4()))
    for project_id in ("not-a-uuid", "../outside", r"..\outside", r"C:\\arbitrary", "/etc/passwd"):
        with pytest.raises(ProjectStoreError, match="UUID"):
            store.delete_project(project_id)
    assert list(tmp_path.iterdir()) == []


def test_delete_project_refuses_project_directory_symlink(tmp_path: Path):
    store = ProjectStore(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    project_id = str(uuid.uuid4())
    link = tmp_path / f"project-{project_id}"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks are unavailable in this environment")

    with pytest.raises(ProjectStoreError, match="symlinks"):
        store.delete_project(project_id)
    assert outside.is_dir()
    assert (outside / "keep.txt").is_file()


def test_changed_image_content_uses_a_new_blob_path_and_identical_content_reuses_it(
    tmp_path: Path, workspace_factory, image_asset_factory,
):
    store = ProjectStore(tmp_path)
    original = workspace_factory(assets=[image_asset_factory()])
    created = store.create_project(original)
    manifest_path = tmp_path / f"project-{created.project_id}" / "project.json"
    old_path = json.loads(manifest_path.read_text(encoding="utf-8"))["sequence"]["generations"][0]["assetFiles"][0]["path"]

    changed_asset = image_asset_factory().model_copy(update={
        "image": ImagePayload(data_url="data:image/png;base64,Ag==", width=1, height=1),
    })
    store.save_project(created.project_id, workspace_factory(assets=[changed_asset]))
    new_path = json.loads(manifest_path.read_text(encoding="utf-8"))["sequence"]["generations"][0]["assetFiles"][0]["path"]
    assert old_path != new_path
    assert store.load_project(created.project_id).workspace.assets[0].id == "img-1"

    store.save_project(created.project_id, workspace_factory(assets=[changed_asset]))
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["sequence"]["generations"][0]["assetFiles"][0]["path"] == new_path
    assert len(list((manifest_path.parent / "assets" / "pictures").iterdir())) == 2


def test_failed_save_preserves_old_manifest_workspace_and_image_bytes(
    tmp_path: Path, workspace_factory, image_asset_factory, monkeypatch,
):
    store = ProjectStore(tmp_path)
    original = workspace_factory(assets=[image_asset_factory()])
    created = store.create_project(original)
    project_dir = tmp_path / f"project-{created.project_id}"
    old_manifest = (project_dir / "project.json").read_text(encoding="utf-8")
    old_workspace = store.load_project(created.project_id).workspace.model_dump(mode="json", by_alias=True)
    changed_asset = image_asset_factory().model_copy(update={
        "image": ImagePayload(data_url="data:image/png;base64,Ag==", width=1, height=1),
    })
    real_atomic_write = store._atomic_write

    def fail_manifest(path: Path, content: bytes) -> None:
        if path.name == "project.json":
            raise OSError("simulated manifest commit failure")
        real_atomic_write(path, content)

    monkeypatch.setattr(store, "_atomic_write", fail_manifest)
    with pytest.raises(OSError, match="manifest commit failure"):
        store.save_project(created.project_id, workspace_factory(projectName="Changed", assets=[changed_asset]))

    assert (project_dir / "project.json").read_text(encoding="utf-8") == old_manifest
    loaded = store.load_project(created.project_id).workspace
    assert loaded.model_dump(mode="json", by_alias=True) == old_workspace
    assert loaded.assets[0].image.data_url == original.assets[0].image.data_url


def test_failed_creation_leaves_no_visible_project_or_listing_poison(tmp_path: Path, workspace_factory, monkeypatch):
    store = ProjectStore(tmp_path)

    def fail_manifest(path: Path, content: bytes) -> None:
        if path.name == "project.json":
            raise OSError("simulated initial manifest failure")
        ProjectStore._atomic_write(path, content)

    monkeypatch.setattr(store, "_atomic_write", fail_manifest)
    with pytest.raises(OSError, match="initial manifest failure"):
        store.create_project(workspace_factory())

    assert not list(tmp_path.glob("project-*"))
    assert not list(tmp_path.glob(".project-*.creating"))
    assert store.list_projects() == []


def test_unsupported_and_malformed_manifests_fail_clearly(tmp_path: Path, workspace_factory):
    store = ProjectStore(tmp_path)
    created = store.create_project(workspace_factory())
    manifest_path = tmp_path / f"project-{created.project_id}" / "project.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw["schemaVersion"] = 99
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(UnsupportedProjectSchemaError, match="Unsupported project schema version"):
        store.load_project(created.project_id)
    manifest_path.write_text("{not json", encoding="utf-8")
    with pytest.raises(MalformedProjectError, match="Malformed project.json"):
        store.load_project(created.project_id)


@pytest.mark.parametrize("project_id", ["../outside", r"..\outside", r"C:\\arbitrary", "/etc/passwd"])
def test_project_identifier_cannot_escape_root(tmp_path: Path, project_id: str):
    store = ProjectStore(tmp_path)
    with pytest.raises(ProjectStoreError, match="UUID"):
        store.load_project(project_id)
    assert list(tmp_path.iterdir()) == []


def test_projects_api_create_list_load_and_save(tmp_path: Path, monkeypatch, workspace_factory):
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(tmp_path))
    workspace = workspace_factory(projectName="API Project")
    with TestClient(app) as client:
        created_response = client.post(
            "/api/projects",
            json={"workspace": workspace.model_dump(mode="json", by_alias=True)},
        )
        assert created_response.status_code == 201
        created = created_response.json()
        project_id = created["id"]
        assert created["name"] == "API Project"
        listed = client.get("/api/projects")
        assert listed.status_code == 200
        assert listed.json()["projects"][0]["id"] == project_id
        loaded = client.get(f"/api/projects/{project_id}")
        assert loaded.status_code == 200
        assert loaded.json()["workspace"]["creativeRequest"] == "A cyclist crosses a wet street."
        updated_workspace = workspace.model_copy(update={"project_name": "API Renamed", "creative_request": "A new request."})
        saved = client.put(
            f"/api/projects/{project_id}",
            json={"workspace": updated_workspace.model_dump(mode="json", by_alias=True)},
        )
        assert saved.status_code == 200
        assert saved.json()["name"] == "API Renamed"
        deleted = client.delete(f"/api/projects/{project_id}")
        assert deleted.status_code == 204
        assert deleted.content == b""
        assert client.get(f"/api/projects/{project_id}").status_code == 404
        assert all(item["id"] != project_id for item in client.get("/api/projects").json()["projects"])
        assert client.delete(f"/api/projects/{uuid.uuid4()}").status_code == 404


def test_v9_shape_adapters_and_listing_isolate_malformed_projects(tmp_path: Path, monkeypatch, workspace_factory, caplog):
    """Both historical v9 layouts remain read-only inputs; writes are v13."""
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(tmp_path))
    store = ProjectStore(tmp_path)

    def path(project):
        return tmp_path / f"project-{project.project_id}" / "project.json"

    def temporal(project, *, status, checkpoints):
        manifest_path = path(project)
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        raw["schemaVersion"] = 9
        for generation in raw["sequence"]["generations"]:
            generation.pop("storyMemoryAfter")
            generation.pop("storyMemoryStatus")
        raw["sequence"]["storyMemory"] = terminal = StoryContinuityMemory(
            recentSummary=f"terminal {status}").model_dump(by_alias=True)
        raw["sequence"]["storyMemoryTemporal"] = {
            "status": status,
            "segments": [{
                "throughGenerationId": raw["sequence"]["generations"][-1]["generationId"],
                "checkpoints": [{
                    "effectiveAfterGenerationId": generation_id,
                    "memorySnapshot": memory.model_dump(by_alias=True),
                } for generation_id, memory in checkpoints],
                "events": [],
            }],
        }
        manifest_path.write_text(json.dumps(raw), encoding="utf-8")
        return terminal

    current = store.create_project(workspace_factory(projectName="Temporal current"))
    current_terminal = temporal(current, status="current", checkpoints=[
        (current.active_generation_id, StoryContinuityMemory(recentSummary="terminal current")),
    ])
    current_path, current_before = path(current), path(current).read_bytes()
    current_loaded = store.load_project(current.project_id)
    assert current_loaded.story_memory_after.model_dump(by_alias=True) == current_terminal
    assert current_path.read_bytes() == current_before

    review = store.create_project(workspace_factory(projectName="Temporal review"))
    review = store.create_generation(review.project_id)
    review_first = store.read_story_snapshot(review.project_id).generations[0].generation_id
    historical = StoryContinuityMemory(recentSummary="known old checkpoint")
    temporal(review, status="needs_review", checkpoints=[(review_first, historical)])
    review_path, review_before = path(review), path(review).read_bytes()
    review_snapshot = store.read_story_snapshot(review.project_id)
    assert review_snapshot.generations[0].story_memory_after == historical
    assert review_snapshot.generations[0].story_memory_status == "trusted"
    assert review_snapshot.generations[-1].story_memory_status == "needs_review"
    assert review_path.read_bytes() == review_before

    generation_owned = store.create_project(workspace_factory(projectName="Generation owned v9"))
    generation_owned_path = path(generation_owned)
    generation_owned_raw = json.loads(generation_owned_path.read_text(encoding="utf-8"))
    generation_owned_raw["schemaVersion"] = 9
    generation_owned_path.write_text(json.dumps(generation_owned_raw), encoding="utf-8")
    generation_owned_before = generation_owned_path.read_bytes()
    assert store.load_project(generation_owned.project_id).story_memory_after == StoryContinuityMemory()
    assert generation_owned_path.read_bytes() == generation_owned_before

    canonical = store.create_project(workspace_factory(projectName="Canonical v13"))
    v8 = store.create_project(workspace_factory(projectName="Legacy v8"))
    v8_path = path(v8)
    v8_raw = json.loads(v8_path.read_text(encoding="utf-8"))
    v8_raw["schemaVersion"] = 8
    v8_raw["sequence"]["storyMemory"] = StoryContinuityMemory(recentSummary="v8 terminal").model_dump(by_alias=True)
    for generation in v8_raw["sequence"]["generations"]:
        generation.pop("storyMemoryAfter")
        generation.pop("storyMemoryStatus")
    v8_path.write_text(json.dumps(v8_raw), encoding="utf-8")
    v8_before = v8_path.read_bytes()
    assert store.load_project(v8.project_id).story_memory_after.recent_summary == "v8 terminal"
    assert v8_path.read_bytes() == v8_before

    corrupt_id = str(uuid.uuid4())
    corrupt = tmp_path / f"project-{corrupt_id}"
    corrupt.mkdir()
    (corrupt / "project.json").write_text("{ malformed", encoding="utf-8")
    listed_before = {item: item.read_bytes() for item in (
        current_path, review_path, generation_owned_path, path(canonical), v8_path,
    )}

    with TestClient(app) as client:
        response = client.get("/api/projects")
    assert response.status_code == 200
    assert {item["id"] for item in response.json()["projects"]} == {
        current.project_id, review.project_id, generation_owned.project_id, canonical.project_id, v8.project_id,
    }
    assert corrupt_id not in {item["id"] for item in response.json()["projects"]}
    assert str(corrupt) in caplog.text and "Skipping Project directory" in caplog.text
    assert {item: item.read_bytes() for item in listed_before} == listed_before

    # Explicit writes, not reads/listing, perform the compatibility migration.
    assert json.loads(path(store.save_project(current.project_id, current_loaded.workspace)).read_text())["schemaVersion"] == 17
    assert json.loads(path(store.save_project(review.project_id, store.load_project(review.project_id).workspace)).read_text())["schemaVersion"] == 17
    assert json.loads(path(store.save_project(generation_owned.project_id, store.load_project(generation_owned.project_id).workspace)).read_text())["schemaVersion"] == 17


def test_workspace_serialization_warning_absent_for_current_legacy_and_save_reload(
    tmp_path: Path, monkeypatch, workspace_factory,
):
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(tmp_path))
    store = ProjectStore(tmp_path)
    created = store.create_project(workspace_factory(projectName="Warning Check"))
    path = _manifest_path(tmp_path, created.project_id)

    def warning_messages(caught):
        return [str(item.message) for item in caught if "PydanticSerializationUnexpectedValue" in str(item.message)]

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with TestClient(app) as client:
            loaded = client.get(f"/api/projects/{created.project_id}")
            assert loaded.status_code == 200
            saved = client.put(
                f"/api/projects/{created.project_id}",
                json={"workspace": loaded.json()["workspace"]},
            )
            assert saved.status_code == 200
            reloaded = client.get(f"/api/projects/{created.project_id}")
            assert reloaded.status_code == 200
    assert warning_messages(caught) == []

    legacy = _downgrade_manifest_to_v1(json.loads(path.read_text(encoding="utf-8")))
    path.write_text(json.dumps(legacy), encoding="utf-8")
    before = path.read_bytes()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with TestClient(app) as client:
            loaded = client.get(f"/api/projects/{created.project_id}")
            assert loaded.status_code == 200
    assert path.read_bytes() == before
    assert warning_messages(caught) == []


def test_projects_api_sequence_lifecycle_and_save_as_override(tmp_path: Path, monkeypatch, workspace_factory):
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(tmp_path))
    workspace = workspace_factory(creativeRequest="A")
    with TestClient(app) as client:
        created = client.post("/api/projects", json={"workspace": workspace.model_dump(mode="json", by_alias=True)}).json()
        project_id = created["id"]
        first_id = created["sequence"]["activeGenerationId"]
        added = client.post(f"/api/projects/{project_id}/generations", json={}).json()
        second_id = added["sequence"]["activeGenerationId"]
        assert added["workspace"]["creativeRequest"] == ""
        activated = client.put(f"/api/projects/{project_id}/active-generation/{first_id}")
        assert activated.status_code == 200
        duplicate = client.post(f"/api/projects/{project_id}/generations/{first_id}/duplicate")
        assert duplicate.status_code == 200
        duplicate_data = duplicate.json()
        assert duplicate_data["sequence"]["generations"][1]["generationId"] == duplicate_data["sequence"]["activeGenerationId"]
        order = [item["generationId"] for item in reversed(duplicate_data["sequence"]["generations"])]
        reordered = client.put(f"/api/projects/{project_id}/generation-order", json={"generationIds": order})
        assert reordered.status_code == 200
        deleted = client.delete(f"/api/projects/{project_id}/generations/{first_id}")
        assert deleted.status_code == 400  # only the active Generation may be deleted
        active_id = reordered.json()["sequence"]["activeGenerationId"]
        deleted = client.delete(f"/api/projects/{project_id}/generations/{active_id}")
        assert deleted.status_code == 200
        fork = client.post(
            f"/api/projects/{project_id}/save-as",
            json={"name": "Fork", "workspace": workspace_factory(creativeRequest="Current override").model_dump(mode="json", by_alias=True)},
        )
        assert fork.status_code == 201
        fork_data = fork.json()
        assert fork_data["id"] != project_id
        assert fork_data["workspace"]["creativeRequest"] == "Current override"
        assert len(fork_data["sequence"]["generations"]) == 2
        assert client.put(f"/api/projects/{project_id}/generation-order", json={"generationIds": [second_id]}).status_code == 400


def test_projects_api_save_as_forwards_sequence_dialogue_language(tmp_path: Path, monkeypatch, workspace_factory):
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(tmp_path))
    source_dialogue = "Kent: Run exactly as authored."
    source_workspace = workspace_factory(dialogue=source_dialogue, dialogueLanguage="as_written")
    with TestClient(app) as client:
        created = client.post("/api/projects", json={"workspace": source_workspace.model_dump(mode="json", by_alias=True)})
        assert created.status_code == 201, created.text
        source_id = created.json()["id"]
        fork = client.post(
            f"/api/projects/{source_id}/save-as",
            json={
                "name": "Japanese fork",
                "dialogueLanguage": "ja",
                "workspace": source_workspace.model_dump(mode="json", by_alias=True),
            },
        )
        assert fork.status_code == 201, fork.text
        fork_data = fork.json()
        assert fork_data["dialogueLanguage"] == "ja"
        assert fork_data["workspace"]["dialogueLanguage"] == "ja"
        assert fork_data["workspace"]["dialogue"] == source_dialogue
        original = client.get(f"/api/projects/{source_id}")
        assert original.status_code == 200
        assert original.json()["dialogueLanguage"] == "as_written"
        assert original.json()["workspace"]["dialogue"] == source_dialogue


def test_projects_api_saves_active_continuity_without_requiring_a_sequence_payload(
    tmp_path: Path, monkeypatch, workspace_factory,
):
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(tmp_path))
    with TestClient(app) as client:
        created = client.post(
            "/api/projects",
            json={
                "workspace": workspace_factory().model_dump(mode="json", by_alias=True),
                "continuity": {
                    "sequenceContext": "The door remains closed.",
                    "incomingState": "Alice is outside.",
                    "intendedOutgoingState": "Alice enters.",
                },
            },
        )
        assert created.status_code == 201, created.text
        project_id = created.json()["id"]
        assert created.json()["continuity"] == {
            "sequenceContext": "The door remains closed.",
            "incomingState": "Alice is outside.",
            "intendedOutgoingState": "Alice enters.",
        }
        saved = client.put(
            f"/api/projects/{project_id}",
            json={"workspace": created.json()["workspace"], "continuity": {"incomingState": "Alice is waiting."}},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["continuity"] == {
            "sequenceContext": "The door remains closed.",
            "incomingState": "Alice is waiting.",
            "intendedOutgoingState": "Alice enters.",
        }


def test_projects_api_planning_visual_handoff_is_typed_persistent_and_patch_safe(
    tmp_path: Path, monkeypatch, workspace_factory,
):
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(tmp_path))
    policy = {
        "strategy": "endpoint_then_immediate_cut",
        "tailSeconds": 1.75,
        "immediateCutMilliseconds": 240,
    }
    with TestClient(app) as client:
        created = client.post("/api/projects", json={
            "workspace": workspace_factory().model_dump(mode="json", by_alias=True),
            "planning": {"scenePlan": ""},
        })
        assert created.status_code == 201, created.text
        assert created.json()["planning"]["scenePlan"] == ""
        project_id, workspace = created.json()["id"], created.json()["workspace"]
        first = client.put(f"/api/projects/{project_id}", json={
            "workspace": workspace,
            "planning": {
                "scenePlan": "Nino checks the desk while Makoto searches the shelves.",
                "relationshipToPrevious": "continue_previous_shot",
                "cameraIntent": "Hold the forward dolly.",
                "visualHandoff": policy,
            },
        })
        assert first.status_code == 200, first.text
        assert first.json()["planning"] == {
            "scenePlan": "Nino checks the desk while Makoto searches the shelves.",
            "relationshipToPrevious": "continue_previous_shot",
            "cameraIntent": "Hold the forward dolly.",
            "shotCount": 1,
            "visualHandoff": policy,
        }
        reloaded = client.get(f"/api/projects/{project_id}")
        assert reloaded.status_code == 200
        assert reloaded.json()["planning"]["scenePlan"] == "Nino checks the desk while Makoto searches the shelves."
        assert reloaded.json()["planning"]["visualHandoff"] == policy

        # Omission means no patch for this field, not reset-to-default.
        patched = client.put(f"/api/projects/{project_id}", json={
            "workspace": reloaded.json()["workspace"],
            "planning": {"cameraIntent": "Pan with the subject."},
        })
        assert patched.status_code == 200, patched.text
        assert patched.json()["planning"] == {
            "scenePlan": "Nino checks the desk while Makoto searches the shelves.",
            "relationshipToPrevious": "continue_previous_shot",
            "cameraIntent": "Pan with the subject.",
            "shotCount": 1,
            "visualHandoff": policy,
        }
        edited = client.put(f"/api/projects/{project_id}", json={
            "workspace": patched.json()["workspace"],
            "planning": {"scenePlan": "They find a photograph under the desk."},
        })
        assert edited.status_code == 200, edited.text
        assert edited.json()["planning"]["scenePlan"] == "They find a photograph under the desk."
        assert edited.json()["planning"]["cameraIntent"] == "Pan with the subject."
        assert edited.json()["planning"]["relationshipToPrevious"] == "continue_previous_shot"
        assert edited.json()["planning"]["visualHandoff"] == policy
        assert client.get(f"/api/projects/{project_id}").json()["planning"]["scenePlan"] == "They find a photograph under the desk."
        invalid = client.put(f"/api/projects/{project_id}", json={
            "workspace": edited.json()["workspace"],
            "planning": {"unknownPlanningField": True},
        })
        assert invalid.status_code == 422

        fork = client.post(f"/api/projects/{project_id}/save-as", json={
            "name": "Scene Plan Fork", "planning": {"scenePlan": "Forked scene plan."},
        })
        assert fork.status_code == 201, fork.text
        assert fork.json()["planning"]["scenePlan"] == "Forked scene plan."


def test_blank_authoring_workspace_saves_and_forks_through_api(tmp_path: Path, monkeypatch, workspace_factory):
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(tmp_path))
    with TestClient(app) as client:
        created = client.post(
            "/api/projects", json={"workspace": workspace_factory().model_dump(mode="json", by_alias=True)}
        ).json()
        project_id = created["id"]
        blank = client.post(f"/api/projects/{project_id}/generations", json={}).json()
        assert blank["workspace"]["creativeRequest"] == ""
        assert blank["workspace"]["aspectRatio"] == "4:3"
        saved = client.put(f"/api/projects/{project_id}", json={"workspace": blank["workspace"]})
        assert saved.status_code == 200, saved.text
        assert saved.json()["workspace"]["creativeRequest"] == ""
        fork = client.post(
            f"/api/projects/{project_id}/save-as",
            json={"name": "Blank Fork", "workspace": saved.json()["workspace"]},
        )
        assert fork.status_code == 201, fork.text
        assert fork.json()["workspace"]["creativeRequest"] == ""
        assert fork.json()["id"] != project_id


@pytest.mark.parametrize("ratio", ["auto", "16:9", "9:16", "1:1", "4:3", "3:4", "custom:11/7"])
def test_explicit_generation_aspect_ratio_survives_load_and_navigation(tmp_path: Path, workspace_factory, ratio):
    store = ProjectStore(tmp_path)
    created = store.create_project(workspace_factory(aspectRatio=ratio))
    first_id = created.active_generation_id
    assert store.load_project(created.project_id).workspace.aspect_ratio == ratio
    added = store.create_generation(created.project_id)
    second_id = added.active_generation_id
    assert added.workspace.aspect_ratio == "4:3"
    reopened = store.activate_generation(created.project_id, first_id)
    assert reopened.workspace.aspect_ratio == ratio
    assert store.activate_generation(created.project_id, second_id).workspace.aspect_ratio == "4:3"


def test_ref2va_without_reference_asset_put_saves_but_generation_request_rejects(
    tmp_path: Path, monkeypatch, workspace_factory
):
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(tmp_path))
    with TestClient(app) as client:
        created = client.post(
            "/api/projects", json={"workspace": workspace_factory().model_dump(mode="json", by_alias=True)}
        )
        assert created.status_code == 201, created.text
        project_id = created.json()["id"]

        generation = client.post(f"/api/projects/{project_id}/generations", json={})
        assert generation.status_code == 200, generation.text
        workspace = generation.json()["workspace"]
        workspace["mode"] = "Ref2VA"
        workspace["assets"] = []

        saved = client.put(f"/api/projects/{project_id}", json={"workspace": workspace})
        assert saved.status_code == 200, saved.text
        saved_workspace = saved.json()["workspace"]
        assert saved_workspace["mode"] == "Ref2VA"
        assert saved_workspace["assets"] == []
        assert saved_workspace["creativeRequest"] == ""

        llm = LLMSettings(
            endpoint=EndpointProfile(
                id="test",
                displayName="Test",
                baseUrl="http://127.0.0.1:1234/v1",
            )
        ).model_dump(mode="json", by_alias=True)
        with pytest.raises(ValidationError):
            GenerationRequest.model_validate({"workspace": saved_workspace, "llm": llm})


def test_authoring_validation_allows_blank_but_generation_validation_does_not(workspace_factory):
    blank = workspace_factory().model_copy(update={"creative_request": ""})
    # model_copy is deliberately used here to represent the browser's blank
    # draft; ordinary model construction remains strict for model requests.
    assert GenerationCreateRequest.model_validate({"workspace": blank.model_dump(mode="json", by_alias=True)}).workspace.creative_request == ""
    assert ProjectSaveAsRequest.model_validate({"name": "Fork", "workspace": blank.model_dump(mode="json", by_alias=True)}).workspace.creative_request == ""
    with pytest.raises(ValidationError):
        type(blank).model_validate(blank.model_dump(mode="json", by_alias=True))


def test_v1_structural_upgrade_canonicalizes_all_generation_workspaces_and_rename(
    tmp_path: Path, workspace_factory,
):
    store = ProjectStore(tmp_path)
    created = store.create_project(workspace_factory(projectName="Legacy", creativeRequest="Legacy request"))
    path = _manifest_path(tmp_path, created.project_id)
    v1_manifest = _downgrade_manifest_to_v1(json.loads(path.read_text(encoding="utf-8")))
    path.write_text(json.dumps(v1_manifest), encoding="utf-8")
    expected_id = str(uuid.uuid5(uuid.UUID(created.project_id), "initial-generation"))

    upgraded = store.create_generation(created.project_id)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["schemaVersion"] == 17
    assert manifest["sequence"]["generations"][0]["generationId"] == expected_id
    assert uuid.UUID(manifest["sequence"]["generations"][1]["generationId"]).version == 4
    assert all(
        "projectId" not in generation["workspace"] and "projectName" not in generation["workspace"]
        for generation in manifest["sequence"]["generations"]
    )
    renamed = store.save_project(
        created.project_id,
        upgraded.workspace.model_copy(update={"project_name": "Renamed"}),
    )
    assert renamed.name == "Renamed"
    reloaded = store.load_project(created.project_id)
    assert reloaded.name == "Renamed"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert all("projectId" not in generation["workspace"] and "projectName" not in generation["workspace"] for generation in manifest["sequence"]["generations"])


def test_v1_save_as_always_writes_canonical_v2_destination(tmp_path: Path, workspace_factory):
    store = ProjectStore(tmp_path)
    created = store.create_project(workspace_factory(projectName="Legacy", creativeRequest="Legacy request"))
    source_path = _manifest_path(tmp_path, created.project_id)
    source_path.write_text(json.dumps(_downgrade_manifest_to_v1(json.loads(source_path.read_text(encoding="utf-8")))), encoding="utf-8")
    fork = store.duplicate_project(created.project_id, "Fork")
    destination = json.loads(_manifest_path(tmp_path, fork.project_id).read_text(encoding="utf-8"))
    assert destination["schemaVersion"] == 17
    assert all("projectId" not in generation["workspace"] and "projectName" not in generation["workspace"] for generation in destination["sequence"]["generations"])
    override = workspace_factory(creativeRequest="V1 override")
    override_fork = store.duplicate_project(created.project_id, "Override Fork", active_workspace=override)
    override_manifest = json.loads(_manifest_path(tmp_path, override_fork.project_id).read_text(encoding="utf-8"))
    assert override_fork.workspace.creative_request == "V1 override"
    assert all("projectId" not in generation["workspace"] and "projectName" not in generation["workspace"] for generation in override_manifest["sequence"]["generations"])


def test_malformed_v1_workspace_identity_is_rejected_before_canonicalization(tmp_path: Path, workspace_factory):
    store = ProjectStore(tmp_path)
    created = store.create_project(workspace_factory(projectName="Legacy", creativeRequest="Legacy request"))
    path = _manifest_path(tmp_path, created.project_id)
    v1_manifest = _downgrade_manifest_to_v1(json.loads(path.read_text(encoding="utf-8")))
    v1_manifest["workspace"]["projectId"] = str(uuid.uuid4())
    path.write_text(json.dumps(v1_manifest), encoding="utf-8")
    with pytest.raises(MalformedProjectError, match="projectId does not agree"):
        store.create_generation(created.project_id)


def test_projects_api_rejects_invalid_identifier(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(tmp_path))
    with TestClient(app) as client:
        response = client.get("/api/projects/not-a-uuid")
        delete_response = client.delete("/api/projects/not-a-uuid")
    assert response.status_code == 400
    assert delete_response.status_code == 400


def _manifest_path(root: Path, project_id: str) -> Path:
    return root / f"project-{project_id}" / "project.json"


def _downgrade_manifest_to_v1(manifest: dict) -> dict:
    generation = manifest["sequence"]["generations"][0]
    workspace = json.loads(json.dumps(generation["workspace"]))
    workspace["projectId"] = manifest["projectId"]
    workspace["projectName"] = manifest["name"]
    return {
        "schemaVersion": 1,
        "projectId": manifest["projectId"],
        "name": manifest["name"],
        "createdAt": manifest["createdAt"],
        "updatedAt": manifest["updatedAt"],
        "workspace": workspace,
        "assetFiles": generation["assetFiles"],
    }


def _downgrade_manifest_to_v2(manifest: dict) -> dict:
    downgraded = json.loads(json.dumps(manifest))
    downgraded.pop("storyIdea", None)
    downgraded["schemaVersion"] = 2
    downgraded["sequence"].pop("storyMemory", None)
    for legacy_generation in downgraded["sequence"]["generations"]:
        legacy_generation.pop("storyMemoryAfter", None)
        legacy_generation.pop("storyMemoryStatus", None)
    downgraded["sequence"].pop("subjects", None)
    downgraded["sequence"].pop("nextSubjectNumber", None)
    downgraded["sequence"].pop("continuityContext", None)
    for generation in downgraded["sequence"]["generations"]:
        generation.pop("continuity", None)
        generation.pop("planning", None)
    return downgraded


def test_schema_v3_continuity_round_trip_and_generation_lifecycle(
    tmp_path: Path, workspace_factory,
):
    store = ProjectStore(tmp_path)
    created = store.create_project(
        workspace_factory(creativeRequest="First"),
        sequence_context="The laboratory remains abandoned.",
        incoming_state="Alice is beside the machine.",
        intended_outgoing_state="The machine is powered off.",
    )
    first_id = created.active_generation_id
    assert created.sequence_context == "The laboratory remains abandoned."
    assert created.incoming_state == "Alice is beside the machine."
    manifest = json.loads(_manifest_path(tmp_path, created.project_id).read_text(encoding="utf-8"))
    assert manifest["schemaVersion"] == 17
    assert manifest["sequence"]["continuityContext"] == "The laboratory remains abandoned."
    assert manifest["sequence"]["generations"][0]["continuity"] == {
        "incomingState": "Alice is beside the machine.",
        "intendedOutgoingState": "The machine is powered off.",
    }

    second = store.create_generation(created.project_id)
    second_id = second.active_generation_id
    assert second.incoming_state == ""
    assert second.intended_outgoing_state == ""
    second = store.save_project(
        created.project_id,
        workspace_factory(creativeRequest="Second"),
        incoming_state="Bob has not entered.",
        intended_outgoing_state="Bob enters the laboratory.",
    )
    store.activate_generation(created.project_id, first_id)
    duplicate = store.duplicate_generation(created.project_id, first_id)
    duplicate_id = duplicate.active_generation_id
    assert duplicate.workspace.aspect_ratio == created.workspace.aspect_ratio
    assert duplicate.incoming_state == "Alice is beside the machine."
    assert duplicate.intended_outgoing_state == "The machine is powered off."

    reordered = store.reorder_generations(created.project_id, [second_id, duplicate_id, first_id])
    assert [item.generation_id for item in reordered.sequence.generations] == [second_id, duplicate_id, first_id]
    store.activate_generation(created.project_id, duplicate_id)
    deleted = store.delete_generation(created.project_id, duplicate_id)
    assert duplicate_id not in [item.generation_id for item in deleted.sequence.generations]
    assert store.load_project(created.project_id).sequence_context == "The laboratory remains abandoned."
    assert store.load_project(created.project_id).incoming_state == "Bob has not entered."


def test_v1_and_v2_reads_are_non_mutating_and_next_write_upgrades_to_v3(
    tmp_path: Path, workspace_factory,
):
    store = ProjectStore(tmp_path)
    created = store.create_project(workspace_factory(creativeRequest="Legacy"))
    path = _manifest_path(tmp_path, created.project_id)

    v2 = _downgrade_manifest_to_v2(json.loads(path.read_text(encoding="utf-8")))
    path.write_text(json.dumps(v2), encoding="utf-8")
    before = path.read_bytes()
    loaded = store.load_project(created.project_id)
    assert (loaded.sequence_context, loaded.incoming_state, loaded.intended_outgoing_state) == ("", "", "")
    assert path.read_bytes() == before
    assert store.list_projects()[0].project_id == created.project_id
    assert path.read_bytes() == before

    upgraded = store.save_project(created.project_id, loaded.workspace)
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert upgraded.project_id == created.project_id
    assert persisted["schemaVersion"] == 17
    assert persisted["sequence"]["continuityContext"] == ""
    assert persisted["sequence"]["generations"][0]["continuity"] == {
        "incomingState": "", "intendedOutgoingState": "",
    }


def test_continuity_save_as_copies_siblings_and_applies_current_overrides(
    tmp_path: Path, workspace_factory,
):
    store = ProjectStore(tmp_path)
    source = store.create_project(workspace_factory(creativeRequest="A"), sequence_context="Shared rule")
    first_id = source.active_generation_id
    source = store.create_generation(source.project_id)
    second_id = source.active_generation_id
    source = store.save_project(
        source.project_id,
        workspace_factory(creativeRequest="B"),
        incoming_state="B incoming", intended_outgoing_state="B outgoing",
    )
    store.activate_generation(source.project_id, first_id)
    before = store.load_project(source.project_id)
    fork = store.duplicate_project(
        source.project_id,
        "Fork",
        active_workspace=workspace_factory(creativeRequest="A current"),
        sequence_context="Current shared rule",
        active_incoming_state="A current incoming",
        active_intended_outgoing_state="A current outgoing",
    )
    assert fork.project_id != source.project_id
    assert fork.sequence_context == "Current shared rule"
    assert fork.incoming_state == "A current incoming"
    assert fork.intended_outgoing_state == "A current outgoing"
    fork_manifest = json.loads(_manifest_path(tmp_path, fork.project_id).read_text(encoding="utf-8"))
    sibling = next(item for item in fork_manifest["sequence"]["generations"] if item["generationId"] != fork.active_generation_id)
    assert sibling["continuity"] == {"incomingState": "B incoming", "intendedOutgoingState": "B outgoing"}
    assert store.load_project(source.project_id).sequence_context == before.sequence_context
    assert store.load_project(source.project_id).incoming_state == before.incoming_state
    assert second_id in [item.generation_id for item in store.load_project(source.project_id).sequence.generations]


def test_rename_and_active_save_preserve_generation_identity(tmp_path: Path, workspace_factory):
    store = ProjectStore(tmp_path)
    created = store.create_project(workspace_factory(projectName="Before"))
    generation_id = created.active_generation_id

    renamed = store.save_project(created.project_id, workspace_factory(projectName="After"))

    assert renamed.active_generation_id == generation_id
    assert store.load_project(created.project_id).active_generation_id == generation_id
    manifest = json.loads(_manifest_path(tmp_path, created.project_id).read_text(encoding="utf-8"))
    assert [item["generationId"] for item in manifest["sequence"]["generations"]] == [generation_id]


@pytest.mark.parametrize("mutation", [
    "duplicate",
    "invalid_generation",
    "missing_active",
    "invalid_active",
    "empty",
])
def test_v2_sequence_validation_rejects_malformed_state(tmp_path: Path, workspace_factory, mutation: str):
    store = ProjectStore(tmp_path)
    created = store.create_project(workspace_factory())
    path = _manifest_path(tmp_path, created.project_id)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    sequence = manifest["sequence"]
    if mutation == "duplicate":
        sequence["generations"].append(json.loads(json.dumps(sequence["generations"][0])))
    elif mutation == "invalid_generation":
        sequence["generations"][0]["generationId"] = "not-a-uuid"
    elif mutation == "missing_active":
        del sequence["activeGenerationId"]
    elif mutation == "invalid_active":
        sequence["activeGenerationId"] = str(uuid.uuid4())
    else:
        sequence["generations"] = []
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(MalformedProjectError):
        store.load_project(created.project_id)


def test_v2_save_preserves_manually_constructed_sibling_generations_and_scoped_assets(
    tmp_path: Path, workspace_factory, image_asset_factory,
):
    store = ProjectStore(tmp_path)
    created = store.create_project(workspace_factory(assets=[image_asset_factory()]))
    path = _manifest_path(tmp_path, created.project_id)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    first = manifest["sequence"]["generations"][0]
    second_id = str(uuid.uuid4())
    second = json.loads(json.dumps(first))
    second["generationId"] = second_id
    second["workspace"]["creativeRequest"] = "Sibling snapshot"
    second_payload = b"different image bytes"
    second_path = "assets/pictures/sibling-payload.png"
    (path.parent / second_path).write_bytes(second_payload)
    second["assetFiles"][0]["path"] = second_path
    manifest["sequence"]["generations"].append(second)
    path.write_text(json.dumps(manifest), encoding="utf-8")

    loaded = store.load_project(created.project_id)
    assert loaded.active_generation_id == first["generationId"]
    assert loaded.generation_count == 2
    assert loaded.workspace.assets[0].image.data_url == image_asset_factory().image.data_url

    store.save_project(created.project_id, workspace_factory(creativeRequest="Updated active"))
    saved_manifest = json.loads(path.read_text(encoding="utf-8"))
    saved_generations = saved_manifest["sequence"]["generations"]
    assert [item["generationId"] for item in saved_generations] == [first["generationId"], second_id]
    assert saved_generations[1]["workspace"]["creativeRequest"] == "Sibling snapshot"
    assert saved_generations[1]["assetFiles"][0]["path"] == second_path

    # Switching the active pointer in a fixture proves each generation resolves
    # its own mapping, including the same asset ID with different bytes.
    saved_manifest["sequence"]["activeGenerationId"] = second_id
    path.write_text(json.dumps(saved_manifest), encoding="utf-8")
    sibling = store.load_project(created.project_id)
    assert sibling.workspace.creative_request == "Sibling snapshot"
    assert sibling.workspace.assets[0].image.data_url == "data:image/png;base64,ZGlmZmVyZW50IGltYWdlIGJ5dGVz"


def test_identical_picture_contents_can_share_one_physical_blob_across_generations(
    tmp_path: Path, workspace_factory, image_asset_factory,
):
    store = ProjectStore(tmp_path)
    created = store.create_project(workspace_factory(assets=[image_asset_factory()]))
    path = _manifest_path(tmp_path, created.project_id)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    first = manifest["sequence"]["generations"][0]
    second = json.loads(json.dumps(first))
    second["generationId"] = str(uuid.uuid4())
    manifest["sequence"]["generations"].append(second)
    path.write_text(json.dumps(manifest), encoding="utf-8")

    loaded = store.load_project(created.project_id)
    assert loaded.generation_count == 2
    assert first["assetFiles"][0]["path"] == second["assetFiles"][0]["path"]
    assert len(list((path.parent / "assets" / "pictures").iterdir())) == 1


def test_schema_v1_loads_lists_deletes_and_migrates_on_next_save(
    tmp_path: Path, workspace_factory, image_asset_factory,
):
    store = ProjectStore(tmp_path)
    created = store.create_project(workspace_factory(projectName="Old Project", assets=[image_asset_factory()]))
    path = _manifest_path(tmp_path, created.project_id)
    v2_manifest = json.loads(path.read_text(encoding="utf-8"))
    v1_manifest = _downgrade_manifest_to_v1(v2_manifest)
    path.write_text(json.dumps(v1_manifest), encoding="utf-8")

    first_load = store.load_project(created.project_id)
    second_load = store.load_project(created.project_id)
    expected_id = str(uuid.uuid5(uuid.UUID(created.project_id), "initial-generation"))
    assert first_load.active_generation_id == expected_id
    assert second_load.active_generation_id == expected_id
    assert first_load.workspace.project_id == created.project_id
    assert first_load.workspace.assets[0].image.data_url == image_asset_factory().image.data_url
    assert store.list_projects()[0].project_id == created.project_id
    assert json.loads(path.read_text(encoding="utf-8"))["schemaVersion"] == 1

    saved = store.save_project(created.project_id, first_load.workspace)
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert saved.active_generation_id == expected_id
    assert persisted["schemaVersion"] == 17
    assert persisted["projectId"] == created.project_id
    assert persisted["name"] == "Old Project"
    assert persisted["createdAt"] == v1_manifest["createdAt"]
    assert persisted["sequence"]["generations"][0]["generationId"] == expected_id
    assert ";base64," not in path.read_text(encoding="utf-8")
    assert store.load_project(created.project_id).workspace.assets[0].image.data_url == image_asset_factory().image.data_url

    store.delete_project(created.project_id)
    assert not path.parent.exists()


def test_listing_is_structural_only_but_load_still_validates_workspaces(
    tmp_path: Path, workspace_factory, monkeypatch,
):
    store = ProjectStore(tmp_path)
    created = store.create_project(workspace_factory())
    calls = []
    original = store._validate_persisted_workspace

    def record_validation(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "_validate_persisted_workspace", record_validation)
    monkeypatch.setattr(Path, "read_bytes", lambda *_args, **_kwargs: pytest.fail("listing read image bytes"))

    assert store.list_projects()[0].project_id == created.project_id
    assert calls == []

    # A full read still validates the persisted Generation Workspace.
    store.load_project(created.project_id)
    assert calls == [True]


def test_malformed_raw_image_asset_id_is_a_malformed_project_error(
    tmp_path: Path, workspace_factory, image_asset_factory,
):
    store = ProjectStore(tmp_path)
    created = store.create_project(workspace_factory(assets=[image_asset_factory()]))
    path = _manifest_path(tmp_path, created.project_id)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["sequence"]["generations"][0]["workspace"]["assets"][0]["id"] = []
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(MalformedProjectError, match="invalid asset ID"):
        store.load_project(created.project_id)


def test_sequence_lifecycle_preserves_identity_and_returns_summary(tmp_path: Path, workspace_factory, image_asset_factory):
    store = ProjectStore(tmp_path)
    created = store.create_project(workspace_factory(creativeRequest="Original", assets=[image_asset_factory()]))
    first_id = created.active_generation_id
    first_blob = created.workspace.assets[0].image.data_url

    appended = store.create_generation(created.project_id)
    blank_id = appended.active_generation_id
    inserted_before = store.create_generation(created.project_id, insert_index=1)
    inserted_id = inserted_before.active_generation_id
    assert [item.generation_id for item in inserted_before.sequence.generations] == [first_id, inserted_id, blank_id]
    assert inserted_before.workspace.creative_request == ""
    assert uuid.UUID(inserted_id).version == 4

    activated = store.activate_generation(created.project_id, first_id)
    duplicated = store.duplicate_generation(created.project_id, first_id)
    duplicate_id = duplicated.active_generation_id
    assert duplicate_id != first_id
    assert [item.generation_id for item in duplicated.sequence.generations][:2] == [first_id, duplicate_id]
    assert duplicated.workspace.creative_request == "Original"
    assert duplicated.workspace.assets[0].image.data_url == first_blob
    assert len(list((tmp_path / f"project-{created.project_id}" / "assets" / "pictures").iterdir())) == 1
    assert activated.active_generation_id == first_id


def test_generation_activation_rejects_an_outdated_project_snapshot(tmp_path: Path, workspace_factory):
    from miniconstruct.project_store import StaleProjectSnapshotError

    store = ProjectStore(tmp_path)
    created = store.create_project(workspace_factory())
    initial_fingerprint = created.project_fingerprint
    newer = store.create_generation(created.project_id)
    with pytest.raises(StaleProjectSnapshotError, match="Project changed since loading"):
        store.activate_generation(created.project_id, created.sequence.generations[0].generation_id,
                                  expected_project_fingerprint=initial_fingerprint)
    assert store.load_project(created.project_id).active_generation_id == newer.active_generation_id


def test_sequence_reorder_delete_and_final_guard(tmp_path: Path, workspace_factory):
    store = ProjectStore(tmp_path)
    created = store.create_project(workspace_factory(creativeRequest="One"))
    first = created.active_generation_id
    second = store.create_generation(created.project_id).active_generation_id
    third = store.create_generation(created.project_id).active_generation_id
    store.activate_generation(created.project_id, second)
    reordered = store.reorder_generations(created.project_id, [third, first, second])
    assert reordered.active_generation_id == second
    deleted = store.delete_generation(created.project_id, second)
    assert deleted.active_generation_id == first
    assert [item.generation_id for item in deleted.sequence.generations] == [third, first]
    with pytest.raises(GenerationNotActiveError):
        store.delete_generation(created.project_id, third)
    store.activate_generation(created.project_id, first)
    remaining = store.delete_generation(created.project_id, first)
    with pytest.raises(FinalGenerationDeletionError):
        store.delete_generation(remaining.project_id, remaining.active_generation_id)
    with pytest.raises(InvalidGenerationOrderError):
        store.reorder_generations(created.project_id, [remaining.active_generation_id, remaining.active_generation_id])
    with pytest.raises(InvalidGenerationInsertionError):
        store.create_generation(created.project_id, insert_index=99)


def test_save_as_clones_sequence_with_new_project_and_generation_ids(tmp_path: Path, workspace_factory):
    store = ProjectStore(tmp_path)
    created = store.create_project(workspace_factory(creativeRequest="One"))
    first = created.active_generation_id
    second = store.create_generation(created.project_id).active_generation_id
    store.activate_generation(created.project_id, first)
    copied = store.duplicate_project(created.project_id, "Copied")
    assert copied.project_id != created.project_id
    assert copied.name == "Copied"
    original_ids = [item.generation_id for item in store.load_project(created.project_id).sequence.generations]
    copied_ids = [item.generation_id for item in copied.sequence.generations]
    assert len(copied_ids) == len(original_ids)
    assert not set(original_ids) & set(copied_ids)
    assert copied.active_generation_id == copied_ids[0]
    assert copied.workspace.creative_request == "One"
    assert store.load_project(created.project_id).active_generation_id == first
    assert second in original_ids


def test_save_as_fork_uses_current_active_override_without_changing_source(
    tmp_path: Path, workspace_factory, image_asset_factory,
):
    store = ProjectStore(tmp_path)
    source = store.create_project(workspace_factory(creativeRequest="A", assets=[image_asset_factory("asset-a")]))
    generation_a = source.active_generation_id
    source = store.create_generation(
        source.project_id,
        workspace=workspace_factory(creativeRequest="B committed", assets=[image_asset_factory("asset-b")]),
    )
    generation_b = source.active_generation_id
    source = store.create_generation(
        source.project_id,
        workspace=workspace_factory(creativeRequest="C", assets=[image_asset_factory("asset-c")]),
    )
    source = store.activate_generation(source.project_id, generation_b)
    source_manifest_path = _manifest_path(tmp_path, source.project_id)
    before_manifest = source_manifest_path.read_bytes()
    override_asset = image_asset_factory("asset-b", filename="override.png").model_copy(
        update={"image": ImagePayload(data_url="data:image/png;base64,AQ==", width=1, height=1)}
    )
    override = workspace_factory(creativeRequest="B edited in browser", assets=[override_asset])

    fork = store.duplicate_project(source.project_id, "Fork", active_workspace=override)
    assert fork.project_id != source.project_id
    assert fork.name == "Fork"
    assert fork.active_generation_id == fork.sequence.generations[1].generation_id
    assert fork.workspace.creative_request == "B edited in browser"
    assert fork.workspace.assets[0].image.data_url == "data:image/png;base64,AQ=="
    assert [item.generation_id for item in fork.sequence.generations] != [generation_a, generation_b, source.sequence.generations[2].generation_id]
    assert [item.created_at for item in fork.sequence.generations] == [item.updated_at for item in fork.sequence.generations]
    assert store.load_project(source.project_id).workspace.creative_request == "B committed"
    assert source_manifest_path.read_bytes() == before_manifest
    assert store.load_project(fork.project_id).sequence.generations[2].generation_id
