"""Sequence identity authority stays separate from local reference participation."""
from copy import deepcopy
import json

import pytest
from fastapi.testclient import TestClient

from miniconstruct.app import app
from miniconstruct.execution_authoring import execution_authoring_fingerprint
from miniconstruct.execution_routing import effective_references
from miniconstruct.h3.builder import (
    _reference_semantics, assemble_prompt, build_h3_input_map, build_reference_manifest,
)
from miniconstruct.h3.vision_input import prompt_writer_image
from miniconstruct.models.story_planning import StoryReferenceRecommendation
from miniconstruct.models.workspace import structural_subject_ids, validate_authoring_workspace
from miniconstruct.project_store import ProjectStore, ProjectStoreError, SequenceSubjectInUseError, StaleProjectSnapshotError
from miniconstruct.story_planning import initial_story_draft
from test_story_production_boundary import dog, files, PROJECT_ID


CAST = [{"id": "subject-1", "number": 1}, {"id": "subject-2", "number": 2}]


def records(subjects):
    return [s.model_dump(include={"id", "number"}) for s in subjects]


def canonical_records(records):
    return [{**record, "narrativeName": None, "narrativeAliases": [],
             "characterProfile": {"roleBackground": "", "personality": "", "appearance": "",
                                  "speechMannerisms": "", "notes": ""}} for record in records]


def legacy(store, change=None):
    store.duplicate_generation(PROJECT_ID)
    path = store.root / f"project-{PROJECT_ID}" / "project.json"
    raw = json.loads(path.read_text())
    raw["sequence"].pop("storyMemory", None)
    for legacy_generation in raw["sequence"]["generations"]:
        legacy_generation.pop("storyMemoryAfter", None)
        legacy_generation.pop("storyMemoryStatus", None)
    raw["schemaVersion"] = 5
    raw["sequence"].pop("subjects")
    raw["sequence"].pop("nextSubjectNumber")
    if change:
        change(raw)
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def test_schema5_exact_mappings_merge_read_only_then_persist_safe_watermark(dog):
    store, _, _ = dog
    path = legacy(store, lambda raw: raw["sequence"]["generations"][1]["workspace"].update(nextSubjectNumber=9))
    disk = files(store)
    loaded = ProjectStore(store.root).load_project(PROJECT_ID)
    assert records(loaded.sequence.subjects) == CAST
    assert loaded.sequence.next_subject_number == 9
    assert files(store) == disk
    store.save_project(PROJECT_ID, loaded.workspace)
    raw = json.loads(path.read_text())
    assert raw["schemaVersion"] == 17 and raw["sequence"]["nextSubjectNumber"] == 9
    assert raw["sequence"]["subjects"] == canonical_records(CAST)
    assert raw["sequence"]["generations"][0]["workspace"]["schemaVersion"] == 1
    assert records(ProjectStore(store.root).load_project(PROJECT_ID).sequence.subjects) == CAST


@pytest.mark.parametrize("conflict", ["id", "number"])
def test_ambiguous_schema5_migration_rejects_without_changing_any_file(dog, conflict):
    store, _, _ = dog
    workspace = store.load_project(PROJECT_ID).workspace
    def change(raw):
        local = raw["sequence"]["generations"][1]["workspace"]
        if conflict == "id":
            local["subjects"][0]["number"] = 7
        else:
            local["subjects"][0]["id"] = "independent-character"
            local["assets"][0]["subjectIdentity"]["subjectId"] = "independent-character"
    legacy(store, change)
    disk = files(store)
    for action in (lambda: store.load_project(PROJECT_ID),
                   lambda: store.save_project(PROJECT_ID, workspace),
                   lambda: store.create_generation(PROJECT_ID),
                   lambda: store.duplicate_project(PROJECT_ID, "Fork")):
        with pytest.raises(ProjectStoreError, match="Sequence cast conflict"):
            action()
        assert files(store) == disk


def test_legacy_implicit_records_materialize_only_their_own_subset(dog):
    store, _, _ = dog
    def change(raw):
        for generation in raw["sequence"]["generations"]:
            generation["workspace"]["subjects"] = []
            generation["workspace"]["assets"] = generation["workspace"]["assets"][:1]
            generation["assetFiles"] = generation["assetFiles"][:1]
    legacy(store, change)
    loaded = store.load_project(PROJECT_ID)
    assert records(loaded.sequence.subjects) == CAST[:1]
    store.create_generation(PROJECT_ID)
    fresh = ProjectStore(store.root).read_story_snapshot(PROJECT_ID)
    assert records(fresh.manifest.sequence.subjects) == CAST[:1]
    assert [records(g.workspace.subjects) for g in fresh.generations] == [CAST[:1], CAST[:1], []]


def selective_apply(store):
    snapshot = store.read_story_snapshot(PROJECT_ID)
    draft = initial_story_draft(snapshot, 2)
    draft.items[1].scene_plan = "The dog runs through the park."
    draft.items[1].reference_recommendations = [StoryReferenceRecommendation(
        sourceGenerationId=snapshot.generations[0].generation_id,
        assetId=snapshot.generations[0].workspace.assets[1].id, recommendation="Use the dog identity",
    )]
    project = store.apply_story_plan(PROJECT_ID, draft)
    return store.activate_generation(PROJECT_ID, project.sequence.generations[1].generation_id)


def test_selective_apply_reload_and_original_subject_reactivation(dog):
    store, _, _ = dog
    source = store.load_project(PROJECT_ID).workspace
    target = selective_apply(store)
    assert records(target.workspace.subjects) == CAST[1:]
    assert records(target.sequence.subjects) == CAST
    assert len(target.workspace.assets) == 1
    fresh = ProjectStore(store.root).load_project(PROJECT_ID)
    assert records(fresh.workspace.subjects) == CAST[1:]
    assert records(fresh.sequence.subjects) == CAST
    data = fresh.workspace.model_dump(by_alias=True)
    data["subjects"].append(CAST[0])
    data["assets"].append(source.assets[0].model_dump(by_alias=True))
    store.save_project(PROJECT_ID, validate_authoring_workspace(data))
    reloaded = ProjectStore(store.root).load_project(PROJECT_ID)
    assert records(reloaded.workspace.subjects) == CAST
    assert records(reloaded.sequence.subjects) == CAST
    assert reloaded.workspace.assets[1].subject_identity.subject_id == "subject-1"
    assert reloaded.sequence.next_subject_number == 3


def test_dormant_cast_does_not_change_prompt_vision_or_effective_execution_inputs(dog):
    store, _, _ = dog
    target = selective_apply(store)
    # This is the pre-change local-only Workspace, with no Project metadata.
    data = target.workspace.model_dump(by_alias=True)
    data.update(projectId=None, projectName="Standalone")
    standalone = validate_authoring_workspace(data)
    assert records(target.sequence.subjects) == CAST
    assert records(target.workspace.subjects) == CAST[1:]
    for build in (build_h3_input_map, build_reference_manifest, effective_references, execution_authoring_fingerprint):
        assert build(target.workspace) == build(standalone)
    for vision in (False, True):
        assert _reference_semantics(target.workspace, vision) == _reference_semantics(standalone, vision)
        assert "<Subject 1> is defined by" not in _reference_semantics(target.workspace, vision)
        assert assemble_prompt(target.workspace, vision).messages == assemble_prompt(standalone, vision).messages
    assert "subject-1" not in json.dumps(build_reference_manifest(target.workspace))
    assert "Subject 1" not in json.dumps(build_h3_input_map(target.workspace))
    images = [part for message in assemble_prompt(target.workspace, True).messages
              if isinstance(message["content"], list) for part in message["content"] if part.get("type") == "image_url"]
    assert len(images) == 1
    assert images[0]["image_url"]["url"] == prompt_writer_image(target.workspace.assets[0].image.data_url)[0]
    assert [ref.asset_id for ref in effective_references(target.workspace)] == [target.workspace.assets[0].id]


def test_unused_local_subjects_are_dematerialized_before_sequence_allocation(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path)
    cast = [*CAST, {"id": "subject-4", "number": 4}]
    project = store.create_project(workspace_factory(subjects=cast, nextSubjectNumber=5))
    project = store.create_generation(project.project_id)
    assert project.workspace.subjects == []
    data = project.workspace.model_dump(by_alias=True)
    data["nextSubjectNumber"] = 100  # Standalone counters cannot redefine Sequence authority.
    data["subjects"] = [CAST[1], {"id": "subject-3", "number": 3}]
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with pytest.raises(ProjectStoreError, match="expected Subject 5"):
        store.save_project(project.project_id, validate_authoring_workspace(data))
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before
    assert ProjectStore(tmp_path).load_project(project.project_id).workspace.subjects == []


def test_reverse_order_canonical_candidates_allocate_in_visible_number_order(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory(subjects=CAST, nextSubjectNumber=3))
    data = project.workspace.model_dump(by_alias=True)
    data["subjects"] = [
        {"id": "new-four", "number": 4},
        {"id": "new-three", "number": 3},
    ]
    saved = store.save_project(project.project_id, validate_authoring_workspace(data))
    assert records(saved.sequence.subjects) == [
        *CAST,
        {"id": "new-three", "number": 3},
        {"id": "new-four", "number": 4},
    ]
    assert saved.sequence.next_subject_number == 5


def test_canonical_candidate_gap_is_rejected(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory(subjects=CAST, nextSubjectNumber=3))
    data = project.workspace.model_dump(by_alias=True)
    data["subjects"] = [{"id": "new-four", "number": 4}]
    with pytest.raises(ProjectStoreError, match="expected Subject 3"):
        store.save_project(project.project_id, validate_authoring_workspace(data))


def test_blank_duplicate_delete_and_save_as_retain_cast_and_dematerialize_local_subjects(dog):
    store, _, _ = dog
    target = selective_apply(store)
    copied = store.duplicate_generation(PROJECT_ID)
    assert records(copied.workspace.subjects) == CAST[1:]
    assert records(copied.sequence.subjects) == CAST
    # Delete all references and then the only Generation with Subject 1.
    data = copied.workspace.model_dump(by_alias=True)
    data["assets"] = []
    saved = store.save_project(PROJECT_ID, validate_authoring_workspace(data))
    assert records(saved.workspace.subjects) == []
    store.activate_generation(PROJECT_ID, target.sequence.generations[0].generation_id)
    deleted = store.delete_generation(PROJECT_ID, target.sequence.generations[0].generation_id)
    assert records(deleted.sequence.subjects) == CAST
    blank = store.create_generation(PROJECT_ID)
    assert blank.workspace.subjects == [] and records(blank.sequence.subjects) == CAST
    fork = store.duplicate_project(PROJECT_ID, "Fork")
    assert fork.workspace.subjects == [] and records(fork.sequence.subjects) == CAST
    assert fork.sequence.next_subject_number == 3


def test_unrelated_cast_addition_changes_story_snapshot_but_not_generation_provenance(dog):
    store, _, _ = dog
    target = selective_apply(store)
    before = store.read_story_snapshot(PROJECT_ID)
    fingerprint = execution_authoring_fingerprint(target.workspace)
    sibling = store.activate_generation(PROJECT_ID, target.sequence.generations[0].generation_id)
    data = sibling.workspace.model_dump(by_alias=True)
    data["subjects"].append({"id": "dormant-third", "number": 3})
    store.save_project(PROJECT_ID, validate_authoring_workspace(data))
    after = store.read_story_snapshot(PROJECT_ID)
    assert after.fingerprint != before.fingerprint
    unchanged = store.read_generation(PROJECT_ID, target.active_generation_id)
    assert execution_authoring_fingerprint(unchanged.workspace) == fingerprint
    assert unchanged.workspace.model_dump() == target.workspace.model_dump()


def test_api_cast_context_and_standalone_import_conflict_contract(dog, monkeypatch):
    store, _, _ = dog
    selective_apply(store)
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(store.root))
    with TestClient(app) as client:
        body = client.get(f"/api/projects/{PROJECT_ID}").json()
        assert body["sequence"]["subjects"] == canonical_records(CAST)
        assert body["sequence"]["nextSubjectNumber"] == 3
        assert body["workspace"]["subjects"] == CAST[1:]
        assert str(store.root) not in json.dumps(body["sequence"])
        exported = {"format": "MiniConstruct Project", "schemaVersion": 1, "workspace": body["workspace"]}
        standalone = client.post("/api/validate-project", json=exported)
        assert standalone.status_code == 200 and "sequence" not in standalone.json()
        bad = deepcopy(body["workspace"])
        bad["subjects"].append({"id": "subject-1", "number": 3})
        disk = files(store)
        conflict = client.put(f"/api/projects/{PROJECT_ID}", json={"workspace": bad})
        assert conflict.status_code == 400 and "Sequence cast conflict" in conflict.json()["detail"]
        assert files(store) == disk
        good = deepcopy(body["workspace"])
        good["subjects"].append({"id": "imported-third", "number": 3})
        saved = client.put(f"/api/projects/{PROJECT_ID}", json={"workspace": good})
        assert saved.status_code == 200
        assert saved.json()["sequence"]["subjects"] == canonical_records([*CAST, {"id": "imported-third", "number": 3}])


def test_missing_local_association_cannot_be_silently_renumbered_on_project_save(dog):
    store, _, _ = dog
    source = store.load_project(PROJECT_ID).workspace
    target = selective_apply(store)
    data = target.workspace.model_dump(by_alias=True)
    data["assets"].append(source.assets[0].model_dump(by_alias=True))
    # Standalone normalization reproduces the original failure. Project save must
    # reject this divergent record; the Project-aware browser resolves it first.
    invalid = validate_authoring_workspace(data)
    assert next(s for s in invalid.subjects if s.id == "subject-1").number == 3
    disk = files(store)
    with pytest.raises(ProjectStoreError, match="Sequence cast conflict"):
        store.save_project(PROJECT_ID, invalid)
    assert files(store) == disk


def test_stale_unused_subject_proposals_do_not_allocate_sequence_identities(dog):
    store, _, _ = dog
    target = selective_apply(store)
    first = target.workspace.model_dump(by_alias=True)
    first["subjects"].append({"id": "first-new-identity", "number": 3})
    store.save_project(PROJECT_ID, validate_authoring_workspace(first))
    second = target.workspace.model_dump(by_alias=True)
    second["subjects"].append({"id": "second-new-identity", "number": 3})
    with pytest.raises(ProjectStoreError, match="different IDs"):
        ProjectStore(store.root).save_project(PROJECT_ID, validate_authoring_workspace(second))


def test_structural_subject_use_ignores_inert_metadata_and_counts_identity_and_comparison():
    assets = [
        {"kind": "image", "role": "subject_identity", "subjectIdentity": {"subjectId": "identity"}},
        {"kind": "image", "role": "character_comparison_scale", "comparisonSubjects": ["one", "two"]},
        {"kind": "image", "role": "environment", "subjectIdentity": {"subjectId": "inert-picture"}},
        {"kind": "audio", "role": "voice_timbre", "subjectIdentity": {"subjectId": "inert-audio"}},
        {"kind": "video", "role": "continuation_source", "subjectIdentity": {"subjectId": "inert-video"}},
    ]
    assert structural_subject_ids(assets) == {"identity", "one", "two"}


def test_final_project_use_dematerializes_local_subject_but_retains_sequence_and_h3_visibility(dog):
    store, _, _ = dog
    target = selective_apply(store)
    before_fingerprint = execution_authoring_fingerprint(target.workspace)
    data = target.workspace.model_dump(by_alias=True)
    data["assets"] = []
    saved = store.save_project(PROJECT_ID, validate_authoring_workspace(data))
    assert saved.workspace.subjects == []
    assert records(saved.sequence.subjects) == CAST
    assert build_reference_manifest(saved.workspace)["subjects"] == []
    assert "Subject 2" not in _reference_semantics(saved.workspace, True)
    standalone = validate_authoring_workspace(saved.workspace.model_dump(by_alias=True))
    assert assemble_prompt(saved.workspace, True).messages == assemble_prompt(standalone, True).messages
    assert execution_authoring_fingerprint(saved.workspace) != before_fingerprint
    reloaded = ProjectStore(store.root).load_project(PROJECT_ID)
    assert reloaded.workspace.subjects == [] and records(reloaded.sequence.subjects) == CAST


def test_comparison_participation_keeps_subject_local_until_its_last_use_is_removed(tmp_path, workspace_factory, image_asset_factory):
    identity = image_asset_factory("identity", "subject_identity")
    identity.subject_identity.subject_id = "subject-2"
    assets = [
        identity,
        image_asset_factory("comparison", "character_comparison_scale", order=1).model_copy(
            update={"comparison_subject_ids": ["subject-1", "subject-2"]}
        ),
    ]
    workspace = workspace_factory(mode="Ref2VA", subjects=CAST, nextSubjectNumber=3, assets=assets)
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace)
    data = project.workspace.model_dump(by_alias=True)
    data["assets"] = data["assets"][1:]
    saved = store.save_project(project.project_id, validate_authoring_workspace(data))
    assert records(saved.workspace.subjects) == CAST
    data = saved.workspace.model_dump(by_alias=True)
    data["assets"] = []
    saved = store.save_project(project.project_id, validate_authoring_workspace(data))
    assert saved.workspace.subjects == [] and records(saved.sequence.subjects) == CAST


def test_sequence_subject_delete_is_atomic_cleans_stale_locals_and_never_reuses_numbers(tmp_path, workspace_factory, image_asset_factory):
    cast = [*CAST, {"id": "subject-3", "number": 3}]
    one = image_asset_factory("one", "subject_identity")
    one.subject_identity.subject_id = "subject-1"
    three = image_asset_factory("three", "subject_identity", order=1)
    three.subject_identity.subject_id = "subject-3"
    assets = [one, three]
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory(mode="Ref2VA", subjects=cast, nextSubjectNumber=4, assets=assets))
    store.duplicate_generation(project.project_id)
    prior_snapshot = store.read_story_snapshot(project.project_id)
    prior_draft = initial_story_draft(prior_snapshot, 2)
    deleted = store.delete_sequence_subject(project.project_id, "subject-2")
    assert records(deleted.sequence.subjects) == [CAST[0], cast[2]]
    assert deleted.sequence.next_subject_number == 4
    with pytest.raises(StaleProjectSnapshotError):
        store.apply_story_plan(project.project_id, prior_draft)
    snapshot = store.read_story_snapshot(project.project_id)
    assert all("subject-2" not in {subject.id for subject in generation.workspace.subjects} for generation in snapshot.generations)
    data = deleted.workspace.model_dump(by_alias=True)
    data["subjects"].append({"id": "new-four", "number": 4})
    data["assets"][0]["subjectIdentity"]["subjectId"] = "new-four"
    created = store.save_project(project.project_id, validate_authoring_workspace(data))
    assert records(created.sequence.subjects) == [CAST[0], cast[2], {"id": "new-four", "number": 4}]
    assert created.sequence.next_subject_number == 5


def test_sequence_subject_delete_api_returns_refreshed_canonical_project(tmp_path, workspace_factory, monkeypatch):
    store = ProjectStore(tmp_path)
    opaque = {"id": "stable subject/id", "number": 1}
    project = store.create_project(workspace_factory(subjects=[opaque, CAST[1]], nextSubjectNumber=3))
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(store.root))
    with TestClient(app) as client:
        response = client.request(
            "DELETE", f"/api/projects/{project.project_id}/subjects", json={"subjectId": opaque["id"]}
        )
    assert response.status_code == 200
    assert response.json()["sequence"]["subjects"] == canonical_records([CAST[1]])
    assert response.json()["sequence"]["nextSubjectNumber"] == 3


def test_unused_canonical_subject_survives_save_and_story_apply_until_explicit_delete(tmp_path, workspace_factory):
    cast = [{"id": "subject-1", "number": 1}, {"id": "subject-2", "number": 2}, {"id": "subject-3", "number": 3}]
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory(subjects=cast, nextSubjectNumber=4))
    data = project.workspace.model_dump(by_alias=True)
    data["subjects"] = []
    saved = store.save_project(project.project_id, validate_authoring_workspace(data))
    assert saved.workspace.subjects == []
    assert records(saved.sequence.subjects) == cast
    snapshot = store.read_story_snapshot(project.project_id)
    draft = initial_story_draft(snapshot, 2)
    draft.items[0].scene_plan = "Keep the existing shot."
    applied = store.apply_story_plan(project.project_id, draft)
    assert records(applied.sequence.subjects) == cast
    assert applied.workspace.subjects == []
    deleted = store.delete_sequence_subject(project.project_id, "subject-3")
    assert records(deleted.sequence.subjects) == cast[:2]


def test_sequence_subject_delete_blocks_identity_or_comparison_use_without_writing(tmp_path, workspace_factory, image_asset_factory):
    identity = image_asset_factory("identity", "subject_identity")
    identity.subject_identity.subject_id = "subject-2"
    comparison = image_asset_factory("comparison", "character_comparison_scale", order=1).model_copy(
        update={"comparison_subject_ids": ["subject-1", "subject-2"]}
    )
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory(mode="Ref2VA", subjects=CAST, nextSubjectNumber=3, assets=[identity, comparison]))
    disk = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with pytest.raises(SequenceSubjectInUseError, match=r"Subject 2.*Generation 1"):
        store.delete_sequence_subject(project.project_id, "subject-2")
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == disk
    data = project.workspace.model_dump(by_alias=True)
    data["assets"] = data["assets"][1:]
    store.save_project(project.project_id, validate_authoring_workspace(data))
    with pytest.raises(SequenceSubjectInUseError, match=r"Subject 2.*Generation 1"):
        store.delete_sequence_subject(project.project_id, "subject-2")
