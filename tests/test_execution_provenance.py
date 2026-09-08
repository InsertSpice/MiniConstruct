"""Response provenance through Generate, streaming Generate, Repair and Revision."""
import json

import pytest
import httpx

from miniconstruct.api import routes
from miniconstruct.execution_authoring import execution_authoring_fingerprint
from miniconstruct.models.api import GenerationRequest, RepairRequest
from miniconstruct.llm.client import LLMStreamEvent
from miniconstruct.generation import generate_workspace_prompts
from test_validator import VALID_BASE
from test_revision import revision_request, decode_event


class FakeWriter:
    text = VALID_BASE

    def __init__(self, _): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *_): pass
    async def generate(self, _): return self.text
    async def stream_events(self, _): yield LLMStreamEvent("content", self.text)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError("No model/Comfy transport")
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    monkeypatch.setattr(routes, "OpenAICompatibleClient", FakeWriter)


def request(workspace):
    return GenerationRequest.model_validate({"workspace": workspace.model_dump(mode="json", by_alias=True),
        "llm": {"baseUrl": "http://unused.test/v1", "model": "synthetic"}})


@pytest.mark.asyncio
async def test_generate_response_provenance(workspace_factory, monkeypatch):
    req = request(workspace_factory())
    async def generate(body): return await generate_workspace_prompts(body, FakeWriter)
    monkeypatch.setattr(routes, "generate_workspace_prompts", generate)
    first, second = await routes.generate(req), await routes.generate(req)
    assert first == second
    assert first.variations[0].source_fingerprint == execution_authoring_fingerprint(req.workspace)
    assert first.model_dump(mode="json", by_alias=True)["variations"][0]["sourceFingerprint"]


@pytest.mark.asyncio
async def test_generate_stream_provenance_reflects_unsaved_request(workspace_factory):
    w = workspace_factory(durationSeconds=9)
    events = [decode_event(e) async for e in routes.stream_generation_events(request(w), FakeWriter)]
    complete = next(data for event, data in events if event == "complete")
    assert complete["sourceFingerprint"] == execution_authoring_fingerprint(w)
    assert complete["sourceFingerprint"] != execution_authoring_fingerprint(workspace_factory())
    assert all("sourceFingerprint" not in data for event, data in events if event == "delta")


@pytest.mark.parametrize("prompt", [VALID_BASE, VALID_BASE.replace("[Shot 1]", "[Shot 3]"), "invalid H3"])
@pytest.mark.asyncio
async def test_repair_all_return_paths_carry_provenance(workspace_factory, prompt):
    req = RepairRequest.model_validate({**request(workspace_factory()).model_dump(by_alias=True), "prompt": prompt, "findings": []})
    result = await routes.repair(req)
    assert result.source_fingerprint == execution_authoring_fingerprint(req.workspace)


@pytest.mark.asyncio
async def test_revision_complete_carries_candidate_snapshot(workspace_factory):
    class RevisionWriter(FakeWriter): text = "[Shot 2] The cyclist accelerates."
    req = revision_request(workspace_factory(durationSeconds=9))
    events = [decode_event(e) async for e in routes.stream_revision_events(req, RevisionWriter)]
    complete = next(data for event, data in events if event == "complete")
    assert complete["sourceFingerprint"] == execution_authoring_fingerprint(req.workspace)
    assert "accelerates" in complete["candidatePrompt"]
