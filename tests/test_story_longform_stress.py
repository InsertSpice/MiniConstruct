"""Offline full-scenario coverage for the opt-in stateful Story stress benchmark."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from miniconstruct.benchmark.runner import BenchmarkRunner, load_suite, write_run
from miniconstruct.llm.client import LLMBackendError


ROOT = Path(__file__).resolve().parents[1] / "benchmarks"
KEY = "11111111-1111-4111-8111-111111111111"
BRIDGE = "22222222-2222-4222-8222-222222222222"
LEDGER = "33333333-3333-4333-8333-333333333333"


def _context(messages):
    content = messages[1]["content"]
    return json.loads(content[0]["text"] if isinstance(content, list) else content)


class OfflineStressClient:
    """Deterministic provider that exercises production parsing and Apply paths."""
    calls: list[dict] = []
    fail_call: int | None = None
    empty_response_call: int | None = None
    delay_bridge_update: bool = False

    def __init__(self, settings):
        self.settings = settings
        self.last_usage = {"prompt_tokens": 100 + len(type(self).calls), "completion_tokens": 20,
                           "total_tokens": 120 + len(type(self).calls)}
        self.last_payload = None
        self.compatibility_fallback = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def generate(self, messages, *, response_instruction=None):
        assert response_instruction is None
        context = _context(messages)
        call = len(type(self).calls)
        type(self).calls.append(context)
        if type(self).fail_call == call:
            raise LLMBackendError("deliberate offline provider failure", category="provider_error")
        if type(self).empty_response_call == call:
            self.last_usage = {"prompt_tokens": 321, "completion_tokens": 0, "total_tokens": 321}
            return ""
        if "targetGenerationIds" in context:
            return json.dumps({"items": [{
                "existingGenerationId": item["generationId"],
                "scenePlan": f"Replanned collapsed-bridge detour beat {index + 1}.",
                "dialogue": "", "incomingState": item["incomingState"],
                "intendedOutgoingState": item["intendedOutgoingState"],
                "relationshipToPrevious": item["relationshipToPrevious"],
                "cameraIntent": item["cameraIntent"], "shotCount": 1,
                "referenceRecommendations": [], "memoryPatchAfter": {},
            } for index, item in enumerate(context["targetRange"])], "warnings": []})
        if "upstreamGenerationId" in context:
            return json.dumps({"slots": [{
                "planItemId": slot_id, "scenePlan": "Alice pauses at the open Old Stone Bridge before continuing.",
                "dialogue": "", "incomingState": "Alice approaches the open bridge.",
                "intendedOutgoingState": "Alice is ready for the protected next beat.",
                "relationshipToPrevious": "new_shot_preserve_continuity", "cameraIntent": "Medium tracking shot",
                "shotCount": 1,
                "referenceRecommendations": [], "memoryPatchAfter": {},
            } for slot_id in context["slotIds"]], "warnings": []})

        phase = sum("anchorGenerationId" in seen for seen in type(self).calls) - 1
        slots = [{
            "planItemId": slot_id, "scenePlan": f"Phase {phase + 1} travel beat {index + 1}.",
            "dialogue": "", "incomingState": "Alice continues the route.",
            "intendedOutgoingState": "Alice reaches the next waypoint.",
            "relationshipToPrevious": "new_shot_preserve_continuity", "cameraIntent": "Grounded wide shot",
            "shotCount": 1,
            "referenceRecommendations": [], "memoryPatchAfter": {},
        } for index, slot_id in enumerate(context["slotIds"])]
        if phase == 0:
            slots[0]["scenePlan"] = "Alice travels through the riverside district toward Old Stone Bridge."
            slots[-1]["intendedOutgoingState"] = "Alice approaches Old Stone Bridge without crossing it."
        elif phase == 1:
            slots[-1]["scenePlan"] = "Alice reaches the approach to the intact, open Old Stone Bridge."
            slots[-1]["intendedOutgoingState"] = "Alice waits at the bridge approach with the sealed ledger undelivered."
        elif phase == 2:
            bridge_slot = 2 if type(self).delay_bridge_update else 0
            slots[bridge_slot]["scenePlan"] = "Alice learns the Old Stone Bridge has collapsed and takes an alternate route."
            slots[bridge_slot]["memoryPatchAfter"] = {"factUpserts": [{
                "id": BRIDGE, "text": "Old Stone Bridge has collapsed and is impassable.", "relatedSubjectIds": ["alice"],
            }]}
        elif phase == 3:
            slots[-1]["scenePlan"] = "Alice follows the alternate route toward the archive with the sealed ledger still undelivered."
        elif phase == 4:
            slots[0]["scenePlan"] = "Alice reaches the archive district by the alternate route."
            slots[1]["scenePlan"] = "Alice finds the Archivist while carrying the sealed ledger."
            slots[-1]["scenePlan"] = "Alice delivers the sealed ledger to the archivist before midnight."
            slots[-1]["memoryPatchAfter"] = {"resolvedThreadIds": [LEDGER]}
        elif phase == 5:
            slots[-1]["scenePlan"] = "Carol returns at the archive gate after her long absence."
            candidate = next(item for item in context["historicalReferenceContext"] if "carol" in item["subjectIds"])
            slots[-1]["referenceRecommendations"] = [{
                "sourceGenerationId": candidate["sourceGenerationId"], "assetId": candidate["asset"]["id"],
                "recommendation": "Keep Carol visually consistent with her earlier appearance.",
            }]
        return json.dumps({"slots": slots, "warnings": []})


def _profile():
    from test_benchmark_planner import profile
    return profile(vision=True)


@pytest.mark.asyncio
async def test_story_longform_stress_runs_full_production_path_and_writes_phase_artifacts(tmp_path):
    OfflineStressClient.calls.clear()
    OfflineStressClient.fail_call = None
    OfflineStressClient.empty_response_call = None
    OfflineStressClient.delay_bridge_update = False
    result = await BenchmarkRunner(ROOT, OfflineStressClient).run(
        _profile(), load_suite(ROOT, "story-longform-stress").cases, "story-longform-stress",
    )
    case = result["cases"][0]

    assert case["status"] == "completed", case.get("error")
    assert case["structuralValid"] is True
    assert case["finalCanonicalGenerationCount"] == 22
    assert case["completedPhases"] == ["Phase A", "Phase B", "Phase C", "Phase D", "Phase E", "Phase F", "Replan", "Insert"]
    assert len(case["phaseEvidence"]) == len(OfflineStressClient.calls) == 8
    assert case["machineChecks"]["passed"], case["machineChecks"]
    assert case["initialState"]["canonicalGenerationCount"] == 3
    assert case["initialState"]["trackedMemoryIds"] == {"key": KEY, "bridge": BRIDGE, "ledger": LEDGER}

    phase = {item["phase"]: item for item in case["phaseEvidence"]}
    assert phase["Phase C"]["machineChecks"][1]["passed"]
    assert phase["Phase C"]["machineChecks"][2]["id"] == "bridge-first-beat-transition"
    assert phase["Phase C"]["machineChecks"][2]["passed"]
    assert next(check for check in phase["Phase A"]["machineChecks"] if check["id"] == "early-milestone-guard")["passed"]
    assert next(check for check in phase["Phase B"]["machineChecks"] if check["id"] == "early-milestone-guard")["passed"]
    assert next(check for check in phase["Phase D"]["machineChecks"] if check["id"] == "phase-d-delivery-prose-guard")["passed"]
    assert phase["Phase E"]["machineChecks"][-1]["evidence"]["presentAfterSlots"] == [True, True, False]
    assert phase["Phase F"]["carolReturnReference"]["historicalCandidateOffered"]
    assert phase["Phase F"]["carolReturnReference"]["pixelsTransmitted"] == 1
    assert phase["Phase F"]["carolReturnReference"]["recommended"] is True
    assert phase["Phase E"]["carolLatePreReturn"] == {"historicalCandidateOffered": False, "pixelsTransmitted": 0}
    assert phase["Replan"]["canonicalGenerationCountBefore"] == phase["Replan"]["canonicalGenerationCountAfter"] == 21
    assert next(check for check in phase["Replan"]["machineChecks"] if check["id"] == "replan-trust-transitions")["passed"]
    assert phase["Insert"]["canonicalGenerationCountBefore"] == 21
    assert phase["Insert"]["canonicalGenerationCountAfter"] == 22
    assert next(check for check in phase["Insert"]["machineChecks"] if check["id"] == "insert-trust-transitions")["passed"]
    assert case["memoryHygieneAggregates"]["factsSurvivingToFinalState"] >= 2
    assert case["memoryHygieneAggregates"]["threadsSurvivingToFinalState"] == 0
    assert all(item["promptTokens"] is not None for item in case["stressSummary"]["phases"])
    assert "data:image" not in json.dumps(case)

    written = write_run(tmp_path, result)
    artifact = json.loads((written / "cases" / "story-longform-stress.json").read_text(encoding="utf-8"))
    summary = (written / "summary.md").read_text(encoding="utf-8")
    assert artifact["finalCanonicalGenerationCount"] == 22
    assert "Stateful Story stress summary" in summary
    assert "| Phase F | REVIEW |" in summary


@pytest.mark.asyncio
async def test_story_longform_stress_keeps_completed_phase_artifacts_on_provider_failure(tmp_path):
    OfflineStressClient.calls.clear()
    OfflineStressClient.fail_call = 2
    OfflineStressClient.empty_response_call = None
    OfflineStressClient.delay_bridge_update = False
    result = await BenchmarkRunner(ROOT, OfflineStressClient).run(
        _profile(), ["story-longform-stress"], "story-longform-stress",
    )
    case = result["cases"][0]
    OfflineStressClient.fail_call = None

    assert case["status"] == "error"
    assert case["failedPhase"] == "Phase C"
    assert case["completedPhases"] == ["Phase A", "Phase B"]
    assert case["lastCanonicalGenerationCount"] == 9
    assert len(case["phaseEvidence"]) == 2
    assert case["error"]["type"] == "provider_error"
    written = write_run(tmp_path, result)
    artifact = json.loads((written / "cases" / "story-longform-stress.json").read_text(encoding="utf-8"))
    assert artifact["completedPhases"] == ["Phase A", "Phase B"]
    assert artifact["failedPhase"] == "Phase C"


@pytest.mark.asyncio
async def test_story_longform_stress_retains_empty_response_failed_phase_diagnostics():
    OfflineStressClient.calls.clear()
    OfflineStressClient.fail_call = None
    OfflineStressClient.empty_response_call = 2
    OfflineStressClient.delay_bridge_update = False
    result = await BenchmarkRunner(ROOT, OfflineStressClient).run(
        _profile(), ["story-longform-stress"], "story-longform-stress",
    )
    OfflineStressClient.empty_response_call = None
    case = result["cases"][0]

    assert case["status"] == "error"
    assert case["failedPhase"] == "Phase C"
    assert case["completedPhases"] == ["Phase A", "Phase B"]
    assert case["lastCanonicalGenerationCount"] == 9
    failed = case["failedPhaseEvidence"]
    assert failed["phase"] == "Phase C"
    assert failed["operation"] == "next"
    assert failed["canonicalGenerationCountBefore"] == 9
    assert failed["rawModelOutput"] == ""
    assert failed["provider"]["usage"] == {"prompt_tokens": 321, "completion_tokens": 0, "total_tokens": 321}
    assert failed["provider"]["requestDurationMs"] is not None
    assert failed["effectiveRequest"]["model"]
    assert failed["requestedReasoningMode"] == "on"
    assert failed["modelInput"]["user"]
    assert failed["error"]["category"] == "story_json"


@pytest.mark.asyncio
async def test_story_longform_stress_delayed_bridge_update_fails_first_beat_check():
    OfflineStressClient.calls.clear()
    OfflineStressClient.fail_call = None
    OfflineStressClient.empty_response_call = None
    OfflineStressClient.delay_bridge_update = True
    result = await BenchmarkRunner(ROOT, OfflineStressClient).run(
        _profile(), ["story-longform-stress"], "story-longform-stress",
    )
    OfflineStressClient.delay_bridge_update = False
    case = result["cases"][0]
    phase_c = next(item for item in case["phaseEvidence"] if item["phase"] == "Phase C")
    timing = next(check for check in phase_c["machineChecks"] if check["id"] == "bridge-first-beat-transition")

    assert case["status"] == "completed"
    assert case["finalCanonicalGenerationCount"] == 22
    assert phase_c["status"] == "fail"
    assert timing["passed"] is False
    assert [slot["validCollapsedCurrentTruth"] for slot in timing["evidence"]["slotStates"]] == [False, False, True]
