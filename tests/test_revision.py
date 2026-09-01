from __future__ import annotations

import asyncio
import json

from pydantic import ValidationError
import pytest

from miniconstruct.api.routes import stream_revision_events
from miniconstruct.h3.revision import assemble_revision_prompt, splice_revision, validate_replacement
from miniconstruct.llm.client import LLMBackendError, LLMStreamEvent
from miniconstruct.models.api import RevisionRequest, RevisionSelection


PROMPT = (
    "integrated_multimodal_description:\n"
    "[Shot 1] A cyclist waits.\n"
    "[Shot 2] The cyclist crosses.\n\n"
    "overall_soundscape:\nRain.\n\n"
    "non_diegetic_music:\nN/A"
)


def revision_request(workspace, *, full_prompt=PROMPT, selected="[Shot 2] The cyclist crosses.", instruction="Make the motion faster.", vision=False):
    start = full_prompt.index(selected)
    return RevisionRequest.model_validate({
        "workspace": workspace.model_dump(mode="json", by_alias=True),
        "llm": {"baseUrl": "http://local.test/v1", "model": "writer", "supportsVision": vision},
        "outputIndex": 0,
        "selection": {
            "fullPrompt": full_prompt,
            "beforeSelection": full_prompt[:start],
            "selectedText": selected,
            "afterSelection": full_prompt[start + len(selected):],
        },
        "instruction": instruction,
    })


def decode_event(raw: str) -> tuple[str, dict]:
    lines = raw.strip().splitlines()
    return lines[0].split(":", 1)[1].strip(), json.loads(lines[1].split(":", 1)[1].strip())


def test_selection_snapshot_rejects_malformed_or_blank_content():
    with pytest.raises(ValidationError, match="does not reconstruct"):
        RevisionSelection.model_validate({
            "fullPrompt": "abc", "beforeSelection": "a", "selectedText": "x", "afterSelection": "c",
        })
    with pytest.raises(ValidationError, match="non-whitespace"):
        RevisionSelection.model_validate({
            "fullPrompt": "a  c", "beforeSelection": "a", "selectedText": "  ", "afterSelection": "c",
        })


def test_splice_is_exact_with_repeated_text_and_unicode():
    full = "🎬 同じ\nfirst 同じ\nsecond 同じ"
    start = full.rindex("同じ")
    selection = RevisionSelection.model_validate({
        "fullPrompt": full, "beforeSelection": full[:start], "selectedText": "同じ", "afterSelection": full[start + 2:],
    })
    assert splice_revision(selection, "改訂✨") == "🎬 同じ\nfirst 同じ\nsecond 改訂✨"


def test_revision_prompt_has_full_h3_workspace_and_dialogue_context(workspace_factory):
    dialogue = "[Japanese] 行こう。"
    request = revision_request(workspace_factory(dialogue=dialogue), instruction="Make the performance angrier.")
    assembled = assemble_revision_prompt(request)
    text = json.dumps(assembled.messages, ensure_ascii=False)
    user_content = assembled.messages[-1]["content"]
    assert "Official MiniMax H3 guide" in text
    assert "Canonical workspace/reference manifest" in text
    assert "CURRENT H3 PROMPT" in user_content
    assert "<MINICONSTRUCT_SELECTION>" in user_content
    assert "</MINICONSTRUCT_SELECTION>" in user_content
    assert user_content.count("[Shot 2] The cyclist crosses.") == 1
    assert user_content.count("integrated_multimodal_description:") == 1
    assert dialogue in text
    assert "Exact Dialogue field is authoritative" in text
    assert "Return ONLY replacement text" in text


def test_revision_uses_one_system_message_and_replaces_generation_policy(workspace_factory):
    assembled = assemble_revision_prompt(revision_request(workspace_factory()))
    assert [message["role"] for message in assembled.messages] == ["system", "user"]
    system = assembled.messages[0]["content"]
    assert "Selective revision policy" in system
    assert "Generation policy" not in system
    assert "===== Selective revision policy =====" in assembled.inspector_text


def test_revision_context_marks_one_unicode_selection_without_serializing_full_prompt_twice(workspace_factory):
    prompt = "before 🎬\n[Shot 1] <d>[Japanese] 行こう。</d>\nafter"
    selected = "[Shot 1] <d>[Japanese] 行こう。</d>"
    assembled = assemble_revision_prompt(revision_request(
        workspace_factory(dialogue="[Japanese] 行こう。"), full_prompt=prompt, selected=selected,
    ))
    text = assembled.messages[-1]["content"]
    assert prompt not in text
    assert text.count(selected) == 1
    assert "before 🎬\n<MINICONSTRUCT_SELECTION>" in text
    assert "</MINICONSTRUCT_SELECTION>\nafter" in text


def test_revision_preserves_attached_image_parts(workspace_factory, image_asset_factory):
    workspace = workspace_factory(mode="Ref2VA", assets=[image_asset_factory()])
    request = revision_request(workspace, vision=True)
    content = assemble_revision_prompt(request).messages[-1]["content"]
    assert content[0]["type"] == "text"
    assert any(part.get("type") == "image_url" for part in content)


class ScriptedClient:
    def __init__(self, _, events):
        self.events = events
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.closed = True

    async def stream_events(self, _):
        for event in self.events:
            if isinstance(event, Exception):
                raise event
            yield event


@pytest.mark.asyncio
async def test_stream_excludes_reasoning_splices_and_validates_candidate(workspace_factory):
    replacement = "[Shot 2] The cyclist sprints across."
    fake = ScriptedClient(None, [
        LLMStreamEvent("reasoning", "private thought"),
        LLMStreamEvent("content", replacement[:12]),
        LLMStreamEvent("content", replacement[12:]),
    ])
    events = [decode_event(raw) async for raw in stream_revision_events(
        revision_request(workspace_factory()), lambda _: fake,
    )]
    deltas = "".join(data["text"] for event, data in events if event == "delta")
    complete = next(data for event, data in events if event == "complete")
    assert deltas == replacement
    assert complete["candidatePrompt"] == PROMPT.replace("[Shot 2] The cyclist crosses.", replacement)
    assert "validation" in complete and "originalValidation" in complete
    assert "private thought" not in json.dumps(events)
    assert fake.closed


@pytest.mark.asyncio
async def test_three_shot_candidate_can_diverge_from_four_shot_workspace(workspace_factory):
    original = (
        "integrated_multimodal_description:\n"
        "[Shot 1] A cyclist waits.\n"
        "[Shot 2] At 00:01.000, the cyclist starts.\n"
        "[Shot 3] At 00:03.000, the cyclist accelerates.\n"
        "[Shot 4] At 00:05.000, the cyclist arrives.\n\n"
        "overall_soundscape:\nRain.\n\nnon_diegetic_music:\nN/A"
    )
    selected = "[Shot 2] At 00:01.000, the cyclist starts.\n[Shot 3] At 00:03.000, the cyclist accelerates.\n[Shot 4] At 00:05.000, the cyclist arrives."
    replacement = (
        "[Shot 2] At 00:01.000, the cyclist launches and accelerates.\n"
        "[Shot 3] At 00:05.000, the cyclist arrives."
    )
    workspace = workspace_factory(shots=4)
    fake = ScriptedClient(None, [LLMStreamEvent("content", replacement)])
    events = [decode_event(raw) async for raw in stream_revision_events(
        revision_request(workspace, full_prompt=original, selected=selected), lambda _: fake,
    )]
    complete = next(data for event, data in events if event == "complete")
    errors = complete["validation"]["findings"]
    assert complete["candidatePrompt"].count("[Shot ") == 3
    assert errors == [{
        "severity": "ERROR", "code": "shot_count",
        "message": "Requested 4 shots, but the output contains 3.",
        "category": "workspace_consistency",
    }]


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", ["", "   ", "```text\nreplacement\n```", "Here is the replacement: revised shot", "<MINICONSTRUCT_SELECTION>bad</MINICONSTRUCT_SELECTION>"])
async def test_empty_or_fenced_replacement_is_retryable_error(workspace_factory, replacement):
    fake = ScriptedClient(None, [LLMStreamEvent("content", replacement)] if replacement else [])
    events = [decode_event(raw) async for raw in stream_revision_events(
        revision_request(workspace_factory()), lambda _: fake,
    )]
    assert any(event == "error" for event, _ in events)
    assert not any(event == "complete" for event, _ in events)


@pytest.mark.asyncio
async def test_backend_error_never_completes_candidate(workspace_factory):
    fake = ScriptedClient(None, [LLMStreamEvent("content", "partial"), LLMBackendError("lost stream")])
    events = [decode_event(raw) async for raw in stream_revision_events(
        revision_request(workspace_factory()), lambda _: fake,
    )]
    assert any(event == "error" and data["partial"] for event, data in events)
    assert not any(event == "complete" for event, _ in events)


class CancellableRevisionClient(ScriptedClient):
    async def stream_events(self, _):
        try:
            yield LLMStreamEvent("content", "partial")
            await asyncio.Event().wait()
        finally:
            self.upstream_closed = True


@pytest.mark.asyncio
async def test_revision_cancellation_closes_upstream(workspace_factory):
    fake = CancellableRevisionClient(None, [])
    generator = stream_revision_events(revision_request(workspace_factory()), lambda _: fake)
    while decode_event(await anext(generator))[0] != "delta":
        pass
    pending = asyncio.create_task(anext(generator))
    await asyncio.sleep(0)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert fake.upstream_closed and fake.closed


def test_replacement_validation_rejects_full_prompt():
    snapshot = RevisionSelection.model_validate({
        "fullPrompt": PROMPT,
        "beforeSelection": PROMPT[:PROMPT.index("[Shot 2]")],
        "selectedText": "[Shot 2] The cyclist crosses.",
        "afterSelection": PROMPT[PROMPT.index("[Shot 2]") + len("[Shot 2] The cyclist crosses."):],
    })
    assert validate_replacement(snapshot, PROMPT) is not None
    assert validate_replacement(snapshot, f"Here is the revision:\n{PROMPT}") is not None
