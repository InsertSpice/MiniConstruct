"""Offline coverage for Project-owned Story Creative Direction."""
from __future__ import annotations

import json
from uuid import uuid4

import pytest

from miniconstruct.h3.builder import assemble_prompt
from miniconstruct.models.api import LLMSettings
from miniconstruct.models.story_creative_direction import StoryCreativeDirection
from miniconstruct.models.story_memory import STORY_MEMORY_NEW_SLOTS, StoryContinuityMemory
from miniconstruct.models.story_operations import (
    StoryInsertAuthority, StoryInsertRequest, StoryOperationAuthority, StoryReplanAuthority,
    StoryReplanRequest,
)
from miniconstruct.models.story_planning import StoryRevisionRequest
from miniconstruct.project_store import ProjectStore, StaleProjectSnapshotError
from miniconstruct.story_operations import (
    assemble_insert_prompt, assemble_next_prompt, assemble_replan_prompt,
    bounded_insert_snapshot, bounded_replan_snapshot, bounded_story_snapshot, plan_insert, plan_next,
    plan_replan,
)
from miniconstruct.story_planning import (
    assemble_revision_prompt, assemble_story_prompt, initial_story_draft, story_revision_response_model,
)
from miniconstruct.models.story_operations import StoryNextRequest


def settings():
    return LLMSettings.model_validate({"modelId": "offline", "endpoint": {
        "id": "offline", "displayName": "Offline", "baseUrl": "http://never.test/v1",
    }})


def direction():
    return StoryCreativeDirection(
        toneMood="Quietly ominous with occasional dry humor.",
        performanceEnergy="Natural, restrained acting; fear builds gradually.",
        pacingRhythm="Slow-burn opening, then increasingly short urgent beats.",
        beatDurationDensity="Let reactions breathe; one meaningful development per Generation.",
    )


def project_with_sequence(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path)
    first = store.create_project(
        workspace_factory(creativeRequest="Alice waits for the delayed train."),
        story_idea="Alice waits for a delayed train.",
        story_creative_direction=direction(),
        story_memory=StoryContinuityMemory(recentSummary="Alice is alone on the platform."),
    )
    for beat in ("The departure board flickers.", "A train arrives without headlights."):
        store.create_generation(first.project_id, workspace=workspace_factory(creativeRequest=beat))
    return store, store.load_project(first.project_id)


def context(messages):
    content = messages[1]["content"]
    return json.loads(content[0]["text"] if isinstance(content, list) else content)


def ids(count=STORY_MEMORY_NEW_SLOTS):
    return [str(uuid4()) for _ in range(count)]


def test_default_save_reload_fork_switch_and_legacy_v10_direction_are_safe(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path)
    blank = store.create_project(workspace_factory())
    assert blank.story_creative_direction == StoryCreativeDirection()
    blank_snapshot = store.read_story_snapshot(blank.project_id)
    blank_messages = assemble_story_prompt(
        blank_snapshot, initial_story_draft(blank_snapshot, 2), settings(), idea=""
    )
    assert context(blank_messages)["storyCreativeDirection"] == {
        "toneMood": "",
        "performanceEnergy": "",
        "pacingRhythm": "",
        "beatDurationDensity": "",
    }

    project = store.create_project(
        workspace_factory(projectName="Direction"), story_creative_direction=direction(),
        story_memory=StoryContinuityMemory(recentSummary="Memory stays factual."),
    )
    store.create_generation(project.project_id, workspace=workspace_factory(creativeRequest="Second beat."))
    loaded = store.load_project(project.project_id)
    assert loaded.story_creative_direction == direction()
    memory_before = loaded.story_memory_after.model_dump(mode="json", by_alias=True)
    first_id = loaded.sequence.generations[0].generation_id
    switched = store.activate_generation(project.project_id, first_id)
    assert switched.story_creative_direction == direction()
    assert switched.story_memory_after.model_dump(mode="json", by_alias=True) == memory_before
    fork = store.duplicate_project(project.project_id, "Direction fork")
    assert fork.story_creative_direction == direction()

    manifest_path = store.root / f"project-{project.project_id}" / "project.json"
    legacy = json.loads(manifest_path.read_text(encoding="utf-8"))
    legacy["schemaVersion"] = 10
    legacy.pop("storyCreativeDirection")
    manifest_path.write_text(json.dumps(legacy), encoding="utf-8")
    before = manifest_path.read_bytes()
    migrated = store.load_project(project.project_id)
    assert migrated.story_creative_direction == StoryCreativeDirection()
    assert manifest_path.read_bytes() == before


def test_every_story_request_context_includes_separate_creative_direction(tmp_path, workspace_factory):
    store, project = project_with_sequence(tmp_path, workspace_factory)
    from miniconstruct.models.workspace import DialogueLanguage
    store.save_project(project.project_id, store.read_generation(project.project_id, project.active_generation_id).workspace,
                       dialogue_language=DialogueLanguage.JA)
    snapshot = store.read_story_snapshot(project.project_id)
    draft = initial_story_draft(snapshot, 3)
    expected = direction().model_dump(mode="json", by_alias=True)

    build_messages = assemble_story_prompt(snapshot, draft, settings(), idea="Alice waits for a delayed train.")
    revision = StoryRevisionRequest(
        draft=draft, selectedPlanItemIds=[draft.items[0].plan_item_id], scopes=["camera_intent"],
        instruction="Keep the pressure restrained.", llm=settings(),
    )
    revision_messages = assemble_revision_prompt(snapshot, revision, story_revision_response_model(revision.scopes))

    generations = snapshot.generations
    next_snapshot = bounded_story_snapshot(store, project.project_id, generations[-1].generation_id, "Continue.")
    next_authority = StoryOperationAuthority(
        baseProjectId=project.project_id, anchorGenerationId=generations[-1].generation_id,
        anchorOrdinal=len(generations), operationFingerprint=next_snapshot.fingerprint, instruction="Continue.",
        slotIds=ids(1), newFactIds=ids(), newThreadIds=ids(),
    )
    insert_snapshot = bounded_insert_snapshot(store, project.project_id, generations[0].generation_id, "Bridge.")
    insert_authority = StoryInsertAuthority(
        baseProjectId=project.project_id, upstreamGenerationId=generations[0].generation_id,
        downstreamGenerationId=generations[1].generation_id, upstreamOrdinal=1, downstreamOrdinal=2,
        operationFingerprint=insert_snapshot.fingerprint, instruction="Bridge.",
        slotIds=ids(1), newFactIds=ids(), newThreadIds=ids(),
    )
    replan_snapshot = bounded_replan_snapshot(store, project.project_id, generations[0].generation_id,
                                              generations[1].generation_id, "Tighten.")
    replan_authority = StoryReplanAuthority(
        baseProjectId=project.project_id, projectFingerprint=replan_snapshot.source.fingerprint,
        operationFingerprint=replan_snapshot.fingerprint,
        targetGenerationIds=replan_snapshot.context["targetGenerationIds"], startOrdinal=1, endOrdinal=2,
        upstreamGenerationId=None, downstreamGenerationId=generations[2].generation_id, instruction="Tighten.",
        memoryBeforeRange=StoryContinuityMemory.model_validate(replan_snapshot.context["storyMemory"]),
        newFactIds=ids(), newThreadIds=ids(), allowedReferencePairs=[],
    )
    messages = [build_messages, revision_messages, assemble_next_prompt(next_snapshot, next_authority, settings()),
                assemble_insert_prompt(insert_snapshot, insert_authority, settings()),
                assemble_replan_prompt(replan_snapshot, replan_authority, settings())]
    for message in messages:
        assert context(message)["storyCreativeDirection"] == expected
        assert "Creative Direction is soft presentation and planning guidance" in message[0]["content"]
        if message is not revision_messages:
            assert "Sequence Dialogue Language is Japanese (ja)" in message[0]["content"]
    dialogue_revision = StoryRevisionRequest(
        draft=draft, selectedPlanItemIds=[draft.items[0].plan_item_id], scopes=["dialogue"],
        instruction="Translate this exact line.", llm=settings(),
    )
    dialogue_messages = assemble_revision_prompt(snapshot, dialogue_revision, story_revision_response_model(dialogue_revision.scopes))
    assert "Sequence Dialogue Language is Japanese (ja)" in dialogue_messages[0]["content"]
    assert context(build_messages)["storyCreativeDirection"] != context(build_messages).get("storyIdea")
    assert "storyCreativeDirection" not in context(build_messages)["draft"]


class OfflineNextClient:
    def __call__(self, _settings):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def generate(self, messages, *, response_instruction=None):
        assert response_instruction is None
        payload = context(messages)
        return json.dumps({"slots": [{
            "planItemId": slot_id, "scenePlan": "A patient, tense beat.", "dialogue": "",
            "incomingState": "The platform is empty.", "intendedOutgoingState": "The train draws closer.",
            "relationshipToPrevious": "new_shot_preserve_continuity", "cameraIntent": "A restrained wide shot.",
            "shotCount": 1,
            "referenceRecommendations": [], "memoryPatchAfter": {},
        } for slot_id in payload["slotIds"]], "warnings": []})


class OfflineInsertReplanClient:
    """Small offline provider that returns schema-valid production proposals."""

    def __call__(self, _settings):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def generate(self, messages, *, response_instruction=None):
        assert response_instruction is None
        content = messages[1]["content"]
        payload = json.loads(content[0]["text"] if isinstance(content, list) else content)
        if "slotIds" in payload:
            slots = [{
                "planItemId": slot_id, "scenePlan": "A restrained bridge beat.", "dialogue": "",
                "incomingState": "The bridge is intact.", "intendedOutgoingState": "The crossing continues.",
                "relationshipToPrevious": "new_shot_preserve_continuity", "cameraIntent": "A quiet medium shot.",
                "shotCount": 1,
                "referenceRecommendations": [], "memoryPatchAfter": {},
            } for slot_id in payload["slotIds"]]
            return json.dumps({"slots": slots, "warnings": []})
        items = [{
            "existingGenerationId": generation_id, "scenePlan": "A more restrained bridge beat.",
            "dialogue": "", "incomingState": "The bridge is intact.",
            "intendedOutgoingState": "The crossing continues.",
            "relationshipToPrevious": None if index == 0 and payload["startOrdinal"] == 1
            else "new_shot_preserve_continuity", "cameraIntent": "A quiet medium shot.",
            "shotCount": 1,
            "referenceRecommendations": [], "memoryPatchAfter": {},
        } for index, generation_id in enumerate(payload["targetGenerationIds"])]
        return json.dumps({"items": items, "warnings": []})


@pytest.mark.asyncio
async def test_saved_direction_changes_operation_fingerprint_and_stales_plan_next(tmp_path, workspace_factory):
    store, project = project_with_sequence(tmp_path, workspace_factory)
    before = bounded_story_snapshot(store, project.project_id, project.active_generation_id, "Continue.")
    proposal = await plan_next(store, project.project_id, StoryNextRequest(
        anchorGenerationId=project.active_generation_id, requestedCount=1, instruction="Continue.", llm=settings(),
    ), OfflineNextClient())
    active = store.load_project(project.project_id)
    changed = direction().model_copy(update={"tone_mood": "Briskly comic but still uneasy."})
    memory_before = active.story_memory_after.model_dump(mode="json", by_alias=True)
    store.save_project(project.project_id, active.workspace, story_creative_direction=changed)
    after = bounded_story_snapshot(store, project.project_id, project.active_generation_id, "Continue.")
    assert after.fingerprint != before.fingerprint
    assert store.load_project(project.project_id).story_memory_after.model_dump(mode="json", by_alias=True) == memory_before
    with pytest.raises(StaleProjectSnapshotError, match="Plan Next context"):
        store.apply_story_plan_next(project.project_id, proposal)


@pytest.mark.asyncio
async def test_saved_direction_changes_insert_context_and_stales_apply(tmp_path, workspace_factory):
    store, project = project_with_sequence(tmp_path, workspace_factory)
    generations = store.read_story_snapshot(project.project_id).generations
    proposal = await plan_insert(store, project.project_id, StoryInsertRequest(
        upstreamGenerationId=generations[0].generation_id, requestedCount=1,
        instruction="Bridge the two beats.", llm=settings(),
    ), OfflineInsertReplanClient())
    memory_before = store.load_project(project.project_id).story_memory_after.model_dump(mode="json", by_alias=True)
    active = store.load_project(project.project_id)
    changed = direction().model_copy(update={"performance_energy": "Suddenly energetic, with sharp interruptions."})
    store.save_project(project.project_id, active.workspace, story_creative_direction=changed)
    assert store.load_project(project.project_id).story_memory_after.model_dump(mode="json", by_alias=True) == memory_before
    with pytest.raises(StaleProjectSnapshotError, match="Insert Between .*context changed"):
        store.apply_story_plan_insert(project.project_id, proposal)


@pytest.mark.asyncio
async def test_saved_direction_changes_replan_context_and_stales_apply(tmp_path, workspace_factory):
    store, project = project_with_sequence(tmp_path, workspace_factory)
    generations = store.read_story_snapshot(project.project_id).generations
    proposal = await plan_replan(store, project.project_id, StoryReplanRequest(
        startGenerationId=generations[1].generation_id, endGenerationId=generations[1].generation_id,
        instruction="Tighten the bridge beat.", llm=settings(),
    ), OfflineInsertReplanClient())
    memory_before = store.load_project(project.project_id).story_memory_after.model_dump(mode="json", by_alias=True)
    active = store.load_project(project.project_id)
    changed = direction().model_copy(update={"pacing_rhythm": "Abrupt, compressed turns."})
    store.save_project(project.project_id, active.workspace, story_creative_direction=changed)
    assert store.load_project(project.project_id).story_memory_after.model_dump(mode="json", by_alias=True) == memory_before
    with pytest.raises(StaleProjectSnapshotError, match="Replan Range (Project or )?context changed"):
        store.apply_story_plan_replan(project.project_id, proposal)


def test_creative_direction_is_not_injected_into_h3_generation_prompt(tmp_path, workspace_factory):
    store, project = project_with_sequence(tmp_path, workspace_factory)
    prompt = assemble_prompt(store.load_project(project.project_id).workspace, False).inspector_text
    assert direction().tone_mood not in prompt
    assert direction().pacing_rhythm not in prompt
