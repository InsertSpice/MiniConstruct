"""Provider-free contracts for the two H3 authoring paths."""
import json

import pytest

from miniconstruct.execution_authoring import execution_authoring_fingerprint
from miniconstruct.execution_state import HistoryAdoptionWrite, ManualEditAdoptionWrite, SelectionWrite
from miniconstruct.generation import resolve_narrative_request
from miniconstruct.h3.builder import assemble_prompt
from miniconstruct.models.api import GenerationRequest, LLMSettings
from miniconstruct.models.story_operations import StoryInitialRangeRequest, StoryReplanRequest
from miniconstruct.project_store import ProjectStore
from miniconstruct.story_operations import build_initial_range, plan_replan
from test_validator import VALID_BASE
from test_story_replan import OfflineReplanClient, prompt_context


LLM = LLMSettings.model_validate({"modelId": "offline", "endpoint": {
    "id": "offline", "displayName": "Offline", "baseUrl": "http://never.test/v1"}})


class StoryWriter:
    def __init__(self, g1_dialogue=""):
        self.calls = []
        self.g1_dialogue = g1_dialogue

    def __call__(self, _settings):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def generate(self, messages, *, response_instruction=None):
        self.calls.append(messages)
        context = json.loads(messages[1]["content"])
        items = []
        for index in range(context["requestedTotalCount"]):
            item = {
                "scenePlan": f"Scene {index + 1}: Nino and Makoto search, argue, then find a clue.",
                "dialogue": self.g1_dialogue if index == 0 else ("Makoto: Look behind the drawer." if index == 1 else ""),
                "incomingState": "At the desk.", "intendedOutgoingState": "A clue is found.",
                "relationshipToPrevious": None if index == 0 else "new_shot_preserve_continuity",
                "cameraIntent": "Follow the search.", "memoryPatchAfter": {},
                "shotCount": 1,
            }
            if index < 4:
                item["durationSeconds"] = [15, 14, 13, 99][index]
            items.append(item)
        return json.dumps({"sequenceContext": "The search continues.", "items": items})


@pytest.mark.asyncio
async def test_initial_range_scene_plans_duration_and_main_prompt_separation(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory(creativeRequest="G1 one-shot idea", durationSeconds=15,
        dialogue="Nino: Keep searching."),
        story_idea="Two friends investigate the station.")
    writer = StoryWriter()
    draft = await build_initial_range(store, project.project_id,
        StoryInitialRangeRequest(requestedTotalCount=5, llm=LLM), writer)
    assert len(writer.calls) == 1
    assert [item.scene_plan for item in draft.items] == [
        f"Scene {index + 1}: Nino and Makoto search, argue, then find a clue." for index in range(5)]
    store.apply_story_initial_range(project.project_id, draft)
    saved = store.read_story_snapshot(project.project_id)
    assert [g.workspace.duration_seconds for g in saved.generations] == [15, 14, 13, 15, 15]
    assert saved.generations[0].workspace.creative_request == "G1 one-shot idea"
    assert saved.generations[0].workspace.dialogue == "Nino: Keep searching."
    assert all(not g.workspace.creative_request for g in saved.generations[1:])
    assert saved.generations[1].workspace.dialogue == "Makoto: Look behind the drawer."

    g1 = saved.generations[0]
    body = {"workspace": g1.workspace.model_dump(mode="json", by_alias=True),
            "filesystemProjectId": project.project_id, "generationId": g1.generation_id,
            "llm": LLM.model_dump(mode="json", by_alias=True)}
    one_shot = resolve_narrative_request(GenerationRequest.model_validate({**body, "authoringPath": "one_shot"}), store)
    story = resolve_narrative_request(GenerationRequest.model_validate({**body, "authoringPath": "story"}), store)
    one_shot_text = assemble_prompt(one_shot.prompt_workspace, False, one_shot.narrative_subjects,
                                    one_shot.continuity_context, one_shot.story_context).inspector_text
    story_text = assemble_prompt(story.prompt_workspace, False, story.narrative_subjects,
                                 story.continuity_context, story.story_context).inspector_text
    assert "G1 one-shot idea" in one_shot_text
    assert "Scene 1:" not in one_shot_text and "Two friends investigate" not in one_shot_text
    assert "Scene 1:" in story_text and "Scene 2:" in story_text
    assert "Two friends investigate" in story_text and "G1 one-shot idea" not in story_text

    g2 = saved.generations[1]
    story_g2 = resolve_narrative_request(GenerationRequest.model_validate({
        "workspace": g2.workspace.model_dump(mode="json", by_alias=True),
        "filesystemProjectId": project.project_id, "generationId": g2.generation_id,
        "authoringPath": "story", "llm": LLM.model_dump(mode="json", by_alias=True),
    }), store)
    assert story_g2.workspace.creative_request == ""
    assert story_g2.story_context["currentGeneration"]["scenePlan"].startswith("Scene 2:")

    changed_main = g1.workspace.model_copy(update={"creative_request": "Different one-shot idea"})
    assert execution_authoring_fingerprint(changed_main, story.narrative_subjects, story.continuity_context,
        authoring_path="story", story_context=story.story_context) == execution_authoring_fingerprint(
        g1.workspace, story.narrative_subjects, story.continuity_context,
        authoring_path="story", story_context=story.story_context)
    assert execution_authoring_fingerprint(changed_main) != execution_authoring_fingerprint(g1.workspace)


@pytest.mark.asyncio
async def test_blank_existing_g1_dialogue_can_receive_story_dialogue(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory(creativeRequest="Existing one-shot idea", dialogue=""),
                                   story_idea="Two friends search a station.")
    draft = await build_initial_range(store, project.project_id,
        StoryInitialRangeRequest(requestedTotalCount=2, llm=LLM),
        StoryWriter(g1_dialogue="Nino: Try the desk."))
    store.apply_story_initial_range(project.project_id, draft)
    saved = store.read_story_snapshot(project.project_id).generations
    assert saved[0].workspace.dialogue == "Nino: Try the desk."
    assert saved[0].workspace.creative_request == "Existing one-shot idea"


def test_story_selection_history_and_fingerprints_use_saved_authoring_path(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory(creativeRequest="One-shot idea"),
                                   story_idea="Friends investigate a station.")
    generation_id = project.active_generation_id
    store.save_project(project.project_id, project.workspace,
                       planning={"scenePlan": "They search the desk and find a photograph."})
    initial = store.execution_snapshot(project.project_id, generation_id)[1].current_fingerprints
    assert set(initial) == {"one_shot", "story"}

    store.select_execution_prompt(project.project_id, generation_id,
        SelectionWrite(prompt=VALID_BASE, sourceFingerprint=initial["story"], authoringPath="story"))
    selected = store.execution_snapshot(project.project_id, generation_id)[1]
    assert selected.selected.authoring_path == "story" and not selected.stale

    current = store.load_project(project.project_id)
    store.save_project(project.project_id,
        current.workspace.model_copy(update={"creative_request": "Changed one-shot idea"}))
    after_main = store.execution_snapshot(project.project_id, generation_id)[1]
    assert after_main.current_fingerprints["story"] == initial["story"]
    assert after_main.current_fingerprints["one_shot"] != initial["one_shot"]
    assert not after_main.stale

    current = store.load_project(project.project_id)
    store.save_project(project.project_id, current.workspace,
                       story_idea="Friends investigate the old station together.")
    after_premise = store.execution_snapshot(project.project_id, generation_id)[1]
    assert after_premise.current_fingerprints["story"] != after_main.current_fingerprints["story"]
    assert after_premise.current_fingerprints["one_shot"] == after_main.current_fingerprints["one_shot"]
    assert after_premise.stale

    store.adopt_manual_edit_execution_prompt(project.project_id, generation_id,
        ManualEditAdoptionWrite(prompt=VALID_BASE, authoringPath="story"))
    manual = store.execution_snapshot(project.project_id, generation_id)[1]
    assert manual.selected.authoring_path == "story" and manual.selected.source_kind == "manual_edit"
    assert not manual.stale
    store.clear_execution_prompt(project.project_id, generation_id)
    cleared = store.execution_snapshot(project.project_id, generation_id)[1]
    assert cleared.selected is None and cleared.current_fingerprints == manual.current_fingerprints
    store.adopt_history_execution_prompt(project.project_id, generation_id,
        HistoryAdoptionWrite(prompt=VALID_BASE, authoringPath="story"))
    adopted = store.execution_snapshot(project.project_id, generation_id)[1]
    assert adopted.selected.authoring_path == "story" and adopted.selected.source_kind == "adopted_history"

    current = store.load_project(project.project_id)
    store.save_project(project.project_id, current.workspace,
                       planning={"scenePlan": "They uncover a photograph behind the drawer."})
    after_scene = store.execution_snapshot(project.project_id, generation_id)[1]
    assert after_scene.current_fingerprints["story"] != adopted.current_fingerprints["story"]
    assert after_scene.current_fingerprints["one_shot"] == adopted.current_fingerprints["one_shot"]
    assert after_scene.stale


def test_story_h3_profile_relevance_avoids_common_word_aliases(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory(subjects=[{"id": "alice", "number": 1}],
        nextSubjectNumber=2), story_idea="Maybe they will mark hope with grace.")
    store.save_project(project.project_id, project.workspace, character_profiles=[{
        "subjectId": "alice", "characterProfile": {"personality": "Careful and observant."},
    }])
    for name in ("May", "Will", "Mark", "Hope", "Rose", "Grace", "Alice"):
        store.set_subject_narrative_name(project.project_id, "alice", name)
    second = store.create_generation(project.project_id)
    store.save_project(project.project_id, second.workspace,
        planning={"scenePlan": "Maybe they will mark the rose with grace and leave."})
    context = store.story_h3_context(project.project_id, second.active_generation_id)
    assert context["characterProfiles"] == []

    current = store.load_project(project.project_id)
    store.save_project(project.project_id, current.workspace,
        planning={"scenePlan": "Alice studies the photograph before they leave."})
    context = store.story_h3_context(project.project_id, second.active_generation_id)
    assert len(context["characterProfiles"]) == 1
    assert context["characterProfiles"][0]["narrativeName"] == "Alice"
    assert "narrativeAliases" not in context["characterProfiles"][0]

    before = store.execution_snapshot(project.project_id, second.active_generation_id)[1].current_fingerprints
    current = store.load_project(project.project_id)
    store.save_project(project.project_id, current.workspace, character_profiles=[{
        "subjectId": "alice", "characterProfile": {"personality": "Impulsive but observant."},
    }])
    after = store.execution_snapshot(project.project_id, second.active_generation_id)[1].current_fingerprints
    assert after["story"] != before["story"] and after["one_shot"] == before["one_shot"]


def test_both_h3_assemblies_keep_shared_exact_authority(tmp_path, workspace_factory, image_asset_factory):
    store = ProjectStore(tmp_path)
    workspace = workspace_factory(mode="Ref2VA", assets=[image_asset_factory()],
        creativeRequest="One-shot: Alice waits by the station door.",
        dialogue="Alice: I found the photograph.", durationSeconds=15,
        subjects=[{"id": "alice", "number": 1}], nextSubjectNumber=2)
    project = store.create_project(workspace, story_idea="Alice investigates the closed station.")
    store.set_subject_narrative_name(project.project_id, "alice", "Alice")
    store.save_project(project.project_id, project.workspace,
        planning={"scenePlan": "Alice searches the desk, finds a photograph, then calls to her friend.",
                  "cameraIntent": "Follow Alice toward the desk."},
        incoming_state="The desk is undisturbed.", intended_outgoing_state="The photograph is in Alice's hand.",
        character_profiles=[{"subjectId": "alice", "characterProfile": {"personality": "Patient and observant."}}])
    generation_id = project.active_generation_id
    body = {"workspace": workspace.model_dump(mode="json", by_alias=True),
            "filesystemProjectId": project.project_id, "generationId": generation_id,
            "llm": LLM.model_dump(mode="json", by_alias=True)}
    texts = {}
    for path in ("one_shot", "story"):
        resolved = resolve_narrative_request(GenerationRequest.model_validate({**body, "authoringPath": path}), store)
        texts[path] = assemble_prompt(resolved.prompt_workspace, False, resolved.narrative_subjects,
            resolved.continuity_context, resolved.story_context).inspector_text
        assert texts[path].count("===== Official MiniMax H3 guide (normative) =====") == 1
        assert "Alice: I found the photograph." in texts[path]
        assert "15.00 seconds" in texts[path]
        assert "<Picture 1>" in texts[path]
    assert "One-shot: Alice waits" in texts["one_shot"]
    assert "Alice investigates the closed station" not in texts["one_shot"]
    assert "Alice searches the desk" not in texts["one_shot"]
    assert "One-shot: Alice waits" not in texts["story"]
    assert "Alice investigates the closed station" in texts["story"]
    assert "Alice searches the desk" in texts["story"]
    assert "CURRENT GENERATION SCENE PLAN — PRIMARY DIRECTING TASK:\nAlice searches the desk, finds a photograph, then calls to her friend." in texts["story"]
    assert "CANONICAL STORY H3 AUTHORITY" in texts["story"]
    assert "Translate the current Scene Plan into concrete temporal performance" in texts["story"]
    assert "Sustained stillness, silence, a held gaze" in texts["story"]
    assert "Patient and observant" in texts["story"]
    assert "The desk is undisturbed" in texts["story"]
    assert "The photograph is in Alice's hand" in texts["story"]


@pytest.mark.asyncio
async def test_applied_g3_scene_edit_is_explicit_upstream_replan_authority(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory(creativeRequest="G1 one-shot idea", durationSeconds=15),
                                   story_idea="Friends search the station for a missing photograph.")
    draft = await build_initial_range(store, project.project_id,
        StoryInitialRangeRequest(requestedTotalCount=6, llm=LLM), StoryWriter())
    store.apply_story_initial_range(project.project_id, draft)
    saved = store.read_story_snapshot(project.project_id)
    ids = [generation.generation_id for generation in saved.generations]
    original_later = [generation.planning.scene_plan for generation in saved.generations[3:]]
    original_durations = [generation.workspace.duration_seconds for generation in saved.generations]
    store.activate_generation(project.project_id, ids[2])
    g3 = store.load_project(project.project_id)
    store.save_project(project.project_id, g3.workspace,
                       planning={"scenePlan": "User-edited G3: they find a photo behind the drawer."})
    edited = store.read_story_snapshot(project.project_id)
    assert [generation.planning.scene_plan for generation in edited.generations[3:]] == original_later

    writer = OfflineReplanClient()
    replan = await plan_replan(store, project.project_id,
        StoryReplanRequest(startGenerationId=ids[3], endGenerationId=ids[4],
                           instruction="Carry the photograph discovery forward.", llm=LLM), writer)
    context = prompt_context(writer)
    assert "User-edited G3" in json.dumps(context)
    assert '"creativeRequest"' not in json.dumps(context)
    store.apply_story_plan_replan(project.project_id, replan)
    after = store.read_story_snapshot(project.project_id).generations
    assert after[2].planning.scene_plan == "User-edited G3: they find a photo behind the drawer."
    assert after[5].planning.scene_plan == original_later[2]
    assert [generation.workspace.duration_seconds for generation in after] == original_durations
    assert after[0].workspace.creative_request == "G1 one-shot idea"
    assert all(not generation.workspace.creative_request for generation in after[1:])
