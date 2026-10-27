"""One soft policy for Project-level Story planning style.

Structural/signed operation authority, explicit instructions, protected fields,
exact dialogue, current Story Memory and continuity, Story Plan and Sequence
Context, Character Profiles and Creative Direction all outrank this policy.
Creative Direction controls tone, performance, pacing and beat density;
creativity controls local invention latitude; detail controls articulation.
"""
from miniconstruct.models.story_planning_style import StoryPlanningStyle


def story_planning_style_policy(style: StoryPlanningStyle, *, scene_plan_editable: bool = True) -> str:
    """Return no text for the baseline; scope-bound Revisions omit Scene Plan guidance."""
    if not scene_plan_editable:
        return ""
    clauses = []
    if style.creativity == "faithful":
        clauses.append(
            "Stay close to supplied Story Plan, Premise and other story authority. Minimize unsupported "
            "local invention or new narrative implications; still infer necessary connective observable "
            "action and reaction so the Scene Plan is coherent and directable."
        )
    elif style.creativity == "exploratory":
        clauses.append(
            "Where authority leaves room, contribute plausible local connective invention: character "
            "business, object or environmental interaction, spatial staging, small complications, "
            "hesitation, revealing reaction or physical cause and effect. Seek readable, performable "
            "specificity. Never contradict the Story Plan, Sequence Context, Story Memory, current "
            "factual state, Character Profiles, protected authoring or exact dialogue; do not invent "
            "major plot facts, resolve open threads prematurely, consume later events, add dialogue "
            "merely for creativity, change operation boundaries, shot count or reference authority, "
            "or turn every scene into spectacle."
        )
    if style.scene_plan_detail == "concise":
        clauses.append(
            "Make each Scene Plan compact but directable, never vague: retain what changes, who acts "
            "and reacts, essential causal and spatial progression, and necessary performance direction. "
            "Avoid decorative prose, redundant micro-actions, camera syntax and H3 timestamps."
        )
    elif style.scene_plan_detail == "detailed":
        clauses.append(
            "Articulate the chosen development in concrete observable progression: cause, action, "
            "reaction and resulting state. Where useful describe attention, posture, gesture, blocking "
            "or spatial interaction, and object or environment interaction. Stillness and restraint "
            "can be detailed. Beat Duration / Density controls how many developments belong in a "
            "Generation; detail does not add extra plot or a fixed beat count. Do not add H3 timestamps, "
            "shot grammar, camera cuts, extra shots, repeated exact dialogue or stable identity facts; "
            "do not duplicate Camera Intent or Incoming / Outgoing State or dictate every frame."
        )
    if not clauses:
        return ""
    return "\nStory Planning Style (soft guidance, subordinate to saved and operation authority):\n" + "\n".join(clauses) + "\n"
