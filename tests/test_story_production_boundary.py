"""Offline DogTest replay and the real Story -> HTTP client -> route boundaries."""
from copy import deepcopy
import base64
import json
from pathlib import Path
import struct
import zlib

from fastapi.testclient import TestClient
import httpx
import pytest

from miniconstruct.api import routes
from miniconstruct.app import app
from miniconstruct.llm.client import OpenAICompatibleClient
from miniconstruct.models.story_planning import LegacyStoryBuildResponse, StoryBuildRequest, StoryPlanProposal
from miniconstruct.models.workspace import validate_authoring_workspace
from miniconstruct.project_store import ProjectStore, _blank_workspace
from miniconstruct.story_planning import (
    build_story_plan, initial_story_draft, normalize_story_model_json, validate_build_authority,
)


CAPTURE = Path(__file__).parent / "fixtures/story/dogtest-gemma-reply.json"
PROJECT_ID = "474063a0-94e3-4e98-9b6a-a683db422b21"
GENERATION_ID = "031450e8-8960-49bb-a282-bcc19dd3764e"
TIMESTAMP = "2026-09-06T14:14:22.291Z"
IDEA = "A cyclist finds a lost dog and brings it home. Exact idea — 雨."
SECRET = "secret-must-never-appear"


def png(red):
    def chunk(kind, value):
        return struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind + value))
    payload = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
               + chunk(b"IDAT", zlib.compress(bytes([0, red, 0, 0]))) + chunk(b"IEND", b""))
    return "data:image/png;base64," + base64.b64encode(payload).decode()


class DogStore(ProjectStore):
    def _new_project_id(self):
        return PROJECT_ID

    def _new_generation_id(self):
        if not getattr(self, "_created_first", False):
            self._created_first = True
            return GENERATION_ID
        return super()._new_generation_id()

    def _now(self):
        return TIMESTAMP


@pytest.fixture
def dog(tmp_path):
    store = DogStore(tmp_path)
    data = _blank_workspace(PROJECT_ID, "DogTest").model_dump(mode="json", by_alias=True)
    raw = json.loads(CAPTURE.read_text())
    refs = raw["items"][0]["referenceRecommendations"]
    data.update(mode="Ref2VA", durationSeconds=12, subjects=[
        {"id": "subject-1", "number": 1}, {"id": "subject-2", "number": 2},
    ], assets=[{
        "id": ref["assetId"], "kind": "image", "filename": f"synthetic-{index}.png",
        "mimeType": "image/png", "role": "subject_identity", "attached": True, "order": index,
        "subjectIdentity": {"subjectId": f"subject-{index + 1}",
                            "view": "three_quarter" if index == 0 else "unspecified"},
        "image": {"data_url": png(index * 255), "width": 1, "height": 1},
    } for index, ref in enumerate(refs)])
    store.create_project(validate_authoring_workspace(data))
    request = StoryBuildRequest(idea=IDEA, desiredGenerationCount=4, llm={
        "endpoint": {"id": "offline", "displayName": "Offline", "baseUrl": "http://never-call.test/v1",
                     "apiKey": SECRET, "source": "unsloth_studio"},
        "modelId": "offline", "supportsVision": True, "storyPlannerVision": True,
    })
    # Synthetic images necessarily change the canonical manifest hash. No identity,
    # narrative, warning or recommendation in the captured response is otherwise changed.
    raw["baseSequenceFingerprint"] = store.read_story_snapshot(PROJECT_ID).fingerprint
    for item in raw["items"]:
        item.setdefault("shotCount", 1)
    return store, request, raw


def compact(raw):
    data = deepcopy(raw)
    for key in ("baseProjectId", "baseProjectUpdatedAt", "baseSequenceFingerprint"):
        data.pop(key)
    for item in data["items"]:
        item.pop("existingGenerationId")
        item.setdefault("shotCount", 1)
    return data


def identities():
    return iter(item["planItemId"] for item in json.loads(CAPTURE.read_text())["items"])


def files(store):
    return {p.relative_to(store.root): p.read_bytes() for p in store.root.rglob("*") if p.is_file()}


def test_literal_gemma_reply_passes_original_schema_and_authority(dog):
    store, _, _ = dog
    raw = CAPTURE.read_text()
    parsed = StoryPlanProposal.model_validate(normalize_story_model_json(raw))
    snapshot = store.read_story_snapshot(PROJECT_ID)
    ids = identities()
    initial = initial_story_draft(snapshot, 4, lambda: next(ids))
    # Evaluate the literal captured envelope, with synthetic media outside that check.
    initial.base_sequence_fingerprint = parsed.base_sequence_fingerprint
    validate_build_authority(snapshot, initial, parsed)
    assert parsed.items[0].scene_plan == parsed.items[0].camera_intent == ""


@pytest.mark.parametrize("small_contract", [False, True])
async def test_dogtest_real_client_payload_reply_and_apply(dog, small_contract):
    store, request, raw = dog
    captured = []
    before = files(store)
    original = store.read_story_snapshot(PROJECT_ID).generations[0].workspace.model_dump()

    def endpoint(http_request):
        captured.append(json.loads(http_request.content))
        return httpx.Response(200, json={"choices": [{"message": {
            "content": json.dumps(compact(raw) if small_contract else raw),
        }}]})

    ids = identities()
    result = await build_story_plan(store, PROJECT_ID, request,
        lambda settings: OpenAICompatibleClient(settings, httpx.MockTransport(endpoint)),
        item_id_factory=lambda: next(ids))
    assert files(store) == before
    assert len(captured) == 1
    payload = captured[0]
    assert payload["stream"] is False
    parts = payload["messages"][1]["content"]
    context = json.loads(parts[0]["text"])
    assert context["idea"] == IDEA and context["desiredGenerationCount"] == 4
    assert context["responseSchema"] == LegacyStoryBuildResponse.model_json_schema(by_alias=True)
    assert "tonePerformance" not in json.dumps(context["responseSchema"])
    assert "..." not in parts[0]["text"]
    assert "base64" not in parts[0]["text"] and "data_url" not in parts[0]["text"]
    assert len([p for p in parts if p["type"] == "image_url"]) == 2
    for index, asset in enumerate(context["sourceGenerations"][0]["assets"]):
        assert "image" not in asset and asset["imageAvailableForVision"]
        assert asset["subjectIdentity"]["subjectId"] == f"subject-{index + 1}"
        assert GENERATION_ID in parts[1 + 2 * index]["text"]
        assert asset["id"] in parts[1 + 2 * index]["text"]
        assert parts[2 + 2 * index]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert set(context["draft"]["items"][0]) == {"planItemId", "existingGenerationId", "relationshipToPrevious", "durationSeconds", "shotCount"}
    assert all(
        set(i) == {"planItemId", "existingGenerationId", "durationSeconds", "shotCount"}
        for i in context["draft"]["items"][1:]
    )
    assert all(len(i.reference_recommendations) == 2 for i in result.items)
    assert result.consistency_warnings[0].model_dump(by_alias=True) == raw["consistencyWarnings"][0]
    warning = result.consistency_warnings[-1]
    assert warning.plan_item_id == warning.related_plan_item_id == result.items[0].plan_item_id
    assert "Scene Plan" in warning.message
    assert result.items[0].scene_plan == result.items[0].incoming_state == ""
    assert SECRET not in result.model_dump_json() and "base64" not in result.model_dump_json()
    assert result.base_sequence_fingerprint == raw["baseSequenceFingerprint"]
    # The unchanged transactional Apply can accept the editable incomplete draft.
    applied = store.apply_story_plan(PROJECT_ID, result)
    assert applied.generation_count == 4
    assert store.read_story_snapshot(PROJECT_ID).generations[0].workspace.model_dump() == original
    for generation in store.read_story_snapshot(PROJECT_ID).generations[1:]:
        assert generation.workspace.mode == "Ref2VA"
        assert len(generation.workspace.assets) == 2
        assert generation.workspace.subjects == applied.workspace.subjects


async def test_legacy_story_build_salvages_unknown_memory_patch_extras(dog):
    store, request, raw = dog
    response = compact(raw)
    for item in response["items"]:
        item["memoryPatchAfter"] = {"field": "invented value"}

    class OfflineClient:
        def __init__(self):
            self.calls = []

        def __call__(self, _settings):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def generate(self, messages, *, response_instruction=None):
            self.calls.append(messages)
            return json.dumps(response)

    client = OfflineClient()
    ids = identities()
    proposal = await build_story_plan(store, PROJECT_ID, request, client, item_id_factory=lambda: next(ids))
    assert len(client.calls) == 1
    assert any("Generations 1" in warning.message and "field" in warning.message
               for warning in proposal.consistency_warnings)


def post_build(monkeypatch, dog, handler, *, request_patch=None):
    store, request, _ = dog
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(store.root))

    async def real_service(s, pid, req):
        ids = identities()
        return await build_story_plan(s, pid, req,
            lambda settings: OpenAICompatibleClient(settings, httpx.MockTransport(handler)),
            item_id_factory=lambda: next(ids))

    monkeypatch.setattr(routes, "build_story_plan", real_service)
    body = request.model_dump(mode="json", by_alias=True)
    body["llm"]["endpoint"]["apiKey"] = SECRET
    if request_patch:
        request_patch(body)
    with TestClient(app) as client:
        return client.post(f"/api/projects/{PROJECT_ID}/story-plan/build", json=body)


@pytest.mark.parametrize("failure,status,category", [
    ("json", 502, "story_json"), ("array", 502, "story_json"),
    ("schema", 502, "story_schema"), ("hash", 502, "story_identity"),
    ("hash_shape", 502, "story_schema"), ("slots", 502, "story_identity"),
    ("relationship", 502, "story_schema"), ("warning", 502, "story_schema"),
    ("http", 502, "provider_http"), ("upstream504", 502, "provider_http"),
    ("connection", 502, "provider_connection"), ("wire_json", 502, "provider_response"),
    ("choices", 502, "provider_response"),
    ("read", 504, "provider_timeout"), ("connect", 504, "provider_timeout"),
    ("write", 504, "provider_timeout"), ("pool", 504, "provider_timeout"),
    ("stale", 409, "stale_snapshot"),
])
def test_route_classifies_failures_without_payloads(dog, monkeypatch, caplog, failure, status, category):
    store, _, raw = dog
    before = files(store)
    calls = []
    leak = SECRET + png(0) + png(0).split(",")[1]

    def endpoint(request):
        calls.append(request)
        output = compact(raw)
        if failure in {"read", "connect", "write", "pool"}:
            cls = {"read": httpx.ReadTimeout, "connect": httpx.ConnectTimeout,
                   "write": httpx.WriteTimeout, "pool": httpx.PoolTimeout}[failure]
            raise cls(leak, request=request)
        if failure == "connection":
            raise httpx.ConnectError(leak, request=request)
        if failure in {"http", "upstream504"}:
            return httpx.Response(504 if failure == "upstream504" else 500, text=leak)
        if failure == "wire_json":
            return httpx.Response(200, text=leak)
        if failure == "choices":
            return httpx.Response(200, json={"choices": []})
        if failure == "schema": output["items"][0]["cameraIntent"] = {leak: leak}
        if failure.startswith("hash"):
            output = deepcopy(raw)
            output["baseSequenceFingerprint"] = "0" * (63 if failure == "hash_shape" else 64)
        if failure == "slots": output["items"].reverse()
        if failure == "relationship": output["items"][1]["relationshipToPrevious"] = None
        if failure == "warning": output["consistencyWarnings"][0]["planItemId"] = leak
        if failure == "stale":
            path = next(store.root.rglob("project.json"))
            manifest = json.loads(path.read_text())
            # A same-timestamp persisted edit must still conflict.
            manifest["sequence"]["generations"][0]["workspace"]["dialogue"] = "Saved during model call"
            path.write_text(json.dumps(manifest))
        content = "{broken " + leak if failure == "json" else "[]" if failure == "array" else json.dumps(output)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    response = post_build(monkeypatch, dog, endpoint)
    assert response.status_code == status, response.text
    assert len(calls) == 1  # No automatic retry of malformed output or transient failures.
    assert f"category={category}" in caplog.text
    assert f"status={status}" in caplog.text
    for sensitive in (SECRET, png(0), png(0).split(",")[1]):
        assert sensitive not in caplog.text + response.text
    if failure != "stale":
        assert files(store) == before


@pytest.mark.parametrize("mode,status", [("bad_count", 422), ("missing_model", 400), ("first_relationship", 400)])
def test_request_failures_precede_provider(dog, monkeypatch, mode, status):
    store, _, _ = dog
    if mode == "first_relationship":
        path = next(store.root.rglob("project.json"))
        data = json.loads(path.read_text())
        data["sequence"]["generations"][0]["planning"]["relationshipToPrevious"] = "independent"
        path.write_text(json.dumps(data))

    def patch(body):
        if mode == "bad_count": body["desiredGenerationCount"] = 1
        if mode == "missing_model": body["llm"]["modelId"] = ""

    def forbidden(_):
        pytest.fail("request validation must not dispatch")

    assert post_build(monkeypatch, dog, forbidden, request_patch=patch).status_code == status


@pytest.mark.parametrize("timeout", [120, 360])
def test_configured_timeout_and_compatibility_fallback_preserve_complete_payload(dog, monkeypatch, timeout):
    _, _, raw = dog
    calls = []

    def endpoint(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert request.extensions["timeout"] == dict(connect=timeout, read=timeout, write=timeout, pool=timeout)
        if len(calls) == 1:
            return httpx.Response(400, json={"error": "Unsupported parameter chat_template_kwargs"})
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(compact(raw))}}]})

    response = post_build(monkeypatch, dog, endpoint,
                          request_patch=lambda body: body["llm"].update(timeoutSeconds=timeout))
    assert response.status_code == 200, response.text
    assert len(calls) == 2 and calls[0]["messages"] == calls[1]["messages"]
    assert "chat_template_kwargs" in calls[0] and "chat_template_kwargs" not in calls[1]
    assert json.loads(calls[1]["messages"][1]["content"][0]["text"])["idea"] == IDEA


def test_small_contract_projects_all_saved_authority_without_model_envelope(dog, monkeypatch):
    store, _, raw = dog
    workspace = store.load_project(PROJECT_ID).workspace
    store.save_project(PROJECT_ID, workspace.model_copy(update={"creative_request": "  Exact first shot.\n"}),
                       sequence_context="  Exact world.\n", incoming_state="Exact start",
                       intended_outgoing_state="Exact ending", planning={"cameraIntent": "Exact camera"})
    second = store.create_generation(PROJECT_ID)
    store.save_project(PROJECT_ID, second.workspace,
                       planning={"relationshipToPrevious": "continue_previous_shot"})
    snapshot = store.read_story_snapshot(PROJECT_ID)
    before = files(store)
    proposed = compact(raw)
    proposed["sequenceContext"] = "Model paraphrase"
    proposed["items"][0].update(scenePlan="Model rewrite", incomingState="Changed",
                                intendedOutgoingState="Changed", cameraIntent="Changed",
                                relationshipToPrevious="independent")
    proposed["items"][1]["relationshipToPrevious"] = None

    response = post_build(monkeypatch, dog, lambda _: httpx.Response(200, json={
        "choices": [{"message": {"content": json.dumps(proposed)}}],
    }))
    assert response.status_code == 200, response.text
    result = StoryPlanProposal.model_validate(response.json())
    assert files(store) == before
    assert result.sequence_context == "  Exact world.\n"
    assert result.items[0].scene_plan == "Model rewrite"
    assert result.items[0].incoming_state == "Exact start"
    assert result.items[0].intended_outgoing_state == "Exact ending"
    assert result.items[0].camera_intent == "Exact camera"
    assert result.items[0].relationship_to_previous is None
    assert result.items[1].relationship_to_previous == "continue_previous_shot"
    assert result.items[1].scene_plan == raw["items"][1]["scenePlan"]
    assert [i.existing_generation_id for i in result.items] == [GENERATION_ID, second.active_generation_id, None, None]
    assert result.base_project_id == PROJECT_ID and result.base_project_updated_at == snapshot.manifest.updated_at
    assert result.base_sequence_fingerprint == snapshot.fingerprint


def test_incomplete_warnings_respect_public_limit_without_filling_text(dog, monkeypatch):
    _, _, raw = dog
    proposed = compact(raw)
    proposed["sequenceContext"] = "  "
    proposed["consistencyWarnings"] *= 256
    for item in proposed["items"]:
        for key in ("scenePlan", "incomingState", "intendedOutgoingState", "cameraIntent"):
            item[key] = " \n "
    response = post_build(monkeypatch, dog, lambda _: httpx.Response(200, json={
        "choices": [{"message": {"content": json.dumps(proposed)}}],
    }))
    assert response.status_code == 200, response.text
    result = StoryPlanProposal.model_validate(response.json())
    assert len(result.consistency_warnings) == 256
    assert "Scene Plan" in result.consistency_warnings[-4].message
    assert all(i.scene_plan == " \n " for i in result.items)
    assert [w.plan_item_id for w in result.consistency_warnings[-4:]] == [i.plan_item_id for i in result.items]
