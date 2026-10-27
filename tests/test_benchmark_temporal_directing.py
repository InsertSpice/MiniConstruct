from __future__ import annotations

import json
from pathlib import Path

import pytest

from miniconstruct.benchmark.temporal_directing import (
    STRUCTURAL_PRIORITY_RULE, SUITE_NAME, TEMPORAL_DIRECTING_POLICY,
    TEMPORAL_DIRECTING_SYSTEM_SECTION, TemporalCase, capture_authority,
    extract_description_body, load_cases, message_metrics, run_case, run_suite, write_run,
)
from miniconstruct.benchmark.runner import BenchmarkProfile
from miniconstruct.h3.builder import STORY_DIRECTING_POLICY
from miniconstruct.h3.validator import validate_workspace_prompt
from test_benchmark import profile


ROOT = Path(__file__).resolve().parents[1] / "benchmarks"
FIXTURE_ROOT = ROOT / "temporal-directing"
T2VA_PROMPT = """integrated_multimodal_description:
[Shot 1] A person turns, takes two steps, and pauses. Alice says <d>[English] I thought you had left.</d> Rowan replies <d>[English] I stayed.</d>
overall_soundscape:
Soft room tone.
non_diegetic_music:
None.
"""
REF2VA_PROMPT = """subject_definitions:
<Subject 1> is Kent, identified by <Picture 1>.
<Subject 2> is Takina, identified by <Picture 2>.
<Subject 3> is Maki, identified by <Picture 3>.

summary:
[reference generation] The three Subjects search a city street.

retention_analysis:
<Subject 1> (appears in [Shot 1], [Shot 2]): fully_preserved - identity retained.
<Subject 2> (appears in [Shot 1], [Shot 2]): fully_preserved - identity retained.
<Subject 3> (appears in [Shot 1], [Shot 2]): fully_preserved - identity retained.

detailed_description:
The target uses live action.
[Shot 1] <Subject 1> moves through the street while <Subject 2> follows; <Subject 3> passes in the background.
[Shot 2] At 00:06.000, <Subject 1> turns toward a side street and continues searching; <Subject 2> and <Subject 3> follow behind.

overall_soundscape:
City ambience.

non_diegetic_music:
N/A"""


class FakeClient:
    instances = []

    def __init__(self, settings):
        self.settings = settings
        self.last_payload = None
        type(self).instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def generate(self, messages):
        self.messages = messages
        blob = json.dumps(messages, ensure_ascii=False)
        return REF2VA_PROMPT if "CANONICAL STORY H3 AUTHORITY" in blob else T2VA_PROMPT


def test_fixture_loading_is_generic_and_accepts_a_single_renamed_case(tmp_path):
    case = {
        "id": "independent-case", "title": "Independent", "authoringPath": "one_shot",
        "durationSeconds": 6, "shotCount": 1,
        "workspace": {"mode": "T2VA", "creativeRequest": "A quiet room."},
    }
    path = tmp_path / "cases.json"
    path.write_text(json.dumps({"schemaVersion": 1, "suite": SUITE_NAME, "cases": [case]}), encoding="utf-8")
    assert [item.id for item in load_cases(path)] == ["independent-case"]
    case["id"] = "same-case"
    path.write_text(json.dumps({"schemaVersion": 1, "suite": SUITE_NAME, "cases": [case, case]}), encoding="utf-8")
    with pytest.raises(ValueError, match="unique IDs"):
        load_cases(path)


def test_kent_is_a_story_boundary_fixture_with_generic_authority_markers():
    case = next(item for item in load_cases(FIXTURE_ROOT / "cases.json")
                if any(marker.text == "gold key" for marker in item.authority_markers))
    authority = capture_authority(case, case.model_workspace(FIXTURE_ROOT))
    assert case.duration_seconds == 13
    assert [item["ordinal"] for item in case.story_authority["orderedScenePlans"]] == [1, 2, 3, 4]
    assert [item["current"] for item in case.story_authority["orderedScenePlans"]] == [True, False, False, False]
    assert "future Generations" not in case.story_authority["sequenceContext"]
    assert authority["declaredAuthorityMarkers"] == [{
        "label": "quest object established in the Story Plan", "text": "gold key", "presentInStoryAuthority": True,
    }]
    assert authority["neighboringScenePlans"][0]["ordinal"] == 2
    assert len(authority["activeReferenceSummary"]) == 3
    mappings = case.story_authority["characterProfiles"]
    assert mappings == [
        {"subjectId": "subject-1", "canonicalH3Label": "<Subject 1>", "narrativeName": "Kent"},
        {"subjectId": "subject-2", "canonicalH3Label": "<Subject 2>", "narrativeName": "Takina"},
        {"subjectId": "subject-3", "canonicalH3Label": "<Subject 3>", "narrativeName": "Maki"},
    ]
    assert authority["storyNarrativeIdentities"] == [
        {"narrativeName": "Kent", "canonicalH3Label": "<Subject 1>", "characterProfileSupplied": False},
        {"narrativeName": "Takina", "canonicalH3Label": "<Subject 2>", "characterProfileSupplied": False},
        {"narrativeName": "Maki", "canonicalH3Label": "<Subject 3>", "characterProfileSupplied": False},
    ]
    assert authority["storyCharacterProfilesSupplied"] is False


def test_authority_profile_diagnostic_uses_only_model_facing_story_projection():
    case = next(item for item in load_cases(FIXTURE_ROOT / "cases.json") if item.authoring_path == "story")
    workspace = case.model_workspace(FIXTURE_ROOT)
    supplied = json.loads(json.dumps(case.story_authority))
    supplied["characterProfiles"][1]["characterProfile"] = {"personality": "Patient and observant."}
    altered_case = TemporalCase.model_validate({
        **case.model_dump(mode="json", by_alias=True), "storyAuthority": supplied,
    })
    diagnostics = capture_authority(altered_case, workspace)
    assert diagnostics["storyNarrativeIdentities"][1] == {
        "narrativeName": "Takina", "canonicalH3Label": "<Subject 2>", "characterProfileSupplied": True,
    }
    assert diagnostics["storyCharacterProfilesSupplied"] is True

    one_shot = next(item for item in load_cases(FIXTURE_ROOT / "cases.json") if item.authoring_path == "one_shot")
    one_shot_diagnostics = capture_authority(one_shot, one_shot.model_workspace(FIXTURE_ROOT))
    assert one_shot_diagnostics["storyNarrativeIdentities"] == []
    assert one_shot_diagnostics["storyCharacterProfilesSupplied"] is False


def test_ref2va_identity_pictures_materialize_as_canonical_workspace_assets():
    case = next(item for item in load_cases(FIXTURE_ROOT / "cases.json") if item.workspace.get("mode") == "Ref2VA")
    workspace = case.model_workspace(FIXTURE_ROOT)
    assert workspace.mode.value == "Ref2VA"
    assert [asset.subject_identity.subject_id for asset in workspace.assets] == ["subject-1", "subject-2", "subject-3"]
    assert all(asset.role == "subject_identity" and asset.image.data_url.startswith("data:image/png;base64,")
               for asset in workspace.assets)
    assert [fixture.path for fixture in case.fixture_assets] == [
        "assets/kent-subject-1.png", "assets/takina-subject-2.png", "assets/maki-subject-3.png",
    ]


@pytest.mark.asyncio
async def test_vision_required_case_skips_clearly_for_text_only_profile_without_provider_access():
    FakeClient.instances.clear()
    case = next(item for item in load_cases(FIXTURE_ROOT / "cases.json") if item.requires_vision)
    result = await run_case(case, profile(vision=False).to_settings(), FakeClient,
                            {"control", "experimental"}, FIXTURE_ROOT)
    assert result["status"] == "skipped"
    assert result["skip"]["code"] == "vision_capability_mismatch"
    assert "vision-enabled" in result["skip"]["message"]
    assert FakeClient.instances == []


def test_description_body_metrics_cover_valid_t2va_and_ref2va_outputs():
    cases = load_cases(FIXTURE_ROOT / "cases.json")
    quiet_case = next(case for case in cases if case.authoring_path == "one_shot" and case.exact_dialogue)
    ref_case = next(case for case in cases if case.workspace.get("mode") == "Ref2VA")
    t2_workspace = quiet_case.model_workspace(FIXTURE_ROOT)
    t2_validation = validate_workspace_prompt(T2VA_PROMPT, t2_workspace).model_dump(mode="json")
    assert t2_validation["valid"]
    t2_metrics = message_metrics(T2VA_PROMPT, t2_validation, 8, 1)
    assert t2_metrics["descriptionBodyCharacters"] > 0
    assert t2_metrics["descriptionBodyWords"] > 0
    assert extract_description_body(T2VA_PROMPT).startswith("[Shot 1]")

    ref_workspace = ref_case.model_workspace(FIXTURE_ROOT).model_copy(update={"shots": 2})
    ref_validation = validate_workspace_prompt(REF2VA_PROMPT, ref_workspace).model_dump(mode="json")
    assert ref_validation["valid"]
    ref_metrics = message_metrics(REF2VA_PROMPT, ref_validation, 13, 2)
    assert ref_metrics["descriptionBodyCharacters"] > 0
    assert ref_metrics["descriptionBodyWords"] > 0
    assert extract_description_body(REF2VA_PROMPT).startswith("The target uses live action.")


def test_h3_timestamp_count_recognizes_timestamp_and_text_forms_without_double_counting():
    prompt = """integrated_multimodal_description:
[Shot 1] A performer begins; the held pose lasts until 00:04.000, about 4 seconds.
[Shot 2] At 00:06.000, she turns; by 00:08.300 she stops, after 6.0 s.
overall_soundscape:
Quiet.
non_diegetic_music:
None."""
    result = message_metrics(prompt, {"valid": True}, 10, 2)
    assert result["explicitInternalTimestampCount"] == 5


@pytest.mark.asyncio
async def test_control_and_experiment_differ_only_by_appended_system_section():
    FakeClient.instances.clear()
    cases = load_cases(FIXTURE_ROOT / "cases.json")
    parity_cases = [next(case for case in cases if case.requires_vision),
                    next(case for case in cases if case.authoring_path == "one_shot" and case.exact_dialogue)]
    for case in parity_cases:
        result = await run_suite(ROOT, profile(vision=True), FakeClient, {case.id})
        case_result = result["cases"][0]
        control = case_result["variants"]["control"]["modelFacingMessages"]
        experiment = case_result["variants"]["experimental"]["modelFacingMessages"]
        assert len(experiment) == len(control)
        assert [message["role"] for message in experiment] == [message["role"] for message in control]
        assert control[0]["role"] == "system"
        assert experiment[0]["content"] == control[0]["content"] + TEMPORAL_DIRECTING_SYSTEM_SECTION
        assert TEMPORAL_DIRECTING_POLICY not in control[0]["content"]
        assert STRUCTURAL_PRIORITY_RULE in experiment[0]["content"]
        restored = json.loads(json.dumps(experiment))
        assert restored[0]["content"].endswith(TEMPORAL_DIRECTING_SYSTEM_SECTION)
        restored[0]["content"] = restored[0]["content"][:-len(TEMPORAL_DIRECTING_SYSTEM_SECTION)]
        assert restored == control
        assert experiment[1:] == control[1:]
        if case.requires_vision:
            content = json.dumps(control, ensure_ascii=False).lower()
            assert STORY_DIRECTING_POLICY in control[0]["content"]
            assert "gold key" in content and "orderedsceneplans" in content
            control_arm = case_result["variants"]["control"]
            experiment_arm = case_result["variants"]["experimental"]
            assert control_arm["modelInput"]["imageInputCount"] == 3
            assert control_arm["visionInputs"] == experiment_arm["visionInputs"]
            assert control[1] == experiment[1]
            assert [part["type"] for part in control[1]["content"]] == [
                part["type"] for part in experiment[1]["content"]
            ]
            assert "characterProfiles" in control[1]["content"][0]["text"]
            assert json.dumps({**case.story_authority, "currentH3SetupShots": case.model_workspace(FIXTURE_ROOT).shots},
                              ensure_ascii=False, separators=(",", ":")) in control[1]["content"][0]["text"]
            assert "continuity" in content
        else:
            content = json.dumps(control, ensure_ascii=False)
            assert case.exact_dialogue.replace("\n", "\\n") in content
            assert "They have been apart" in content


@pytest.mark.asyncio
@pytest.mark.parametrize(("seed_mode", "source_fixed_seed"), [("random", None), ("fixed", 37)])
async def test_run_settings_report_effective_profile_values_and_suite_seed_override(
    tmp_path, monkeypatch, seed_mode, source_fixed_seed,
):
    FakeClient.instances.clear()
    monkeypatch.setenv("TEMPORAL_BENCHMARK_TEST_KEY", "never-persist-this-secret")
    profile_data = profile(vision=True).model_dump(mode="json", by_alias=True)
    profile_data.update({
        "displayName": "Friendly label that is not the model ID",
        "modelId": "vendor/model-exact-id",
        "temperature": 0.73,
        "maxTokens": 777,
        "contextWindow": 32768,
        "reasoningMode": "on",
        "seedMode": seed_mode,
        "fixedSeed": source_fixed_seed,
        "apiKeyEnv": "TEMPORAL_BENCHMARK_TEST_KEY",
    })
    selected_profile = BenchmarkProfile.model_validate(profile_data)
    result = await run_suite(
        ROOT, selected_profile, FakeClient, {"quiet-platform-dialogue"}, {"control"},
    )
    settings = result["settings"]
    assert settings == {
        "source": "benchmark_profile_and_temporal_suite_harness",
        "modelId": "vendor/model-exact-id",
        "temperature": 0.73,
        "maxTokens": 777,
        "contextWindow": 32768,
        "reasoningMode": "on",
        "supportsVision": True,
        "seedMode": "fixed",
        "effectiveSeed": 1,
        "seedPolicy": "fixed seed 1 for temporal A/B; overrides source profile seed settings",
        "sourceProfileSeedMode": seed_mode,
        "sourceProfileFixedSeed": source_fixed_seed,
        "transport": "non_streaming",
        "llmJudge": "none",
    }
    assert result["profile"]["modelId"] == "vendor/model-exact-id"
    actual = FakeClient.instances[0].settings
    assert actual.model_id == "vendor/model-exact-id"
    assert actual.temperature == 0.73 and actual.max_tokens == 777 and actual.context_window == 32768
    assert actual.reasoning_mode.value == "on" and actual.supports_vision is True
    assert actual.seed_mode.value == "fixed" and actual.fixed_seed == actual.seed == 1
    write_run(tmp_path, result)
    persisted = (tmp_path / "runs" / SUITE_NAME / result["runId"] / "results.json").read_text(encoding="utf-8")
    assert "never-persist-this-secret" not in persisted
    assert "apiKey" not in persisted


@pytest.mark.asyncio
async def test_text_only_suite_skips_only_vision_required_case():
    FakeClient.instances.clear()
    result = await run_suite(ROOT, profile(vision=False), FakeClient)
    statuses = {case["caseId"]: case["status"] for case in result["cases"]}
    requirements = {case.id: case.requires_vision for case in load_cases(FIXTURE_ROOT / "cases.json")}
    assert all(status == ("skipped" if requirements[case_id] else "completed") for case_id, status in statuses.items())
    assert len(FakeClient.instances) == 2 * sum(not required for required in requirements.values())


@pytest.mark.asyncio
async def test_vision_inputs_reach_both_arms_and_are_redacted_in_persisted_reports(tmp_path):
    FakeClient.instances.clear()
    vision_case = next(case for case in load_cases(FIXTURE_ROOT / "cases.json") if case.requires_vision)
    result = await run_suite(ROOT, profile(vision=True), FakeClient, {vision_case.id})
    case = result["cases"][0]
    assert case["status"] == "completed"
    for variant in ("control", "experimental"):
        arm = case["variants"][variant]
        assert arm["modelInput"]["imageInputCount"] == 3
        assert [image["assetId"] for image in arm["visionInputs"]] == [
            "subject-1-identity", "subject-2-identity", "subject-3-identity",
        ]
        assert all(image["sha256"] and image["width"] == 320 and image["height"] == 480
                   for image in arm["visionInputs"])
        assert all("data:image" not in json.dumps(message) for message in arm["modelFacingMessages"])
    report = write_run(tmp_path, result)
    persisted = (tmp_path / "runs" / SUITE_NAME / result["runId"] / "results.json").read_text(encoding="utf-8")
    assert "data:image" not in persisted and "base64" not in persisted
    assert "fixturePath" in persisted and "sha256" in persisted
    report_text = report.read_text(encoding="utf-8")
    assert "fixturePath" in report_text and "sha256" in report_text


def test_authority_marker_schema_does_not_special_case_story_facts():
    case = TemporalCase.model_validate({
        "id": "marker-case", "title": "Marker", "authoringPath": "story", "durationSeconds": 6, "shotCount": 1,
        "workspace": {"mode": "T2VA", "creativeRequest": ""},
        "storyAuthority": {
            "storyPlanPremise": "A silver compass is missing.",
            "currentGeneration": {"scenePlan": "Mara searches the room."},
            "orderedScenePlans": [{"ordinal": 1, "scenePlan": "Mara searches the room.", "current": True}],
        },
        "authorityMarkers": [{"label": "object from premise", "text": "silver compass"}],
    })
    assert case.authority_markers[0].model_dump() == {"label": "object from premise", "text": "silver compass"}


def test_result_persistence_keeps_human_review_fields(tmp_path):
    case_result = {
        "caseId": "small-case", "title": "A case", "status": "completed",
        "storyAuthority": {"authoringPath": "one_shot"}, "durationSeconds": 6, "shotCount": 1,
        "reviewSheet": {"fields": {"overallPreference": {"question": "Preference?", "value": None},
                                      "reviewerNote": {"question": "Note", "value": None}}},
        "variants": {"control": {"exactOutput": "control", "metrics": {"totalWords": 1}, "modelInput": {}, "visionInputs": []},
                     "experimental": {"exactOutput": "experiment", "metrics": {"totalWords": 1}, "modelInput": {}, "visionInputs": []}},
    }
    result = {"profile": {"displayName": "Test"}, "runId": "test-run", "cases": [case_result]}
    report = write_run(tmp_path, result)
    saved = json.loads((tmp_path / "runs" / SUITE_NAME / "test-run" / "results.json").read_text())
    assert saved["cases"][0]["reviewSheet"]["fields"]["overallPreference"]["value"] is None
    assert "Control" in report.read_text() and "Experimental" in report.read_text()
