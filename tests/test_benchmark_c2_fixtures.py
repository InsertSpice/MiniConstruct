from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from miniconstruct.benchmark.runner import BenchmarkRunner, load_case
from miniconstruct.models.workspace import ReferenceLayout


ROOT = Path(__file__).resolve().parents[1] / "benchmarks"
SUBJECT_A_SHA256 = "06d9d07d9b62a96fe99009462bdd2c8838d31e513f3d5f02212eeb396646601b"
SUBJECT_B_SHA256 = "e441aa5056af889b27500b9f223f8c73320461c8d9ca3bc117eaeb0d6127dd19"
WARDROBE_REQUEST = "Subject 1 exercises alone in a bright school gymnasium. For the entire target video, she wears a plain white short-sleeve athletic T-shirt, navy gym shorts, white ankle socks, and simple indoor training shoes. Begin with a full-body view as she performs a controlled side stretch, then she takes several light jogging steps across the gym floor and stops near the wall. End with a relaxed full-body three-quarter view of Subject 1. Keep Subject 1's facial identity, hairstyle, body proportions, and recognizable appearance consistent throughout while using the explicitly requested athletic outfit for the entire video."
LATERALITY_REQUEST = "In a small workshop, an adult woman stands facing the camera beside a work table. A bright red work glove is worn only on her left hand, while her right hand is bare. A blue tool box sits on screen-left and a yellow chair sits on screen-right. She reaches with her left hand toward the blue tool box, then turns her head toward screen-right without turning her body. Finally, she raises her bare right hand to shoulder height while the red glove remains on her left hand. Keep the left/right and screen-left/screen-right relationships exactly as stated throughout the video."
INFERENCE_REQUEST = "Subject 1 waits alone beneath a covered bus stop during light evening rain. She is visibly impatient and periodically checks whether the bus is coming."
CONTINUATION_REQUEST = "Continue directly from the end state of Video 1. Subject 1 opens the classroom door with the hand already resting on the handle, steps through the doorway, then glances back over her shoulder toward the hallway before continuing inside. Preserve the established hallway, her starting orientation, her initial hand placement, and her identity."
CONTINUATION_NOTES = "At the end of Video 1, Subject 1 is standing beside a classroom doorway, facing screen-right with her right hand resting on the door handle. The hallway is empty. The camera is at medium-full distance in front of her. Subject 1 is alert but motionless at the exact end of the reference."
CAMERA_REQUEST = "Subject 1 crosses a quiet rooftop at sunset. In the first shot, show a wide full-body view as she begins walking. In the second shot, move closer while following alongside her as she continues forward. In the final shot, arc around her as she stops near the railing and turns toward the camera. Keep Subject 1 visible and recognizable throughout."


def _fixture_bytes(case_id: str) -> bytes:
    case, _, case_dir, _ = load_case(ROOT, case_id)
    return (case_dir / case.fixture_assets[0].path).read_bytes()


def _assert_clean(case_id: str) -> None:
    case_dir = ROOT / "cases" / case_id
    definitions = "\n".join(path.read_text(encoding="utf-8") for path in case_dir.glob("*.json"))
    assert ";base64," not in definitions
    assert "MiniConstruct-Benchmark-Source" not in definitions
    assert "I:\\AI-Models" not in definitions
    raw_workspace = json.loads((case_dir / "workspace.json").read_text(encoding="utf-8"))
    assert all(";base64," not in asset.get("image", {}).get("data_url", "") for asset in raw_workspace["assets"])


def test_wardrobe_authority_converts_the_source_workspace_and_uses_subject_a_fixture():
    case, workspace, _, _ = load_case(ROOT, "ref2va-wardrobe-authority")
    assert case.requirements.vision is True
    assert (workspace.project_id, workspace.project_name) == ("0ca4cb36-2363-4e70-85d7-458998fbdad9", "WardrobeAuthorityTest")
    assert (workspace.mode.value, workspace.duration_seconds, workspace.shots, workspace.aspect_ratio, workspace.variations) == ("Ref2VA", 7, None, "auto", 1)
    assert workspace.creative_request == WARDROBE_REQUEST and workspace.dialogue == ""
    assert len(workspace.subjects) == 1 and workspace.next_subject_number == 2
    picture = workspace.assets[0]
    assert (picture.id, picture.filename, picture.mime_type, picture.role) == ("3e9816f2-db1f-42a0-9223-89ac5721f877", "Codex Image Sep 1, 2026, 05_50_06 AM.png", "image/jpeg", "subject_identity")
    assert picture.subject_identity.layout == ReferenceLayout.REFERENCE_SHEET
    assert workspace.creative_controls.subject_identity_fidelity.level.value == "strict"
    assert workspace.creative_controls.music.mode.value == "off"
    assert hashlib.sha256(_fixture_bytes(case.id)).hexdigest() == SUBJECT_A_SHA256
    _assert_clean(case.id)


def test_laterality_baseline_is_a_clean_real_t2va_workspace_without_references():
    case, workspace, _, _ = load_case(ROOT, "t2va-laterality-baseline")
    assert case.requirements.vision is False
    assert (workspace.mode.value, workspace.duration_seconds, workspace.shots, workspace.aspect_ratio, workspace.variations) == ("T2VA", 8, None, "auto", 1)
    assert workspace.creative_request == LATERALITY_REQUEST
    assert workspace.dialogue == "" and workspace.assets == [] and workspace.subjects == []
    assert workspace.creative_controls.music.mode.value == "off"
    _assert_clean(case.id)


def test_creative_inference_baseline_uses_subject_b_identity_with_no_added_staging():
    case, workspace, _, _ = load_case(ROOT, "ref2va-creative-inference-baseline")
    assert case.requirements.vision is True
    assert workspace.mode.value == "Ref2VA" and workspace.creative_request == INFERENCE_REQUEST
    assert len(workspace.subjects) == len(workspace.assets) == 1 and workspace.next_subject_number == 2
    assert workspace.assets[0].subject_identity.layout == ReferenceLayout.REFERENCE_SHEET
    assert workspace.creative_controls.subject_identity_fidelity.level.value == "strict"
    assert workspace.creative_controls.music.mode.value == "off"
    assert hashlib.sha256(_fixture_bytes(case.id)).hexdigest() == SUBJECT_B_SHA256
    _assert_clean(case.id)


def test_continuation_state_uses_metadata_only_video_and_identity_picture_precedence():
    case, workspace, _, _ = load_case(ROOT, "ref2va-continuation-state")
    assert case.requirements.vision is True
    assert workspace.mode.value == "Ref2VA" and workspace.creative_request == CONTINUATION_REQUEST
    picture, video = workspace.assets
    assert picture.role == "subject_identity" and picture.subject_identity.layout == ReferenceLayout.REFERENCE_SHEET
    assert (video.kind.value, video.role, video.notes, video.image, video.attached) == ("video", "continuation_source", CONTINUATION_NOTES, None, False)
    assert hashlib.sha256(_fixture_bytes(case.id)).hexdigest() == SUBJECT_A_SHA256
    _assert_clean(case.id)


def test_camera_multishot_controls_use_real_camera_enums_and_subject_b_fixture():
    case, workspace, _, _ = load_case(ROOT, "ref2va-camera-multishot-controls")
    assert case.requirements.vision is True
    assert (workspace.mode.value, workspace.duration_seconds, workspace.shots, workspace.aspect_ratio, workspace.variations) == ("Ref2VA", 8, 3, "auto", 1)
    assert workspace.creative_request == CAMERA_REQUEST
    assert workspace.creative_controls.music.mode.value == "off"
    assert workspace.creative_controls.subject_identity_fidelity.level.value == "strict"
    assert workspace.creative_controls.camera.arc.value == "prefer"
    assert workspace.creative_controls.camera.tracking.value == "prefer"
    assert workspace.creative_controls.camera.static.value == "avoid"
    assert hashlib.sha256(_fixture_bytes(case.id)).hexdigest() == SUBJECT_B_SHA256
    _assert_clean(case.id)


@pytest.mark.asyncio
async def test_new_cases_use_native_prompt_assembly_without_transmitting_video_bytes():
    class FakeClient:
        instances: list["FakeClient"] = []

        def __init__(self, settings):
            self.settings = settings
            self.messages = []
            type(self).instances.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def generate(self, messages):
            self.messages = messages
            return "integrated_multimodal_description:\n[Shot 1] The requested sequence begins.\noverall_soundscape:\nAmbient sound.\nnon_diegetic_music:\nN/A.\n"

    from tests.test_benchmark import profile

    wardrobe = await BenchmarkRunner(ROOT, FakeClient).run(profile(vision=True), ["ref2va-wardrobe-authority"])
    wardrobe_item = wardrobe["cases"][0]
    assert wardrobe_item["status"] == "completed"
    assert wardrobe_item["modelInput"]["imageInputCount"] == 1
    assert wardrobe_item["modelInput"]["images"] == [{
        "index": 1,
        "sha256": SUBJECT_A_SHA256,
        "mimeType": "image/jpeg",
        "assetId": "3e9816f2-db1f-42a0-9223-89ac5721f877",
        "filename": "Codex Image Sep 1, 2026, 05_50_06 AM.png",
        "fixturePath": "assets/subject-1-reference-sheet.jpg",
    }]

    laterality = await BenchmarkRunner(ROOT, FakeClient).run(profile(vision=False), ["t2va-laterality-baseline"])
    assert laterality["cases"][0]["status"] == "completed"
    assert laterality["cases"][0]["modelInput"]["images"] == []

    continuation = await BenchmarkRunner(ROOT, FakeClient).run(profile(vision=True), ["ref2va-continuation-state"])
    item = continuation["cases"][0]
    assert item["status"] == "completed" and item["modelInput"]["imageInputCount"] == 1
    system = item["modelInput"]["system"]
    assert "normal continuation source" in system and CONTINUATION_NOTES in system
    serialized_messages = json.dumps(FakeClient.instances[-1].messages)
    assert "video_url" not in serialized_messages and "data:video" not in serialized_messages

    camera = await BenchmarkRunner(ROOT, FakeClient).run(profile(vision=True), ["ref2va-camera-multishot-controls"])
    assert camera["cases"][0]["modelInput"]["imageInputCount"] == 1
    assert "Avoid: Static" in camera["cases"][0]["modelInput"]["user"]
    assert "Prefer: Arc, Tracking" in camera["cases"][0]["modelInput"]["user"]
