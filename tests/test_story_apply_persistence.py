"""Offline regressions for useful Story continuations and canonical Project ideas."""
from copy import deepcopy
import json
import shutil
from uuid import UUID
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient

from miniconstruct.app import app
from miniconstruct.llm.client import OpenAICompatibleClient
from miniconstruct.models.workspace import validate_authoring_workspace
from miniconstruct.project_store import ProjectStore, ProjectStoreError, StaleProjectSnapshotError
from miniconstruct.story_planning import build_story_plan, initial_story_draft
from test_story_production_boundary import dog, compact, identities, files, PROJECT_ID


IDEA = "  A cyclist finds a lost dog.\n\n Rain, trust, and a reunion.  "


@pytest.mark.parametrize("selective", [False, True])
async def test_compact_dogtest_apply_materializes_only_planned_setup(dog, selective):
    store, request, raw = dog
    source = store.load_project(PROJECT_ID)
    workspace = source.workspace.model_dump(mode="json", by_alias=True)
    unrelated = deepcopy(workspace["assets"][0])
    unrelated.update(id="unrelated", role="environment", notes="Never selected")
    workspace["assets"].append(unrelated)
    workspace["subjects"].append({"id": "unrelated-subject", "number": 3})
    store.save_project(PROJECT_ID, validate_authoring_workspace(workspace), story_idea=IDEA)
    before = store.read_story_snapshot(PROJECT_ID)
    disk = files(store)
    response = compact(raw)
    for item in response["items"]:
        for key in ("creativeRequest", "incomingState", "intendedOutgoingState", "cameraIntent"):
            item[key] = item[key] or "Synthetic planned authoring text"
    if selective:
        response["items"][2]["referenceRecommendations"] = response["items"][2]["referenceRecommendations"][1:]
        response["items"][3]["referenceRecommendations"] = []
    def endpoint(request):
        context = json.loads(json.loads(request.content)["messages"][1]["content"][0]["text"])
        assert all(k not in context["draft"] for k in ("baseProjectId", "baseProjectUpdatedAt", "baseSequenceFingerprint"))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(response)}}]})
    ids = identities()
    draft = await build_story_plan(store, PROJECT_ID, request,
        lambda settings: OpenAICompatibleClient(settings, httpx.MockTransport(endpoint)),
        item_id_factory=lambda: next(ids))
    applied = store.apply_story_plan(PROJECT_ID, draft)
    after = store.read_story_snapshot(PROJECT_ID)
    assert applied.story_idea == IDEA
    assert applied.active_generation_id == before.generations[0].generation_id
    original = before.generations[0].workspace.model_dump()
    original["creative_request"] = draft.items[0].creative_request
    assert after.generations[0].workspace.model_dump() == original
    source_assets = before.generations[0].workspace.assets
    for index, target in enumerate(after.generations[1:], 1):
        assert target.workspace.mode == "Ref2VA"
        expected = [next(a for a in source_assets if a.id == r.asset_id) for r in draft.items[index].reference_recommendations]
        assert len(target.workspace.assets) == len(expected)
        assert len({a.id for a in target.workspace.assets}) == len(expected)
        for asset, authority in zip(target.workspace.assets, expected):
            assert UUID(asset.id).version == 5 and asset.id != authority.id
            assert asset.model_dump(exclude={"id"}) == authority.model_dump(exclude={"id"})
        assert {s.id for s in target.workspace.subjects} == {a.subject_identity.subject_id for a in expected}
        assert target.workspace.dialogue == "" and target.workspace.duration_seconds == 6
    assert {p: b for p, b in files(store).items() if p.name != "project.json"} == {p: b for p, b in disk.items() if p.name != "project.json"}
    manifest = after.manifest.model_dump(by_alias=True)
    source_paths = {a.path for a in after.manifest.sequence.generations[0].asset_files}
    assert all(a.path in source_paths for g in after.manifest.sequence.generations[1:] for a in g.asset_files)
    assert not any(k in json.dumps(manifest) for k in ("planItemId", "referenceRecommendations", "consistencyWarnings"))


@pytest.mark.parametrize("mode", ["Ref2VA", "I2VA", "FL2VA", "L2VA", "T2VA"])
def test_mode_inherits_immediate_predecessor_only(dog, mode):
    store, _, _ = dog
    second = store.create_generation(PROJECT_ID)
    data = second.workspace.model_dump(mode="json", by_alias=True)
    data.update(mode=mode, creativeRequest="Previous prompt", dialogue="Do not copy", durationSeconds=12)
    store.save_project(PROJECT_ID, validate_authoring_workspace(data))
    before = store.read_story_snapshot(PROJECT_ID)
    draft = initial_story_draft(before, 4)
    store.apply_story_plan(PROJECT_ID, draft)
    after = store.read_story_snapshot(PROJECT_ID)
    assert [g.workspace for g in after.generations[:2]] == [g.workspace for g in before.generations]
    for g in after.generations[2:]:
        assert g.workspace.mode == mode
        assert not g.workspace.assets and not g.workspace.subjects
        assert g.workspace.creative_request == g.workspace.dialogue == ""
        assert g.workspace.duration_seconds == 6


@pytest.mark.parametrize("conflict", ["id", "number"])
def test_conflicting_subject_authority_fails_without_any_write(dog, conflict):
    store, _, _ = dog
    source = store.load_project(PROJECT_ID)
    store.duplicate_generation(PROJECT_ID)
    second = store.load_project(PROJECT_ID)
    data = second.workspace.model_dump(mode="json", by_alias=True)
    data["assets"] = data["assets"][:1]
    data["subjects"] = [{"id": "subject-1" if conflict == "id" else "other-id", "number": 9 if conflict == "id" else 1}]
    data["assets"][0]["subjectIdentity"]["subjectId"] = data["subjects"][0]["id"]
    store.save_project(PROJECT_ID, validate_authoring_workspace(data))
    snapshot = store.read_story_snapshot(PROJECT_ID)
    draft = initial_story_draft(snapshot, 4)
    from miniconstruct.models.story_planning import StoryReferenceRecommendation
    draft.items[2].reference_recommendations = [StoryReferenceRecommendation(
        sourceGenerationId=g.generation_id, assetId=g.workspace.assets[0].id, recommendation="Use") for g in snapshot.generations]
    disk = files(store)
    with pytest.raises(ProjectStoreError, match="conflicting Subject"):
        store.apply_story_plan(PROJECT_ID, draft)
    assert files(store) == disk


def test_story_idea_lifecycle_and_zip_backup(dog, tmp_path):
    store, _, _ = dog
    project = store.load_project(PROJECT_ID)
    assert project.story_idea == ""
    old = store.read_story_snapshot(PROJECT_ID)
    project = store.save_project(PROJECT_ID, project.workspace, story_idea=IDEA)
    assert ProjectStore(store.root).load_project(PROJECT_ID).story_idea == IDEA
    assert store.read_story_snapshot(PROJECT_ID).fingerprint != old.fingerprint
    with pytest.raises(StaleProjectSnapshotError):
        store.apply_story_plan(PROJECT_ID, initial_story_draft(old, 4))
    assert store.duplicate_project(PROJECT_ID, "Duplicate").story_idea == IDEA
    assert store.duplicate_project(PROJECT_ID, "Save As", active_workspace=project.workspace).story_idea == IDEA
    added = store.create_generation(PROJECT_ID)
    assert added.story_idea == IDEA
    ids = [g.generation_id for g in added.sequence.generations]
    assert store.reorder_generations(PROJECT_ID, ids[::-1]).story_idea == IDEA
    assert store.delete_generation(PROJECT_ID, ids[-1]).story_idea == IDEA
    assert store.apply_story_plan(PROJECT_ID, initial_story_draft(store.read_story_snapshot(PROJECT_ID), 4)).story_idea == IDEA
    archive = shutil.make_archive(str(tmp_path / "backup"), "zip", store.root, f"project-{PROJECT_ID}")
    restored = tmp_path / "restored"
    with zipfile.ZipFile(archive) as zipped:
        zipped.extractall(restored)
    assert ProjectStore(restored).load_project(PROJECT_ID).story_idea == IDEA


@pytest.mark.parametrize("version", [1, 2, 3, 4])
def test_old_schemas_default_blank_then_write_v5(dog, version):
    store, _, _ = dog
    path = store.root / f"project-{PROJECT_ID}" / "project.json"
    raw = json.loads(path.read_text())
    raw.pop("storyIdea")
    raw["schemaVersion"] = version
    if version == 1:
        g = raw.pop("sequence")["generations"][0]
        raw.update(workspace=g["workspace"], assetFiles=g["assetFiles"])
    elif version < 4:
        for g in raw["sequence"]["generations"]:
            g.pop("planning")
            if version == 2:
                g.pop("continuity")
        if version == 2:
            raw["sequence"].pop("continuityContext")
    path.write_text(json.dumps(raw))
    disk = path.read_bytes()
    project = store.load_project(PROJECT_ID)
    assert project.story_idea == "" and path.read_bytes() == disk
    store.save_project(PROJECT_ID, project.workspace, story_idea=IDEA)
    assert json.loads(path.read_text())["schemaVersion"] == 5
    assert store.load_project(PROJECT_ID).story_idea == IDEA


def test_story_idea_api_save_export_import_and_limits(dog, monkeypatch):
    store, _, _ = dog
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(store.root))
    with TestClient(app) as client:
        workspace = client.get(f"/api/projects/{PROJECT_ID}").json()["workspace"]
        response = client.put(f"/api/projects/{PROJECT_ID}", json={"workspace": workspace, "storyIdea": IDEA})
        assert response.status_code == 200 and response.json()["storyIdea"] == IDEA
        assert client.get(f"/api/projects/{PROJECT_ID}").json()["storyIdea"] == IDEA
        envelope = {"format": "MiniConstruct Project", "schemaVersion": 1, "workspace": workspace, "storyIdea": IDEA}
        exported = client.post("/api/validate-project", json=envelope)
        assert exported.status_code == 200
        imported = client.post("/api/projects", json={k: exported.json()[k] for k in ("workspace", "storyIdea")})
        assert imported.status_code == 201 and imported.json()["storyIdea"] == IDEA
        fork = client.post(f"/api/projects/{PROJECT_ID}/save-as", json={"name": "Fork", "storyIdea": IDEA + "edited"})
        assert fork.status_code == 201 and fork.json()["storyIdea"] == IDEA + "edited"
        assert client.put(f"/api/projects/{PROJECT_ID}", json={"workspace": workspace, "storyIdea": "x" * 20001}).status_code == 422


def test_comparison_and_metadata_only_references_copy_required_authority(dog):
    store, _, _ = dog
    project = store.load_project(PROJECT_ID)
    data = project.workspace.model_dump(mode="json", by_alias=True)
    comparison = deepcopy(data["assets"][0])
    comparison.update(id="comparison", role="character_comparison_scale", comparisonSubjects=["subject-1", "subject-2"],
                      notes="Keep relative scale", options={"authored": "exact"})
    media = [{"id": kind, "kind": kind, "filename": f"reference.{kind}", "mimeType": f"{kind}/example",
              "role": role, "notes": "User description only", "attached": False, "durationSeconds": 3}
             for kind, role in [("audio", "general_audio"), ("video", "general_video")]]
    data["assets"] = [comparison, *media]
    data["subjects"].append({"id": "unrelated", "number": 3})
    store.save_project(PROJECT_ID, validate_authoring_workspace(data))
    snapshot = store.read_story_snapshot(PROJECT_ID)
    draft = initial_story_draft(snapshot, 2)
    from miniconstruct.models.story_planning import StoryReferenceRecommendation
    draft.items[1].reference_recommendations = [StoryReferenceRecommendation(
        sourceGenerationId=project.active_generation_id, assetId=a.id, recommendation="Use")
        for a in snapshot.generations[0].workspace.assets]
    applied = store.apply_story_plan(PROJECT_ID, draft)
    target = store.read_generation(PROJECT_ID, applied.sequence.generations[1].generation_id).workspace
    assert {s.id for s in target.subjects} == {"subject-1", "subject-2"}
    for asset, source in zip(target.assets, snapshot.generations[0].workspace.assets):
        assert asset.model_dump(exclude={"id"}) == source.model_dump(exclude={"id"})


@pytest.mark.parametrize("change", ["asset", "subject", "missing_blob"])
def test_source_changes_and_missing_storage_never_partially_apply(dog, change):
    store, _, _ = dog
    snapshot = store.read_story_snapshot(PROJECT_ID)
    draft = initial_story_draft(snapshot, 4)
    from miniconstruct.models.story_planning import StoryReferenceRecommendation
    draft.items[1].reference_recommendations = [StoryReferenceRecommendation(
        sourceGenerationId=snapshot.generations[0].generation_id,
        assetId=snapshot.generations[0].workspace.assets[0].id, recommendation="Use")]
    path = store.root / f"project-{PROJECT_ID}" / "project.json"
    raw = json.loads(path.read_text())
    if change == "missing_blob":
        (path.parent / raw["sequence"]["generations"][0]["assetFiles"][0]["path"]).unlink()
    else:
        workspace = raw["sequence"]["generations"][0]["workspace"]
        if change == "asset":
            workspace["assets"][0]["notes"] = "Changed authority"
        else:
            workspace["subjects"][0]["number"] = 9
        path.write_text(json.dumps(raw))  # Deliberately retain updatedAt.
    disk = files(store)
    with pytest.raises(ProjectStoreError):
        store.apply_story_plan(PROJECT_ID, draft)
    assert files(store) == disk
