"""Exact retry uses saved attempt authority and fake ComfyUI transport only."""
import json
from threading import Event, Thread

import httpx
import pytest

from miniconstruct import execution as ex, execution_send as send
from miniconstruct.execution_state import ManualEditAdoptionWrite
from miniconstruct.models.takes import RendererJobState
from test_execution_send import env, recorder, no_live, mapping, capacity_profile  # noqa: F401


def source_attempt(env, status="failed", profile=None, monkeypatch=None):
    store, config, _, _, project, body = env
    if profile is not None:
        request = body(f=profile)
    else:
        request = body()
    calls = []
    first = send.send_generation(request, store=store, config=config, transport=recorder(calls))
    source = store.update_render_job(project.project_id, project.active_generation_id,
        first.render_submission_id, RendererJobState(status=status))
    return source, calls


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_retry_new_attempt_lineage_and_no_second_retry(env, status):
    store, config, _, _, project, _ = env
    source, calls = source_attempt(env, status)
    result = send.retry_generation(project.project_id, project.active_generation_id, source.id,
        store=store, config=config, transport=recorder(calls))
    assert result.retry_of_submission_id == source.id
    assert result.prompt_id != source.comfy_prompt_id
    assert result.render_submission_id != source.id
    assert store.render_submission(project.project_id, project.active_generation_id, source.id).job.status == status
    child = store.render_submission(project.project_id, project.active_generation_id, result.render_submission_id)
    assert child.job.status == "queued" and child.retry_of_submission_id == source.id
    assert child.execution_fingerprint == source.execution_fingerprint
    assert source.renderer_seed is None and result.renderer_seed is None
    assert len([r for r in calls if r.url.path.endswith("/prompt")]) == 2
    with pytest.raises(ex.InvalidBindings, match="already retried"):
        send.retry_generation(project.project_id, project.active_generation_id, source.id,
            store=store, config=config, transport=recorder(calls))
    assert len([r for r in calls if r.url.path.endswith("/prompt")]) == 2


@pytest.mark.parametrize("status", ["queued", "running", "unknown", "completed"])
def test_nonfailure_cannot_retry(env, status):
    store, config, _, _, project, _ = env
    source, calls = source_attempt(env, status)
    with pytest.raises(ex.InvalidBindings, match="failed or cancelled"):
        send.retry_generation(project.project_id, project.active_generation_id, source.id,
            store=store, config=config, transport=recorder(calls))
    assert len(calls) == 1


def test_prompt_drift_blocks_before_transport(env):
    store, config, _, _, project, _ = env
    source, calls = source_attempt(env)
    store.adopt_manual_edit_execution_prompt(project.project_id, project.active_generation_id,
        ManualEditAdoptionWrite(prompt=source.prompt_snapshot.prompt + " Changed."))
    with pytest.raises(ex.InvalidBindings, match="selected execution prompt"):
        send.retry_generation(project.project_id, project.active_generation_id, source.id,
            store=store, config=config, transport=recorder(calls))
    assert len(calls) == 1


def test_missing_recorded_connection_blocks(env):
    store, config, _, connection, project, _ = env
    source, calls = source_attempt(env)
    config.delete("connections", connection.id)
    with pytest.raises(ex.InvalidBindings, match="recorded Connection"):
        send.retry_generation(project.project_id, project.active_generation_id, source.id,
            store=store, config=config, transport=recorder(calls))
    assert len(calls) == 1


@pytest.mark.parametrize("change, message", [("profile", "Workflow Profile changed"),
                                             ("workflow", "imported workflow changed")])
def test_profile_drift_blocks_before_transport(env, change, message):
    store, config, profile, _, project, _ = env
    source, calls = source_attempt(env)
    draft = capacity_profile().model_dump(by_alias=True)
    if change == "profile":
        draft["description"] = "Edited after the original attempt"
    else:
        graph = json.loads(draft["workflowJson"])
        graph["h3"]["inputs"]["seed"] = 42
        draft["workflowJson"] = json.dumps(graph)
    config.save("profiles", ex.ProfileUpdate(**draft), profile.id)
    with pytest.raises(ex.InvalidBindings, match=message):
        send.retry_generation(project.project_id, project.active_generation_id, source.id,
            store=store, config=config, transport=recorder(calls))
    assert len(calls) == 1


def test_retry_reuses_recorded_random_seed_while_normal_send_rerolls(env, monkeypatch):
    store, config, profile, _, project, body = env
    draft = capacity_profile().model_dump(by_alias=True)
    graph = json.loads(draft["workflowJson"])
    graph["h3"]["inputs"]["seed"] = 44
    draft["workflowJson"] = json.dumps(graph)
    draft["bindings"].append(mapping("renderer_seed", "h3", "seed"))
    controlled = config.save("profiles", ex.ProfileCreate(**draft))
    numbers = iter([123, 456])
    monkeypatch.setattr(send.secrets, "randbelow", lambda _: next(numbers))
    calls = []
    first = send.send_generation(body(f=controlled), store=store, config=config, transport=recorder(calls))
    store.update_render_job(project.project_id, project.active_generation_id,
        first.render_submission_id, RendererJobState(status="failed"))
    retried = send.retry_generation(project.project_id, project.active_generation_id, first.render_submission_id,
        store=store, config=config, transport=recorder(calls))
    reroll = send.send_generation(body(f=controlled), store=store, config=config, transport=recorder(calls))
    assert [first.renderer_seed, retried.renderer_seed, reroll.renderer_seed] == [123, 123, 456]
    prompts = [json.loads(r.content)["prompt"]["h3"]["inputs"]["seed"]
               for r in calls if r.url.path.endswith("/prompt")]
    assert prompts == [123, 123, 456]


def test_ambiguous_retry_acceptance_never_auto_resends(env):
    store, config, _, _, project, _ = env
    source, calls = source_attempt(env)
    def ambiguous(request, seen):
        if request.url.path.endswith("/prompt"):
            return httpx.Response(503, json={})
    with pytest.raises(send.SendError, match="No automatic retry"):
        send.retry_generation(project.project_id, project.active_generation_id, source.id,
            store=store, config=config, transport=recorder(calls, ambiguous))
    assert len([r for r in calls if r.url.path.endswith("/prompt")]) == 2  # original + one retry
    assert len(store.generation_outputs(project.project_id, project.active_generation_id)["renderSubmissions"]) == 1
    assert store.render_submission(project.project_id, project.active_generation_id, source.id).job.status == "failed"


def test_concurrent_retry_has_one_prompt_submission(env):
    store, config, _, _, project, _ = env
    source, calls = source_attempt(env)
    entered, release = Event(), Event()
    errors = []
    def handle(request):
        calls.append(request)
        entered.set()
        assert release.wait(5)
        body = json.loads(request.content)
        return httpx.Response(200, json={"prompt_id": body["prompt_id"], "number": 1, "node_errors": {}})
    def first():
        try:
            send.retry_generation(project.project_id, project.active_generation_id, source.id,
                store=store, config=config, transport=httpx.MockTransport(handle))
        except Exception as exc:
            errors.append(exc)
    thread = Thread(target=first)
    thread.start()
    try:
        assert entered.wait(5)
        with pytest.raises(ex.InvalidBindings, match="already being submitted"):
            send.retry_generation(project.project_id, project.active_generation_id, source.id,
                store=store, config=config, transport=recorder(calls))
    finally:
        release.set(); thread.join(5)
    assert not errors and not thread.is_alive()
    assert len([r for r in calls if r.url.path.endswith("/prompt")]) == 2
