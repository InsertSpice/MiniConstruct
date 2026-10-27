from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from miniconstruct.app import app
from miniconstruct.execution_authoring import execution_authoring_fingerprint
from miniconstruct.h3.builder import assemble_prompt
from miniconstruct.models.api import (
    AssistedGenerationPlan,
    EndpointProfile,
    LLMSettings,
    PlanningInferenceRequest,
    PlanningRevisionRequest,
    PlanningRevisionScope,
)
from miniconstruct.models.planning import GenerationPlanning
from miniconstruct.models.sequence_subject import SequenceSubject
from miniconstruct.models.workspace import ReferenceAsset, validate_authoring_workspace
from miniconstruct.planning import (
    BASE_PLANNER_SYSTEM,
    assemble_inference_prompt,
    assemble_revision_plan_prompt,
    infer_plan,
    revise_plan,
)
from miniconstruct.project_store import MalformedProjectError, ProjectStore
from miniconstruct.story_planning import initial_story_draft


def _settings(vision: bool = False) -> LLMSettings:
    return LLMSettings(
        endpoint=EndpointProfile(id="test", displayName="Test", baseUrl="http://planner.test/v1"),
        modelId="planner",
        supportsVision=vision,
    )


class FakeClient:
    def __init__(self, response: str):
        self.response = response
        self.messages = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def generate(self, messages, *, response_instruction=None):
        self.messages = messages
        self.response_instruction = response_instruction
        return self.response


def _plan(**overrides):
    value = {
        "creativeRequest": "Continue the action.",
        "incomingState": "Alice is ready.",
        "intendedOutgoingState": "Alice reaches the door.",
        "relationshipToPrevious": "new_shot_preserve_continuity",
        "cameraIntent": "A low-angle tracking shot.",
        "referenceRecommendations": [],
    }
    value.update(overrides)
    return json.dumps(value)


def _revision_plan(request, *, dialogue=None, **overrides):
    """Return the ordinary broad planner result with exact current authority."""
    value = {
        "creativeRequest": request.workspace.creative_request,
        "incomingState": request.continuity.incoming_state or "",
        "intendedOutgoingState": request.continuity.intended_outgoing_state or "",
        "relationshipToPrevious": request.planning.relationship_to_previous,
        "cameraIntent": request.planning.camera_intent,
        "referenceRecommendations": [],
    }
    if dialogue is not None:
        value["dialogue"] = dialogue
    value.update(overrides)
    return json.dumps(value)


def _blank_workspace(workspace_factory):
    value = workspace_factory().model_dump(mode="json", by_alias=True)
    value["creativeRequest"] = ""
    return validate_authoring_workspace(value)


def test_planning_persists_lifecycle_and_partial_updates(tmp_path: Path, workspace_factory):
    store = ProjectStore(tmp_path)
    created = store.create_project(
        workspace_factory(),
        planning={"relationshipToPrevious": "independent", "cameraIntent": "Wide master"},
    )
    first = created.active_generation_id
    second = store.create_generation(created.project_id)
    second = store.save_project(
        created.project_id,
        workspace_factory(creativeRequest="Second"),
        planning={"cameraIntent": "Handheld close-up"},
    )
    assert second.planning.relationship_to_previous is None
    duplicate = store.duplicate_generation(created.project_id, second.active_generation_id)
    assert duplicate.planning.camera_intent == "Handheld close-up"
    store.activate_generation(created.project_id, first)
    loaded = store.load_project(created.project_id)
    assert loaded.planning.relationship_to_previous.value == "independent"
    assert loaded.planning.camera_intent == "Wide master"
    assert json.loads((tmp_path / f"project-{created.project_id}" / "project.json").read_text())["schemaVersion"] == 17


def test_shared_media_policy_distinguishes_intended_use_from_observed_content(workspace_factory):
    previous = type("Previous", (), {
        "ordinal": 1,
        "workspace": workspace_factory(),
        "incoming_state": "",
        "intended_outgoing_state": "",
        "planning": GenerationPlanning(),
    })()
    inference = assemble_inference_prompt(
        previous,
        PlanningInferenceRequest(
            workspace=workspace_factory(),
            llm=_settings(),
            planning=GenerationPlanning(),
        ),
    )
    revision = assemble_revision_plan_prompt(PlanningRevisionRequest(
        workspace=workspace_factory(),
        llm=_settings(),
        planning=GenerationPlanning(),
        instruction="Change only the camera.",
        scopes=[PlanningRevisionScope.CAMERA_INTENT],
    ))

    policy_fragments = [
        '"Metadata-only" Video and Audio may be recommended for intended use',
        "role, filename, notes, attachment state, and explicit textual Generation or Sequence",
        "Do not state or imply that the media itself contains specific visual content",
        "voice characteristics, music, sound, ambience, environmental audio",
        "unless those facts are explicitly provided in asset metadata or contentAnalysis",
        "When content is unknown, phrase the recommendation as intended use",
        "A declared role such as voice_timbre or camera_movement may support recommending",
        "Pictures with actual vision input are different",
    ]
    for system in (inference.messages[0]["content"], revision.messages[0]["content"]):
        assert BASE_PLANNER_SYSTEM in system
        normalized = " ".join(system.split())
        assert all(fragment in normalized for fragment in policy_fragments)
    assert inference.messages[0]["content"].count('"Metadata-only" Video and Audio') == 1
    assert revision.messages[0]["content"].count('"Metadata-only" Video and Audio') == 1


def test_api_planning_is_saved_and_omitted_fields_are_preserved(tmp_path: Path, monkeypatch, workspace_factory):
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(tmp_path))
    with TestClient(app) as client:
        created = client.post("/api/projects", json={
            "workspace": workspace_factory().model_dump(mode="json", by_alias=True),
            "planning": {"relationshipToPrevious": "independent", "cameraIntent": "Wide master"},
        })
        assert created.status_code == 201, created.text
        project_id = created.json()["id"]
        workspace = created.json()["workspace"]
        saved = client.put(f"/api/projects/{project_id}", json={
            "workspace": workspace, "planning": {"cameraIntent": "Closer master"},
        })
        assert saved.status_code == 200, saved.text
        assert saved.json()["planning"] == {
            "relationshipToPrevious": "independent", "cameraIntent": "Closer master",
            "scenePlan": "",
            "shotCount": 1,
            "visualHandoff": {"strategy": "auto", "tailSeconds": 1.5, "immediateCutMilliseconds": 100},
        }


def test_frozen_v3_compatibility_is_strict_and_non_mutating(tmp_path: Path, workspace_factory):
    store = ProjectStore(tmp_path)
    created = store.create_project(workspace_factory(), sequence_context="Shared", incoming_state="Start")
    path = tmp_path / f"project-{created.project_id}" / "project.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.pop("storyIdea", None)
    raw["schemaVersion"] = 3
    raw["sequence"].pop("storyMemory", None)
    for legacy_generation in raw["sequence"]["generations"]:
        legacy_generation.pop("storyMemoryAfter", None)
        legacy_generation.pop("storyMemoryStatus", None)
    raw["sequence"].pop("subjects", None)
    raw["sequence"].pop("nextSubjectNumber", None)
    raw["sequence"]["generations"][0].pop("planning", None)
    path.write_text(json.dumps(raw), encoding="utf-8")
    before = path.read_bytes()

    loaded = store.load_project(created.project_id)
    assert loaded.planning.relationship_to_previous is None
    assert loaded.planning.camera_intent == ""
    assert loaded.sequence_context == "Shared"
    assert path.read_bytes() == before
    assert store.list_projects()[0].project_id == created.project_id
    assert path.read_bytes() == before

    store.save_project(created.project_id, loaded.workspace)
    assert json.loads(path.read_text(encoding="utf-8"))["schemaVersion"] == 17

    raw["sequence"]["generations"][0]["planning"] = {"cameraIntent": "unexpected"}
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(MalformedProjectError):
        store.load_project(created.project_id)


@pytest.mark.asyncio
async def test_inference_reads_committed_predecessor_without_activation(tmp_path: Path, workspace_factory):
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory(creativeRequest="Previous action."))
    first_id = project.active_generation_id
    project = store.create_generation(project.project_id)
    second_id = project.active_generation_id
    fake = FakeClient(_plan())
    request = PlanningInferenceRequest(
        workspace=_blank_workspace(workspace_factory),
        llm=_settings(),
        continuity={"sequenceContext": "Alice stays in the station."},
        planning=GenerationPlanning(),
    )

    previous = store.read_predecessor_generation(project.project_id, second_id)
    result = await infer_plan(previous, request, lambda _settings: fake)

    assert result.relationship_to_previous.value == "new_shot_preserve_continuity"
    assert store.load_project(project.project_id).active_generation_id == second_id
    assert first_id == previous.generation_id
    text = fake.messages[1]["content"]
    assert "Alice stays in the station." in str(text)
    assert "Previous action." in str(text)
    assert "Generation 1" in str(text)
    system = fake.messages[0]["content"]
    assert "Non-empty current user-authored fields are authoritative" in system
    assert "Return exactly one JSON object" in system
    assert "Output the finished H3 prompt directly." not in system
    assert fake.response_instruction is None
    assert "new_shot_preserve_continuity" in system
    assert "previous final frame becomes the new first frame" in system


@pytest.mark.asyncio
async def test_inference_treats_dialogue_as_read_only_even_when_model_returns_another_value(workspace_factory):
    current_dialogue = "Alice: Wait.\nBob: No."
    previous = type("Previous", (), {
        "ordinal": 1, "workspace": workspace_factory(creativeRequest="Previous action."),
        "incoming_state": "", "intended_outgoing_state": "", "planning": GenerationPlanning(),
    })()
    request = PlanningInferenceRequest(
        workspace=workspace_factory(dialogue=current_dialogue), llm=_settings(), planning=GenerationPlanning(),
    )
    result = await infer_plan(previous, request, lambda _settings: FakeClient(_plan(dialogue="Model must not adopt.")))
    assert result.dialogue == current_dialogue
    assert request.workspace.dialogue == current_dialogue


@pytest.mark.asyncio
async def test_inference_sends_picture_vision_and_media_metadata(workspace_factory, image_asset_factory):
    image = image_asset_factory()
    # Use metadata-only media entries to make the no-analysis contract visible.
    video = ReferenceAsset.model_validate({
        "id": "video-1", "kind": "video", "filename": "arrival.mp4", "mimeType": "video/mp4",
        "role": "continuation_source", "notes": "Ending action reference", "order": 1,
    })
    audio = ReferenceAsset.model_validate({
        "id": "audio-1", "kind": "audio", "filename": "voice.wav", "mimeType": "audio/wav",
        "role": "voice_timbre", "notes": "Reuse the voice quality", "order": 2,
    })
    previous = type("Previous", (), {
        "ordinal": 1, "workspace": workspace_factory(mode="Ref2VA", assets=[image, video, audio]),
        "incoming_state": "", "intended_outgoing_state": "", "planning": GenerationPlanning(),
    })()
    request = PlanningInferenceRequest(
        workspace=workspace_factory(mode="Ref2VA", assets=[image]), llm=_settings(True), planning=GenerationPlanning()
    )
    assembly = assemble_inference_prompt(previous, request, supports_vision=True)
    content = assembly.messages[1]["content"]
    assert "Pictures with actual vision input are different" in assembly.messages[0]["content"]
    assert any(part.get("type") == "image_url" for part in content)
    text = " ".join(part.get("text", "") for part in content if part.get("type") == "text")
    assert "arrival.mp4" in text and "continuation_source" in text
    assert "voice.wav" in text and "voice_timbre" in text
    assert "no audio bytes" in text
    assert "contentAnalysis" in text


@pytest.mark.asyncio
async def test_malformed_or_invented_planner_output_is_safe(workspace_factory):
    previous = type("Previous", (), {
        "ordinal": 1, "workspace": workspace_factory(), "incoming_state": "", "intended_outgoing_state": "",
        "planning": GenerationPlanning(),
    })()
    request = PlanningInferenceRequest(
        workspace=_blank_workspace(workspace_factory),
        llm=_settings(),
    )
    malformed = FakeClient("not json")
    with pytest.raises(Exception, match="malformed structured JSON"):
        await infer_plan(previous, request, lambda _settings: malformed)

    fake = FakeClient(_plan(referenceRecommendations=[
        {"source": "previous_generation", "assetId": "not-real", "recommendation": "Invented"},
    ]))
    result = await infer_plan(previous, request, lambda _settings: fake)
    assert result.reference_recommendations == []


@pytest.mark.asyncio
async def test_recommendation_source_disambiguates_same_ids_and_media_generations(workspace_factory):
    from miniconstruct.models.workspace import ReferenceAsset
    previous_asset = ReferenceAsset.model_validate({
        "id": "shared", "kind": "audio", "filename": "previous.wav", "mimeType": "audio/wav",
        "role": "voice_timbre",
    })
    current_asset = ReferenceAsset.model_validate({
        "id": "shared", "kind": "video", "filename": "current.mp4", "mimeType": "video/mp4",
        "role": "camera_movement",
    })
    previous = type("Previous", (), {
        "ordinal": 1, "workspace": workspace_factory(mode="Ref2VA", assets=[previous_asset]),
        "incoming_state": "", "intended_outgoing_state": "", "planning": GenerationPlanning(),
    })()
    request = PlanningInferenceRequest(
        workspace=workspace_factory(mode="Ref2VA", assets=[current_asset]), llm=_settings(), planning=GenerationPlanning()
    )
    fake = FakeClient(_plan(referenceRecommendations=[
        {"source": "previous_generation", "assetId": "shared", "recommendation": "Reuse voice."},
        {"source": "current_generation", "assetId": "shared", "recommendation": "Use movement."},
        {"source": "previous_generation", "assetId": "missing", "recommendation": "Reject."},
    ]))
    result = await infer_plan(previous, request, lambda _settings: fake)
    assert [(item.source.value, item.asset_id) for item in result.reference_recommendations] == [
        ("previous_generation", "shared"), ("current_generation", "shared"),
    ]


@pytest.mark.asyncio
async def test_revision_requires_unselected_values_to_remain_exact(workspace_factory):
    request = PlanningRevisionRequest(
        workspace=workspace_factory(creativeRequest="Keep this action."),
        llm=_settings(),
        continuity={"incomingState": "Exact incoming", "intendedOutgoingState": "Exact outgoing"},
        planning={"cameraIntent": "Original camera"},
        instruction="Change only the camera.",
        scopes=[PlanningRevisionScope.CAMERA_INTENT],
    )
    fake = FakeClient(_plan(
        creativeRequest="Keep this action.", incomingState="Exact incoming", intendedOutgoingState="Exact outgoing",
        relationshipToPrevious=None, cameraIntent="New camera",
    ))
    result = await revise_plan(request, lambda _settings: fake)
    assert result.camera_intent == "New camera"
    assert "fields in editableScopes" in fake.messages[0]["content"]
    assert "outside editableScopes" in fake.messages[0]["content"]
    assert "match_previous_ending_frame" in fake.messages[0]["content"]
    assert "Return exactly one JSON object" in fake.messages[0]["content"]
    assert "Output the finished H3 prompt directly." not in fake.messages[0]["content"]
    assert fake.response_instruction is None

    bad = FakeClient(_plan(incomingState="Changed behind the scenes"))
    with pytest.raises(Exception, match="unselected field"):
        await revise_plan(request, lambda _settings: bad)


@pytest.mark.asyncio
async def test_dialogue_revision_is_exact_scoped_and_uses_local_narrative_names(workspace_factory):
    workspace = workspace_factory(
        creativeRequest="Alice waits at the station.", dialogue="Alice: Wait.\nBob: No.",
        subjects=[{"id": "alice", "number": 1}],
    )
    request = PlanningRevisionRequest(
        workspace=workspace, llm=_settings(),
        continuity={"incomingState": "Alice is waiting.", "intendedOutgoingState": "The train arrives."},
        planning={"relationshipToPrevious": "new_shot_preserve_continuity", "cameraIntent": "Wide shot."},
        instruction="Make Alice more urgent.", scopes=[PlanningRevisionScope.DIALOGUE],
    )
    request._narrative_subjects = [SequenceSubject(id="alice", number=1, narrativeName="Alice", narrativeAliases=["Al"])]
    fake = FakeClient(_revision_plan(request, dialogue="Alice: Please hurry!\nBob: I'm coming."))

    result = await revise_plan(request, lambda _settings: fake)

    assert result.dialogue == "Alice: Please hurry!\nBob: I'm coming."
    assert result.creative_request == workspace.creative_request
    assert result.incoming_state == "Alice is waiting."
    assert result.intended_outgoing_state == "The train arrives."
    assert result.relationship_to_previous.value == "new_shot_preserve_continuity"
    assert result.camera_intent == "Wide shot."
    context = str(fake.messages[1]["content"])
    assert '"dialogue": "Alice: Wait.\\nBob: No."' in context
    assert '"narrativeName": "Alice"' in context
    assert '"narrativeAliases"' not in context and '"Al"' not in context
    assert "empty string means deliberate silence" in fake.messages[0]["content"]


@pytest.mark.asyncio
async def test_dialogue_revision_accepts_explicit_blank_but_rejects_omission_or_unselected_mutation(workspace_factory):
    request = PlanningRevisionRequest(
        workspace=workspace_factory(dialogue="Alice: We have to leave."), llm=_settings(),
        instruction="Make this shot silent.", scopes=[PlanningRevisionScope.DIALOGUE],
    )
    blank = await revise_plan(request, lambda _settings: FakeClient(_revision_plan(request, dialogue="")))
    assert blank.dialogue == ""

    with pytest.raises(Exception, match="omitted selected field 'dialogue'"):
        await revise_plan(request, lambda _settings: FakeClient(_revision_plan(request)))

    camera_request = request.model_copy(update={"scopes": [PlanningRevisionScope.CAMERA_INTENT]})
    with pytest.raises(Exception, match="unselected field 'dialogue'"):
        await revise_plan(camera_request, lambda _settings: FakeClient(
            _revision_plan(camera_request, dialogue="Alice: This must not be applied.")
        ))


@pytest.mark.asyncio
async def test_dialogue_can_be_revised_with_other_scopes_and_respects_workspace_limit(workspace_factory):
    request = PlanningRevisionRequest(
        workspace=workspace_factory(creativeRequest="Original action.", dialogue="Alice: Original."), llm=_settings(),
        continuity={"incomingState": "Original incoming", "intendedOutgoingState": "Original outgoing"},
        planning={"cameraIntent": "Original camera"}, instruction="Revise dialogue, prompt, and incoming state.",
        scopes=[PlanningRevisionScope.DIALOGUE, PlanningRevisionScope.MAIN_PROMPT, PlanningRevisionScope.INCOMING_STATE],
    )
    result = await revise_plan(request, lambda _settings: FakeClient(_revision_plan(
        request, dialogue="Alice: Revised.", creativeRequest="Revised action.", incomingState="Revised incoming",
    )))
    assert (result.dialogue, result.creative_request, result.incoming_state) == (
        "Alice: Revised.", "Revised action.", "Revised incoming",
    )
    assert result.intended_outgoing_state == "Original outgoing" and result.camera_intent == "Original camera"

    with pytest.raises(ValidationError):
        PlanningRevisionRequest(workspace=workspace_factory(dialogue="x" * 30001), llm=_settings(),
                                instruction="Too long.", scopes=[PlanningRevisionScope.DIALOGUE])
    with pytest.raises(Exception, match="malformed structured JSON"):
        await revise_plan(request, lambda _settings: FakeClient(_revision_plan(request, dialogue="x" * 30001)))


@pytest.mark.asyncio
async def test_story_apply_and_manual_dialogue_revision_share_workspace_persistence_h3_and_provenance(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path / "projects")
    created = store.create_project(workspace_factory(creativeRequest="Alice enters the station."))
    draft = initial_story_draft(store.read_story_snapshot(created.project_id), 2)
    draft.items[0].dialogue = "Alice: Where is the train?"
    store.apply_story_plan(created.project_id, draft)
    applied = store.load_project(created.project_id)
    assert applied.workspace.dialogue == "Alice: Where is the train?"
    before_fingerprint = execution_authoring_fingerprint(applied.workspace)

    request = PlanningRevisionRequest(
        workspace=applied.workspace, llm=_settings(),
        continuity={"sequenceContext": applied.sequence_context, "incomingState": applied.incoming_state,
                    "intendedOutgoingState": applied.intended_outgoing_state}, planning=applied.planning,
        instruction="Make the line more urgent.", scopes=[PlanningRevisionScope.DIALOGUE],
    )
    revised = await revise_plan(request, lambda _settings: FakeClient(
        _revision_plan(request, dialogue="Alice: Where is the train?!")
    ))
    store.save_project(created.project_id, applied.workspace.model_copy(update={"dialogue": revised.dialogue}))
    reloaded = store.load_project(created.project_id)

    assert reloaded.workspace.dialogue == "Alice: Where is the train?!"
    assert execution_authoring_fingerprint(reloaded.workspace) != before_fingerprint
    assembled = assemble_prompt(reloaded.workspace, False, store.generation_narrative_subjects(
        created.project_id, reloaded.workspace,
    ))
    assert "Alice: Where is the train?!" in assembled.inspector_text

    manual = reloaded.workspace.model_copy(update={"dialogue": "Alice: A manual line."})
    manual_request = request.model_copy(update={"workspace": manual, "instruction": "Make it silent."})
    silenced = await revise_plan(manual_request, lambda _settings: FakeClient(_revision_plan(manual_request, dialogue="")))
    store.save_project(created.project_id, manual.model_copy(update={"dialogue": silenced.dialogue}))
    assert store.load_project(created.project_id).workspace.dialogue == ""
