"""Production request assembly and HTTP drafting without any provider or renderer."""
import asyncio
import hashlib
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from miniconstruct.app import app
from miniconstruct import continuation_drafting as drafting
from miniconstruct.execution_state import SelectionWrite
from miniconstruct.llm.client import LLMBackendError, OpenAICompatibleClient
from miniconstruct.models.takes import RenderSubmission, TakePromptSnapshot
from miniconstruct.models.observations import TakeObservation
from miniconstruct.project_store import StaleProjectSnapshotError, ProjectStoreError
from test_take_continuation import source, authority, raw, offline
from test_observations import observed
from test_takes import select_prompt
from test_validator import VALID_BASE


class Writer:
    def __init__(self, text="She continues toward the door.", action=None):
        self.text, self.action, self.calls = text, action, []
    def __call__(self, settings): return self
    async def __aenter__(self): return self
    async def __aexit__(self, *_): pass
    async def generate(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if self.action: self.action()
        return self.text


def request(store, p, direction="She opens the door and enters the room."):
    return drafting.DraftContinuationRequest(expectedProjectFingerprint=store.load_project(p.project_id).project_fingerprint,
        direction=direction, llm={"baseUrl": "http://127.0.0.1:1234/v1", "modelId": "offline"})


def successor(source):
    store, p, take = source
    return store.continue_selected_take(p.project_id, p.active_generation_id, **authority(store, p, take))


def modify_source(source, fn):
    store, p, _ = source
    directory, manifest, generations, active, g = store._output_context(p.project_id, p.active_generation_id)
    fn(g)
    store._commit_sequence(directory, manifest, generations, active)


@pytest.mark.parametrize("status", ["model_observed", "user_edited", None])
async def test_production_evidence_corrected_precedence_direction_and_no_writes(source, status):
    store, p, take = source
    def edit(g):
        g.workspace["creativeRequest"] = "She removes the jacket."
        if status:
            g.take_observations.append(TakeObservation(takeId=take["id"], takeSha256=take["sha256"],
                analyzedAt="2026-10-20", analyzerModel="offline", offeredSubjectIds=[], status=status,
                modelObservedState=observed("Original model ending"),
                effectiveObservedState=observed("Corrected: jacket remains on") if status == "user_edited" else observed("Original model ending"),
                evidence={"sampledTimestamps": [3.9], "endpointTimestamp": 3.9,
                    "endpointSha256": "a" * 64, "contactSheetSha256": "b" * 64}))
    modify_source(source, edit)
    new = successor(source)
    before = raw(store, p)
    writer = Writer()
    req = request(store, p)
    result = await drafting.draft_continuation(p.project_id, new.active_generation_id, req, store, writer)
    evidence = json.loads(writer.calls[0][0][1]["content"])
    assert evidence["historicalUserIntent"]["mainPrompt"] == "She removes the jacket."
    assert evidence["newCreativeDirection"] == req.direction
    assert evidence["exactRendererSubmittedH3"] is None  # external video is fully supported
    assert evidence["authority"]["takeId"] == take["id"]
    assert evidence["authority"]["takeSha256"] == take["sha256"]
    assert evidence["authority"]["sourceGenerationId"] == p.active_generation_id
    if status:
        assert evidence["observedVisualEvidence"]["status"] == status
        assert ("Corrected" if status == "user_edited" else "Original") in evidence["observedVisualEvidence"]["effectiveObservedState"]["summary"]
    else:
        assert evidence["observedVisualEvidence"] is None
        assert "Analyze Selected Take" in result["warnings"][0]
        assert "do not assert final pose" in writer.calls[0][0][0]["content"]
    assert writer.calls[0][1]["allow_retries"] is False
    assert len(writer.calls) == 1
    assert raw(store, p) == before
    assert store.read_generation(p.project_id, new.active_generation_id).workspace.creative_request == ""


@pytest.mark.parametrize("kind", ["comfyui_submission", "selected_prompt"])
async def test_exact_submission_not_current_selected_prompt(source, kind):
    store, p, take = source
    selected = select_prompt(store, p)
    snapshot = TakePromptSnapshot.capture(selected)
    def edit(g):
        submission = RenderSubmission(connectionId=uuid4(), workflowProfileId=uuid4(),
            workflowFingerprint="a" * 64, profileFingerprint="b" * 64, comfyPromptId=uuid4(), promptSnapshot=snapshot)
        if kind == "comfyui_submission": g.render_submissions.append(submission)
        g.takes[0] = g.takes[0].model_copy(update={"source_kind": kind, "prompt_snapshot": snapshot,
            "render_submission_id": submission.id if kind == "comfyui_submission" else None})
    modify_source(source, edit)
    _, status = store.execution_snapshot(p.project_id, p.active_generation_id)
    wrong = VALID_BASE + "\nWrong current authoring prompt."
    store.select_execution_prompt(p.project_id, p.active_generation_id,
        SelectionWrite(prompt=wrong, sourceFingerprint=status.current_fingerprint))
    new = successor(source)
    writer = Writer()
    result = await drafting.draft_continuation(p.project_id, new.active_generation_id, request(store, p), store, writer)
    text = writer.calls[0][0][1]["content"]
    assert "Wrong current authoring prompt" not in text
    if kind == "comfyui_submission":
        assert result["evidence"]["exactRendererSubmittedH3"]["prompt"] == selected.prompt
    else:
        assert result["evidence"]["exactRendererSubmittedH3"] is None
        assert result["evidence"]["historicalUserIntent"]["associatedH3AuthoringIntent"] == selected.prompt


@pytest.mark.parametrize("change", ["remove", "different", "navigate", "observation"])
async def test_revalidate_source_after_call_and_at_adoption(source, change):
    store, p, take = source
    new = successor(source)
    req = request(store, p)
    result = await drafting.draft_continuation(p.project_id, new.active_generation_id, req, store, Writer())
    def mutate():
        if change == "navigate": store.activate_generation(p.project_id, p.active_generation_id)
        elif change == "different":
            modify_source(source, lambda g: setattr(g, "selected_take_id", None))
        elif change == "remove": store.delete_take(p.project_id, p.active_generation_id, take["id"])
        else: modify_source(source, lambda g: g.workspace.update(creativeRequest="Changed intent"))
    with pytest.raises((StaleProjectSnapshotError, ProjectStoreError)):
        await drafting.draft_continuation(p.project_id, new.active_generation_id, req, store, Writer(action=mutate))
    with pytest.raises((StaleProjectSnapshotError, ProjectStoreError)):
        drafting.validate_continuation_authority(store, p.project_id, new.active_generation_id,
                                                drafting.ContinuationAuthority(**result["authority"]))


def unrelated_submission(prompt):
    from miniconstruct.models.takes import RenderSubmission
    return RenderSubmission(connectionId=uuid4(), workflowProfileId=uuid4(),
        workflowFingerprint="a" * 64, profileFingerprint="b" * 64, comfyPromptId=uuid4(),
        promptSnapshot={"prompt": prompt, "sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "sourceFingerprint": "c" * 64, "selectionId": uuid4(), "selectedAt": "2026-10-20"})


async def test_renderer_status_update_during_model_call_keeps_candidate_current(source):
    store, p, _ = source
    new = successor(source)
    def reconcile():
        directory, manifest, generations, active, g = store._output_context(p.project_id, new.active_generation_id)
        g.render_submissions.append(unrelated_submission("unrelated render"))
        store._commit_sequence(directory, manifest, generations, active)
        directory, manifest, generations, active, g = store._output_context(p.project_id, new.active_generation_id)
        job = g.render_submissions[0].job.model_copy(update={"status": "completed"})
        g.render_submissions[0] = g.render_submissions[0].model_copy(update={"job": job})
        store._commit_sequence(directory, manifest, generations, active)
    writer = Writer(action=reconcile)
    result = await drafting.draft_continuation(p.project_id, new.active_generation_id, request(store, p), store, writer)
    drafting.validate_continuation_authority(store, p.project_id, new.active_generation_id,
                                             drafting.ContinuationAuthority(**result["authority"]))
    assert len(writer.calls) == 1


async def test_unrelated_renderer_history_before_adoption_is_ignored(source):
    store, p, _ = source
    new = successor(source)
    result = await drafting.draft_continuation(p.project_id, new.active_generation_id, request(store, p), store, Writer())
    directory, manifest, generations, active, g = store._output_context(p.project_id, new.active_generation_id)
    g.render_submissions.append(unrelated_submission("unrelated renderer history"))
    store._commit_sequence(directory, manifest, generations, active)
    drafting.validate_continuation_authority(store, p.project_id, new.active_generation_id,
                                             drafting.ContinuationAuthority(**result["authority"]))


async def test_selected_take_renderer_prompt_provenance_change_rejects_draft(source):
    store, p, _ = source
    selected = select_prompt(store, p)
    snapshot = TakePromptSnapshot.capture(selected)
    def establish(g):
        submission = RenderSubmission(connectionId=uuid4(), workflowProfileId=uuid4(),
            workflowFingerprint="a" * 64, profileFingerprint="b" * 64, comfyPromptId=uuid4(), promptSnapshot=snapshot)
        g.render_submissions.append(submission)
        g.takes[0] = g.takes[0].model_copy(update={"source_kind": "comfyui_submission",
            "prompt_snapshot": snapshot, "render_submission_id": submission.id})
    modify_source(source, establish)
    new = successor(source)
    result = await drafting.draft_continuation(p.project_id, new.active_generation_id, request(store, p), store, Writer())
    def change_provenance(g):
        updated = TakePromptSnapshot(prompt="A different submitted prompt", sha256=hashlib.sha256(
            b"A different submitted prompt").hexdigest(), sourceFingerprint="d" * 64,
            selectionId=uuid4(), selectedAt="2026-10-20")
        g.render_submissions[0] = g.render_submissions[0].model_copy(update={"prompt_snapshot": updated})
        g.takes[0] = g.takes[0].model_copy(update={"prompt_snapshot": updated})
    modify_source(source, change_provenance)
    with pytest.raises(StaleProjectSnapshotError):
        drafting.validate_continuation_authority(store, p.project_id, new.active_generation_id,
                                                drafting.ContinuationAuthority(**result["authority"]))


async def test_observation_change_rejects_draft(source):
    store, p, take = source
    new = successor(source)
    result = await drafting.draft_continuation(p.project_id, new.active_generation_id, request(store, p), store, Writer())
    def add_observation(g):
        state = observed("Corrected state differs from the prior evidence")
        g.take_observations.append(TakeObservation(takeId=take["id"], takeSha256=take["sha256"],
            analyzedAt="2026-10-20", analyzerModel="offline", offeredSubjectIds=[],
            modelObservedState=state, effectiveObservedState=state,
            evidence={"sampledTimestamps": [3.9], "endpointTimestamp": 3.9,
                "endpointSha256": "a" * 64, "contactSheetSha256": "b" * 64}))
    modify_source(source, add_observation)
    with pytest.raises(StaleProjectSnapshotError):
        drafting.validate_continuation_authority(store, p.project_id, new.active_generation_id,
                                                drafting.ContinuationAuthority(**result["authority"]))


@pytest.mark.parametrize("text", ["", "```H3\nwrong", "{\"fake\":true}", "x" * 20001])
async def test_invalid_response_no_repair_or_retry(source, text):
    store, p, _ = source
    new = successor(source)
    writer = Writer(text)
    before = raw(store, p)
    with pytest.raises(LLMBackendError):
        await drafting.draft_continuation(p.project_id, new.active_generation_id, request(store, p), store, writer)
    assert len(writer.calls) == 1 and raw(store, p) == before


async def test_http_production_route_and_validation_read_only(source, monkeypatch):
    store, p, _ = source
    new = successor(source)
    calls = []
    async def generate(self, messages, **kwargs):
        calls.append((messages, kwargs)); return "Continue carefully toward the doorway."
    monkeypatch.setattr(OpenAICompatibleClient, "generate", generate)
    before = raw(store, p)
    url = f"/api/projects/{p.project_id}/generations/{new.active_generation_id}/draft-continuation"
    with TestClient(app) as client:
        result = client.post(url, json=request(store, p, "Expand creatively but preserve known facts.").model_dump(mode="json", by_alias=True))
        assert result.status_code == 200, result.text
        assert client.post(url + "/validate", json=result.json()["authority"]).json() == {"valid": True}
        assert raw(store, p) == before
        store.select_take(p.project_id, p.active_generation_id, None)
        assert client.post(url + "/validate", json=result.json()["authority"]).status_code == 409
    assert len(calls) == 1
    assert "Expand creatively but preserve known facts." in calls[0][0][1]["content"]


async def test_failure_and_cancellation_do_not_write(source):
    store, p, _ = source
    new = successor(source)
    before = raw(store, p)
    for error in [LLMBackendError("offline failure"), asyncio.CancelledError()]:
        def fail(): raise error
        with pytest.raises(type(error)):
            await drafting.draft_continuation(p.project_id, new.active_generation_id, request(store, p), store, Writer(action=fail))
    assert raw(store, p) == before


async def test_story_projection_and_trusted_memory_are_read_only(source):
    store, p, _ = source
    def edit(g):
        g.planning.scene_plan = "She approaches the closed door."
        g.workspace["dialogue"] = "Wait for me."
        g.story_memory_status = "trusted"
        g.story_memory_after.recent_summary = "She knows the room belongs to her sister."
    modify_source(source, edit)
    new = successor(source)
    before = raw(store, p)
    result = await drafting.draft_continuation(p.project_id, new.active_generation_id, request(store, p, ""), store, Writer())
    evidence = result["evidence"]
    story = evidence["historicalUserIntent"]["storyAuthoringContext"]
    assert story["currentGeneration"]["scenePlan"] == "She approaches the closed door."
    assert story["currentGeneration"]["dialogue"] == "Wait for me."
    assert evidence["savedContinuityAuthoringContext"]["recentSummary"] == "She knows the room belongs to her sister."
    assert evidence["newCreativeDirection"] == ""
    assert raw(store, p) == before


async def test_requires_immediate_predecessor_selection_not_newest_take(source):
    from miniconstruct.models.takes import TakeImport
    store, p, take = source
    new = successor(source)
    async def chunks(): yield b"newest unselected opaque video"
    await store.import_take(p.project_id, p.active_generation_id, chunks(), "newest.mp4", TakeImport(sourceKind="external_unknown"))
    writer = Writer()
    result = await drafting.draft_continuation(p.project_id, new.active_generation_id, request(store, p), store, writer)
    assert result["authority"]["takeId"] == take["id"]
    store.select_take(p.project_id, p.active_generation_id, None)
    with pytest.raises(ProjectStoreError, match="explicit Selected Take"):
        await drafting.draft_continuation(p.project_id, new.active_generation_id, request(store, p), store, writer)
    assert len(writer.calls) == 1


async def test_first_or_independent_generation_is_ineligible(source):
    store, p, _ = source
    with pytest.raises(ProjectStoreError, match="saved continuation"):
        await drafting.draft_continuation(p.project_id, p.active_generation_id, request(store, p), store, Writer())
    new = successor(source)
    directory, manifest, generations, active, g = store._output_context(p.project_id, new.active_generation_id)
    g.planning = g.planning.model_copy(update={"relationship_to_previous": type(g.planning.relationship_to_previous)("independent")})
    store._commit_sequence(directory, manifest, generations, active)
    with pytest.raises(ProjectStoreError, match="saved continuation"):
        await drafting.draft_continuation(p.project_id, new.active_generation_id, request(store, p), store, Writer())
