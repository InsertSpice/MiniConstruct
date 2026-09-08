from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from miniconstruct.benchmark.runner import BenchmarkRunner, load_case
from miniconstruct.models.workspace import ReferenceLayout


ROOT = Path(__file__).resolve().parents[1] / "benchmarks"
CASE_ID = "ref2va-transient-expression-identity"
CASE_DIR = ROOT / "cases" / CASE_ID
CANONICAL_FIXTURE = ROOT / "cases" / "ref2va-reference-sheet-identity" / "assets" / "subject-1-reference-sheet.jpg"
EXPECTED_SHA256 = "06d9d07d9b62a96fe99009462bdd2c8838d31e513f3d5f02212eeb396646601b"


def test_transient_expression_case_preserves_workspace_semantics_and_fixture_bytes():
    case, workspace, case_dir, _ = load_case(ROOT, CASE_ID)

    assert case.requirements.vision is True
    assert workspace.mode.value == "Ref2VA"
    assert workspace.duration_seconds == 7
    assert workspace.shots is None
    assert workspace.aspect_ratio == "auto"
    assert workspace.variations == 1
    assert len(workspace.subjects) == 1
    assert workspace.subjects[0].id == "subject-1"
    assert workspace.subjects[0].number == 1
    assert workspace.next_subject_number == 2
    assert len(workspace.assets) == 1

    picture = workspace.assets[0]
    assert picture.id == "8a39f3ad-3425-4f6c-b1c1-b8fbbc47fbb0"
    assert picture.role == "subject_identity"
    assert picture.subject_identity.subject_id == "subject-1"
    assert picture.subject_identity.focus.value == "general"
    assert picture.subject_identity.layout == ReferenceLayout.REFERENCE_SHEET
    assert picture.subject_identity.view.value == "unspecified"
    assert picture.attached is True
    assert workspace.creative_controls.subject_identity_fidelity.level.value == "strict"
    assert workspace.creative_controls.music.mode.value == "off"
    assert workspace.dialogue == ""

    fixture = case_dir / "assets" / "subject-1-reference-sheet.jpg"
    assert fixture.is_file()
    assert fixture.read_bytes() == CANONICAL_FIXTURE.read_bytes()
    assert hashlib.sha256(fixture.read_bytes()).hexdigest() == EXPECTED_SHA256


def test_transient_expression_request_and_definitions_are_exact_and_clean():
    _, workspace, _, _ = load_case(ROOT, CASE_ID)
    assert workspace.creative_request == (
        "Subject 1 stands alone beside a classroom window in a quiet school classroom. She begins calm with a relaxed neutral expression. A sudden loud crash from the hallway startles her; her eyes widen and she visibly flinches, then she looks toward the doorway with brief uncertainty. After realizing there is no danger, she gradually relaxes and ends with a relieved gentle smile. Keep Subject 1's identity, facial features, hairstyle, clothing, and body proportions consistent while her facial expression and immediate emotional reaction change naturally throughout the video."
    )

    definitions = "\n".join(path.read_text(encoding="utf-8") for path in CASE_DIR.glob("*.json"))
    raw_workspace = json.loads((CASE_DIR / "workspace.json").read_text(encoding="utf-8"))
    assert ";base64," not in definitions
    assert "MiniConstruct-Benchmark-Source" not in definitions
    assert "I:\\AI-Models" not in definitions
    assert all(";base64," not in asset["image"]["data_url"] for asset in raw_workspace["assets"])


@pytest.mark.asyncio
async def test_transient_expression_skips_without_vision_and_sends_one_image():
    class FakeClient:
        def __init__(self, settings):
            self.settings = settings

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def generate(self, messages):
            self.messages = messages
            return """integrated_multimodal_description:\n[Shot 1] Subject 1 stands in the classroom.\noverall_soundscape:\nA quiet classroom and hallway crash.\nnon_diegetic_music:\nNone.\n"""

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
    assert item["expectations"] == {"checks": [], "passed": True}
    assert item["modelInput"]["imageInputCount"] == 1
    assert item["modelInput"]["images"][0]["sha256"] == EXPECTED_SHA256
    assert len([part for part in instances[0].messages[1]["content"] if part.get("type") == "image_url"]) == 1
