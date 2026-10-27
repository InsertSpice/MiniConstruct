"""Provider-free canonical participation contracts on real Story request paths."""
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from miniconstruct.llm.client import LLMBackendError
from miniconstruct.models.api import LLMSettings
from miniconstruct.models.story_memory import StoryContinuityMemory
from miniconstruct.models.story_operations import (
    StoryInitialRangeRequest, StoryInitialRangeResponse, StoryNextRequest,
    StoryNextResponse, StoryInsertRequest, StoryInsertResponse, StoryReplanResponse,
    story_participant_response_model,
)
from miniconstruct.models.story_planning import StoryRevisionScope, story_revision_response_model
from miniconstruct.project_store import ProjectStore
from miniconstruct.story_operations import (
    build_initial_range, plan_next, plan_insert, validate_participating_subjects,
)

A = "11111111-1111-4111-8111-111111111111"
B = "22222222-2222-4222-8222-222222222222"
DORMANT = "33333333-3333-4333-8333-333333333333"
RANDOM = "44444444-4444-4444-8444-444444444444"
PREMISE = ("A cyclist finds a lost dog in a rainy city, earns its trust, leads it home, "
           "and reunites it with its owner.")


class OfflineClient:
    def __init__(self, participants):
        self.participants = participants
        self.context = None
        self.response = None
        self.settings = None

    def __call__(self, settings):
        self.settings = settings
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def generate(self, messages, *, response_instruction=None, response_format=None):
        assert response_instruction is None
        assert response_format == {"type": "json_object"}
        await asyncio.sleep(0)  # Overlap requests without contacting a provider.
        self.system = messages[0]["content"]
        content = messages[1]["content"]
        self.context = json.loads(content if isinstance(content, str) else content[0]["text"])
        initial = "requestedTotalCount" in self.context
        count = self.context.get("requestedTotalCount", self.context.get("requestedCount"))
        items = []
        for index in range(count):
            item = {
                "scenePlan": "The cyclist shelters the dog before reuniting it with its owner.",
                "dialogue": "", "incomingState": "Rain falls.", "intendedOutgoingState": "The dog trusts the cyclist.",
                "relationshipToPrevious": None if initial and index == 0 else "new_shot_preserve_continuity",
                "cameraIntent": "Wide shot", "shotCount": 1,
                "participatingSubjectIds": self.participants,
                "referenceRecommendations": [], "memoryPatchAfter": {},
            }
            if not initial:
                item["planItemId"] = self.context["slotIds"][index]
            items.append(item)
        self.response = {"items" if initial else "slots": items, "warnings": []}
        if initial:
            self.response["sequenceContext"] = "A rainy city."
        return json.dumps(self.response)


def make_story(tmp_path, workspace_factory, operation, cast):
    store = ProjectStore(tmp_path)
    subjects = [{"id": sid, "number": index + 1} for index, sid in enumerate(cast)]
    project = store.create_project(
        workspace_factory(subjects=subjects, nextSubjectNumber=len(subjects) + 1),
        story_idea=PREMISE, story_memory=StoryContinuityMemory(),
    )
    for sid in cast:
        store.set_subject_narrative_name(project.project_id, sid, {A: "Cyclist", B: "Dog", DORMANT: "Dormant"}[sid])
    if operation != "initial":
        # Move all three records outside the local window. Only Cyclist and Dog
        # remain relevant through the premise; Dormant must never enter the enum.
        for _ in range(5):
            project = store.create_generation(project.project_id, workspace=workspace_factory())
    return store, project


async def run_operation(store, project, operation, client, reasoning="off"):
    llm = LLMSettings.model_validate({
        "modelId": "offline", "reasoningMode": reasoning,
        "endpoint": {"id": "offline", "displayName": "Offline", "baseUrl": "http://never.test/v1"},
    })
    if operation == "initial":
        return await build_initial_range(store, project.project_id,
                                        StoryInitialRangeRequest(requestedTotalCount=2, llm=llm), client)
    generations = store.read_story_snapshot(project.project_id).generations
    if operation == "next":
        return await plan_next(store, project.project_id,
                               StoryNextRequest(anchorGenerationId=generations[-1].generation_id,
                                                requestedCount=1, llm=llm), client)
    return await plan_insert(store, project.project_id,
                             StoryInsertRequest(upstreamGenerationId=generations[-2].generation_id,
                                                requestedCount=1, llm=llm), client)


def participant_schema(context):
    schema = context["responseSchema"]
    collection = "items" if "items" in schema["properties"] else "slots"
    ref = schema["properties"][collection]["items"]["$ref"].split("/")[-1]
    return schema["$defs"][ref]["properties"]["participatingSubjectIds"]


@pytest.mark.parametrize("operation", ["initial", "next", "insert"])
@pytest.mark.parametrize("reasoning", ["off", "on"])
@pytest.mark.parametrize("bounded", [False, True])
async def test_actual_request_and_valid_participants(tmp_path, workspace_factory, operation, reasoning, bounded):
    cast = [A, B, DORMANT] if bounded else []
    store, project = make_story(tmp_path, workspace_factory, operation, cast)
    ids = [B, A] if bounded else []
    client = OfflineClient(ids)
    proposal = await run_operation(store, project, operation, client, reasoning)
    expected = cast if operation == "initial" else cast[:2]
    assert client.context["availableCanonicalSubjectIds"] == expected
    field = participant_schema(client.context)
    if bounded:
        assert field["items"] == {"type": "string", "enum": expected}
    else:
        assert field["maxItems"] == 0
    assert client.settings.reasoning_mode == reasoning
    assert "S1/S2-style identifiers" in client.system
    assert "output [] even if Scene Plan/dialogue describes people, animals" in client.system
    items = proposal.items if operation == "initial" else proposal.slots
    assert items[-1].participating_subject_ids == ids
    assert items[-1].reference_recommendations == []


@pytest.mark.parametrize("operation", ["initial", "next", "insert"])
@pytest.mark.parametrize("bounded", [False, True])
@pytest.mark.parametrize("invalid", [["S1", "S2"], ["Subject 1"], ["Cyclist"], [RANDOM], [A, A], [None], [""]])
async def test_invalid_provider_ids_rejected(tmp_path, workspace_factory, operation, bounded, invalid):
    store, project = make_story(tmp_path, workspace_factory, operation, [A, B, DORMANT] if bounded else [])
    client = OfflineClient(invalid)
    with pytest.raises(LLMBackendError, match="participatingSubjectIds") as caught:
        await run_operation(store, project, operation, client)
    assert caught.value.category == "story_schema"


@pytest.mark.parametrize("operation", ["next", "insert"])
async def test_canonical_but_dormant_id_rejected(tmp_path, workspace_factory, operation):
    store, project = make_story(tmp_path, workspace_factory, operation, [A, B, DORMANT])
    with pytest.raises(LLMBackendError, match="participatingSubjectIds"):
        await run_operation(store, project, operation, OfflineClient([DORMANT]))


@pytest.mark.parametrize("base", [StoryInitialRangeResponse, StoryNextResponse, StoryInsertResponse])
def test_request_local_models_do_not_mutate_global_contract(base):
    original = deepcopy(base.model_json_schema(by_alias=True))
    model_a = story_participant_response_model(base, [A])
    schema_a = deepcopy(model_a.model_json_schema(by_alias=True))
    model_b = story_participant_response_model(base, [B])
    empty = story_participant_response_model(base, [])
    assert participant_schema({"responseSchema": schema_a})["items"]["enum"] == [A]
    assert participant_schema({"responseSchema": model_b.model_json_schema(by_alias=True)})["items"]["enum"] == [B]
    assert participant_schema({"responseSchema": empty.model_json_schema(by_alias=True)})["maxItems"] == 0
    assert model_a.model_json_schema(by_alias=True) == schema_a
    assert base.model_json_schema(by_alias=True) == original


async def test_concurrent_projects_do_not_share_allowlists(tmp_path, workspace_factory):
    a_store, a_project = make_story(tmp_path / "a", workspace_factory, "initial", [A])
    b_store, b_project = make_story(tmp_path / "b", workspace_factory, "initial", [B])
    clients = [OfflineClient([A]), OfflineClient([B])]
    await asyncio.gather(run_operation(a_store, a_project, "initial", clients[0]),
                         run_operation(b_store, b_project, "initial", clients[1]))
    for client, own, other in [(clients[0], A, B), (clients[1], B, A)]:
        model = story_participant_response_model(StoryInitialRangeResponse, client.context["availableCanonicalSubjectIds"])
        assert participant_schema(client.context)["items"]["enum"] == [own]
        altered = deepcopy(client.response)
        altered["items"][1]["participatingSubjectIds"] = [other]
        with pytest.raises(ValidationError):
            model.model_validate(altered)


def test_semantic_validation_remains_independent():
    with pytest.raises(ValueError, match="unavailable canonical Subject"):
        validate_participating_subjects([SimpleNamespace(participating_subject_ids=["S1", "S2"])], set(), label="Build Initial Range")
    with pytest.raises(ValueError, match="unique"):
        validate_participating_subjects([SimpleNamespace(participating_subject_ids=[A, A])], {A}, label="Plan Next")


def test_replan_and_revision_do_not_expose_participant_edits():
    for model in [StoryReplanResponse, story_revision_response_model([StoryRevisionScope.DIALOGUE])]:
        assert "participatingSubjectIds" not in json.dumps(model.model_json_schema(by_alias=True))


@pytest.mark.parametrize("operation", ["next", "insert"])
async def test_existing_cast_with_no_bounded_eligible_subjects(tmp_path, workspace_factory, operation):
    store, project = make_story(tmp_path, workspace_factory, operation, [DORMANT])
    client = OfflineClient([])
    await run_operation(store, project, operation, client)
    assert client.context["availableCanonicalSubjectIds"] == []
    assert participant_schema(client.context)["maxItems"] == 0
    with pytest.raises(LLMBackendError, match="participatingSubjectIds"):
        await run_operation(store, project, operation, OfflineClient([DORMANT]))
