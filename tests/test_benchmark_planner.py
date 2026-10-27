from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from miniconstruct.benchmark.runner import (
    BenchmarkCase,
    BenchmarkRunner,
    BenchmarkProfile,
    PlannerFixture,
    ProfileFile,
    load_planner_case,
    load_suite,
)


ROOT = Path(__file__).resolve().parents[1] / "benchmarks"
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@pytest.fixture
def external_source_root(tmp_path):
    source = tmp_path / "MiniConstruct-Benchmark-Source" / "assets"
    source.mkdir(parents=True)
    for name in ("alice_station_identity.png", "alice_station_endpoint.png", "station_environment.png"):
        (source / name).write_bytes(TINY_PNG)
    return source.parent


def profile(*, vision: bool = False):
    from miniconstruct.benchmark.runner import BenchmarkProfile

    return BenchmarkProfile.model_validate({
        "id": "planner-fake", "displayName": "Planner fake", "modelId": "planner",
        "supportsVision": vision,
        "endpoint": {"id": "fake-endpoint", "displayName": "Fake endpoint", "baseUrl": "http://fake.test/v1"},
        "reasoningMode": "on",
    })


@pytest.mark.parametrize("timeout", [600, 1200, 3600])
def test_benchmark_profile_accepts_extended_local_timeouts(timeout):
    value = profile().model_dump(by_alias=True)
    value["timeoutSeconds"] = timeout
    assert BenchmarkProfile.model_validate(value).timeout_seconds == timeout


def test_benchmark_profile_rejects_timeout_above_extended_ceiling():
    value = profile().model_dump(by_alias=True)
    value["timeoutSeconds"] = 3601
    with pytest.raises(ValidationError):
        BenchmarkProfile.model_validate(value)


def test_profile_file_keeps_qwen_1200_and_gemma_profiles_selectable():
    qwen = profile().model_dump(by_alias=True)
    qwen.update({"id": "qwen-thinking", "displayName": "Qwen thinking", "timeoutSeconds": 1200})
    gemma = profile().model_dump(by_alias=True)
    gemma.update({"id": "gemma", "displayName": "Gemma"})
    profiles = ProfileFile.model_validate({"schemaVersion": 1, "profiles": [qwen, gemma]}).profiles
    assert [(item.id, item.timeout_seconds) for item in profiles] == [("qwen-thinking", 1200), ("gemma", 120)]


def plan(**overrides) -> str:
    value = {
        "creativeRequest": "Alice begins walking toward the train doors while remaining on the platform.",
        "incomingState": "Alice is beside the arriving train.",
        "intendedOutgoingState": "Alice reaches the train doors.",
        "relationshipToPrevious": "new_shot_preserve_continuity",
        "cameraIntent": "A deliberate medium tracking shot from the platform.",
        "referenceRecommendations": [],
    }
    value.update(overrides)
    return json.dumps(value)


class FakePlannerClient:
    instances: list["FakePlannerClient"] = []
    response = plan()

    def __init__(self, settings):
        self.settings = settings
        self.messages = None
        self.last_usage = {"prompt_tokens": 12, "completion_tokens": 8}
        self.last_payload = None
        self.compatibility_fallback = False
        type(self).instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def generate(self, messages, *, response_instruction=None):
        self.messages = messages
        self.response_instruction = response_instruction
        return type(self).response


def test_planner_suite_and_json_fixtures_parse(external_source_root):
    suite = load_suite(ROOT, "planner")
    assert len(suite.cases) == 12
    visual_cases = {
        "planner-picture-identity-continuity",
        "planner-match-ending-frame",
        "planner-picture-environment-continuity",
    }
    for case_id in suite.cases:
        case, current, previous, _case_dir, _source = load_planner_case(ROOT, case_id, external_source_root)
        assert case.planner is not None
        assert case.planner.operation in {"infer_plan", "revise_plan"}
        assert current.creative_request
        if case_id in visual_cases:
            assert case.requirements.vision is True
        if case.planner.operation == "infer_plan":
            assert previous is not None

    identity, _current, _previous, _case_dir, _source = load_planner_case(
        ROOT, "planner-picture-identity-continuity", external_source_root
    )
    continuity = identity.planner.previous_generation
    assert continuity is not None
    assert "window" not in continuity.incoming_state.lower()
    assert "doorway" not in continuity.intended_outgoing_state.lower()
    assert "rainy railway platform" in continuity.incoming_state.lower()
    assert "arriving train" in continuity.intended_outgoing_state.lower()

    ending, _current, ending_previous, _case_dir, _source = load_planner_case(
        ROOT, "planner-match-ending-frame", external_source_root
    )
    assert ending.planner is not None
    assert ending.planner.previous_generation is not None
    assert ending.planner.previous_generation.workspace_path == "previous-workspace.json"
    assert ending_previous is not None
    assert ending_previous.project_name == "Rainy railway platform predecessor"


def test_invalid_planner_operation_and_schema_are_rejected():
    with pytest.raises(ValidationError):
        BenchmarkCase.model_validate({"schemaVersion": 1, "id": "bad", "title": "Bad", "operation": "story_plan"})
    with pytest.raises(ValidationError):
        BenchmarkCase.model_validate({"schemaVersion": 2, "id": "bad", "title": "Bad"})


def test_planner_cross_field_invariants_are_model_level_and_authoritative():
    with pytest.raises(ValidationError, match="require revision"):
        PlannerFixture.model_validate({"operation": "revise_plan"})
    with pytest.raises(ValidationError, match="must not contain revision"):
        PlannerFixture.model_validate({"operation": "infer_plan", "previousGeneration": {}, "revision": {"instruction": "x", "editableScopes": ["camera_intent"]}})
    with pytest.raises(ValidationError, match="require previousGeneration"):
        PlannerFixture.model_validate({"operation": "infer_plan"})
    with pytest.raises(ValidationError, match="require planner configuration"):
        BenchmarkCase.model_validate({"schemaVersion": 1, "id": "bad", "title": "Bad", "operation": "infer_plan"})
    with pytest.raises(ValidationError, match="require planner configuration"):
        BenchmarkCase.model_validate({"schemaVersion": 1, "id": "bad", "title": "Bad", "operation": "revise_plan"})
    with pytest.raises(ValidationError, match="does not match"):
        BenchmarkCase.model_validate({
            "schemaVersion": 1, "id": "bad", "title": "Bad", "operation": "revise_plan",
            "planner": {"operation": "infer_plan", "previousGeneration": {}},
        })
    with pytest.raises(ValidationError, match="must not contain planner"):
        BenchmarkCase.model_validate({
            "schemaVersion": 1, "id": "bad", "title": "Bad", "operation": "generate_prompt",
            "planner": {"operation": "infer_plan", "previousGeneration": {}},
        })
    # Legacy H3 cases retain the canonical omitted operation shape.
    legacy = BenchmarkCase.model_validate({"schemaVersion": 1, "id": "legacy", "title": "Legacy"})
    assert legacy.operation is None and legacy.planner is None


@pytest.mark.asyncio
async def test_planner_current_and_previous_workspaces_use_authoring_validation(tmp_path):
    root = tmp_path / "benchmarks"
    case_dir = root / "cases" / "planner-authoring-draft"
    case_dir.mkdir(parents=True)
    (case_dir / "case.json").write_text(json.dumps({
        "schemaVersion": 1, "id": "planner-authoring-draft", "title": "Blank draft", "operation": "infer_plan",
        "planner": {"operation": "infer_plan", "previousGeneration": {}, "currentGeneration": {"workspacePath": "draft.json"}},
    }), encoding="utf-8")
    (case_dir / "draft.json").write_text(json.dumps({
        "schemaVersion": 1, "mode": "T2VA", "durationSeconds": 6, "variations": 1, "creativeRequest": "",
    }), encoding="utf-8")
    (case_dir / "previous-workspace.json").write_text(json.dumps({
        "schemaVersion": 1, "mode": "T2VA", "durationSeconds": 6, "variations": 1, "creativeRequest": "Previous committed action.",
    }), encoding="utf-8")
    _case, current, previous, _directory, _source = load_planner_case(root, "planner-authoring-draft")
    assert current.creative_request == ""
    assert previous is not None and previous.creative_request == "Previous committed action."
    FakePlannerClient.instances.clear()
    FakePlannerClient.response = plan()
    result = await BenchmarkRunner(root, FakePlannerClient).run(profile(), ["planner-authoring-draft"])
    assert result["cases"][0]["status"] == "completed"


@pytest.mark.asyncio
async def test_infer_planner_benchmark_uses_production_assembly_and_canonical_settings():
    FakePlannerClient.instances.clear()
    FakePlannerClient.response = plan()
    result = await BenchmarkRunner(ROOT, FakePlannerClient).run(profile(), ["planner-manual-authority"])
    item = result["cases"][0]
    assert item["operation"] == "infer_plan"
    assert item["status"] == "completed"
    assert item["settings"] == {
        "temperature": 0.4, "seedMode": "fixed", "fixedSeed": 1,
        "variations": 1, "reasoning": "off", "transport": "non_streaming",
    }
    assert "Non-empty current user-authored fields are authoritative" in FakePlannerClient.instances[0].messages[0]["content"]
    assert "Alice begins walking toward the train doors" in FakePlannerClient.instances[0].messages[1]["content"]
    assert "Return exactly one JSON object" in FakePlannerClient.instances[0].messages[0]["content"]
    assert "Output the finished H3 prompt directly." not in json.dumps(item["modelInput"])
    assert FakePlannerClient.instances[0].response_instruction is None
    assert item["provider"]["usage"] == {"prompt_tokens": 12, "completion_tokens": 8}


@pytest.mark.asyncio
async def test_revision_uses_production_scoped_revision_path_and_selected_checks():
    FakePlannerClient.instances.clear()
    FakePlannerClient.response = plan(
        creativeRequest="Alice bursts through the doorway and runs into the rain.",
        incomingState="Alice stands beside the door.",
        intendedOutgoingState="Alice ends outside in the rain.",
        relationshipToPrevious="new_shot_preserve_continuity",
        cameraIntent="Wide static room composition.",
    )
    result = await BenchmarkRunner(ROOT, FakePlannerClient).run(profile(), ["planner-revision-action-and-outgoing"])
    item = result["cases"][0]
    assert item["operation"] == "revise_plan"
    assert item["machineChecks"]["passed"]
    assert "editableScopes" in FakePlannerClient.instances[0].messages[1]["content"]


@pytest.mark.asyncio
async def test_planner_machine_checks_cover_manual_equality_pairs_and_media_honesty():
    FakePlannerClient.instances.clear()
    FakePlannerClient.response = plan()
    manual = (await BenchmarkRunner(ROOT, FakePlannerClient).run(profile(), ["planner-manual-authority"]))["cases"][0]
    assert manual["machineChecks"]["passed"]

    FakePlannerClient.response = plan(
        incomingState="I heard the audio and the speaker says hello.",
    )
    media = (await BenchmarkRunner(ROOT, FakePlannerClient).run(profile(), ["planner-unanalysed-media-honesty"]))["cases"][0]
    assert not media["machineChecks"]["passed"]
    assert any(check["kind"] == "forbidden_text_absent" and not check["passed"] for check in media["machineChecks"]["checks"])

    FakePlannerClient.response = plan(referenceRecommendations=[
        {"source": "current_generation", "assetId": "fabricated", "recommendation": "I heard the speaker says something."},
    ])
    raw_media = (await BenchmarkRunner(ROOT, FakePlannerClient).run(profile(), ["planner-unanalysed-media-honesty"]))["cases"][0]
    assert raw_media["structuredPlannerOutput"]["referenceRecommendations"] == []
    assert not raw_media["machineChecks"]["passed"]

    FakePlannerClient.response = plan(referenceRecommendations=[
        {"source": "previous_generation", "assetId": "shared-reference", "recommendation": "Reuse voice."},
        {"source": "current_generation", "assetId": "shared-reference", "recommendation": "Use camera metadata."},
    ])
    source = (await BenchmarkRunner(ROOT, FakePlannerClient).run(profile(), ["planner-reference-source-disambiguation"]))["cases"][0]
    assert source["machineChecks"]["passed"]

    FakePlannerClient.response = plan(referenceRecommendations=[
        {"source": "current_generation", "assetId": "audio-alice-voice", "recommendation": "Fabricated source."},
    ])
    invalid = (await BenchmarkRunner(ROOT, FakePlannerClient).run(profile(), ["planner-audio-voice-continuity"]))["cases"][0]
    assert not invalid["machineChecks"]["passed"]


@pytest.mark.asyncio
async def test_external_picture_cases_send_real_resolved_images_only_with_vision(external_source_root):
    cases = {
        "planner-picture-identity-continuity": "picture-alice",
        "planner-match-ending-frame": "picture-alice-endpoint",
        "planner-picture-environment-continuity": "picture-station-environment",
    }
    for case_id, asset_id in cases.items():
        FakePlannerClient.instances.clear()
        FakePlannerClient.response = plan()
        result = (await BenchmarkRunner(ROOT, FakePlannerClient, source_root=external_source_root).run(
            profile(vision=True), [case_id]
        ))["cases"][0]
        assert result["referenceInputs"][0]["assetId"] == asset_id
        assert result["referenceInputs"][0]["fixtureSource"] == "external"
        parts = FakePlannerClient.instances[0].messages[1]["content"]
        assert len([part for part in parts if part.get("type") == "image_url"]) == 1

        FakePlannerClient.instances.clear()
        skipped = (await BenchmarkRunner(ROOT, FakePlannerClient).run(
            profile(vision=False), [case_id]
        ))["cases"][0]
        assert skipped["status"] == "skipped"
        assert skipped["skip"]["code"] == "vision_capability_mismatch"
        assert FakePlannerClient.instances == []


@pytest.mark.asyncio
async def test_planner_result_sanitizes_image_payloads_and_secrets(external_source_root):
    FakePlannerClient.instances.clear()
    FakePlannerClient.response = plan(referenceRecommendations=[
        {"source": "current_generation", "assetId": "picture-alice", "recommendation": "Preserve identity."},
    ])
    result = (await BenchmarkRunner(ROOT, FakePlannerClient, source_root=external_source_root).run(
        profile(vision=True), ["planner-picture-identity-continuity"]
    ))["cases"][0]
    serialized = json.dumps(result)
    assert "data:image/" not in serialized
    assert "Authorization" not in serialized
    assert "referenceRecommendations" in serialized
    assert any(
        part.get("type") == "image_url"
        for part in FakePlannerClient.instances[0].messages[1]["content"]
    )


@pytest.mark.asyncio
async def test_planner_error_result_keeps_raw_malformed_output():
    FakePlannerClient.response = "not valid planner JSON"
    result = (await BenchmarkRunner(ROOT, FakePlannerClient).run(profile(), ["planner-manual-authority"]))["cases"][0]
    assert result["status"] == "error"
    assert result["rawModelOutput"] == "not valid planner JSON"
