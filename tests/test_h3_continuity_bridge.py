"""Provider-free saved authority, production assembly, carry and provenance tests."""
import json
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from miniconstruct.generation import resolve_narrative_request, generate_workspace_prompts
from miniconstruct.h3.builder import assemble_prompt
from miniconstruct.llm.compatibility import build_generation_payload
from miniconstruct.models.api import GenerationRequest
from miniconstruct.models.planning import RelationshipToPrevious
from miniconstruct.models.story_memory import StoryContinuityMemory
from miniconstruct.execution_state import SelectionWrite
from miniconstruct.project_store import ProjectStore
from miniconstruct.story_planning import initial_story_draft
from test_validator import VALID_BASE
from test_story_initial_range import OfflineInitialRangeClient, propose
from test_story_next import OfflineNextClient
from test_story_insert import OfflineInsertClient


@pytest.fixture(autouse=True)
def no_transport(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Provider calls forbidden")
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)


def request(store, project, generation_id=None, authoring_path="one_shot", reasoning="off"):
    return resolve_narrative_request(GenerationRequest.model_validate({
        "workspace": project.workspace.model_dump(by_alias=True),
        "filesystemProjectId": project.project_id,
        "generationId": generation_id or project.active_generation_id,
        "authoringPath": authoring_path,
        "llm": {"baseUrl": "http://127.0.0.1:8888/v1", "model": "offline", "reasoningMode": reasoning},
    }), store)


@pytest.fixture
def saved(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path)
    memory = StoryContinuityMemory(recentSummary="Alice removed the red jacket.", durableFacts=[
        {"id": str(uuid4()), "text": "The bedroom door is open."}], openThreads=[
        {"id": str(uuid4()), "text": "SECRET PLANNER THREAD"}])
    g1 = store.create_project(workspace_factory(), story_memory=memory, sequence_context="Same blue bedroom.")
    g2 = store.create_generation(g1.project_id, workspace=workspace_factory())
    g2 = store.save_project(g1.project_id, g2.workspace,
        incoming_state="Alice is no longer wearing the jacket.",
        intended_outgoing_state="Alice has closed the door.",
        planning={"relationshipToPrevious": "new_shot_preserve_continuity", "cameraIntent": "Low close bedside angle facing the door.",
                  "scenePlan": "Alice searches the bedroom, sees the open door, then closes it."})
    return store, g1, g2


@pytest.mark.asyncio
async def test_production_bridge_uses_saved_uuid_and_separate_authority(saved):
    store, g1, g2 = saved
    store.activate_generation(g1.project_id, g1.active_generation_id)
    req = request(store, g2, authoring_path="story")
    class Writer:
        def __init__(self, _): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def generate(self, messages):
            system = messages[0]["content"]
            for text in ["Same blue bedroom.", "Alice is no longer wearing the jacket.", "Alice has closed the door.",
                         "Low close bedside angle facing the door.", "The bedroom door is open.", "Same scene, new camera shot",
                         "explicit current user authoring > Incoming State > predecessor user-corrected Observed End State > predecessor machine Observed End State > predecessor Intended Outgoing State > trusted relevant Story Memory > stable Character Profile / reference defaults"]:
                assert text in system
            assert "SECRET PLANNER THREAD" not in system
            assert "Low close bedside" in messages[1]["content"]
            assert "not observed truth" in system
            return VALID_BASE
    generated = await generate_workspace_prompts(req, Writer)
    assert generated.variations[0].generated.source_fingerprint == store.execution_snapshot(g1.project_id, g2.active_generation_id)[1].current_fingerprints["story"]
    assert req.continuity_context.predecessor_generation_id == g1.active_generation_id
    assert req.workspace.creative_request == g2.workspace.creative_request
    raw = req.model_dump(by_alias=True)
    raw["continuityContext"] = {"incomingState": "Spoofed"}
    with pytest.raises(ValidationError):
        GenerationRequest.model_validate(raw)


@pytest.mark.parametrize("field,value", [
    ("sequence_context", "Different sequence"), ("incoming_state", "Jacket absent; door closed"),
    ("intended_outgoing_state", "Door locked"), ("planning", {"cameraIntent": "Overhead"}),
    ("planning", {"relationshipToPrevious": "independent"}),
])
def test_selected_prompt_stales_without_rewriting_history(saved, field, value):
    store, g1, g2 = saved
    _, status = store.execution_snapshot(g1.project_id, g2.active_generation_id)
    store.select_execution_prompt(g1.project_id, g2.active_generation_id,
                                  SelectionWrite(prompt=VALID_BASE, sourceFingerprint=status.current_fingerprint))
    old_selection = store.execution_snapshot(g1.project_id, g2.active_generation_id)[1].selected
    store.save_project(g1.project_id, g2.workspace, **{field: value})
    after = store.execution_snapshot(g1.project_id, g2.active_generation_id)[1]
    assert after.stale and after.selected == old_selection


def test_memory_fingerprint_is_bounded_and_local(saved, workspace_factory):
    store, g1, g2 = saved
    before = request(store, g2, authoring_path="story").source_fingerprint
    active = store.activate_generation(g1.project_id, g1.active_generation_id)
    memory = active.story_memory_after.model_copy(deep=True)
    memory.open_threads[0].text = "Changed planner-only thread"
    store.save_project(g1.project_id, active.workspace, story_memory=memory, story_memory_generation_id=g1.active_generation_id)
    assert request(store, g2, authoring_path="story").source_fingerprint == before
    memory.durable_facts[0].text = "The door was broken."
    store.save_project(g1.project_id, active.workspace, story_memory=memory, story_memory_generation_id=g1.active_generation_id)
    assert request(store, g2, authoring_path="story").source_fingerprint != before
    stable = request(store, g1).source_fingerprint
    store.create_generation(g1.project_id, workspace=workspace_factory())
    assert request(store, g1).source_fingerprint == stable


@pytest.mark.parametrize("status", ["needs_review", "legacy_unknown"])
def test_untrusted_memory_omitted_warned(saved, status):
    store, g1, g2 = saved
    path = store.root / f"project-{g1.project_id}" / "project.json"
    data = json.loads(path.read_text())
    data["sequence"]["generations"][0]["storyMemoryStatus"] = status
    path.write_text(json.dumps(data))
    req = request(store, g2)
    context = req.continuity_context
    assert not context.recent_summary and not context.durable_facts
    assert status in context.warnings[0]
    assembled = assemble_prompt(req.workspace, False, (), context)
    assert any(status in w for w in assembled.warnings)
    assert "Alice removed the red jacket." not in assembled.inspector_text


def test_independent_and_first_generation_do_not_inherit_local_memory(saved):
    store, g1, g2 = saved
    store.save_project(g1.project_id, g2.workspace, planning={"relationshipToPrevious": "independent"})
    context = request(store, g2).continuity_context
    assert not context.recent_summary and not context.durable_facts and context.predecessor_generation_id is None
    text = assemble_prompt(g2.workspace, False, (), context).inspector_text
    assert "No predecessor-local visual or shot continuity assumption" in text
    first = request(store, g1).continuity_context
    assert first.predecessor_generation_id is None and first.relationship_to_previous is None
    assert not first.recent_summary


def test_endpoint_warning_requires_active_frame_evidence(saved, image_asset_factory, workspace_factory):
    store, g1, g2 = saved
    store.save_project(g1.project_id, g2.workspace, planning={"relationshipToPrevious": "match_previous_ending_frame"})
    context = request(store, g2).continuity_context
    assert any("text alone" in w for w in assemble_prompt(g2.workspace, False, (), context).warnings)
    frame = image_asset_factory(role="first_frame_anchor")
    anchored = workspace_factory(mode="I2VA", assets=[frame])
    assert not any("text alone" in w for w in assemble_prompt(anchored, True, (), context).warnings)
    hidden = workspace_factory(mode="T2VA", assets=[frame])
    assert any("text alone" in w for w in assemble_prompt(hidden, True, (), context).warnings)


def test_environment_composition_and_camera_separate(saved, workspace_factory, image_asset_factory):
    store, _, g2 = saved
    context = request(store, g2).continuity_context
    workspace = workspace_factory(mode="Ref2VA", assets=[image_asset_factory(role="environment")])
    assembled = assemble_prompt(workspace, True, (), context)
    assert "Environment-only evidence" in assembled.inspector_text
    assert "does NOT define target first-frame composition" in assembled.inspector_text
    assert "Low close bedside angle" in assembled.inspector_text
    assert len(assembled.messages[1]["content"]) == 3
    assert not any("no usable visual" in w for w in assembled.warnings)


@pytest.mark.parametrize("relationship", ["continue_previous_shot", "new_shot_preserve_continuity", "match_previous_ending_frame", "independent"])
@pytest.mark.parametrize("operation", ["legacy", "initial"])
@pytest.mark.asyncio
async def test_environment_carry_shared_payload_and_role_isolation(tmp_path, workspace_factory, image_asset_factory, relationship, operation):
    store = ProjectStore(tmp_path)
    environment = image_asset_factory("room", role="environment")
    second_view = image_asset_factory("view", role="environment")
    second_view.image = second_view.image.model_copy(update={"data_url": "data:image/png;base64,AQ=="})
    composition = image_asset_factory("composition", role="storyboard_composition")
    anchor = image_asset_factory("anchor", role="first_frame_anchor")
    project = store.create_project(workspace_factory(mode="Ref2VA", assets=[environment, second_view, composition, anchor]))
    before_files = {p.relative_to(store.root): p.read_bytes() for p in store.root.rglob("*") if p.is_file() and p.name != "project.json"}
    if operation == "legacy":
        draft = initial_story_draft(store.read_story_snapshot(project.project_id), 3)
        for item in draft.items[1:]:
            item.relationship_to_previous = RelationshipToPrevious(relationship)
            item.reference_recommendations = []
        store.apply_story_plan(project.project_id, draft)
    else:
        def transform(result, context):
            for item in result["items"][1:]:
                item["relationshipToPrevious"] = relationship
                item["referenceRecommendations"] = []
        draft = await propose(store, project, 3, OfflineInitialRangeClient(transform))
        store.apply_story_initial_range(project.project_id, draft)
    snapshot = store.read_story_snapshot(project.project_id)
    for generation in snapshot.generations[1:]:
        assert [a.role for a in generation.workspace.assets] == ([] if relationship == "independent" else ["environment", "environment"])
    source_paths = {a.path for a in snapshot.manifest.sequence.generations[0].asset_files}
    assert all(a.path in source_paths for g in snapshot.manifest.sequence.generations[1:] for a in g.asset_files)
    assert before_files == {p.relative_to(store.root): p.read_bytes() for p in store.root.rglob("*") if p.is_file() and p.name != "project.json"}


def test_standalone_unchanged(workspace_factory):
    raw = GenerationRequest.model_validate({"workspace": workspace_factory(), "llm": {"baseUrl": "http://never.test", "model": "offline"}})
    resolved = resolve_narrative_request(raw)
    assert resolved.continuity_context is None
    assert raw.source_fingerprint == resolved.source_fingerprint
    assert assemble_prompt(raw.workspace, False) == assemble_prompt(resolved.workspace, False, (), resolved.continuity_context)


@pytest.mark.parametrize(("authoring_path", "reasoning"), [
    ("story", "off"), ("story", "on"), ("one_shot", "off"), ("one_shot", "on"),
])
def test_h3_provider_payload_uses_global_reasoning_independent_of_authoring_path(saved, authoring_path, reasoning):
    store, _, g2 = saved
    req = request(store, g2, authoring_path=authoring_path, reasoning=reasoning)
    assembled = assemble_prompt(req.prompt_workspace, req.llm.supports_vision,
                                req.narrative_subjects, req.continuity_context, req.story_context)
    payload, _ = build_generation_payload(req.llm, assembled.messages, stream=True)
    expected = reasoning == "on"
    assert payload["chat_template_kwargs"] == {"thinking": expected, "enable_thinking": expected}
    if reasoning == "off":
        assert "Do not use extended reasoning" in payload["messages"][0]["content"]
    else:
        assert "Do not use extended reasoning" not in payload["messages"][0]["content"]


def test_story_planner_reasoning_preference_is_not_an_h3_request_authority(saved):
    store, _, g2 = saved
    req = request(store, g2, authoring_path="story", reasoning="off")
    raw = req.model_dump(mode="json", by_alias=True)
    raw["storyPlannerReasoning"] = True
    with pytest.raises(ValidationError):
        GenerationRequest.model_validate(raw)


def test_continue_previous_shot_without_visual_evidence_keeps_textual_incoming_guidance(saved):
    store, _, g2 = saved
    store.save_project(g2.project_id, g2.workspace,
        incoming_state="Alice is standing at the bedside facing the door.",
        planning={"relationshipToPrevious": "continue_previous_shot",
                  "scenePlan": "Alice notices the door move, turns, and calls for help."})
    req = request(store, g2, authoring_path="story")
    assembled = assemble_prompt(req.prompt_workspace, req.llm.supports_vision,
                                req.narrative_subjects, req.continuity_context, req.story_context)
    instructions = "\n".join(str(message.get("content", "")) for message in assembled.messages)
    assert "continue directly from its rendered pose" in instructions
    assert "do not restage these to approximate a conflicting planned Incoming State" in instructions
    assert "Continue the existing motion naturally before progressing into the current Scene Plan" in instructions
    assert "Incoming State remains semantic/planned guidance and is the best physical opening description" in instructions
    for secret in ("native-artifact-uuid", "accepted-execution-uuid", "latent-cache-key",
                   "workflow-profile-uuid", "latent_filename"):
        assert secret not in instructions
    assert "continue_previous_shot" in instructions
    assert "Shot 1 must open from the predecessor ending shot" in instructions
    assert "any desired profile, medium, or other camera change occurs after continuation begins" in instructions
    assert "Do not begin with an unmotivated establishing shot" in instructions


@pytest.mark.parametrize("operation", ["next", "insert"])
@pytest.mark.asyncio
async def test_scoped_apply_carries_only_predecessor_and_deduplicates(tmp_path, workspace_factory, image_asset_factory, operation):
    from miniconstruct.models.story_operations import StoryNextRequest, StoryInsertRequest
    from miniconstruct.story_operations import plan_next, plan_insert
    from test_story_initial_range import LLM
    store = ProjectStore(tmp_path)
    env = image_asset_factory("room", role="environment")
    duplicate = env.model_copy(update={"id": "duplicate-view"}, deep=True)
    project = store.create_project(workspace_factory(mode="Ref2VA", assets=[env, duplicate]))
    anchor = project.active_generation_id
    if operation == "next":
        proposal = await plan_next(store, project.project_id, StoryNextRequest(anchorGenerationId=anchor, requestedCount=2, llm=LLM), OfflineNextClient())
        store.apply_story_plan_next(project.project_id, proposal)
    else:
        future = image_asset_factory("future-room", role="environment")
        future.image = future.image.model_copy(update={"data_url": "data:image/png;base64,Ag=="})
        store.create_generation(project.project_id, workspace=workspace_factory(mode="Ref2VA", assets=[future]))
        proposal = await plan_insert(store, project.project_id, StoryInsertRequest(upstreamGenerationId=anchor, requestedCount=2, llm=LLM), OfflineInsertClient())
        store.apply_story_plan_insert(project.project_id, proposal)
    snapshot = store.read_story_snapshot(project.project_id)
    for generation in snapshot.generations[1:3]:
        assert len(generation.workspace.assets) == 1
        assert generation.workspace.assets[0].image == env.image


def test_bounded_projection_excludes_current_future_and_unselected_facts(saved):
    store, g1, g2 = saved
    active = store.activate_generation(g1.project_id, g1.active_generation_id)
    memory = StoryContinuityMemory(recentSummary="s" * 6000, durableFacts=[
        {"id": str(uuid4()), "text": f"Background fact {i}"} for i in range(64)])
    store.save_project(g1.project_id, active.workspace, story_memory=memory, story_memory_generation_id=g1.active_generation_id)
    req = request(store, g2, authoring_path="story")
    assert len(req.continuity_context.recent_summary) == 1500
    assert len(req.continuity_context.durable_facts) == 8
    memory.durable_facts[-1].text = "Unprojected fact changed"
    store.save_project(g1.project_id, active.workspace, story_memory=memory, story_memory_generation_id=g1.active_generation_id)
    assert request(store, g2, authoring_path="story").source_fingerprint == req.source_fingerprint
    active = store.activate_generation(g1.project_id, g2.active_generation_id)
    store.save_project(g1.project_id, active.workspace, story_memory=StoryContinuityMemory(recentSummary="Future outcome"), story_memory_generation_id=g2.active_generation_id)
    assert request(store, g2, authoring_path="story").source_fingerprint == req.source_fingerprint


@pytest.mark.asyncio
async def test_streaming_and_assemble_use_same_continuity(saved, monkeypatch):
    from miniconstruct.api import routes
    from miniconstruct.llm.client import LLMStreamEvent
    from test_revision import decode_event
    store, _, g2 = saved
    req = request(store, g2)
    class Writer:
        def __init__(self, _): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def stream_events(self, messages):
            assert g2.incoming_state in messages[0]["content"]
            yield LLMStreamEvent("content", VALID_BASE)
    assembled = await routes.assemble(req)
    assert g2.incoming_state in assembled["instructions"]
    events = [decode_event(event) async for event in routes.stream_generation_events(req, Writer)]
    complete = next(data for event, data in events if event == "complete")
    assert complete["sourceFingerprint"] == req.source_fingerprint


def test_benchmark_fixture_validation_only():
    from pathlib import Path
    from miniconstruct.benchmark.runner import load_case, load_suite, resolve_h3_fixture
    from test_story_initial_range import LLM
    root = Path(__file__).resolve().parents[1] / "benchmarks"
    suite = load_suite(root, "h3-continuity-bridge")
    assert len(suite.cases) == 2
    for case_id in suite.cases:
        case, workspace, _, _ = load_case(root, case_id)
        req = resolve_h3_fixture(GenerationRequest(workspace=workspace, llm=LLM), case.h3_project)
        text = assemble_prompt(req.workspace, True, req.narrative_subjects, req.continuity_context).inspector_text
        assert all(value in text for value in case.h3_project.input_contains)
        assert all(value not in text for value in case.h3_project.input_absent)
        assert case.human_review


@pytest.mark.parametrize("relationship", ["independent", "new_shot_preserve_continuity"])
@pytest.mark.asyncio
async def test_explicit_environment_recommendation_is_kept_once(tmp_path, workspace_factory, image_asset_factory, relationship):
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory(mode="Ref2VA", assets=[image_asset_factory(role="environment")]))
    def transform(result, _context):
        result["items"][1]["relationshipToPrevious"] = relationship
        assert result["items"][1]["referenceRecommendations"]
    draft = await propose(store, project, 2, OfflineInitialRangeClient(transform))
    store.apply_story_initial_range(project.project_id, draft)
    target = store.read_story_snapshot(project.project_id).generations[1]
    assert len(target.workspace.assets) == 1
    assert target.workspace.assets[0].role == "environment"


def test_carried_reference_set_invalidates_selected_prompt(tmp_path, workspace_factory, image_asset_factory):
    from test_validator import valid_ref_prompt
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory(mode="Ref2VA", assets=[image_asset_factory(), image_asset_factory("room", role="environment")]))
    draft = initial_story_draft(store.read_story_snapshot(project.project_id), 2)
    draft.items[1].relationship_to_previous = RelationshipToPrevious.NEW_SHOT_PRESERVE_CONTINUITY
    store.apply_story_plan(project.project_id, draft)
    target_id = store.load_project(project.project_id).sequence.generations[1].generation_id
    target = store.activate_generation(project.project_id, target_id)
    # Add normal identity authoring so the selected full-reference fixture is valid.
    workspace = target.workspace.model_copy(deep=True)
    workspace.assets.insert(0, project.workspace.assets[0].model_copy(update={"order": 0}))
    workspace.assets[-1].order = 1
    workspace.subjects = project.workspace.subjects
    workspace.next_subject_number = project.workspace.next_subject_number
    workspace.creative_request = "Subject 1 walks in the room."
    store.save_project(project.project_id, workspace)
    status = store.execution_snapshot(project.project_id, target_id)[1]
    store.select_execution_prompt(project.project_id, target_id, SelectionWrite(prompt=valid_ref_prompt(), sourceFingerprint=status.current_fingerprint))
    selected = store.execution_snapshot(project.project_id, target_id)[1].selected
    workspace.assets = [a for a in workspace.assets if a.role != "environment"]
    store.save_project(project.project_id, workspace)
    status = store.execution_snapshot(project.project_id, target_id)[1]
    assert status.selected is None
    assert status.current_fingerprint != selected.source_fingerprint


def test_resolution_is_read_only_and_legacy_request_uses_saved_active_uuid(saved):
    from miniconstruct.project_store import GenerationNotFoundError
    store, g1, g2 = saved
    path = store.root / f"project-{g1.project_id}" / "project.json"
    before = path.read_bytes()
    raw = GenerationRequest.model_validate({"workspace": g2.workspace, "filesystemProjectId": g1.project_id,
                                          "llm": {"baseUrl": "http://never.test", "model": "offline"}})
    resolved = resolve_narrative_request(raw, store)
    assert resolved.continuity_context.incoming_state == g2.incoming_state
    assert path.read_bytes() == before
    raw.generation_id = str(uuid4())
    with pytest.raises(GenerationNotFoundError):
        resolve_narrative_request(raw, store)
