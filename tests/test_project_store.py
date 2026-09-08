from __future__ import annotations

import json
from pathlib import Path
import uuid

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
    assert manifest["schemaVersion"] == 5
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
    assert loaded.assets[0].image.data_url == "data:image/png;base64,AA=="


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


def test_blank_authoring_workspace_saves_and_forks_through_api(tmp_path: Path, monkeypatch, workspace_factory):
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(tmp_path))
    with TestClient(app) as client:
        created = client.post(
            "/api/projects", json={"workspace": workspace_factory().model_dump(mode="json", by_alias=True)}
        ).json()
        project_id = created["id"]
        blank = client.post(f"/api/projects/{project_id}/generations", json={}).json()
        assert blank["workspace"]["creativeRequest"] == ""
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
    assert manifest["schemaVersion"] == 5
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
    assert destination["schemaVersion"] == 5
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
    assert manifest["schemaVersion"] == 5
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
    assert persisted["schemaVersion"] == 5
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
    assert loaded.workspace.assets[0].image.data_url == "data:image/png;base64,AA=="

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
    assert first_load.workspace.assets[0].image.data_url == "data:image/png;base64,AA=="
    assert store.list_projects()[0].project_id == created.project_id
    assert json.loads(path.read_text(encoding="utf-8"))["schemaVersion"] == 1

    saved = store.save_project(created.project_id, first_load.workspace)
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert saved.active_generation_id == expected_id
    assert persisted["schemaVersion"] == 5
    assert persisted["projectId"] == created.project_id
    assert persisted["name"] == "Old Project"
    assert persisted["createdAt"] == v1_manifest["createdAt"]
    assert persisted["sequence"]["generations"][0]["generationId"] == expected_id
    assert ";base64," not in path.read_text(encoding="utf-8")
    assert store.load_project(created.project_id).workspace.assets[0].image.data_url == "data:image/png;base64,AA=="

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
