from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from miniconstruct.benchmark.runner import BenchmarkRunner, load_case
from miniconstruct.models.workspace import ReferenceLayout


ROOT = Path(__file__).resolve().parents[1] / "benchmarks"
CASE_ID = "ref2va-height-comparison"
CASE_DIR = ROOT / "cases" / CASE_ID

EXPECTED_FIXTURES = {
    "a46fa829-2b96-43f3-8e70-9e1b08d0b824": ("assets/subject-1-reference-sheet.jpg", "image/jpeg", "06d9d07d9b62a96fe99009462bdd2c8838d31e513f3d5f02212eeb396646601b"),
    "b9059e49-58b0-4d86-895e-17bc5c2f9552": ("assets/subject-2-reference-sheet.jpg", "image/jpeg", "e441aa5056af889b27500b9f223f8c73320461c8d9ca3bc117eaeb0d6127dd19"),
    "1293518a-f858-45e5-8e57-b075d83c7969": ("assets/height-scale-comparison.jpg", "image/jpeg", "0e56cbaab55817db697569eadfed9d234bd627a0430151689d4e675b94c96b28"),
}


def test_height_case_preserves_real_workspace_semantics_and_fixture_bytes():
    case, workspace, case_dir, _ = load_case(ROOT, CASE_ID)

    assert case.requirements.vision is True
    assert len(case.fixture_assets) == 3
    assert workspace.mode.value == "Ref2VA"
    assert workspace.duration_seconds == 7
    assert workspace.variations == 1
    assert workspace.shots is None
    assert workspace.aspect_ratio == "auto"
    assert workspace.creative_controls.music.mode.value == "off"
    assert workspace.creative_controls.subject_identity_fidelity.level.value == "strict"
    assert [asset.order for asset in workspace.assets] == [0, 1, 2]
    assert [asset.role for asset in workspace.assets] == ["subject_identity", "subject_identity", "character_comparison_scale"]
    assert [asset.subject_identity.subject_id for asset in workspace.assets[:2]] == ["subject-1", "subject-2"]
    assert [asset.subject_identity.layout for asset in workspace.assets[:2]] == [ReferenceLayout.REFERENCE_SHEET] * 2
    assert workspace.assets[2].comparison_subject_ids == ["subject-1", "subject-2"]

    for asset in workspace.assets:
        path, mime_type, expected_sha256 = EXPECTED_FIXTURES[asset.id]
        fixture = case_dir / path
        assert fixture.is_file()
        assert asset.mime_type == mime_type
        assert hashlib.sha256(fixture.read_bytes()).hexdigest() == expected_sha256
        assert fixture.read_bytes()[:2] == b"\xff\xd8"


def test_height_case_has_authoritative_request_and_no_embedded_source_or_base64():
    case, workspace, _, _ = load_case(ROOT, CASE_ID)
    expected_request = "Subject 1 and Subject 2 are walking together through a quiet school courtyard. They begin side by side on the same ground plane, then stop and turn toward each other to have a brief serious conversation. Their relative height and body proportions should remain clearly consistent whenever they are standing near each other. The camera begins with a full-body two-shot showing both subjects clearly from head to toe, then slowly moves closer while keeping both visible. End with another full-body view of the two subjects standing next to each other."
    assert workspace.creative_request == expected_request

    definitions = "\n".join(path.read_text(encoding="utf-8") for path in CASE_DIR.glob("*.json"))
    assert ";base64," not in definitions
    assert "MiniConstruct-Benchmark-Source" not in definitions
    assert "I:\\AI-Models" not in definitions
    raw_workspace = json.loads((CASE_DIR / "workspace.json").read_text(encoding="utf-8"))
    assert all(";base64," not in asset["image"]["data_url"] for asset in raw_workspace["assets"])
    assert json.loads((CASE_DIR / "case.json").read_text(encoding="utf-8"))["fixtureAssets"]


@pytest.mark.asyncio
async def test_height_case_skips_without_vision_and_sends_three_images_in_order():
    class FakeClient:
        def __init__(self, settings):
            self.settings = settings

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def generate(self, messages):
            self.messages = messages
            return """integrated_multimodal_description:\n[Shot 1] Two subjects walk together.\noverall_soundscape:\nQuiet courtyard.\nnon_diegetic_music:\nNone.\n"""

    from tests.test_benchmark import profile

    skipped = await BenchmarkRunner(ROOT, FakeClient).run(profile(vision=False), [CASE_ID])
    assert skipped["cases"][0]["status"] == "skipped"

    client_instances = []

    class RecordingClient(FakeClient):
        def __init__(self, settings):
            super().__init__(settings)
            client_instances.append(self)

    result = await BenchmarkRunner(ROOT, RecordingClient).run(profile(vision=True), [CASE_ID])
    item = result["cases"][0]
    assert item["status"] == "completed"
    assert item["modelInput"]["imageInputCount"] == 3
    assert [image["assetId"] for image in item["modelInput"]["images"]] == list(EXPECTED_FIXTURES)
    assert [image["fixturePath"] for image in item["modelInput"]["images"]] == [value[0] for value in EXPECTED_FIXTURES.values()]
    assert len(client_instances) == 1
    image_parts = [part for part in client_instances[0].messages[1]["content"] if part.get("type") == "image_url"]
    assert len(image_parts) == 3
