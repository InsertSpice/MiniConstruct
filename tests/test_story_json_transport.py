"""Provider-free regressions for structured Story JSON transport and failures."""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from fastapi import HTTPException

from miniconstruct.api.routes import _http_error
from miniconstruct.llm.client import LLMBackendError, OpenAICompatibleClient
from miniconstruct.models.api import LLMSettings, PlanningInferenceRequest
from miniconstruct.models.planning import GenerationPlanning
from miniconstruct.models.story_operations import (
    StoryInitialRangeRequest, StoryInsertRequest, StoryNextRequest, StoryReplanRequest,
)
from miniconstruct.models.story_planning import StoryBuildRequest, StoryRevisionRequest
from miniconstruct.planning import infer_plan
from miniconstruct.story_operations import build_initial_range, plan_insert, plan_next, plan_replan
from miniconstruct.story_planning import build_story_plan, initial_story_draft, revise_story_plan
from test_story_initial_range import OfflineInitialRangeClient, saved_story


LLM = LLMSettings.model_validate({
    "modelId": "offline",
    "endpoint": {"id": "offline", "displayName": "Offline", "baseUrl": "http://never.test/v1"},
})


class FixedTextClient:
    def __init__(self, text):
        self.text = text
        self.calls = 0

    def __call__(self, _settings):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def generate(self, _messages, *, response_instruction=None):
        assert response_instruction is None
        self.calls += 1
        return self.text


@pytest.mark.asyncio
async def test_malformed_fenced_related_subject_ids_reports_location_and_retains_exact_text(tmp_path, workspace_factory):
    store, project = saved_story(tmp_path, workspace_factory)
    raw = (
        '```json\n{\n  "items": [{"memoryPatchAfter":{"factUpserts":[{"relatedSubjectIds":[\n'
        'es"subject-8f622fd4-ea1e-4239-9be8-6fce6b74a1d8"]}]}}]\n}\n```'
    )
    client = FixedTextClient(raw)

    with pytest.raises(LLMBackendError) as caught:
        await build_initial_range(store, project.project_id,
            StoryInitialRangeRequest(requestedTotalCount=3, llm=LLM), client)

    error = caught.value
    assert error.category == "story_json"
    assert "line 4, column" in str(error)
    assert "near character" in str(error)
    assert error.raw_response == raw
    assert client.calls == 1
    assert "subject-8f622fd4" not in str(error)


@pytest.mark.asyncio
async def test_corrected_fenced_nested_memory_response_proceeds_with_three_total_generations(tmp_path, workspace_factory):
    store, project = saved_story(tmp_path, workspace_factory)

    class CorrectedFencedClient(OfflineInitialRangeClient):
        async def generate(self, messages, *, response_instruction=None):
            raw = json.loads(await super().generate(messages, response_instruction=response_instruction))
            raw["items"][0]["memoryPatchAfter"]["factUpserts"][0]["relatedSubjectIds"] = []
            return "```json\n" + json.dumps(raw) + "\n```"

    client = CorrectedFencedClient()
    proposal = await build_initial_range(store, project.project_id,
        StoryInitialRangeRequest(requestedTotalCount=3, llm=LLM), client)

    assert len(proposal.items) == 3
    assert [item.existing_generation_id for item in proposal.items] == [project.active_generation_id, None, None]
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_valid_json_with_invalid_story_enum_reports_schema_path_and_retains_text(tmp_path, workspace_factory):
    store, project = saved_story(tmp_path, workspace_factory)

    def invalid_enum(result, _context):
        result["items"][1]["relationshipToPrevious"] = "not_an_existing_relationship"

    client = OfflineInitialRangeClient(invalid_enum)
    raw = None
    original_generate = client.generate

    async def capture(messages, *, response_instruction=None):
        nonlocal raw
        raw = await original_generate(messages, response_instruction=response_instruction)
        return raw

    client.generate = capture
    with pytest.raises(LLMBackendError) as caught:
        await build_initial_range(store, project.project_id,
            StoryInitialRangeRequest(requestedTotalCount=3, llm=LLM), client)

    error = caught.value
    assert error.category == "story_schema"
    assert "items.1.relationshipToPrevious: enum" in str(error)
    assert error.raw_response == raw
    assert client.calls and len(client.calls) == 1


@pytest.mark.asyncio
async def test_all_six_story_operations_send_json_mode_in_actual_chat_payloads(tmp_path, workspace_factory):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    def client_factory(settings):
        return OpenAICompatibleClient(settings, transport=httpx.MockTransport(handler))

    initial_store, initial_project = saved_story(tmp_path / "sole-g1", workspace_factory)
    with pytest.raises(LLMBackendError):
        await build_initial_range(initial_store, initial_project.project_id,
            StoryInitialRangeRequest(requestedTotalCount=3, llm=LLM), client_factory)

    store, project = saved_story(tmp_path / "range", workspace_factory)
    offline_initial = await build_initial_range(store, project.project_id,
        StoryInitialRangeRequest(requestedTotalCount=3, llm=LLM), OfflineInitialRangeClient())
    store.apply_story_initial_range(project.project_id, offline_initial)
    snapshot = store.read_story_snapshot(project.project_id)
    generation_ids = [generation.generation_id for generation in snapshot.generations]

    operations = [
        lambda: build_story_plan(store, project.project_id,
            StoryBuildRequest(idea="Continue the delivery.", desiredGenerationCount=3, llm=LLM), client_factory),
        lambda: plan_next(store, project.project_id,
            StoryNextRequest(anchorGenerationId=generation_ids[-1], requestedCount=1, llm=LLM), client_factory),
        lambda: plan_insert(store, project.project_id,
            StoryInsertRequest(upstreamGenerationId=generation_ids[0], requestedCount=1, llm=LLM), client_factory),
        lambda: plan_replan(store, project.project_id,
            StoryReplanRequest(startGenerationId=generation_ids[1], endGenerationId=generation_ids[2], llm=LLM), client_factory),
    ]
    draft = initial_story_draft(snapshot, 3)
    operations.append(lambda: revise_story_plan(store, project.project_id,
        StoryRevisionRequest(draft=draft, selectedPlanItemIds=[draft.items[1].plan_item_id],
                             scopes=["scene_plan"], instruction="Tighten the action.", llm=LLM), client_factory))

    for operation in operations:
        with pytest.raises(LLMBackendError):
            await operation()

    from miniconstruct.story_planning import SEQUENCE_CONTEXT_POLICY
    assert all(SEQUENCE_CONTEXT_POLICY in payload["messages"][0]["content"] for payload in requests)
    assert len(requests) == 6
    assert all(payload["response_format"] == {"type": "json_object"} for payload in requests)


@pytest.mark.asyncio
async def test_ordinary_h3_inference_payload_does_not_use_json_mode(workspace_factory):
    requests = []
    plan = {
        "creativeRequest": "Continue the action.", "incomingState": "At the doorway.",
        "intendedOutgoingState": "Inside the room.", "relationshipToPrevious": "independent",
        "cameraIntent": "Wide shot", "referenceRecommendations": [],
    }

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(plan)}}]})

    previous_workspace = workspace_factory()
    previous = SimpleNamespace(ordinal=1, workspace=previous_workspace, incoming_state="", intended_outgoing_state="",
                               planning=GenerationPlanning())
    request = PlanningInferenceRequest(workspace=workspace_factory(), llm=LLM)
    result = await infer_plan(previous, request,
        lambda settings: OpenAICompatibleClient(settings, transport=httpx.MockTransport(handler)))

    assert result.creative_request == plan["creativeRequest"]
    assert len(requests) == 1
    assert "response_format" not in requests[0]


def test_rejected_raw_response_api_error_contains_only_diagnostic_and_provider_text():
    raw = "```json\n{broken\n```"
    response = _http_error(LLMBackendError(
        "Story Planner returned malformed JSON at line 2, column 2 (near character 9).",
        category="story_json", raw_response=raw,
    ))
    assert isinstance(response, HTTPException)
    body = response.detail
    assert response.status_code == 502
    assert body == {
        "message": "Story Planner returned malformed JSON at line 2, column 2 (near character 9).",
        "category": "story_json", "rawResponse": raw,
    }
    assert "request" not in body and "apiKey" not in body


@pytest.mark.asyncio
async def test_backend_json_mode_rejection_is_not_retried():
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(400, json={"error": {"message": "response_format is unsupported"}})

    async with OpenAICompatibleClient(LLM, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(LLMBackendError) as caught:
            await client.generate([{"role": "user", "content": "{}"}], response_instruction=None,
                                  response_format={"type": "json_object"})

    assert caught.value.upstream_status == 400
    assert len(calls) == 1
    assert calls[0]["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
@pytest.mark.parametrize("streamed", [False, True])
async def test_json_mode_preserves_exact_model_text_for_diagnostics(streamed):
    raw = ' \n```json\n{"items": [broken]}\n``` \r\n'
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": raw}}]})

    async with OpenAICompatibleClient(LLM, transport=httpx.MockTransport(handler)) as client:
        if streamed:
            result = await client.generate_streamed([{"role": "user", "content": "{}"}],
                response_instruction=None, response_format={"type": "json_object"})
        else:
            result = await client.generate([{"role": "user", "content": "{}"}],
                response_instruction=None, response_format={"type": "json_object"})

    assert result == raw
    assert calls[0]["response_format"] == {"type": "json_object"}
