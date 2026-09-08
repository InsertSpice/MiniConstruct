from __future__ import annotations

from miniconstruct.models.workspace import CreativeInterpretation, Workspace


CREATIVE_INTERPRETATION_LABELS = {
    CreativeInterpretation.CONSERVATIVE: "Conservative",
    CreativeInterpretation.BALANCED: "Balanced",
    CreativeInterpretation.EXPANSIVE: "Expansive",
}


_SHARED_PRECEDENCE = """Creative Interpretation changes only how much new creative content may be inferred in genuinely unspecified space; it does not control output length, verbosity, adjective density, or prose richness.
Explicit workspace authority always wins, including Creative Request facts, actions, states, outcomes, and prohibitions; Exact Dialogue; Subject/reference identity and roles; Notes/Reference Labels; wardrobe; comparison/scale; continuation/frame alignment; off-screen requirements; shot count/duration; laterality; audio reuse; and Creative Controls.
Depict explicit user actions directly as requested. Preserve what is done, its target, purpose, state, and specified outcome; different behavior that merely suggests the same intent is not a substitute."""


_MODE_POLICIES = {
    CreativeInterpretation.CONSERVATIVE: """Realize explicit actions and conditions literally, preserving their targets, purposes, states, and outcomes.
Add only connective motion, performance, staging, and continuity needed for a natural, coherent, filmable scene: breathing, gaze, posture, facial response, weight shifts, and similar ordinary adjustments are allowed.
Prefer the least unsupported content; do not add props, events, sub-goals, characters, dialogue, or narrative beats without support.
Keep user-unspecified situations pending or unresolved unless an outcome is supplied or required.
Let the final beat come from performance, gaze, posture, or composition; do not make characters rigid or robotic.""",
    CreativeInterpretation.BALANCED: """Preserve explicit actions and intent literally.
In genuinely unspecified space, add restrained compatible body language, gestures, staging, environmental motion, and unconstrained camera behavior that support an existing requested beat.
Avoid major new plot events, characters, dialogue, identity or wardrobe changes, and action-driving props unless explicitly supported.
Balanced elaborates the request without replacing its explicit behavior or becoming merely more verbose.""",
    CreativeInterpretation.EXPANSIVE: """When meaningful unspecified space exists, proactively enrich some of it with compatible secondary performance, environmental activity, incidental props, richer staging/blocking, camera choreography, reactions/transitions, or small supporting story beats.
Choose what fits; no category is required.
Newly introduced scene elements must be concretely present and consistently depicted.
Preserve all explicit actions, targets, purposes, states, outcomes, and other workspace authority.
Do not invent additional spoken dialogue or named Subjects, and do not force invention into a closely specified scene.
Use no quotas or target output length.""",
}


def creative_interpretation_policy(workspace: Workspace) -> str:
    """Return the one selected creative-inference policy for the prompt writer."""
    mode = workspace.creative_interpretation
    return "\n".join((
        f"Creative Interpretation: {CREATIVE_INTERPRETATION_LABELS[mode]}",
        _SHARED_PRECEDENCE,
        _MODE_POLICIES[mode],
    ))
