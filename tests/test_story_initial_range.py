"""Offline production-path coverage for bounded Build Initial Range."""
from __future__ import annotations

import asyncio
import base64
from copy import deepcopy
from io import BytesIO
import json
import logging
from uuid import UUID, uuid4

import httpx
import pytest
from PIL import Image
from fastapi.testclient import TestClient

from miniconstruct.api import routes
from miniconstruct.app import app
from miniconstruct.llm.client import LLMBackendError, OpenAICompatibleClient
from miniconstruct.h3.builder import build_h3_input_map, build_reference_manifest
from miniconstruct.h3.builder import assemble_prompt
from miniconstruct.generation import resolve_narrative_request
from miniconstruct.models.api import GenerationRequest
from miniconstruct.models.api import LLMSettings
from miniconstruct.models.story_operations import StoryInitialRangeRequest
from miniconstruct.models.story_memory import StoryContinuityMemory, StoryMemoryPatch
from miniconstruct.models.story_planning import StoryReferenceRecommendation
from miniconstruct.models.sequence_subject import SequenceSubject
from miniconstruct.project_store import ProjectStore, StaleProjectSnapshotError
from miniconstruct.execution_routing import GenerationRouting
from miniconstruct.story_operations import build_initial_range, initial_range_authority_fingerprint
from miniconstruct.h3.vision_input import model_vision_stats
from miniconstruct.models.workspace import H3Mode


LLM = LLMSettings.model_validate({
    "modelId": "offline", "endpoint": {"id": "offline", "displayName": "Offline", "baseUrl": "http://never.test/v1"},
})


@pytest.mark.asyncio
async def test_story_vision_is_opt_in_and_uses_disposable_bounded_images(tmp_path, workspace_factory, image_asset_factory):
    assets = []
    for index in range(3):
        picture = image_asset_factory(asset_id=f"identity-{index}", order=index)
        assets.append(picture.model_dump(mode="json", by_alias=True))
    image = BytesIO()
    Image.new("RGBA", (1536, 1024), (80, 95, 120, 255)).save(image, format="PNG")
    environment = image_asset_factory(asset_id="bedroom", role="environment", order=3).model_dump(mode="json", by_alias=True)
    environment["notes"] = "Bedroom doorway is on the north wall."
    environment["image"] = {"data_url": "data:image/png;base64," + base64.b64encode(image.getvalue()).decode(),
                            "width": 1536, "height": 1024}
    assets.append(environment)
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory(assets=assets), story_idea="Plan a bedroom scene.")
    original = store.read_story_snapshot(project.project_id).generations[0].workspace.model_dump(mode="json", by_alias=True)
    requests = []
    for capability, preference in [(True, False), (False, True), (True, True)]:
        client = OfflineInitialRangeClient()
        await build_initial_range(store, project.project_id, StoryInitialRangeRequest(
            requestedTotalCount=2, llm=LLM.model_copy(update={"supports_vision": capability,
                                                       "story_planner_vision": preference})), client)
        requests.append(client.calls[0])
    off, incapable, on = requests
    assert all(isinstance(messages[1]["content"], str) for messages in (off, incapable))
    assert "Bedroom doorway is on the north wall." in off[1]["content"]
    off_context = json.loads(off[1]["content"])
    on_context = json.loads(on[1]["content"][0]["text"])
    off_context.pop("memoryCreationIds")
    on_context.pop("memoryCreationIds")
    assert on_context == off_context
    image_parts = [part for part in on[1]["content"] if part["type"] == "image_url"]
    assert len(image_parts) == 4
    assert all(part["image_url"]["url"].startswith("data:image/jpeg;base64,") for part in image_parts)
    assert max(max(item["width"], item["height"]) for item in model_vision_stats(on)) == 768
    assert store.read_story_snapshot(project.project_id).generations[0].workspace.model_dump(mode="json", by_alias=True) == original
    h3_workspace = store.read_story_snapshot(project.project_id).generations[0].workspace.model_copy(update={"mode": H3Mode.REF2VA})
    h3 = assemble_prompt(h3_workspace, True)
    assert len([part for part in h3.messages[1]["content"] if part["type"] == "image_url"]) == 4
    assert LLM.story_planner_vision is False
    assert LLM.model_copy(update={"supports_vision": True}).story_vision_enabled is False


class OfflineInitialRangeClient:
    """The production structured response path, with no provider/network access."""

    def __init__(self, transform=None):
        self.transform = transform
        self.calls = []

    def __call__(self, settings):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def generate(self, messages, *, response_instruction=None):
        assert response_instruction is None
        self.calls.append(messages)
        content = messages[1]["content"]
        context = json.loads(content[0]["text"] if isinstance(content, list) else content)
        facts = context["memoryCreationIds"]["newFactIds"]
        threads = context["memoryCreationIds"]["newThreadIds"]
        pairs = context["allowedReferencePairs"]
        result = {
            "sequenceContext": context["sequenceContext"] or "A stormy courier route ends at the old observatory.",
            "items": [{
                # The service must restore saved G1 text instead of trusting this attempted rewrite.
                "scenePlan": f"Model beat {index}",
                "dialogue": "Model: This must not replace saved G1 dialogue.",
                "incomingState": f"Arrival {index}",
                "intendedOutgoingState": f"Departure {index}",
                "relationshipToPrevious": None if index == 0 else "new_shot_preserve_continuity",
                "cameraIntent": f"Camera {index}",
                "shotCount": 1,
                "referenceRecommendations": ([{
                    **pairs[0], "recommendation": "Use the committed G1 identity evidence.",
                }] if pairs and index else []),
                "memoryPatchAfter": ({
                    "recentSummary": "The courier accepts the sealed map.",
                    "factUpserts": [{"id": facts[0], "text": "The courier carries a sealed map."}],
                    "threadUpserts": [{"id": threads[0], "text": "Deliver the map before the storm closes the pass."}],
                } if index == 0 else {
                    "factUpserts": [{"id": facts[0], "text": f"The courier carries the sealed map through beat {index + 1}."}],
                    **({"resolvedThreadIds": [threads[0]]} if index == context["requestedTotalCount"] - 1 else {}),
                }),
            } for index in range(context["requestedTotalCount"])],
            "warnings": [],
        }
        if self.transform:
            self.transform(result, context)
        return json.dumps(result)


class DelayedOfflineInitialRangeClient(OfflineInitialRangeClient):
    def __init__(self):
        super().__init__()
        self.started = asyncio.Event()
        self.resume = asyncio.Event()

    async def generate(self, messages, *, response_instruction=None):
        self.started.set()
        await self.resume.wait()
        return await super().generate(messages, response_instruction=response_instruction)


def saved_story(tmp_path, workspace_factory, image_asset_factory=None):
    store = ProjectStore(tmp_path)
    assets = [image_asset_factory(role="environment")] if image_asset_factory else []
    project = store.create_project(
        workspace_factory(
            creativeRequest="User-authored G1 request.", dialogue="Courier: I will carry it.\n", assets=assets,
        ),
        story_idea="A courier must deliver a map before a storm.",
        sequence_context="The pass will close at nightfall.",
        incoming_state="At the gate.", intended_outgoing_state="Heading into the pass.",
        planning={"relationshipToPrevious": None, "cameraIntent": "User-authored G1 camera."},
    )
    return store, project


def files(store):
    return {path.relative_to(store.root).as_posix(): path.read_bytes() for path in store.root.rglob("*") if path.is_file()}


async def propose(store, project, count, client=None):
    return await build_initial_range(
        store, project.project_id, StoryInitialRangeRequest(requestedTotalCount=count, llm=LLM),
        client or OfflineInitialRangeClient(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("g1_duration,proposals,expected", [
    (6, [6, 14, 12], [6, 14, 12]),
    (6, [6, None, 99], [6, 6, 6]),
    (15, [15, 13, 14], [15, 13, 14]),
])
async def test_initial_range_new_durations_are_independent_with_g1_fallback(
    tmp_path, workspace_factory, g1_duration, proposals, expected,
):
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory(durationSeconds=g1_duration),
                                   story_idea="A courier delivers a map.")

    def durations(result, context):
        assert context["fallbackDurationSeconds"] == g1_duration
        for item, duration in zip(result["items"], proposals):
            if duration is not None:
                item["durationSeconds"] = duration

    draft = await propose(store, project, 3, OfflineInitialRangeClient(durations))
    assert [item.duration_seconds for item in draft.items] == [g1_duration, *[
        value if type(value) is int and 4 <= value <= 15 else None for value in proposals[1:]
    ]]
    store.apply_story_initial_range(project.project_id, draft)
    saved = store.read_story_snapshot(project.project_id)
    assert [g.workspace.duration_seconds for g in saved.generations] == expected


@pytest.mark.asyncio
async def test_initial_range_preserves_authored_g1_story_controls_and_references(
    tmp_path, workspace_factory, image_asset_factory,
):
    store = ProjectStore(tmp_path)
    image = image_asset_factory(role="environment")
    project = store.create_project(workspace_factory(
        assets=[image.model_dump(mode="json", by_alias=True)],
        dialogue="Courier: Finally...",
        creativeControls={"camera": {"zoom": "prefer"}, "music": {"mode": "off"},
                          "tonePerformance": {"drama": "strong"}},
    ), story_idea="A courier crosses the pass.",
        incoming_state="At the gate.", intended_outgoing_state="Inside the pass.",
        planning={"scenePlan": "The courier passes the gate.", "cameraIntent": "Wide tracking shot."})
    before = store.read_story_snapshot(project.project_id).generations[0]

    def rewrite(result, _context):
        result["items"][0].update({
            "scenePlan": "Replace the scene.", "dialogue": "Model: Changed.",
            "incomingState": "Elsewhere.", "intendedOutgoingState": "Elsewhere again.",
            "cameraIntent": "Close static shot.", "durationSeconds": 14,
            "tonePerformance": {"drama": "auto", "tension": "moderate"},
        })

    draft = await propose(store, project, 2, OfflineInitialRangeClient(rewrite))
    assert draft.items[0].scene_plan == before.planning.scene_plan
    assert draft.items[0].dialogue == before.workspace.dialogue
    assert "tonePerformance" not in draft.items[0].model_dump(by_alias=True)
    store.apply_story_initial_range(project.project_id, draft)
    after = store.read_story_snapshot(project.project_id).generations[0]
    assert after.generation_id == before.generation_id
    assert after.planning.scene_plan == before.planning.scene_plan
    assert after.planning.camera_intent == before.planning.camera_intent
    assert after.workspace.dialogue == before.workspace.dialogue
    assert after.incoming_state == before.incoming_state
    assert after.intended_outgoing_state == before.intended_outgoing_state
    assert after.workspace.duration_seconds == before.workspace.duration_seconds
    assert after.workspace.assets == before.workspace.assets
    assert after.workspace.creative_controls.camera == before.workspace.creative_controls.camera
    assert after.workspace.creative_controls.music == before.workspace.creative_controls.music
    assert after.workspace.creative_controls.tone_performance.drama.value == "strong"
    assert after.workspace.creative_controls.tone_performance.tension.value == "auto"
    request = GenerationRequest.model_validate({
        "workspace": after.workspace.model_dump(mode="json", by_alias=True),
        "filesystemProjectId": project.project_id, "generationId": after.generation_id,
        "authoringPath": "story", "llm": LLM.model_dump(mode="json", by_alias=True),
    })
    resolved = resolve_narrative_request(request, store)
    inspector = assemble_prompt(resolved.prompt_workspace, False, resolved.narrative_subjects,
                                resolved.continuity_context, resolved.story_context).inspector_text
    assert "Strongly emphasize emotional weight" in inspector
    assert "Build clear suspense" not in inspector
    assert "Non-diegetic music: forbidden" in inspector
    assert "Prefer: Zoom" in inspector


async def start_delayed_proposal(store, project, client):
    task = asyncio.create_task(propose(store, project, 3, client))
    await asyncio.wait_for(client.started.wait(), timeout=5)
    return task


def save_current_project(store, current, *, workspace=None, **changes):
    return store.save_project(
        current.project_id, workspace or current.workspace,
        expected_project_fingerprint=current.project_fingerprint,
        story_idea=changes.get("story_idea", current.story_idea),
        story_creative_direction=changes.get("story_creative_direction", current.story_creative_direction),
        sequence_context=changes.get("sequence_context", current.sequence_context),
        incoming_state=changes.get("incoming_state", current.incoming_state),
        intended_outgoing_state=changes.get("intended_outgoing_state", current.intended_outgoing_state),
        planning=changes.get("planning", current.planning),
        character_profiles=changes.get("character_profiles"),
        dialogue_language=current.dialogue_language,
    )


async def test_estimated_four_generation_overage_warns_and_still_calls_provider_once(
    tmp_path, workspace_factory,
):
    store, project = saved_story(tmp_path, workspace_factory)
    client = OfflineInitialRangeClient()
    request = StoryInitialRangeRequest(
        requestedTotalCount=4, llm=LLM.model_copy(update={"context_window": 16384, "max_tokens": 12000}),
    )
    proposal = await build_initial_range(store, project.project_id, request, client)
    assert len(client.calls) == 1
    assert any("estimate is approximate" in warning for warning in proposal.warnings)


async def test_provider_context_failure_is_surfaced_without_retry_or_reduction(
    tmp_path, workspace_factory,
):
    store, project = saved_story(tmp_path, workspace_factory)

    class RejectingClient(OfflineInitialRangeClient):
        async def generate(self, messages, *, response_instruction=None):
            self.calls.append(messages)
            raise LLMBackendError("Provider context length exceeded", 400, category="provider_context",
                                  safe_message="Provider context length exceeded")

    client = RejectingClient()
    request = StoryInitialRangeRequest(
        requestedTotalCount=4, llm=LLM.model_copy(update={"context_window": 16384}),
    )
    with pytest.raises(LLMBackendError, match="Provider context length exceeded") as error:
        await build_initial_range(store, project.project_id, request, client)
    assert error.value.category == "provider_context"
    assert len(client.calls) == 1
    context = json.loads(client.calls[0][1]["content"])
    assert context["requestedTotalCount"] == 4


async def test_initial_range_exposes_only_current_name_and_preserves_old_saved_prose(
    tmp_path, workspace_factory,
):
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory(
        subjects=[{"id": "girl", "number": 1}], creativeRequest="OldName searches the city.",
    ), story_idea="OldName notices a discarded wheel.")
    store.set_subject_narrative_name(project.project_id, "girl", "OldName")
    project = store.set_subject_narrative_name(project.project_id, "girl", "NewName")

    def current_name_response(result, _context):
        result["items"][1]["scenePlan"] = "NewName investigates the wheel."
        result["items"][1]["incomingState"] = "NewName notices the clue."

    client = OfflineInitialRangeClient(current_name_response)
    proposal = await propose(store, project, 2, client)
    context = json.loads(client.calls[0][1]["content"])
    assert context["sequenceNarrativeCast"] == [{
        "subjectId": "girl", "canonicalH3Label": "<Subject 1>", "narrativeName": "NewName",
    }]
    assert "OldName" in json.dumps(client.calls)
    assert "OldName" not in json.dumps(context["sequenceNarrativeCast"])
    assert context["storyIdea"] == "OldName notices a discarded wheel."
    assert context["generation1"]["scenePlan"] == ""
    assert proposal.items[1].scene_plan == "NewName investigates the wheel."


@pytest.mark.parametrize("count", [2, 4, 5, 8, 12])
async def test_bounded_initial_range_preserves_g1_and_applies_sequential_memory(
    tmp_path, workspace_factory, image_asset_factory, count,
):
    store, project = saved_story(tmp_path, workspace_factory, image_asset_factory)
    before = store.read_story_snapshot(project.project_id)
    disk = files(store)
    client = OfflineInitialRangeClient()
    proposal = await propose(store, project, count, client)

    assert len(client.calls) == 1 and files(store) == disk
    context = json.loads(client.calls[0][1]["content"])
    assert context["soleGenerationPrerequisite"] is True
    assert context["requestedTotalCount"] == count
    assert "slotIds" not in context
    assert "planItemId" not in json.dumps(context["responseSchema"])
    system = client.calls[0][0]["content"]
    assert "position-binds" in system and "reserve space for explicit ending/aftermath intent" in system
    assert "Keep patches sparse" in system and "transient navigation" in system
    assert "ferry" not in system.lower() and "beacon" not in system.lower()
    assert context["storyIdea"] == project.story_idea
    assert context["sequenceContext"] == before.sequence_context
    assert context["storyCreativeDirection"] == project.story_creative_direction.model_dump(by_alias=True)
    assert context["generation1"]["generationId"] == before.generations[0].generation_id
    assert context["emptyPreG1StoryMemory"] == {"recentSummary": "", "durableFacts": [], "openThreads": []}
    assert len(context["memoryCreationIds"]["newFactIds"]) == len(context["memoryCreationIds"]["newThreadIds"]) == 16
    assert len(proposal.items) == count
    assert proposal.items[0].existing_generation_id == before.generations[0].generation_id
    assert all(item.existing_generation_id is None for item in proposal.items[1:])
    assert proposal.items[0].scene_plan == "Model beat 0"
    assert proposal.items[0].dialogue == "Courier: I will carry it.\n"
    assert proposal.items[0].camera_intent == "User-authored G1 camera."
    assert all(item.memory_patch_after for item in proposal.items)
    assert proposal.warnings == []  # Blank dialogue alone means intentional silence.

    applied = store.apply_story_initial_range(project.project_id, proposal)
    after = store.read_story_snapshot(project.project_id)
    assert applied.active_generation_id == before.manifest.sequence.active_generation_id
    assert [generation.generation_id for generation in after.generations[:1]] == [before.generations[0].generation_id]
    assert len(after.generations) == count
    assert all(UUID(generation.generation_id).version == 4 for generation in after.generations[1:])
    assert all(generation.generation_id not in proposal.authority.slot_ids for generation in after.generations[1:])
    assert all(generation.story_memory_status == "trusted" for generation in after.generations)
    assert "sealed map" in after.generations[0].story_memory_after.durable_facts[0].text
    assert "beat 2" in after.generations[1].story_memory_after.durable_facts[0].text
    assert not after.generations[-1].story_memory_after.open_threads
    persisted = json.dumps(after.manifest.model_dump(mode="json", by_alias=True))
    assert not any(temporary in persisted for temporary in proposal.authority.new_fact_ids + proposal.authority.new_thread_ids)
    assert after.generations[0].workspace.dialogue == "Courier: I will carry it.\n"
    assert len(after.generations[1].workspace.assets) == 1


async def test_initial_range_participation_carries_kent_identity_without_recommendation_and_restores_after_absence(
    tmp_path, workspace_factory, image_asset_factory,
):
    """Regression for a visible Kent card whose new Generation used to have no reference."""
    kent = image_asset_factory(asset_id="kent-g1-identity", role="subject_identity")
    kent_data = kent.model_dump(by_alias=True)
    kent_data["subjectIdentity"] = {"subjectId": "kent", "focus": "face", "view": "front"}
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory(
        subjects=[{"id": "kent", "number": 1}], nextSubjectNumber=2, assets=[kent_data],
        creativeRequest="Kent waits above the sewer entrance.",
    ))
    project = store.set_subject_narrative_name(project.project_id, "kent", "Kent")
    source_id = project.active_generation_id

    def kent_slots(result, _context):
        # G2 and G4 depict Kent. G3 only discusses him; no recommendation is
        # supplied for any of them, exactly matching the production failure.
        for index, item in enumerate(result["items"]):
            item["participatingSubjectIds"] = ["kent"] if index in {1, 3} else []
            item["referenceRecommendations"] = []
        result["items"][1]["scenePlan"] = (
            "Kent descends into the sewers, walking past mossy walls until he arrives at the ancient stone door."
        )
        result["items"][2]["scenePlan"] = "Alice tells Bob that Kent went missing."

    proposal = await propose(store, project, 4, OfflineInitialRangeClient(kent_slots))
    applied = store.apply_story_initial_range(project.project_id, proposal)
    after = ProjectStore(store.root).read_story_snapshot(applied.project_id).generations
    assert [subject.id for subject in after[1].workspace.subjects] == ["kent"]
    assert not after[2].workspace.subjects and not after[2].workspace.assets
    assert [subject.id for subject in after[3].workspace.subjects] == ["kent"]
    for generation in (after[1], after[3]):
        assert len(generation.workspace.assets) == 1
        asset = generation.workspace.assets[0]
        assert asset.role == "subject_identity" and asset.image == kent.image
        # UUIDv5 is target-local, while the source payload is the committed G1 evidence.
        assert asset.id != "kent-g1-identity"
    assert source_id == proposal.authority.g1_generation_id


async def test_initial_range_rejects_g1_participation_outside_local_workspace_authority(
    tmp_path, workspace_factory, image_asset_factory,
):
    kent = image_asset_factory(asset_id="kent-g1-identity", role="subject_identity")
    kent_data = kent.model_dump(by_alias=True)
    kent_data["subjectIdentity"] = {"subjectId": "kent", "focus": "face", "view": "front"}
    store = ProjectStore(tmp_path)
    project = store.create_project(
        workspace_factory(subjects=[{"id": "kent", "number": 1}], nextSubjectNumber=2, assets=[kent_data],
                          dialogue="Kent: I am ready.\n"),
        sequence_subjects=[SequenceSubject(id="kent", number=1), SequenceSubject(id="alice", number=2)],
    )
    before = store.read_story_snapshot(project.project_id).generations[0].workspace.model_dump(mode="json", by_alias=True)

    def malicious(result, _context):
        result["items"][0]["participatingSubjectIds"] = ["alice"]
        result["items"][1]["participatingSubjectIds"] = ["kent", "alice"]

    proposal = await propose(store, project, 2, OfflineInitialRangeClient(malicious))
    assert proposal.items[0].participating_subject_ids == ["kent"]
    store.apply_story_initial_range(project.project_id, proposal)
    after = store.read_story_snapshot(project.project_id).generations
    assert after[0].workspace.model_dump(mode="json", by_alias=True) == before
    assert [subject.id for subject in after[1].workspace.subjects] == ["kent", "alice"]
    assert [asset.subject_identity.subject_id for asset in after[1].workspace.assets] == ["kent"]
    assert len(after) == 2 and after[1].planning.scene_plan == "Model beat 1"


@pytest.mark.asyncio
async def test_initial_range_streams_provider_response_and_recovers_from_gateway_timeout(
    tmp_path, workspace_factory,
):
    store, project = saved_story(tmp_path, workspace_factory)
    observed_stream_flags = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        observed_stream_flags.append(payload["stream"])
        if not payload["stream"]:
            return httpx.Response(502, text="gateway timed out while waiting for provider")
        content = payload["messages"][1]["content"]
        context = json.loads(content[0]["text"] if isinstance(content, list) else content)
        facts = context["memoryCreationIds"]["newFactIds"]
        threads = context["memoryCreationIds"]["newThreadIds"]
        result = {
            "sequenceContext": context["sequenceContext"],
            "items": [{
                "scenePlan": f"Beat {index + 1}",
                "dialogue": "",
                "incomingState": f"Arrival {index + 1}",
                "intendedOutgoingState": f"Departure {index + 1}",
                "relationshipToPrevious": None if index == 0 else "new_shot_preserve_continuity",
                "cameraIntent": f"Camera {index + 1}",
                "shotCount": 1,
                "referenceRecommendations": [],
                "memoryPatchAfter": {
                    "recentSummary": "",
                    "factUpserts": [{"id": facts[0], "text": f"Fact {index + 1}"}],
                    "threadUpserts": [{"id": threads[0], "text": f"Thread {index + 1}"}],
                },
            } for index in range(context["requestedTotalCount"])],
            "warnings": [],
        }
        event = json.dumps({"choices": [{"delta": {"content": json.dumps(result)}}]})
        body = f"data: {event}\n\ndata: [DONE]\n\n".encode()
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    transport = httpx.MockTransport(handler)
    async with OpenAICompatibleClient(LLM, transport) as client:
        with pytest.raises(LLMBackendError, match="gateway timed out") as error:
            await client.generate([{"role": "user", "content": "probe"}], response_instruction=None)
        assert error.value.status_code == 502 and error.value.upstream_status == 502

    proposal = await build_initial_range(
        store, project.project_id,
        StoryInitialRangeRequest(requestedTotalCount=3, llm=LLM),
        lambda settings: OpenAICompatibleClient(settings, transport),
    )

    assert observed_stream_flags == [False, True]
    assert len(proposal.items) == 3
    assert proposal.items[0].existing_generation_id == project.active_generation_id
    assert proposal.items[1].existing_generation_id is None


def test_story_participation_identity_reaches_normal_h3_reference_manifest(
    workspace_factory, image_asset_factory,
):
    identity = image_asset_factory(asset_id="kent-identity", role="subject_identity")
    data = identity.model_dump(by_alias=True)
    data["subjectIdentity"] = {"subjectId": "kent", "focus": "face", "view": "front"}
    workspace = workspace_factory(
        mode="Ref2VA", subjects=[{"id": "kent", "number": 1}], nextSubjectNumber=2, assets=[data],
    )
    manifest = build_reference_manifest(workspace)
    assert [asset["subjectIdentity"]["subjectId"] for asset in manifest["assets"]] == ["kent"]
    assert "kent" in json.dumps(build_h3_input_map(workspace), ensure_ascii=False)


async def test_initial_range_participation_deduplicates_redundant_identity_recommendation_and_rejects_unknown_ids(
    tmp_path, workspace_factory, image_asset_factory,
):
    identity = image_asset_factory(asset_id="kent-identity", role="subject_identity")
    data = identity.model_dump(by_alias=True); data["subjectIdentity"] = {"subjectId": "kent"}
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory(subjects=[{"id": "kent", "number": 1}], nextSubjectNumber=2, assets=[data]))

    def duplicate(result, context):
        result["items"][1]["participatingSubjectIds"] = ["kent"]
        result["items"][1]["referenceRecommendations"] = [{
            **context["allowedReferencePairs"][0], "recommendation": "Redundant identity recommendation.",
        }]

    proposal = await propose(store, project, 2, OfflineInitialRangeClient(duplicate))
    store.apply_story_initial_range(project.project_id, proposal)
    assert len(store.read_story_snapshot(project.project_id).generations[1].workspace.assets) == 1

    # The response cannot invent a canonical participant.
    second_store = ProjectStore(tmp_path / "unknown")
    second = second_store.create_project(workspace_factory(subjects=[{"id": "kent", "number": 1}], nextSubjectNumber=2))
    def unknown(result, _context): result["items"][1]["participatingSubjectIds"] = ["invented"]
    with pytest.raises(LLMBackendError, match="participatingSubjectIds"):
        await propose(second_store, second, 2, OfflineInitialRangeClient(unknown))


def test_initial_range_request_rejects_outside_two_to_twelve():
    for count in [1, 13, 2.5]:
        with pytest.raises(ValueError):
            StoryInitialRangeRequest(requestedTotalCount=count, llm=LLM)


def test_initial_range_api_accepts_twelve_and_rejects_outside_range(monkeypatch):
    received = []

    async def fake_build(store, project_id, request):
        received.append((project_id, request.requested_total_count))
        raise ValueError("test request accepted")

    monkeypatch.setattr(routes, "build_initial_range", fake_build)
    body = {"requestedTotalCount": 12, "llm": LLM.model_dump(mode="json", by_alias=True)}
    with TestClient(app) as client:
        assert client.post("/api/projects/test-project/story-plan/initial-range", json=body).status_code == 400
        for count in (1, 13):
            rejected = client.post("/api/projects/test-project/story-plan/initial-range", json={**body, "requestedTotalCount": count})
            assert rejected.status_code == 422
    assert received == [("test-project", 12)]


def test_initial_range_stale_409_has_safe_server_and_browser_detail(monkeypatch, caplog):
    detail = "Story authority used by Build Initial Range changed while planning; review saved Story fields and try again"

    async def stale_build(store, project_id, request):
        raise StaleProjectSnapshotError(detail)

    monkeypatch.setattr(routes, "build_initial_range", stale_build)
    caplog.set_level(logging.WARNING, logger="miniconstruct.api.routes")
    body = {"requestedTotalCount": 3, "llm": LLM.model_dump(mode="json", by_alias=True)}
    with TestClient(app) as client:
        response = client.post("/api/projects/test-project/story-plan/initial-range", json=body)
    assert response.status_code == 409
    assert response.json()["detail"] == detail
    assert "Initial Range failed: category=story_snapshot_stale status=409 detail=" + detail in caplog.text


async def test_initial_range_requires_the_sole_saved_generation(tmp_path, workspace_factory):
    store, project = saved_story(tmp_path, workspace_factory)
    store.create_generation(project.project_id)
    with pytest.raises(ValueError, match="sole canonical"):
        await propose(store, project, 2)


async def test_initial_range_accepts_valid_response_after_long_unmutated_wait(tmp_path, workspace_factory):
    store, project = saved_story(tmp_path, workspace_factory)
    before = files(store)
    client = DelayedOfflineInitialRangeClient()
    task = await start_delayed_proposal(store, project, client)
    assert files(store) == before
    client.resume.set()
    proposal = await task
    assert len(client.calls) == 1
    assert len(proposal.items) == 3


async def test_initial_range_accepts_noop_save_with_new_manifest_timestamps(
    tmp_path, workspace_factory,
):
    store, project = saved_story(tmp_path, workspace_factory)
    before = store.read_story_snapshot(project.project_id)
    start_authority_fingerprint = initial_range_authority_fingerprint(before)
    client = DelayedOfflineInitialRangeClient()
    task = await start_delayed_proposal(store, project, client)

    store._now = lambda: "2099-01-01T00:00:00.000Z"
    saved = save_current_project(store, store.load_project(project.project_id))
    after = store.read_story_snapshot(project.project_id)
    assert saved.updated_at == "2099-01-01T00:00:00.000Z"
    assert after.fingerprint != before.fingerprint
    assert initial_range_authority_fingerprint(after) == start_authority_fingerprint

    client.resume.set()
    proposal = await task
    assert len(proposal.items) == 3
    assert len(client.calls) == 1


async def test_initial_range_accepts_read_only_and_execution_sidecar_activity(
    tmp_path, workspace_factory,
):
    store, project = saved_story(tmp_path, workspace_factory)
    before = store.read_story_snapshot(project.project_id)
    client = DelayedOfflineInitialRangeClient()
    task = await start_delayed_proposal(store, project, client)

    store.load_project(project.project_id)
    store.read_story_snapshot(project.project_id)
    store.set_execution_routing(project.project_id, before.generations[0].generation_id, GenerationRouting())
    assert store.read_story_snapshot(project.project_id).fingerprint == before.fingerprint

    client.resume.set()
    assert len((await task).items) == 3


async def test_initial_range_accepts_unrelated_main_prompt_edit_during_provider_call(
    tmp_path, workspace_factory,
):
    store, project = saved_story(tmp_path, workspace_factory)
    client = DelayedOfflineInitialRangeClient()
    task = await start_delayed_proposal(store, project, client)

    current = store.load_project(project.project_id)
    edited_workspace = current.workspace.model_copy(update={"creative_request": "A new one-shot request."})
    save_current_project(store, current, workspace=edited_workspace)

    client.resume.set()
    assert len((await task).items) == 3


@pytest.mark.parametrize("change", [
    "story_idea", "story_direction", "sequence_context", "scene_plan", "dialogue",
    "incoming_state", "outgoing_state", "camera_intent", "duration", "profile", "cast_name", "tone",
])
async def test_initial_range_rejects_relevant_story_authority_edits_while_provider_runs(
    tmp_path, workspace_factory, change,
):
    store = ProjectStore(tmp_path)
    project = store.create_project(
        workspace_factory(subjects=[{"id": "courier", "number": 1}], dialogue="Courier: Ready.\n"),
        story_idea="A courier crosses a storm pass.",
        sequence_context="The pass closes at nightfall.",
        planning={"relationshipToPrevious": None, "scenePlan": "The courier waits at the gate."},
    )
    client = DelayedOfflineInitialRangeClient()
    task = await start_delayed_proposal(store, project, client)

    current = store.load_project(project.project_id)
    if change == "story_idea":
        save_current_project(store, current, story_idea="The courier loses the map.")
    elif change == "story_direction":
        save_current_project(store, current, story_creative_direction={"toneMood": "Noir restraint"})
    elif change == "sequence_context":
        save_current_project(store, current, sequence_context="The pass now opens at dawn.")
    elif change == "scene_plan":
        save_current_project(store, current, planning=current.planning.model_copy(
            update={"scene_plan": "The courier enters the old observatory."},
        ))
    elif change == "dialogue":
        save_current_project(store, current, workspace=current.workspace.model_copy(
            update={"dialogue": "Courier: The map is gone.\n"},
        ))
    elif change == "incoming_state":
        save_current_project(store, current, incoming_state="At the old observatory.")
    elif change == "outgoing_state":
        save_current_project(store, current, intended_outgoing_state="The courier returns to the pass.")
    elif change == "camera_intent":
        save_current_project(store, current, planning=current.planning.model_copy(
            update={"camera_intent": "Track the courier through the gate."},
        ))
    elif change == "duration":
        save_current_project(store, current, workspace=current.workspace.model_copy(
            update={"duration_seconds": 12},
        ))
    elif change == "profile":
        save_current_project(store, current, character_profiles=[{
            "subjectId": "courier", "characterProfile": {"roleBackground": "A veteran courier."},
        }])
    elif change == "cast_name":
        store.set_subject_narrative_name(project.project_id, "courier", "Mara")
    else:
        tone = current.workspace.creative_controls.tone_performance
        changed_tone = tone.__class__.model_validate({**tone.model_dump(mode="json", by_alias=True), "drama": "strong"})
        controls = current.workspace.creative_controls.model_copy(update={
            "tone_performance": changed_tone,
        })
        save_current_project(store, current, workspace=current.workspace.model_copy(
            update={"creative_controls": controls},
        ))

    client.resume.set()
    with pytest.raises(StaleProjectSnapshotError, match="changed while planning"):
        await task


async def test_initial_range_rejects_reference_authority_edit_while_provider_runs(
    tmp_path, workspace_factory, image_asset_factory,
):
    store, project = saved_story(tmp_path, workspace_factory, image_asset_factory)
    client = DelayedOfflineInitialRangeClient()
    task = await start_delayed_proposal(store, project, client)
    current = store.load_project(project.project_id)
    save_current_project(store, current, workspace=current.workspace.model_copy(update={"assets": []}))
    client.resume.set()
    with pytest.raises(StaleProjectSnapshotError, match="reference authority changed"):
        await task


async def test_initial_range_rejects_sequence_structure_edit_while_provider_runs(
    tmp_path, workspace_factory,
):
    store, project = saved_story(tmp_path, workspace_factory)
    client = DelayedOfflineInitialRangeClient()
    task = await start_delayed_proposal(store, project, client)
    store.create_generation(project.project_id)
    client.resume.set()
    with pytest.raises(StaleProjectSnapshotError, match="prerequisite changed"):
        await task


async def test_initial_range_rejects_g1_memory_edit_while_provider_runs(tmp_path, workspace_factory):
    store, project = saved_story(tmp_path, workspace_factory)
    client = DelayedOfflineInitialRangeClient()
    task = await start_delayed_proposal(store, project, client)
    current = store.load_project(project.project_id)
    store.save_project(project.project_id, current.workspace,
                       story_memory_generation_id=current.active_generation_id,
                       story_memory=StoryContinuityMemory(recentSummary="A newly saved G1 memory."))
    before_release = files(store)
    client.resume.set()
    with pytest.raises(StaleProjectSnapshotError, match="changed while planning"):
        await task
    assert files(store) == before_release


@pytest.mark.parametrize("change", ["idea", "direction", "g1", "structure"])
async def test_initial_range_stales_for_relevant_authority(tmp_path, workspace_factory, change):
    store, project = saved_story(tmp_path, workspace_factory)
    proposal = await propose(store, project, 12)
    before = files(store)
    if change == "idea":
        current = store.load_project(project.project_id)
        store.save_project(project.project_id, current.workspace, story_idea="Changed canonical story idea.")
    elif change == "direction":
        current = store.load_project(project.project_id)
        store.save_project(project.project_id, current.workspace, story_creative_direction={"toneMood": "Noir restraint"})
    elif change == "g1":
        current = store.load_project(project.project_id)
        save_current_project(store, current, planning=current.planning.model_copy(
            update={"scene_plan": "Changed G1 Scene Plan authority."},
        ))
    else:
        store.create_generation(project.project_id)
    changed = files(store)
    assert changed != before
    with pytest.raises(StaleProjectSnapshotError):
        store.apply_story_initial_range(project.project_id, proposal)
    assert files(store) == changed


@pytest.mark.parametrize("damage", ["unknown-memory", "reused-memory", "future-reference", "item-id"])
async def test_invalid_initial_range_apply_is_atomic(tmp_path, workspace_factory, damage):
    store, project = saved_story(tmp_path, workspace_factory)
    proposal = await propose(store, project, 12)
    if damage == "unknown-memory":
        proposal.items[-1].memory_patch_after = StoryMemoryPatch(resolvedFactIds=[str(uuid4())])
    elif damage == "reused-memory":
        temporary = proposal.authority.new_fact_ids[0]
        proposal.items[1].memory_patch_after = StoryMemoryPatch(resolvedFactIds=[temporary])
        proposal.items[2].memory_patch_after = StoryMemoryPatch(factUpserts=[{"id": temporary, "text": "Reuse is forbidden."}])
    elif damage == "future-reference":
        proposal.items[1].reference_recommendations = [StoryReferenceRecommendation(
            sourceGenerationId=str(uuid4()), assetId="invented", recommendation="Invalid",
        )]
    else:
        proposal.items[-1].plan_item_id = str(uuid4())
    before = files(store)
    with pytest.raises(ValueError):
        store.apply_story_initial_range(project.project_id, proposal)
    assert files(store) == before


async def test_twelve_total_initial_range_keeps_order_and_allocates_eleven_new_generations(
    tmp_path, workspace_factory,
):
    store, project = saved_story(tmp_path, workspace_factory)
    before = store.read_story_snapshot(project.project_id)
    client = OfflineInitialRangeClient()

    proposal = await propose(store, project, 12, client)

    assert len(client.calls) == 1
    assert len(proposal.items) == len(proposal.authority.slot_ids) == 12
    assert [item.plan_item_id for item in proposal.items] == proposal.authority.slot_ids
    assert proposal.items[0].existing_generation_id == before.generations[0].generation_id
    assert [item.existing_generation_id for item in proposal.items[1:]] == [None] * 11
    assert proposal.items[0].scene_plan == "Model beat 0"
    assert proposal.items[0].dialogue == "Courier: I will carry it.\n"
    assert proposal.items[0].incoming_state == "At the gate."
    assert proposal.items[0].intended_outgoing_state == "Heading into the pass."
    assert proposal.items[0].camera_intent == "User-authored G1 camera."

    store.apply_story_initial_range(project.project_id, proposal)
    after = store.read_story_snapshot(project.project_id)
    saved_ids = [generation.generation_id for generation in after.generations]
    memory_snapshots = [generation.story_memory_after.model_dump_json(by_alias=True) for generation in after.generations]

    assert len(saved_ids) == 12 and saved_ids[0] == before.generations[0].generation_id
    assert len(saved_ids[1:]) == len(set(saved_ids[1:])) == 11
    assert not set(saved_ids[1:]) & set(proposal.authority.slot_ids)
    assert len(set(memory_snapshots)) == 12
    assert all(generation.story_memory_status == "trusted" for generation in after.generations)


async def test_initial_range_model_response_rejects_authored_plan_item_ids(tmp_path, workspace_factory):
    store, project = saved_story(tmp_path, workspace_factory)

    def damage(response, context):
        response["items"][0]["planItemId"] = str(uuid4())

    with pytest.raises(LLMBackendError):
        await propose(store, project, 2, OfflineInitialRangeClient(damage))


async def test_initial_range_ignores_unused_authorized_thread_resolution_with_warning(tmp_path, workspace_factory):
    store, project = saved_story(tmp_path, workspace_factory)

    def unused_resolution(response, context):
        response["items"][3]["memoryPatchAfter"]["resolvedThreadIds"] = [
            context["memoryCreationIds"]["newThreadIds"][1],
        ]

    client = OfflineInitialRangeClient(unused_resolution)
    proposal = await propose(store, project, 8, client)
    ignored = proposal.authority.new_thread_ids[1]
    assert proposal.items[3].memory_patch_after.resolved_thread_ids == []
    assert proposal.warnings == [
        "Generation 4: Story Memory ignored a resolution for a temporary entry that had not been created.",
    ]
    store.apply_story_initial_range(project.project_id, proposal)
    after = store.read_story_snapshot(project.project_id)
    assert len(client.calls) == 1 and len(after.generations) == 8
    assert all(ignored not in {entry.id for entry in generation.story_memory_after.open_threads}
               for generation in after.generations)


@pytest.mark.parametrize("damage", ["unknown", "wrong-collection", "same-patch-conflict", "consumed-reuse"])
async def test_initial_range_keeps_other_temporary_memory_authority_failures_strict(tmp_path, workspace_factory, damage):
    store, project = saved_story(tmp_path, workspace_factory)

    def invalid_memory(response, context):
        thread = context["memoryCreationIds"]["newThreadIds"][1]
        if damage == "unknown":
            response["items"][2]["memoryPatchAfter"]["resolvedThreadIds"] = [str(uuid4())]
        elif damage == "wrong-collection":
            response["items"][2]["memoryPatchAfter"]["resolvedThreadIds"] = [context["memoryCreationIds"]["newFactIds"][1]]
        elif damage == "same-patch-conflict":
            response["items"][2]["memoryPatchAfter"] = {
                "threadUpserts": [{"id": thread, "text": "Conflicting temporary thread."}],
                "resolvedThreadIds": [thread],
            }
        else:
            response["items"][1]["memoryPatchAfter"] = {"resolvedThreadIds": [context["memoryCreationIds"]["newThreadIds"][0]]}
            response["items"][2]["memoryPatchAfter"] = {
                "threadUpserts": [{"id": context["memoryCreationIds"]["newThreadIds"][0], "text": "Reused temporary thread."}],
            }
            response["items"][3]["memoryPatchAfter"] = {}

    client = OfflineInitialRangeClient(invalid_memory)
    with pytest.raises(LLMBackendError):
        await propose(store, project, 4, client)
    assert len(client.calls) == 1


async def test_initial_range_salvages_unknown_memory_patch_fields_once_and_warns(tmp_path, workspace_factory):
    store, project = saved_story(tmp_path, workspace_factory)

    def add_unknown_field(response, _context):
        for index, item in enumerate(response["items"]):
            if index == 0:
                item["memoryPatchAfter"] = {
                    "recentSummary": "The search reaches the river.", "field": "invented value",
                }
            else:
                item["memoryPatchAfter"] = {"field": "invented value"}

    client = OfflineInitialRangeClient(add_unknown_field)
    proposal = await propose(store, project, 3, client)

    assert len(client.calls) == 1
    assert [item.shot_count for item in proposal.items] == [1, 1, 1]
    assert [item.camera_intent for item in proposal.items] == [
        "User-authored G1 camera.", "Camera 1", "Camera 2",
    ]
    patches = [item.memory_patch_after for item in proposal.items]
    assert patches[0].recent_summary == "The search reaches the river."
    assert patches[1].recent_summary is None and patches[2].recent_summary is None
    assert all(not patch.fact_upserts and not patch.thread_upserts for patch in patches)
    patch_warnings = [warning for warning in proposal.warnings if "unsupported Story Memory patch" in warning]
    assert len(patch_warnings) == 1
    assert "field" in patch_warnings[0]
    assert "Generations 1, 2, and 3" in patch_warnings[0]


async def test_initial_range_merges_model_patch_and_capacity_warnings_once(
    tmp_path, workspace_factory, monkeypatch,
):
    store, project = saved_story(tmp_path, workspace_factory)
    capacity_warning = "Completion capacity warning"
    monkeypatch.setattr("miniconstruct.story_planning.preflight_request_capacity",
                        lambda *_args: {"completionBudgetWarning": capacity_warning})

    def add_warnings(response, _context):
        response["warnings"] = [f"model {index}" for index in range(63)]
        response["items"][1]["memoryPatchAfter"]["unknown"] = "remove"

    client = OfflineInitialRangeClient(add_warnings)
    proposal = await propose(store, project, 3, client)
    assert len(client.calls) == 1
    assert len(proposal.warnings) == 64
    assert proposal.warnings[:62] == [f"model {index}" for index in range(62)]
    assert proposal.warnings[62].startswith("Ignored unsupported Story Memory patch field(s): unknown")
    assert proposal.warnings[63] == capacity_warning
    assert proposal.warnings.count(capacity_warning) == 1


async def test_initial_range_still_rejects_malformed_known_memory_patch_values(tmp_path, workspace_factory):
    store, project = saved_story(tmp_path, workspace_factory)

    def malformed_known_field(response, _context):
        response["items"][1]["memoryPatchAfter"] = {"factUpserts": "not-an-array"}

    client = OfflineInitialRangeClient(malformed_known_field)
    with pytest.raises(LLMBackendError) as error:
        await propose(store, project, 3, client)
    assert error.value.category == "story_schema"
    assert len(client.calls) == 1


@pytest.mark.parametrize("patch", ["foo", 3, [], None])
async def test_initial_range_does_not_sanitize_non_dictionary_memory_patches(tmp_path, workspace_factory, patch):
    store, project = saved_story(tmp_path, workspace_factory)
    client = OfflineInitialRangeClient(
        lambda response, _context: response["items"][1].__setitem__("memoryPatchAfter", patch)
    )
    with pytest.raises(LLMBackendError) as error:
        await propose(store, project, 3, client)
    assert error.value.category == "story_schema"
    assert len(client.calls) == 1


async def test_initial_range_warns_for_blank_required_fields_including_g1(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path)
    project = store.create_project(
        workspace_factory().model_copy(update={"creative_request": "", "dialogue": ""}), story_idea="A courier must deliver a map.",
        sequence_context="The pass will close at nightfall.", incoming_state="Known state.",
        intended_outgoing_state="Known outgoing state.", planning={"relationshipToPrevious": None, "cameraIntent": ""},
    )
    def blank_g1_fields(response, _context):
        response["items"][0]["scenePlan"] = ""
        response["items"][0]["cameraIntent"] = ""

    client = OfflineInitialRangeClient(blank_g1_fields)
    proposal = await propose(store, project, 2, client)
    assert proposal.items[0].scene_plan == proposal.items[0].camera_intent == ""
    assert proposal.items[0].incoming_state == "Known state."
    assert proposal.items[0].intended_outgoing_state == "Known outgoing state."
    assert proposal.warnings == [
        "Generation 1: Build left these planning fields incomplete: Scene Plan. Edit the draft before Apply.",
    ]
    assert len(client.calls) == 1


async def test_initial_range_rejects_wrong_item_count_without_retry_or_padding(tmp_path, workspace_factory):
    store, project = saved_story(tmp_path, workspace_factory)

    def remove_final_item(response, _context):
        response["items"].pop()

    client = OfflineInitialRangeClient(remove_final_item)
    with pytest.raises(LLMBackendError, match="wrong total Generation count"):
        await propose(store, project, 4, client)
    assert len(client.calls) == 1


async def test_initial_range_rejects_truncated_json_without_retry(tmp_path, workspace_factory):
    store, project = saved_story(tmp_path, workspace_factory)

    class TruncatedClient(OfflineInitialRangeClient):
        async def generate(self, messages, *, response_instruction=None):
            return (await super().generate(messages, response_instruction=response_instruction))[:-12]

    client = TruncatedClient()
    with pytest.raises(LLMBackendError, match="invalid structured JSON"):
        await propose(store, project, 2, client)
    assert len(client.calls) == 1


async def test_custom_direction_preserves_whitespace_and_line_endings_through_save_fork_and_planning(tmp_path, workspace_factory):
    store, project = saved_story(tmp_path, workspace_factory)
    exact = "  Slow-burn opening.\r\nThen escalate.\n\tKeep space.  "
    direction = dict.fromkeys(["toneMood", "performanceEnergy", "pacingRhythm", "beatDurationDensity"], exact)
    store.save_project(project.project_id, project.workspace, story_creative_direction=direction)
    loaded = store.load_project(project.project_id)
    fork = store.duplicate_project(project.project_id, "Custom direction fork")
    for saved in [loaded, fork]:
        assert saved.story_creative_direction.model_dump(by_alias=True) == direction
        client = OfflineInitialRangeClient()
        await propose(store, saved, 2, client)
        assert json.loads(client.calls[0][1]["content"])["storyCreativeDirection"] == direction


@pytest.mark.parametrize("vision", [False, True])
async def test_initial_range_real_g1_picture_transport_and_only_actual_reference_pairs(
    tmp_path, workspace_factory, image_asset_factory, vision,
):
    store, project = saved_story(tmp_path, workspace_factory, image_asset_factory)
    snapshot = store.read_story_snapshot(project.project_id)
    g1 = snapshot.generations[0]
    expected_pair = {"sourceGenerationId": g1.generation_id, "assetId": g1.workspace.assets[0].id}

    def invented_references(response, context):
        assert context["allowedReferencePairs"] == [expected_pair]
        response["items"][1]["referenceRecommendations"] += [
            {"sourceGenerationId": str(uuid4()), "assetId": expected_pair["assetId"], "recommendation": "Future source"},
            {"sourceGenerationId": g1.generation_id, "assetId": "invented", "recommendation": "Invented asset"},
        ]

    client = OfflineInitialRangeClient(invented_references)
    proposal = await build_initial_range(store, project.project_id,
        StoryInitialRangeRequest(requestedTotalCount=2, llm=LLM.model_copy(update={"supports_vision": vision, "story_planner_vision": vision})), client)
    content = client.calls[0][1]["content"]
    pictures = [part for part in content if part["type"] == "image_url"] if isinstance(content, list) else []
    assert len(pictures) == int(vision)
    if vision:
        assert pictures[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        assert g1.workspace.assets[0].image.data_url.startswith("data:image/png;base64,")
    else:
        assert "data:image" not in content and "image_url" not in content
    refs = proposal.items[1].reference_recommendations
    assert len(refs) == 1
    assert (refs[0].source_generation_id, refs[0].asset_id) == (g1.generation_id, expected_pair["assetId"])
    store.apply_story_initial_range(project.project_id, proposal)


@pytest.mark.parametrize("operation", ["next", "insert", "replan", "revision"])
async def test_initial_range_applied_output_immediately_accepts_ordinary_operations(
    tmp_path, workspace_factory, operation,
):
    from miniconstruct.models.story_operations import StoryNextRequest, StoryInsertRequest, StoryReplanRequest
    from miniconstruct.models.story_planning import StoryRevisionRequest
    from miniconstruct.story_operations import plan_next, plan_insert, plan_replan
    from miniconstruct.story_planning import initial_story_draft, revise_story_plan
    from test_story_next import OfflineNextClient
    from test_story_insert import OfflineInsertClient
    from test_story_replan import OfflineReplanClient
    from test_story_planning import PatchModel

    store, project = saved_story(tmp_path, workspace_factory)
    direction = {"toneMood": "Moderate horror; subtle tension.", "performanceEnergy": "Restrained performance; low energy.",
                 "pacingRhythm": "Slow pacing.", "beatDurationDensity": "Sparse beat density."}
    store.save_project(project.project_id, project.workspace, story_creative_direction=direction)
    initial_client = OfflineInitialRangeClient()
    initial = await propose(store, project, 4, initial_client)
    assert json.loads(initial_client.calls[0][1]["content"])["storyCreativeDirection"] == direction
    store.apply_story_initial_range(project.project_id, initial)
    snapshot = store.read_story_snapshot(project.project_id)
    ids = [generation.generation_id for generation in snapshot.generations]
    if operation == "next":
        client = OfflineNextClient()
        draft = await plan_next(store, project.project_id,
            StoryNextRequest(anchorGenerationId=ids[-1], requestedCount=1, llm=LLM), client)
        store.apply_story_plan_next(project.project_id, draft)
    elif operation == "insert":
        client = OfflineInsertClient()
        draft = await plan_insert(store, project.project_id,
            StoryInsertRequest(upstreamGenerationId=ids[0], requestedCount=1, llm=LLM), client)
        store.apply_story_plan_insert(project.project_id, draft)
    elif operation == "replan":
        client = OfflineReplanClient()
        draft = await plan_replan(store, project.project_id,
            StoryReplanRequest(startGenerationId=ids[1], endGenerationId=ids[2], llm=LLM), client)
        store.apply_story_plan_replan(project.project_id, draft)
    else:
        client = PatchModel()
        draft = initial_story_draft(snapshot, 4)
        draft = await revise_story_plan(store, project.project_id,
            StoryRevisionRequest(draft=draft, selectedPlanItemIds=[draft.items[1].plan_item_id],
                                 scopes=["camera_intent"], instruction="Widen the camera.", llm=LLM), client)
        store.apply_story_plan(project.project_id, draft)
    assert len(client.calls) == 1
    after = store.read_story_snapshot(project.project_id)
    assert len(after.generations) == (5 if operation in {"next", "insert"} else 4)
    assert after.manifest.schema_version == 17
    assert after.manifest.story_creative_direction.model_dump(by_alias=True) == direction
    fork = store.duplicate_project(project.project_id, "Direction fork")
    assert fork.story_creative_direction.model_dump(by_alias=True) == direction
