from __future__ import annotations

import json
import hashlib
import io
from pathlib import Path
import shutil

import httpx
import pytest
from PIL import Image

from miniconstruct.benchmark.runner import (
    BenchmarkProfile,
    BenchmarkRunner,
    compare_runs,
    load_case,
    load_profiles,
    load_suite,
    write_run,
)
from miniconstruct.llm.client import LLMBackendError
from miniconstruct.llm.client import OpenAICompatibleClient
from miniconstruct.llm.compatibility import build_generation_payload
from miniconstruct.h3.builder import assemble_prompt


ROOT = Path(__file__).resolve().parents[1] / "benchmarks"

VALID_PROMPT = """integrated_multimodal_description:
[Shot 1] A cyclist crosses the wet street. <d>[English] Don't touch that!</d>
overall_soundscape:
Rain and distant traffic.
non_diegetic_music:
None.
"""


class FakeClient:
    instances: list["FakeClient"] = []

    def __init__(self, settings):
        self.settings = settings
        self.seed_unsupported = False
        self.effective_seed = settings.seed
        self.compatibility_fallback = False
        self.last_usage = {"prompt_tokens": 10, "completion_tokens": 5}
        self.last_payload = None
        self.messages = []
        type(self).instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def generate(self, messages):
        self.messages = messages
        return VALID_PROMPT


class UnsupportedSeedClient(FakeClient):
    async def generate(self, messages):
        await super().generate(messages)
        self.seed_unsupported = True
        self.effective_seed = None
        return VALID_PROMPT


class FailFirstClient(FakeClient):
    calls = 0

    async def generate(self, messages):
        type(self).calls += 1
        if type(self).calls == 1:
            raise LLMBackendError("offline", 502)
        return await super().generate(messages)


class EffectivePayloadClient(FakeClient):
    async def generate(self, messages):
        await super().generate(messages)
        payload, _ = build_generation_payload(self.settings, messages, stream=False)
        payload.pop("seed")
        payload["max_completion_tokens"] = payload.pop("max_tokens")
        payload["chat_template_kwargs"] = {"thinking": False}
        self.last_payload = payload
        self.effective_seed = None
        self.seed_known_unsupported = True
        return VALID_PROMPT


class DialogueResolverBenchmarkClient(FakeClient):
    resolver_calls = 0
    h3_calls = 0

    async def generate(self, messages, *, response_instruction=None):
        self.messages = messages
        if response_instruction is not None:
            type(self).resolver_calls += 1
            return "Kent: 早く逃げよう。\nMira: すぐ後ろにいる。"
        type(self).h3_calls += 1
        assert "Kent: 早く逃げよう。" in json.dumps(messages, ensure_ascii=False)
        assert "We need to get out before the tunnel collapses." not in json.dumps(messages, ensure_ascii=False)
        return "integrated_multimodal_description:\n[Shot 1] Kent and Mira run, <d>[Japanese] Kent: 早く逃げよう。 Mira: すぐ後ろにいる。</d>\noverall_soundscape:\nQuiet.\nnon_diegetic_music:\nNone.\n"


def profile(*, vision: bool = False) -> BenchmarkProfile:
    return BenchmarkProfile.model_validate({
        "id": "fake", "displayName": "Fake", "modelId": "writer", "supportsVision": vision,
        "endpoint": {"id": "fake-endpoint", "displayName": "Fake endpoint", "baseUrl": "http://fake.test/v1"},
    })


def test_loads_real_workspace_case_and_suite_definitions():
    case, workspace, _, _ = load_case(ROOT, "exact-dialogue-basic")
    suite = load_suite(ROOT, "smoke")
    assert case.id == "exact-dialogue-basic"
    assert workspace.creative_request
    assert suite.cases == ["basic-t2va", "exact-dialogue-basic"]


def test_malformed_case_definition_is_rejected_clearly(tmp_path):
    case_dir = tmp_path / "cases" / "broken"
    case_dir.mkdir(parents=True)
    (case_dir / "case.json").write_text('{"schemaVersion": 1, "id": "broken"}', encoding="utf-8")
    (case_dir / "workspace.json").write_text('{"creativeRequest": "x"}', encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid benchmark case broken"):
        load_case(tmp_path, "broken")


def test_profiles_are_secret_free_and_make_existing_llm_settings():
    profiles = load_profiles(ROOT / "models.example.json")
    settings = profiles["qwen38"].to_settings()
    assert settings.temperature == 0.4
    assert settings.fixed_seed == 1 and settings.seed_mode.value == "fixed"
    assert settings.endpoint.api_key is None


@pytest.mark.asyncio
async def test_benchmark_uses_production_assembly_and_records_provenance():
    FakeClient.instances.clear()
    result = await BenchmarkRunner(ROOT, FakeClient).run(profile(), ["exact-dialogue-basic"])
    item = result["cases"][0]
    assert item["status"] == "completed"
    assert item["seed"] == {"requested": 1, "mode": "fixed", "effective": 1, "status": "accepted_by_provider", "unsupportedFallback": False, "knownUnsupported": False, "finalRequestIncludedSeed": True}
    assert item["structuralValid"]
    assert item["expectations"]["passed"]
    assert "MiniConstruct core operating instructions" in item["modelInput"]["system"]
    assert "Don't touch that!" in item["modelInput"]["user"]
    assert "Repair Format" not in item["modelInput"]["system"]
    assert FakeClient.instances[0].settings.temperature == 0.4
    assert FakeClient.instances[0].settings.seed == 1
    assert [message["role"] for message in FakeClient.instances[0].messages] == ["system", "user"]


@pytest.mark.asyncio
async def test_manual_dialogue_benchmark_uses_production_resolver_and_cache():
    DialogueResolverBenchmarkClient.resolver_calls = 0
    DialogueResolverBenchmarkClient.h3_calls = 0
    result = await BenchmarkRunner(ROOT, DialogueResolverBenchmarkClient).run(
        profile(), ["dialogue-language-manual-en-ja"],
    )
    item = result["cases"][0]
    evidence = item["dialogueResolution"]
    assert evidence == {
        "sourceDialogue": "Kent: We need to get out before the tunnel collapses.\nMira: I'm right behind you.",
        "resolvedDialogue": "Kent: 早く逃げよう。\nMira: すぐ後ろにいる。",
        "targetLanguage": "ja",
        "resolverInvoked": True,
        "cacheReused": True,
    }
    assert DialogueResolverBenchmarkClient.resolver_calls == 1
    assert DialogueResolverBenchmarkClient.h3_calls == 1
    assert "Kent: 早く逃げよう。" in item["modelInput"]["user"]
    assert "We need to get out before the tunnel collapses." not in item["modelInput"]["user"]


@pytest.mark.asyncio
async def test_mocked_benchmark_path_includes_creative_policy_and_controls():
    FakeClient.instances.clear()
    result = await BenchmarkRunner(ROOT, FakeClient).run(
        profile(vision=True), ["ref2va-creative-inference-expansive"],
    )
    assert result["cases"][0]["status"] == "completed"
    system, user = FakeClient.instances[0].messages
    assert "Creative Interpretation: Expansive" in system["content"]
    user_text = user["content"][0]["text"]
    assert "Non-diegetic music: forbidden." in user_text
    assert "Subject Identity Fidelity (strict; anchors: Picture 1)" in user_text


@pytest.mark.asyncio
async def test_unsupported_seed_fallback_is_recorded_without_claiming_determinism():
    result = await BenchmarkRunner(ROOT, UnsupportedSeedClient).run(profile(), ["basic-t2va"])
    item = result["cases"][0]
    assert item["seed"]["effective"] is None
    assert item["seed"]["status"] == "dropped_unsupported"
    assert item["seed"]["unsupportedFallback"]
    assert "does not support the seed parameter" in item["warnings"][0]


@pytest.mark.asyncio
async def test_effective_request_and_known_seed_omission_are_recorded_without_secrets():
    result = await BenchmarkRunner(ROOT, EffectivePayloadClient).run(profile(), ["basic-t2va"])
    item = result["cases"][0]
    assert item["effectiveRequest"] == {
        "model": "writer", "temperature": 0.4, "max_completion_tokens": 4096,
        "stream": False, "chat_template_kwargs": {"thinking": False},
    }
    assert item["seed"]["status"] == "dropped_unsupported"
    assert item["seed"]["knownUnsupported"] and not item["seed"]["finalRequestIncludedSeed"]
    serialized = json.dumps(item)
    assert "data:image/" not in serialized and "Authorization" not in serialized


@pytest.mark.asyncio
async def test_actual_transmitted_image_hashes_are_ordered_and_unsent_assets_are_excluded(tmp_path):
    root = tmp_path / "benchmarks"
    case_dir = root / "cases" / "vision-input"
    (case_dir / "assets").mkdir(parents=True)
    output = io.BytesIO()
    Image.new("RGB", (1536, 1536), (40, 80, 120)).save(output, "PNG")
    image_bytes = output.getvalue()
    (case_dir / "assets" / "reference.png").write_bytes(image_bytes)
    (case_dir / "case.json").write_text(json.dumps({
        "schemaVersion": 1, "id": "vision-input", "title": "Vision input",
        "fixtureAssets": [{"assetId": "image-1", "path": "assets/reference.png"}],
    }), encoding="utf-8")
    (case_dir / "workspace.json").write_text(json.dumps({
        "schemaVersion": 1, "mode": "Ref2VA", "durationSeconds": 6, "variations": 1,
        "creativeRequest": "Use the reference.",
        "assets": [{"id": "image-1", "kind": "image", "filename": "reference.png", "mimeType": "image/png", "role": "subject_identity", "image": {"data_url": "data:image/png;base64,AA=="}}],
    }), encoding="utf-8")
    sent = (await BenchmarkRunner(root, FakeClient).run(profile(vision=True), ["vision-input"]))["cases"][0]
    image = sent["modelInput"]["images"][0]
    assert image["index"] == 1 and image["mimeType"] == "image/jpeg"
    assert image["sha256"] != hashlib.sha256(image_bytes).hexdigest()
    assert {key: image[key] for key in ("assetId", "filename", "fixturePath")} == {
        "assetId": "image-1", "filename": "reference.png", "fixturePath": "assets/reference.png"}
    assert "data:image/" not in json.dumps(sent)
    unsent = (await BenchmarkRunner(root, FakeClient).run(profile(vision=False), ["vision-input"]))["cases"][0]
    assert unsent["referenceInputs"] and unsent["modelInput"]["images"] == []


def test_streaming_and_benchmark_preparation_share_messages_and_generation_settings():
    _, workspace, _, _ = load_case(ROOT, "basic-t2va")
    settings = profile().to_settings().model_copy(update={"seed": 1})
    assembled = assemble_prompt(workspace, settings.supports_vision)
    non_streaming, _ = build_generation_payload(settings, assembled.messages, stream=False)
    streaming, _ = build_generation_payload(settings, assembled.messages, stream=True)
    for key in ("model", "messages", "temperature", "max_tokens", "seed"):
        assert streaming[key] == non_streaming[key]
    assert streaming["stream"] is True and non_streaming["stream"] is False
    assert "stream_options" not in non_streaming and streaming["stream_options"] == {"include_usage": True}


@pytest.mark.asyncio
async def test_previously_known_unsupported_seed_is_never_accepted():
    endpoint = "http://seed-known.test/v1"
    known_key = (endpoint, "writer")
    OpenAICompatibleClient._unsupported_seed_endpoints.add(known_key)
    try:
        def handler(request: httpx.Request) -> httpx.Response:
            assert "seed" not in json.loads(request.content)
            return httpx.Response(200, json={"choices": [{"message": {"content": VALID_PROMPT}}]})
        seeded_profile = profile().model_copy(update={"endpoint": profile().endpoint.model_copy(update={"base_url": endpoint})})
        result = await BenchmarkRunner(ROOT, lambda settings: OpenAICompatibleClient(settings, httpx.MockTransport(handler))).run(seeded_profile, ["basic-t2va"])
        seed = result["cases"][0]["seed"]
        assert seed["status"] == "dropped_unsupported"
        assert seed["knownUnsupported"] and not seed["finalRequestIncludedSeed"]
    finally:
        OpenAICompatibleClient._unsupported_seed_endpoints.discard(known_key)


@pytest.mark.asyncio
async def test_vision_requirement_skips_and_provider_failure_does_not_stop_suite(tmp_path):
    copied_root = tmp_path / "benchmarks"
    shutil.copytree(ROOT, copied_root)
    vision_case = copied_root / "cases" / "basic-t2va" / "case.json"
    original = json.loads(vision_case.read_text(encoding="utf-8"))
    changed = dict(original)
    changed["requirements"] = {"vision": True}
    vision_case.write_text(json.dumps(changed), encoding="utf-8")
    skipped = await BenchmarkRunner(copied_root, FakeClient).run(profile(vision=False), ["basic-t2va"])
    assert skipped["cases"][0]["status"] == "skipped"
    FailFirstClient.calls = 0
    result = await BenchmarkRunner(ROOT, FailFirstClient).run(profile(), ["basic-t2va", "exact-dialogue-basic"])
    assert [item["status"] for item in result["cases"]] == ["error", "completed"]


def test_results_are_written_only_under_the_run_directory_and_compare(tmp_path):
    result = {
        "schemaVersion": 1, "runId": "run-a", "profile": {"id": "fake"},
        "cases": [{"caseId": "one", "status": "completed", "structuralValid": True, "expectations": {"passed": True}, "validation": {"findings": []}, "provider": {"requestDurationMs": 1}}],
    }
    destination = write_run(tmp_path, result)
    assert destination == tmp_path.resolve() / "runs" / "run-a"
    assert (destination / "run.json").exists() and (destination / "cases" / "one.json").exists()
    later = dict(result, runId="run-b")
    later["cases"] = [{**result["cases"][0], "structuralValid": False}]
    second = write_run(tmp_path, later)
    assert "validity" in compare_runs(destination, second)
