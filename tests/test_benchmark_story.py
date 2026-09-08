from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct
from uuid import uuid4
import zlib

import httpx
import pytest
from pydantic import ValidationError

from miniconstruct import story_planning as service
from miniconstruct.benchmark.recording import SingleAttemptClient
from miniconstruct.benchmark.runner import (
    BenchmarkCase, BenchmarkCheck, BenchmarkRunner, _load_case_definition,
    _messages_provenance, load_case, load_planner_case, load_suite, write_run,
)
from miniconstruct.benchmark.story import (
    DeterministicStoryProjectStore, evaluate_story_checks, load_story_workspaces,
    materialize_story_project, revision_request,
)
from miniconstruct.llm.client import LLMBackendError, OpenAICompatibleClient
from miniconstruct.models.story_planning import StoryBuildRequest, StoryPlanProposal
from miniconstruct.models.workspace import Workspace
from miniconstruct.project_store import ProjectStore
from test_benchmark_planner import profile


ROOT = Path(__file__).resolve().parents[1] / "benchmarks"
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
    def __init__(self, transform=None, wrapper=None):
        self.transform, self.wrapper = transform, wrapper
        self.calls = []
        self.last_usage = {"prompt_tokens": 12, "completion_tokens": 8}

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
        result = deepcopy(context["draft"])
        result.setdefault("sequenceContext", "")
        result.setdefault("consistencyWarnings", [])
        for index, item in enumerate(result["items"]):
            for field in ("creativeRequest", "incomingState", "intendedOutgoingState", "cameraIntent"):
                item.setdefault(field, "")
            item.setdefault("relationshipToPrevious", "independent" if index else None)
            item.setdefault("referenceRecommendations", [])
        if "editableScopes" in context:
            # Deliberately disobey scope in raw output: production must project it.
            result["sequenceContext"] = "Harmless model paraphrase to discard."
            for item in result["items"]:
                for field in ("creativeRequest", "incomingState", "intendedOutgoingState", "cameraIntent"):
                    item[field] = "Model revised " + field
                if item["relationshipToPrevious"] is not None:
                    item["relationshipToPrevious"] = "continue_previous_shot"
        else:
            result["sequenceContext"] = result["sequenceContext"] or "Coherent fictional sequence."
            for item in result["items"]:
                for field in ("creativeRequest", "incomingState", "intendedOutgoingState", "cameraIntent"):
                    item[field] = item[field] or f"Planned {field}."
            if "no cut" in context["idea"]:
                result["items"][1]["relationshipToPrevious"] = "continue_previous_shot"
        if self.transform:
            self.transform(result, context)
        if "editableScopes" not in context:
            for key in ("baseProjectId", "baseProjectUpdatedAt", "baseSequenceFingerprint"):
                result.pop(key, None)
            for item in result["items"]:
                item.pop("existingGenerationId")
        raw = json.dumps(result)
        return self.wrapper(raw) if self.wrapper else raw


def definition(case_id):
    return json.loads((ROOT / "cases" / case_id / "case.json").read_text())


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
    lambda r: r["draft"]["items"][1].pop("creativeRequest"),
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
            "variations": 1, "reasoning": "off", "transport": "non_streaming",
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
    assert context["draft"]["baseProjectUpdatedAt"] == "2020-01-01T00:00:00.000Z"
    assert context["draft"]["baseProjectId"] == _story_request_context(second)["draft"]["baseProjectId"]
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
    assert all(base64.b64decode(p["image_url"]["url"].split(",")[1]) == PNG for p in images)
    assert {entry["fixturePath"] for entry in case["referenceInputs"]} == {"assets/" + name for name in names}
    assert all(e["fixtureSha256"] == hashlib.sha256(PNG).hexdigest() for e in case["referenceInputs"])
    assert all(e["sha256"] == hashlib.sha256(PNG).hexdigest() for e in case["modelInput"]["images"])
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


async def test_revision_effective_output_discards_raw_paraphrases_and_generates_boundary_warnings():
    ids = ["story-revision-camera-multiselect", "story-revision-outgoing-warning", "story-revision-transition-reframe"]
    run = await BenchmarkRunner(ROOT, OfflineStoryClient()).run(profile(), ids)
    for result in run["cases"]:
        assert result["machineChecks"]["passed"]
        final, before = result["structuredStoryPlannerOutput"], result["initialStoryDraft"]
        assert json.loads(result["rawModelOutput"])["sequenceContext"] != final["sequenceContext"]
        assert final["sequenceContext"] == before["sequenceContext"]
        assert final["items"][1]["creativeRequest"] == before["items"][1]["creativeRequest"]
        assert "removes one wet glove" in final["items"][1]["creativeRequest"]
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
        result["items"][1]["creativeRequest"] = "I heard the audio and I watched the video."
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
    assert check("story_field_unchanged", item=2, field="creativeRequest")
    assert not check("story_field_changed", item=2, field="creativeRequest")
    assert check("story_field_changed", item=2, field="intendedOutgoingState")
    assert check("story_selected_fields_only_changed")
    assert check("story_warning_exists", item=2, relatedItem=3, direction="downstream")
    assert not check("story_warning_exists", item=2, relatedItem=3, direction="upstream")
    assert not check("story_warning_exists", item=2, relatedItem=4, direction="downstream")
    assert not check("story_field_not_empty", item=12, field="creativeRequest")
    plan.items[2].camera_intent = "Illegal unselected edit."
    assert not check("story_selected_fields_only_changed")
    plan.items[0].creative_request = "Illegal authority edit."
    assert not check("story_field_unchanged", item=1, field="creativeRequest")
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
        result["items"][0]["creativeRequest"] = "Illegally rewritten author field."
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
