"""One-shot H3 articulation-depth policy for ordinary Main Prompt generation."""
from miniconstruct.models.workspace import H3PromptDetail


_POLICIES = {
    H3PromptDetail.CONCISE: """H3 Prompt Detail: Concise. Write a compact, complete, directable H3 prompt. Prioritize the essential requested action and temporal progression, important selected performance, necessary camera/staging, required reference use, required sound/dialogue, and final resulting state. Preserve every meaningful requested development, necessary timing, exact dialogue, reference authority, identity, and required H3 grammar; never become vague or compress distinct developments into an ambiguous summary. Remove decorative prose, redundant adjectives, repeated descriptions, unnecessary reference restatement, and explanations of obvious transitions. This controls articulation depth only; it grants no extra creative license and does not change performance, shot count, duration, or explicit stillness.""",
    H3PromptDetail.DETAILED: """H3 Prompt Detail: Detailed. Describe the same chosen Main Prompt interpretation with richer concrete, observable temporal direction where useful: action progression, cause and reaction, posture, gaze or attention, facial response, hand/object interaction, spatial blocking, environmental response, sound/physical response, camera response to meaningful action, and the resulting state. Stay faithful to the Main Prompt and all stronger workspace authority. Detail controls articulation depth, not Creative Interpretation latitude; do not add plot events, unrelated props, characters, unsupported emotional beats, dialogue, or action solely to add length. Preserve exact dialogue, references, identity and selected Tone / Performance. H3 Setup shot count and duration remain authoritative. Obey required H3 timestamp grammar; do not increase shot count or introduce extra cuts or timestamped beats merely to add detail, and do not overload the requested duration with implausible action. Use no fixed beat count or constant-motion requirement. Preserve deliberate stillness, silence, held gaze, waiting, restraint, numbness, and quiet observation; add only appropriate subtle observable detail without forcing movement.""",
}


def h3_prompt_detail_policy(detail: H3PromptDetail) -> str:
    """Return only non-default guidance; Standard deliberately preserves the baseline."""
    return _POLICIES.get(detail, "")
