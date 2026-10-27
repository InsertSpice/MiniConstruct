from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import shutil
import zipfile

import pytest

from miniconstruct.benchmark.character_performance import (
    PERFORMANCE_POLICY, PERFORMANCE_SYSTEM_SECTION, REVIEW_QUESTIONS,
    _overlay_isolation_passes,
    load_cases, run_suite, write_run,
)
from miniconstruct.generation import generate_workspace_prompts
from miniconstruct.models.api import GenerationRequest
from test_benchmark import profile


ROOT = Path(__file__).resolve().parents[1] / "benchmarks"
FIXTURE = ROOT / "character-performance"


class FakeClient:
    calls = []

    def __init__(self, settings):
        self.settings = settings
        self.last_payload = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def generate(self, messages):
        type(self).calls.append(messages)
        text = json.dumps(messages, ensure_ascii=False)
        shots = 2 if any(mark in text for mark in (
            "currentH3SetupShots\\\":2", "neutral-subject-sheet.png", "warehouse aisle",
        )) else 1
        if "neutral-subject-sheet.png" in text:
            return (
                "subject_definitions:\n<Subject 1> is the person shown in <Picture 1>.\n"
                "summary:\n[reference generation] A workshop reaction.\n"
                "retention_analysis:\n<Subject 1> (appears in [Shot 1], [Shot 2]): fully_preserved - identity retained.\n"
                "detailed_description:\n[Shot 1] <Subject 1> waits by the door.\n"
                "[Shot 2] <Subject 1> turns toward the sound and settles.\n"
                "overall_soundscape:\nA falling object.\nnon_diegetic_music:\nNone."
            )
        dialogue = " Iris says <d>[English] The door was open when I got here.</d>" if "Iris" in text else ""
        body = "[Shot 1] A person attends to the event." + dialogue
        if shots == 2:
            body += "\n[Shot 2] The action continues."
        return "integrated_multimodal_description:\n" + body + "\noverall_soundscape:\nRoom tone.\nnon_diegetic_music:\nNone."


def test_suite_discovery_and_authority_shapes():
    cases = load_cases(FIXTURE / "cases.json")
    assert [case.id for case in cases] == [
        "strict-neutral-reference-reaction", "story-reserved-profile-news",
        "story-exact-dialogue-listener", "quiet-news-restraint",
        "active-alarm-movement", "one-shot-hope-to-uncertainty",
    ]
    strict = cases[0]
    workspace = strict.model_workspace(FIXTURE)
    assert workspace.mode.value == "Ref2VA"
    assert workspace.creative_controls.subject_identity_fidelity.level.value == "strict"
    assert workspace.assets[0].subject_identity.layout.value == "reference_sheet"
    assert workspace.assets[0].image.data_url.startswith("data:image/png;base64,")
    assert cases[1].story_authority["characterProfiles"][0]["characterProfile"]["personality"].startswith("Normally composed")
    assert cases[2].exact_dialogue == ""
    assert cases[2].workspace["dialogue"] == 'Iris: "The door was open when I got here."'
    assert cases[2].model_workspace(FIXTURE).dialogue == cases[2].story_authority["currentGeneration"]["dialogue"]
    assert cases[3].shot_count == 1 and "Neither cries" in cases[3].workspace["creativeRequest"]
    assert cases[4].shot_count == 2 and "keeps moving" in cases[4].workspace["creativeRequest"]
    assert cases[5].authoring_path == "one_shot" and not cases[5].story_authority


@pytest.mark.asyncio
async def test_pair_uses_same_production_generation_with_only_overlay_difference():
    FakeClient.calls.clear()
    selected = {"strict-neutral-reference-reaction", "story-reserved-profile-news",
                "story-exact-dialogue-listener", "quiet-news-restraint",
                "active-alarm-movement", "one-shot-hope-to-uncertainty"}
    result = await run_suite(ROOT, profile(vision=True), FakeClient, selected)
    assert len(FakeClient.calls) == 12
    for case in result["cases"]:
        assert case["status"] == "completed"
        control = case["variants"]["control"]
        experimental = case["variants"]["experimental"]
        base = control["modelFacingMessages"]
        changed = experimental["modelFacingMessages"]
        assert PERFORMANCE_POLICY not in base[0]["content"]
        assert PERFORMANCE_SYSTEM_SECTION in changed[0]["content"]
        assert changed[1:] == base[1:]
        assert control["requestedSeed"] == experimental["requestedSeed"] == 1
        assert control["effectiveSeed"] == experimental["effectiveSeed"] == 1
        assert control["visionInputs"] == experimental["visionInputs"]
        assert all(check["passed"] for check in case["checks"])
        assert case["reviewQuestions"] == REVIEW_QUESTIONS
        assert "valid" in control["validation"] and "valid" in experimental["validation"]
    by_id = {case["caseId"]: case for case in result["cases"]}
    strict = by_id["strict-neutral-reference-reaction"]
    assert strict["authority"]["subjectIdentityFidelity"] == "strict"
    assert strict["authority"]["referenceSheetDesignated"] is True
    assert strict["variants"]["control"]["modelInput"]["imageInputCount"] == 1
    assert strict["variants"]["control"]["visionInputs"][0]["assetId"] == "neutral-sheet"
    assert by_id["story-reserved-profile-news"]["authority"]["storyCharacterProfilesSupplied"] is True
    assert by_id["story-reserved-profile-news"]["authority"]["currentScenePlan"]
    dialogue_case = by_id["story-exact-dialogue-listener"]
    canonical_dialogue = 'Iris: "The door was open when I got here."'
    assert dialogue_case["authority"]["exactDialogue"] == canonical_dialogue
    for arm in ("control", "experimental"):
        user_text = dialogue_case["variants"][arm]["modelFacingMessages"][1]["content"]
        assert "EXACT RESOLVED SPOKEN OUTPUT" in user_text
        assert canonical_dialogue in user_text.split("EXACT RESOLVED SPOKEN OUTPUT", 1)[1]
    assert dialogue_case["variants"]["control"]["modelFacingMessages"][1] == dialogue_case["variants"]["experimental"]["modelFacingMessages"][1]
    assert next(check for check in dialogue_case["checks"] if check["name"] == "dedicated_exact_dialogue_authority")["passed"]
    assert by_id["one-shot-hope-to-uncertainty"]["authority"]["storyCharacterProfilesSupplied"] is False
    assert "data:image" not in json.dumps(result)


def test_overlay_isolation_check_rejects_an_unrelated_experimental_message_change():
    response = "\n\n## Response behavior\n\nOutput directly."
    control = [{"role": "system", "content": "base" + response}, {"role": "user", "content": "authority"}]
    experimental = [
        {"role": "system", "content": "base" + PERFORMANCE_SYSTEM_SECTION + response},
        {"role": "user", "content": "authority"},
    ]
    assert _overlay_isolation_passes(control, experimental)
    altered = json.loads(json.dumps(experimental))
    altered[1]["content"] = "changed authority"
    assert not _overlay_isolation_passes(control, altered)


@pytest.mark.asyncio
async def test_fake_provider_pair_check_fails_when_experimental_user_message_changes():
    class AlterExperimentalMessageClient(FakeClient):
        async def generate(self, messages):
            if PERFORMANCE_SYSTEM_SECTION in messages[0]["content"]:
                user = messages[1]["content"]
                if isinstance(user, str):
                    messages[1]["content"] = user + "\nUNAPPROVED experimental change"
                else:
                    messages[1]["content"][0]["text"] += "\nUNAPPROVED experimental change"
                self.last_payload = {"messages": deepcopy(messages)}
            return await super().generate(messages)

    result = await run_suite(ROOT, profile(vision=False), AlterExperimentalMessageClient,
                             {"story-exact-dialogue-listener"})
    checks = {item["name"]: item["passed"] for item in result["cases"][0]["checks"]}
    assert checks["experimental_has_overlay"] is True
    assert checks["only_overlay_differs_in_model_messages"] is False


@pytest.mark.asyncio
async def test_control_is_default_production_assembly_and_vision_skip_never_calls_provider():
    FakeClient.calls.clear()
    one_shot = next(case for case in load_cases(FIXTURE / "cases.json") if case.id == "one-shot-hope-to-uncertainty")
    workspace = one_shot.model_workspace(FIXTURE)
    settings = profile(vision=False).to_settings()
    ordinary = await generate_workspace_prompts(
        GenerationRequest(workspace=workspace, llm=settings, resolvedSeeds=[1]), FakeClient)
    result = await run_suite(ROOT, profile(vision=False), FakeClient,
                             {"one-shot-hope-to-uncertainty", "strict-neutral-reference-reaction"})
    control = next(case for case in result["cases"] if case["status"] == "completed")["variants"]["control"]
    assert control["modelFacingMessages"] == ordinary.assembled.messages
    skipped = next(case for case in result["cases"] if case["status"] == "skipped")
    assert skipped["skip"]["code"] == "vision_capability_mismatch"
    assert len(FakeClient.calls) == 3


@pytest.mark.asyncio
async def test_missing_first_shot_is_reported_as_a_structural_check_failure():
    class NoShotClient(FakeClient):
        async def generate(self, messages):
            type(self).calls.append(messages)
            return "integrated_multimodal_description:\nA static description.\noverall_soundscape:\nQuiet.\nnon_diegetic_music:\nNone."

    result = await run_suite(ROOT, profile(vision=False), NoShotClient,
                             {"one-shot-hope-to-uncertainty"})
    checks = {check["name"]: check["passed"] for check in result["cases"][0]["checks"]}
    assert checks["both_variants_completed"] is True
    assert checks["expected_shot_count_valid"] is False
    assert result["cases"][0]["variants"]["control"]["exactOutput"].startswith("integrated_multimodal_description:")


@pytest.mark.asyncio
async def test_wrong_provider_dialogue_fails_production_validator_in_both_arms():
    class WrongDialogueClient(FakeClient):
        async def generate(self, messages):
            prompt = await super().generate(messages)
            return prompt.replace("The door was open when I got here.",
                                  "The door was already open when I got here.")

    result = await run_suite(ROOT, profile(vision=False), WrongDialogueClient,
                             {"story-exact-dialogue-listener"})
    case = result["cases"][0]
    checks = {check["name"]: check["passed"] for check in case["checks"]}
    assert checks["dedicated_exact_dialogue_authority"] is True
    assert checks["only_overlay_differs_in_model_messages"] is True
    assert checks["exact_dialogue_preserved_in_output"] is False
    for arm in ("control", "experimental"):
        validation = case["variants"][arm]["validation"]
        assert validation["valid"] is False
        assert "dialogue_changed" in {finding["code"] for finding in validation["findings"]}


@pytest.mark.asyncio
async def test_sanitized_results_and_existing_manual_render_packs(tmp_path):
    fixture_copy = tmp_path / "character-performance"
    fixture_copy.mkdir()
    shutil.copy2(FIXTURE / "cases.json", fixture_copy / "cases.json")
    (fixture_copy / "assets").mkdir()
    shutil.copy2(FIXTURE / "assets" / "neutral-subject-sheet.png",
                 fixture_copy / "assets" / "neutral-subject-sheet.png")
    result = await run_suite(tmp_path, profile(vision=True), FakeClient,
                             {"strict-neutral-reference-reaction"})
    report = write_run(tmp_path, result)
    saved = (report.parent / "results.json").read_text(encoding="utf-8")
    assert "data:image" not in saved and "base64" not in saved
    assert "neutral-sheet" in saved and "sha256" in saved
    assert "subjectIdentityFidelity" in saved and "storyCharacterProfilesSupplied" in saved
    assert "strict" in saved and "reference_sheet" in saved
    assert json.loads(saved)["experimentalPolicy"] == PERFORMANCE_POLICY
    for arm in ("control", "experimental"):
        bundle = report.parent / f"strict-neutral-reference-reaction.{arm}.manual-render.zip"
        with zipfile.ZipFile(bundle) as archive:
            assert sorted(archive.namelist()) == [
                "manual-render-manifest.json", "prompt.txt", "references/picture-1.png",
            ]
            manifest = json.loads(archive.read("manual-render-manifest.json"))
            assert manifest["subjectIdentityFidelity"] == "strict"
            assert manifest["references"][0]["h3Label"] == "Picture 1"
            assert archive.read("references/picture-1.png") == (fixture_copy / "assets" / "neutral-subject-sheet.png").read_bytes()


@pytest.mark.asyncio
@pytest.mark.parametrize("note", [None, "Opaque suite note supplied through metadata."])
async def test_generic_review_priority_note_and_safe_fence_preserve_raw_output(tmp_path, note):
    shutil.copytree(FIXTURE, tmp_path / "character-performance")
    raw = "`````text\nraw provider response\n`````"
    class FencedClient(FakeClient):
        async def generate(self, messages):
            return raw
    result = await run_suite(tmp_path, profile(vision=False), FencedClient, {"quiet-news-restraint"})
    result["renderPriority"] = ["opaque-alpha", "opaque-beta"]
    if note is not None:
        result["renderPriorityNote"] = note
    report = write_run(tmp_path, result)
    review = report.read_text(encoding="utf-8")
    assert "Matched render priority: opaque-alpha, opaque-beta." in review
    assert "environment-subject" not in review
    assert "``````text\n" + raw + "\n``````" in review
    if note is not None:
        assert note in review
    for arm in result["cases"][0]["variants"].values():
        assert arm["exactOutput"] == raw
        with zipfile.ZipFile(report.parent / arm["manualRenderBundle"]) as archive:
            assert archive.read("prompt.txt").decode("utf-8") == raw
