"""Provider-free Project Planning Style and Story operation contracts."""
import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from miniconstruct.models.api import ProjectEnvelope
from miniconstruct.models.story_planning_style import StoryPlanningStyle
from miniconstruct.models.story_creative_direction import StoryCreativeDirection
from miniconstruct.models.story_memory import STORY_MEMORY_NEW_SLOTS, StoryContinuityMemory
from miniconstruct.models.story_operations import StoryInitialRangeAuthority, StoryInsertAuthority, StoryOperationAuthority, StoryReplanAuthority
from miniconstruct.models.story_operations import StoryNextRequest
from miniconstruct.models.story_planning import StoryRevisionRequest
from miniconstruct.project_store import ProjectStore, ProjectManifestV16, StaleProjectSnapshotError
from miniconstruct.story_operations import (
    assemble_initial_range_prompt, assemble_insert_prompt, assemble_next_prompt, assemble_replan_prompt,
    bounded_insert_snapshot, bounded_replan_snapshot, bounded_story_snapshot, initial_range_authority_fingerprint,
    plan_next,
)
from miniconstruct.story_planning import (
    assemble_revision_prompt, assemble_story_prompt, initial_story_draft, story_revision_response_model,
)
from miniconstruct.story_planning_style import story_planning_style_policy
from miniconstruct.dialogue_language import story_dialogue_policy
from miniconstruct.models.sequence_subject import character_profile_policy_for
from miniconstruct import story_operations as operations, story_planning as planning
from test_story_creative_direction import OfflineNextClient, settings


DEFAULT = StoryPlanningStyle()
GUIDED = StoryPlanningStyle(creativity="exploratory", scenePlanDetail="detailed")


def ids(count=STORY_MEMORY_NEW_SLOTS):
    return [str(uuid4()) for _ in range(count)]


def project(tmp_path, workspace_factory, style=DEFAULT):
    store = ProjectStore(tmp_path)
    created = store.create_project(workspace_factory(), story_idea="A quiet platform encounter.",
                                   story_planning_style=style)
    for beat in ("A traveler waits.", "The train arrives."):
        store.create_generation(created.project_id, workspace=workspace_factory(creativeRequest=beat))
    return store, store.load_project(created.project_id)


def operation_messages(store, project_id):
    snapshot = store.read_story_snapshot(project_id)
    draft = initial_story_draft(snapshot, 3)
    revision = StoryRevisionRequest(draft=draft, selectedPlanItemIds=[draft.items[0].plan_item_id],
                                    scopes=["scene_plan"], instruction="Clarify the response.", llm=settings())
    camera_revision = StoryRevisionRequest(draft=draft, selectedPlanItemIds=[draft.items[0].plan_item_id],
                                           scopes=["camera_intent"], instruction="Widen the frame.", llm=settings())
    generations = snapshot.generations
    next_snapshot = bounded_story_snapshot(store, project_id, generations[-1].generation_id, "Continue.")
    next_authority = StoryOperationAuthority(
        baseProjectId=project_id, anchorGenerationId=generations[-1].generation_id,
        anchorOrdinal=len(generations), operationFingerprint=next_snapshot.fingerprint,
        instruction="Continue.", slotIds=ids(1), newFactIds=ids(), newThreadIds=ids())
    insert_snapshot = bounded_insert_snapshot(store, project_id, generations[0].generation_id, "Bridge.")
    insert_authority = StoryInsertAuthority(
        baseProjectId=project_id, upstreamGenerationId=generations[0].generation_id,
        downstreamGenerationId=generations[1].generation_id, upstreamOrdinal=1, downstreamOrdinal=2,
        operationFingerprint=insert_snapshot.fingerprint, instruction="Bridge.",
        slotIds=ids(1), newFactIds=ids(), newThreadIds=ids())
    replan_snapshot = bounded_replan_snapshot(store, project_id, generations[0].generation_id,
                                              generations[1].generation_id, "Tighten.")
    replan_authority = StoryReplanAuthority(
        baseProjectId=project_id, projectFingerprint=replan_snapshot.source.fingerprint,
        operationFingerprint=replan_snapshot.fingerprint,
        targetGenerationIds=replan_snapshot.context["targetGenerationIds"], startOrdinal=1, endOrdinal=2,
        upstreamGenerationId=None, downstreamGenerationId=generations[2].generation_id, instruction="Tighten.",
        memoryBeforeRange=StoryContinuityMemory.model_validate(replan_snapshot.context["storyMemory"]),
        newFactIds=ids(), newThreadIds=ids(), allowedReferencePairs=[])
    initial_authority = StoryInitialRangeAuthority(
        baseProjectId=project_id, projectFingerprint=initial_range_authority_fingerprint(snapshot),
        g1GenerationId=generations[0].generation_id, sourceGenerationCount=1, requestedTotalCount=2,
        slotIds=ids(2), newFactIds=ids(), newThreadIds=ids(), allowedReferencePairs=[])
    return {
        "legacy_build": assemble_story_prompt(snapshot, draft, settings(), idea="A quiet platform encounter."),
        "initial": assemble_initial_range_prompt(snapshot, initial_authority, settings()),
        "next": assemble_next_prompt(next_snapshot, next_authority, settings()),
        "insert": assemble_insert_prompt(insert_snapshot, insert_authority, settings()),
        "replan": assemble_replan_prompt(replan_snapshot, replan_authority, settings()),
        "revision": assemble_revision_prompt(snapshot, revision, story_revision_response_model(revision.scopes)),
        "camera_revision": assemble_revision_prompt(snapshot, camera_revision,
                                                   story_revision_response_model(camera_revision.scopes)),
    }


def test_style_policy_semantics_and_strict_enums():
    assert DEFAULT.model_dump(by_alias=True) == {"creativity": "balanced", "scenePlanDetail": "standard"}
    assert story_planning_style_policy(DEFAULT) == ""
    faithful = story_planning_style_policy(StoryPlanningStyle(creativity="faithful"))
    assert all(term in faithful for term in ("Minimize unsupported", "Stay close", "necessary connective observable"))
    exploratory = story_planning_style_policy(StoryPlanningStyle(creativity="exploratory"))
    assert all(term in exploratory for term in ("plausible local connective invention", "Story Memory", "exact dialogue", "shot count"))
    concise = story_planning_style_policy(StoryPlanningStyle(scenePlanDetail="concise"))
    assert "compact but directable, never vague" in concise
    detailed = story_planning_style_policy(StoryPlanningStyle(scenePlanDetail="detailed"))
    assert all(term in detailed for term in ("action", "reaction", "attention", "posture", "blocking", "environment", "H3 timestamps", "fixed beat count", "extra plot", "Beat Duration / Density"))
    assert story_planning_style_policy(GUIDED, scene_plan_editable=False) == ""
    for invalid in ({"creativity": "wild"}, {"scenePlanDetail": "verbose"}, {"extra": True}):
        with pytest.raises(ValidationError):
            StoryPlanningStyle.model_validate(invalid)


def test_project_defaults_v16_read_save_fork_and_portable_round_trip(tmp_path, workspace_factory):
    store, created = project(tmp_path, workspace_factory)
    path = store.root / f"project-{created.project_id}" / "project.json"
    raw = json.loads(path.read_text())
    assert raw["schemaVersion"] == 17
    assert raw["storyPlanningStyle"] == DEFAULT.model_dump(by_alias=True)
    raw["schemaVersion"] = 16
    raw.pop("storyPlanningStyle")
    ProjectManifestV16.model_validate(raw)
    path.write_text(json.dumps(raw))
    before = path.read_bytes()
    assert store.load_project(created.project_id).story_planning_style == DEFAULT
    assert path.read_bytes() == before
    saved = store.save_project(created.project_id, store.load_project(created.project_id).workspace,
                               story_planning_style=GUIDED)
    assert saved.story_planning_style == GUIDED
    assert json.loads(path.read_text())["schemaVersion"] == 17
    assert store.duplicate_project(created.project_id, "Style fork").story_planning_style == GUIDED
    envelope = ProjectEnvelope.model_validate({"format": "MiniConstruct Project", "schemaVersion": 1,
                                                "workspace": saved.workspace.model_dump(mode="json", by_alias=True),
                                                "storyPlanningStyle": GUIDED.model_dump(by_alias=True)})
    assert ProjectEnvelope.model_validate(envelope.model_dump(mode="json", by_alias=True)).story_planning_style == GUIDED


@pytest.mark.parametrize("version", range(11, 17))
def test_frozen_recent_readers_adapt_without_mutation(tmp_path, workspace_factory, version):
    store, created = project(tmp_path, workspace_factory, GUIDED)
    path = store.root / f"project-{created.project_id}" / "project.json"
    raw = json.loads(path.read_text())
    raw["schemaVersion"] = version
    raw.pop("storyPlanningStyle")
    path.write_text(json.dumps(raw))
    before = path.read_bytes()
    loaded = store.load_project(created.project_id)
    assert loaded.story_planning_style == DEFAULT
    assert path.read_bytes() == before
    saved = store.save_project(created.project_id, loaded.workspace)
    assert saved.story_planning_style == DEFAULT
    assert json.loads(path.read_text())["schemaVersion"] == 17


def test_all_operations_receive_only_nondefault_style_and_revision_is_scope_bound(tmp_path, workspace_factory):
    store, created = project(tmp_path, workspace_factory)
    baseline = operation_messages(store, created.project_id)
    for name, messages in baseline.items():
        assert "Story Planning Style" not in messages[0]["content"], name
        payload = messages[1]["content"]
        if isinstance(payload, list): payload = payload[0]["text"]
        assert "storyPlanningStyle" not in json.loads(payload), name
    store.save_project(created.project_id, created.workspace, story_planning_style=GUIDED)
    guided = operation_messages(store, created.project_id)
    for name, messages in guided.items():
        if name == "camera_revision":
            assert "Story Planning Style" not in messages[0]["content"]
        else:
            assert "Story Planning Style" in messages[0]["content"], name
            assert "plausible local connective invention" in messages[0]["content"], name
            assert "Beat Duration / Density" in messages[0]["content"], name
        assert "Story Creative Direction" in messages[0]["content"] or "Creative Direction" in messages[0]["content"]
    assert "editableScopes" in json.dumps(guided["camera_revision"][1])


def test_default_initial_next_and_revision_system_messages_equal_pre_feature_composition(tmp_path, workspace_factory):
    store, created = project(tmp_path, workspace_factory)
    snapshot = store.read_story_snapshot(created.project_id)
    messages = operation_messages(store, created.project_id)
    cast = character_profile_policy_for(snapshot.sequence_subjects)
    expected_initial = (operations.INITIAL_RANGE_SYSTEM + "\n" + story_dialogue_policy(snapshot.dialogue_language)
        + operations.SEQUENCE_CONTEXT_POLICY + operations.SCENE_PLAN_POLICY + operations.SAME_SHOT_OPENING_POLICY
        + operations.STORY_SHOT_PLAN_POLICY + operations.STORY_DURATION_POLICY + planning.CREATIVE_DIRECTION_POLICY
        + cast + operations.shot_guidance_policy(None))
    expected_next = (operations.NEXT_SYSTEM + operations.SEQUENCE_CONTEXT_POLICY + operations.SCENE_PLAN_POLICY
        + operations.SAME_SHOT_OPENING_POLICY + operations.STORY_SHOT_PLAN_POLICY + operations.STORY_DURATION_POLICY
        + operations.OBSERVATION_POLICY + "\n" + story_dialogue_policy(snapshot.dialogue_language)
        + planning.CREATIVE_DIRECTION_POLICY + cast + operations.shot_guidance_policy(None))
    expected_revision = planning.REVISION_AUTHORITY + planning.CREATIVE_DIRECTION_POLICY + cast
    assert messages["initial"][0] == {"role": "system", "content": expected_initial}
    assert messages["next"][0] == {"role": "system", "content": expected_next}
    assert messages["revision"][0] == {"role": "system", "content": expected_revision}
    for name in ("initial", "next", "revision"):
        assert len(messages[name]) == 2 and messages[name][1]["role"] == "user"
        assert "storyPlanningStyle" not in json.dumps(messages[name][1])


def test_saved_style_changes_story_authority_fingerprints(tmp_path, workspace_factory):
    store, created = project(tmp_path, workspace_factory)
    before = store.read_story_snapshot(created.project_id)
    initial = initial_range_authority_fingerprint(before)
    next_before = bounded_story_snapshot(store, created.project_id, before.generations[-1].generation_id, "Continue.")
    revision_before = store._story_revision_fingerprint(before.manifest)
    store.save_project(created.project_id, created.workspace,
                       story_planning_style=StoryPlanningStyle(scenePlanDetail="detailed"))
    after = store.read_story_snapshot(created.project_id)
    assert initial_range_authority_fingerprint(after) != initial
    assert bounded_story_snapshot(store, created.project_id, after.generations[-1].generation_id, "Continue.").fingerprint != next_before.fingerprint
    assert store._story_revision_fingerprint(after.manifest) != revision_before


def test_creative_direction_density_and_current_memory_coexist_with_style(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path)
    created = store.create_project(
        workspace_factory(), story_planning_style=GUIDED,
        story_creative_direction=StoryCreativeDirection(
            performanceEnergy="Restrained performance.", beatDurationDensity="Sparse: one development."),
        story_memory=StoryContinuityMemory(recentSummary="The traveler currently uses crutches."))
    bounded = bounded_story_snapshot(store, created.project_id, created.active_generation_id, "Continue.")
    authority = StoryOperationAuthority(
        baseProjectId=created.project_id, anchorGenerationId=created.active_generation_id,
        anchorOrdinal=1, operationFingerprint=bounded.fingerprint, instruction="Continue.",
        slotIds=ids(1), newFactIds=ids(), newThreadIds=ids())
    messages = assemble_next_prompt(bounded, authority, settings())
    system, user = messages[0]["content"], json.loads(messages[1]["content"])
    assert "plausible local connective invention" in system and "Beat Duration / Density" in system
    assert "Restrained performance." in user["storyCreativeDirection"]["performanceEnergy"]
    assert "Sparse: one development." in user["storyCreativeDirection"]["beatDurationDensity"]
    assert "crutches" in user["storyMemory"]["recentSummary"]


def test_style_save_does_not_change_h3_execution_fingerprints(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path)
    created = store.create_project(workspace_factory(), story_idea="A quiet arrival.",
                                   planning={"scenePlan": "The traveler notices the train."})
    before = store.execution_snapshot(created.project_id, created.active_generation_id)[1].current_fingerprints
    store.save_project(created.project_id, created.workspace, story_planning_style=GUIDED)
    after = store.execution_snapshot(created.project_id, created.active_generation_id)[1].current_fingerprints
    assert before == after and "story" in after and "one_shot" in after


@pytest.mark.asyncio
async def test_saved_style_change_stales_outstanding_story_proposal(tmp_path, workspace_factory):
    store, created = project(tmp_path, workspace_factory)
    latest = created.sequence.generations[-1].generation_id
    proposal = await plan_next(store, created.project_id,
                               StoryNextRequest(anchorGenerationId=latest, requestedCount=1, llm=settings()),
                               OfflineNextClient())
    store.save_project(created.project_id, created.workspace,
                       story_planning_style=StoryPlanningStyle(creativity="exploratory"))
    with pytest.raises(StaleProjectSnapshotError):
        store.apply_story_plan_next(created.project_id, proposal)
