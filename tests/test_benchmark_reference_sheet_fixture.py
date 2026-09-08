from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from miniconstruct.benchmark.runner import BenchmarkRunner, load_case
from miniconstruct.models.workspace import ReferenceLayout


ROOT = Path(__file__).resolve().parents[1] / "benchmarks"
CASE_ID = "ref2va-reference-sheet-identity"
CASE_DIR = ROOT / "cases" / CASE_ID
HEIGHT_FIXTURE = ROOT / "cases" / "ref2va-height-comparison" / "assets" / "subject-1-reference-sheet.jpg"
EXPECTED_SHA256 = "06d9d07d9b62a96fe99009462bdd2c8838d31e513f3d5f02212eeb396646601b"


def test_reference_sheet_case_loads_as_one_real_subject_picture():
    case, workspace, case_dir, _ = load_case(ROOT, CASE_ID)

    assert case.requirements.vision is True
    assert len(case.fixture_assets) == 1
    assert workspace.mode.value == "Ref2VA"
    assert workspace.duration_seconds == 7
    assert workspace.shots is None
    assert workspace.aspect_ratio == "auto"
    assert workspace.variations == 1
    assert len(workspace.subjects) == 1
    assert workspace.subjects[0].id == "subject-1"
    assert workspace.subjects[0].number == 1
    assert len(workspace.assets) == 1

    picture = workspace.assets[0]
    assert picture.order == 0
    assert picture.role == "subject_identity"
    assert picture.subject_identity.subject_id == "subject-1"
    assert picture.subject_identity.focus.value == "general"
    assert picture.subject_identity.layout == ReferenceLayout.REFERENCE_SHEET
    assert picture.subject_identity.view.value == "unspecified"
    assert picture.attached is True
    assert workspace.creative_controls.subject_identity_fidelity.level.value == "strict"
    assert workspace.creative_controls.music.mode.value == "off"

    fixture = case_dir / "assets" / "subject-1-reference-sheet.jpg"
    assert fixture.is_file()
    assert fixture.read_bytes() == HEIGHT_FIXTURE.read_bytes()
    assert hashlib.sha256(fixture.read_bytes()).hexdigest() == EXPECTED_SHA256
    assert hashlib.sha256(HEIGHT_FIXTURE.read_bytes()).hexdigest() == EXPECTED_SHA256


def test_reference_sheet_request_and_definitions_are_clean():
    _, workspace, _, _ = load_case(ROOT, CASE_ID)
    assert workspace.creative_request == (
        "Subject 1 walks alone through a quiet modern school hallway. Begin with a full-body three-quarter front view as she walks toward the camera, then the camera smoothly arcs around her while she continues walking so that her side profile and partial rear view become visible naturally during the movement. She stops beside a classroom doorway, turns back toward the camera, and ends in a relaxed full-body three-quarter pose. Keep Subject 1's identity, hairstyle, clothing, body proportions, and overall appearance consistent throughout."
    )

    definitions = "\n".join(path.read_text(encoding="utf-8") for path in CASE_DIR.glob("*.json"))
    raw_workspace = json.loads((CASE_DIR / "workspace.json").read_text(encoding="utf-8"))
    assert ";base64," not in definitions
    assert "MiniConstruct-Benchmark-Source" not in definitions
    assert "I:\\AI-Models" not in definitions
    assert all(";base64," not in asset["image"]["data_url"] for asset in raw_workspace["assets"])


@pytest.mark.asyncio
async def test_reference_sheet_skips_without_vision_and_sends_one_image():
    class FakeClient:
        def __init__(self, settings):
            self.settings = settings

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def generate(self, messages):
            self.messages = messages
            return """integrated_multimodal_description:\n[Shot 1] Subject 1 walks through a hallway.\noverall_soundscape:\nQuiet hallway.\nnon_diegetic_music:\nNone.\n"""

    from tests.test_benchmark import profile

    skipped = await BenchmarkRunner(ROOT, FakeClient).run(profile(vision=False), [CASE_ID])
    assert skipped["cases"][0]["status"] == "skipped"

    instances = []

    class RecordingClient(FakeClient):
        def __init__(self, settings):
            super().__init__(settings)
            instances.append(self)

    result = await BenchmarkRunner(ROOT, RecordingClient).run(profile(vision=True), [CASE_ID])
    item = result["cases"][0]
    assert item["status"] == "completed"
    assert item["modelInput"]["imageInputCount"] == 1
    assert item["modelInput"]["images"] == [{
        "index": 1,
        "sha256": EXPECTED_SHA256,
        "mimeType": "image/jpeg",
        "assetId": "a46fa829-2b96-43f3-8e70-9e1b08d0b824",
        "filename": "Codex Image Sep 1, 2026, 05_50_06 AM.png",
        "fixturePath": "assets/subject-1-reference-sheet.jpg",
    }]
    image_parts = [part for part in instances[0].messages[1]["content"] if part.get("type") == "image_url"]
    assert len(image_parts) == 1
