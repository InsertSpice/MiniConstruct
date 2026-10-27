"""Provider-free request, prompt, exact response, and signed draft checks."""
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient

from miniconstruct.app import app
from miniconstruct.llm.client import LLMBackendError
from miniconstruct.models.story_operations import (
    StoryShotGuidance, StoryInitialRangeRequest, StoryNextRequest, StoryInsertRequest, StoryReplanRequest,
)
from miniconstruct.story_operations import build_initial_range, plan_next, plan_insert, plan_replan, authority_token
from test_story_initial_range import OfflineInitialRangeClient, saved_story
from test_story_next import OfflineNextClient, create_story, LLM
from test_story_insert import OfflineInsertClient, make_story as make_insert_story
from test_story_replan import OfflineReplanClient, make_story as make_replan_story


@pytest.mark.parametrize("model,body", [
    (StoryInitialRangeRequest, {"requestedTotalCount": 2}),
    (StoryNextRequest, {"anchorGenerationId": "anchor", "requestedCount": 2}),
    (StoryInsertRequest, {"upstreamGenerationId": "upstream", "requestedCount": 2}),
    (StoryReplanRequest, {"startGenerationId": "start", "endGenerationId": "end"}),
])
def test_request_guidance_validation(model, body):
    assert model.model_validate({**body, "llm": LLM}).shot_guidance is None
    for guidance in ({"mode": "prefer"}, {"mode": "exact"},
                     {"mode": "prefer", "count": 0}, {"mode": "exact", "count": -1},
                     {"mode": "auto", "count": 4}, {"mode": "many", "count": 4},
                     {"mode": "exact", "count": "4"}, {"mode": "exact", "count": 4.0}):
        with pytest.raises(ValidationError):
            model.model_validate({**body, "llm": LLM, "shotGuidance": guidance})
    for mode in ("prefer", "exact"):
        assert model.model_validate({**body, "llm": LLM,
                                     "shotGuidance": {"mode": mode, "count": 17}}).shot_guidance.count == 17


@pytest.mark.parametrize("path,body", [
    ("initial-range", {"requestedTotalCount": 2}),
    ("next", {"anchorGenerationId": "anchor", "requestedCount": 2}),
    ("insert", {"upstreamGenerationId": "upstream", "requestedCount": 2}),
    ("replan", {"startGenerationId": "start", "endGenerationId": "end"}),
])
def test_http_rejects_invalid_guidance_before_provider(path, body):
    payload = {**body, "llm": LLM.model_dump(mode="json", by_alias=True)}
    with TestClient(app) as client:
        for guidance in ({"mode": "prefer"}, {"mode": "exact"},
                         {"mode": "prefer", "count": 0}, {"mode": "unknown", "count": 4}):
            response = client.post(f"/api/projects/test-project/story-plan/{path}",
                                   json={**payload, "shotGuidance": guidance})
            assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["initial", "next", "insert", "replan"])
@pytest.mark.parametrize("mode,returned", [(None, 3), ("prefer", 3), ("prefer", 4),
                                          ("prefer", 5), ("exact", 4), ("exact", 2)])
async def test_guidance_production_request_and_response(tmp_path, workspace_factory, operation, mode, returned):
    guidance = {"mode": mode, "count": 4} if mode else None
    def transform(response, _context):
        for item in response["items" if operation in {"initial", "replan"} else "slots"]:
            item["shotCount"] = returned

    if operation == "initial":
        store, project = saved_story(tmp_path, workspace_factory)
        client = OfflineInitialRangeClient(transform)
        request = StoryInitialRangeRequest(requestedTotalCount=2, llm=LLM, shotGuidance=guidance)
        invoke = lambda: build_initial_range(store, project.project_id, request, client)
    elif operation == "next":
        store, project = create_story(tmp_path, workspace_factory, length=4)
        client = OfflineNextClient(transform)
        request = StoryNextRequest(anchorGenerationId=project.active_generation_id, requestedCount=2,
                                   llm=LLM, shotGuidance=guidance)
        invoke = lambda: plan_next(store, project.project_id, request, client)
    elif operation == "insert":
        store, project, _, _, upstream = make_insert_story(tmp_path, workspace_factory)
        client = OfflineInsertClient(transform)
        request = StoryInsertRequest(upstreamGenerationId=upstream, requestedCount=2,
                                     llm=LLM, shotGuidance=guidance)
        invoke = lambda: plan_insert(store, project.project_id, request, client)
    else:
        store, project, _, _, ids = make_replan_story(tmp_path, workspace_factory)
        client = OfflineReplanClient(transform)
        request = StoryReplanRequest(startGenerationId=ids[2], endGenerationId=ids[3],
                                     llm=LLM, shotGuidance=guidance)
        invoke = lambda: plan_replan(store, project.project_id, request, client)

    if mode == "exact" and returned != 4:
        with pytest.raises(LLMBackendError, match=r"exact shot guidance for Generation \d+: expected 4 shots, received 2") as exc:
            await invoke()
        assert exc.value.category == "story_schema"
    else:
        draft = await invoke()
        items = draft.items if operation in {"initial", "replan"} else draft.slots
        assert [item.shot_count for item in items] == [returned, returned]
        assert draft.authority.shot_guidance == request.shot_guidance
        assert authority_token(draft.authority) == draft.authority_token
        if mode:
            altered = draft.authority.model_copy(update={"shot_guidance": StoryShotGuidance(mode="exact", count=5)})
            assert authority_token(altered) != draft.authority_token
    assert len(client.calls) == 1
    system = client.calls[0][0]["content"]
    assert "fewest shots" in system
    if mode is None:
        assert "Shot-count preference" not in system and "Every TARGET Generation must use exactly" not in system
    elif mode == "prefer":
        assert "prefer about 4 purposeful" in system and "Vary the count" in system
    else:
        assert "shotCount = 4" in system and "exactly 4 purposeful" in system
