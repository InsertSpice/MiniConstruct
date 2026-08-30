from __future__ import annotations

import pytest

from miniconstruct.api import routes
from miniconstruct.h3.repair import assemble_repair_prompt
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


def test_repair_prompt_keeps_workspace_context_but_makes_current_output_authoritative(workspace_factory):
    request = repair_request(workspace_factory(shots=3, dialogue="Narrator: Keep moving."))
    assembled = assemble_repair_prompt(request, [])
    text = "\n".join(str(message["content"]) for message in assembled.messages)
    assert "Official MiniMax H3 guide" in text
    assert "Canonical workspace/reference manifest" in text
    assert "not authority to reconcile an intentionally edited output" in text
    assert FOUR_SHOT_PROMPT in text
    assert "do not add, remove, merge, split, or renumber shots" in text


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
