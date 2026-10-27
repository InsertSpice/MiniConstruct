"""Offline acceptance of the production Story Revision patch and context boundary."""
from copy import deepcopy
import json
from uuid import uuid4

import httpx
import pytest

from miniconstruct.llm.client import LLMBackendError, OpenAICompatibleClient
from miniconstruct.models.story_planning import (
    SCOPE_FIELDS, StoryPlanItem, StoryPlanProposal, StoryReferenceRecommendation, StoryRevisionRequest,
)
from miniconstruct.models.workspace import validate_authoring_workspace
from miniconstruct.project_store import ProjectStore, StaleProjectSnapshotError
from miniconstruct.story_planning import initial_story_draft, revise_story_plan
from test_story_planning import PatchModel, build, files, revision, saved, settings


async def run(saved, request, model):
    return await revise_story_plan(saved[0], saved[1].project_id, request, model)


@pytest.mark.parametrize("fault,match", [
    ("missing", "Missing selected"), ("duplicate", "Duplicate"),
    ("unknown", "Unknown"), ("unselected", "unselected planItemId"),
    ("extra", "extra_forbidden"), ("omitted", "missing"),
    ("null", "string_type"), ("number", "string_type"), ("list", "string_type"),
    ("too_long", "string_too_long"), ("ordinal", "extra_forbidden"),
    ("alias", "extra_forbidden"), ("warnings", "extra_forbidden"), ("full_draft", "extra_forbidden"),
])
async def test_patch_rejects_bad_coverage_authority_and_values_atomically(saved, fault, match):
    draft = await build(saved)
    request = revision(draft, selected=[draft.items[i].plan_item_id for i in (0, 2)], scopes=["dialogue"])
    before, disk = draft.model_dump(), files(saved[0])
    def corrupt(payload):
        patch = payload["patches"][0]
        if fault == "missing": payload["patches"].pop()
        if fault == "duplicate": payload["patches"][1] = deepcopy(patch)
        if fault == "unknown": patch["planItemId"] = str(uuid4())
        if fault == "unselected": patch["planItemId"] = draft.items[1].plan_item_id
        if fault == "extra": patch["cameraIntent"] = "Unauthorized"
        if fault == "omitted": patch.pop("dialogue")
        if fault == "null": patch["dialogue"] = None
        if fault == "number": patch["dialogue"] = 3
        if fault == "list": patch["dialogue"] = []
        if fault == "too_long": patch["dialogue"] = "a" * 30001
        if fault == "ordinal": patch["ordinal"] = 1
        if fault == "alias": patch["plan_item_id"] = patch.pop("planItemId")
        if fault == "warnings": payload["consistencyWarnings"] = []
        if fault == "full_draft": payload.clear(); payload.update(draft.model_dump(by_alias=True))
    with pytest.raises(LLMBackendError, match=match):
        await run(saved, request, PatchModel(corrupt))
    assert draft.model_dump() == before and files(saved[0]) == disk


@pytest.mark.parametrize("scope", list(SCOPE_FIELDS))
@pytest.mark.parametrize("omit", [False, True])
async def test_core_scopes_remain_required_when_missing(saved, scope, omit):
    draft = await build(saved)
    field = SCOPE_FIELDS[scope]
    alias = StoryPlanItem.model_fields[field].alias or field
    request = revision(draft, scopes=[scope])
    def change(payload):
        if omit:
            payload["patches"][0].pop(alias)
        else:
            payload["patches"][0][alias] = "continue_previous_shot" if scope == "relationship_to_previous" else ""
    model = PatchModel(change)
    if omit:
        with pytest.raises(LLMBackendError, match="missing"):
            await run(saved, request, model)
    else:
        result = await run(saved, request, model)
        assert getattr(result.items[1], field) == ("continue_previous_shot" if scope == "relationship_to_previous" else "")
        assert result.items[0] == draft.items[0]
        assert result.items[2:] == draft.items[2:]
        for name in StoryPlanItem.model_fields:
            if name != field:
                assert getattr(result.items[1], name) == getattr(draft.items[1], name)
    schema = model.context["responseSchema"]
    patch = schema["$defs"]["StoryRevisionPatch"]
    assert schema["additionalProperties"] is False and patch["additionalProperties"] is False
    assert set(schema["properties"]) == {"patches"}
    expected = {"planItemId", alias} | ({"shotCount"} if scope == "camera_intent" else set())
    assert set(patch["properties"]) == set(patch["required"]) == expected
    if scope != "relationship_to_previous":
        original = StoryPlanItem.model_json_schema(by_alias=True)["properties"][alias]
        assert patch["properties"][alias]["maxLength"] == original["maxLength"]
    assert len(json.dumps(schema)) < len(json.dumps(StoryPlanProposal.model_json_schema()))


async def test_reordered_multi_item_multi_scope_patches_preserve_all_other_state(saved):
    draft = await build(saved)
    draft.items[0].dialogue = "  Exact\nwords. "
    draft.sequence_context = "  Manual global context. "
    request = revision(draft, selected=[draft.items[i].plan_item_id for i in (0, 2)],
                       scopes=["dialogue", "camera_intent"])
    def change(payload):
        for index, patch in enumerate(payload["patches"]):
            patch.update(dialogue="" if index == 0 else "Alice: Leave.", cameraIntent=f"Camera {index}")
        payload["patches"].reverse()
    result = await run(saved, request, PatchModel(change, wrapper=lambda raw: f"```json\n{raw}\n```"))
    assert result.items[0].dialogue == "" and result.items[2].dialogue == "Alice: Leave."
    assert result.items[0].camera_intent == "Camera 0" and result.items[2].camera_intent == "Camera 1"
    for new, old in zip(result.items, draft.items):
        allowed = {"dialogue", "camera_intent"} if old.plan_item_id in request.selected_plan_item_ids else set()
        assert all(getattr(new, f) == getattr(old, f) for f in StoryPlanItem.model_fields if f not in allowed)
    assert result.sequence_context == draft.sequence_context


@pytest.mark.parametrize("index,value,valid", [(0, None, True), (0, "independent", False),
    (1, None, False), (1, "teleport", False), (1, "match_previous_ending_frame", True)])
async def test_relationship_patch_cannot_bypass_full_draft_structure(saved, index, value, valid):
    draft = await build(saved)
    request = revision(draft, selected=[draft.items[index].plan_item_id], scopes=["relationship_to_previous"])
    model = PatchModel(lambda p: p["patches"][0].update(relationshipToPrevious=value))
    if valid:
        assert (await run(saved, request, model)).items[index].relationship_to_previous == value
    else:
        with pytest.raises(LLMBackendError):
            await run(saved, request, model)


@pytest.mark.parametrize("scope", ["incoming_state", "intended_outgoing_state", "dialogue"])
@pytest.mark.parametrize("selection,window", [([0], [0, 1]), ([3], [2, 3]), ([1, 2], [0, 1, 2, 3])])
async def test_neighbor_windows_cover_endpoints_and_merge_overlap(saved, scope, selection, window):
    draft = await build(saved)
    request = revision(draft, selected=[draft.items[i].plan_item_id for i in selection], scopes=[scope])
    model = PatchModel()
    result = await run(saved, request, model)
    assert [item["planItemId"] for item in model.context["contextItems"]] == [draft.items[i].plan_item_id for i in window]
    assert [item["ordinal"] for item in model.context["contextItems"]] == [i + 1 for i in window]
    assert result == draft  # No invented changes/warnings on an explicit no-op.


async def test_snapshot_change_during_patch_call_is_rejected(saved):
    draft = await build(saved)
    def concurrent_edit(_):
        saved[0].save_project(saved[1].project_id, saved[1].workspace, sequence_context="Changed during call")
    with pytest.raises(StaleProjectSnapshotError):
        await run(saved, revision(draft), PatchModel(concurrent_edit))


@pytest.mark.parametrize("scope,vision,planner_vision,expected_images", [("dialogue", True, True, 0),
    ("camera_intent", True, True, 4), ("incoming_state", True, True, 4),
    ("camera_intent", True, False, 0), ("intended_outgoing_state", False, True, 0)])
async def test_real_http_messages_scope_visual_sources_and_recommended_pairs(tmp_path, workspace_factory,
                                                                          image_asset_factory, scope, vision, planner_vision, expected_images):
    store = ProjectStore(tmp_path)
    for index in range(5):
        assets = [image_asset_factory(asset_id=f"picture-{index}")]
        if index == 4:
            assets.append(image_asset_factory(asset_id="unrelated-remote-picture"))
        workspace = validate_authoring_workspace({**workspace_factory().model_dump(by_alias=True),
                                                  "assets": [a.model_dump(by_alias=True) for a in assets]})
        if index == 0:
            project = store.create_project(workspace)
        else:
            store.create_generation(project.project_id, workspace=workspace)
    snapshot = store.read_story_snapshot(project.project_id)
    draft = initial_story_draft(snapshot, 5)
    draft.items[1].reference_recommendations = [StoryReferenceRecommendation(
        sourceGenerationId=snapshot.generations[4].generation_id, assetId="picture-4", recommendation="Use identity",
    )]
    request = revision(draft, scopes=[scope])
    request.llm = settings(vision).model_copy(update={"story_planner_vision": planner_vision})
    captured = []
    def endpoint(http_request):
        payload = json.loads(http_request.content)
        captured.append(payload)
        content = payload["messages"][1]["content"]
        context = json.loads(content[0]["text"] if isinstance(content, list) else content)
        alias = context["editableFields"][0]
        patch = {"planItemId": draft.items[1].plan_item_id, alias: "Revised"}
        if scope == "camera_intent": patch["shotCount"] = 2
        raw = json.dumps({"patches": [patch]})
        return httpx.Response(200, json={"choices": [{"message": {"content": raw}}]})
    result = await revise_story_plan(store, project.project_id, request,
        lambda llm: OpenAICompatibleClient(llm, transport=httpx.MockTransport(endpoint)))
    assert len(captured) == 1
    content = captured[0]["messages"][1]["content"]
    parts = content if isinstance(content, list) else []
    assert len([p for p in parts if p["type"] == "image_url"]) == expected_images
    context = json.loads(parts[0]["text"] if parts else content)
    assert "draft" not in context and "creativeControls" not in str(context)
    assert snapshot.generations[3].generation_id not in str(context)
    assert "unrelated-remote-picture" not in str(context)
    assert result.items[1].reference_recommendations == draft.items[1].reference_recommendations
    assert result.items[0] == draft.items[0] and result.items[2:] == draft.items[2:]
    if scope == "dialogue":
        assert len(context["sourceGenerations"]) == 3
        assert "assets" not in str(context) and "referenceRecommendations" not in str(context)
    else:
        assert len(context["sourceGenerations"]) == 4
        assert [a["id"] for a in context["sourceGenerations"][-1]["assets"]] == ["picture-4"]
