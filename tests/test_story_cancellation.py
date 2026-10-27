"""Request-lifetime Story cancellation, without any live provider."""
import asyncio

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from miniconstruct.api import routes
from miniconstruct.api.request_lifetime import run_until_disconnect
from miniconstruct.llm.client import LLMBackendError, OpenAICompatibleClient
from miniconstruct.models.api import LLMSettings


class Connection:
    def __init__(self):
        self.messages = asyncio.Queue()
        self.waiting = asyncio.Event()
        self.closed = asyncio.Event()
        self.request = Request({"type": "http"}, self.receive)

    async def receive(self):
        self.waiting.set()
        try:
            return await self.messages.get()
        finally:
            self.closed.set()

    def disconnect(self):
        self.messages.put_nowait({"type": "http.disconnect"})


ROUTES = [
    (routes.propose_story_initial_range, "build_initial_range", "/initial-range"),
    (routes.propose_story_next, "plan_next", "/next"),
    (routes.propose_story_insert, "plan_insert", "/insert"),
    (routes.propose_story_replan, "plan_replan", "/replan"),
    (routes.revise_project_story_plan, "revise_story_plan", "/revise"),
    (routes.build_project_story_plan, "build_story_plan", "/build"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("route,service,path", ROUTES)
async def test_each_story_route_cancels_and_awaits_exact_operation(monkeypatch, route, service, path):
    connection = Connection()
    started, cleaned = asyncio.Event(), asyncio.Event()
    owned_tasks = []

    async def operation(*_args):
        owned_tasks.append(asyncio.current_task())
        started.set()
        try:
            await asyncio.Future()
        finally:
            await asyncio.sleep(0)  # Cleanup must be awaited before the route exits.
            cleaned.set()

    monkeypatch.setattr(routes, service, operation)
    endpoint = next(r for r in routes.router.routes if r.path.endswith("/projects/{project_id}/story-plan" + path))
    assert endpoint.dependant.request_param_name == "http_request"
    task = asyncio.create_task(route("project", object(), connection.request))
    await asyncio.wait_for(started.wait(), 1)
    connection.disconnect()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    assert cleaned.is_set()
    assert owned_tasks[0].cancelled()
    assert connection.closed.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "provider_error", "cancelled"])
async def test_completion_preserves_result_and_exception_and_closes_waiter(outcome):
    connection = Connection()
    result = object()
    error = LLMBackendError("ordinary provider failure", 504)

    async def operation():
        await connection.waiting.wait()
        if outcome == "provider_error":
            raise error
        if outcome == "cancelled":
            raise asyncio.CancelledError()
        return result

    pending = run_until_disconnect(connection.request, operation())
    if outcome == "success":
        assert await pending is result
    else:
        with pytest.raises(LLMBackendError if outcome == "provider_error" else asyncio.CancelledError) as caught:
            await pending
        if outcome == "provider_error":
            assert caught.value is error
    assert connection.closed.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("route,service,path", ROUTES)
async def test_story_route_preserves_provider_error_mapping(monkeypatch, route, service, path):
    async def operation(*_args):
        raise LLMBackendError("provider failure", 502)
    monkeypatch.setattr(routes, service, operation)
    with pytest.raises(HTTPException) as caught:
        await route("project", object(), Connection().request)
    assert caught.value.status_code == 502


@pytest.mark.asyncio
async def test_cancelling_route_owner_cleans_children():
    connection = Connection()
    started, cleaned = asyncio.Event(), asyncio.Event()
    async def operation():
        started.set()
        try:
            await asyncio.Future()
        finally:
            cleaned.set()
    task = asyncio.create_task(run_until_disconnect(connection.request, operation()))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned.is_set()
    assert connection.closed.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("streamed", [False, True])
async def test_disconnect_closes_exact_httpx_body_and_client(streamed):
    connection = Connection()
    started, closed = asyncio.Event(), asyncio.Event()

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            started.set()
            await asyncio.Future()
            yield b"unreachable"
        async def aclose(self):
            closed.set()

    settings = LLMSettings.model_validate({"baseUrl": "http://offline.test/v1", "model": "offline"})
    client = OpenAICompatibleClient(settings, httpx.MockTransport(lambda request: httpx.Response(200, stream=Body())))
    async def operation():
        async with client:
            method = client.generate_streamed if streamed else client.generate
            return await method([{"role": "user", "content": "offline"}], response_instruction=None)
    task = asyncio.create_task(run_until_disconnect(connection.request, operation()))
    await asyncio.wait_for(started.wait(), 1)
    connection.disconnect()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    assert closed.is_set()
    assert client.client.is_closed
