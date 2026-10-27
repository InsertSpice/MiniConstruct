"""Provider-free composition guard for ordinary Ref2VA Story authoring."""
from pathlib import Path

from miniconstruct.context_capacity import estimate_request_tokens
from miniconstruct.benchmark.runner import load_case
from miniconstruct.h3.builder import assemble_prompt, instruction_layers
from miniconstruct.h3.creative_controls import compile_creative_controls
from miniconstruct.models.h3_continuity import H3ContinuityContext
from miniconstruct.story_planning import SCENE_PLAN_POLICY


def representative_request():
    path = Path(__file__).resolve().parents[1] / "benchmarks/cases/ref2va-height-comparison/workspace.json"
    _, workspace, _, _ = load_case(path.parents[2], path.parent.name)
    workspace.creative_request = (
        "The two friends search a city courtyard for a missing clue. One notices a bicycle wheel, "
        "calls the other over, and they follow the marks it left while preserving their established appearances."
    )
    continuity = H3ContinuityContext(
        sequenceContext="A grounded city mystery; both friends remain together.",
        incomingState="Both arrive at the courtyard, still searching and uncertain.",
        intendedOutgoingState="They identify the wheel as a clue and turn toward the next street.",
        relationshipToPrevious="continue_previous_shot",
        cameraIntent="Follow their shared discovery in a continuous moving shot.",
        recentSummary="The friends agreed to search together after the previous discovery.",
        durableFacts=["The friends trust each other."],
    )
    return workspace, continuity


def test_ref2va_story_prompt_composition_has_single_official_guide_and_no_planner_policy():
    workspace, continuity = representative_request()
    layers = instruction_layers(workspace, True, (), continuity)
    assembled = assemble_prompt(workspace, True, (), continuity)
    text_chars, images, estimated = estimate_request_tokens(assembled.messages)
    system = assembled.messages[0]["content"]
    user = assembled.messages[1]["content"][0]["text"]
    guide = next(body for title, body in layers if title == "Official MiniMax H3 guide (normative)")
    assert system.count(guide) == 1
    assert system.count("# Full-Reference Mode Rewrite Output Format Guide") == 1
    assert "# Video Prompt Writing Guide (T2VA / I2VA / FL2VA / L2VA)" not in system
    assert SCENE_PLAN_POLICY.strip() not in system + user
    assert "The two friends search" in user
    assert "continue_previous_shot" in system
    assert "Picture 3" in system
    assert "subject_definitions" in guide and "retention_analysis" in guide
    assert "Non-diegetic music: forbidden" in user
    assert images == 3
    # Printed only with pytest -s; the estimate excludes image binary and reserves image tokens separately.
    print(f"H3 text: system={len(system)}, user={len(user)}, total={text_chars}; approximate text tokens={(text_chars + 2) // 3}; images={images}; estimator total={estimated}")
    for title, body in layers:
        print(f"  {title}: {len(body)} chars")
    print(f"  Creative Request: {len(workspace.creative_request)} chars")
    print(f"  Creative Controls: {len(compile_creative_controls(workspace, True))} chars")
    print(f"  Dialogue: {len(workspace.dialogue)} chars")
    print(f"  Reference Labels: {len(workspace.reference_labels)} chars")


def test_reference_labels_are_sent_once_and_continuity_values_survive_compaction():
    workspace, continuity = representative_request()
    workspace.reference_labels = "Picture 1 and Picture 2 establish the friends' separate identities."
    assembled = assemble_prompt(workspace, True, (), continuity)
    sent_text = assembled.messages[0]["content"] + assembled.messages[1]["content"][0]["text"]
    assert sent_text.count(workspace.reference_labels) == 1
    for value in (continuity.incoming_state, continuity.intended_outgoing_state,
                  continuity.recent_summary, continuity.durable_facts[0], continuity.camera_intent):
        assert value in sent_text


def test_static_h3_prefix_is_deterministic_and_precedes_generation_context():
    workspace, continuity = representative_request()
    first = assemble_prompt(workspace, True, (), continuity)
    second = assemble_prompt(workspace, True, (), continuity)
    assert first.messages == second.messages
    system = first.messages[0]["content"]
    assert system.index("MiniConstruct core operating instructions") < system.index("Official MiniMax H3 guide")
    assert system.index("Official MiniMax H3 guide") < system.index("Saved Project H3 continuity context")
    changed = workspace.model_copy(update={"creative_request": "Different request"})
    assert assemble_prompt(changed, True, (), continuity).messages[0] == first.messages[0]
    assert assemble_prompt(changed, True, (), continuity).messages[1] != first.messages[1]
