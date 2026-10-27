from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct
from types import SimpleNamespace
from uuid import uuid4
import zlib

import httpx
import pytest
from pydantic import ValidationError

from miniconstruct import story_planning as service
from miniconstruct.benchmark import story as story_benchmark
from miniconstruct.benchmark.recording import RecordingClient, SingleAttemptClient, require_single_recorded_request
from miniconstruct.benchmark.runner import (
    BenchmarkCase, BenchmarkCheck, BenchmarkProfile, BenchmarkRunner, _load_case_definition,
    _messages_provenance, compare_runs, load_case, load_planner_case, load_suite, write_run,
)
from miniconstruct.benchmark.story import (
    DeterministicStoryProjectStore, evaluate_story_checks, load_story_workspaces,
    materialize_story_project, revision_request,
)
from miniconstruct.llm.client import LLMBackendError, OpenAICompatibleClient
from miniconstruct.models.story_operations import StoryInsertRequest, StoryNextRequest
from miniconstruct.models.story_planning import StoryBuildRequest, StoryPlanProposal, StoryReferenceRecommendation
from miniconstruct.models.api import ReasoningMode
from miniconstruct.models.workspace import Workspace
from miniconstruct.project_store import ProjectStore
from miniconstruct.story_operations import BoundedStorySnapshot
from test_benchmark_planner import profile


ROOT = Path(__file__).resolve().parents[1] / "benchmarks"
QUALITY_STORY_CASE_ID = "story-creative-quality-quiet"
VISION = {
    "story-reference-sheet-continuity": ["Anime_Girl_ReferenceSheet.png"],
    "story-station-multi-source-continuity": ["alice_station_identity.png", "station_environment.png"],
    "story-aviation-visual-sequencing": [f"glider{i}.png" for i in range(1, 7)],
}
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")


@pytest.fixture
def source_root(tmp_path):
    root = tmp_path / "external"
    (root / "assets").mkdir(parents=True)
    for filename in {name for names in VISION.values() for name in names} | {"alice_station_endpoint.png"}:
        (root / "assets" / filename).write_bytes(PNG)
    return root


class OfflineStoryClient:
    """Deterministic plumbing stub. It has no HTTP client or endpoint access."""
    def __init__(self, transform=None, wrapper=None, *, usage=None, effective_seed=1,
                 seed_unsupported=False, seed_known_unsupported=False):
        self.transform, self.wrapper = transform, wrapper
        self.calls = []
        self.last_usage = usage if usage is not None else {"prompt_tokens": 12, "completion_tokens": 8}
        self.effective_seed = effective_seed
        self.seed_unsupported = seed_unsupported
        self.seed_known_unsupported = seed_known_unsupported

    def __call__(self, settings):
        self.settings = settings
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def generate(self, messages, *, response_instruction="H3 sentinel"):
        self.calls.append(messages)
        assert response_instruction is None
        content = messages[1]["content"]
        context = json.loads(content[0]["text"] if isinstance(content, list) else content)
        if "targetGenerationIds" in context:
            relationship = None if context["startOrdinal"] == 1 else "new_shot_preserve_continuity"
            result = {"items": [{
                "existingGenerationId": generation_id,
                "scenePlan": "Replanned historical beat.",
                "dialogue": "",
                "incomingState": "Historical continuity remains intact.",
                "intendedOutgoingState": "The next protected beat remains possible.",
                "relationshipToPrevious": relationship if index == 0 else "independent",
                "cameraIntent": "A measured medium shot.",
                "shotCount": 1,
                "referenceRecommendations": [],
                "memoryPatchAfter": {},
            } for index, generation_id in enumerate(context["targetGenerationIds"])], "warnings": []}
        elif "g1GenerationId" in context:
            result = {"sequenceContext": context["sequenceContext"], "items": [{
                "scenePlan": f"A coherent initial beat {index + 1} advances the ferry emergency.",
                "dialogue": "",
                "incomingState": "The ferry emergency is underway.",
                "intendedOutgoingState": "The next beat remains causally possible.",
                "relationshipToPrevious": None if index == 0 else "new_shot_preserve_continuity",
                "cameraIntent": "A measured medium shot.",
                "shotCount": 1,
                "referenceRecommendations": [],
                "memoryPatchAfter": {},
            } for index in range(context["requestedTotalCount"])], "warnings": []}
        elif "slotIds" in context:
            result = {"slots": [{
                "planItemId": slot_id,
                "scenePlan": "A coherent continuation follows the saved Story Memory.",
                "dialogue": "",
                "incomingState": "The established situation continues.",
                "intendedOutgoingState": "The next beat remains causally possible.",
                "relationshipToPrevious": "new_shot_preserve_continuity",
                "cameraIntent": "A measured medium shot.",
                "shotCount": 1,
                "referenceRecommendations": [],
                # The neutral fake validates plumbing only; it must not infer
                # narrative resolution from benchmark instructions.
                "memoryPatchAfter": {},
            } for index, slot_id in enumerate(context["slotIds"])], "warnings": []}
        elif "editableScopes" in context:
            result = {"patches": []}
            for item in context["contextItems"]:
                if item["planItemId"] not in context["selectedPlanItemIds"]:
                    continue
                patch = {"planItemId": item["planItemId"]}
                for field in context["editableFields"]:
                    patch[field] = (None if item[field] is None else "continue_previous_shot") if field == "relationshipToPrevious" else (2 if field == "shotCount" else "Model revised " + field)
                result["patches"].append(patch)
        else:
            result = deepcopy(context["draft"])
            result.setdefault("sequenceContext", "")
            result.setdefault("consistencyWarnings", [])
            for index, item in enumerate(result["items"]):
                for field in ("scenePlan", "incomingState", "intendedOutgoingState", "cameraIntent"):
                    item.setdefault(field, "")
                item.setdefault("relationshipToPrevious", "independent" if index else None)
                item.setdefault("referenceRecommendations", [])
            result["sequenceContext"] = result["sequenceContext"] or "Coherent fictional sequence."
            for item in result["items"]:
                for field in ("scenePlan", "incomingState", "intendedOutgoingState", "cameraIntent"):
                    item[field] = item[field] or f"Planned {field}."
            if "no cut" in context["idea"]:
                result["items"][1]["relationshipToPrevious"] = "continue_previous_shot"
        if self.transform:
            self.transform(result, context)
        if "editableScopes" not in context and "slotIds" not in context and "targetGenerationIds" not in context:
            for key in ("baseProjectId", "baseProjectUpdatedAt", "baseSequenceFingerprint"):
                result.pop(key, None)
            for item in result["items"]:
                item.pop("existingGenerationId", None)
        raw = json.dumps(result)
        return self.wrapper(raw) if self.wrapper else raw


def definition(case_id):
    return json.loads((ROOT / "cases" / case_id / "case.json").read_text())


def _decode_png_data_url(data_url):
    prefix, encoded = data_url.split(",", 1)
    assert prefix == "data:image/png;base64"
    payload = base64.b64decode(encoded, validate=True)
    assert payload.startswith(b"\x89PNG\r\n\x1a\n")
    position, ihdr, idat = 8, None, bytearray()
    while position < len(payload):
        length = struct.unpack(">I", payload[position:position + 4])[0]
        chunk_type = payload[position + 4:position + 8]
        chunk_data = payload[position + 8:position + 8 + length]
        crc = struct.unpack(">I", payload[position + 8 + length:position + 12 + length])[0]
        assert zlib.crc32(chunk_type + chunk_data) & 0xffffffff == crc
        if chunk_type == b"IHDR":
            ihdr = chunk_data
        elif chunk_type == b"IDAT":
            idat.extend(chunk_data)
        position += length + 12
    assert payload[-12:] == b"\x00\x00\x00\x00IEND\xaeB`\x82"
    assert ihdr is not None and len(ihdr) == 13
    width, height, bit_depth, color_type, *_ = struct.unpack(">IIBBBBB", ihdr)
    assert width > 0 and height > 0 and bit_depth in {1, 2, 4, 8, 16}
    assert zlib.decompress(bytes(idat))
    return width, height, color_type, zlib.decompress(bytes(idat))


def test_story_longform_identity_fixtures_are_decodable_pngs_and_distinct():
    workspaces = {
        case_id: json.loads((ROOT / "cases" / case_id / "identity-workspace.json").read_text())
        for case_id in ("story-longform-returning-subject", "story-longform-irrelevant-subject")
    }
    decoded = {}
    for case_id, workspace in workspaces.items():
        decoded[case_id] = {
            asset["subjectIdentity"]["subjectId"]: _decode_png_data_url(asset["image"]["data_url"])
            for asset in workspace["assets"]
        }
        assert all(item[:2] == (1, 1) for item in decoded[case_id].values())
    alice = decoded["story-longform-irrelevant-subject"]["alice"]
    bob = decoded["story-longform-irrelevant-subject"]["bob"]
    assert alice[3] != bob[3]  # decoded scanlines, not merely different metadata


def test_suites_and_all_existing_fixtures_remain_valid(source_root):
    normal = load_suite(ROOT, "story").cases
    stress = load_suite(ROOT, "story-stress").cases
    assert len(normal) == 11 and len(set(normal)) == 11
    assert stress == ["story-eight-generation-stress"]
    assert not set(normal) & set(stress)
    assert set(normal) <= set(load_suite(ROOT, "all").cases)
    assert not set(stress) & set(load_suite(ROOT, "all").cases)
    for case_path in (ROOT / "cases").glob("*/case.json"):
        case = BenchmarkCase.model_validate_json(case_path.read_text())
        if case.story:
            assert load_story_workspaces(case, case_path.parent, source_root)
        elif case.planner:
            assert load_planner_case(ROOT, case.id, source_root)
        else:
            assert load_case(ROOT, case.id)


@pytest.mark.asyncio
async def test_extended_initial_range_suite_is_matched_and_text_only_even_with_vision_profile():
    case_ids = load_suite(ROOT, "story-initial-range-extended").cases
    assert case_ids == [
        "story-initial-range-extended-4", "story-initial-range-extended-8", "story-initial-range-extended-12",
    ]
    definitions = [definition(case_id) for case_id in case_ids]
    stories = [{key: value for key, value in item["story"].items() if key != "initialRange"} for item in definitions]
    assert stories[0] == stories[1] == stories[2]
    workspace_paths = [ROOT / "cases" / case_id / "workspace.json" for case_id in case_ids]
    workspaces = [path.read_bytes() for path in workspace_paths]
    assert workspaces[0] == workspaces[1] == workspaces[2]
    assert all(json.loads(path.read_text())["assets"] == [] for path in workspace_paths)
    assert all(not definition(case_id).get("fixtureAssets") for case_id in case_ids)
    assert all(not generation.get("fixtureAssets") for item in definitions for generation in item["story"]["generations"])

    results = (await BenchmarkRunner(ROOT, OfflineStoryClient()).run(profile(vision=True), case_ids))["cases"]
    for expected_total, result in zip((4, 8, 12), results):
        assert result["status"] == "completed"
        assert result["storyMetrics"]["requestedTotalRange"] == expected_total
        assert result["storyMetrics"]["returnedItemCount"] == expected_total
        assert result["storyMetrics"]["newGenerationCount"] == expected_total - 1
        assert result["storyMetrics"]["sourcePictureInputCount"] == 0
        assert result["storyMetrics"]["picturesTransmitted"] == 0
        assert result["modelInput"]["imageInputCount"] == 0
        assert result["modelInput"]["images"] == []
        assert len(result["projectedStoryMemoryAfter"]) == expected_total
        assert result["machineChecks"]["passed"] is True


@pytest.mark.asyncio
async def test_story_creative_request_quality_suite_is_opt_in_text_only_and_one_call_per_case():
    suite_id = "story-creative-request-quality"
    case_ids = [
        "story-creative-quality-mystery",
        "story-creative-quality-interpersonal",
        "story-creative-quality-quiet",
    ]
    assert load_suite(ROOT, suite_id).cases == case_ids
    for suite_path in (ROOT / "suites").glob("*.json"):
        if suite_path.stem == suite_id:
            continue
        assert not set(case_ids) & set(load_suite(ROOT, suite_path.stem).cases)

    expected_subjects = [["woman"], ["older_sister", "younger_sister"], ["man"]]
    expected_counts = [4, 3, 3]
    definitions = [definition(case_id) for case_id in case_ids]
    assert [item["story"]["initialRange"]["requestedTotalCount"] for item in definitions] == expected_counts
    assert all(item["story"]["sequenceContext"] == "" for item in definitions)
    assert all(not item.get("fixtureAssets") for item in definitions)
    for case_id, expected in zip(case_ids, expected_subjects):
        workspace = json.loads((ROOT / "cases" / case_id / "workspace.json").read_text())
        assert workspace["assets"] == []
        assert [subject["id"] for subject in workspace["subjects"]] == expected
        assert [subject["subjectId"] for subject in definition(case_id)["story"]["narrativeSubjects"]] == expected
        assert not any(generation.get("fixtureAssets") for generation in definition(case_id)["story"]["generations"])

    def use_canonical_subjects(result, context):
        allowed = [subject["subjectId"] for subject in context["sequenceNarrativeCast"]]
        assert allowed
        for item in result["items"]:
            item["participatingSubjectIds"] = allowed

    client = OfflineStoryClient(transform=use_canonical_subjects)
    results = (await BenchmarkRunner(ROOT, client).run(profile(), case_ids))["cases"]
    assert len(client.calls) == 3
    for result, expected_count, expected in zip(results, expected_counts, expected_subjects):
        assert result["status"] == "completed"
        assert result["machineChecks"]["passed"] is True
        assert result["storyMetrics"]["requestedTotalRange"] == expected_count
        assert result["storyMetrics"]["returnedItemCount"] == expected_count
        assert result["storyMetrics"]["sourcePictureInputCount"] == 0
        assert result["storyMetrics"]["picturesTransmitted"] == 0
        assert result["modelInput"]["imageInputCount"] == 0
        assert result["modelInput"]["images"] == []
        assert result["settings"]["repair"] == "never"
        assert result["settings"]["automaticRetry"] == "never"
        assert result["settings"]["llmJudge"] == "none"
        context = _story_request_context(result)
        assert context["sequenceContext"] == ""
        assert [subject["subjectId"] for subject in context["sequenceNarrativeCast"]] == expected


@pytest.mark.asyncio
async def test_story_benchmark_uses_populated_recorder_when_another_is_only_constructed(monkeypatch):
    original = story_benchmark.build_initial_range

    async def construct_unused_recorder(store, project_id, request, client_factory, *, item_id_factory=None):
        client_factory(request.llm)
        return await original(store, project_id, request, client_factory, item_id_factory=item_id_factory)

    monkeypatch.setattr(story_benchmark, "build_initial_range", construct_unused_recorder)
    client = OfflineStoryClient()
    result = (await BenchmarkRunner(ROOT, client).run(
        profile(), [QUALITY_STORY_CASE_ID],
    ))["cases"][0]

    assert result["status"] == "completed"
    assert result["modelInput"]["user"]
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_story_benchmark_reports_zero_recorded_requests_without_traceback(monkeypatch):
    async def bypass_recording(self, messages, *, response_instruction=None):
        raw = await self.wrapped.generate(messages, response_instruction=response_instruction)
        self.raw_outputs.append(raw)
        return raw

    monkeypatch.setattr(RecordingClient, "generate_streamed", bypass_recording)
    client = OfflineStoryClient()
    result = (await BenchmarkRunner(ROOT, client).run(
        profile(), [QUALITY_STORY_CASE_ID],
    ))["cases"][0]

    assert result["status"] == "error"
    assert result["error"]["type"] == "benchmark_instrumentation"
    assert "zero provider request payloads" in result["error"]["message"]
    assert result["rawModelOutput"]
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_story_benchmark_reports_multiple_recorded_requests_without_choosing_one(monkeypatch):
    original = story_benchmark.build_initial_range

    async def send_twice(store, project_id, request, client_factory, *, item_id_factory=None):
        await original(store, project_id, request, client_factory, item_id_factory=item_id_factory)
        return await original(store, project_id, request, client_factory, item_id_factory=item_id_factory)

    monkeypatch.setattr(story_benchmark, "build_initial_range", send_twice)
    client = OfflineStoryClient()
    result = (await BenchmarkRunner(ROOT, client).run(
        profile(), [QUALITY_STORY_CASE_ID],
    ))["cases"][0]

    assert result["status"] == "error"
    assert result["error"]["type"] == "benchmark_instrumentation"
    assert "2 provider request payloads" in result["error"]["message"]
    assert "modelInput" not in result
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_streamed_recording_captures_final_request_and_selects_only_populated_recorder():
    class StreamedOfflineStoryClient(OfflineStoryClient):
        def __init__(self):
            super().__init__()
            self.streamed_calls = 0

        async def generate_streamed(self, messages, *, response_instruction=None):
            self.streamed_calls += 1
            self.last_payload = {"messages": messages, "stream": True, "seed": 1, "marker": "final"}
            self.seed_unsupported = True
            return await self.generate(messages, response_instruction=response_instruction)

    settings = profile().to_settings()
    raw_outputs = []
    wrapped = StreamedOfflineStoryClient()
    populated = RecordingClient(wrapped, raw_outputs, settings)
    constructed_only = RecordingClient(OfflineStoryClient(), raw_outputs, settings)
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": json.dumps({
            "g1GenerationId": "g1", "sequenceContext": "context", "requestedTotalCount": 2,
        })},
    ]

    raw = await populated.generate_streamed(messages, response_instruction=None)

    assert wrapped.streamed_calls == 1
    assert raw_outputs == [raw]
    assert populated.payload["marker"] == "final"
    assert populated.payload["stream"] is True
    assert "seed" not in populated.payload
    assert populated.last_request_duration_ms is not None
    assert require_single_recorded_request([constructed_only, populated]) is populated


@pytest.mark.asyncio
async def test_story_quality_fixture_still_rejects_invented_subject_id():
    def invent_subject(result, _context):
        result["items"][1]["participatingSubjectIds"] = ["invented"]

    client = OfflineStoryClient(transform=invent_subject)
    result = (await BenchmarkRunner(ROOT, client).run(
        profile(), [QUALITY_STORY_CASE_ID],
    ))["cases"][0]

    assert result["status"] == "error"
    assert result["error"]["type"] == "provider_or_validation"
    assert "participatingSubjectIds" in result["error"]["message"]
    assert len(client.calls) == 1


@pytest.mark.parametrize("mutation", [
    lambda c: c["story"].pop("build"),
    lambda c: c["story"]["build"].pop("desiredGenerationCount"),
    lambda c: c["story"]["build"].update(idea=" \n "),
    lambda c: c["story"]["build"].update(desiredGenerationCount=True),
    lambda c: c["story"]["build"].update(desiredGenerationCount=1),
    lambda c: c["story"].update(revision=definition("story-revision-camera-multiselect")["story"]["revision"]),
    lambda c: c.update(operation="revise_story_plan"),
    lambda c: c.update(operation="generate_prompt"),
    lambda c: c.update(operation="infer_plan", planner={"operation": "infer_plan", "previousGeneration": {}}),
    lambda c: c.update(operation=None),
    lambda c: c.pop("story"),
    lambda c: c.update(operation="apply_story_plan"),
])
def test_build_schema_rejects_invalid_shapes(mutation):
    case = definition("story-basic-four-beat")
    mutation(case)
    with pytest.raises(ValidationError):
        BenchmarkCase.model_validate(case)


@pytest.mark.parametrize("mutation", [
    lambda r: r.pop("draft"), lambda r: r.pop("selectedItems"), lambda r: r.pop("scopes"),
    lambda r: r.update(selectedItems=[]), lambda r: r.update(selectedItems=[2, 2]),
    lambda r: r.update(selectedItems=[5]), lambda r: r.update(selectedItems=[True]),
    lambda r: r.update(scopes=[]), lambda r: r.update(scopes=["camera_intent", "camera_intent"]),
    lambda r: r.update(instruction=" \n "), lambda r: r.update(desiredGenerationCount=4),
    lambda r: r["draft"]["items"][1].pop("scenePlan"),
    lambda r: r["draft"]["items"][1].update(existingGeneration=1),
    lambda r: r["draft"]["items"][0].update(relationshipToPrevious="independent"),
])
def test_revision_schema_rejects_invalid_shapes(mutation):
    case = definition("story-revision-camera-multiselect")
    mutation(case["story"]["revision"])
    with pytest.raises(ValidationError):
        BenchmarkCase.model_validate(case)


async def test_all_story_cases_use_real_services_saved_authoring_projects_and_no_apply(source_root, monkeypatch):
    seen = []
    def spy(original):
        async def invoke(store, project_id, request, client_factory, **kwargs):
            assert store.root.is_dir()
            assert not store.root.is_relative_to(ROOT.parent)
            snapshot = store.read_story_snapshot(project_id)
            before = {p.relative_to(store.root): p.read_bytes() for p in store.root.rglob("*") if p.is_file()}
            seen.append((original.__name__, store.root, snapshot))
            result = await original(store, project_id, request, client_factory, **kwargs)
            assert before == {p.relative_to(store.root): p.read_bytes() for p in store.root.rglob("*") if p.is_file()}
            return result
        return invoke
    monkeypatch.setattr(service, "build_story_plan", spy(service.build_story_plan))
    monkeypatch.setattr(service, "revise_story_plan", spy(service.revise_story_plan))
    def forbidden(*args, **kwargs):
        pytest.fail("Story Apply must never be called")
    monkeypatch.setattr(ProjectStore, "apply_story_plan", forbidden)
    normal = load_suite(ROOT, "story").cases
    client = OfflineStoryClient()
    result = await BenchmarkRunner(ROOT, client, source_root).run(profile(vision=True), normal + load_suite(ROOT, "story-stress").cases)
    assert len(client.calls) == 12
    for item in result["cases"]:
        assert item["status"] == "completed", item
        assert item["machineChecks"]["passed"], item
        assert item["structuralValid"]
        assert item["rawModelOutput"]
        assert item["structuredStoryPlannerOutput"]
        assert item["settings"] == {"temperature": 0.4, "seedMode": "fixed", "fixedSeed": 1,
            "variations": 1, "reasoning": "on", "transport": "non_streaming",
            "repair": "never", "automaticRetry": "never", "llmJudge": "none"}
        assert item["effectiveRequest"]["temperature"] == 0.4
        assert item["effectiveRequest"]["seed"] == 1
        assert item["effectiveRequest"]["stream"] is False
        assert item["provider"]["usage"] == client.last_usage
        assert "Output the finished H3 prompt directly." not in item["modelInput"]["system"]
    assert sum(name == "revise_story_plan" for name, _, _ in seen) == 3
    assert len({root for _, root, _ in seen}) == 12
    assert all(not root.exists() for _, root, _ in seen)
    assert seen[0][2].generations[0].workspace.creative_request == ""
    with pytest.raises(ValidationError):
        Workspace.model_validate_json((ROOT / "cases/story-basic-four-beat/workspace.json").read_text())


def story_reasoning_profile(mode: ReasoningMode) -> BenchmarkProfile:
    base = profile(vision=False)
    return base.model_copy(update={
        "endpoint": base.endpoint.model_copy(update={"base_url": "http://127.0.0.1:8888/v1"}),
        "reasoning_mode": mode,
    })


@pytest.mark.asyncio
@pytest.mark.parametrize("mode, expected_kwargs", [
    (ReasoningMode.OFF, {"thinking": False, "enable_thinking": False}),
    (ReasoningMode.ON, {"thinking": True, "enable_thinking": True}),
    (ReasoningMode.DEFAULT, None),
])
async def test_story_profile_reasoning_reaches_final_provider_payload(mode, expected_kwargs):
    item = (await BenchmarkRunner(ROOT, OfflineStoryClient()).run(
        story_reasoning_profile(mode), ["story-basic-four-beat"],
    ))["cases"][0]
    assert item["settings"]["reasoning"] == mode.value
    assert item["effectiveRequest"].get("chat_template_kwargs") == expected_kwargs


def _story_request_context(result):
    user = result["modelInput"]["user"]
    return json.JSONDecoder().raw_decode(user)[0]


@pytest.mark.asyncio
async def test_identical_build_cases_receive_byte_identical_deterministic_saved_snapshots():
    first = (await BenchmarkRunner(ROOT, OfflineStoryClient()).run(profile(), ["story-basic-four-beat"]))["cases"][0]
    second = (await BenchmarkRunner(ROOT, OfflineStoryClient()).run(profile(), ["story-basic-four-beat"]))["cases"][0]
    assert first["modelInput"] == second["modelInput"]
    assert first["effectiveRequest"] == second["effectiveRequest"]
    assert first["canonicalGenerations"] == second["canonicalGenerations"]
    assert first["canonicalProjectSnapshot"] == second["canonicalProjectSnapshot"]
    first_context, second_context = _story_request_context(first), _story_request_context(second)
    for field in ("baseProjectId", "baseProjectUpdatedAt", "baseSequenceFingerprint"):
        assert field not in first_context["draft"] and field not in second_context["draft"]
    assert first_context["draft"] == second_context["draft"]
    assert first_context["sourceGenerations"] == second_context["sourceGenerations"]
    assert first["canonicalGenerations"][0]["generationId"] == first_context["draft"]["items"][0]["existingGenerationId"]
    assert "baseProjectUpdatedAt" not in first_context["draft"]
    assert first["canonicalProjectSnapshot"]["createdAt"] == first["canonicalProjectSnapshot"]["updatedAt"] == "2020-01-01T00:00:00.000Z"
    assert all(g["createdAt"] == g["updatedAt"] == "2020-01-01T00:00:00.000Z" for g in first["canonicalGenerations"])
    assert first["canonicalProjectSnapshot"]["baseSequenceFingerprint"] == second["canonicalProjectSnapshot"]["baseSequenceFingerprint"]
    assert first["provider"]["usage"] == second["provider"]["usage"]


@pytest.mark.asyncio
async def test_revision_reproducibility_includes_ids_timestamps_fingerprint_and_raw_plan_ids():
    first = (await BenchmarkRunner(ROOT, OfflineStoryClient()).run(profile(), ["story-revision-camera-multiselect"]))["cases"][0]
    second = (await BenchmarkRunner(ROOT, OfflineStoryClient()).run(profile(), ["story-revision-camera-multiselect"]))["cases"][0]
    assert first["modelInput"] == second["modelInput"]
    assert first["effectiveRequest"] == second["effectiveRequest"]
    assert first["canonicalGenerations"] == second["canonicalGenerations"]
    assert first["canonicalProjectSnapshot"] == second["canonicalProjectSnapshot"]
    assert first["initialStoryDraft"] == second["initialStoryDraft"]
    context = _story_request_context(first)
    assert first["initialStoryDraft"]["baseProjectUpdatedAt"] == "2020-01-01T00:00:00.000Z"
    assert "draft" not in context
    assert "baseProjectId" not in context
    assert [item["planItemId"] for item in first["initialStoryDraft"]["items"]] == [item["planItemId"] for item in second["initialStoryDraft"]["items"]]
    assert context["selectedPlanItemIds"] == _story_request_context(second)["selectedPlanItemIds"]


def test_deterministic_story_project_store_separates_case_and_generation_identities(tmp_path):
    stores = [DeterministicStoryProjectStore(tmp_path / name, name) for name in ("case-a", "case-b")]
    # A minimal authoring workspace is enough to exercise the identity seam.
    payload = {"schemaVersion": 1, "projectName": "Identity", "mode": "T2VA", "durationSeconds": 6,
               "shots": 1, "aspectRatio": "16:9", "variations": 1, "creativeRequest": "A."}
    workspaces = [Workspace.model_validate(payload) for _ in stores]
    projects = [store.create_project(workspace) for store, workspace in zip(stores, workspaces)]
    assert projects[0].project_id != projects[1].project_id
    assert projects[0].active_generation_id != projects[1].active_generation_id
    assert stores[0].read_story_snapshot(projects[0].project_id).generations[0].generation_id != stores[1].read_story_snapshot(projects[1].project_id).generations[0].generation_id
    assert projects[0].created_at == projects[1].created_at == "2020-01-01T00:00:00.000Z"


def test_deterministic_store_keeps_project_clock_when_adding_second_and_third_generations(tmp_path):
    store = DeterministicStoryProjectStore(tmp_path, "multi-generation-clock")
    payload = {"schemaVersion": 1, "projectName": "Identity", "mode": "T2VA", "durationSeconds": 6,
               "shots": 1, "aspectRatio": "16:9", "variations": 1, "creativeRequest": "A."}
    project = store.create_project(Workspace.model_validate(payload))
    first_id = project.active_generation_id
    store.create_generation(project.project_id)
    store.create_generation(project.project_id)
    manifest = store._read_manifest(store._project_directory(project.project_id, must_exist=True))
    assert manifest.project_id == project.project_id
    assert manifest.created_at == manifest.updated_at == "2020-01-01T00:00:00.000Z"
    assert len(manifest.sequence.generations) == 3
    assert [generation.created_at for generation in manifest.sequence.generations] == ["2020-01-01T00:00:00.000Z"] * 3
    assert [generation.updated_at for generation in manifest.sequence.generations] == ["2020-01-01T00:00:00.000Z"] * 3
    assert manifest.sequence.generations[0].generation_id == first_id
    assert len({generation.generation_id for generation in manifest.sequence.generations}) == 3


@pytest.mark.parametrize("case_id", [
    "story-existing-authority", "story-reference-source-disambiguation",
    "story-station-multi-source-continuity",
])
@pytest.mark.asyncio
async def test_every_two_generation_story_fixture_repeats_identically(case_id, source_root):
    vision = case_id == "story-station-multi-source-continuity"
    kwargs = {"source_root": source_root} if vision else {}
    first = (await BenchmarkRunner(ROOT, OfflineStoryClient(), **kwargs).run(profile(vision=vision), [case_id]))["cases"][0]
    second = (await BenchmarkRunner(ROOT, OfflineStoryClient(), **kwargs).run(profile(vision=vision), [case_id]))["cases"][0]
    assert first["status"] == second["status"] == "completed"
    assert first["modelInput"] == second["modelInput"]
    assert first["effectiveRequest"] == second["effectiveRequest"]
    assert first["canonicalProjectSnapshot"] == second["canonicalProjectSnapshot"]
    assert first["canonicalGenerations"] == second["canonicalGenerations"]
    assert first["canonicalProjectSnapshot"]["createdAt"] == first["canonicalProjectSnapshot"]["updatedAt"] == "2020-01-01T00:00:00.000Z"
    assert all(generation["createdAt"] == generation["updatedAt"] == "2020-01-01T00:00:00.000Z" for generation in first["canonicalGenerations"])
    context = _story_request_context(first)
    assert "baseProjectUpdatedAt" not in context["draft"]
    assert "baseSequenceFingerprint" not in context["draft"]
    assert [generation["generationId"] for generation in first["canonicalGenerations"]] == [
        item["existingGenerationId"] for item in context["draft"]["items"][:2]
    ]


@pytest.mark.parametrize("case_id,names", VISION.items())
async def test_real_resolver_vision_provenance_and_nonvision_skip(case_id, names, source_root, tmp_path):
    client = OfflineStoryClient()
    case = (await BenchmarkRunner(ROOT, client, source_root).run(profile(vision=True), [case_id]))["cases"][0]
    assert case["status"] == "completed"
    content = client.calls[0][1]["content"]
    images = [p for p in content if p["type"] == "image_url"]
    assert len(images) == len(names) == case["modelInput"]["imageInputCount"]
    assert all(p["image_url"]["url"].startswith("data:image/jpeg;base64,") for p in images)
    assert {entry["fixturePath"] for entry in case["referenceInputs"]} == {"assets/" + name for name in names}
    assert all(e["fixtureSha256"] == hashlib.sha256(PNG).hexdigest() for e in case["referenceInputs"])
    transmitted_hashes = {hashlib.sha256(base64.b64decode(part["image_url"]["url"].split(",", 1)[1])).hexdigest()
                          for part in images}
    assert {entry["sha256"] for entry in case["modelInput"]["images"]} == transmitted_hashes
    serialized = json.dumps(case)
    assert str(source_root) not in serialized and str(source_root).replace("\\", "\\\\") not in serialized
    assert "base64" not in serialized and base64.b64encode(PNG).decode() not in serialized
    skipped_client = OfflineStoryClient()
    skipped = (await BenchmarkRunner(ROOT, skipped_client, tmp_path / "missing").run(profile(), [case_id]))["cases"][0]
    assert skipped["status"] == "skipped"
    assert skipped["skip"]["code"] == "vision_capability_mismatch"
    assert skipped_client.calls == []
    definition_, directory = _load_case_definition(ROOT, case_id)
    store = ProjectStore(tmp_path / "assembly")
    snapshot = materialize_story_project(store, definition_, load_story_workspaces(definition_, directory, source_root))
    draft = service.initial_story_draft(snapshot, definition_.story.build.desired_generation_count)
    assert _messages_provenance(service.assemble_story_prompt(snapshot, draft, profile().to_settings(), idea="Idea"))["imageInputCount"] == 0


async def test_revision_patch_output_preserves_authority_and_generates_boundary_warnings():
    ids = ["story-revision-camera-multiselect", "story-revision-outgoing-warning", "story-revision-transition-reframe"]
    run = await BenchmarkRunner(ROOT, OfflineStoryClient()).run(profile(), ids)
    for result in run["cases"]:
        assert result["machineChecks"]["passed"]
        final, before = result["structuredStoryPlannerOutput"], result["initialStoryDraft"]
        assert set(json.loads(result["rawModelOutput"])) == {"patches"}
        assert final["sequenceContext"] == before["sequenceContext"]
        assert final["items"][1]["scenePlan"] == before["items"][1]["scenePlan"]
        assert "removes one wet glove" in final["items"][1]["scenePlan"]
        assert final["items"][0] == before["items"][0] and final["items"][3] == before["items"][3]
    assert run["cases"][1]["structuredStoryPlannerOutput"]["consistencyWarnings"][0]["direction"] == "downstream"
    assert run["cases"][2]["structuredStoryPlannerOutput"]["consistencyWarnings"][0]["direction"] == "upstream"


async def test_metadata_only_media_and_qualified_pair_sanitization():
    def references(result, context):
        sources = [g["sourceGenerationId"] for g in context["sourceGenerations"]]
        result["items"][2]["referenceRecommendations"] = [
            {"sourceGenerationId": source, "assetId": "shared-reference", "recommendation": "Intended reference use."}
            for source in sources + [sources[0], str(uuid4())]
        ]
    client = OfflineStoryClient(references)
    item = (await BenchmarkRunner(ROOT, client).run(profile(vision=True), ["story-reference-source-disambiguation"]))["cases"][0]
    assert item["machineChecks"]["passed"]
    pairs = item["structuredStoryPlannerOutput"]["items"][2]["referenceRecommendations"]
    assert len(pairs) == 2 and pairs[0]["sourceGenerationId"] != pairs[1]["sourceGenerationId"]
    assert len(json.loads(item["rawModelOutput"])["items"][2]["referenceRecommendations"]) == 4
    assert {part["type"] for part in client.calls[0][1]["content"]} == {"text"}
    assert {a["kind"] for a in item["referenceInputs"]} == {"audio", "video"}
    def unsupported(result, _):
        result["items"][1]["scenePlan"] = "I heard the audio and I watched the video."
    dishonest = (await BenchmarkRunner(ROOT, OfflineStoryClient(unsupported)).run(profile(), ["story-media-honesty"]))["cases"][0]
    assert not dishonest["machineChecks"]["passed"]


@pytest.mark.parametrize("wrapper,accepted", [
    (lambda raw: raw, True), (lambda raw: f"```json\n{raw}\n```", True),
    (lambda raw: f"```\n{raw}\n```", True),
    (lambda raw: f"prose\n```json\n{raw}\n```", False),
    (lambda raw: f"```json\n{raw}\n```\ntrailing", False),
    (lambda raw: f"```json\n{raw}\n```\n```json\n{raw}\n```", False),
    (lambda raw: "{malformed}", False), (lambda raw: "[]", False),
])
async def test_production_fenced_json_and_raw_capture(wrapper, accepted):
    client = OfflineStoryClient(wrapper=wrapper)
    item = (await BenchmarkRunner(ROOT, client).run(profile(), ["story-basic-four-beat"]))["cases"][0]
    assert (item["status"] == "completed") == accepted
    assert item["rawModelOutput"]
    assert len(client.calls) == 1


@pytest.mark.parametrize("rejection", ["seed is unsupported", "Unsupported parameter chat_template_kwargs"])
async def test_story_transport_never_retries_compatibility_errors(rejection):
    calls = []
    def endpoint(request):
        calls.append(json.loads(request.content))
        return httpx.Response(400, json={"error": rejection})
    settings = BenchmarkRunner(ROOT)._planner_settings(profile())
    cache_key = (settings.endpoint.base_url, settings.selected_model_id)
    OpenAICompatibleClient._unsupported_seed_endpoints.add(cache_key)
    try:
        async with SingleAttemptClient(settings, transport=httpx.MockTransport(endpoint)) as client:
            with pytest.raises(LLMBackendError):
                await client.generate([{"role": "user", "content": "Test"}], response_instruction=None)
        assert len(calls) == 1 and calls[0]["seed"] == 1
    finally:
        OpenAICompatibleClient._unsupported_seed_endpoints.discard(cache_key)


def test_check_failures_authority_counts_relationships_pairs_scope_and_warning(tmp_path):
    case, directory = _load_case_definition(ROOT, "story-revision-outgoing-warning")
    snapshot = materialize_story_project(ProjectStore(tmp_path), case, load_story_workspaces(case, directory, None))
    request = revision_request(case, snapshot, profile().to_settings())
    plan = request.draft.model_copy(deep=True)
    plan.items[1].intended_outgoing_state = "New end."
    plan = service.add_boundary_warnings(request, plan)
    def check(kind, **kwargs):
        return evaluate_story_checks([BenchmarkCheck(kind=kind, **kwargs)], plan, snapshot, request, {"imageInputCount": 0})[0]["passed"]
    assert check("story_item_count_equals", value=4)
    assert not check("story_item_count_equals", value=3)
    assert check("story_existing_generation_ids_preserved")
    assert check("story_first_relationship_null")
    assert check("story_relationship_valid")
    assert not check("story_relationship_equals", item=2, value="continue_previous_shot")
    assert check("story_field_unchanged", item=2, field="scenePlan")
    assert not check("story_field_changed", item=2, field="scenePlan")
    assert check("story_field_changed", item=2, field="intendedOutgoingState")
    assert check("story_selected_fields_only_changed")
    assert check("story_warning_exists", item=2, relatedItem=3, direction="downstream")
    assert not check("story_warning_exists", item=2, relatedItem=3, direction="upstream")
    assert not check("story_warning_exists", item=2, relatedItem=4, direction="downstream")
    assert not check("story_field_not_empty", item=12, field="scenePlan")
    plan.items[2].camera_intent = "Illegal unselected edit."
    assert not check("story_selected_fields_only_changed")
    plan.items[0].scene_plan = "Illegal authority edit."
    assert not check("story_field_unchanged", item=1, field="scenePlan")
    plan.items[1].existing_generation_id = str(uuid4())
    assert not check("story_existing_generation_ids_preserved")
    assert not check("story_new_items_have_no_existing_generation_id")


def test_qualified_checks_accept_two_sources_but_reject_duplicates_and_unknown_sources(tmp_path):
    case, directory = _load_case_definition(ROOT, "story-reference-source-disambiguation")
    snapshot = materialize_story_project(ProjectStore(tmp_path), case, load_story_workspaces(case, directory, None))
    settings = profile().to_settings()
    request = StoryBuildRequest(idea="Story", desiredGenerationCount=4, llm=settings)
    data = service.initial_story_draft(snapshot, 4).model_dump(mode="json", by_alias=True)
    references = [{"sourceGenerationId": g.generation_id, "assetId": "shared-reference", "recommendation": "Intended use."} for g in snapshot.generations]
    data["items"][2]["referenceRecommendations"] = references
    checks = [BenchmarkCheck(kind=kind) for kind in ("story_recommendation_pairs_valid", "story_no_duplicate_recommendation_pairs")]
    def passed():
        plan = StoryPlanProposal.model_validate(data)
        return [r["passed"] for r in evaluate_story_checks(checks, plan, snapshot, request, {"imageInputCount": 0})]
    assert passed() == [True, True]
    references.append(references[0])
    assert passed() == [True, False]
    references[-1] = {**references[0], "sourceGenerationId": str(uuid4())}
    assert passed() == [False, True]
    references[-1] = {**references[0], "assetId": "invented"}
    assert passed() == [False, True]


@pytest.mark.asyncio
async def test_bounded_next_valid_recommendation_is_checked_without_snapshot_shape_crash():
    def recommend_available_pair(result, context):
        pair = context["historicalReferenceContext"][0]
        result["slots"][0]["referenceRecommendations"] = [{
            "sourceGenerationId": pair["sourceGenerationId"],
            "assetId": pair["asset"]["id"],
            "recommendation": "Use the offered identity reference for continuity.",
        }]

    case = (await BenchmarkRunner(ROOT, OfflineStoryClient(transform=recommend_available_pair)).run(
        profile(vision=True), ["story-longform-returning-subject"]
    ))["cases"][0]
    recommendation_check = next(check for check in case["machineChecks"]["checks"]
                                if check["kind"] == "story_recommendation_pairs_valid")
    assert recommendation_check["passed"] is True


@pytest.mark.parametrize("request_type", [StoryNextRequest, StoryInsertRequest])
@pytest.mark.parametrize("pair_is_valid", [True, False])
def test_bounded_next_and_insert_recommendation_checks_fail_loudly_on_invalid_pair(
    tmp_path, request_type, pair_is_valid,
):
    case, directory = _load_case_definition(ROOT, "story-longform-returning-subject")
    snapshot = materialize_story_project(ProjectStore(tmp_path), case, load_story_workspaces(case, directory, None))
    bounded = BoundedStorySnapshot(
        source=snapshot, context={}, fingerprint="f" * 64,
        source_sequence_length=len(snapshot.generations),
    )
    source_generation_id, asset_id = next(iter(snapshot.allowed_refs))
    if not pair_is_valid:
        source_generation_id, asset_id = str(uuid4()), "unavailable-asset"
    recommendation = StoryReferenceRecommendation(
        sourceGenerationId=source_generation_id, assetId=asset_id, recommendation="Test reference.",
    )
    plan = SimpleNamespace(slots=[SimpleNamespace(reference_recommendations=[recommendation])])
    if request_type is StoryNextRequest:
        request = request_type(
            anchorGenerationId=snapshot.generations[-1].generation_id,
            requestedCount=1, instruction="", llm=profile().to_settings(),
        )
    else:
        request = request_type(
            upstreamGenerationId=snapshot.generations[0].generation_id,
            requestedCount=1, instruction="", llm=profile().to_settings(),
        )
    result = evaluate_story_checks(
        [BenchmarkCheck(kind="story_recommendation_pairs_valid")],
        plan, bounded, request, {"imageInputCount": 0},
    )
    assert result[0]["passed"] is pair_is_valid


async def test_generation_scoped_external_mappings_disambiguate_same_picture_id(tmp_path):
    # A second valid 1x1 PNG with different bytes; no dependency on image libraries.
    def chunk(kind, value):
        return struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind + value))
    second = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b"")
    root, source = tmp_path / "benchmarks", tmp_path / "external"
    case = definition("story-station-multi-source-continuity")
    directory = root / "cases" / case["id"]
    directory.mkdir(parents=True)
    (source / "assets").mkdir(parents=True)
    for index, fixture in enumerate(case["story"]["generations"]):
        workspace = json.loads((ROOT / "cases" / case["id"] / fixture["workspacePath"]).read_text())
        workspace["assets"][0]["id"] = "shared-picture"
        fixture["fixtureAssets"][0]["assetId"] = "shared-picture"
        (source / fixture["fixtureAssets"][0]["path"]).write_bytes(PNG if index == 0 else second)
        (directory / fixture["workspacePath"]).write_text(json.dumps(workspace))
    (directory / "case.json").write_text(json.dumps(case))
    result = (await BenchmarkRunner(root, OfflineStoryClient(), source).run(profile(vision=True), [case["id"]]))["cases"][0]
    assert result["status"] == "completed"
    first, second_input = result["modelInput"]["images"]
    assert first["assetId"] == second_input["assetId"] == "shared-picture"
    assert first["sourceGenerationId"] != second_input["sourceGenerationId"]
    assert first["fixturePath"] != second_input["fixturePath"]
    assert first["sha256"] != second_input["sha256"]


async def test_build_authority_projection_retains_raw_output_and_no_retry():
    def rewrite(result, _):
        result["items"][0]["scenePlan"] = "Illegally rewritten author field."
    client = OfflineStoryClient(rewrite)
    result = (await BenchmarkRunner(ROOT, client).run(profile(), ["story-existing-authority"]))["cases"][0]
    assert result["status"] == "completed" and result["structuralValid"]
    assert result["machineChecks"]["passed"]
    assert "Illegally rewritten" in result["rawModelOutput"]
    assert len(client.calls) == 1


async def test_error_and_raw_echo_sanitization_and_summary(source_root, tmp_path, monkeypatch):
    secret = "test-secret-not-a-real-key"
    monkeypatch.setenv("STORY_TEST_KEY", secret)
    configured = profile(vision=True).model_copy(update={"api_key_env": "STORY_TEST_KEY"})
    encoded = base64.b64encode(PNG).decode()
    echo = f"{source_root} {secret} data:image/png;base64,{encoded} standalone body: {encoded}"
    result = await BenchmarkRunner(ROOT, OfflineStoryClient(wrapper=lambda _: echo), source_root).run(configured, ["story-reference-sheet-continuity"])
    output = write_run(tmp_path / "benchmarks", result)
    for path in output.rglob("*.json"):
        text = path.read_text()
        assert secret not in text and base64.b64encode(PNG).decode() not in text
        assert str(source_root).replace("\\", "\\\\") not in text
    assert "review questions" in (output / "summary.md").read_text()
    missing = (await BenchmarkRunner(ROOT, OfflineStoryClient(), tmp_path / "missing").run(configured, ["story-reference-sheet-continuity"]))["cases"][0]
    assert missing["status"] == "error" and missing["error"]["type"] == "fixture_or_validation"
    assert str(tmp_path).replace("\\", "\\\\") not in json.dumps(missing)


@pytest.mark.asyncio
async def test_story_smoke_uses_production_narrative_names_and_records_measured_dimensions(source_root):
    configured = profile().model_copy(update={"context_window": 100})
    client = OfflineStoryClient()
    item = (await BenchmarkRunner(ROOT, client, source_root).run(configured, ["story-smoke-three-generation-build"]))["cases"][0]
    assert item["status"] == "completed"
    context = _story_request_context(item)
    assert context["sequenceNarrativeCast"] == [{
        "subjectId": "subject-1", "canonicalH3Label": "<Subject 1>",
        "narrativeName": "Alice",
    }]
    metrics = item["storyMetrics"]
    assert metrics["operation"] == "build"
    assert metrics["generationCount"] == 1
    assert metrics["selectedRevisionItemCount"] == 0
    assert metrics["sourcePictureInputCount"] == 0
    assert metrics["providerPromptTokens"] == 12
    assert metrics["providerCompletionTokens"] == 8
    assert metrics["providerTotalTokens"] is None
    assert metrics["contextWindow"] == 100
    assert metrics["promptContextUtilization"] == 12.0
    assert metrics["totalContextUtilization"] is None
    assert metrics["sourceGenerationCount"] == 1
    assert metrics["desiredGenerationCount"] == 3
    assert metrics["requestDraftItemCount"] is None
    assert metrics["responseItemCount"] == 3
    assert metrics["responseCharacters"] == len(item["rawModelOutput"])
    assert metrics["responseBytes"] == len(item["rawModelOutput"].encode("utf-8"))
    assert item["modelInput"]["totalTextInputCharacters"] == (
        item["modelInput"]["systemTextCharacters"] + item["modelInput"]["userTextCharacters"]
    )
    assert item["seed"]["requested"] == 1 and item["seed"]["status"] == "accepted_by_provider"
    assert item["seed"]["effective"] == 1 and item["seed"]["finalRequestIncludedSeed"]


@pytest.mark.asyncio
async def test_controlled_scaling_uses_real_build_and_narrow_dialogue_revision_paths():
    cases = load_suite(ROOT, "story-scaling").cases
    assert cases == [
        "story-scaling-build-2", "story-scaling-build-4", "story-scaling-build-8", "story-scaling-build-12",
        "story-scaling-revision-2", "story-scaling-revision-4", "story-scaling-revision-8", "story-scaling-revision-12",
    ]
    results = (await BenchmarkRunner(ROOT, OfflineStoryClient()).run(profile(), cases))["cases"]
    assert all(result["status"] == "completed" and result["machineChecks"]["passed"] for result in results)
    builds, revisions = results[:4], results[4:]
    assert [item["storyMetrics"]["generationCount"] for item in builds] == [2, 4, 8, 12]
    assert [item["storyMetrics"]["responseItemCount"] for item in builds] == [2, 4, 8, 12]
    assert [item["storyMetrics"]["generationCount"] for item in revisions] == [2, 4, 8, 12]
    assert all(item["storyMetrics"]["selectedRevisionItemCount"] == 1 for item in revisions)
    assert all(item["storyMetrics"]["selectedRevisionScopes"] == ["dialogue"] for item in revisions)
    assert all(item["storyMetrics"]["responseItemCount"] == item["storyMetrics"]["generationCount"] for item in revisions)
    assert [item["storyMetrics"]["responsePatchCount"] for item in revisions] == [1, 1, 1, 1]
    assert len({item["storyMetrics"]["responseBytes"] for item in revisions}) == 1
    for item in revisions:
        raw = json.loads(item["rawModelOutput"])
        assert set(raw) == {"patches"}
        assert len(raw["patches"]) == 1 and set(raw["patches"][0]) == {"planItemId", "dialogue"}
        context = _story_request_context(item)
        assert "draft" not in context
        assert len(context["contextItems"]) <= 3
        assert len(context["sourceGenerations"]) <= 3
        assert "creativeControls" not in json.dumps(context)
        assert "referenceRecommendations" not in json.dumps(context)
        for new, old in zip(item["structuredStoryPlannerOutput"]["items"], item["initialStoryDraft"]["items"]):
            allowed = {"dialogue"} if old["planItemId"] in context["selectedPlanItemIds"] else set()
            assert all(new[key] == value for key, value in old.items() if key not in allowed)


@pytest.mark.asyncio
async def test_dialogue_revision_accepts_explicit_blank_and_protects_unselected_dialogue():
    def blank_selected(result, _context):
        result["patches"][0]["dialogue"] = ""
    item = (await BenchmarkRunner(ROOT, OfflineStoryClient(blank_selected)).run(
        profile(), ["story-dialogue-blank-revision"]
    ))["cases"][0]
    assert item["status"] == "completed" and item["machineChecks"]["passed"]
    assert item["structuredStoryPlannerOutput"]["items"][1]["dialogue"] == ""
    assert item["structuredStoryPlannerOutput"]["items"][0]["dialogue"] == 'Mira: "Someone lost this."'
    assert item["structuredStoryPlannerOutput"]["items"][2]["dialogue"] == ""


@pytest.mark.asyncio
async def test_story_comparison_reports_raw_context_and_response_metric_deltas(source_root, tmp_path):
    result = await BenchmarkRunner(ROOT, OfflineStoryClient(), source_root).run(profile(), ["story-smoke-three-generation-build"])
    first = write_run(tmp_path / "benchmarks", result)
    changed = deepcopy(result)
    changed["runId"] = "later"
    changed["cases"][0]["storyMetrics"]["providerPromptTokens"] = 24
    changed["cases"][0]["storyMetrics"]["responseCharacters"] += 10
    second = write_run(tmp_path / "benchmarks", changed)
    comparison = compare_runs(first, second)
    assert "prompt tokens 12 → 24" in comparison
    assert "response size" in comparison
    summary = (first / "summary.md").read_text()
    assert "Story performance" in summary
    assert "Prompt" in summary and "Pictures" in summary and "unavailable" in summary


@pytest.mark.asyncio
async def test_story_compare_accepts_legacy_metric_shape_without_inventing_values(source_root, tmp_path):
    result = await BenchmarkRunner(ROOT, OfflineStoryClient(), source_root).run(profile(), ["story-smoke-three-generation-build"])
    legacy = deepcopy(result)
    legacy["runId"] = "legacy"
    metrics = legacy["cases"][0]["storyMetrics"]
    for key in ("sourceGenerationCount", "desiredGenerationCount", "requestDraftItemCount", "totalContextUtilization", "responsePatchCount"):
        metrics.pop(key, None)
    old = write_run(tmp_path / "benchmarks", legacy)
    new = write_run(tmp_path / "benchmarks", result)
    text = compare_runs(old, new)
    assert "source Generations None → 1" in text
    assert "total context utilization" not in text


@pytest.mark.asyncio
async def test_same_narrow_revision_records_actual_vision_on_off_image_cost(source_root):
    case_id = "story-vision-dialogue-revision"
    off = (await BenchmarkRunner(ROOT, OfflineStoryClient(), source_root).run(profile(), [case_id]))["cases"][0]
    on = (await BenchmarkRunner(ROOT, OfflineStoryClient(), source_root).run(profile(vision=True), [case_id]))["cases"][0]
    assert off["status"] == on["status"] == "completed"
    assert off["storyMetrics"]["selectedRevisionItemCount"] == on["storyMetrics"]["selectedRevisionItemCount"] == 1
    assert off["storyMetrics"]["selectedRevisionScopes"] == on["storyMetrics"]["selectedRevisionScopes"] == ["dialogue"]
    assert off["storyMetrics"]["sourcePictureInputCount"] == 0
    assert on["storyMetrics"]["sourcePictureInputCount"] == 0
    assert on["modelInput"]["images"] == []


async def test_revision_compare_reads_legacy_full_draft_run_without_patch_metric(tmp_path):
    current = await BenchmarkRunner(ROOT, OfflineStoryClient()).run(profile(), ["story-scaling-revision-2"])
    legacy = deepcopy(current)
    legacy["runId"] = "legacy-full-draft"
    case = legacy["cases"][0]
    case["storyMetrics"].pop("responsePatchCount")
    case["rawModelOutput"] = json.dumps(case["structuredStoryPlannerOutput"])
    old = write_run(tmp_path, legacy)
    new = write_run(tmp_path, current)
    comparison = compare_runs(old, new)
    assert "response patch count None → 1" in comparison
    assert "response item count" not in comparison


@pytest.mark.asyncio
async def test_story_seed_provenance_matches_generation_contract():
    accepted = (await BenchmarkRunner(ROOT, OfflineStoryClient(effective_seed=1)).run(
        profile(), ["story-scaling-build-2"]
    ))["cases"][0]["seed"]
    assert accepted == {
        "requested": 1, "mode": "fixed", "effective": 1,
        "status": "accepted_by_provider", "unsupportedFallback": False,
        "knownUnsupported": False, "finalRequestIncludedSeed": True,
    }
    dropped = (await BenchmarkRunner(ROOT, OfflineStoryClient(effective_seed=None, seed_unsupported=True)).run(
        profile(), ["story-scaling-build-2"]
    ))["cases"][0]["seed"]
    assert dropped == {
        "requested": 1, "mode": "fixed", "effective": None,
        "status": "dropped_unsupported", "unsupportedFallback": True,
        "knownUnsupported": False, "finalRequestIncludedSeed": False,
    }
    known = (await BenchmarkRunner(ROOT, OfflineStoryClient(effective_seed=None, seed_known_unsupported=True)).run(
        profile(), ["story-scaling-build-2"]
    ))["cases"][0]["seed"]
    assert known["status"] == "dropped_unsupported" and known["knownUnsupported"]
    assert not known["unsupportedFallback"] and not known["finalRequestIncludedSeed"]


@pytest.mark.asyncio
async def test_total_context_utilization_requires_measured_provider_total_tokens(source_root):
    measured = OfflineStoryClient(usage={"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20})
    result = (await BenchmarkRunner(ROOT, measured, source_root).run(
        profile().model_copy(update={"context_window": 100}), ["story-smoke-three-generation-build"]
    ))["cases"][0]
    assert result["storyMetrics"]["providerTotalTokens"] == 20
    assert result["storyMetrics"]["totalContextUtilization"] == 20.0
    missing = (await BenchmarkRunner(ROOT, OfflineStoryClient(), source_root).run(
        profile().model_copy(update={"context_window": 100}), ["story-smoke-three-generation-build"]
    ))["cases"][0]
    assert missing["storyMetrics"]["providerTotalTokens"] is None
    assert missing["storyMetrics"]["totalContextUtilization"] is None


@pytest.mark.asyncio
async def test_story_longform_suite_runs_all_bounded_operations_offline_and_records_behavioral_evidence(tmp_path):
    cases = load_suite(ROOT, "story-longform").cases
    assert cases == [
        "story-longform-durable-fact", "story-longform-open-thread", "story-longform-thread-resolution",
        "story-longform-changing-fact", "story-longform-returning-subject", "story-longform-irrelevant-subject",
        "story-longform-insert-history", "story-longform-replan-history",
        "story-longform-dialogue-commitment", "story-longform-memory-hygiene",
    ]
    client = OfflineStoryClient()
    result = await BenchmarkRunner(ROOT, client).run(profile(vision=True), cases, "story-longform")
    assert len(client.calls) == len(cases)
    by_id = {item["caseId"]: item for item in result["cases"]}
    assert len(cases) == 10 and sum(bool(item["structuralValid"]) for item in by_id.values()) == 10
    assert all(item["status"] == "completed" and item["machineChecks"]["passed"] for item in by_id.values())
    assert all(item["storyMemoryBefore"] and item["projectedStoryMemoryAfter"]
               and item["memoryHygieneMetrics"] and item["referenceMetrics"]
               and item["behavioralChecks"] for item in by_id.values())

    durable_context = _story_request_context(by_id["story-longform-durable-fact"])
    assert "brass key" not in json.dumps(durable_context["localContext"]).lower()
    assert "brass key" in json.dumps(durable_context["storyMemory"]).lower()
    thread_checks = {check["id"]: check for check in by_id["story-longform-thread-resolution"]["behavioralChecks"]}
    assert thread_checks["document-thread-resolved"]["status"] == "fail"
    assert thread_checks["unrelated-thread-survives"]["status"] == "pass"
    assert thread_checks["unrelated-key-survives"]["status"] == "pass"
    hygiene_checks = {check["id"]: check for check in by_id["story-longform-memory-hygiene"]["behavioralChecks"]}
    assert hygiene_checks["hygiene-ferry-thread-resolved"]["status"] == "fail"
    assert hygiene_checks["hygiene-map-thread-preserved"]["status"] == "pass"
    assert hygiene_checks["hygiene-letter-thread-preserved"]["status"] == "pass"

    changing = by_id["story-longform-changing-fact"]
    assert changing["storyMemoryBefore"]["durableFacts"][0]["text"] == "The pedestrian bridge has collapsed."
    assert changing["behavioralChecks"][0]["status"] == changing["behavioralChecks"][1]["status"] == "pass"

    identity = by_id["story-longform-irrelevant-subject"]["referenceMetrics"]
    assert identity["historicalReferenceSemanticCount"] == 1
    assert identity["transmittedUniquePicturePayloadCount"] == 1
    assert [item["subjectIds"] for item in identity["historicalReferenceCandidates"]] == [["alice"]]
    assert {item["id"] for item in identity["sequenceSubjects"]} == {"alice", "bob"}
    assert {item["subjectId"] for item in identity["relevantSubjects"]} == {"alice"}
    returning = by_id["story-longform-returning-subject"]["referenceMetrics"]
    assert {item["subjectId"] for item in returning["relevantSubjects"]} == {"alice"}
    assert returning["relevantSubjects"][0]["characterProfile"]["personality"] == "Calm and methodical."

    inserted = by_id["story-longform-insert-history"]
    assert inserted["insertApplyProjection"]["proposalOnly"]
    assert inserted["insertApplyProjection"]["storedDownstreamSnapshotsUnchanged"]
    assert all(entry["wouldBeStatus"] == "needs_review"
               for entry in inserted["insertApplyProjection"]["downstreamStatusAfterApply"])

    replanned = by_id["story-longform-replan-history"]
    assert replanned["storyMetrics"]["operation"] == "replan"
    assert all(check["status"] == "pass" for check in replanned["behavioralChecks"][:3])
    assert [entry["targetGenerationId"] for entry in replanned["projectedStoryMemoryAfter"]] == [
        item["existingGenerationId"] for item in replanned["structuredStoryPlannerOutput"]["items"]
    ]

    written = write_run(tmp_path / "runs", result)
    summary = (written / "summary.md").read_text()
    assert "Story behavioral evidence" in summary
    assert "Behavioral checks: PASS=" in summary
    assert "Full raw response, parsed proposal, memory snapshots" in summary
    assert all((written / "cases" / f"{case_id}.json").exists() for case_id in cases)


@pytest.mark.asyncio
async def test_story_longform_specific_resolution_transform_resolves_only_target_obligation():
    def resolve_document(result, _context):
        result["slots"][0]["memoryPatchAfter"] = {
            "resolvedThreadIds": ["22222222-2222-4222-8222-222222222222"]
        }

    result = (await BenchmarkRunner(ROOT, OfflineStoryClient(transform=resolve_document)).run(
        profile(), ["story-longform-thread-resolution"]
    ))["cases"][0]
    checks = {check["id"]: check for check in result["behavioralChecks"]}
    assert checks["document-thread-resolved"]["status"] == "pass"
    assert checks["unrelated-thread-survives"]["status"] == "pass"
    assert checks["unrelated-key-survives"]["status"] == "pass"


def test_story_longform_changing_fact_prompt_is_neutral():
    story = definition("story-longform-changing-fact")["story"]
    prompt = f"{story['storyIdea']} {story['sequenceContext']}".casefold()
    assert "collapsed" not in prompt
    assert "latest trusted" not in prompt
    assert "superseded" not in prompt


@pytest.mark.asyncio
async def test_general_story_smoke_is_profile_agnostic(source_root):
    off = (await BenchmarkRunner(ROOT, OfflineStoryClient(), source_root).run(
        profile(), ["story-smoke-three-generation-build"]
    ))["cases"][0]
    on = (await BenchmarkRunner(ROOT, OfflineStoryClient(), source_root).run(
        profile(vision=True), ["story-smoke-three-generation-build"]
    ))["cases"][0]
    assert off["status"] == on["status"] == "completed"
    assert off["machineChecks"]["passed"] and on["machineChecks"]["passed"]
    assert off["storyMetrics"]["sourcePictureInputCount"] == 0
    assert on["storyMetrics"]["sourcePictureInputCount"] == 1
