from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

import pytest

from miniconstruct.benchmark import character_performance as harness
from miniconstruct.benchmark import natural_liveliness as suite
from miniconstruct.generation import generate_workspace_prompts
from miniconstruct.models.api import GenerationRequest, ReasoningMode
from miniconstruct.models.workspace import H3PromptDetail
from test_benchmark import profile

ROOT = Path(__file__).resolve().parents[1] / "benchmarks"
FIXTURE = ROOT / suite.FIXTURE_NAME


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
        self.calls.append((deepcopy(messages), self.settings.model_dump(mode="json")))
        text = json.dumps(messages)
        if "Selected mode: I2VA." in text:
            return ("For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.\n\n"
                    "integrated_multimodal_description: [Shot 1] A calm anime character remains present and attentive.\n\n"
                    "overall_soundscape: Quiet room tone.\n\nnon_diegetic_music: N/A")
        if "neutral-subject-sheet.png" in text:
            return (
                "subject_definitions:\n<Subject 1> is the adult shown in <Picture 1>.\n"
                "<Subject 2> is another adult at the bus stop.\n"
                "summary:\n[reference generation] Two adults wait.\n"
                "retention_analysis:\n<Subject 1> (appears in [Shot 1]): fully_preserved - identity retained.\n"
                "<Subject 2> (appears in [Shot 1]): fully_preserved - identity retained.\n"
                "detailed_description:\n[Shot 1] <Subject 1> and <Subject 2> wait at the bus stop.\n"
                "overall_soundscape:\nStreet ambience.\nnon_diegetic_music:\nNone."
            )
        dialogue = " Iris says <d>[English] The door was open when I got here.</d> Leo listens silently." if "Iris" in text else ""
        return ("integrated_multimodal_description:\n[Shot 1] The supplied scene continues."
                + dialogue + "\noverall_soundscape:\nRoom tone.\nnon_diegetic_music:\nNone.")


def checks(case):
    return {item["name"]: item["passed"] for item in case["checks"]}


def test_prompt_detail_workspace_normalization_is_unconditional_and_preserves_other_fields():
    case = suite.load_cases(FIXTURE / "cases.json")[0]
    normalized = []
    for detail in ("standard", "detailed"):
        arm_case = case.model_copy(deep=True)
        arm_case.workspace.setdefault("creativeControls", {})["promptDetail"] = detail
        workspace = arm_case.model_workspace(FIXTURE).model_dump(mode="json", by_alias=True)
        observed_detail = workspace["creativeControls"].pop("promptDetail", None)
        if observed_detail != detail:
            raise ValueError(f"Expected {detail!r}; observed {observed_detail!r}")
        normalized.append(workspace)
        assert "promptDetail" not in workspace["creativeControls"]
    assert normalized[0] == normalized[1]


def test_loading_cases_and_negative_controls():
    cases = suite.load_cases(FIXTURE / "cases.json")
    assert [case.id for case in cases] == [
        "neutral-visible-face", "exact-dialogue-listener", "implied-concern",
        "deliberate-stillness", "closed-eyes-rest", "two-visible-characters",
        "anime-awkwardness", "nonliving-mannequin",
        "anime-neutral-fixed-frame", "anime-deliberate-stillness-fixed-frame",
    ]
    for case in cases:
        workspace = case.model_workspace(FIXTURE)
        assert case.authoring_path == "one_shot" and case.story_authority is None
        assert case.shot_count == 1 and workspace.variations == 1
        assert workspace.creative_controls.prompt_detail == H3PromptDetail.STANDARD
    assert "eyes deliberately closed throughout" in cases[4].workspace["creativeRequest"]
    assert "deliberate stillness" in cases[3].workspace["creativeRequest"]
    assert "nonliving" in cases[7].workspace["creativeRequest"]
    assert "2D digital anime" in cases[6].workspace["creativeRequest"]
    assert not any(mark in cases[6].workspace["creativeRequest"] for mark in ("^_^", ">_<", "-_-"))
    neutral, stillness = cases[8], cases[9]
    for case in (neutral, stillness):
        workspace = case.model_workspace(FIXTURE)
        assert workspace.mode.value == "I2VA" and case.duration_seconds == 8 and case.shot_count == 1
        assert workspace.aspect_ratio == "16:9"
        assert len(workspace.assets) == 1 and workspace.assets[0].role == "first_frame_anchor"
        assert [item.path for item in case.fixture_assets] == ["assets/anime-fixed-first-frame.png"]
    assert neutral.workspace["creativeRequest"] == (
        "The character remains calmly present and attentive in the same setting. "
        "Keep the performance natural and understated.")
    assert stillness.workspace["creativeRequest"] == (
        "She remains very still, quietly holding her attention toward the same point in front of her. "
        "Preserve the same calm expression, head position, and gaze direction. "
        "No deliberate gesture or emotional change occurs.")
    assert not any(token in neutral.workspace["creativeRequest"].lower() for token in
                   ("blink", "refocus", "breath", "brow", "micro-expression", "smile", "sad", "concern", "nod", "turn", "gesture", "dialogue"))
    assert "blink" not in stillness.workspace["creativeRequest"].lower()


def test_experimental_policy_preserves_performance_state_and_render_review_questions():
    from miniconstruct.h3.natural_liveliness import NATURAL_LIVELINESS_POLICY
    assert suite.LIVELINESS_POLICY == NATURAL_LIVELINESS_POLICY
    assert "symbolic" not in suite.LIVELINESS_POLICY
    assert len(suite.load_cases(FIXTURE / "cases.json")) == 10
    assert "Did a blink reopen immediately?" in suite.REVIEW_QUESTIONS
    assert "Did gaze return to or remain on the same attention target?" in suite.REVIEW_QUESTIONS
    assert "Did Experimental create an unsupported emotional change?" in suite.REVIEW_QUESTIONS
    assert "Did deliberate stillness remain genuinely still?" in suite.REVIEW_QUESTIONS


@pytest.mark.asyncio
@pytest.mark.parametrize("reasoning", [ReasoningMode.OFF, ReasoningMode.ON])
async def test_four_arms_real_detail_path_exact_inputs_and_fixed_settings(reasoning):
    FakeClient.calls.clear()
    selected_profile = profile(vision=True).model_copy(update={"reasoning_mode": reasoning})
    result = await suite.run_suite(ROOT, selected_profile, FakeClient)
    assert len(FakeClient.calls) == 40
    for case in result["cases"]:
        assert list(case["variants"]) == list(suite.ARMS)
        assert all(checks(case).values()), (case["caseId"], checks(case))
        assert case["reviewQuestions"] == suite.REVIEW_QUESTIONS + suite.CASE_REVIEW_QUESTIONS.get(case["caseId"], [])
        for name, arm in case["variants"].items():
            detail, overlay = name.split("-")
            assert arm["promptDetail"] == detail and arm["livelinessOverlay"] == overlay
            system = arm["modelFacingMessages"][0]["content"]
            assert (suite.DETAIL_SYSTEM_SECTION in system) == (detail == "detailed")
            assert (suite.LIVELINESS_SYSTEM_SECTION in system) == (overlay == "experimental")
            assert harness.PERFORMANCE_SYSTEM_SECTION not in system
            assert arm["validation"]["valid"]
            assert system.count(suite.LIVELINESS_SYSTEM_SECTION) == (overlay == "experimental")
            assert arm["livelinessExperimentRevision"] == result["livelinessExperimentRevision"] == 2
            assert arm["livelinessTreatment"] == ("production" if overlay == "experimental" else "ablation_control")
    assert all(settings == FakeClient.calls[0][1] for _, settings in FakeClient.calls)
    assert result["settings"]["reasoningMode"] == reasoning.value
    assert result["settings"]["temperature"] == selected_profile.temperature
    assert "data:image" not in json.dumps(result)
    # Independently assemble ordinary production for each detail level.
    case = suite.load_cases(FIXTURE / "cases.json")[0]
    for detail in ("standard", "detailed"):
        workspace = case.model_workspace(FIXTURE)
        workspace.creative_controls.prompt_detail = H3PromptDetail(detail)
        ordinary = await generate_workspace_prompts(
            GenerationRequest(workspace=workspace, llm=selected_profile.to_settings(), resolvedSeeds=[1]), FakeClient)
        assert result["cases"][0]["variants"][f"{detail}-experimental"]["modelFacingMessages"] == ordinary.assembled.messages


@pytest.mark.asyncio
@pytest.mark.parametrize("vision", [False, True])
async def test_fixed_frame_i2va_cases_share_exact_picture_bytes_and_validate_all_arms(vision):
    selected = {"anime-neutral-fixed-frame", "anime-deliberate-stillness-fixed-frame"}
    result = await suite.run_suite(ROOT, profile(vision=vision), FakeClient, selected)
    fixture_bytes = (FIXTURE / "assets/anime-fixed-first-frame.png").read_bytes()
    fixture_sha = hashlib.sha256(fixture_bytes).hexdigest()
    assert len(result["cases"]) == 2
    for case in result["cases"]:
        assert case["mode"] == "I2VA" and case["durationSeconds"] == 8 and case["shotCount"] == 1
        assert list(case["variants"]) == list(suite.ARMS)
        assert checks(case)["picture_bytes_and_transmitted_image_inputs_match"]
        assert checks(case)["i2va_first_frame_picture_1_alignment_valid"]
        for arm in case["variants"].values():
            assert arm["pictureInputs"] == [{
                "h3Label": "Picture 1", "assetId": "anime-first-frame",
                "role": "first_frame_anchor", "authority": "required_first_frame",
                "fixturePath": "assets/anime-fixed-first-frame.png", "fixtureSource": "case",
                "fixtureSha256": fixture_sha,
            }]
            assert arm["modelInput"]["imageInputCount"] == (1 if vision else 0)
            assert arm["validation"]["valid"]
            assert arm["exactOutput"].startswith(
                "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.")
        assert case["variants"]["standard-control"]["modelInput"]["images"] == \
               case["variants"]["standard-experimental"]["modelInput"]["images"]
        assert case["variants"]["detailed-control"]["modelInput"]["images"] == \
               case["variants"]["detailed-experimental"]["modelInput"]["images"]


@pytest.mark.parametrize("section", [suite.LIVELINESS_SYSTEM_SECTION, suite.DETAIL_SYSTEM_SECTION])
def test_exact_isolation_rejects_extra_or_missing_sections_and_unrelated_changes(section):
    control = [{"role": "system", "content": "base\n\n## Response behavior\n\nDirect output."},
               {"role": "user", "content": [{"type": "text", "text": "authority"},
                                              {"type": "image_url", "image_url": {"url": "original"}}]}]
    changed = deepcopy(control)
    changed[0]["content"] = changed[0]["content"].replace("base", "base" + section)
    assert harness._overlay_isolation_passes(control, changed, section)
    assert not harness._overlay_isolation_passes(control, control, section)
    duplicate = deepcopy(changed)
    duplicate[0]["content"] += section
    assert not harness._overlay_isolation_passes(control, duplicate, section)
    for kind in ("system", "user", "image", "extra_message"):
        altered = deepcopy(changed)
        if kind == "system":
            altered[0]["content"] += "unrelated"
        elif kind == "user":
            altered[1]["content"][0]["text"] = "changed authority"
        elif kind == "image":
            altered[1]["content"][1]["image_url"]["url"] = "changed image"
        else:
            altered.append({"role": "user", "content": "extra"})
        assert not harness._overlay_isolation_passes(control, altered, section)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["overlay", "detail"])
async def test_fake_provider_unrelated_message_mutation_fails_isolation(change):
    class AlteredClient(FakeClient):
        async def generate(self, messages):
            section = suite.LIVELINESS_SYSTEM_SECTION if change == "overlay" else suite.DETAIL_SYSTEM_SECTION
            if section in messages[0]["content"]:
                messages[1]["content"] += "\nUNAPPROVED extra user authority"
                self.last_payload = {"messages": deepcopy(messages)}
            return await super().generate(messages)
    result = await suite.run_suite(ROOT, profile(vision=False), AlteredClient, {"neutral-visible-face"})
    observed = checks(result["cases"][0])
    key = ("standard.only_overlay_differs_in_model_messages" if change == "overlay"
           else "control.only_prompt_detail_policy_differs_in_model_messages")
    assert observed[key] is False


@pytest.mark.asyncio
async def test_wrong_dialogue_fails_in_every_arm():
    class WrongDialogueClient(FakeClient):
        async def generate(self, messages):
            return (await super().generate(messages)).replace("The door was open", "The door was already open")
    result = await suite.run_suite(ROOT, profile(vision=False), WrongDialogueClient, {"exact-dialogue-listener"})
    observed = checks(result["cases"][0])
    for detail in ("standard", "detailed"):
        assert observed[f"{detail}.dedicated_exact_dialogue_authority"]
        assert not observed[f"{detail}.exact_dialogue_preserved_in_output"]
    assert all(not arm["validation"]["valid"] for arm in result["cases"][0]["variants"].values())


@pytest.mark.asyncio
async def test_four_manual_render_packs_have_exact_prompt_references_and_identity(tmp_path):
    shutil.copytree(FIXTURE, tmp_path / suite.FIXTURE_NAME)
    result = await suite.run_suite(tmp_path, profile(vision=True), FakeClient)
    report = suite.write_run(tmp_path, result)
    assert len(list(report.parent.glob("*.manual-render.zip"))) == 40
    saved = json.loads((report.parent / "results.json").read_text(encoding="utf-8"))
    assert "data:image" not in json.dumps(saved)
    assert saved["experimentalPolicy"] == suite.LIVELINESS_POLICY
    for case in saved["cases"]:
        for name, arm in case["variants"].items():
            with zipfile.ZipFile(report.parent / arm["manualRenderBundle"]) as archive:
                assert archive.read("prompt.txt").decode() == arm["exactOutput"]
                manifest = json.loads(archive.read("manual-render-manifest.json"))
                assert manifest["variant"] == name
                assert manifest["promptDetail"] == manifest["creativeControls"]["promptDetail"] == arm["promptDetail"]
                assert manifest["livelinessOverlay"] == arm["livelinessOverlay"]
                assert manifest["livelinessExperimentRevision"] == 2
                assert manifest["livelinessTreatment"] == arm["livelinessTreatment"]
                assert manifest["profile"] == result["profile"]
                assert manifest["settings"] == result["settings"]
                assert manifest["mode"] == case["mode"]
                assert manifest["durationSeconds"] == 8 and manifest["shotCount"] == 1
                assert manifest["promptSha256"] == hashlib.sha256(archive.read("prompt.txt")).hexdigest()
                if case["caseId"] == "two-visible-characters":
                    assert manifest["references"][0]["h3Label"] == "Picture 1"
                    assert archive.read("references/picture-1.png") == (FIXTURE / "assets/neutral-subject-sheet.png").read_bytes()
                if case["caseId"] in {"anime-neutral-fixed-frame", "anime-deliberate-stillness-fixed-frame"}:
                    assert manifest["h3Mode"] == manifest["mode"] == "I2VA"
                    assert manifest["references"][0]["h3Label"] == "Picture 1"
                    assert manifest["references"][0]["role"] == "first_frame_anchor"
                    assert manifest["references"][0]["authority"] == "required_first_frame"
                    assert manifest["references"][0]["fixture"] == {
                        "source": "case", "path": "assets/anime-fixed-first-frame.png"}
                    assert archive.read(manifest["references"][0]["path"]) == \
                           (FIXTURE / "assets/anime-fixed-first-frame.png").read_bytes()
                    assert manifest["references"][0]["sha256"] == hashlib.sha256(
                        (FIXTURE / "assets/anime-fixed-first-frame.png").read_bytes()).hexdigest()
    assert all(name.title() in report.read_text(encoding="utf-8") for name in suite.ARMS)


@pytest.mark.asyncio
async def test_vision_skip_and_invalid_selection():
    FakeClient.calls.clear()
    result = await suite.run_suite(ROOT, profile(vision=False), FakeClient, {"two-visible-characters"})
    assert result["cases"][0]["status"] == "skipped" and FakeClient.calls == []
    with pytest.raises(ValueError, match="Unknown or empty"):
        await suite.run_suite(ROOT, profile(), FakeClient, {"unknown"})


@pytest.mark.parametrize("failure", ["missing", "duplicate", "altered", "extra_system"])
def test_ablation_rejects_invalid_production_setup(failure):
    from miniconstruct.h3.builder import assemble_prompt
    from miniconstruct.models.workspace import Workspace
    messages = assemble_prompt(Workspace(), False).messages
    if failure == "missing":
        messages = suite.ablate_natural_liveliness(messages)
    elif failure == "duplicate":
        messages[0]["content"] += suite.LIVELINESS_SYSTEM_SECTION
    elif failure == "altered":
        messages[0]["content"] = messages[0]["content"].replace(suite.LIVELINESS_POLICY, suite.LIVELINESS_POLICY + " altered")
    else:
        messages.append({"role": "system", "content": suite.LIVELINESS_SYSTEM_SECTION.lstrip()})
    with pytest.raises(ValueError, match="exactly one canonical"):
        suite.ablate_natural_liveliness(messages)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "duplicate", "altered"])
async def test_invalid_assembly_stops_benchmark_before_provider(monkeypatch, failure):
    from miniconstruct import generation
    original_assemble = generation.assemble_prompt

    def invalid_assemble(*args, **kwargs):
        assembled = original_assemble(*args, **kwargs)
        if failure == "missing":
            assembled.messages[0]["content"] = suite.ablate_natural_liveliness(assembled.messages)[0]["content"]
        elif failure == "duplicate":
            assembled.messages[0]["content"] += suite.LIVELINESS_SYSTEM_SECTION
        else:
            assembled.messages[0]["content"] = assembled.messages[0]["content"].replace(
                suite.LIVELINESS_POLICY, suite.LIVELINESS_POLICY + " altered")
        return assembled

    FakeClient.calls.clear()
    monkeypatch.setattr(generation, "assemble_prompt", invalid_assemble)
    with pytest.raises(ValueError, match="exactly one canonical"):
        await suite.run_suite(ROOT, profile(vision=False), FakeClient, {"neutral-visible-face"})
    assert FakeClient.calls == []


@pytest.mark.asyncio
async def test_historical_result_packaging_without_revision_metadata(tmp_path):
    shutil.copytree(FIXTURE, tmp_path / suite.FIXTURE_NAME)
    result = await suite.run_suite(tmp_path, profile(vision=False), FakeClient, {"neutral-visible-face"})
    result.pop("livelinessExperimentRevision")
    for case in result["cases"]:
        case.pop("livelinessExperimentRevision")
        for arm in case["variants"].values():
            arm.pop("livelinessExperimentRevision")
            arm.pop("livelinessTreatment")
    before = deepcopy(result)
    report = suite.write_run(tmp_path, result)
    saved = json.loads((report.parent / "results.json").read_text(encoding="utf-8"))
    assert "livelinessExperimentRevision" not in saved
    for name, arm in saved["cases"][0]["variants"].items():
        assert "livelinessTreatment" not in arm
        assert arm["modelFacingMessages"] == before["cases"][0]["variants"][name]["modelFacingMessages"]
