from __future__ import annotations

import json

import pytest

from miniconstruct.h3.builder import assemble_prompt
from miniconstruct.models.api import LLMSettings
from miniconstruct.models.story_planning import StoryBuildRequest, StoryBuildResponse, StoryRevisionRequest
from miniconstruct.project_store import ProjectStore
from miniconstruct.story_planning import (
    SCENE_PLAN_POLICY,
    build_story_plan,
    initial_story_draft,
    revise_story_plan,
)


RICH_REQUEST = (
    "The girl scans storefronts and street signs while checking her folded map, growing frustrated "
    "as nothing matches. A flash from the metal rim of a discarded bicycle wheel catches her eye; she "
    "stops mid-step, looks back, and doubles toward it. Crouching beside the wheel, she examines a mark "
    "on its hub, her uncertainty shifting into focused recognition."
)
LLM = LLMSettings.model_validate({
    "modelId": "offline",
    "endpoint": {"id": "offline", "displayName": "Offline", "baseUrl": "http://never.test/v1"},
})


def _context(messages):
    content = messages[1]["content"]
    return json.loads(content[0]["text"] if isinstance(content, list) else content)


class RichBuildClient:
    def __init__(self, creative_requests=None):
        self.calls = []
        self.scene_plans = creative_requests or [RICH_REQUEST, RICH_REQUEST.replace("The girl scans", "She follows")]

    def __call__(self, _settings):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def generate(self, messages, *, response_instruction=None):
        assert response_instruction is None
        self.calls.append(messages)
        context = _context(messages)
        items = []
        for index, item in enumerate(context["draft"]["items"]):
            items.append({
                "planItemId": item["planItemId"],
                "scenePlan": self.scene_plans[min(index, len(self.scene_plans) - 1)],
                "dialogue": "",
                "incomingState": "She begins the search at the edge of the city.",
                "intendedOutgoingState": "She has identified a clue in the wheel.",
                "relationshipToPrevious": None if index == 0 else "new_shot_preserve_continuity",
                "cameraIntent": "A measured medium view that stays with her search.",
                "shotCount": 1,
                "referenceRecommendations": [],
            })
        return json.dumps({
            "sequenceContext": "The city search follows clues found in ordinary street objects.",
            "items": items,
            "consistencyWarnings": [],
        })


class RichRevisionClient:
    def __init__(self, value=RICH_REQUEST):
        self.calls = []
        self.value = value

    def __call__(self, _settings):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def generate(self, messages, *, response_instruction=None):
        assert response_instruction is None
        self.calls.append(messages)
        context = _context(messages)
        assert context["editableFields"] == ["scenePlan"]
        return json.dumps({
            "patches": [{"planItemId": context["selectedPlanItemIds"][0], "scenePlan": self.value}],
        })


def test_story_response_schema_accepts_detailed_creative_request():
    items = [{
        "planItemId": f"00000000-0000-4000-8000-00000000000{index + 1}",
        "scenePlan": RICH_REQUEST,
        "dialogue": "",
        "incomingState": "The search is just beginning.",
        "intendedOutgoingState": "A clue has been recognized.",
        "relationshipToPrevious": None if index == 0 else "new_shot_preserve_continuity",
        "cameraIntent": "A restrained tracking view.",
        "referenceRecommendations": [],
    } for index in range(2)]
    response = StoryBuildResponse.model_validate({
        "sequenceContext": "A city search unfolds through found clues.",
        "items": items,
        "consistencyWarnings": [],
    })
    assert response.items[0].scene_plan == RICH_REQUEST
    assert response.items[0].incoming_state != response.items[0].scene_plan


@pytest.mark.asyncio
async def test_scene_plan_survives_apply_reload_and_story_h3_path(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory(creativeRequest="A girl begins searching the city."), story_idea="A girl searches the city and notices a discarded bicycle wheel.")
    client = RichBuildClient()
    draft = await build_story_plan(store, project.project_id, StoryBuildRequest(
        idea="A girl searches the city and notices a discarded bicycle wheel.",
        desiredGenerationCount=2, llm=LLM,
    ), client)
    rich_generation_request = RICH_REQUEST.replace("The girl scans", "She follows")
    assert draft.items[0].scene_plan == RICH_REQUEST
    assert draft.items[1].scene_plan == rich_generation_request
    assert draft.items[1].incoming_state != draft.items[1].scene_plan
    store.apply_story_plan(project.project_id, draft)

    reloaded = store.read_story_snapshot(project.project_id)
    generation = reloaded.generations[1]
    assert generation.planning.scene_plan == rich_generation_request
    assert generation.incoming_state == "She begins the search at the edge of the city."
    assert generation.intended_outgoing_state == "She has identified a clue in the wheel."
    story_h3 = assemble_prompt(generation.workspace, False,
                               story_context=store.story_h3_context(project.project_id, generation.generation_id)).inspector_text
    assert rich_generation_request in story_h3
    assert "A girl begins searching the city." not in story_h3

    simple_manual = workspace_factory(creativeRequest="A woman notices a bicycle wheel on the pavement.")
    assert "A woman notices a bicycle wheel on the pavement." in assemble_prompt(simple_manual, False).inspector_text


@pytest.mark.asyncio
async def test_story_revision_main_prompt_replaces_rich_request(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory(creativeRequest="Original action."))
    store.create_generation(project.project_id, workspace=workspace_factory(creativeRequest="Existing action."))
    draft = initial_story_draft(store.read_story_snapshot(project.project_id), 2)
    selected = draft.items[1].plan_item_id
    result = await revise_story_plan(store, project.project_id, StoryRevisionRequest(
        draft=draft, selectedPlanItemIds=[selected], scopes=["scene_plan"],
        instruction="Author the within-Generation action more fully.", llm=LLM,
    ), RichRevisionClient())
    assert result.items[1].scene_plan == RICH_REQUEST
    assert result.items[1].incoming_state == draft.items[1].incoming_state
    assert result.items[1].intended_outgoing_state == draft.items[1].intended_outgoing_state


@pytest.mark.asyncio
async def test_rich_request_survives_plan_next_insert_and_replan_apply(tmp_path, workspace_factory):
    from test_story_initial_range import OfflineInitialRangeClient, propose as propose_initial, saved_story
    from test_story_insert import OfflineInsertClient, make_story as make_insert_story, propose as propose_insert
    from test_story_next import OfflineNextClient, create_story as make_next_story, propose as propose_next
    from test_story_replan import OfflineReplanClient, make_story as make_replan_story, propose as propose_replan

    def rich_initial(response, _context):
        response["items"][1]["scenePlan"] = RICH_REQUEST

    initial_store, initial_project = saved_story(tmp_path / "initial", workspace_factory)
    initial_client = OfflineInitialRangeClient(rich_initial)
    initial_proposal = await propose_initial(initial_store, initial_project, 2, initial_client)
    assert initial_proposal.items[1].scene_plan == RICH_REQUEST
    initial_store.apply_story_initial_range(initial_project.project_id, initial_proposal)
    initial_generation = initial_store.read_story_snapshot(initial_project.project_id).generations[1]
    assert initial_generation.planning.scene_plan == RICH_REQUEST
    assert initial_generation.incoming_state == "Arrival 1"
    assert initial_generation.intended_outgoing_state == "Departure 1"

    def rich_slot(response, _context):
        response["slots"][0]["scenePlan"] = RICH_REQUEST

    next_store, next_project = make_next_story(tmp_path / "next", workspace_factory, length=4)
    next_client = OfflineNextClient(rich_slot)
    next_proposal = await propose_next(next_store, next_project, next_client, count=1)
    assert next_proposal.slots[0].scene_plan == RICH_REQUEST
    next_store.apply_story_plan_next(next_project.project_id, next_proposal)
    next_generation = next_store.read_story_snapshot(next_project.project_id).generations[-1]
    assert next_generation.planning.scene_plan == RICH_REQUEST
    assert next_generation.incoming_state == "At the station"
    assert next_generation.intended_outgoing_state == "Moving inside"

    def rich_insert_slot(response, _context):
        response["slots"][0]["scenePlan"] = RICH_REQUEST

    insert_store, insert_project, _upstream, _downstream, insert_id = make_insert_story(tmp_path / "insert", workspace_factory)
    insert_client = OfflineInsertClient(rich_insert_slot)
    insert_proposal = await propose_insert(insert_store, insert_project, insert_id, insert_client, count=1)
    assert insert_proposal.slots[0].scene_plan == RICH_REQUEST
    insert_store.apply_story_plan_insert(insert_project.project_id, insert_proposal)
    inserted = next(g for g in insert_store.read_story_snapshot(insert_project.project_id).generations
                    if g.planning.scene_plan == RICH_REQUEST)
    assert inserted.incoming_state == "At the intact bridge"
    assert inserted.intended_outgoing_state == "Approaching the protected next beat"

    def rich_replan_item(response, _context):
        response["items"][0]["scenePlan"] = RICH_REQUEST

    replan_store, replan_project, _upstream, _downstream, replan_ids = make_replan_story(tmp_path / "replan", workspace_factory)
    replan_client = OfflineReplanClient(rich_replan_item)
    replan_proposal = await propose_replan(replan_store, replan_project, replan_ids[2], replan_ids[2], replan_client)
    assert replan_proposal.items[0].scene_plan == RICH_REQUEST
    replan_store.apply_story_plan_replan(replan_project.project_id, replan_proposal)
    replanned = replan_store.read_generation(replan_project.project_id, replan_ids[2])
    assert replanned.planning.scene_plan == RICH_REQUEST
    assert replanned.incoming_state == "Bridge intact"
    assert replanned.intended_outgoing_state == "Delivery still unresolved"


@pytest.mark.asyncio
async def test_all_story_operations_receive_the_shared_creative_request_policy(tmp_path, workspace_factory):
    from test_story_initial_range import OfflineInitialRangeClient, propose as propose_initial, saved_story
    from test_story_insert import OfflineInsertClient, make_story as make_insert_story, propose as propose_insert
    from test_story_next import OfflineNextClient, create_story as make_next_story, propose as propose_next
    from test_story_replan import OfflineReplanClient, make_story as make_replan_story, propose as propose_replan

    expected = SCENE_PLAN_POLICY.strip()

    initial_store, initial_project = saved_story(tmp_path / "initial", workspace_factory)
    initial_client = OfflineInitialRangeClient()
    await propose_initial(initial_store, initial_project, 2, initial_client)
    assert expected in initial_client.calls[0][0]["content"]

    next_store, next_project = make_next_story(tmp_path / "next", workspace_factory, length=4)
    next_client = OfflineNextClient()
    await propose_next(next_store, next_project, next_client, count=1)
    assert expected in next_client.calls[0][0]["content"]

    insert_store, insert_project, _upstream, _downstream, insert_id = make_insert_story(tmp_path / "insert", workspace_factory)
    insert_client = OfflineInsertClient()
    await propose_insert(insert_store, insert_project, insert_id, insert_client, count=1)
    assert expected in insert_client.calls[0][0]["content"]

    replan_store, replan_project, _upstream, _downstream, replan_ids = make_replan_story(tmp_path / "replan", workspace_factory)
    replan_client = OfflineReplanClient()
    await propose_replan(replan_store, replan_project, replan_ids[2], replan_ids[2], replan_client)
    assert expected in replan_client.calls[0][0]["content"]

    revision_store = ProjectStore(tmp_path / "revision-policy")
    revision_project = revision_store.create_project(workspace_factory(creativeRequest="Original."))
    revision_store.create_generation(revision_project.project_id, workspace=workspace_factory(creativeRequest="Second."))
    revision_draft = initial_story_draft(revision_store.read_story_snapshot(revision_project.project_id), 2)
    revision_client = RichRevisionClient()
    await revise_story_plan(revision_store, revision_project.project_id, StoryRevisionRequest(
        draft=revision_draft, selectedPlanItemIds=[revision_draft.items[1].plan_item_id], scopes=["scene_plan"],
        instruction="Add concrete within-Generation progression.", llm=LLM,
    ), revision_client)
    assert expected in revision_client.calls[0][0]["content"]
