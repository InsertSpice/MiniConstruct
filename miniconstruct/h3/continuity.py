"""H3 planned-continuity authoring policy and evidence warnings."""
from miniconstruct.models.h3_continuity import H3ContinuityContext


def one_shot_continuity_context(context: H3ContinuityContext | None) -> H3ContinuityContext | None:
    """Retain technical continuity without treating Story Memory as a one-shot brief."""
    if context is None:
        return None
    return context.model_copy(update={"recent_summary": "", "durable_facts": []})


RELATIONSHIP_POLICY = {
    "continue_previous_shot": "Same event and same ongoing shot/action. Shot 1 must open from the predecessor ending shot: preserve its camera framing and orientation, Subject pose/orientation, spatial relationship, environment state and immediate action momentum. At frame zero, when actual predecessor visual evidence is supplied, continue directly from its rendered pose, body orientation, placement, framing, object placement, and ongoing motion; do not restage these to approximate a conflicting planned Incoming State. Incoming State remains semantic/planned guidance. Continue the existing motion naturally before progressing into the current Scene Plan. Camera / Shot Intent is subordinate at this opening boundary if it implies an instant framing or composition reset; any desired profile, medium, or other camera change occurs after continuation begins as a motivated transition. Do not begin with an unmotivated establishing shot or torso/profile reset. Exact I2VA/FL2VA/L2VA frame anchors retain their stronger authority. Native H3 latent context is execution state and supplies no prompt-visible Video or Picture. When execution carries native continuation state, that runtime state governs the physical opening; do not reconstruct or reset it from a textual approximation, and do not expose native identifiers or settings. When no actual predecessor visual evidence or native continuation is supplied, use textual continuity, including Incoming State, as the best available opening guidance. For a supplied Video, use normal Video continuation semantics from its ending state. Do not invent a media label or a [video continuation] task for native-only context.",
    "new_shot_preserve_continuity": "Same scene, new camera shot: preserve environment identity, subject appearance, wardrobe, props, condition, positions/state and story facts unless current authoring explicitly changes them. Change framing/camera as requested; continuity evidence is NOT an instruction to duplicate the previous composition.",
    "match_previous_ending_frame": "Match the previous endpoint exactly only when an actual matching endpoint Picture or continuation input is supplied. Without that evidence preserve textual state only; never claim exact visual matching from text alone.",
    "independent": "No predecessor-local visual or shot continuity assumption. Only Sequence-wide facts and explicit current authoring apply; do not inherit a predecessor's local scene/state.",
}

CONTINUITY_POLICY = """Planned Continuity is AUTHORING context, not text to mechanically repeat. Planned continuity is not observed truth.
For exact frame-zero physical state, supplied actual predecessor visual evidence governs pose, orientation, placement, framing, object placement and ongoing motion over a textual approximation in Incoming State. Incoming State remains semantic/planned guidance and is the best physical opening description when no actual visual or native continuation evidence is supplied. Explicit current authoring may request a deliberate change or reset. When execution carries native continuation, its runtime opening state governs physical frame zero; the writer does not receive latent details and must not restage a textual approximation. Do not expose execution IDs, artifact keys, profile IDs or settings in the prompt.
Without actual visual evidence, textual continuity precedence is explicit current user authoring > Incoming State > predecessor user-corrected Observed End State > predecessor machine Observed End State > predecessor Intended Outgoing State > trusted relevant Story Memory > stable Character Profile / reference defaults. Observed state is visual evidence only: preserve nonvisual knowledge, relationships and motivations from Story Memory. Visibility not_visible or uncertain proves no unseen wardrobe or condition. If the predecessor plan requested jacket removal but its effective observation shows the jacket still on, keep it on unless current authoring explicitly changes it. Effective state already includes any user corrections. Observations are textual evidence, not attached endpoint Pictures or Videos. Identity evidence says who a Subject is; continuity says what is currently true. A removed jacket stays absent even when an older identity Picture or Profile shows it. Never mutate historical Pictures or Profiles.
Sequence Context: preserve relevant stable world/sequence constraints; do not force every fact visibly into every shot or gratuitously paraphrase irrelevant guidance.
Intended Outgoing State: desired END state, never assumed true at frame 0. Author a plausible transition from Incoming State to this outcome.
Camera / Shot Intent: shot-authoring authority, subordinate to harder I2VA/FL2VA/L2VA exact-frame anchors. Environment-only references do not override this camera intent.
Story Memory below is trusted predecessor background continuity, not observed output. Preserve relevant facts without forcing them visibly into the shot. It contains no Open Threads or current Generation's still-future after-state.
This visible-state precedence also governs strict Subject Identity Fidelity and Outfit reference defaults; preserve stable identity without restoring superseded wardrobe, pose or object state."""


def continuity_layers(context: H3ContinuityContext | None) -> list[tuple[str, str]]:
    if context is None:
        return []
    return [("Planned Continuity authoring policy", CONTINUITY_POLICY + "\n" +
             RELATIONSHIP_POLICY.get(context.relationship_to_previous, "No predecessor relationship was authored.")),
            ("Saved Project H3 continuity context", context.model_dump_json(by_alias=True, indent=2))]


def continuity_warnings(context: H3ContinuityContext | None, active_assets) -> list[str]:
    if context is None:
        return []
    warnings = list(context.warnings)
    assets = [a for a, _ in active_assets]
    continuation = any(a.kind == "video" and a.role in {"continuation_source", "tail_reference_continuation"}
                       and a.media_payload is not None for a in assets)
    endpoint = any(a.kind == "image" and a.image is not None and a.role == "first_frame_anchor" for a in assets)
    if context.relationship_to_previous == "match_previous_ending_frame" and not (endpoint or continuation):
        warnings.append("Exact previous-ending-frame matching cannot be guaranteed from text alone; supply a matching first-frame Picture or continuation Video.")
    elif context.relationship_to_previous in {"continue_previous_shot", "new_shot_preserve_continuity"}:
        visual = continuation or any(a.kind == "image" and a.image is not None and a.role in {
            "environment", "continuity_state", "first_frame_anchor", "subject_identity"} for a in assets)
        if not visual:
            warnings.append("Same-scene continuity has no usable visual continuity reference in this H3 mode; authoring will rely on text.")
    return warnings
