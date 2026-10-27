"""Story planning cannot write the ordinary Workspace H3 control enums."""
import json

import pytest

from miniconstruct.generation import resolve_narrative_request
from miniconstruct.h3.builder import assemble_prompt
from miniconstruct.models.api import GenerationRequest
from miniconstruct.models.story_operations import StoryInitialRangeRequest, StoryInitialRangeResponse, StoryNextResponse, StoryReplanResponse
from miniconstruct.models.story_planning import StoryBuildResponse, StoryRevisionScope
from miniconstruct.models.workspace import ToneLevel
from miniconstruct.project_store import ProjectStore
from miniconstruct.story_operations import build_initial_range
from miniconstruct.story_planning import SCENE_PLAN_POLICY
from test_story_initial_range import LLM, OfflineInitialRangeClient


def test_story_model_contracts_exclude_tone_controls_and_revision_scope():
    for model in (StoryInitialRangeResponse, StoryNextResponse, StoryReplanResponse, StoryBuildResponse):
        assert "tonePerformance" not in json.dumps(model.model_json_schema(by_alias=True))
    with pytest.raises(ValueError):
        StoryRevisionScope("tone_performance_creative_controls")
    assert "one free-form, ordered account" in SCENE_PLAN_POLICY
    assert "cause, reaction and consequence" in SCENE_PLAN_POLICY
    assert "Do not impose a beat count" in SCENE_PLAN_POLICY
    assert "beat" not in StoryInitialRangeResponse.model_json_schema(by_alias=True)["properties"]["items"]["items"].get("properties", {})


@pytest.mark.asyncio
async def test_legacy_model_tone_suggestions_are_ignored_and_new_slots_stay_auto(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory(creativeControls={
        "tonePerformance": {"drama": "strong", "performanceEnergy": "energetic"},
    }), story_idea="A courier crosses the old pass.")

    def obsolete_suggestions(result, context):
        assert "tonePerformance" not in json.dumps(context["responseSchema"])
        for item in result["items"]:
            item["tonePerformance"] = {"drama": "intense", "performanceEnergy": "low"}

    draft = await build_initial_range(store, project.project_id,
        StoryInitialRangeRequest(requestedTotalCount=2, llm=LLM), OfflineInitialRangeClient(obsolete_suggestions))
    assert all("tonePerformance" not in item.model_dump(by_alias=True) for item in draft.items)
    store.apply_story_initial_range(project.project_id, draft)
    g1, g2 = store.read_story_snapshot(project.project_id).generations
    assert g1.workspace.creative_controls.tone_performance.drama.value == "strong"
    assert g1.workspace.creative_controls.tone_performance.performance_energy.value == "energetic"
    assert all(value.value == "auto" for value in g2.workspace.creative_controls.tone_performance.__dict__.values())

    # Manual shared H3 controls still enter Story H3 compilation.
    current = store.activate_generation(project.project_id, g2.generation_id)
    controls = current.workspace.creative_controls.model_copy(deep=True)
    controls.tone_performance.drama = ToneLevel.STRONG
    store.save_project(project.project_id, current.workspace.model_copy(update={"creative_controls": controls}))
    g2 = store.read_story_snapshot(project.project_id).generations[1]
    request = GenerationRequest.model_validate({
        "workspace": g2.workspace.model_dump(mode="json", by_alias=True),
        "filesystemProjectId": project.project_id, "generationId": g2.generation_id,
        "authoringPath": "story", "llm": LLM.model_dump(mode="json", by_alias=True),
    })
    resolved = resolve_narrative_request(request, store)
    inspector = assemble_prompt(resolved.prompt_workspace, False, resolved.narrative_subjects,
                                resolved.continuity_context, resolved.story_context).inspector_text
    assert "Strongly emphasize emotional weight" in inspector
