"""Production-path Character Profile foundation regressions."""
from __future__ import annotations

import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from miniconstruct.h3.builder import assemble_prompt
from miniconstruct.models.api import LLMSettings, ProjectEnvelope
from miniconstruct.models.sequence_subject import CharacterProfile, SequenceSubject, narrative_mapping
from miniconstruct.models.sequence_subject import CHARACTER_PROFILE_POLICY
from miniconstruct.models.story_operations import StoryInitialRangeAuthority, StoryNextResponse, StoryInsertRequest, StoryReplanRequest
from miniconstruct.models.story_operations import StoryNextRequest
from miniconstruct.models.story_planning import StoryRevisionRequest, story_revision_response_model
from miniconstruct.models.workspace import SubjectRecord
from miniconstruct.project_store import ProjectStore, StaleProjectSnapshotError
from miniconstruct.story_operations import (
    assemble_initial_range_prompt,
    bounded_insert_snapshot,
    bounded_replan_snapshot,
    plan_next,
    plan_insert,
    plan_replan,
)
from miniconstruct.story_planning import assemble_revision_prompt, assemble_story_prompt, initial_story_draft


LLM = LLMSettings.model_validate({"modelId": "offline", "endpoint": {
    "id": "offline", "displayName": "Offline", "baseUrl": "http://never.test/v1",
}})

ALICE_PROFILE = {
    "roleBackground": "A meticulous station archivist.",
    "personality": "Usually calm and methodical.",
    "appearance": "Silver-streaked braid and a charcoal coat.",
    "speechMannerisms": "Measured sentences; notices small details.",
    "notes": "Stable author guidance only.",
}


def profile_update(subject_id, profile=ALICE_PROFILE):
    return {"subjectId": subject_id, "characterProfile": profile}


def profile_for(project, subject_id):
    return next(subject for subject in project.sequence.subjects if subject.id == subject_id).character_profile


def create_profiled_story(tmp_path, workspace_factory, *, image=None):
    store = ProjectStore(tmp_path)
    assets = [image] if image else []
    workspace = workspace_factory(
        creativeRequest="A neutral opening.",
        subjects=[{"id": "alice", "number": 1}, {"id": "distant", "number": 2}],
        nextSubjectNumber=3,
        assets=assets,
    )
    project = store.create_project(workspace, story_idea="Alice returns to the station.")
    project = store.save_project(project.project_id, project.workspace, character_profiles=[
        profile_update("alice"),
        profile_update("distant", {"roleBackground": "An unrelated lighthouse keeper.", "personality": "Brisk.",
                                   "appearance": "Oilskin coat.", "speechMannerisms": "Short answers.", "notes": ""}),
    ])
    store.set_subject_narrative_name(project.project_id, "alice", "Alice")
    for _ in range(3):
        project = store.create_generation(project.project_id, workspace=workspace_factory(creativeRequest="Local scene."))
    return store, store.load_project(project.project_id)


def test_fresh_project_create_normalizes_workspace_subjects_before_profile_application(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path)
    workspace = workspace_factory(
        subjects=[{"id": "alice", "number": 1}, {"id": "distant", "number": 2}],
        nextSubjectNumber=3,
    )

    project = store.create_project(workspace, character_profiles=[])
    reloaded = store.load_project(project.project_id)

    assert all(isinstance(subject, SequenceSubject) for subject in project.sequence.subjects)
    assert [(subject.id, subject.number) for subject in project.sequence.subjects] == [("alice", 1), ("distant", 2)]
    assert all(profile_for(project, subject.id).is_blank() for subject in project.sequence.subjects)
    assert all(type(subject) is SubjectRecord for subject in project.workspace.subjects)
    assert all(isinstance(subject, SequenceSubject) for subject in reloaded.sequence.subjects)
    assert [(subject.id, subject.number) for subject in reloaded.sequence.subjects] == [("alice", 1), ("distant", 2)]


def test_fresh_project_create_persists_nonblank_character_profile_without_mutating_workspace(
    tmp_path, workspace_factory,
):
    store = ProjectStore(tmp_path)
    workspace = workspace_factory(subjects=[{"id": "alice", "number": 1}], nextSubjectNumber=2)

    project = store.create_project(workspace, character_profiles=[profile_update("alice")])
    reloaded = store.load_project(project.project_id)

    assert isinstance(project.sequence.subjects[0], SequenceSubject)
    assert profile_for(project, "alice").model_dump(by_alias=True) == ALICE_PROFILE
    assert profile_for(reloaded, "alice").model_dump(by_alias=True) == ALICE_PROFILE
    assert all(type(subject) is SubjectRecord for subject in project.workspace.subjects)


def test_fresh_project_create_accepts_empty_workspace_with_empty_character_profiles(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory(subjects=[]), character_profiles=[])

    assert not project.sequence.subjects
    assert not store.load_project(project.project_id).sequence.subjects


def test_api_first_save_accepts_empty_character_profiles_for_workspace_subjects(
    tmp_path, workspace_factory, monkeypatch,
):
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(tmp_path))
    workspace = workspace_factory(
        subjects=[{"id": "alice", "number": 1}],
        nextSubjectNumber=2,
    ).model_dump(mode="json", by_alias=True)

    from fastapi.testclient import TestClient
    from miniconstruct.app import app

    with TestClient(app) as client:
        response = client.post("/api/projects", json={"workspace": workspace, "characterProfiles": []})
        assert response.status_code == 201
        body = response.json()
        assert body["sequence"]["subjects"][0]["id"] == "alice"
        assert body["sequence"]["subjects"][0]["number"] == 1
        assert body["sequence"]["subjects"][0]["characterProfile"] == CharacterProfile().model_dump(by_alias=True)
        assert client.get(f"/api/projects/{body['id']}").status_code == 200


def test_blank_profile_model_and_length_limit():
    assert CharacterProfile().model_dump(by_alias=True) == {
        "roleBackground": "", "personality": "", "appearance": "", "speechMannerisms": "", "notes": "",
    }
    assert CharacterProfile(roleBackground="  preserved\nspacing  ").role_background == "  preserved\nspacing  "
    with pytest.raises(ValidationError):
        CharacterProfile(notes="x" * 4001)


@pytest.mark.asyncio
async def test_blank_profiles_add_no_story_policy_noise_and_nonblank_adds_one(tmp_path, workspace_factory):
    from test_story_next import OfflineNextClient

    blank_store = ProjectStore(tmp_path / "blank")
    blank = blank_store.create_project(workspace_factory(subjects=[{"id": "alice", "number": 1}], nextSubjectNumber=2))
    blank_snapshot = blank_store.read_story_snapshot(blank.project_id)
    blank_draft = initial_story_draft(blank_snapshot, 2)
    blank_revision = StoryRevisionRequest(
        draft=blank_draft, selectedPlanItemIds=[blank_draft.items[0].plan_item_id], scopes=["scene_plan"],
        instruction="Keep it simple.", llm=LLM,
    )
    assert CHARACTER_PROFILE_POLICY not in assemble_story_prompt(blank_snapshot, blank_draft, LLM)[0]["content"]
    assert CHARACTER_PROFILE_POLICY not in assemble_revision_prompt(blank_snapshot, blank_revision, story_revision_response_model(blank_revision.scopes))[0]["content"]
    next_client = OfflineNextClient()
    await plan_next(blank_store, blank.project_id, StoryNextRequest(
        anchorGenerationId=blank.active_generation_id, requestedCount=1, instruction="Continue.", llm=LLM,
    ), next_client)
    assert CHARACTER_PROFILE_POLICY not in next_client.calls[0][0]["content"]

    profiled_store, profiled = create_profiled_story(tmp_path / "profiled", workspace_factory)
    profiled_snapshot = profiled_store.read_story_snapshot(profiled.project_id)
    profiled_draft = initial_story_draft(profiled_snapshot, len(profiled_snapshot.generations))
    build_messages = assemble_story_prompt(profiled_snapshot, profiled_draft, LLM, idea="Alice returns.")
    assert build_messages[0]["content"].count(CHARACTER_PROFILE_POLICY) == 1
    profiled_revision = StoryRevisionRequest(
        draft=profiled_draft, selectedPlanItemIds=[profiled_draft.items[-1].plan_item_id], scopes=["scene_plan"],
        instruction="Keep Alice consistent.", llm=LLM,
    )
    revision_messages = assemble_revision_prompt(
        profiled_snapshot, profiled_revision, story_revision_response_model(profiled_revision.scopes),
    )
    assert revision_messages[0]["content"].count(CHARACTER_PROFILE_POLICY) == 1


def test_v11_migration_defaults_profiles_without_rewriting_legacy_file(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory(subjects=[{"id": "alice", "number": 1}], nextSubjectNumber=2))
    path = store.root / f"project-{project.project_id}" / "project.json"
    legacy = json.loads(path.read_text())
    legacy["schemaVersion"] = 11
    for subject in legacy["sequence"]["subjects"]:
        subject.pop("characterProfile")
    path.write_text(json.dumps(legacy), encoding="utf-8")
    before = path.read_bytes()
    loaded = store.load_project(project.project_id)
    assert path.read_bytes() == before and profile_for(loaded, "alice").is_blank()
    store.save_project(project.project_id, loaded.workspace)
    upgraded = json.loads(path.read_text())
    assert upgraded["schemaVersion"] == 17
    assert upgraded["sequence"]["subjects"][0]["characterProfile"] == CharacterProfile().model_dump(by_alias=True)


def test_profile_survives_save_fork_export_import_switch_and_dormancy(tmp_path, workspace_factory):
    store, project = create_profiled_story(tmp_path, workspace_factory)
    assert profile_for(project, "alice").model_dump(by_alias=True) == ALICE_PROFILE
    switched = store.activate_generation(project.project_id, project.sequence.generations[1].generation_id)
    assert profile_for(switched, "alice").model_dump(by_alias=True) == ALICE_PROFILE
    fork = store.duplicate_project(project.project_id, "Profile fork")
    assert profile_for(fork, "alice").model_dump(by_alias=True) == ALICE_PROFILE

    envelope = ProjectEnvelope.model_validate({
        "format": "MiniConstruct Project", "schemaVersion": 1,
        "workspace": switched.workspace.model_dump(mode="json", by_alias=True),
        "sequenceSubjects": [subject.model_dump(mode="json", by_alias=True) for subject in switched.sequence.subjects],
        "sequenceNextSubjectNumber": switched.sequence.next_subject_number,
    })
    imported_store = ProjectStore(tmp_path / "imported")
    imported = imported_store.create_project(
        envelope.workspace,
        sequence_subjects=envelope.sequence_subjects,
        sequence_next_subject_number=envelope.sequence_next_subject_number,
    )
    assert profile_for(imported, "alice").model_dump(by_alias=True) == ALICE_PROFILE
    # The original active Generation does not locally materialize Alice, but
    # canonical profile ownership remains intact across that dormant cycle.
    assert not switched.workspace.subjects and not profile_for(switched, "alice").is_blank()


def test_portable_sequence_watermark_preserves_deleted_numbers(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path / "source")
    project = store.create_project(workspace_factory(
        subjects=[{"id": f"subject-{number}", "number": number} for number in range(1, 9)],
        nextSubjectNumber=9,
    ))
    for number in range(4, 9):
        project = store.delete_sequence_subject(project.project_id, f"subject-{number}")
    assert project.sequence.next_subject_number == 9

    envelope = ProjectEnvelope.model_validate({
        "format": "MiniConstruct Project", "schemaVersion": 1,
        "workspace": project.workspace.model_dump(mode="json", by_alias=True),
        "sequenceSubjects": [subject.model_dump(mode="json", by_alias=True) for subject in project.sequence.subjects],
        "sequenceNextSubjectNumber": project.sequence.next_subject_number,
    })
    imported_store = ProjectStore(tmp_path / "imported")
    imported = imported_store.create_project(
        envelope.workspace,
        sequence_subjects=envelope.sequence_subjects,
        sequence_next_subject_number=envelope.sequence_next_subject_number,
    )
    assert imported.sequence.next_subject_number == 9
    assert [subject.number for subject in imported.sequence.subjects] == [1, 2, 3]

    next_workspace = imported.workspace.model_copy(update={
        "subjects": [SubjectRecord(id="subject-1", number=1), SubjectRecord(id="subject-2", number=2),
                     SubjectRecord(id="subject-3", number=3), SubjectRecord(id="subject-9", number=9)],
        "nextSubjectNumber": 10,
    })
    allocated = imported_store.save_project(imported.project_id, next_workspace)
    assert [subject.number for subject in allocated.sequence.subjects] == [1, 2, 3, 9]
    assert allocated.sequence.next_subject_number == 10

    # Older Workspace-only portable envelopes retain the ordinary import path.
    legacy_store = ProjectStore(tmp_path / "legacy")
    legacy = legacy_store.create_project(envelope.workspace)
    assert legacy.sequence.next_subject_number == envelope.workspace.next_subject_number


def test_portable_sequence_watermark_preserves_empty_canonical_cast(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path / "source")
    project = store.create_project(workspace_factory(
        subjects=[{"id": f"subject-{number}", "number": number} for number in range(1, 9)],
        nextSubjectNumber=9,
    ))
    for number in range(1, 9):
        project = store.delete_sequence_subject(project.project_id, f"subject-{number}")
    assert not project.sequence.subjects
    assert project.sequence.next_subject_number == 9

    envelope = ProjectEnvelope.model_validate({
        "format": "MiniConstruct Project", "schemaVersion": 1,
        "workspace": project.workspace.model_dump(mode="json", by_alias=True),
        "sequenceSubjects": [],
        "sequenceNextSubjectNumber": project.sequence.next_subject_number,
    })
    imported_store = ProjectStore(tmp_path / "imported")
    imported = imported_store.create_project(
        envelope.workspace,
        sequence_subjects=envelope.sequence_subjects,
        sequence_next_subject_number=envelope.sequence_next_subject_number,
    )
    assert not imported.sequence.subjects
    assert imported.sequence.next_subject_number == 9

    next_workspace = imported.workspace.model_copy(update={
        "subjects": [SubjectRecord(id="subject-9", number=9)],
        "nextSubjectNumber": 10,
    })
    allocated = imported_store.save_project(imported.project_id, next_workspace)
    assert [subject.number for subject in allocated.sequence.subjects] == [9]
    assert allocated.sequence.next_subject_number == 10


def test_profile_follows_canonical_subject_deletion_only(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory(subjects=[{"id": "dormant", "number": 1}], nextSubjectNumber=2))
    project = store.save_project(project.project_id, project.workspace,
                                 character_profiles=[profile_update("dormant")])
    deleted = store.delete_sequence_subject(project.project_id, "dormant")
    assert not deleted.sequence.subjects


def test_story_context_selects_nonblank_relevant_profiles_and_keeps_memory_distinct(tmp_path, workspace_factory):
    store, project = create_profiled_story(tmp_path, workspace_factory)
    terminal = project.sequence.generations[-1].generation_id
    next_snapshot = store.read_story_operation_snapshot(project.project_id, terminal, "Alice returns after years away.")
    cast = next_snapshot.context["sequenceNarrativeCast"]
    assert [entry["subjectId"] for entry in cast] == ["alice"]
    assert cast[0]["characterProfile"] == ALICE_PROFILE
    assert "distant" not in json.dumps(next_snapshot.context)
    assert next_snapshot.context["storyMemory"] != ALICE_PROFILE

    ids = [item.generation_id for item in project.sequence.generations]
    inserted = bounded_insert_snapshot(store, project.project_id, ids[1], "Alice bridges the gap.")
    replanned = bounded_replan_snapshot(store, project.project_id, ids[1], ids[2], "Keep Alice consistent.")
    assert [entry["subjectId"] for entry in inserted.context["sequenceNarrativeCast"]] == ["alice"]
    assert [entry["subjectId"] for entry in replanned.context["sequenceNarrativeCast"]] == ["alice"]
    assert inserted.context["sequenceNarrativeCast"][0]["characterProfile"] == ALICE_PROFILE
    assert replanned.context["sequenceNarrativeCast"][0]["characterProfile"] == ALICE_PROFILE

    full = store.read_story_snapshot(project.project_id)
    draft = initial_story_draft(full, len(full.generations))
    revision = StoryRevisionRequest(
        draft=draft, selectedPlanItemIds=[draft.items[-1].plan_item_id], scopes=["scene_plan"],
        instruction="Bring Alice back.", llm=LLM,
    )
    message = assemble_revision_prompt(full, revision, story_revision_response_model(revision.scopes))
    revision_context = json.loads(message[1]["content"])
    assert [entry["subjectId"] for entry in revision_context["sequenceNarrativeCast"]] == ["alice"]
    assert revision_context["sequenceNarrativeCast"][0]["characterProfile"] == ALICE_PROFILE


@pytest.mark.asyncio
async def test_insert_and_replan_relevant_profile_edits_reject_stale_proposals(tmp_path, workspace_factory):
    from test_story_insert import OfflineInsertClient
    from test_story_replan import OfflineReplanClient

    store, project = create_profiled_story(tmp_path, workspace_factory)
    ids = [generation.generation_id for generation in project.sequence.generations]
    insert = await plan_insert(
        store, project.project_id,
        StoryInsertRequest(upstreamGenerationId=ids[1], requestedCount=1, instruction="Alice bridges the gap.", llm=LLM),
        OfflineInsertClient(),
    )
    store.save_project(project.project_id, project.workspace, character_profiles=[profile_update("alice", {
        **ALICE_PROFILE, "notes": "Changed after Insert proposal.",
    })])
    with pytest.raises(StaleProjectSnapshotError, match="Insert Between .*context changed"):
        store.apply_story_plan_insert(project.project_id, insert)

    project = store.load_project(project.project_id)
    replan = await plan_replan(
        store, project.project_id,
        StoryReplanRequest(startGenerationId=ids[1], endGenerationId=ids[2], instruction="Keep Alice consistent.", llm=LLM),
        OfflineReplanClient(),
    )
    store.save_project(project.project_id, project.workspace, character_profiles=[profile_update("alice", {
        **ALICE_PROFILE, "notes": "Changed after Replan proposal.",
    })])
    with pytest.raises(StaleProjectSnapshotError, match="Replan Range context changed before Apply"):
        store.apply_story_plan_replan(project.project_id, replan)


def test_build_profiles_and_subject_identity_picture_coexist_without_h3_profile_injection(tmp_path, workspace_factory, image_asset_factory):
    image = image_asset_factory(role="subject_identity").model_dump(by_alias=True)
    image["subjectIdentity"] = {"subjectId": "alice"}
    store, project = create_profiled_story(tmp_path, workspace_factory, image=image)
    single = ProjectStore(tmp_path / "initial")
    first = single.create_project(workspace_factory(subjects=[{"id": "alice", "number": 1}], nextSubjectNumber=2))
    first = single.save_project(first.project_id, first.workspace, character_profiles=[profile_update("alice")])
    snapshot = single.read_story_snapshot(first.project_id)
    authority = StoryInitialRangeAuthority(
        baseProjectId=first.project_id, projectFingerprint=snapshot.fingerprint,
        g1GenerationId=first.active_generation_id, sourceGenerationCount=1, requestedTotalCount=2,
        slotIds=[str(uuid4()), str(uuid4())], newFactIds=[str(uuid4()) for _ in range(16)],
        newThreadIds=[str(uuid4()) for _ in range(16)], allowedReferencePairs=[],
    )
    build_context = json.loads(assemble_initial_range_prompt(snapshot, authority, LLM)[1]["content"])
    assert build_context["sequenceNarrativeCast"][0]["characterProfile"] == ALICE_PROFILE

    later = store.read_story_operation_snapshot(project.project_id, project.sequence.generations[-1].generation_id,
                                                "Alice returns.")
    assert later.context["sequenceNarrativeCast"][0]["characterProfile"] == ALICE_PROFILE
    assert any("alice" in reference["subjectIds"] for reference in later.context["historicalReferenceContext"])

    subject = SequenceSubject(id="alice", number=1, narrativeName="Alice", characterProfile=ALICE_PROFILE)
    workspace = workspace_factory(subjects=[{"id": "alice", "number": 1}], nextSubjectNumber=2)
    h3 = assemble_prompt(workspace, False, [subject])
    assert "meticulous station archivist" not in h3.inspector_text
    assert "characterProfile" not in json.dumps(narrative_mapping([subject]))


def test_relevant_profile_edits_change_only_relevant_bounded_fingerprints(tmp_path, workspace_factory):
    store, project = create_profiled_story(tmp_path, workspace_factory)
    terminal = project.sequence.generations[-1].generation_id
    before = store.read_story_operation_snapshot(project.project_id, terminal, "Alice returns.")
    store.save_project(project.project_id, project.workspace, character_profiles=[profile_update("distant", {
        "roleBackground": "Changed distant role.", "personality": "Brisk.", "appearance": "Oilskin coat.",
        "speechMannerisms": "Short answers.", "notes": "",
    })])
    unrelated = store.read_story_operation_snapshot(project.project_id, terminal, "Alice returns.")
    assert unrelated.fingerprint == before.fingerprint
    store.save_project(project.project_id, project.workspace, character_profiles=[profile_update("alice", {
        **ALICE_PROFILE, "personality": "Still calm, now more decisive.",
    })])
    relevant = store.read_story_operation_snapshot(project.project_id, terminal, "Alice returns.")
    assert relevant.fingerprint != before.fingerprint


@pytest.mark.asyncio
async def test_profile_edits_stale_relevant_plan_next_but_not_an_unrelated_profile(tmp_path, workspace_factory):
    class OfflineClient:
        def __call__(self, _settings):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def generate(self, messages, *, response_instruction=None):
            context = json.loads(messages[1]["content"])
            return json.dumps({"slots": [{
                "planItemId": context["slotIds"][0], "scenePlan": "Alice steps inside.",
                "dialogue": "", "incomingState": "At the archive.", "intendedOutgoingState": "Inside.",
                "relationshipToPrevious": "new_shot_preserve_continuity", "cameraIntent": "Medium view.",
                "shotCount": 1,
                "referenceRecommendations": [], "memoryPatchAfter": {},
            }], "warnings": []})

    store, project = create_profiled_story(tmp_path, workspace_factory)
    request = StoryNextRequest(anchorGenerationId=project.sequence.generations[-1].generation_id,
                               requestedCount=1, instruction="Alice returns.", llm=LLM)
    proposal = await plan_next(store, project.project_id, request, OfflineClient())
    store.save_project(project.project_id, project.workspace, character_profiles=[profile_update("alice", {
        **ALICE_PROFILE, "notes": "Updated stable characterization.",
    })])
    with pytest.raises(StaleProjectSnapshotError, match="Plan Next context or Story Memory changed"):
        store.apply_story_plan_next(project.project_id, proposal)

    proposal = await plan_next(store, project.project_id, request, OfflineClient())
    store.save_project(project.project_id, project.workspace, character_profiles=[profile_update("distant", {
        "roleBackground": "Updated distant role.", "personality": "Brisk.", "appearance": "Oilskin coat.",
        "speechMannerisms": "Short answers.", "notes": "",
    })])
    applied = store.apply_story_plan_next(project.project_id, proposal)
    assert applied.generation_count == 5


def test_story_response_cannot_mutate_profiles():
    with pytest.raises(ValidationError):
        StoryNextResponse.model_validate({"slots": [], "characterProfiles": []})
