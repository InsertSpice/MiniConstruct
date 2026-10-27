from __future__ import annotations

import hashlib
import base64
import json
from pathlib import Path

import pytest

from miniconstruct.benchmark.runner import BenchmarkRunner, load_case
from miniconstruct.models.workspace import ReferenceLayout


ROOT = Path(__file__).resolve().parents[1] / "benchmarks"
CASE_ID = "ref2va-offscreen-retention"
CASE_DIR = ROOT / "cases" / CASE_ID
CANONICAL_FIXTURE = ROOT / "cases" / "ref2va-reference-sheet-identity" / "assets" / "subject-1-reference-sheet.jpg"
EXPECTED_SHA256 = "06d9d07d9b62a96fe99009462bdd2c8838d31e513f3d5f02212eeb396646601b"


def test_offscreen_case_preserves_workspace_semantics_and_fixture_bytes():
    case, workspace, case_dir, _ = load_case(ROOT, CASE_ID)

    assert case.requirements.vision is True
    assert workspace.mode.value == "Ref2VA"
    assert workspace.duration_seconds == 8
    assert workspace.shots is None
    assert workspace.aspect_ratio == "auto"
    assert workspace.variations == 1
    assert len(workspace.subjects) == 1
    assert workspace.subjects[0].id == "subject-1"
    assert len(workspace.assets) == 1

    picture = workspace.assets[0]
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
    assert fixture.read_bytes() == CANONICAL_FIXTURE.read_bytes()
    assert hashlib.sha256(fixture.read_bytes()).hexdigest() == EXPECTED_SHA256


def test_offscreen_request_dialogue_and_definitions_are_exact_and_clean():
    _, workspace, _, _ = load_case(ROOT, CASE_ID)
    assert workspace.creative_request == (
        "Subject 1 walks down a quiet school hallway toward an open classroom doorway. Begin with her clearly visible in a medium-full tracking shot. She stops at the doorway, briefly looks into the classroom, then walks through it and completely leaves the camera's view. The camera remains in the hallway after she exits and does not follow her inside. Once Subject 1 is fully off-screen, she calls back from inside the classroom using the exact supplied dialogue. Subject 1 must remain completely out of frame for the rest of the video after exiting."
    )
    assert workspace.dialogue == "Subject 1: Wait here. I'll only be a minute."

    definitions = "\n".join(path.read_text(encoding="utf-8") for path in CASE_DIR.glob("*.json"))
    raw_workspace = json.loads((CASE_DIR / "workspace.json").read_text(encoding="utf-8"))
    assert ";base64," not in definitions
    assert "MiniConstruct-Benchmark-Source" not in definitions
    assert "I:\\AI-Models" not in definitions
    assert all(";base64," not in asset["image"]["data_url"] for asset in raw_workspace["assets"])


@pytest.mark.asyncio
async def test_offscreen_case_skips_without_vision_and_sends_one_image_and_checks_spoken_text():
    class FakeClient:
        def __init__(self, settings):
            self.settings = settings

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def generate(self, messages):
            self.messages = messages
            return """integrated_multimodal_description:\n[Shot 1] Subject 1 walks through the hallway.\n[Shot 2] Subject 1 exits through the classroom doorway.\n[Shot 3] Subject 1 says, \"Wait here. I'll only be a minute.\" from inside.\noverall_soundscape:\nQuiet hallway.\nnon_diegetic_music:\nNone.\n"""

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
    assert item["expectations"]["passed"] is True
    assert item["modelInput"]["imageInputCount"] == 1
    image_parts = [part for part in instances[0].messages[1]["content"] if part.get("type") == "image_url"]
    assert len(image_parts) == 1
    sent_digest = hashlib.sha256(base64.b64decode(image_parts[0]["image_url"]["url"].split(",", 1)[1])).hexdigest()
    assert sent_digest != EXPECTED_SHA256
    assert item["modelInput"]["images"][0]["sha256"] == sent_digest
