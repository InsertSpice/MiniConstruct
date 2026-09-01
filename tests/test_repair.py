from __future__ import annotations

import pytest

from miniconstruct.api import routes
from miniconstruct.h3.repair import assemble_repair_prompt
from miniconstruct.h3.validator import validate_prompt
from miniconstruct.models.workspace import H3Mode
from miniconstruct.models.api import RepairRequest


FOUR_SHOT_PROMPT = (
    "integrated_multimodal_description:\n"
    "[Shot 1] A cyclist says, <d>[English] Keep moving.</d>\n"
    "[Shot 2] At 00:01.000, the cyclist starts.\n"
    "[Shot 3] At 00:03.000, the cyclist accelerates.\n"
    "[Shot 4] At 09.000, the cyclist arrives.\n\n"
    "overall_soundscape:\nRain and distant traffic.\n\n"
    "non_diegetic_music:\nN/A"
)


def repair_request(workspace, prompt=FOUR_SHOT_PROMPT):
    return RepairRequest.model_validate({
        "workspace": workspace.model_dump(mode="json", by_alias=True),
        "llm": {"baseUrl": "http://local.test/v1", "model": "writer"},
        "prompt": prompt,
        "findings": [],
    })


def comparison_workspace(workspace_factory, image_asset_factory):
    first = image_asset_factory("identity-1", order=0)
    second = image_asset_factory("identity-2", order=1)
    second.subject_identity.subject_id = "subject-2"
    comparison = image_asset_factory("comparison", role="character_comparison_scale", order=2)
    comparison.comparison_subject_ids = ["subject-1", "subject-2"]
    return workspace_factory(
        mode="Ref2VA", shots=2, assets=[first, second, comparison],
        subjects=[{"id": "subject-1", "number": 1}, {"id": "subject-2", "number": 2}], nextSubjectNumber=3,
    )


def comparison_prompt(include_definition=True, include_retention=False, appears_in=False):
    definition = "<Picture 3> is the Character Comparison / Scale reference for\n<Subject 1> and <Subject 2>, establishing their visible relative height.\n" if include_definition else ""
    if appears_in:
        retention = "<Picture 3> (appears in [Shot 1], [Shot 2]): fully_preserved - retain the scale relationship.\n"
    elif include_retention:
        retention = "<Picture 3> (scale relationship between [Shot 1]-[Shot 3]): weak_reference - retain the scale relationship.\n"
    else:
        retention = ""
    return (
        "subject_definitions:\n"
        "<Subject 1> is defined by <Picture 1>.\n"
        "<Subject 2> is defined by <Picture 2>.\n"
        f"{definition}\n"
        "summary:\n[reference generation] Two subjects meet.\n\n"
        "retention_analysis:\n"
        "<Subject 1> (appears in [Shot 1]): fully_preserved - identity retained.\n"
        "<Subject 2> (appears in [Shot 2]): fully_preserved - identity retained.\n"
        f"{retention}\n"
        "detailed_description:\n"
        "[Shot 1] <Subject 1> enters.\n"
        "[Shot 2] At 00:03.000, <Subject 2> replies.\n\n"
        "overall_soundscape:\nQuiet room.\n\n"
        "non_diegetic_music:\nN/A"
    )


def test_repair_prompt_keeps_workspace_context_but_makes_current_output_authoritative(workspace_factory):
    request = repair_request(workspace_factory(shots=3, dialogue="Narrator: Keep moving."))
    assembled = assemble_repair_prompt(request, [])
    text = "\n".join(str(message["content"]) for message in assembled.messages)
    assert "Official MiniMax H3 guide" in text
    assert "Canonical workspace/reference manifest" in text
    assert "not authority to reconcile an intentionally edited output" in text
    assert FOUR_SHOT_PROMPT in text
    assert "do not add, remove, merge, split, or renumber shots" in text


def test_repair_uses_one_system_message_and_replaces_generation_policy(workspace_factory):
    assembled = assemble_repair_prompt(repair_request(workspace_factory()), [])
    assert [message["role"] for message in assembled.messages] == ["system", "user"]
    system = assembled.messages[0]["content"]
    assert "Format repair policy" in system
    assert "Generation policy" not in system
    assert "===== Format repair policy =====" in assembled.inspector_text


def test_repair_receives_bare_reference_label_finding(workspace_factory):
    prompt = FOUR_SHOT_PROMPT.replace("A cyclist", "<Subject 1> is defined by Picture 1")
    findings = [item for item in validate_prompt(prompt, H3Mode.T2VA, 8).findings if item.code == "bare_reference_label"]
    assembled = assemble_repair_prompt(repair_request(workspace_factory(), prompt), findings)
    assert findings and 'use canonical H3 label <Picture 1>' in assembled.messages[1]["content"]


class RepairClient:
    def __init__(self, repaired):
        self.repaired = repaired
        self.messages = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def generate(self, messages):
        self.messages = messages
        return self.repaired


@pytest.mark.asyncio
async def test_repair_fixes_bad_timestamp_without_reconciling_shot_count(workspace_factory, monkeypatch):
    workspace = workspace_factory(shots=3, dialogue="Narrator: Keep moving.")
    repaired = FOUR_SHOT_PROMPT.replace("At 09.000", "At 00:05.000")
    client = RepairClient(repaired)
    monkeypatch.setattr(routes, "OpenAICompatibleClient", lambda _: client)

    result = await routes.repair(repair_request(workspace))

    assert result.prompt == repaired
    assert result.prompt.count("[Shot ") == 4
    assert "<d>[English] Keep moving.</d>" in result.prompt
    assert "overall_soundscape:\nRain and distant traffic." in result.prompt
    assert any(finding.code == "shot_count" for finding in result.validation.findings)
    assert all(finding.code != "missing_shot_time" for finding in result.validation.findings)
    assert "not authority to reconcile" in str(client.messages)


@pytest.mark.asyncio
async def test_repair_skips_model_when_only_workspace_consistency_is_wrong(workspace_factory, monkeypatch):
    valid_four_shot = FOUR_SHOT_PROMPT.replace("At 09.000", "At 00:05.000")
    workspace = workspace_factory(shots=3, dialogue="Narrator: Keep moving.")
    monkeypatch.setattr(routes, "OpenAICompatibleClient", lambda _: pytest.fail("repair model should not run"))

    result = await routes.repair(repair_request(workspace, valid_four_shot))

    assert result.prompt == valid_four_shot
    assert [finding.category for finding in result.validation.findings] == ["workspace_consistency"]


@pytest.mark.asyncio
async def test_explicit_repair_deterministically_canonicalizes_bare_labels(workspace_factory, monkeypatch):
    prompt = FOUR_SHOT_PROMPT.replace(
        "A cyclist says", '<Subject 1> has narrower-set eyes relative to Subject 1 and says',
    ).replace("Keep moving.", 'Keep moving about "Picture 1".')
    client = RepairClient(prompt)
    monkeypatch.setattr(routes, "OpenAICompatibleClient", lambda _: client)

    result = await routes.repair(repair_request(workspace_factory(), prompt))

    assert "relative to <Subject 1>" in result.prompt
    assert '"Picture 1"' in result.prompt
    assert "<d>[English] Keep moving" in result.prompt
    assert client.messages is not None


@pytest.mark.asyncio
async def test_explicit_repair_preserves_valid_comparison_retention(workspace_factory, image_asset_factory, monkeypatch):
    workspace = comparison_workspace(workspace_factory, image_asset_factory)
    prompt = comparison_prompt(include_retention=True)
    initial = validate_prompt(prompt, H3Mode.REF2VA, 8, workspace.assets)
    assert not any(item.code.startswith("comparison_scale_") for item in initial.findings)
    assert not any(item.code == "comparison_scale_reference_missing" for item in initial.findings)
    monkeypatch.setattr(routes, "OpenAICompatibleClient", lambda _: pytest.fail("repair model should not run"))

    result = await routes.repair(repair_request(workspace, prompt))

    definition = "<Picture 3> is the Character Comparison / Scale reference for\n<Subject 1> and <Subject 2>, establishing their visible relative height."
    assert definition in result.prompt
    assert "<Picture 3> (scale relationship between [Shot 1]-[Shot 3]): weak_reference - retain the scale relationship." in result.prompt
    assert "<Subject 1> (appears in [Shot 1])" in result.prompt
    assert "<Subject 2> (appears in [Shot 2])" in result.prompt
    assert result.validation.valid


@pytest.mark.asyncio
async def test_explicit_repair_inserts_conservative_missing_comparison_definition(workspace_factory, image_asset_factory, monkeypatch):
    workspace = comparison_workspace(workspace_factory, image_asset_factory)
    prompt = comparison_prompt(include_definition=False, include_retention=True)
    assert any(item.code == "comparison_scale_reference_missing" for item in validate_prompt(prompt, H3Mode.REF2VA, 8, workspace.assets).findings)
    monkeypatch.setattr(routes, "OpenAICompatibleClient", lambda _: pytest.fail("repair model should not run"))

    result = await routes.repair(repair_request(workspace, prompt))

    assert "<Picture 3> is the Character Comparison / Scale reference for <Subject 1> and <Subject 2>" in result.prompt
    assert "their relative height, body-size contrast, and broad body-proportion relationship" in result.prompt
    assert "taller" not in result.prompt and "half a head" not in result.prompt
    assert result.validation.valid


@pytest.mark.asyncio
async def test_explicit_repair_inserts_missing_comparison_retention_without_measurements(workspace_factory, image_asset_factory, monkeypatch):
    workspace = comparison_workspace(workspace_factory, image_asset_factory)
    prompt = comparison_prompt()
    assert any(item.code == "comparison_scale_retention_missing" for item in validate_prompt(prompt, H3Mode.REF2VA, 8, workspace.assets).findings)
    monkeypatch.setattr(routes, "OpenAICompatibleClient", lambda _: pytest.fail("repair model should not run"))

    result = await routes.repair(repair_request(workspace, prompt))

    assert "<Picture 3> (relative scale relationship): fully_preserved -" in result.prompt
    assert "the configured relative height, body-size contrast, and broad body-proportion relationship" in result.prompt
    assert "linked Subjects are co-visible.\n\ndetailed_description:" in result.prompt
    assert "taller" not in result.prompt and "half a head" not in result.prompt
    assert result.validation.valid


@pytest.mark.asyncio
async def test_explicit_repair_replaces_only_comparison_appears_in_scope(workspace_factory, image_asset_factory, monkeypatch):
    workspace = comparison_workspace(workspace_factory, image_asset_factory)
    prompt = comparison_prompt(appears_in=True)
    assert any(item.code == "comparison_scale_picture_appears_in_shot" for item in validate_prompt(prompt, H3Mode.REF2VA, 8, workspace.assets).findings)
    monkeypatch.setattr(routes, "OpenAICompatibleClient", lambda _: pytest.fail("repair model should not run"))

    result = await routes.repair(repair_request(workspace, prompt))

    assert "<Picture 3> (relative scale relationship): fully_preserved - retain the scale relationship." in result.prompt
    assert "<Subject 1> (appears in [Shot 1])" in result.prompt
    assert "<Subject 2> (appears in [Shot 2])" in result.prompt
    assert result.validation.valid


@pytest.mark.asyncio
async def test_repair_passes_comparison_normalized_prompt_to_model_and_reapplies_it(workspace_factory, image_asset_factory, monkeypatch):
    workspace = comparison_workspace(workspace_factory, image_asset_factory)
    prompt = comparison_prompt(include_retention=True).replace("[Shot 2] At 00:03.000", "[Shot 2]")
    repaired = comparison_prompt(include_definition=False, include_retention=True)
    client = RepairClient(repaired)
    monkeypatch.setattr(routes, "OpenAICompatibleClient", lambda _: client)

    result = await routes.repair(repair_request(workspace, prompt))

    sent = str(client.messages)
    assert "<Picture 3> (scale relationship" in sent
    assert "[Shot 2]" in sent
    assert "<Picture 3> (scale relationship between [Shot 1]-[Shot 3]): weak_reference - retain the scale relationship." in result.prompt
    assert "<Picture 3> is the Character Comparison / Scale reference for\n<Subject 1> and <Subject 2>, establishing their visible relative height." in result.prompt
    assert result.validation.valid
