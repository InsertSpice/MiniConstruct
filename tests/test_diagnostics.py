from __future__ import annotations

import json

from miniconstruct.h3.builder import assemble_prompt
from miniconstruct.llm.compatibility import build_generation_payload
from miniconstruct.llm.diagnostics import cache_input_fingerprint
from miniconstruct.models.api import LLMSettings


def settings() -> LLMSettings:
    return LLMSettings.model_validate({
        "baseUrl": "http://127.0.0.1:8888/v1", "model": "writer", "supportsVision": True,
    })


def fingerprint(workspace) -> tuple[str, list[dict]]:
    assembled = assemble_prompt(workspace, True)
    payload, _ = build_generation_payload(settings(), assembled.messages, stream=True)
    return cache_input_fingerprint(payload), payload["messages"]


def test_identical_workspace_and_nonsemantic_project_state_are_stable(workspace_factory):
    workspace = workspace_factory()
    first, first_messages = fingerprint(workspace)
    second, second_messages = fingerprint(workspace.model_copy(deep=True))
    renamed, _ = fingerprint(workspace.model_copy(update={"project_name": "UI-only rename", "project_id": "another-id"}))
    assert first == second == renamed
    assert first_messages == second_messages


def test_meaningful_workspace_change_alters_fingerprint(workspace_factory):
    workspace = workspace_factory()
    original, _ = fingerprint(workspace)
    changed, _ = fingerprint(workspace.model_copy(update={"creative_request": "A materially different scene."}))
    assert original != changed


def test_internal_asset_ids_are_not_model_visible_or_cache_relevant(workspace_factory, image_asset_factory):
    first_asset = image_asset_factory("random-uuid-one")
    second_asset = first_asset.model_copy(update={"id": "random-uuid-two"})
    first, first_messages = fingerprint(workspace_factory(mode="Ref2VA", assets=[first_asset]))
    second, second_messages = fingerprint(workspace_factory(mode="Ref2VA", assets=[second_asset]))
    rendered = json.dumps(first_messages)
    assert "random-uuid-one" not in rendered
    assert first == second
    assert first_messages == second_messages


def test_image_bytes_hash_stably_and_asset_order_is_deterministic(workspace_factory, image_asset_factory):
    assets = [
        image_asset_factory("second", order=1, filename="b.png"),
        image_asset_factory("first", order=0, filename="a.png"),
    ]
    first, messages = fingerprint(workspace_factory(mode="Ref2VA", assets=assets))
    second, _ = fingerprint(workspace_factory(mode="Ref2VA", assets=list(reversed(assets))))
    assert first == second
    rendered = json.dumps(messages)
    assert rendered.index("a.png") < rendered.index("b.png")
    assert "AA==" in rendered  # Raw data stays in the request; only diagnostics hash it.
