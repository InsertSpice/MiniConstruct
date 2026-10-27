import json
from pathlib import Path
import shutil
import uuid

from miniconstruct.benchmark.runner import load_profiles
from miniconstruct.benchmark.story_scene_directability import (
    DIRECTABILITY_POLICY, POLICY_SECTION, _safe_messages,
    load_cases, policy_messages, run_suite,
)
from miniconstruct.models.api import SeedMode
from miniconstruct.h3.builder import assemble_prompt
from miniconstruct.story_planning import assemble_story_prompt, initial_story_draft


ROOT = Path(__file__).resolve().parents[1]


def test_fixtures_parse_as_three_small_story_scenarios():
    cases = load_cases(ROOT / "benchmarks/story-scene-directability/cases.json")
    assert len(cases) == 3
    assert {"city-key-search-begins", "quiet-platform-reconciliation", "doorway-handoff"} == {case.id for case in cases}
    assert all(case.desired_generation_count == 3 for case in cases)


def test_control_is_exact_production_build_assembly_and_experimental_only_appends_policy(tmp_path):
    from miniconstruct.benchmark.story import DeterministicStoryProjectStore

    case = load_cases(ROOT / "benchmarks/story-scene-directability/cases.json")[0]
    store = DeterministicStoryProjectStore(tmp_path, case.id)
    project = store.create_project(case.model_workspace(), sequence_context=case.sequence_context,
                                   story_idea=case.story_idea)
    snapshot = store.read_story_snapshot(project.project_id)
    draft = initial_story_draft(snapshot, case.desired_generation_count,
                                item_id_factory=lambda: str(uuid.uuid4()))
    settings = load_profiles(ROOT / "benchmarks/models.example.json")["qwen38"].to_settings()
    production = assemble_story_prompt(snapshot, draft, settings, idea=case.story_idea)
    control = production
    experimental = policy_messages(production)
    assert control == production
    assert experimental[0]["content"] == production[0]["content"] + POLICY_SECTION
    assert experimental[1:] == production[1:]


def test_experimental_policy_stays_inside_scene_plan_abstraction():
    lowered = DIRECTABILITY_POLICY.casefold()
    assert "do not write h3 syntax" in lowered
    assert "shot construction" in lowered
    assert "timestamps" in lowered
    assert "camera instructions" in lowered
    assert "do not force motion" in lowered
    assert "later generation" in lowered
    assert "picture n" not in lowered and "subject n" not in lowered


def test_h3_image_payloads_are_redacted_with_provenance():
    from miniconstruct.models.workspace import ReferenceAsset

    case = load_cases(ROOT / "benchmarks/story-scene-directability/cases.json")[0]
    workspace = case.model_workspace()
    data_url = "data:image/png;base64,AAECAw=="
    asset = ReferenceAsset(id="env", kind="image", filename="platform.png", mimeType="image/png",
                           role="environment", image={"data_url": data_url})
    workspace = workspace.model_copy(update={"assets": [asset]})
    image = {"role": "user", "content": [{"type": "image_url", "image_url": {"url": data_url}}]}
    safe = _safe_messages([image], workspace)
    assert "base64" not in json.dumps(safe)
    assert "image payload omitted" in safe[0]["content"][0]["image_url"]["url"]
    assert safe[0]["content"][0]["imageProvenance"]["assetId"] == "env"


class _FakeProvider:
    planner_settings = []

    def __init__(self, settings):
        self.settings = settings

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def generate(self, messages, **kwargs):
        system = messages[0]["content"]
        if "optional Story Planner" not in system:
            return "detailed_description: Two characters move through a readable sequence of actions.\n\nsubject_definitions: <Subject 1> is the first character. <Subject 2> is the second character.\n\nretention_analysis: Both subjects remain present."
        type(self).planner_settings.append((self.settings.seed_mode.value, self.settings.fixed_seed, self.settings.seed))
        user = messages[1]["content"]
        context = json.loads(user)
        ids = [item["planItemId"] for item in context["draft"]["items"]]
        experimental = "BENCHMARK-ONLY SCENE-PLAN DIRECTABILITY POLICY" in system
        source_id = context["sourceGenerations"][0]["sourceGenerationId"]
        names = ["Kent", "Mira", "Aya"]
        items = []
        for index, slot_id in enumerate(ids):
            if experimental:
                scene_plan = f"{names[index]} takes three observable actions in ordered scene {index + 1}."
            else:
                scene_plan = f"{names[index]} holds the original state in ordered scene {index + 1}."
            items.append({"planItemId": slot_id, "scenePlan": scene_plan,
                          "durationSeconds": 13 if experimental else 10,
                          "dialogue": "A new exact line." if experimental else "",
                          "incomingState": "Experimental incoming." if experimental else "The scene is beginning.",
                          "intendedOutgoingState": "Experimental outgoing." if experimental else "The action continues.",
                          "relationshipToPrevious": None if index == 0 else (
                              "new_shot_preserve_continuity" if experimental else "continue_previous_shot"),
                          "cameraIntent": "Experimental camera idea." if experimental else "",
                          "shotCount": 1,
                          "referenceRecommendations": ([{"sourceGenerationId": source_id,
                              "assetId": "not-a-real-reference", "recommendation": "Experimental reference."}]
                              if experimental else [])})
        return json.dumps({"sequenceContext": context.get("sequenceContext", ""), "items": items,
                           "consistencyWarnings": []})


def test_end_to_end_provider_free_ab_parity_h3_production_only_and_settings_recording(tmp_path):
    fixture_dir = tmp_path / "benchmarks/story-scene-directability"
    fixture_dir.mkdir(parents=True)
    shutil.copyfile(ROOT / "benchmarks/story-scene-directability/cases.json", fixture_dir / "cases.json")
    profiles = load_profiles(ROOT / "benchmarks/models.example.json")
    _FakeProvider.planner_settings.clear()
    random_planner_profile = profiles["qwen38"].model_copy(update={"seed_mode": SeedMode.RANDOM, "fixed_seed": None})
    result = __import__("asyncio").run(run_suite(tmp_path / "benchmarks", random_planner_profile,
                                                    profiles["qwen38"], client_factory=_FakeProvider))
    assert result["profiles"]["planner"]["effective"]["reasoningMode"] == "off"
    assert result["profiles"]["h3"]["effective"]["reasoningMode"] == "off"
    report_json = json.dumps(result, ensure_ascii=False)
    assert "apiKey" not in report_json and "api_key" not in report_json
    assert "data:image" not in report_json
    assert _FakeProvider.planner_settings == [("fixed", 1, 1)] * 6
    assert result["profiles"]["planner"]["effective"]["sourceProfileSeedMode"] == "random"
    assert result["profiles"]["planner"]["effective"]["effectiveSeedPolicy"] == "case.plannerSeed is fixed and shared by both arms"
    assert len(result["cases"]) == 3
    assert sorted(path.name for path in (tmp_path / "benchmarks").iterdir()) == ["story-scene-directability"]
    for case in result["cases"]:
        assert case["plannerInputParity"]["identicalAfterRemovingBenchmarkPolicy"] is True
        assert case["plannerSeed"] == 1
        control, experimental = case["arms"]["control"], case["arms"]["experimental"]
        assert "BENCHMARK-ONLY SCENE-PLAN DIRECTABILITY POLICY" not in control["h3"]["modelFacingRequest"][0]["content"]
        assert "BENCHMARK-ONLY SCENE-PLAN DIRECTABILITY POLICY" not in experimental["h3"]["modelFacingRequest"][0]["content"]
        assert control["h3"]["modelFacingRequest"][0] == experimental["h3"]["modelFacingRequest"][0]
        control_items = control["planner"]["rawPlannerResult"]["parsed"]["items"]
        experimental_items = experimental["planner"]["rawPlannerResult"]["parsed"]["items"]
        assert len(control_items) == len(experimental_items) == 3
        assert experimental["planner"]["rawPlannerResult"]["reply"]
        assert experimental["planner"]["rawPlannerResult"]["parsed"] != control["planner"]["rawPlannerResult"]["parsed"]
        spillover = case["plannerSpilloverDiff"]
        assert len(spillover) == 3
        for spillover_item in spillover:
            changed = spillover_item["changed"]
            assert all(changed[field] for field in (
                "scenePlan", "durationSeconds", "dialogue", "incomingState",
                "intendedOutgoingState", "cameraIntent", "referenceRecommendations"))
            assert changed["relationshipToPrevious"] is (spillover_item["ordinal"] > 1)
        assert experimental["planner"]["validation"]["valid"] is True
        assert experimental["planner"]["effectiveSettings"]["seedMode"] == "fixed"
        assert experimental["planner"]["effectiveSettings"]["effectiveSeed"] == case["plannerSeed"]
        normalized = experimental["planner"]["downstreamNormalizedDraft"]
        control_projected = control["planner"]["rawPlannerResult"]["projected"]
        assert [i["scenePlan"] for i in normalized["items"]] == [
            i["scenePlan"] for i in experimental_items]
        for normalized_item, control_item in zip(normalized["items"], control_projected["items"]):
            assert {k: v for k, v in normalized_item.items() if k != "scenePlan"} == {
                k: v for k, v in control_item.items() if k != "scenePlan"}
        assert [p["scenePlan"] for p in experimental["planner"]["downstreamNormalizedStoryAuthority"]["orderedScenePlans"]] == [
            item["scenePlan"] for item in experimental_items]
        assert experimental["h3"]["effectiveGenerationDurationsSeconds"] == control["h3"]["effectiveGenerationDurationsSeconds"]
        assert case["downstreamH3Parity"]["completeSafeMessagesMatchAfterAllScenePlanNormalization"]
        assert case["downstreamH3Parity"]["effectiveGenerationDurationsMatch"]
        assert control["h3"]["effectiveSettings"] == experimental["h3"]["effectiveSettings"]
        effective_durations = control["planner"]["metrics"]["effectiveGenerationDurationsSeconds"]
        assert effective_durations[0] == control["h3"]["metrics"]["durationSeconds"]
        assert effective_durations[1:] == [6, 6]
        assert control["h3"]["metrics"]["descriptionBodyWords"] >= 0


def test_references_and_secret_fields_do_not_leak_into_reports():
    profiles = load_profiles(ROOT / "benchmarks/models.example.json")
    public = profiles["qwen38"].public_dict()
    assert "apiKeyEnv" not in public
    serialized = json.dumps(public)
    assert "api_key" not in serialized and "apiKey" not in serialized
