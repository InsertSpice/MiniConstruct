from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory

import pytest

from miniconstruct.benchmark.runner import BenchmarkCase, BenchmarkRunner, load_case, load_suite
from miniconstruct.benchmark.story import DeterministicStoryProjectStore, load_story_workspaces, materialize_story_project

from test_benchmark_planner import profile
from test_benchmark_story import OfflineStoryClient


ROOT = Path(__file__).resolve().parents[1] / "benchmarks"
CASE_IDS = ["story-creative-direction-control", "story-creative-direction-guided"]
GUIDED_DIRECTION = {
    "toneMood": "Quietly ominous and grounded. Unease should build gradually rather than becoming overt horror immediately.",
    "performanceEnergy": "Natural, restrained reactions. Characters notice unsettling details subtly; avoid melodramatic fear or exaggerated reactions.",
    "pacingRhythm": "Patient slow-burn. Allow silence and observation early, then progressively shorten and intensify later beats.",
    "beatDurationDensity": "One meaningful narrative development per Generation. Let reactions and atmosphere breathe rather than packing several major actions into one beat.",
}


def _context(result):
    return json.JSONDecoder().raw_decode(result["modelInput"]["user"])[0]


def _normalized_without_direction(value):
    """Compare request structure while ignoring case-specific UUID identities."""
    uuid_pattern = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", re.I)

    def normalize(item):
        if isinstance(item, dict):
            return {
                key: normalize(value)
                for key, value in item.items()
                if key != "storyCreativeDirection"
            }
        if isinstance(item, list):
            return [normalize(value) for value in item]
        if isinstance(item, str):
            return uuid_pattern.sub("<uuid>", item)
        return item

    return normalize(deepcopy(value))


def _normalized_snapshot(snapshot):
    manifest = snapshot.manifest.model_dump(mode="json", by_alias=True)
    manifest.pop("projectId", None)
    manifest.pop("createdAt", None)
    manifest.pop("updatedAt", None)
    manifest.pop("storyCreativeDirection", None)
    sequence = manifest["sequence"]
    sequence.pop("activeGenerationId", None)
    for generation in sequence["generations"]:
        generation.pop("generationId", None)
        generation.pop("createdAt", None)
        generation.pop("updatedAt", None)
    return manifest


def test_story_creative_direction_fixture_pair_is_identical_except_direction():
    assert load_suite(ROOT, "story-creative-direction").cases == CASE_IDS
    definitions = [
        BenchmarkCase.model_validate_json((ROOT / "cases" / case_id / "case.json").read_text())
        for case_id in CASE_IDS
    ]
    control, guided = definitions

    assert control.story.story_creative_direction.model_dump(by_alias=True) == {
        "toneMood": "", "performanceEnergy": "", "pacingRhythm": "", "beatDurationDensity": "",
    }
    assert guided.story.story_creative_direction.model_dump(by_alias=True) == GUIDED_DIRECTION
    assert all(case.story.next.requested_count == 3 for case in definitions)
    assert control.story.model_dump(mode="json", by_alias=True, exclude={"story_creative_direction"}) == guided.story.model_dump(
        mode="json", by_alias=True, exclude={"story_creative_direction"}
    )

    with TemporaryDirectory(prefix="miniconstruct-story-direction-fixture-test-") as first_root, TemporaryDirectory(
        prefix="miniconstruct-story-direction-fixture-test-"
    ) as second_root:
        snapshots = []
        for case, temporary in zip(definitions, (first_root, second_root)):
            case_dir = ROOT / "cases" / case.id
            loaded = load_story_workspaces(case, case_dir, None)
            assert len(loaded) == 1
            snapshots.append(materialize_story_project(DeterministicStoryProjectStore(Path(temporary), case.id), case, loaded))
        assert _normalized_snapshot(snapshots[0]) == _normalized_snapshot(snapshots[1])


@pytest.mark.asyncio
async def test_story_creative_direction_cases_have_offline_comparable_plan_next_requests():
    clients = [OfflineStoryClient(), OfflineStoryClient()]
    results = [
        (await BenchmarkRunner(ROOT, client).run(profile(), [case_id]))["cases"][0]
        for client, case_id in zip(clients, CASE_IDS)
    ]

    assert all(result["status"] == "completed" for result in results)
    assert all(result["machineChecks"]["passed"] for result in results)
    assert all(result["storyMetrics"]["operation"] == "next" for result in results)
    assert all(result["storyMetrics"]["requestedNewGenerationCount"] == 3 for result in results)
    assert all(result["storyMetrics"]["responseSlotCount"] == 3 for result in results)
    assert all(result["storyMetrics"]["sourcePictureInputCount"] == 0 for result in results)
    assert all(result["modelInput"]["images"] == [] for result in results)

    contexts = [_context(result) for result in results]
    assert contexts[0]["storyCreativeDirection"] != contexts[1]["storyCreativeDirection"]
    assert contexts[0]["storyCreativeDirection"] == {
        "toneMood": "", "performanceEnergy": "", "pacingRhythm": "", "beatDurationDensity": "",
    }
    assert contexts[1]["storyCreativeDirection"] == GUIDED_DIRECTION
    assert all(context["requestedCount"] == 3 for context in contexts)
    assert _normalized_without_direction(contexts[0]) == _normalized_without_direction(contexts[1])
    assert all(result["effectiveRequest"]["stream"] is False for result in results)

    # The injected provider is deliberately transport-free; these calls prove
    # the cases exercised the normal request path without live provider access.
    assert all(len(client.calls) == 1 for client in clients)
    assert all(
        isinstance(call[1]["content"], str)
        or not any(part.get("type") == "image_url" for part in call[1]["content"])
        for client in clients for call in client.calls
    )
