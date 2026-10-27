"""Phase C: real local decoding, mocked vision, canonical persistence and textual handoff."""
import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
from io import BytesIO
import json
from pathlib import Path
from uuid import uuid4
from fastapi import FastAPI

import av
import httpx
from PIL import Image
import pytest
from pydantic import ValidationError

from miniconstruct.api.observation_routes import AnalyzeRequest
from miniconstruct.api import observation_routes
from miniconstruct.execution_state import SelectionWrite
from miniconstruct.models.observations import ObservedEndState, TakeObservation, observation_projection
from miniconstruct.models.takes import TakeImport
from miniconstruct.observation_analysis import analyze_selected_take, _parse_observation_response
from miniconstruct.observation_media import extract_evidence
from miniconstruct.project_store import ProjectStore, ProjectStoreError
from miniconstruct.story_operations import bounded_story_snapshot, plan_next
from miniconstruct.models.story_operations import StoryNextRequest
from test_story_next import OfflineNextClient, context_of, LLM
from test_validator import VALID_BASE


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Real provider calls are forbidden")
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)


def video(path, frames=40):
    with av.open(str(path), "w") as container:
        stream = container.add_stream("mpeg4", rate=10)
        stream.width = 96; stream.height = 64; stream.pix_fmt = "yuv420p"
        for index in range(frames):
            frame = av.VideoFrame.from_image(Image.new("RGB", (96, 64), "lime" if index == frames - 1 else "red"))
            for packet in stream.encode(frame): container.mux(packet)
        for packet in stream.encode(): container.mux(packet)
    return path.read_bytes()


def observed(summary="Kent visibly still wears the red jacket at the cut.", sid=None):
    return ObservedEndState(summary=summary, environment="Room with open door",
        subjectStates=[] if not sid else [{"subjectId": sid, "visibility": "visible", "appearance": "",
            "wardrobe": "red jacket on", "condition": "", "positionPose": "standing", "heldObjects": "", "currentAction": "turning"}],
        propsObjects=["Door open"], actionMomentum="Turning toward the door", cameraState="Medium shot, settled",
        lightingConditions="Daylight", plannedOutcomeAssessment={"status": "conflicts", "explanation": "The intended jacket removal did not occur."},
        unresolvedVisibleEntities=[])


class Vision:
    def __init__(self, state=None, action=None, raw=None):
        self.state = state or observed(); self.action = action; self.raw = raw; self.calls = []
    def __call__(self, settings): self.settings = settings; return self
    async def __aenter__(self): return self
    async def __aexit__(self, *_): pass
    async def generate(self, messages, **kwargs):
        assert kwargs == {"response_instruction": None, "allow_retries": False}
        assert self.settings.reasoning_mode == "off" and self.settings.seed is None
        self.calls.append(messages)
        if self.action: self.action()
        return self.raw if self.raw is not None else self.state.model_dump_json(by_alias=True)


@pytest.fixture
def env(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path / "projects")
    project = store.create_project(workspace_factory(subjects=[{"id": "kent", "number": 1}], nextSubjectNumber=2),
        intended_outgoing_state="Kent removes the red jacket.")
    data = video(tmp_path / "fixture.mp4")
    return store, project, data


async def add(env, data=None, provenance=None):
    store, project, default = env
    async def chunks(): yield data or default
    result = await store.import_take(project.project_id, project.active_generation_id, chunks(), "fixture.mp4",
                                     provenance or TakeImport(sourceKind="external_unknown"))
    take_id = result["takes"][-1]["id"]
    store.select_take(project.project_id, project.active_generation_id, take_id)
    return take_id


def request(env, **kwargs):
    store, p, _ = env
    return AnalyzeRequest(llm={"baseUrl": "http://never.test/v1", "model": "offline", "supportsVision": True},
        expectedProjectFingerprint=store.generation_outputs(p.project_id, p.active_generation_id)["projectFingerprint"], **kwargs)


async def analyze(env, client=None, **kwargs):
    store, p, _ = env
    return await analyze_selected_take(store, p.project_id, p.active_generation_id, request(env, **kwargs), client or Vision())


@pytest.mark.asyncio
async def test_asgi_disconnect_cancels_provider_and_prevents_observation_commit(env, monkeypatch):
    store, project, _ = env
    await add(env)
    started, cancelled = asyncio.Event(), asyncio.Event()

    class DeferredVision:
        def __init__(self, settings): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def generate(self, *_args, **_kwargs):
            started.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

    monkeypatch.setattr(observation_routes, "ProjectStore", lambda: store)
    async def deferred_analysis(store_arg, project_id, generation_id, request_arg):
        return await analyze_selected_take(store_arg, project_id, generation_id, request_arg, DeferredVision)
    monkeypatch.setattr(observation_routes, "analyze_selected_take", deferred_analysis)
    body = request(env).model_dump(mode="json", by_alias=True)
    from json import dumps
    payload = dumps(body).encode()
    incoming = asyncio.Queue()
    await incoming.put({"type": "http.request", "body": payload, "more_body": False})
    sent = []

    async def receive():
        return await incoming.get()

    async def send(message):
        sent.append(message)

    app = FastAPI()
    app.include_router(observation_routes.router)
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "method": "POST",
        "path": f"/api/projects/{project.project_id}/generations/{project.active_generation_id}/selected-take/analyze",
        "raw_path": b"", "query_string": b"", "headers": [(b"content-type", b"application/json")],
        "server": ("test", 80), "client": ("test", 1), "root_path": ""}
    request_task = asyncio.create_task(app(scope, receive, send))
    await asyncio.wait_for(started.wait(), 5)
    await incoming.put({"type": "http.disconnect"})
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(request_task, 5)
    assert cancelled.is_set()
    assert not store.generation_outputs(project.project_id, project.active_generation_id)["takeObservations"]
    assert not any(message["type"] == "http.response.start" for message in sent)


@pytest.mark.parametrize("frames", [1, 7, 40])
def test_actual_endpoint_tail_bounds_and_untouched_bytes(tmp_path, frames):
    path = tmp_path / "fixture.mp4"; original = video(path, frames)
    meta, images = extract_evidence(path)
    assert path.read_bytes() == original
    assert meta.endpoint_timestamp == pytest.approx((frames - 1) / 10)
    assert 1 <= len(meta.sampled_timestamps) <= 4
    assert min(meta.sampled_timestamps) >= max(0, meta.endpoint_timestamp - 2)
    assert meta.sampled_timestamps[-1] == meta.endpoint_timestamp
    pixel = Image.open(BytesIO(images["endpoint"])).getpixel((40, 30))
    assert pixel[1] > 200 and pixel[0] < 20, pixel
    assert Image.open(BytesIO(images["contact-sheet"])).size == (960, 588)
    assert all(len(data) <= 1024 * 1024 for data in images.values())


async def test_external_unknown_vision_payload_plan_conflict_and_memory_preserved(env):
    store, p, data = env; take_id = await add(env)
    memory = store.read_generation(p.project_id, p.active_generation_id).story_memory_after
    client = Vision(observed(sid="kent")); result = await analyze(env, client)
    observation = result["takeObservations"][0]
    assert observation["takeId"] == take_id and observation["takeSha256"] == hashlib.sha256(data).hexdigest()
    assert observation["effectiveObservedState"]["plannedOutcomeAssessment"]["status"] == "conflicts"
    assert "jacket at the cut" in observation["effectiveObservedState"]["summary"]
    parts = client.calls[0][1]["content"]
    assert len([part for part in parts if part["type"] == "image_url"]) == 2
    text = json.dumps(client.calls)
    assert "VIDEO EVIDENCE IS AUTHORITY" in text and "Kent removes" in text
    assert base64.b64encode(data).decode() not in text and "renderSubmission" not in text
    assert store.read_generation(p.project_id, p.active_generation_id).story_memory_after == memory
    raw = (store.root / f"project-{p.project_id}" / "project.json").read_text(encoding="utf-8")
    assert "base64" not in raw and "image_url" not in raw and '"schemaVersion": 17' in raw


async def test_requires_selected_take_vision_and_media(env):
    client = Vision()
    with pytest.raises(ProjectStoreError, match="Select a Take"): await analyze(env, client)
    take_id = await add(env)
    store, p, _ = env
    req = request(env); req.llm.supports_vision = False
    with pytest.raises(ProjectStoreError, match="vision-capable"):
        await analyze_selected_take(store, p.project_id, p.active_generation_id, req, client)
    path, _ = store.take_media(p.project_id, p.active_generation_id, take_id); path.unlink()
    with pytest.raises(ProjectStoreError, match="missing"): await analyze(env, client)
    assert not client.calls


@pytest.mark.parametrize("raw", ['not json', '```json\n{}\n```', '{}', '{"takeId":"invented"}'])
async def test_malformed_response_no_retry_or_fake_commit(env, raw):
    await add(env); client = Vision(raw=raw)
    with pytest.raises(ProjectStoreError, match="Invalid observation"): await analyze(env, client)
    assert len(client.calls) == 1
    store, p, _ = env
    assert not store.generation_outputs(p.project_id, p.active_generation_id)["takeObservations"]


async def test_local_model_json_normalization_and_subject_identity_aliases(env):
    store, p, _ = env; await add(env)
    store.set_subject_narrative_name(p.project_id, "kent", "Kent")
    payload = observed(sid="kent").model_dump(mode="json", by_alias=True)
    for raw in (json.dumps(payload), "  \n" + json.dumps(payload) + " \n",
                "```json\n" + json.dumps({**payload, "subjectStates": [{**payload["subjectStates"][0], "subjectId": "Kent"}]}) + "\n```"):
        result = await analyze(env, Vision(raw=raw))
        assert result["takeObservations"][0]["effectiveObservedState"]["subjectStates"][0]["subjectId"] == "kent"
    label_payload = {**payload, "subjectStates": [{**payload["subjectStates"][0], "subjectId": "<Subject 1>"}]}
    result = await analyze(env, Vision(raw=json.dumps(label_payload)))
    assert result["takeObservations"][0]["effectiveObservedState"]["subjectStates"][0]["subjectId"] == "kent"


async def test_local_model_validation_failures_are_precise_and_never_retried(env):
    await add(env)
    base = observed(sid="kent").model_dump(mode="json", by_alias=True)
    cases = [
        ({**base, "environment": "x" * 801}, "environment", "string_too_long"),
        ({**base, "plannedOutcomeAssessment": {"status": "invalid", "explanation": ""}}, "plannedOutcomeAssessment.status", "literal_error"),
        ({**base, "unexpectedSemanticField": "ignored"}, "response", "extra"),
    ]
    for payload, field, kind in cases:
        client = Vision(raw=json.dumps(payload))
        with pytest.raises(ProjectStoreError) as exc: await analyze(env, client)
        assert field in str(exc.value) and kind in str(exc.value)
        assert len(client.calls) == 1


def test_subject_alias_ambiguity_is_rejected_without_fuzzy_matching():
    payload = observed(sid="a").model_dump(mode="json", by_alias=True)
    payload["subjectStates"][0]["subjectId"] = "KENT"
    with pytest.raises(ProjectStoreError, match="ambiguous"):
        _parse_observation_response(json.dumps(payload), [
            {"subjectId": "a", "canonicalH3Label": "<Subject 1>", "narrativeName": "Kent"},
            {"subjectId": "b", "canonicalH3Label": "<Subject 2>", "narrativeName": "kent"},
        ], {"a", "b"})


async def test_offered_subject_ids_only_and_strict_limits(env):
    await add(env)
    with pytest.raises(ProjectStoreError, match="Subject identity"): await analyze(env, Vision(observed(sid="invented")))
    for update in ({"summary": "x" * 801}, {"propsObjects": ["x"] * 13},
                   {"subjectStates": [observed(sid="kent").subject_states[0].model_dump(by_alias=True)] * 2},
                   {"plannedOutcomeAssessment": {"status": "probably", "explanation": ""}}):
        raw = observed().model_dump(by_alias=True); raw.update(update)
        with pytest.raises(ValidationError): ObservedEndState.model_validate(raw)


async def test_historical_recognition_candidates_are_analysis_only_and_never_use_future_evidence(tmp_path, workspace_factory):
    encoded = BytesIO(); Image.new("RGB", (8, 8), "navy").save(encoded, format="PNG")
    identity = {
        "id": "identity-1", "kind": "image", "filename": "subject-1.png", "mimeType": "image/png",
        "role": "subject_identity", "subjectIdentity": {"subjectId": "subject-1"},
        "image": {"data_url": "data:image/png;base64," + base64.b64encode(encoded.getvalue()).decode(), "width": 8, "height": 8},
    }
    store = ProjectStore(tmp_path / "projects")
    project = store.create_project(workspace_factory(subjects=[{"id": "subject-1", "number": 1}, {"id": "subject-4", "number": 2}],
        nextSubjectNumber=3, assets=[identity]))
    current = store.create_generation(project.project_id, workspace=workspace_factory(subjects=[{"id": "subject-2", "number": 3}], nextSubjectNumber=4))
    future = store.create_generation(project.project_id, workspace=workspace_factory(subjects=[{"id": "subject-3", "number": 4}], nextSubjectNumber=5))
    raw_video = video(tmp_path / "current.mp4")
    async def chunks(): yield raw_video
    imported = await store.import_take(project.project_id, current.active_generation_id, chunks(), "current.mp4", TakeImport(sourceKind="external_unknown"))
    take_id = imported["takes"][-1]["id"]
    store.select_take(project.project_id, current.active_generation_id, take_id)
    request = AnalyzeRequest(llm={"baseUrl": "http://never.test/v1", "model": "offline", "supportsVision": True},
        expectedProjectFingerprint=store.generation_outputs(project.project_id, current.active_generation_id)["projectFingerprint"])
    client = Vision(observed(sid="subject-1"))
    result = await analyze_selected_take(store, project.project_id, current.active_generation_id, request, client)
    parts = client.calls[0][1]["content"]
    candidate_ids = [item["subjectId"] for item in json.loads(parts[0]["text"])["candidateSubjects"]]
    assert candidate_ids[:3] == ["subject-2", "subject-1", "subject-4"]
    assert "subject-3" not in candidate_ids and len(candidate_ids) <= 16
    identity_messages = [item["text"] for item in parts if item["type"] == "text" and item["text"].startswith("Identity-only")]
    assert identity_messages == ["Identity-only reference for subjectId subject-1; NOT endpoint state."]
    assert len([item for item in parts if item["type"] == "image_url"]) <= 6
    saved = store.read_generation(project.project_id, current.active_generation_id).workspace
    assert [subject.id for subject in saved.subjects] == ["subject-2"] and not saved.assets
    assert result["takeObservations"][0]["effectiveObservedState"]["subjectStates"][0]["subjectId"] == "subject-1"
    # Merely offering the predecessor's other canonical Subject creates no retention dependency.
    store.delete_sequence_subject(project.project_id, "subject-4")
    assert all(subject.id != "subject-4" for subject in store.load_project(project.project_id).sequence.subjects)
    assert future.active_generation_id != current.active_generation_id


async def test_v13_read_nonmutating_then_v14_analysis(env):
    store, p, _ = env; await add(env)
    path = store.root / f"project-{p.project_id}" / "project.json"
    data = json.loads(path.read_text()); data["schemaVersion"] = 13
    data["sequence"]["generations"][0].pop("takeObservations")
    path.write_text(json.dumps(data)); before = path.read_bytes()
    assert store.load_project(p.project_id).outputs["takeObservations"] == []
    assert path.read_bytes() == before
    await analyze(env)
    assert json.loads(path.read_text())["schemaVersion"] == 17


async def test_corrections_switch_clear_reload_save_as_and_delete(env):
    store, p, _ = env; a = await add(env); result = await analyze(env)
    model = result["takeObservations"][0]["modelObservedState"]
    correction = observed("User correction: black T-shirt; coat gone.")
    result = store.correct_observation(p.project_id, p.active_generation_id, a, result["projectFingerprint"], correction)
    assert result["takeObservations"][0]["modelObservedState"] == model
    assert result["takeObservations"][0]["status"] == "user_edited"
    with pytest.raises(ProjectStoreError, match="confirm"): await analyze(env)
    b = await add(env); await analyze(env, Vision(observed("B endpoint")))
    def active(): return store.read_generation(p.project_id, p.active_generation_id).observed_end_state
    assert active()["effectiveObservedState"]["summary"] == "B endpoint"
    store.select_take(p.project_id, p.active_generation_id, a)
    assert active()["status"] == "user_edited"
    c = await add(env); assert active() is None
    store.select_take(p.project_id, p.active_generation_id, None); assert active() is None
    store.select_take(p.project_id, p.active_generation_id, a)
    saved = store.save_project(p.project_id, p.workspace)
    fork = store.duplicate_project(p.project_id, "Copied observations")
    assert fork.outputs["takeObservations"] == saved.outputs["takeObservations"]
    assert ProjectStore(store.root).load_project(p.project_id).outputs["takeObservations"] == saved.outputs["takeObservations"]
    # Save As omits disposable evidence; review regenerates from copied Take.
    assert not (store.root / f"project-{fork.project_id}" / "outputs/observations").exists()
    assert store.observation_evidence(fork.project_id, fork.active_generation_id, a, "endpoint").exists()
    result = store.delete_take(p.project_id, p.active_generation_id, b)
    assert [o["takeId"] for o in result["takeObservations"]] == [a]
    assert not (store.root / f"project-{p.project_id}" / "outputs/observations" / b).exists()
    result = store.correct_observation(p.project_id, p.active_generation_id, a, result["projectFingerprint"], reset=True)
    assert result["takeObservations"][0]["effectiveObservedState"] == model


async def test_cache_regeneration_missing_raw_and_exact_reattach(env, monkeypatch):
    store, p, data = env; take_id = await add(env); await analyze(env)
    directory, _, take, source, observation = store.observation_snapshot(p.project_id, p.active_generation_id)
    from miniconstruct import observation_store
    original = observation_store.extract_evidence
    def fail(*_): raise AssertionError("Cache should be reused")
    monkeypatch.setattr(observation_store, "extract_evidence", fail)
    store.prepare_observation_evidence(directory, take, source)
    monkeypatch.setattr(observation_store, "extract_evidence", original)
    path = store.observation_evidence(p.project_id, p.active_generation_id, take_id, "endpoint"); path.unlink()
    assert store.observation_evidence(p.project_id, p.active_generation_id, take_id, "endpoint").exists()
    source.unlink()
    assert store.read_generation(p.project_id, p.active_generation_id).observed_end_state
    assert store.generation_outputs(p.project_id, p.active_generation_id)["takes"][0]["availability"] == "missing"
    async def chunks(): yield data
    await store.import_take(p.project_id, p.active_generation_id, chunks(), "reattached.mp4", reattach_id=take_id)
    assert source.read_bytes() == data


async def test_malformed_media_no_fake_observation(env):
    await add(env, b"broken media")
    client = Vision()
    with pytest.raises(ProjectStoreError, match="decoded safely"): await analyze(env, client)
    assert not client.calls


async def test_selection_switch_during_inference_caches_original(env):
    store, p, _ = env; a = await add(env); b = await add(env)
    store.select_take(p.project_id, p.active_generation_id, a)
    def switch():
        # A different thread can acquire the Project lock during provider inference.
        with ThreadPoolExecutor() as pool:
            pool.submit(store.select_take, p.project_id, p.active_generation_id, b).result(timeout=5)
    result = await analyze(env, Vision(action=switch))
    assert result["selectedTakeId"] == b and result["takeObservations"][0]["takeId"] == a
    assert store.read_generation(p.project_id, p.active_generation_id).observed_end_state is None


@pytest.mark.parametrize("remove", ["take", "generation", "project"])
async def test_deletion_during_inference_prevents_commit(env, workspace_factory, remove):
    store, p, _ = env; take_id = await add(env)
    if remove == "generation":
        store.create_generation(p.project_id, workspace=workspace_factory())
        store.activate_generation(p.project_id, p.active_generation_id)
    actions = {"take": lambda: store.delete_take(p.project_id, p.active_generation_id, take_id),
               "generation": lambda: store.delete_generation(p.project_id, p.active_generation_id),
               "project": lambda: store.delete_project(p.project_id)}
    with pytest.raises(ProjectStoreError): await analyze(env, Vision(action=actions[remove]))
    assert not list(store.root.rglob("endpoint.jpg"))


async def test_concurrent_correction_is_not_lost(env):
    store, p, _ = env; a = await add(env); result = await analyze(env)
    def correct(): store.correct_observation(p.project_id, p.active_generation_id, a, result["projectFingerprint"], observed("Keep this correction"))
    with pytest.raises(ProjectStoreError, match="changed during analysis"): await analyze(env, Vision(action=correct))
    assert "Keep this correction" in json.dumps(store.load_project(p.project_id).outputs)
    result = await analyze(env, replaceCorrections=True)
    assert result["takeObservations"][0]["status"] == "model_observed"


@pytest.mark.parametrize("relationship", ["continue_previous_shot", "new_shot_preserve_continuity", "match_previous_ending_frame", "independent"])
async def test_h3_selected_effective_state_authority_and_fingerprints(env, workspace_factory, relationship):
    store, p, _ = env; a = await add(env); result = await analyze(env)
    own = store.execution_snapshot(p.project_id, p.active_generation_id)[1].current_fingerprint
    next_p = store.create_generation(p.project_id, workspace=workspace_factory())
    next_p = store.save_project(p.project_id, next_p.workspace, planning={"relationshipToPrevious": relationship}, incoming_state="Explicit current start")
    gid = next_p.active_generation_id
    _, selection = store.execution_snapshot(p.project_id, gid)
    store.select_execution_prompt(p.project_id, gid, SelectionWrite(prompt=VALID_BASE, sourceFingerprint=selection.current_fingerprint))
    context = store.h3_continuity_context(p.project_id, gid)
    assert context.incoming_state == "Explicit current start"
    assert bool(context.predecessor_observation) == (relationship != "independent")
    receipt = store.generation_outputs(p.project_id, p.active_generation_id)
    store.correct_observation(p.project_id, p.active_generation_id, a, receipt["projectFingerprint"], observed("Corrected jacket on"))
    assert store.execution_snapshot(p.project_id, gid)[1].stale == (relationship != "independent")
    context = store.h3_continuity_context(p.project_id, gid)
    if relationship != "independent":
        assert context.predecessor_observation["effectiveObservedState"]["summary"] == "Corrected jacket on"
        assert context.predecessor_intended_outgoing_state == "Kent removes the red jacket."
    before = store.execution_snapshot(p.project_id, gid)[1].current_fingerprint
    b = await add(env); await analyze(env, Vision(observed("B jacket on")))
    assert (store.execution_snapshot(p.project_id, gid)[1].current_fingerprint != before) == (relationship != "independent")
    assert store.execution_snapshot(p.project_id, p.active_generation_id)[1].current_fingerprint == own
    c = await add(env)
    context = store.h3_continuity_context(p.project_id, gid)
    assert context.predecessor_observation is None
    assert any("has not been analyzed" in w for w in context.warnings) == (relationship != "independent")
    store.select_take(p.project_id, p.active_generation_id, a)
    store.take_media(p.project_id, p.active_generation_id, a)[0].unlink()
    context = store.h3_continuity_context(p.project_id, gid)
    assert any("unavailable for review" in w for w in context.warnings) == (relationship != "independent")


async def test_plan_next_read_only_selected_observation_and_bounded_context(env):
    store, p, _ = env; a = await add(env); result = await analyze(env)
    store.correct_observation(p.project_id, p.active_generation_id, a, result["projectFingerprint"], observed("Effective jacket on"))
    b = await add(env); await analyze(env, Vision(observed("UNSELECTED SECRET")))
    store.select_take(p.project_id, p.active_generation_id, a)
    before = (store.root / f"project-{p.project_id}" / "project.json").read_bytes()
    client = OfflineNextClient()
    await plan_next(store, p.project_id, StoryNextRequest(anchorGenerationId=p.active_generation_id, requestedCount=1, llm=LLM), client)
    context = context_of(client); text = json.dumps(client.calls)
    assert "Effective jacket on" in text and "Kent removes the red jacket" in text
    assert "UNSELECTED SECRET" not in text and "read-only evidence" in text
    assert len(context["localContext"]) <= 3
    assert (store.root / f"project-{p.project_id}" / "project.json").read_bytes() == before


@pytest.mark.parametrize("kind", ["external_unknown", "selected_prompt", "comfyui_submission"])
async def test_all_provenance_modes_equal_analysis_authority(env, kind):
    from miniconstruct.models.takes import RenderSubmission, TakePromptSnapshot
    store, p, _ = env
    if kind == "external_unknown":
        provenance = TakeImport(sourceKind=kind)
    else:
        status = store.execution_snapshot(p.project_id, p.active_generation_id)[1]
        selected = store.select_execution_prompt(p.project_id, p.active_generation_id,
            SelectionWrite(prompt=VALID_BASE, sourceFingerprint=status.current_fingerprint)).selected
        if kind == "selected_prompt":
            provenance = TakeImport(sourceKind=kind, selectedPromptId=selected.id)
        else:
            submission = RenderSubmission(connectionId=uuid4(), workflowProfileId=uuid4(),
                workflowFingerprint="a" * 64, profileFingerprint="b" * 64, comfyPromptId=uuid4(),
                promptSnapshot=TakePromptSnapshot.capture(selected))
            store.record_render_submission(p.project_id, p.active_generation_id, submission)
            provenance = TakeImport(sourceKind=kind, renderSubmissionId=submission.id)
    take_id = await add(env, provenance=provenance)
    before = store.take_detail(p.project_id, p.active_generation_id, take_id)
    result = await analyze(env)
    assert result["takeObservations"][0]["takeId"] == take_id
    assert store.take_detail(p.project_id, p.active_generation_id, take_id) == before


async def test_scoped_evidence_endpoints_and_correction_fingerprint(env, monkeypatch):
    from fastapi.testclient import TestClient
    from miniconstruct.app import app
    store, p, _ = env; a = await add(env); result = await analyze(env)
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(store.root))
    client = TestClient(app)
    base = f"/api/projects/{p.project_id}/generations/{p.active_generation_id}"
    response = client.get(base + f"/takes/{a}/evidence/endpoint")
    assert response.status_code == 200 and response.headers["content-type"] == "image/jpeg"
    assert client.get(base + f"/takes/{uuid4()}/evidence/endpoint").status_code == 404
    assert client.get(base + f"/takes/{a}/evidence/arbitrary.jpg").status_code == 422
    body = {"expectedProjectFingerprint": "0" * 64, "effectiveObservedState": observed().model_dump(by_alias=True)}
    assert client.put(base + f"/takes/{a}/observation", json=body).status_code == 409
    body["expectedProjectFingerprint"] = result["projectFingerprint"]
    assert client.put(base + f"/takes/{a}/observation", json=body).status_code == 200
    assert client.post(base + "/selected-take/analyze", json={**request(env).model_dump(mode="json", by_alias=True), "derivedPath": "C:/private"}).status_code == 422


async def test_identity_images_bounded_and_labeled(env, tmp_path):
    from miniconstruct.observation_analysis import identity_parts
    pictures = []
    for i in range(12):
        path = tmp_path / f"picture-{i}.png"
        Image.new("RGB", (1024, 700), "red").save(path)
        pictures.append((f"subject-{i // 2}", path))
    parts = identity_parts(pictures, {f"subject-{i}" for i in range(6)})
    assert len([p for p in parts if p["type"] == "image_url"]) == 4
    assert all("NOT endpoint state" in p["text"] for p in parts if p["type"] == "text")
    for part in parts:
        if part["type"] == "image_url":
            image = Image.open(BytesIO(base64.b64decode(part["image_url"]["url"].split(",")[1])))
            assert max(image.size) <= 512


async def test_decoder_outside_project_lock_and_generation_delete_cleanup(env, monkeypatch, workspace_factory):
    store, p, _ = env; await add(env)
    from miniconstruct import observation_store
    original = observation_store.extract_evidence
    def checked(path):
        with ThreadPoolExecutor() as pool:
            pool.submit(store.generation_outputs, p.project_id, p.active_generation_id).result(timeout=5)
        return original(path)
    monkeypatch.setattr(observation_store, "extract_evidence", checked)
    await analyze(env)
    store.create_generation(p.project_id, workspace=workspace_factory())
    store.activate_generation(p.project_id, p.active_generation_id)
    store.delete_generation(p.project_id, p.active_generation_id)
    assert not list(store.root.rglob("endpoint.jpg"))


def test_opt_in_fixtures_use_production_context_without_provider():
    from miniconstruct.benchmark.runner import BenchmarkCase, resolve_h3_fixture
    from miniconstruct.models.api import GenerationRequest
    from miniconstruct.h3.builder import assemble_prompt
    for name in ("h3-observed-failed-plan", "h3-observed-current-override"):
        folder = Path("benchmarks/cases") / name
        case = BenchmarkCase.model_validate_json((folder / "case.json").read_text())
        workspace = json.loads((folder / "workspace.json").read_text())
        workspace["assets"][0]["image"]["data_url"] = "data:image/png;base64," + base64.b64encode((folder / "assets/kent-jacket.png").read_bytes()).decode()
        request = resolve_h3_fixture(GenerationRequest(workspace=workspace, llm=LLM), case.h3_project)
        text = assemble_prompt(request.workspace, True, request.narrative_subjects, request.continuity_context).inspector_text
        assert all(needle in text for needle in case.h3_project.input_contains)
        assert request.continuity_context.predecessor_observation["takeId"] == "10000000-0000-4000-8000-000000000010"


async def test_actual_client_disables_compatibility_retry():
    from miniconstruct.llm.client import OpenAICompatibleClient, LLMBackendError
    calls = []
    def reject(request):
        calls.append(request)
        return httpx.Response(400, json={"error": "Unsupported parameter chat_template_kwargs"})
    settings = LLM.model_copy(deep=True)
    settings.endpoint.source = "unsloth_studio"
    async with OpenAICompatibleClient(settings, transport=httpx.MockTransport(reject)) as client:
        with pytest.raises(LLMBackendError):
            await client.generate([{"role": "user", "content": "Observe"}], allow_retries=False, response_instruction=None)
    assert len(calls) == 1


async def test_persisted_observation_rejects_wrong_take_or_hash(env):
    from miniconstruct.project_store import GenerationManifest
    store, p, _ = env; await add(env); await analyze(env)
    raw = store.observation_snapshot(p.project_id, p.active_generation_id)[1].model_dump(mode="json", by_alias=True)
    for field, value in [("takeId", str(uuid4())), ("takeSha256", "0" * 64)]:
        altered = json.loads(json.dumps(raw)); altered["takeObservations"][0][field] = value
        with pytest.raises(ValidationError, match="UUID and SHA-256"):
            GenerationManifest.model_validate(altered)


async def test_missing_optional_identity_picture_does_not_block_analysis(tmp_path, workspace_factory, image_asset_factory):
    asset = image_asset_factory()
    store = ProjectStore(tmp_path / "projects")
    p = store.create_project(workspace_factory(assets=[asset]))
    data = video(tmp_path / "source.mp4")
    env = (store, p, data); await add(env)
    directory, generation, *_ = store.observation_snapshot(p.project_id, p.active_generation_id)
    (directory / generation.asset_files[0].path).unlink()
    client = Vision(); result = await analyze(env, client)
    assert len(result["takeObservations"]) == 1
    assert len([part for part in client.calls[0][1]["content"] if part["type"] == "image_url"]) == 2


async def test_observation_subject_links_prevent_dangling_identity(env):
    store, p, _ = env; take_id = await add(env); await analyze(env, Vision(observed(sid="kent")))
    with pytest.raises(ProjectStoreError, match="linked by Take observations"):
        store.delete_sequence_subject(p.project_id, "kent")
    store.delete_take(p.project_id, p.active_generation_id, take_id)
    store.delete_sequence_subject(p.project_id, "kent")
