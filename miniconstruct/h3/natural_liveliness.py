"""Conservative default performance guidance for fresh H3 authoring only."""

NATURAL_LIVELINESS_TITLE = "Natural Liveliness policy"
NATURAL_LIVELINESS_POLICY = """When a living character's open face remains clearly visible and no stronger authority conflicts, avoid an unnaturally frozen facial performance. Where useful, use sparse, context-appropriate brief natural blinking, subtle breathing-linked facial motion, or tiny same-target gaze refocusing. Momentary cues should return immediately to the established gaze target, expression, pose, and attention state.
A natural blink is a brief eyelid closure followed by reopening. Do not turn it into prolonged eye closure, lowered eyes, looking away, redirected gaze, or a new emotional beat. Small physiological motion must not redirect attention or manufacture emotion. During deliberate stillness, preserving the held pose, gaze, and expression takes priority; a brief natural blink or subtle breathing may be enough.
Do not schedule blinking every N seconds, force constant facial motion, require every character to blink, or synchronize multiple characters' blinks. Preserve deliberately closed eyes and sleep without blinking. Do not add biological facial motion to nonliving subjects or force facial cues on obscured/not-visible faces. Do not invent dialogue, plot, gestures, emotional transitions, nods or acknowledgements for listeners, sighing, smiling, frowning, or emotional eyebrow acting merely to demonstrate liveliness.
User intent, exact performance direction, Tone / Performance controls, Creative Interpretation, exact dialogue, Story Scene Plans / Story Memory / Character Profiles, continuity, identity/reference evidence, and official H3 structure remain stronger authority. This policy grants no additional invention latitude or performance intensity. Preserve stable identity without treating transient eyelid position, gaze, or expression as identity drift or freezing a reference Picture's transient expression."""


def natural_liveliness_layer() -> tuple[str, str]:
    return NATURAL_LIVELINESS_TITLE, NATURAL_LIVELINESS_POLICY
