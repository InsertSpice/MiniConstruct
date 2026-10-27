from __future__ import annotations

import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory

import pytest

from miniconstruct.benchmark.runner import BenchmarkCase, BenchmarkRunner, load_suite
from miniconstruct.benchmark.story import DeterministicStoryProjectStore, load_story_workspaces, materialize_story_project

from test_benchmark_planner import profile
from test_benchmark_story import OfflineStoryClient, PNG


ROOT = Path(__file__).resolve().parents[1] / "benchmarks"
CASE_IDS = [
    "story-character-profile-control",
    "story-character-profile-guided",
    "story-character-profile-current-state-authority",
    "story-character-profile-returning-subject",
    "story-character-profile-irrelevant-subject",
]


def _definition(case_id):
    return json.loads((ROOT / "cases" / case_id / "case.json").read_text())


def _strip_profiles(value):
    if isinstance(value, dict):
        return {key: _strip_profiles(item) for key, item in value.items() if key != "characterProfile"}
    if isinstance(value, list):
        return [_strip_profiles(item) for item in value]
    if isinstance(value, str):
        return re.sub(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            "<uuid>", value, flags=re.IGNORECASE,
        )
    return value


def _context(result):
    return json.JSONDecoder().raw_decode(result["modelInput"]["user"])[0]


def _external_source(tmp_path):
    root = tmp_path / "external"
    (root / "assets").mkdir(parents=True)
    (root / "assets" / "alice_station_identity.png").write_bytes(PNG)
    return root


def test_story_character_profile_suite_is_opt_in_and_pair_is_frozen():
    assert load_suite(ROOT, "story-character-profiles").cases == CASE_IDS
    assert not set(CASE_IDS) & set(load_suite(ROOT, "all").cases)
    assert not set(CASE_IDS) & set(load_suite(ROOT, "story").cases)

    control = BenchmarkCase.model_validate(_definition(CASE_IDS[0]))
    guided = BenchmarkCase.model_validate(_definition(CASE_IDS[1]))
    control_story = control.story.model_dump(mode="json", by_alias=True)
    guided_story = guided.story.model_dump(mode="json", by_alias=True)
    assert _strip_profiles(control_story) == _strip_profiles(guided_story)
    assert control_story["narrativeSubjects"][0]["characterProfile"] == {
        "roleBackground": "", "personality": "", "appearance": "", "speechMannerisms": "", "notes": "",
    }
    assert guided_story["narrativeSubjects"][0]["characterProfile"]["roleBackground"].startswith("Senior conservator")
    assert control.story.next.requested_count == guided.story.next.requested_count == 2
    assert (ROOT / "cases" / CASE_IDS[0] / "workspace.json").read_bytes() == (
        ROOT / "cases" / CASE_IDS[1] / "workspace.json"
    ).read_bytes()

    with TemporaryDirectory(prefix="miniconstruct-story-profile-pair-") as first_root, TemporaryDirectory(
        prefix="miniconstruct-story-profile-pair-"
    ) as second_root:
        snapshots = []
        for case, temporary in zip((control, guided), (first_root, second_root)):
            loaded = load_story_workspaces(case, ROOT / "cases" / case.id, None)
            snapshots.append(materialize_story_project(DeterministicStoryProjectStore(Path(temporary), case.id), case, loaded))
        normalized_snapshots = []
        for snapshot in snapshots:
            manifest = snapshot.manifest.model_dump(mode="json", by_alias=True)
            manifest.pop("projectId", None)
            manifest.pop("createdAt", None)
            manifest.pop("updatedAt", None)
            manifest["sequence"].pop("activeGenerationId", None)
            for generation in manifest["sequence"]["generations"]:
                generation.pop("generationId", None)
                generation.pop("createdAt", None)
                generation.pop("updatedAt", None)
            generation_subjects = manifest["sequence"].get("subjects", [])
            for subject in generation_subjects:
                subject.pop("characterProfile", None)
            normalized_snapshots.append(manifest)
        assert normalized_snapshots[0] == normalized_snapshots[1]


@pytest.mark.asyncio
async def test_story_character_profile_suite_records_profile_memory_and_picture_evidence(tmp_path):
    source_root = _external_source(tmp_path)
    clients = [OfflineStoryClient() for _ in CASE_IDS]
    result = await BenchmarkRunner(ROOT, clients[0], source_root).run(
        profile(vision=True), CASE_IDS, "story-character-profiles"
    )
    assert len(result["cases"]) == len(CASE_IDS)
    assert all(item["status"] == "completed" for item in result["cases"])
    assert all(item["structuralValid"] and item["machineChecks"]["passed"] for item in result["cases"])

    # The runner accepts one client factory, so this suite proves the complete
    # five-case path with the same deterministic provider-free client.
    assert len(clients[0].calls) == len(CASE_IDS)
    by_id = {item["caseId"]: item for item in result["cases"]}
    contexts = {case_id: _context(item) for case_id, item in by_id.items()}

    control, guided = contexts[CASE_IDS[0]], contexts[CASE_IDS[1]]
    assert _strip_profiles(control) == _strip_profiles(guided)
    assert "characterProfile" not in control["sequenceNarrativeCast"][0]
    assert guided["sequenceNarrativeCast"][0]["characterProfile"]["personality"].startswith("Calm, methodical")
    assert "Character Profiles are stable author-provided Subject guidance" not in by_id[CASE_IDS[0]]["modelInput"]["system"]
    assert "Character Profiles are stable author-provided Subject guidance" in by_id[CASE_IDS[1]]["modelInput"]["system"]

    current = by_id[CASE_IDS[2]]
    assert current["storyMemoryBefore"]["durableFacts"][0]["text"] == "Alice fractured her ankle and currently uses crutches."
    assert current["storyMetrics"]["sourcePictureInputCount"] == 0
    current_case = _definition(CASE_IDS[2])
    current_profile_notes = current_case["story"]["narrativeSubjects"][0]["characterProfile"]["notes"]
    current_request = current_case["story"]["generations"][0]["scenePlan"]
    current_instruction = current_case["story"]["next"]["instruction"]
    assert current_profile_notes == "Alice normally walks unaided."
    assert "current plot state" not in current_profile_notes
    assert "crutch" not in current_request.casefold() and "injur" not in current_request.casefold()
    assert "crutch" not in current_instruction.casefold() and "injur" not in current_instruction.casefold()
    current_context = contexts[CASE_IDS[2]]
    assert "fractured her ankle" in json.dumps(current_context["storyMemory"])
    assert "uses crutches" in json.dumps(current_context["storyMemory"])
    assert contexts[CASE_IDS[2]]["sequenceNarrativeCast"][0]["characterProfile"]["notes"] == current_profile_notes
    assert "crutch" not in json.dumps(current_context["localContext"]).casefold()
    assert "injur" not in json.dumps(current_context["localContext"]).casefold()
    current_checks = {check["id"]: check for check in current["behavioralChecks"]}
    assert current_checks["current-injury-memory-transmitted"]["status"] == "pass"
    assert current_checks["current-crutches-memory-transmitted"]["status"] == "pass"

    returning = by_id[CASE_IDS[3]]
    returning_refs = returning["referenceMetrics"]
    assert {item["subjectId"] for item in returning_refs["relevantSubjects"]} == {"alice"}
    assert returning_refs["historicalReferenceSemanticCount"] == 1
    assert returning_refs["historicalReferenceCandidates"][0]["sourceGenerationOrdinal"] == 1
    assert returning_refs["historicalReferenceCandidates"][0]["assetId"] == "alice-identity"
    historical_pair = (
        returning_refs["historicalReferenceCandidates"][0]["sourceGenerationId"],
        returning_refs["historicalReferenceCandidates"][0]["assetId"],
    )
    assert historical_pair in {
        (item["sourceGenerationId"], item["assetId"])
        for item in returning_refs["allowedReferencePairs"]
    }
    assert contexts[CASE_IDS[3]]["sequenceNarrativeCast"][0]["characterProfile"]["roleBackground"].startswith("Senior conservator")
    assert returning["storyMetrics"]["sourcePictureInputCount"] == 1
    assert returning["modelInput"]["images"][0]["assetId"] == "alice-identity"
    assert returning["modelInput"]["images"][0]["fixturePath"] == "assets/alice_station_identity.png"
    assert returning["modelInput"]["images"][0]["fixtureSource"] == "external"
    assert {check["id"]: check for check in returning["behavioralChecks"]}["alice-historical-identity-available"]["status"] == "pass"

    irrelevant = by_id[CASE_IDS[4]]
    irrelevant_refs = irrelevant["referenceMetrics"]
    assert {item["id"] for item in irrelevant_refs["sequenceSubjects"]} == {"alice", "bob"}
    assert {item["subjectId"] for item in irrelevant_refs["relevantSubjects"]} == {"alice"}
    assert all("characterProfile" not in item for item in irrelevant_refs["relevantSubjects"])
    assert irrelevant_refs["historicalReferenceCandidates"][0]["subjectIds"] == ["alice"]
    assert irrelevant["modelInput"]["images"][0]["assetId"] == "alice-identity"
    assert "Character Profiles are stable author-provided Subject guidance" not in irrelevant["modelInput"]["system"]
    irrelevant_checks = {check["id"]: check for check in irrelevant["behavioralChecks"]}
    assert irrelevant_checks["alice-historical-identity-available"]["status"] == "pass"
    assert irrelevant_checks["bob-historical-identity-excluded"]["status"] == "pass"


def test_character_profile_cases_do_not_commit_media_payloads():
    for case_id in CASE_IDS:
        for path in (ROOT / "cases" / case_id).glob("*.json"):
            assert "data:image" not in path.read_text()
