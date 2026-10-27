"""Provider-free contracts for the production Story Planner instruction layer."""

import pytest

from miniconstruct.story_operations import (
    INITIAL_RANGE_SYSTEM, INSERT_SYSTEM, NEXT_SYSTEM, REPLAN_SYSTEM,
    build_initial_range,
)
from miniconstruct.story_planning import REVISION_AUTHORITY, SCENE_PLAN_POLICY, STORY_SYSTEM
from miniconstruct.models.api import LLMSettings
from miniconstruct.models.story_operations import StoryInitialRangeRequest
from miniconstruct.project_store import ProjectStore


def test_scene_plan_policy_is_shared_and_states_directability_boundaries():
    policy = SCENE_PLAN_POLICY.casefold()
    for clause in (
        "meaningfully develops", "static pose", "abstract intent",
        "observable progression", "action", "movement", "attention",
        "interaction", "reaction", "posture or state", "spatial relationships",
        "beginning leads to development and an outgoing state",
        "intentional stillness", "silence", "pause", "held gaze", "waiting",
        "later generation events", "merely\nto make this generation active",
        "compact and useful", "not decorative", "not a synopsis",
        "not a synopsis, h3 shot plan, timestamp schedule",
        "do not impose a beat count, fixed beat interval",
        "action count, minimum length",
    ):
        assert clause in policy
    assert "where story authority supports it" in policy
    assert "do not add arbitrary\nmovement" in policy
    assert "detailed h3 execution instructions" in policy

    # Every production operation uses the one shared policy, rather than a
    # separate copy that can drift from Initial Range or Replan.
    assert SCENE_PLAN_POLICY in STORY_SYSTEM
    assert SCENE_PLAN_POLICY in REVISION_AUTHORITY
    assert SCENE_PLAN_POLICY not in INITIAL_RANGE_SYSTEM
    assert SCENE_PLAN_POLICY not in NEXT_SYSTEM
    assert SCENE_PLAN_POLICY not in INSERT_SYSTEM
    assert SCENE_PLAN_POLICY not in REPLAN_SYSTEM


@pytest.mark.asyncio
async def test_initial_range_model_request_contains_shared_policy(tmp_path, workspace_factory):
    class OfflinePlanner:
        messages = None

        def __init__(self, _settings):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def generate(self, messages, *, response_instruction=None):
            type(self).messages = messages
            raise RuntimeError("stop after inspecting production assembly")

    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory(), story_idea="G1 begins a search; G2 continues it; G3 finds the target.")
    settings = LLMSettings.model_validate({
        "modelId": "offline", "endpoint": {"id": "offline", "displayName": "Offline", "baseUrl": "http://never.test/v1"},
    })
    with pytest.raises(RuntimeError, match="stop after inspecting"):
        await build_initial_range(store, project.project_id,
            StoryInitialRangeRequest(requestedTotalCount=3, llm=settings), OfflinePlanner)
    assert OfflinePlanner.messages[0]["role"] == "system"
    assert OfflinePlanner.messages[0]["content"].count(SCENE_PLAN_POLICY) == 1


def test_sequence_context_is_durable_and_respects_explicit_user_authority():
    from miniconstruct.story_planning import SEQUENCE_CONTEXT_POLICY
    policy = " ".join(SEQUENCE_CONTEXT_POLICY.split())
    for clause in (
        "requested Generation count is a temporary operation window",
        "not the final Sequence length",
        "must remain valid if the Sequence later grows",
        "do not write them into Sequence Context unless they originate from explicit user authority",
        "Preserve existing user-authored nonblank Sequence Context exactly",
        "Keep requested counts and range boundaries in structured operation input/output",
    ):
        assert clause in policy
