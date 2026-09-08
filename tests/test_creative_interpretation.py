from pathlib import Path
import hashlib
import json

import pytest
from pydantic import ValidationError

from miniconstruct.benchmark.runner import load_case
from miniconstruct.h3.builder import assemble_prompt
from miniconstruct.h3.creative_interpretation import creative_interpretation_policy
from miniconstruct.models.api import ProjectEnvelope
from miniconstruct.models.workspace import CreativeInterpretation, Workspace


def test_creative_interpretation_defaults_to_balanced_when_omitted():
    workspace = Workspace.model_validate({
        "schemaVersion": 1, "projectName": "Old", "mode": "T2VA", "durationSeconds": 6,
        "creativeRequest": "A cyclist crosses a wet street.", "assets": [],
    })
    assert workspace.creative_interpretation == CreativeInterpretation.BALANCED
    assert workspace.model_dump(mode="json", by_alias=True)["creativeInterpretation"] == "balanced"


@pytest.mark.parametrize("value", ("conservative", "balanced", "expansive"))
def test_creative_interpretation_values_parse_and_round_trip(workspace_factory, value):
    workspace = workspace_factory(creativeInterpretation=value)
    restored = Workspace.model_validate_json(workspace.model_dump_json(by_alias=True))
    assert workspace.creative_interpretation.value == value
    assert restored.creative_interpretation.value == value


def test_invalid_creative_interpretation_is_rejected(workspace_factory):
    with pytest.raises(ValidationError):
        workspace_factory(creativeInterpretation="automatic")


@pytest.mark.parametrize(
    ("mode", "selected_anchor", "other_anchors"),
    (
        ("conservative", "Realize explicit actions and conditions literally", ("restrained compatible body language", "proactively enrich some of it")),
        ("balanced", "restrained compatible body language", ("Realize explicit actions and conditions literally", "proactively enrich some of it")),
        ("expansive", "proactively enrich some of it", ("Realize explicit actions and conditions literally", "restrained compatible body language")),
    ),
)
def test_assembled_prompt_includes_only_selected_creative_interpretation_policy(
    workspace_factory, mode, selected_anchor, other_anchors
):
    assembled = assemble_prompt(workspace_factory(creativeInterpretation=mode), False)
    policy = assembled.inspector_text.split("===== Creative Interpretation policy =====\n", 1)[1].split(
        "\n\n===== Canonical workspace/reference manifest =====", 1
    )[0]
    assert policy.count("Creative Interpretation:") == 1
    assert f"Creative Interpretation: {mode.title()}" in policy
    assert selected_anchor in policy
    assert all(anchor not in policy for anchor in other_anchors)


def test_conservative_allows_natural_connective_motion_without_unsupported_invention(workspace_factory):
    text = creative_interpretation_policy(workspace_factory(creativeInterpretation="conservative"))
    assert "connective motion, performance, staging, and continuity" in text
    assert "breathing, gaze, posture, facial response, weight shifts" in text
    assert "do not make characters rigid or robotic" in text


def test_shared_policy_distinguishes_creative_content_from_verbosity(workspace_factory):
    text = creative_interpretation_policy(workspace_factory(creativeInterpretation="balanced"))
    assert "how much new creative content may be inferred in genuinely unspecified space" in text
    assert "does not control output length, verbosity, adjective density, or prose richness" in text
    assert "Explicit workspace authority always wins" in text
    assert "Creative Request facts, actions, states, outcomes, and prohibitions" in text
    assert "Depict explicit user actions directly as requested" in text
    assert "what is done, its target, purpose, state, and specified outcome" in text
    assert "different behavior that merely suggests the same intent is not a substitute" in text
    assert "Exact Dialogue" in text
    assert "Subject/reference identity and roles" in text
    assert "Creative Controls" in text


def test_conservative_prefers_literal_direct_behavior_and_rejects_unneeded_proxies(workspace_factory):
    text = creative_interpretation_policy(workspace_factory(creativeInterpretation="conservative"))
    assert "Realize explicit actions and conditions literally" in text
    assert "preserving their targets, purposes, states, and outcomes" in text
    assert "Prefer the least unsupported content" in text
    assert "do not add props, events, sub-goals, characters, dialogue, or narrative beats" in text


def test_conservative_preserves_unresolved_states_without_inventing_an_ending(workspace_factory):
    text = creative_interpretation_policy(workspace_factory(creativeInterpretation="conservative"))
    assert "Keep user-unspecified situations pending or unresolved" in text
    assert "unless an outcome is supplied or required" in text
    assert "Let the final beat come from performance, gaze, posture, or composition" in text


def test_balanced_allows_restrained_detail_without_major_invented_narrative(workspace_factory):
    text = creative_interpretation_policy(workspace_factory(creativeInterpretation="balanced"))
    assert "restrained compatible body language, gestures, staging, environmental motion" in text
    assert "support an existing requested beat" in text
    assert "Avoid major new plot events, characters, dialogue" in text
    assert "action-driving props unless explicitly supported" in text


def test_expansive_allows_broader_elaboration_but_keeps_authority(workspace_factory):
    text = creative_interpretation_policy(workspace_factory(creativeInterpretation="expansive"))
    assert "proactively enrich some of it" in text
    assert "secondary performance, environmental activity, incidental props" in text
    assert "richer staging/blocking, camera choreography" in text
    assert "Preserve all explicit actions, targets, purposes, states, outcomes" in text


def test_expansive_actively_enriches_content_without_quotas_or_forced_invention(workspace_factory):
    text = creative_interpretation_policy(workspace_factory(creativeInterpretation="expansive"))
    assert "When meaningful unspecified space exists" in text
    assert "no category is required" in text
    assert "do not force invention into a closely specified scene" in text
    assert "no quotas or target output length" in text


def test_expansive_incidental_props_must_be_concrete_but_explicit_pantomime_remains_allowed(workspace_factory):
    text = creative_interpretation_policy(workspace_factory(creativeInterpretation="expansive"))
    assert "Newly introduced scene elements must be concretely present and consistently depicted" in text
    assert "imaginary" not in text.lower()
    assert "invisible" not in text.lower()


def test_expansive_policy_keeps_exact_dialogue_and_reference_authority(workspace_factory, image_asset_factory):
    workspace = workspace_factory(
        mode="Ref2VA",
        assets=[image_asset_factory()],
        dialogue="Subject 1: Do not leave.",
        creativeInterpretation="expansive",
    )
    assembled = assemble_prompt(workspace, False)
    assert "EXACT DIALOGUE (preserve wording and punctuation verbatim;" in assembled.inspector_text
    assert "Exact Dialogue" in assembled.inspector_text
    assert "Subject/reference identity and roles" in assembled.inspector_text


def test_project_without_creative_interpretation_loads_as_balanced(image_asset_factory):
    project = ProjectEnvelope.model_validate({
        "format": "MiniConstruct Project", "schemaVersion": 1,
        "workspace": {
            "mode": "Ref2VA", "durationSeconds": 6, "creativeRequest": "Use the reference.",
            "assets": [image_asset_factory().model_dump(by_alias=True)],
        },
    })
    assert project.workspace.creative_interpretation == CreativeInterpretation.BALANCED


def test_frozen_creative_inference_fixture_loads_as_balanced_without_fixture_migration():
    root = Path(__file__).resolve().parents[1] / "benchmarks"
    _, workspace, _, source = load_case(root, "ref2va-creative-inference-baseline")
    assert b'"creativeInterpretation"' not in source
    assert workspace.creative_interpretation == CreativeInterpretation.BALANCED


def test_creative_interpretation_cases_are_controlled_workspace_variants():
    root = Path(__file__).resolve().parents[1] / "benchmarks"
    case_ids = [
        "ref2va-creative-inference-conservative",
        "ref2va-creative-inference-baseline",
        "ref2va-creative-inference-expansive",
    ]
    loaded = [load_case(root, case_id) for case_id in case_ids]
    workspaces = [item[1] for item in loaded]
    assert [workspace.creative_interpretation.value for workspace in workspaces] == ["conservative", "balanced", "expansive"]

    canonical = []
    for workspace in workspaces:
        dumped = workspace.model_dump(mode="json", by_alias=True)
        dumped.pop("creativeInterpretation")
        canonical.append(dumped)
    assert canonical[0] == canonical[1] == canonical[2]

    assert len({workspace.creative_request for workspace in workspaces}) == 1
    assert [(workspace.duration_seconds, workspace.shots, workspace.aspect_ratio) for workspace in workspaces] == [(7, None, "auto")] * 3
    assert [workspace.subjects for workspace in workspaces] == [workspaces[0].subjects] * 3
    assert [workspace.assets[0].role for workspace in workspaces] == ["subject_identity"] * 3
    assert [workspace.assets[0].notes for workspace in workspaces] == [""] * 3
    assert [workspace.creative_controls.subject_identity_fidelity.level.value for workspace in workspaces] == ["strict"] * 3
    assert [workspace.creative_controls.music.mode.value for workspace in workspaces] == ["off"] * 3

    asset_hashes = [hashlib.sha256((case_dir / case.fixture_assets[0].path).read_bytes()).hexdigest() for case, _, case_dir, _ in loaded]
    assert asset_hashes == ["e441aa5056af889b27500b9f223f8c73320461c8d9ca3bc117eaeb0d6127dd19"] * 3


def test_creative_interpretation_suite_order_and_frozen_core_content():
    root = Path(__file__).resolve().parents[1] / "benchmarks"
    suite = json.loads((root / "suites" / "creative-interpretation.json").read_text(encoding="utf-8"))
    assert suite["cases"] == [
        "ref2va-creative-inference-conservative",
        "ref2va-creative-inference-baseline",
        "ref2va-creative-inference-expansive",
    ]
    assert hashlib.sha256((root / "suites" / "core.json").read_bytes()).hexdigest() == "9ebbc4b4f930708fff68ed7dbcad698a5560f22b832fdf2f42baabbf06e072a3"
    assert hashlib.sha256((root / "cases" / "ref2va-creative-inference-baseline" / "case.json").read_bytes()).hexdigest() == "83f8de32acdc1b73fcc4187bd069afab4bc23b7af5e561b8c7808f1decb59161"
    assert hashlib.sha256((root / "cases" / "ref2va-creative-inference-baseline" / "workspace.json").read_bytes()).hexdigest() == "f905d741ac50e8b4f02727cbcdd85c71b4a484c4042d71021dbc05bf4f58ae1b"
