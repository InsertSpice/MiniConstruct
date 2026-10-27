"""Provider-free checks of the fresh-authoring boundary and policy authority."""
import json

import pytest

from miniconstruct.h3.builder import assemble_prompt, instruction_layers
from miniconstruct.h3.natural_liveliness import (
    NATURAL_LIVELINESS_POLICY, NATURAL_LIVELINESS_TITLE, natural_liveliness_layer,
)
from miniconstruct.h3.repair import assemble_repair_prompt
from miniconstruct.h3.revision import assemble_revision_prompt
from test_repair import repair_request
from test_revision import revision_request


@pytest.mark.parametrize("detail", ["concise", "standard", "detailed"])
@pytest.mark.parametrize("story", [False, True])
def test_fresh_generation_and_inspector_include_one_policy(workspace_factory, detail, story):
    workspace = workspace_factory(creativeControls={"promptDetail": detail})
    context = {"currentGeneration": {"scenePlan": "Hold eye contact in numb stillness."}} if story else None
    assembled = assemble_prompt(workspace, False, story_context=context)
    section = "## %s\n\n%s" % natural_liveliness_layer()
    assert assembled.messages[0]["content"].count(section) == 1
    assert assembled.inspector_text.count(f"===== {NATURAL_LIVELINESS_TITLE} =====\n{NATURAL_LIVELINESS_POLICY}") == 1
    assert assembled.messages[0]["content"].count(NATURAL_LIVELINESS_POLICY) == 1
    assert natural_liveliness_layer() not in instruction_layers(workspace, False)
    if story:
        assert "H3 Prompt Detail policy" not in str(assembled.messages)
        standard = workspace_factory(creativeControls={"promptDetail": "standard"})
        assert assembled.messages == assemble_prompt(standard, False, story_context=context).messages


@pytest.mark.parametrize("story", [False, True])
def test_revision_effective_messages_exclude_policy(workspace_factory, story):
    request = revision_request(workspace_factory())
    if story:
        request._story_context = {"currentGeneration": {"scenePlan": "Remain still."}}
    assembled = assemble_revision_prompt(request)
    assert NATURAL_LIVELINESS_TITLE not in json.dumps(assembled.messages)
    assert NATURAL_LIVELINESS_POLICY not in json.dumps(assembled.messages)
    assert NATURAL_LIVELINESS_TITLE not in assembled.inspector_text


def test_repair_effective_messages_exclude_policy(workspace_factory):
    assembled = assemble_repair_prompt(repair_request(workspace_factory()), [])
    assert NATURAL_LIVELINESS_TITLE not in json.dumps(assembled.messages)
    assert NATURAL_LIVELINESS_POLICY not in json.dumps(assembled.messages)
    assert NATURAL_LIVELINESS_TITLE not in assembled.inspector_text


def test_policy_boundaries_and_authority():
    for phrase in (
        "open face remains clearly visible", "sparse, context-appropriate",
        "established gaze target, expression, pose, and attention state",
        "brief eyelid closure followed by reopening", "prolonged eye closure",
        "lowered eyes", "looking away", "redirected gaze", "manufacture emotion",
        "preserving the held pose, gaze, and expression takes priority",
        "schedule blinking every N seconds", "require every character to blink",
        "synchronize multiple characters' blinks", "closed eyes and sleep",
        "nonliving subjects", "obscured/not-visible faces", "nods or acknowledgements for listeners",
        "Tone / Performance controls", "Creative Interpretation", "exact dialogue",
        "Story Scene Plans / Story Memory / Character Profiles", "continuity",
        "identity/reference evidence", "official H3 structure", "no additional invention latitude",
        "transient eyelid position, gaze, or expression",
    ):
        assert phrase in NATURAL_LIVELINESS_POLICY
    for token in ("^_^", '-_-"', ">_<", "sweat", "anger marks", "symbolic"):
        assert token not in NATURAL_LIVELINESS_POLICY
