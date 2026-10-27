"""Provider-free checks for Story shot-count authority and H3 adaptation."""
import json

import pytest
from pydantic import ValidationError

from miniconstruct.models.story_planning import StoryPlanItem, LegacyStoryBuildResponse, story_revision_response_model
from miniconstruct.models.story_operations import (
    StoryInitialRangeResponse, StoryNextResponse, StoryInsertResponse, StoryReplanResponse,
)
from miniconstruct.models.api import PlanningRevisionScope
from miniconstruct.project_store import ProjectStore
from miniconstruct.story_planning import initial_story_draft
from miniconstruct.h3.builder import assemble_prompt
from test_story_initial_range import OfflineInitialRangeClient, saved_story, propose as propose_initial
from test_story_next import OfflineNextClient, create_story, propose as propose_next
from test_story_insert import OfflineInsertClient, make_story as make_insert_story, propose as propose_insert
from test_story_replan import OfflineReplanClient, make_story as make_replan_story, propose as propose_replan
from miniconstruct.story_planning import revise_story_plan
from test_story_planning import PatchModel, revision, settings


def test_story_shot_count_uses_workspace_positive_integer_bounds():
    values = dict(planItemId="00000000-0000-4000-8000-000000000001", scenePlan="Scene",
                  incomingState="Start", intendedOutgoingState="End",
                  relationshipToPrevious=None, cameraIntent="Shot 1 — hold")
    for count in (1, 3, 17):
        assert StoryPlanItem.model_validate({**values, "shotCount": count,
                                             "existingGenerationId": None}).shot_count == count
    for count in (0, -1, 1.5, "3"):
        with pytest.raises(ValidationError):
            StoryPlanItem.model_validate({**values, "shotCount": count,
                                          "existingGenerationId": None})


def test_legacy_planning_count_follows_saved_workspace_and_manual_override(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path)
    created = store.create_project(workspace_factory(shots=3))
    path = tmp_path / f"project-{created.project_id}" / "project.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["sequence"]["generations"][0]["planning"].pop("shotCount", None)
    path.write_text(json.dumps(raw), encoding="utf-8")
    snapshot = store.read_story_snapshot(created.project_id)
    assert snapshot.generations[0].planning.shot_count == 3
    assert initial_story_draft(snapshot, 2).items[0].shot_count == 3
    assert "shotCount" not in json.loads(path.read_text())["sequence"]["generations"][0]["planning"]
    store.save_project(created.project_id, snapshot.generations[0].workspace.model_copy(update={"shots": 2}))
    overridden = store.read_story_snapshot(created.project_id).generations[0]
    assert overridden.workspace.shots == 2 and overridden.planning.shot_count == 3


def test_shot_plan_revision_schema_requires_count_and_camera():
    schema = story_revision_response_model([PlanningRevisionScope.CAMERA_INTENT]).model_json_schema(by_alias=True)
    patch = schema["$defs"]["StoryRevisionPatch"]
    assert set(patch["required"]) == {"planItemId", "shotCount", "cameraIntent"}
    for model in (StoryInitialRangeResponse, StoryNextResponse, StoryInsertResponse, StoryReplanResponse):
        schema = model.model_json_schema(by_alias=True)
        item = next(definition for definition in schema["$defs"].values()
                    if "shotCount" in definition.get("properties", {}))
        assert "shotCount" in item["required"]
    legacy_item = LegacyStoryBuildResponse.model_json_schema(by_alias=True)["$defs"]["LegacyStoryBuildItem"]
    assert "shotCount" in legacy_item["required"]


def test_h3_policy_keeps_current_workspace_count_and_boundary_guidance(workspace_factory):
    workspace = workspace_factory(shots=2)
    story = {"currentGeneration": {"scenePlan": "Discovery", "shotCount": 3,
              "cameraIntent": "Shot 1 — close-up. Shot 2 — insert. Shot 3 — reaction.",
              "incomingState": "Tight close-up", "intendedOutgoingState": "Wide reaction"}}
    assembled = assemble_prompt(workspace, False, story_context=story)
    text = str(assembled.messages)
    assert '"currentH3SetupShots":2' in text
    assert "Incoming State" in text and "Intended Outgoing State" in text
    assert "timestamps" in text


@pytest.mark.asyncio
async def test_all_bounded_story_applies_copy_planned_shots_only_on_apply(tmp_path, workspace_factory):
    store, project = saved_story(tmp_path / "initial", workspace_factory)
    proposal = await propose_initial(store, project, 2,
        OfflineInitialRangeClient(lambda response, _context: response["items"][1].update(shotCount=3)))
    assert store.read_story_snapshot(project.project_id).generations[0].workspace.shots is None
    store.apply_story_initial_range(project.project_id, proposal)
    assert store.read_story_snapshot(project.project_id).generations[1].workspace.shots == 3

    store, project = create_story(tmp_path / "next", workspace_factory, length=4)
    proposal = await propose_next(store, project,
        OfflineNextClient(lambda response, _context: response["slots"][0].update(shotCount=3)), count=1)
    assert len(store.read_story_snapshot(project.project_id).generations) == 4
    store.apply_story_plan_next(project.project_id, proposal)
    assert store.read_story_snapshot(project.project_id).generations[-1].workspace.shots == 3

    store, project, _before, _after, upstream_id = make_insert_story(tmp_path / "insert", workspace_factory)
    proposal = await propose_insert(store, project, upstream_id,
        OfflineInsertClient(lambda response, _context: response["slots"][0].update(shotCount=3)), count=1)
    assert len(store.read_story_snapshot(project.project_id).generations) == 5
    store.apply_story_plan_insert(project.project_id, proposal)
    assert store.read_story_snapshot(project.project_id).generations[3].workspace.shots == 3

    store, project, _before, _after, ids = make_replan_story(tmp_path / "replan", workspace_factory)
    proposal = await propose_replan(store, project, ids[2], ids[2],
        OfflineReplanClient(lambda response, _context: response["items"][0].update(shotCount=3)))
    assert store.read_story_snapshot(project.project_id).generations[2].workspace.shots is None
    store.apply_story_plan_replan(project.project_id, proposal)
    assert store.read_story_snapshot(project.project_id).generations[2].workspace.shots == 3


def _applied_two_generation_story(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path)
    workspace = workspace_factory(shots=3, creativeRequest="A quiet search.")
    project = store.create_project(workspace, planning={"scenePlan": "The search begins."})
    project = store.create_generation(project.project_id, workspace=workspace)
    snapshot = store.read_story_snapshot(project.project_id)
    draft = initial_story_draft(snapshot, 2)
    for item in draft.items:
        item.shot_count = 3
    store.apply_story_plan(project.project_id, draft)
    target_id = store.read_story_snapshot(project.project_id).generations[1].generation_id
    active = store.activate_generation(project.project_id, target_id)
    store.save_project(project.project_id, active.workspace.model_copy(update={"shots": 2}))
    return store, project.project_id, target_id


@pytest.mark.asyncio
@pytest.mark.parametrize("scope,alias,value", [
    ("dialogue", "dialogue", "Alice: I found it."),
    ("scene_plan", "scenePlan", "Alice finds the marked door."),
    ("incoming_state", "incomingState", "Alice reaches the marked door."),
    ("intended_outgoing_state", "intendedOutgoingState", "The door opens."),
    ("relationship_to_previous", "relationshipToPrevious", "independent"),
])
async def test_non_shot_story_revision_preserves_manual_h3_shot_override(
    tmp_path, workspace_factory, scope, alias, value,
):
    store, project_id, target_id = _applied_two_generation_story(tmp_path, workspace_factory)
    snapshot = store.read_story_snapshot(project_id)
    draft = initial_story_draft(snapshot, 2)
    target = draft.items[1]
    model = PatchModel(lambda payload: payload["patches"][0].update({alias: value}))
    revised = await revise_story_plan(
        store, project_id, revision(draft, selected=[target.plan_item_id], scopes=[scope]), model,
    )
    store.apply_story_plan(project_id, revised)
    saved = store.read_generation(project_id, target_id)
    assert saved.planning.shot_count == 3
    assert saved.workspace.shots == 2
    actual = {
        "dialogue": saved.workspace.dialogue,
        "scene_plan": saved.planning.scene_plan,
        "incoming_state": saved.incoming_state,
        "intended_outgoing_state": saved.intended_outgoing_state,
        "relationship_to_previous": saved.planning.relationship_to_previous,
    }[scope]
    assert actual == value


@pytest.mark.asyncio
async def test_explicit_shot_plan_revision_synchronizes_manual_override(tmp_path, workspace_factory):
    store, project_id, target_id = _applied_two_generation_story(tmp_path, workspace_factory)
    snapshot = store.read_story_snapshot(project_id)
    draft = initial_story_draft(snapshot, 2)
    target = draft.items[1]
    def change(payload):
        payload["patches"][0].update(cameraIntent="Shot 1 — open wider.", shotCount=4)
    revised = await revise_story_plan(
        store, project_id, revision(draft, selected=[target.plan_item_id], scopes=["camera_intent"]),
        PatchModel(change),
    )
    store.apply_story_plan(project_id, revised)
    saved = store.read_generation(project_id, target_id)
    assert saved.planning.shot_count == 4 and saved.workspace.shots == 4


def test_direct_story_draft_shot_edit_is_an_explicit_count_change(tmp_path, workspace_factory):
    store, project_id, target_id = _applied_two_generation_story(tmp_path, workspace_factory)
    draft = initial_story_draft(store.read_story_snapshot(project_id), 2)
    draft.items[1].shot_count = 4
    store.apply_story_plan(project_id, draft)
    saved = store.read_generation(project_id, target_id)
    assert saved.planning.shot_count == 4 and saved.workspace.shots == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("new_count,expected_workspace_count", [(3, 2), (4, 4)])
async def test_replan_only_synchronizes_when_planned_count_changes(
    tmp_path, workspace_factory, new_count, expected_workspace_count,
):
    store, project, _before, _after, ids = make_replan_story(tmp_path, workspace_factory)
    target_id = ids[2]
    active = store.activate_generation(project.project_id, target_id)
    store.save_project(project.project_id, active.workspace.model_copy(update={"shots": 3}),
                       planning={"shotCount": 3})
    active = store.activate_generation(project.project_id, target_id)
    store.save_project(project.project_id, active.workspace.model_copy(update={"shots": 2}))
    client = OfflineReplanClient(lambda response, _context: response["items"][0].update(shotCount=new_count))
    proposal = await propose_replan(store, project, target_id, target_id, client)
    store.apply_story_plan_replan(project.project_id, proposal)
    saved = store.read_generation(project.project_id, target_id)
    assert saved.planning.shot_count == new_count
    assert saved.workspace.shots == expected_workspace_count
