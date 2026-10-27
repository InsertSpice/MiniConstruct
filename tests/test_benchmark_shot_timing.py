from copy import deepcopy
import json
from pathlib import Path
import re
import zipfile

import pytest

from miniconstruct.benchmark.shot_timing import (
    APPROXIMATE_TOLERANCE_MS, REVIEW_QUESTIONS, SUITE_NAME, TIMING_POLICY,
    TIMING_SYSTEM_SECTION, load_cases, run_suite, timing_evidence, write_run,
)
from miniconstruct.benchmark.character_performance import _overlay_isolation_passes
from miniconstruct.benchmark.runner import load_suite
from miniconstruct.generation import generate_workspace_prompts
from miniconstruct.llm.client import LLMBackendError
from miniconstruct.llm.compatibility import build_generation_payload
from miniconstruct.models.api import GenerationRequest
from test_benchmark import profile

ROOT = Path(__file__).resolve().parents[1] / "benchmarks"
FIXTURE = ROOT / "shot-timing"


def prompt(starts, extra=""):
    body = "[Shot 1] A simple opening. " + extra
    for number, ms in enumerate(starts, 2):
        body += f"\n[Shot {number}] At {ms // 60000:02}:{ms // 1000 % 60:02}.{ms % 1000:03}, Action continues."
    return "integrated_multimodal_description:\n" + body + "\noverall_soundscape:\nQuiet.\nnon_diegetic_music:\nNone."


class FakeClient:
    calls = []
    output = prompt([2000, 4000, 6000])

    def __init__(self, settings):
        self.settings = settings
        self.last_payload = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def generate(self, messages, **kwargs):
        self.last_payload, _ = build_generation_payload(self.settings, messages, stream=False)
        type(self).calls.append(deepcopy(self.last_payload))
        return self.output

    async def repair(self, *_):
        raise AssertionError("Repair must never run")


def cases():
    return {case.id: case for case in load_cases(FIXTURE / "cases.json")}


def test_named_suite_catalog_and_broad_exclusion():
    loaded = cases()
    assert SUITE_NAME == "h3-shot-timing"
    assert list(loaded) == ["dialogue-reaction", "fast-escalation", "slow-atmosphere", "regular-rhythm", "explicit-timing"]
    assert {(case.duration_seconds, case.shot_count) for case in loaded.values()} == {(10, 4), (8, 3), (12, 4), (8, 4), (10, 3)}
    assert loaded["dialogue-reaction"].model_workspace(FIXTURE).dialogue
    for path in (ROOT / "suites").glob("*.json"):
        suite = load_suite(ROOT, path.stem)
        assert not set(loaded) & set(suite.cases)
        assert SUITE_NAME not in suite.cases
    for case in loaded.values():
        workspace = case.model_workspace(FIXTURE)
        assert workspace.mode.value == "T2VA" and not workspace.assets
        assert workspace.variations == 1


def test_implicit_pacing_fixtures_specify_ordered_scene_beats_without_telling_timing():
    loaded = cases()
    assert json.loads((FIXTURE / "cases.json").read_text(encoding="utf-8"))["fixtureRevision"] == 2
    count_words = {3: "three", 4: "four"}
    expected = {
        "dialogue-reaction": [
            "Shot 1 shows Mara and Eli beside a closed cupboard",
            "Shot 2 Mara says the supplied sentence",
            "Shot 3 Eli silently reacts and turns toward the cupboard",
            "Shot 4 Mara opens it and reveals a small kitten",
        ],
        "fast-escalation": [
            "Shot 1 shows a loose shopping trolley begin rolling",
            "Shot 2 a nearby worker notices it, runs several steps, catches the handle and stops it safely",
            "Shot 3 the worker sees that the eggs in the trolley are intact and reacts with relief",
        ],
        "slow-atmosphere": [
            "Shot 1 shows mist moving over the jetty and water",
            "Shot 2 shows a bead of dew detach from the railing and fall",
            "Shot 3 shows the resulting ripple expanding across the water",
            "Shot 4 shows the dawn horizon",
        ],
    }
    for case_id, beats in expected.items():
        case = loaded[case_id]
        request = case.model_workspace(FIXTURE).creative_request
        lower = request.casefold()
        assert f"exactly {count_words[case.shot_count]} shots" in lower
        assert all(lower.index(beat.casefold()) < lower.index(next_beat.casefold())
                   for beat, next_beat in zip(beats, beats[1:]))
        assert all(beat.casefold() in lower for beat in beats)
        # The event sequence is required above; these phrase and timestamp checks
        # separately guard against accidentally restoring explicit relative timing.
        assert not re.search(
            r"\b(?:brief(?:ly)?|longer|long|short|sustained|patient|hold|montage|"
            r"room\s+to\s+(?:read|breathe)|take\s+the\s+time|most\s+of\s+the\s+time)\b", lower)
        assert not re.search(r"\b(?:\d{1,2}:)?\d{1,2}:\d{2}(?:\.\d+)?\b", request)
        assert not re.search(r"\b(?:at|after|until|by)\s+\d+(?:\.\d+)?\s*(?:seconds?|s)\b", lower)
    dialogue = loaded["dialogue-reaction"].model_workspace(FIXTURE)
    assert "I think your missing visitor has been here all morning." in dialogue.dialogue
    for case in loaded.values():
        assert f"exactly {count_words[case.shot_count]} shots" in case.workspace["creativeRequest"].casefold()


def test_regular_rhythm_and_explicit_timing_guard_fixtures_are_retained():
    raw = json.loads((FIXTURE / "cases.json").read_text(encoding="utf-8"))
    loaded = {case["id"]: case for case in raw["cases"]}
    assert loaded["regular-rhythm"]["workspace"]["creativeRequest"] == (
        "Exactly four shots in a steady rhythmic sequence: a hand sets down a red cup, then a blue cup, "
        "then a green cup, then a yellow cup. Each shot contains the same simple placing action, "
        "comparable framing and a brief settled hold. Cut with the same recurring musical pulse; "
        "no action needs special emphasis or extra time. No dialogue.")
    assert loaded["explicit-timing"]["explicitCuts"] == [
        {"shot": 2, "startMs": 4000, "requirement": "start Shot 2 at 4 seconds"},
        {"shot": 3, "startMs": 6000, "requirement": "start Shot 3 at 6 seconds"},
    ]


@pytest.mark.parametrize("mutation", ["schema", "duplicate", "duration", "authority", "range", "vision", "variation"])
def test_fixture_validation_rejects_conflicting_contracts(tmp_path, mutation):
    raw = json.loads((FIXTURE / "cases.json").read_text())
    if mutation == "schema":
        raw["suite"] = "other-suite"
    elif mutation == "duplicate":
        raw["cases"].append(raw["cases"][0])
    elif mutation == "duration":
        raw["cases"][0]["workspace"]["durationSeconds"] = 8
    elif mutation == "authority":
        raw["cases"][-1]["explicitCuts"][0]["requirement"] = "unsupplied timing"
    elif mutation == "range":
        raw["cases"][-1]["explicitCuts"][0]["startMs"] = 11000
    elif mutation == "vision":
        raw["cases"][0]["workspace"]["mode"] = "Ref2VA"
    elif mutation == "variation":
        raw["cases"][0]["workspace"]["variations"] = 2
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        load_cases(path)


@pytest.mark.parametrize("starts,exact,approx", [
    ([2000, 4000, 6000], True, True),
    ([2100, 4100, 6100], False, True),
    ([2101, 4101, 6101], False, False),
    ([1000, 6000, 7000], False, False),
])
def test_equality_includes_final_hold_and_tolerance_boundary(starts, exact, approx):
    evidence = timing_evidence(prompt(starts), cases()["regular-rhythm"], True)
    assert evidence["emittedStartTimestampMs"] == [None, *starts]
    assert evidence["openingStartInferred"] is True
    assert evidence["shotStartsMs"] == [0, *starts]
    assert sum(evidence["derivedShotDurationsMs"]) == 8000
    assert evidence["exactlyEquallySpaced"] is exact
    assert evidence["approximatelyEquallySpaced"] is approx
    assert evidence["approximateToleranceMs"] == APPROXIMATE_TOLERANCE_MS == 100


def test_thirds_and_scope_masking():
    case = cases()["fast-escalation"]
    raw = prompt([2667, 5333], '<d>[English] [Shot 8] At 00:07.000, spoken text.</d> "[Shot 9]"')
    raw += "\n[Shot 10] At 00:07.000, music annotation."
    evidence = timing_evidence(raw, case, True)
    assert evidence["observedShotNumbers"] == [1, 2, 3]
    assert evidence["derivedShotDurationsMs"] == [2667, 2666, 2667]
    assert evidence["exactlyEquallySpaced"] is False
    assert evidence["approximatelyEquallySpaced"] is True


@pytest.mark.parametrize("raw", [
    "No H3 fields", prompt([2000]), prompt([4000, 2000, 6000]), prompt([2000, 4000, 8000]),
    prompt([2000, 4000, 6000]).replace("[Shot 1]", "[Shot 1] At 00:00.001,"),
    prompt([2000, 4000, 6000]).replace("At 00:04.000,", ""),
])
def test_invalid_sequences_do_not_get_numeric_conclusions(raw):
    evidence = timing_evidence(raw, cases()["regular-rhythm"], False)
    assert evidence["eligibleForTimingDescription"] is False
    assert evidence["derivedShotDurationsMs"] is None
    assert evidence["exactlyEquallySpaced"] is None
    assert evidence["approximatelyEquallySpaced"] is None


def test_explicit_timestamp_evidence_is_exact_and_event_review_is_unanswered():
    case = cases()["explicit-timing"]
    evidence = timing_evidence(prompt([4000, 6000]), case, True)
    assert evidence["explicitTimestampAuthorityPreserved"] is True
    assert [cut["observedStartMs"] for cut in evidence["explicitTiming"]] == [4000, 6000]
    assert all(cut["eventAtCutHumanReview"] == "" for cut in evidence["explicitTiming"])
    assert timing_evidence(prompt([4001, 6000]), case, True)["explicitTimestampAuthorityPreserved"] is False
    assert timing_evidence(prompt([4000, 6000]), case, False)["explicitTimestampAuthorityPreserved"] is None
    assert timing_evidence(prompt([2000, 4000, 6000]), cases()["regular-rhythm"], True)["explicitTiming"] == []


@pytest.mark.asyncio
async def test_pair_is_ordinary_production_plus_one_section_with_all_settings_matched():
    FakeClient.calls.clear()
    selected_profile = profile(vision=False)
    result = await run_suite(ROOT, selected_profile, FakeClient, {"regular-rhythm"})
    assert len(FakeClient.calls) == 2
    case = result["cases"][0]
    assert case["matchedRequestIsolation"] is True
    control, experimental = (case["variants"][name] for name in ("control", "experimental"))
    assert TIMING_POLICY not in control["modelFacingMessages"][0]["content"]
    assert experimental["modelFacingMessages"][0]["content"].count(TIMING_SYSTEM_SECTION) == 1
    assert _overlay_isolation_passes(control["modelFacingMessages"], experimental["modelFacingMessages"], TIMING_SYSTEM_SECTION)
    assert control["effectiveRequest"] == experimental["effectiveRequest"]
    assert control["seedProvenance"]["status"] == "accepted_by_provider"
    assert control["structuralEligibility"] is True
    assert case["reviewQuestions"] == REVIEW_QUESTIONS and case["humanReview"]["notes"] == ""
    ordinary = await generate_workspace_prompts(GenerationRequest(
        workspace=cases()["regular-rhythm"].model_workspace(FIXTURE),
        llm=selected_profile.to_settings(), resolvedSeeds=[1]), FakeClient)
    assert control["modelFacingMessages"] == ordinary.variations[0].model_payload["messages"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["user", "tokens"])
async def test_unrelated_request_difference_fails_isolation(change):
    class ChangedClient(FakeClient):
        async def generate(self, messages, **kwargs):
            output = await super().generate(messages)
            if TIMING_SYSTEM_SECTION in messages[0]["content"]:
                if change == "user":
                    self.last_payload["messages"][1]["content"] += " changed authority"
                else:
                    self.last_payload["max_tokens"] += 1
            return output
    result = await run_suite(ROOT, profile(vision=False), ChangedClient, {"regular-rhythm"})
    assert result["cases"][0]["matchedRequestIsolation"] is False


@pytest.mark.asyncio
async def test_invalid_output_is_retained_without_repair_or_regeneration():
    class InvalidClient(FakeClient):
        calls = []
        output = "untouched invalid response"
    result = await run_suite(ROOT, profile(vision=False), InvalidClient)
    assert len(InvalidClient.calls) == 10
    assert len(result["cases"]) == 5
    for case in result["cases"]:
        for arm in case["variants"].values():
            assert arm["providerRequestSuccess"] is True and arm["structuralEligibility"] is False
            assert arm["exactOutput"] == InvalidClient.output
            assert arm["validation"]["findings"]
            assert arm["timingEvidence"]["exactShotCountCompliance"] is False
            assert arm["timingEvidence"]["exactlyEquallySpaced"] is None


@pytest.mark.asyncio
async def test_provider_failure_keeps_other_arm_and_safe_request_evidence(tmp_path):
    class FailingClient(FakeClient):
        calls = []
        async def generate(self, messages, **kwargs):
            output = await super().generate(messages)
            if TIMING_SYSTEM_SECTION not in messages[0]["content"]:
                raise LLMBackendError("SECRET should never appear", category="provider_error")
            return output
    result = await run_suite(ROOT, profile(vision=False), FailingClient, {"regular-rhythm"})
    assert len(FailingClient.calls) == 2
    case = result["cases"][0]
    assert case["variants"]["control"]["providerRequestSuccess"] is False
    assert case["variants"]["control"]["structuralEligibility"] is None
    assert case["variants"]["experimental"]["providerRequestSuccess"] is True
    assert case["matchedRequestIsolation"] is True
    report = write_run(tmp_path, result)
    assert "SECRET" not in (report.parent / "results.json").read_text(encoding="utf-8")
    assert len(list(report.parent.glob("*.zip"))) == 1


@pytest.mark.asyncio
async def test_small_matched_repeats_and_self_contained_artifacts(tmp_path):
    FakeClient.calls.clear()
    result = await run_suite(ROOT, profile(vision=False), FakeClient, {"regular-rhythm"}, repeats=3)
    assert len(FakeClient.calls) == 6
    assert [call["seed"] for call in FakeClient.calls] == [1, 1, 2, 2, 3, 3]
    assert all(case["matchedRequestIsolation"] for case in result["cases"])
    report = write_run(tmp_path, result)
    saved = json.loads((report.parent / "results.json").read_text(encoding="utf-8"))
    assert saved["suite"] == SUITE_NAME and saved["experimentalPolicy"] == TIMING_POLICY
    assert len(list(report.parent.glob("*.zip"))) == 6
    assert all(question in report.read_text(encoding="utf-8") for question in REVIEW_QUESTIONS)
    assert "data:image" not in json.dumps(saved)
    for case in saved["cases"]:
        for arm in case["variants"].values():
            with zipfile.ZipFile(report.parent / arm["manualRenderBundle"]) as archive:
                assert sorted(archive.namelist()) == ["manual-render-manifest.json", "prompt.txt", "review.md"]
                assert archive.read("prompt.txt").decode() == arm["exactOutput"]
                manifest = json.loads(archive.read("manual-render-manifest.json"))
                assert manifest["writerSeed"] == case["repeatSeed"]
                assert manifest["rendererSeed"] is None and manifest["references"] == []
                assert manifest["structuralEligibility"] is True
                assert manifest["profile"] == saved["profile"]
                assert manifest["reviewQuestions"] == REVIEW_QUESTIONS
    with pytest.raises(ValueError):
        await run_suite(ROOT, profile(vision=False), FakeClient, repeats=4)
    with pytest.raises(ValueError):
        await run_suite(ROOT, profile(vision=False), FakeClient, {"unknown-case"})
