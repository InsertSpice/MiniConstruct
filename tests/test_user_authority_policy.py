from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from miniconstruct.h3.builder import USER_TERMINOLOGY_AUTHORITY, assemble_prompt


ROOT = Path(__file__).resolve().parents[1]
TERMINOLOGY_RULE = (
    "When the user explicitly supplies a term for an action, role, relationship, dynamic, style, or other named concept, "
    "retain that user-supplied term verbatim at least once in the H3 prompt. Do not replace the term entirely with a "
    "synonym, euphemism, generic category, or only its observable description. Supporting visual description may expand "
    "or clarify the term but must not substitute for it."
)
LATERALITY_ANCHOR = "laterality; audio reuse"


@pytest.mark.parametrize("mode", ("conservative", "balanced", "expansive"))
def test_terminology_authority_rule_is_in_actual_assembled_prompt_for_every_mode(workspace_factory, mode):
    assembled = assemble_prompt(workspace_factory(creativeInterpretation=mode), False)
    system = assembled.messages[0]["content"]
    user = assembled.messages[1]["content"]
    assert TERMINOLOGY_RULE in user
    assert user.count(TERMINOLOGY_RULE) == 1
    assert TERMINOLOGY_RULE == USER_TERMINOLOGY_AUTHORITY
    assert TERMINOLOGY_RULE not in system
    assert "Creative Interpretation policy" in assembled.inspector_text


def test_terminology_rule_is_user_request_authority_and_not_system_policy(workspace_factory):
    assembled = assemble_prompt(workspace_factory(), False)
    system = assembled.messages[0]["content"]
    user = assembled.messages[1]["content"]
    core_start = system.index("## MiniConstruct core operating instructions")
    official_start = system.index("## Official MiniMax H3 guide (normative)")
    interpretation_start = system.index("## Creative Interpretation policy")
    assert core_start < official_start < interpretation_start
    assert TERMINOLOGY_RULE not in system
    assert TERMINOLOGY_RULE in user
    assert TERMINOLOGY_RULE not in system[core_start:official_start]
    assert TERMINOLOGY_RULE not in system[interpretation_start:]
    assert "Explicit workspace authority always wins" in system[interpretation_start:]
    assert "field archivist" not in system
    assert "Gemma" not in system and "Qwen" not in system
    assert "copy the entire Creative Request verbatim" not in system
    assert "every word" not in system


def test_terminology_rule_requires_the_term_but_allows_supporting_description(workspace_factory):
    assembled = assemble_prompt(workspace_factory(), False)
    user = assembled.messages[1]["content"]
    assert "explicitly supplies a term for an action, role, relationship, dynamic, style, or other named concept" in user
    assert "distinctive terminology" not in user
    assert "user-supplied term verbatim at least once" in user
    assert "synonym, euphemism, generic category" in user
    assert "only its observable description" in user
    assert "visual description may expand or clarify the term" in user
    assert "must not substitute for it" in user


def test_terminology_rule_does_not_replace_exact_dialogue_contract(workspace_factory):
    assembled = assemble_prompt(
        workspace_factory(dialogue="Subject 1: Keep this exact line."),
        False,
    )
    system = assembled.messages[0]["content"]
    user = assembled.messages[1]["content"]
    assert TERMINOLOGY_RULE in user
    assert TERMINOLOGY_RULE not in system
    assert "EXACT DIALOGUE (preserve wording and punctuation verbatim;" in assembled.inspector_text
    assert "speaker IDs follow first vocal-event order" in assembled.inspector_text


def test_existing_laterality_authority_wording_remains_unchanged(workspace_factory):
    assembled = assemble_prompt(workspace_factory(), False)
    assert LATERALITY_ANCHOR in assembled.inspector_text
    assert "laterality" in assembled.inspector_text
    assert "subject-relative anatomical coordinate frame" not in assembled.inspector_text


def test_terminology_rule_follows_official_guide_and_precedes_workspace_manifest(workspace_factory):
    assembled = assemble_prompt(workspace_factory(), False)
    system = assembled.messages[0]["content"]
    user = assembled.messages[1]["content"]
    main_request = user.index("MAIN CREATIVE REQUEST (authoritative):")
    rule_index = user.index("USER TERMINOLOGY AUTHORITY:")
    response_format = user.index("Return only the clean H3 prompt as plain text.")
    assert main_request < rule_index < response_format
    assert system.index("## Official MiniMax H3 guide (normative)") < system.index("## Creative Interpretation policy")
    assert TERMINOLOGY_RULE not in system


@pytest.mark.parametrize("mode", ("T2VA", "I2VA", "FL2VA", "L2VA", "Ref2VA"))
def test_user_message_terminology_authority_is_shared_across_h3_modes(workspace_factory, image_asset_factory, mode):
    assets = []
    if mode == "I2VA":
        assets = [image_asset_factory("first", "first_frame_anchor")]
    elif mode == "FL2VA":
        assets = [
            image_asset_factory("first", "first_frame_anchor"),
            image_asset_factory("last", "last_frame_anchor", 1),
        ]
    elif mode == "L2VA":
        assets = [image_asset_factory("last", "last_frame_anchor")]
    elif mode == "Ref2VA":
        assets = [image_asset_factory()]
    assembled = assemble_prompt(workspace_factory(mode=mode, assets=assets), False)
    system = assembled.messages[0]["content"]
    user = assembled.messages[1]["content"]
    assert user.count(TERMINOLOGY_RULE) == 1
    assert TERMINOLOGY_RULE not in system
    assert user.index("MAIN CREATIVE REQUEST (authoritative):") < user.index("USER TERMINOLOGY AUTHORITY:")
    assert user.index("USER TERMINOLOGY AUTHORITY:") < user.index("Return only the clean H3 prompt as plain text.")


def test_frozen_user_authority_benchmark_files_are_unchanged():
    expected_hashes = {
        "benchmarks/suites/user-authority.json": "f954ab41ac3d042457dde2fc851534f29f0ac64b81cfd4e459214f858d94ef21",
        "benchmarks/cases/t2va-laterality-preservation/case.json": "3e10dcbb444e8b182a4dbf125c826bedd921fd56e096f039664c1294fa43e722",
        "benchmarks/cases/t2va-laterality-preservation/workspace.json": "0303be67daaefc9ea77efbf5d53319e610d10bb06e3f7c1ec11260566f085c5d",
        "benchmarks/cases/t2va-user-terminology-preservation/case.json": "fbf4f598754f6da143481702d1baef6f39511e7204efc239b31543287a035565",
        "benchmarks/cases/t2va-user-terminology-preservation/workspace.json": "d014f149da9c15a505df0ae68415ce1c1634ab6a608092775c5dbc85555c004f",
    }
    for relative, expected in expected_hashes.items():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == expected
