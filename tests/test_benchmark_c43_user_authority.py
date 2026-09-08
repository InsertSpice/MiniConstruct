from __future__ import annotations

import hashlib
import json
from pathlib import Path

from miniconstruct.benchmark.runner import load_case, load_suite


ROOT = Path(__file__).resolve().parents[1] / "benchmarks"
LATERALITY_ID = "t2va-laterality-preservation"
TERMINOLOGY_ID = "t2va-user-terminology-preservation"
SUITE_ID = "user-authority"

LATERALITY_REQUEST = (
    "In a single locked-off medium shot, an adult woman faces the camera beside a plain worktable. "
    "A small blue wristband is on her right wrist; her left wrist is bare. She raises her right hand to shoulder "
    "height while her left hand stays down. Because she is facing the camera, the raised right hand and blue wristband "
    "appear on the left side of the image. There is no spoken dialogue."
)
TERMINOLOGY_REQUEST = (
    "In a quiet municipal archive, an adult woman works as a field archivist. She sorts weathered maps into labeled "
    "folders, checks one map under a desk lamp, and closes the folder. There is no spoken dialogue."
)


def _assert_clean(case_id: str) -> None:
    case_dir = ROOT / "cases" / case_id
    definitions = "\n".join(path.read_text(encoding="utf-8") for path in case_dir.glob("*.json"))
    assert ";base64," not in definitions
    assert "I:\\AI-Models" not in definitions
    workspace = json.loads((case_dir / "workspace.json").read_text(encoding="utf-8"))
    assert workspace.get("assets", []) == []


def test_user_authority_suite_has_the_frozen_two_case_order():
    suite = load_suite(ROOT, SUITE_ID)
    assert suite.cases == [LATERALITY_ID, TERMINOLOGY_ID]


def test_laterality_fixture_is_a_real_isolated_workspace_with_authoritative_mapping():
    case, workspace, _, source = load_case(ROOT, LATERALITY_ID)
    assert case.requirements.vision is False
    assert (workspace.mode.value, workspace.duration_seconds, workspace.shots, workspace.aspect_ratio, workspace.variations) == (
        "T2VA", 6, 1, "16:9", 1
    )
    assert workspace.creative_request == LATERALITY_REQUEST
    assert workspace.dialogue == "" and workspace.reference_labels == ""
    assert workspace.assets == [] and workspace.subjects == [] and workspace.next_subject_number == 1
    assert "faces the camera" in workspace.creative_request
    assert "blue wristband is on her right wrist" in workspace.creative_request
    assert "her left wrist is bare" in workspace.creative_request
    assert "raises her right hand to shoulder height" in workspace.creative_request
    assert "her left hand stays down" in workspace.creative_request
    assert "Because she is facing the camera" in workspace.creative_request
    assert "appear on the left side of the image" in workspace.creative_request
    assert "subject-relative anatomical coordinate frame" not in workspace.creative_request
    assert "do not mirror" not in workspace.creative_request.lower()
    assert b'"creativeRequest"' in source
    _assert_clean(case.id)


def test_terminology_fixture_is_a_real_isolated_workspace_without_exact_dialogue():
    case, workspace, _, source = load_case(ROOT, TERMINOLOGY_ID)
    assert case.requirements.vision is False
    assert (workspace.mode.value, workspace.duration_seconds, workspace.shots, workspace.aspect_ratio, workspace.variations) == (
        "T2VA", 6, 1, "16:9", 1
    )
    assert workspace.creative_request == TERMINOLOGY_REQUEST
    assert "field archivist" in workspace.creative_request
    assert "chosen professional title" not in workspace.creative_request
    assert workspace.dialogue == "" and workspace.reference_labels == ""
    assert workspace.assets == [] and workspace.subjects == [] and workspace.next_subject_number == 1
    assert b'"dialogue"' not in source
    _assert_clean(case.id)


def test_user_authority_cases_differ_only_in_the_experimental_request():
    _, laterality, _, _ = load_case(ROOT, LATERALITY_ID)
    _, terminology, _, _ = load_case(ROOT, TERMINOLOGY_ID)
    laterality_dump = laterality.model_dump(mode="json", by_alias=True)
    terminology_dump = terminology.model_dump(mode="json", by_alias=True)
    laterality_dump.pop("creativeRequest")
    terminology_dump.pop("creativeRequest")
    assert laterality_dump == terminology_dump


def test_user_authority_fixture_files_are_frozen():
    expected_hashes = {
        "suites/user-authority.json": "f954ab41ac3d042457dde2fc851534f29f0ac64b81cfd4e459214f858d94ef21",
        "cases/t2va-laterality-preservation/case.json": "3e10dcbb444e8b182a4dbf125c826bedd921fd56e096f039664c1294fa43e722",
        "cases/t2va-laterality-preservation/workspace.json": "0303be67daaefc9ea77efbf5d53319e610d10bb06e3f7c1ec11260566f085c5d",
        "cases/t2va-user-terminology-preservation/case.json": "fbf4f598754f6da143481702d1baef6f39511e7204efc239b31543287a035565",
        "cases/t2va-user-terminology-preservation/workspace.json": "d014f149da9c15a505df0ae68415ce1c1634ab6a608092775c5dbc85555c004f",
    }
    for relative, expected in expected_hashes.items():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == expected
