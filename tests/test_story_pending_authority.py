"""Paused, provider-free checks of the production Story proposal paths."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from miniconstruct.api import routes
from miniconstruct.app import app
from miniconstruct.models.story_operations import StoryNextRequest, StoryInsertRequest, StoryReplanRequest
from miniconstruct.models.story_memory import StoryContinuityMemory
from miniconstruct.project_store import StaleProjectSnapshotError
from miniconstruct.story_operations import plan_next, plan_insert, plan_replan
from test_story_next import OfflineNextClient, create_story as next_story, LLM
from test_story_insert import OfflineInsertClient, make_story as insert_story
from test_story_replan import OfflineReplanClient, make_story as replan_story


class PausedProvider:
    """Pause the actual client call after the service has captured authority."""

    def __init__(self, client):
        self.client = client
        self.started = asyncio.Event()
        self.resume = asyncio.Event()

    def __call__(self, settings):
        self.client(settings)
        return self

    async def __aenter__(self):
        await self.client.__aenter__()
        return self

    async def __aexit__(self, *args):
        return await self.client.__aexit__(*args)

    async def generate(self, messages, *, response_instruction=None):
        self.started.set()
        await self.resume.wait()
        return await self.client.generate(messages, response_instruction=response_instruction)


def saved_bytes(store, project_id):
    return (store.root / f"project-{project_id}" / "project.json").read_bytes()


def change_idea(store, project_id):
    current = store.load_project(project_id)
    store.save_project(project_id, current.workspace, story_idea="A changed canonical premise.")


@pytest.mark.parametrize("operation", ["replan", "next", "insert"])
@pytest.mark.parametrize("mutation", ["none", "relevant", "structure"])
async def test_pending_bounded_story_proposal(tmp_path, workspace_factory, operation, mutation):
    if operation == "replan":
        store, project, _upstream, _downstream, ids = replan_story(tmp_path, workspace_factory)
        client = PausedProvider(OfflineReplanClient())
        call = plan_replan(store, project.project_id, StoryReplanRequest(
            startGenerationId=ids[2], endGenerationId=ids[3], instruction="Tense beats", llm=LLM), client)
    elif operation == "next":
        store, project = next_story(tmp_path, workspace_factory, length=4)
        client = PausedProvider(OfflineNextClient())
        call = plan_next(store, project.project_id, StoryNextRequest(
            anchorGenerationId=project.active_generation_id, requestedCount=1, llm=LLM), client)
    else:
        store, project, _authoritative, _terminal, anchor_id = insert_story(tmp_path, workspace_factory)
        client = PausedProvider(OfflineInsertClient())
        call = plan_insert(store, project.project_id, StoryInsertRequest(
            upstreamGenerationId=anchor_id, requestedCount=1, llm=LLM), client)
    before = saved_bytes(store, project.project_id)
    task = asyncio.create_task(call)
    await client.started.wait()
    if mutation == "relevant":
        change_idea(store, project.project_id)
    elif mutation == "structure":
        store.create_generation(project.project_id, insert_index={"replan": 2, "insert": 3}.get(operation))
    after_mutation = saved_bytes(store, project.project_id)
    client.resume.set()
    if mutation in {"relevant", "structure"}:
        with pytest.raises(StaleProjectSnapshotError):
            await task
    else:
        proposal = await task
        assert proposal.authority_token
        assert before == after_mutation
    assert saved_bytes(store, project.project_id) == after_mutation


@pytest.mark.parametrize("mutation", ["noop_save", "selection", "execution_sidecar"])
async def test_pending_replan_ignores_unrelated_canonical_change(tmp_path, workspace_factory, mutation):
    store, project, _upstream, _downstream, ids = replan_story(tmp_path, workspace_factory)
    client = PausedProvider(OfflineReplanClient())
    task = asyncio.create_task(plan_replan(store, project.project_id, StoryReplanRequest(
        startGenerationId=ids[2], endGenerationId=ids[3], llm=LLM), client))
    await client.started.wait()
    starting = store.read_story_replan_snapshot(project.project_id, ids[2], ids[3]).fingerprint
    if mutation == "noop_save":
        store._now = lambda: "2099-01-01T00:00:00.000Z"
        current = store.load_project(project.project_id)
        store.save_project(project.project_id, current.workspace)
    elif mutation == "selection":
        store.activate_generation(project.project_id, ids[0])
    else:
        from miniconstruct.execution_routing import GenerationRouting
        store.set_execution_routing(project.project_id, ids[2], GenerationRouting())
    assert store.read_story_replan_snapshot(project.project_id, ids[2], ids[3]).fingerprint == starting
    after_mutation = saved_bytes(store, project.project_id)
    client.resume.set()
    proposal = await task
    assert proposal.authority_token
    assert saved_bytes(store, project.project_id) == after_mutation
    store.apply_story_plan_replan(project.project_id, proposal)


async def test_pending_replan_rejects_target_memory_edit_without_writing(tmp_path, workspace_factory):
    store, project, _upstream, _downstream, ids = replan_story(tmp_path, workspace_factory)
    client = PausedProvider(OfflineReplanClient())
    task = asyncio.create_task(plan_replan(store, project.project_id, StoryReplanRequest(
        startGenerationId=ids[2], endGenerationId=ids[3], llm=LLM), client))
    await client.started.wait()
    selected = store.activate_generation(project.project_id, ids[2])
    store.save_project(project.project_id, selected.workspace, story_memory_generation_id=ids[2],
                       story_memory=StoryContinuityMemory(recentSummary="A newly saved target memory."))
    after_edit = saved_bytes(store, project.project_id)
    client.resume.set()
    with pytest.raises(StaleProjectSnapshotError, match="context changed"):
        await task
    assert saved_bytes(store, project.project_id) == after_edit


def test_replan_http_conflict_exposes_only_safe_category(monkeypatch):
    async def stale(*_args):
        raise StaleProjectSnapshotError("Replan Range context changed; relevant Story authority differs; plan again",
                                        category="story_authority_changed")
    monkeypatch.setattr(routes, "plan_replan", stale)
    body = StoryReplanRequest(startGenerationId="11111111-1111-4111-8111-111111111111",
                              endGenerationId="22222222-2222-4222-8222-222222222222", llm=LLM)
    with TestClient(app) as client:
        response = client.post("/api/projects/test-project/story-plan/replan",
                               json=body.model_dump(mode="json", by_alias=True))
    assert response.status_code == 409
    assert response.json()["detail"] == {
        "category": "story_authority_changed",
        "message": "Replan Range context changed; relevant Story authority differs; plan again",
    }
