"""Optional multi-Generation planning: read, propose, validate; never persist."""
from __future__ import annotations

import json
import re
import inspect
from typing import get_args, get_origin
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from miniconstruct.context_capacity import preflight_request_capacity
from miniconstruct.llm.client import LLMBackendError, OpenAICompatibleClient
from miniconstruct.models.story_planning import (
    LegacyStoryBuildResponse, StoryBuildRequest, StoryBuildResponse, StoryConsistencyWarning, StoryPlanItem, StoryPlanProposal, StoryRevisionRequest,
    StoryReferenceRecommendation, SCOPE_FIELDS, story_revision_response_model,
)
from miniconstruct.models.story_memory import StoryMemoryPatch
from miniconstruct.planning import (
    PLANNER_MEDIA_POLICY, RELATIONSHIP_SEMANTICS, _asset_metadata, _image_parts, _workspace_context,
)
from miniconstruct.models.sequence_subject import (
    character_profile_policy_for, narrative_key, narrative_mapping,
)
from miniconstruct.project_store import ProjectStore, StoryProjectSnapshot, StaleProjectSnapshotError
from miniconstruct.story_planning_style import story_planning_style_policy
from miniconstruct.dialogue_language import story_dialogue_policy


CREATIVE_DIRECTION_POLICY = """
Creative Direction is soft presentation and planning guidance for how the story feels and moves.
Use it only where compatible with stronger authority. Priority is structural or signed authority,
explicit operation instructions, saved exact or authoritative Generation fields, factual Story
Memory or protected boundaries, Story Idea or Sequence Context, then Creative Direction. It cannot
override exact dialogue, assets, or response constraints.
"""


SEQUENCE_CONTEXT_POLICY = """
Generated Sequence Context is durable sequence-level framing: premise, setting/world
conditions, broad narrative direction, stable stylistic/story constraints, and persistent
high-level continuity guidance. The requested Generation count is a temporary operation
window, not the final Sequence length. Generated Sequence Context must remain valid if
the Sequence later grows through Plan Next or Insert Between. Operation counts and window
labels are planning mechanics, not durable story facts: do not write them into Sequence
Context unless they originate from explicit user authority. This applies to Initial Range,
Plan Next, Insert Between, and Replan Range. Do not describe a Sequence as "a four-Generation
story", "a six-Generation sequence", "this four-part plan", "the requested three clips",
"the Initial Range", or "the current planning window" based on the requested operation.
Keep requested counts and range boundaries in structured operation input/output to produce
the correct items. Preserve existing user-authored nonblank Sequence Context exactly,
including deliberate wording such as "This is a four-part anthology".
"""


SCENE_PLAN_POLICY = """
The Story Planner writes a coherent story from Story Plan / Premise into ordered Scene Plans.
A Scene Plan is one free-form, ordered account of how a Generation develops from beginning
to end, not a synopsis, H3 shot plan, timestamp schedule, checklist, or Workspace Main Prompt.
Describe what meaningfully develops, not just a static pose or state with abstract intent.
Where Story authority supports it, use concise observable progression in action, movement,
attention, interaction, reaction, posture or state, and spatial relationships; show how
the beginning leads to development and an outgoing state. Where natural, show
cause, reaction and consequence. Keep Scene Plan prose compact and useful, not decorative
atmosphere, repeated description, or detailed H3 execution instructions.
Multiple developments may occur within one Generation; a brief action should normally lead
into what happens next rather than occupy the entire Generation. When stronger authority leaves
details open, invent plausible local connective action, dialogue, discoveries, obstacles,
reactions, humour and interactions. Keep major plot facts and outcomes grounded in stronger
authority. Do not consume later Generation events or invent new plot discoveries merely
to make this Generation active. Intentional stillness, silence, a pause, held gaze,
waiting, or restrained reaction can itself be the development; do not add arbitrary
movement or pad a quiet scene. Do not impose a beat count, fixed beat interval,
action count, minimum length, or force dialogue or motion.
Respect saved Scene Plans, exact dialogue, Character Profiles, Story Memory, continuity,
Subjects, references, and explicit user authority. Use dialogue proactively when it
naturally improves character interaction, conflict, humour, revelation, or relationship;
leave a scene silent when silence works better. Never rewrite existing nonblank exact
dialogue unless an explicit Revision scope permits it.
"""

SAME_SHOT_OPENING_POLICY = """
For relationshipToPrevious == continue_previous_shot, Scene Plan and Camera Intent
start at the predecessor ending shot boundary. Continue its best available ending
framing, camera orientation, Subject pose/orientation, movement, spatial relationships,
environment state and immediate action momentum. Use current explicit Incoming State
and user authoring first, then corrected observation, observation, intended predecessor
outgoing state, trusted Story Memory and stable defaults in that order. An intended
outgoing state is planned evidence, never a claim that a rendered frame was observed.
Do not open with an unmotivated establishing shot, new torso/profile framing, camera
reset or composition reset. If Camera Intent calls for different framing, begin at
the predecessor ending framing and describe the change after continuation starts.
For example, a predecessor tight face close-up facing camera may ease outward toward
a medium profile later; it must not start at that medium profile.
"""

STORY_DURATION_POLICY = """
Choose each new Story Generation's duration independently from the full 4–15 second
range according to its scene's natural pacing and a meaningful story or visual handoff.
Do not make a new Generation for each small action or pad an action to consume duration.
Propose durationSeconds for new slots; if omitted or invalid, the server uses G1's
saved duration as a fallback, not as a preference. Keep saved Generation 1's duration.
Existing Generation durations remain fixed during Replan.
"""

STORY_SHOT_PLAN_POLICY = """
Each Generation may contain one or several camera shots. Return shotCount and cameraIntent
for every Generation. Choose the fewest shots that tell its action clearly within its actual
4–15 second duration. Add purposeful coverage for a reaction, insert, reveal, composition,
pace, or cause and consequence; never add cuts just because more shots are available.
A quiet moment may be one held shot. Do not mechanically vary counts by Generation number,
fragment every action, or overcut four seconds. Use the same positive-integer legal range
as Workspace.shots (positive integers, with no separate Story maximum). cameraIntent
is the ordered Shot Plan / Camera Intent: for multiple
shots distinguish Shot 1, Shot 2, etc. Describe framing, visual purpose and camera movement
within each shot versus an actual cut. No timestamps or H3 syntax are needed.
Incoming State constrains the opening of Shot 1. For continue_previous_shot, Shot 1 must
begin from the predecessor's ending framing, orientation, pose, positions, motion,
expression and environment as available. Later shots may deliberately cut or reframe.
Intended Outgoing State constrains the END of the final planned shot; the next Generation
continues from that final state, never from an intermediate composition. For an insertion
or replan, also honor the untouched downstream incoming boundary. Creative Direction may
influence pacing qualitatively, not through a numeric shot-count formula.
"""

STORY_SYSTEM = """You are MiniConstruct's optional Story Planner. Plan authoring fields,
not a finished H3 prompt. Return exactly one JSON object matching the supplied JSON
schema, without Markdown or commentary. Preserve the supplied base snapshot fields,
planItemIds, existingGenerationIds, item order and exact item count.
Each item is one coherent, feasible Generation that may contain several shots. Distribute the high-level idea
across the Sequence, respecting duration, mode, Subjects, controls and asset metadata.
Distinguish semantic continuity from exact composition/frame continuity. Use deliberate
camera variety for new shots when useful. Adjacent Incoming/Outgoing states should
make narrative sense, but need not be identical strings. Never blindly copy endpoints.
Generation 1 relationship is null; all later relationships use the closed enum.
References are advisory: only recommend committed sourceGenerationId + assetId pairs
present in sourceGenerations. Proposed new items own no assets. Never invent, attach,
copy assets, invent Subjects or change Workspace settings/modes. Creative Controls are
not a model-editable Build surface.
Use consistencyWarnings for transitions that may need manual reconciliation. Warnings
reference planItemId and relatedPlanItemId, direction upstream/downstream, and message.
""" + SEQUENCE_CONTEXT_POLICY + SCENE_PLAN_POLICY + SAME_SHOT_OPENING_POLICY + STORY_SHOT_PLAN_POLICY + PLANNER_MEDIA_POLICY + RELATIONSHIP_SEMANTICS

BUILD_AUTHORITY = """BUILD field ownership: use the high-level idea to complete the plan.
The sparse draft is read-only source authority, not a response template. Missing text
fields are unfilled planning work, not instructions to stay blank: you MUST fill each
with a useful proposal, including on Generation 1. Author scenePlan, incomingState,
intendedOutgoingState and cameraIntent for every item. Do not invent fallback filler.
dialogue is different: it is exact spoken/sung wording, not direction or sound design.
It may be blank for a deliberately silent Generation; do not invent speech for silence.
When dialogue is useful, write its exact free-form text. For more than one distinct
vocal event or speaker turn, use existing canonical Name: or Subject N: textual
speaker labels on separate lines. A single utterance with an ellipsis or pause remains
one utterance; do not split it among Subjects based on punctuation.
Technical Subject N labels remain valid. Existing nonblank
dialogue is protected exact authority and must be reproduced unchanged.
Every existing nonblank text field, saved non-null relationship and nonblank Sequence
Context is backend-owned authority; it will be preserved verbatim even if the idea
conflicts. Existing assets, Subjects and settings remain read-only.
For later items without a saved relationship, actively choose relationshipToPrevious
from the story and relationship semantics. Use independent only when the story calls
for it. Generation 1 relationship remains null. No provisional relationship is supplied.
Return the smaller responseSchema: sequenceContext, items and consistencyWarnings.
Each item supplies planItemId and planning proposals, not existingGenerationId.
The backend constructs baseProjectId, baseProjectUpdatedAt, baseSequenceFingerprint
and existingGenerationIds from its trusted request snapshot. Do not echo these fields.
Use each issued planItemId exactly once, in supplied order; never invent or reorder slots.
"""

REVISION_AUTHORITY = """You are MiniConstruct's Story Revision planner, editing authoring
fields, not finished H3 prompts. Return exactly one JSON object matching responseSchema:
patches only, never a full draft. Return exactly one patch for EVERY selectedPlanItemId,
using its immutable planItemId, with EVERY editableField explicitly present. Patch order
does not matter. Only selected items x selected fields are writable. scene_plan means
scenePlan. All contextItems and sourceGenerations are read-only evidence from the
current draft (including manual edits) and saved sources; do not echo their other fields,
Sequence Context, ordinals, existingGenerationIds, references or warnings into output.
The backend applies patches and generates boundary warnings without changing neighbors.
Strings may intentionally be empty; dialogue is exact spoken/sung wording, not direction
or sound design. Explicit dialogue "" means silence; omitting it is invalid when selected.
Generation 1 relationship must be null; later relationships must use the closed enum.
Respect neighboring continuity and feasible shot intent, mode and duration.
sequenceNarrativeCast contains relevant canonical Subjects. The current narrativeName and
technical Subject N label identify the SAME stable subjectId. Newly authored prose must use
the current narrativeName; previous names are recognition-only and are not output vocabulary.
Cast availability does not imply shot participation. Never invent Subjects or references.
Reference identity is sourceGenerationId + assetId; recommendations remain read-only.
Creative Controls are Workspace authority and are never editable through Story Revision.
Only claim image observations from Pictures actually supplied as vision inputs.
""" + SEQUENCE_CONTEXT_POLICY + SCENE_PLAN_POLICY + SAME_SHOT_OPENING_POLICY + STORY_SHOT_PLAN_POLICY + PLANNER_MEDIA_POLICY + RELATIONSHIP_SEMANTICS

_JSON_FENCE = re.compile(r"\A```(?P<language>json)?\r?\n(?P<body>[\s\S]*?)\r?\n```\Z")


class StoryJSONParseError(ValueError):
    def __init__(self, line: int, column: int, offset: int) -> None:
        super().__init__("Story Planner returned malformed JSON")
        self.line = line
        self.column = column
        self.offset = offset


def _story_workspace_context(workspace, *, planning, incoming, outgoing):
    """Expose saved Story context without making Workspace controls model-writable."""
    context = _workspace_context(workspace, planning=planning, incoming=incoming, outgoing=outgoing)
    context.pop("creativeRequest", None)
    context["scenePlan"] = planning.scene_plan
    context.pop("creativeControls", None)
    return context


def normalize_story_model_json(raw: str) -> dict:
    """Accept raw JSON or one complete JSON Markdown fence, with no prose."""
    if not isinstance(raw, str):
        raise ValueError("structured Story Planner response must be text")
    trimmed = raw.strip()
    candidate = trimmed
    body_offset = len(raw) - len(raw.lstrip())
    fenced = _JSON_FENCE.fullmatch(candidate)
    if fenced:
        candidate = fenced.group("body")
        body_offset = raw.find(candidate, body_offset)
    elif "```" in candidate:
        raise ValueError("Story Planner response must be raw JSON or one complete JSON fence")
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError as exc:
        offset = max(0, body_offset) + exc.pos
        prefix = raw[:offset]
        line = prefix.count("\n") + 1
        line_start = prefix.rfind("\n")
        column = offset - line_start
        raise StoryJSONParseError(line, column, offset + 1) from exc
    except TypeError as exc:
        raise ValueError("Story Planner response is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("Story Planner response must be a JSON object")
    return value


_STORY_MEMORY_PATCH_KEYS = frozenset(
    field.alias or name for name, field in StoryMemoryPatch.model_fields.items()
)


def sanitize_story_memory_patch_extras(data: dict, generation_start: int = 1) -> list[str]:
    """Drop unsupported top-level Story Memory patch keys and report them compactly."""
    locations = {}
    entries = data.get("items")
    if not isinstance(entries, list):
        entries = data.get("slots")
    if not isinstance(entries, list):
        return []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        patch = entry.get("memoryPatchAfter")
        if not isinstance(patch, dict):
            continue
        unknown = sorted(key for key in patch if key not in _STORY_MEMORY_PATCH_KEYS)
        if not unknown:
            continue
        for key in unknown:
            del patch[key]
            safe_key = " ".join("".join(char for char in key if char.isprintable()).split())
            if not safe_key:
                safe_key = "<blank/non-printable>"
            if len(safe_key) > 80:
                safe_key = safe_key[:77] + "..."
            locations.setdefault(safe_key, set()).add(generation_start + index)
    if not locations:
        return []

    def generation_label(ordinals):
        ordered = sorted(ordinals)
        if len(ordered) == 1:
            return f"Generation {ordered[0]}"
        if len(ordered) == 2:
            return f"Generations {ordered[0]} and {ordered[1]}"
        return f"Generations {', '.join(map(str, ordered[:-1]))}, and {ordered[-1]}"

    fields = [f"{key} in {generation_label(ordinals)}" for key, ordinals in sorted(locations.items())[:8]]
    remaining = len(locations) - len(fields)
    if remaining:
        fields.append(f"{remaining} more unsupported field(s)")
    return ["Ignored unsupported Story Memory patch field(s): " + "; ".join(fields) + "."]


def sanitize_reference_recommendations(items, allowed_refs: set[tuple[str, str]]):
    """Drop invalid advisory pairs and keep the first valid qualified pair."""
    sanitized = []
    for item in items:
        seen: set[tuple[str, str]] = set()
        recommendations = []
        for recommendation in item.reference_recommendations:
            pair = (recommendation.source_generation_id, recommendation.asset_id)
            if pair in allowed_refs and pair not in seen:
                seen.add(pair)
                recommendations.append(recommendation)
        sanitized.append(item.model_copy(update={"reference_recommendations": recommendations}))
    return sanitized


def sanitize_recommendations(proposal: StoryPlanProposal, allowed_refs: set[tuple[str, str]]) -> StoryPlanProposal:
    return proposal.model_copy(update={"items": sanitize_reference_recommendations(proposal.items, allowed_refs)})


def initial_story_draft(snapshot: StoryProjectSnapshot, count: int, item_id_factory=None) -> StoryPlanProposal:
    if count < len(snapshot.generations):
        raise ValueError("Desired total Generations cannot be smaller than the existing Sequence")
    if snapshot.generations[0].planning.relationship_to_previous is not None:
        raise ValueError("Save Generation 1 with no Relationship to Previous before building a Story Plan")
    items = []
    make_item_id = item_id_factory or (lambda: str(uuid4()))
    for index in range(count):
        g = snapshot.generations[index] if index < len(snapshot.generations) else None
        items.append(StoryPlanItem(
            planItemId=make_item_id(), existingGenerationId=g.generation_id if g else None,
            scenePlan=g.planning.scene_plan if g else "",
            dialogue=g.workspace.dialogue if g else "",
            incomingState=g.incoming_state if g else "",
            intendedOutgoingState=g.intended_outgoing_state if g else "",
            relationshipToPrevious=(g.planning.relationship_to_previous if g else None) or ("independent" if index else None),
            cameraIntent=g.planning.camera_intent if g else "",
            shotCount=(g.planning.shot_count or g.workspace.shots or 1) if g else 1,
        ))
    return StoryPlanProposal(
        baseProjectId=snapshot.manifest.project_id, baseProjectUpdatedAt=snapshot.manifest.updated_at,
        baseSequenceFingerprint=snapshot.fingerprint, sequenceContext=snapshot.sequence_context, items=items,
    )


def assemble_revision_prompt(snapshot, request, response_model):
    """Bounded read-only windows; visual scopes additionally receive relevant assets.

    Dialogue uses narrative text and adjacent dialogue, never Pictures/asset dumps.
    Other scopes use local visual evidence, plus specifically recommended committed
    pairs from those windows. Overlapping windows and source assets are deduplicated.
    """
    draft = request.draft
    selected = set(request.selected_plan_item_ids)
    scopes = {scope.value for scope in request.scopes}
    visual = bool(scopes - {"dialogue"})
    indices = {index for index, item in enumerate(draft.items) if item.plan_item_id in selected}
    window = sorted({neighbor for index in indices for neighbor in (index - 1, index, index + 1)
                     if 0 <= neighbor < len(draft.items)})
    context_fields = {"plan_item_id", "existing_generation_id", "scene_plan", "incoming_state",
                      "intended_outgoing_state", "relationship_to_previous"}
    if "dialogue" in scopes or "scene_plan" in scopes:
        context_fields.add("dialogue")
    if visual:
        context_fields.update({"camera_intent", "shot_count", "reference_recommendations"})
    items = [{"ordinal": index + 1,
              **draft.items[index].model_dump(mode="json", by_alias=True, include=context_fields)}
             for index in window]
    local_sources = {draft.items[index].existing_generation_id for index in window}
    recommended = {(r.source_generation_id, r.asset_id) for index in window
                   for r in draft.items[index].reference_recommendations} if visual else set()
    sources, image_workspaces = [], []
    relevant_subjects = set()
    for generation in snapshot.generations:
        local = generation.generation_id in local_sources
        assets = [asset for asset in generation.workspace.assets
                  if visual and (local or (generation.generation_id, asset.id) in recommended)]
        if not local and not assets:
            continue
        workspace = generation.workspace.model_copy(update={"assets": assets})
        # A remote recommendation carries only its asset-associated Subjects, not
        # every Subject that happened to be materialized in the source Generation.
        subject_ids = {s.id for s in workspace.subjects} if local else set()
        for asset in assets:
            metadata = asset.model_dump(mode="json", by_alias=True)
            identity = metadata.get("subjectIdentity") or {}
            if asset.role == "subject_identity" and identity.get("subjectId"):
                subject_ids.add(identity["subjectId"])
            if asset.role == "character_comparison_scale":
                subject_ids.update(metadata.get("comparisonSubjects") or [])
        relevant_subjects.update(subject_ids)
        source = {"sourceGenerationId": generation.generation_id,
                  "subjects": [s.model_dump(mode="json") for s in workspace.subjects if s.id in subject_ids]}
        if local:
            source.update(mode=workspace.mode, durationSeconds=workspace.duration_seconds)
        if visual:
            source["assets"] = _asset_metadata(workspace)
            if request.llm.story_vision_enabled:
                image_workspaces.append((generation, workspace))
        sources.append(source)
    # Resolve dormant names/aliases already mentioned in local prose, global context
    # or the instruction without exposing the rest of the dormant cast.
    local_text = "\n".join(getattr(draft.items[index], field) for index in window for field in
                           ("scene_plan", "dialogue", "incoming_state", "intended_outgoing_state", "camera_intent"))
    prose = narrative_key(local_text + "\n" + draft.sequence_context
                          + "\n" + request.instruction)
    cast = []
    for subject in snapshot.sequence_subjects:
        tokens = [f"Subject {subject.number}", *(subject.narrative_aliases or [])]
        if subject.narrative_name:
            tokens.append(subject.narrative_name)
        if subject.id in relevant_subjects or any(
            re.search(r"(?<!\w)" + re.escape(narrative_key(token)) + r"(?!\w)", prose) for token in tokens
        ):
            cast.append(subject)
    context = {
        "storyIdea": ProjectStore._story_idea(snapshot.manifest),
        "sequenceContext": draft.sequence_context, "itemCount": len(draft.items),
        "storyCreativeDirection": ProjectStore._story_creative_direction(snapshot.manifest).model_dump(
            mode="json", by_alias=True,
        ),
        "contextItems": items, "sourceGenerations": sources,
        "sequenceNarrativeCast": narrative_mapping(cast, include_character_profiles=True),
        "instruction": request.instruction, "selectedPlanItemIds": request.selected_plan_item_ids,
        "editableScopes": [scope.value for scope in request.scopes],
        "editableFields": [StoryPlanItem.model_fields[field].alias or field
                           for field in ([SCOPE_FIELDS[scope.value] for scope in request.scopes]
                                         + (["shot_count"] if "camera_intent" in scopes else []))],
        "responseSchema": response_model.model_json_schema(by_alias=True),
    }
    text = json.dumps(context, ensure_ascii=False, separators=(",", ":"), default=str)
    parts = [{"type": "text", "text": text}]
    for generation, workspace in image_workspaces:
        parts.extend(_image_parts(workspace, f"sourceGenerationId {generation.generation_id} (Generation {generation.ordinal})"))
    language_policy = story_dialogue_policy(snapshot.dialogue_language) if "dialogue" in scopes else ""
    style_policy = story_planning_style_policy(ProjectStore._story_planning_style(snapshot.manifest),
                                               scene_plan_editable="scene_plan" in scopes)
    return [{"role": "system", "content": REVISION_AUTHORITY + language_policy + CREATIVE_DIRECTION_POLICY + character_profile_policy_for(cast) + style_policy},
            {"role": "user", "content": parts if request.llm.story_vision_enabled else text}]


def assemble_story_prompt(snapshot, draft, settings, *, idea=None, revision=None):
    if revision is not None:
        return assemble_revision_prompt(snapshot, revision, story_revision_response_model(revision.scopes))
    sources = [{
        "sourceGenerationId": g.generation_id, "label": f"Generation {g.ordinal}",
        **_story_workspace_context(g.workspace, planning=g.planning, incoming=g.incoming_state, outgoing=g.intended_outgoing_state),
    } for g in snapshot.generations]
    context = {
        "storyIdea": ProjectStore._story_idea(snapshot.manifest),
        "draft": draft.model_dump(mode="json", by_alias=True),
        "storyCreativeDirection": ProjectStore._story_creative_direction(snapshot.manifest).model_dump(
            mode="json", by_alias=True,
        ),
        "sourceGenerations": sources,
        "sequenceNarrativeCast": narrative_mapping(snapshot.sequence_subjects, include_character_profiles=True),
        "responseSchema": StoryPlanProposal.model_json_schema(by_alias=True),
    }
    if revision is None:
        # Show saved authority only, without blank text or provisional relationships.
        sparse = context["draft"]
        for field in ("baseProjectId", "baseProjectUpdatedAt", "baseSequenceFingerprint"):
            sparse.pop(field)
        if not snapshot.sequence_context.strip():
            sparse.pop("sequenceContext")
        sparse.pop("consistencyWarnings")
        for index, item in enumerate(sparse["items"]):
            for field in ("scenePlan", "dialogue", "incomingState", "intendedOutgoingState", "cameraIntent"):
                if not item[field].strip():
                    item.pop(field)
            if index and (index >= len(snapshot.generations)
                          or snapshot.generations[index].planning.relationship_to_previous is None):
                item.pop("relationshipToPrevious")
            item.pop("referenceRecommendations")
            item.pop("participatingSubjectIds", None)
            item.pop("tonePerformance", None)
        context.update(idea=idea, desiredGenerationCount=len(draft.items),
                       responseSchema=LegacyStoryBuildResponse.model_json_schema(by_alias=True))
    else:
        context.update(instruction=revision.instruction, selectedPlanItemIds=revision.selected_plan_item_ids,
                       editableScopes=[scope.value for scope in revision.scopes])
    text = json.dumps(context, ensure_ascii=False, indent=2, default=str)
    parts = [{"type": "text", "text": text}]
    if settings.story_vision_enabled:
        for g in snapshot.generations:
            parts.extend(_image_parts(g.workspace, f"sourceGenerationId {g.generation_id} (Generation {g.ordinal})"))
    system = STORY_SYSTEM + """
Sequence narrative identity: sequenceNarrativeCast is the full canonical Sequence cast,
including dormant Subjects. The current narrativeName and technical Subject N label denote
the SAME stable subjectId, never additional characters. Newly authored scenePlan,
dialogue, incomingState, intendedOutgoingState, cameraIntent and sequenceContext must use
the current narrativeName. Previous names are recognition-only compatibility metadata and
are not output vocabulary. Dialogue is exact spoken or sung text only, may be blank for
silence, and must not be paraphrased once authored. Continue supporting technical Subject N labels.
Existing nonblank saved fields remain exact authority. For Revision, this naming policy
applies only to selected fields; it never broadens editable scopes. Narrative availability
does not imply shot participation. Reference recommendations still use only committed
sourceGenerationId + assetId pairs; names are never recommendation identity keys.
"""
    if revision is None:
        system = system.replace(
            "References are advisory: only recommend committed sourceGenerationId + assetId pairs\n"
            "present in sourceGenerations. Proposed new items own no assets. Never invent, attach,\n"
            "copy assets, invent Subjects or change Workspace settings/modes. Creative Controls are\n"
            "not a model-editable Build surface.",
            "Only recommend committed sourceGenerationId + assetId pairs present in sourceGenerations.\n"
            "On new slots, Apply will attach each recommended reference and its required saved Subjects.\n"
            "New slots inherit the immediately preceding Generation's mode. Existing-slot references\n"
            "remain advisory. Select only useful references for each shot; never invent asset or Subject\n"
            "metadata or change Workspace settings/modes yourself. Creative Controls are not a\n"
            "model-editable Build surface.",
        )
        system = system.replace(
            "Preserve the supplied base snapshot fields,\nplanItemIds, existingGenerationIds, item order and exact item count.",
            "Use the supplied slot IDs, item order and exact item count.",
        )
    return [
        {"role": "system", "content": system + "\n" + story_dialogue_policy(snapshot.dialogue_language) + CREATIVE_DIRECTION_POLICY + character_profile_policy_for(snapshot.sequence_subjects)
         + (BUILD_AUTHORITY if revision is None else REVISION_AUTHORITY)
         + story_planning_style_policy(ProjectStore._story_planning_style(snapshot.manifest))},
        {"role": "user", "content": parts if settings.story_vision_enabled else text},
    ]


def validate_identity(before, after):
    for field in ("base_project_id", "base_project_updated_at", "base_sequence_fingerprint"):
        if getattr(before, field) != getattr(after, field):
            raise ValueError("Model changed the base Project snapshot")
    identity = lambda draft: [(i.plan_item_id, i.existing_generation_id) for i in draft.items]
    if identity(before) != identity(after):
        raise ValueError("Model changed draft count, identity or order")


def validate_build_authority(snapshot, before, after):
    validate_identity(before, after)
    after.validate_sources([g.generation_id for g in snapshot.generations])
    if snapshot.sequence_context.strip() and after.sequence_context != snapshot.sequence_context:
        raise ValueError("Model changed authoritative Sequence Context")
    for g, item in zip(snapshot.generations, after.items):
        authoritative = {
            "scene_plan": g.planning.scene_plan,
            "dialogue": g.workspace.dialogue,
            "incoming_state": g.incoming_state, "intended_outgoing_state": g.intended_outgoing_state,
            "relationship_to_previous": g.planning.relationship_to_previous,
            "camera_intent": g.planning.camera_intent,
        }
        for field, value in authoritative.items():
            if value is not None and str(value).strip() and getattr(item, field) != value:
                raise ValueError(f"Model changed authoritative {field} in Generation {g.ordinal}")


def project_revision(request, result):
    """Require exact item/field coverage, then project by stable ID, never order."""
    before = request.draft
    selected_items = set(request.selected_plan_item_ids)
    selected_fields = {SCOPE_FIELDS[scope.value] for scope in request.scopes}
    if "camera_intent" in selected_fields:
        selected_fields.add("shot_count")
    ids = [patch.plan_item_id for patch in result.patches]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate planItemId patches")
    if set(ids) - {item.plan_item_id for item in before.items}:
        raise ValueError("Unknown planItemId patch")
    if set(ids) - selected_items:
        raise ValueError("Patch targets an unselected planItemId")
    if selected_items - set(ids):
        raise ValueError("Missing selected planItemId patch")
    for patch in result.patches:
        if patch.model_fields_set != selected_fields | {"plan_item_id"}:
            raise ValueError("Patch must explicitly provide exactly the selected fields")
    projected = before.model_copy(deep=True)
    result_by_id = {patch.plan_item_id: patch for patch in result.patches}
    for item in projected.items:
        if item.plan_item_id not in selected_items:
            continue
        model_item = result_by_id[item.plan_item_id]
        for field in selected_fields:
            setattr(item, field, getattr(model_item, field))
    return StoryPlanProposal.model_validate(projected.model_dump())


def add_boundary_warnings(request, result):
    selected = set(request.selected_plan_item_ids)
    warnings = list(request.draft.consistency_warnings) + list(result.consistency_warnings)
    for index, (old, new) in enumerate(zip(request.draft.items, result.items)):
        if new.plan_item_id not in selected:
            continue
        def warn(neighbor, direction, message):
            if neighbor.plan_item_id not in selected:
                warnings.append(StoryConsistencyWarning(
                    planItemId=new.plan_item_id, relatedPlanItemId=neighbor.plan_item_id,
                    direction=direction, message=message,
                ))
        if index + 1 < len(result.items) and (old.intended_outgoing_state != new.intended_outgoing_state
                                            or old.scene_plan != new.scene_plan):
            warn(result.items[index + 1], "downstream", f"Review Generation {index + 2} Incoming State after the upstream edit. Its draft was not changed.")
        camera_transition = (old.camera_intent != new.camera_intent and new.relationship_to_previous in {
            "continue_previous_shot", "match_previous_ending_frame",
        })
        if index and (old.incoming_state != new.incoming_state
                      or old.relationship_to_previous != new.relationship_to_previous or camera_transition):
            warn(result.items[index - 1], "upstream", f"Review the transition from Generation {index} after the incoming/relationship/camera edit. Its draft was not changed.")
    unique = {tuple(w.model_dump().values()): w for w in warnings}
    result.consistency_warnings = list(unique.values())
    return StoryPlanProposal.model_validate(result.model_dump())


def _strip_legacy_tone_performance(data):
    """Ignore legacy model attempts to author controls; public drafts retain current values."""
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return data
    result = dict(data)
    result["items"] = [
        {key: value for key, value in item.items()
         if key not in {"tonePerformance", "tone_performance", "participatingSubjectIds", "participating_subject_ids"}}
        if isinstance(item, dict) else item
        for item in data["items"]
    ]
    return result


def _structured_error(exc, model=None):
    # Pydantic's str(exc) includes arbitrary model input, potentially image/key echoes.
    known = {"patches"}
    seen = set()

    def add_model_fields(model_type):
        if not isinstance(model_type, type) or not issubclass(model_type, BaseModel) or model_type in seen:
            return
        seen.add(model_type)
        for name, field in model_type.model_fields.items():
            known.update((name, field.alias or name))
            add_annotation(field.annotation)

    def add_annotation(annotation):
        origin = get_origin(annotation)
        if origin is not None:
            for arg in get_args(annotation):
                add_annotation(arg)
        else:
            add_model_fields(annotation)

    add_model_fields(model or LegacyStoryBuildResponse)
    safe_checks = {
        "Value error, Story draft identities must be unique",
        "Value error, Generation 1 relationship must be null; later relationships must be specified",
        "Value error, Generation 1 relationship must be null",
        "Value error, Every appended Generation requires a relationship",
        "Value error, warning references an unknown planItemId",
    }
    errors = exc.errors(include_input=False, include_context=False, include_url=False)
    summaries = []
    for error in errors[:5]:
        path = ".".join(str(p) if isinstance(p, int) or p in known else "field" for p in error["loc"]) or "response"
        detail = error["msg"] if error["msg"] in safe_checks else error["type"]
        summaries.append(f"{path}: {detail}")
    return f"{len(errors)} validation error(s): " + "; ".join(summaries)


def _raw_story_response_is_safe_to_show(raw, messages, settings):
    """Withhold a raw completion that includes credentials or echoed request material."""
    if not isinstance(raw, str):
        return False
    api_key = settings.endpoint.api_key.get_secret_value() if settings.endpoint.api_key else ""
    if api_key and api_key in raw:
        return False
    if re.search(r"data:(?:image|audio|video)/[^,\s;]+;base64,", raw, re.IGNORECASE):
        return False

    def text_parts(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for nested in value.values():
                yield from text_parts(nested)
        elif isinstance(value, (list, tuple)):
            for nested in value:
                yield from text_parts(nested)

    # Long exact matches are strong evidence the provider echoed a prompt or
    # request field. Short repeated story phrases remain inspectable.
    return not any(len(part) >= 256 and part in raw for part in text_parts(messages))


def _merge_story_warnings(result, additions):
    if not additions or "warnings" not in result.__class__.model_fields:
        return result
    return result.model_copy(update={
        "warnings": result.warnings[:max(0, 64 - len(additions))] + additions[:64],
    })


async def _call(messages, settings, client_factory, *, build_initial=None, revision_model=None, response_model=None,
                response_preprocessor=None, stream_response=False, operation_warnings=None,
                memory_generation_start=1):
    capacity = preflight_request_capacity(messages, settings)
    if operation_warnings is not None and capacity.get("completionBudgetWarning"):
        operation_warnings.append(capacity["completionBudgetWarning"])
    try:
        async with client_factory(settings) as client:
            method = client.generate_streamed if stream_response and hasattr(client, "generate_streamed") else client.generate
            try:
                parameters = inspect.signature(method).parameters
            except (TypeError, ValueError):
                parameters = {}
            kwargs = {"response_instruction": None}
            if "response_format" in parameters or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
                kwargs["response_format"] = {"type": "json_object"}
            raw = await method(messages, **kwargs)
    except LLMBackendError as exc:
        # Provider bodies can echo a request. Story reports only bounded safe metadata.
        raise LLMBackendError(exc.safe_message, exc.status_code, exc.upstream_status,
                              category=exc.category, safe_message=exc.safe_message) from exc
    try:
        data = normalize_story_model_json(raw)
    except StoryJSONParseError as exc:
        detail = ("Story Planner returned invalid structured JSON: malformed JSON at "
                  f"line {exc.line}, column {exc.column} (near character {exc.offset}).")
        raise LLMBackendError(detail, category="story_json", safe_message=detail, raw_response=raw,
                              raw_response_visible=_raw_story_response_is_safe_to_show(raw, messages, settings)) from exc
    except (ValueError, TypeError) as exc:
        detail = f"Story Planner returned invalid structured JSON: {exc}"
        raise LLMBackendError(detail, category="story_json", safe_message=detail, raw_response=raw,
                              raw_response_visible=_raw_story_response_is_safe_to_show(raw, messages, settings)) from exc
    patch_warnings = sanitize_story_memory_patch_extras(data, memory_generation_start)
    if operation_warnings is not None and patch_warnings:
        operation_warnings.extend(patch_warnings)
    try:
        if response_preprocessor is not None:
            data = response_preprocessor(data)
        structured_model = response_model or revision_model
        if structured_model is not None:
            result = structured_model.model_validate(data)
            additions = patch_warnings[:]
            if capacity.get("completionBudgetWarning"):
                additions.append(capacity["completionBudgetWarning"])
            return _merge_story_warnings(result, additions)
        if build_initial is None:
            result = StoryPlanProposal.model_validate(data)
            return _merge_story_warnings(result, patch_warnings)
        data = _strip_legacy_tone_performance(data)
        items = data.get("items") if isinstance(data.get("items"), list) else []
        # The compatibility /build contract has no Story Memory authority. A patch
        # object can be inspected for unsupported keys above, but is never applied.
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("memoryPatchAfter"), dict):
                item.pop("memoryPatchAfter", None)
        # Recorded /build replies predating Scene Plan used creativeRequest as
        # their narrative field. Translate only that compatibility response.
        for item in items:
            if isinstance(item, dict) and "creativeRequest" in item and "scenePlan" not in item:
                item["scenePlan"] = item.pop("creativeRequest")
        if any(isinstance(item, dict) and "shotCount" not in item for item in items):
            raise LLMBackendError("Story Planner response is missing required shotCount.", category="story_schema")
        if any(key in data for key in ("baseProjectId", "baseProjectUpdatedAt", "baseSequenceFingerprint",
                                        "base_project_id", "base_project_updated_at", "base_sequence_fingerprint")):
            # Strict compatibility with previously captured full-draft responses.
            # Never discard or repair a mistyped envelope before checking it.
            legacy = StoryPlanProposal.model_validate(data)
            try:
                validate_identity(build_initial, legacy)
            except ValueError as exc:
                raise LLMBackendError(f"Story Planner draft rejected: {exc}", category="story_identity") from exc
            data = legacy.model_dump(by_alias=True, exclude={
                "base_project_id", "base_project_updated_at", "base_sequence_fingerprint",
            })
            data = _strip_legacy_tone_performance(data)
            for item in data["items"]:
                item.pop("existingGenerationId")
        result = LegacyStoryBuildResponse.model_validate(data)
        return _merge_story_warnings(result, patch_warnings)
    except ValidationError as exc:
        structured_model = response_model or revision_model or LegacyStoryBuildResponse
        detail = ("Story Planner returned invalid structured JSON: schema validation failed: "
                  f"{_structured_error(exc, structured_model)}")
        raise LLMBackendError(detail, category="story_schema", safe_message=detail, raw_response=raw,
                              raw_response_visible=_raw_story_response_is_safe_to_show(raw, messages, settings)) from exc


TEXT_FIELDS = ("scene_plan", "dialogue", "incoming_state", "intended_outgoing_state", "camera_intent")
REQUIRED_TEXT_FIELDS = ("scene_plan",)


def project_build(snapshot, initial, proposals):
    """Construct a public draft from trusted identities and planner-owned proposals."""
    if [i.plan_item_id for i in proposals.items] != [i.plan_item_id for i in initial.items]:
        raise LLMBackendError("Story Planner draft rejected: Model changed draft count, identity or order",
                              category="story_identity")
    result = initial.model_copy(deep=True)
    if not snapshot.sequence_context.strip():
        result.sequence_context = proposals.sequence_context
    for index, (item, proposal) in enumerate(zip(result.items, proposals.items)):
        for field in TEXT_FIELDS:
            if not getattr(item, field).strip():
                setattr(item, field, getattr(proposal, field))
        if index and (index >= len(snapshot.generations)
                      or snapshot.generations[index].planning.relationship_to_previous is None):
            item.relationship_to_previous = proposal.relationship_to_previous
        item.reference_recommendations = proposal.reference_recommendations
    result.consistency_warnings = proposals.consistency_warnings
    try:
        # Check relationships, warning targets and all public limits after projection.
        result = StoryPlanProposal.model_validate(result.model_dump())
        validate_build_authority(snapshot, initial, result)
    except ValidationError as exc:
        raise LLMBackendError(f"Story Planner draft rejected: {_structured_error(exc)}",
                              category="story_schema") from exc
    except ValueError as exc:
        raise LLMBackendError(f"Story Planner draft rejected: {exc}", category="story_authority") from exc
    return result


def add_incomplete_build_warnings(result):
    labels = {"scene_plan": "Scene Plan"}
    warnings = []
    for item in result.items:
        # Exact Dialogue / Lyrics deliberately has no completeness requirement: blank means silence.
        missing = [labels[field] for field in REQUIRED_TEXT_FIELDS if not getattr(item, field).strip()]
        if missing:
            warnings.append(StoryConsistencyWarning(
                planItemId=item.plan_item_id, relatedPlanItemId=item.plan_item_id, direction="downstream",
                message="Build left the Scene Plan blank. Edit the draft before Apply.",
            ))
    # Reserve room for deterministic warnings within the existing public limit.
    result.consistency_warnings = result.consistency_warnings[:256 - len(warnings)] + warnings
    return result


async def build_story_plan(store: ProjectStore, project_id: str, request: StoryBuildRequest,
                           client_factory=OpenAICompatibleClient, *, item_id_factory=None):
    snapshot = store.read_story_snapshot(project_id)
    initial = initial_story_draft(snapshot, request.desired_generation_count, item_id_factory)
    operation_warnings = []
    proposals = await _call(assemble_story_prompt(snapshot, initial, request.llm, idea=request.idea),
                            request.llm, client_factory, build_initial=initial,
                            operation_warnings=operation_warnings)
    # Validate the trusted request snapshot, independently of anything the model echoes.
    store.read_story_snapshot(project_id).validate_draft(initial)
    result = project_build(snapshot, initial, proposals)
    result = sanitize_recommendations(result, snapshot.allowed_refs)
    result = add_incomplete_build_warnings(result)
    if operation_warnings:
        item_id = result.items[0].plan_item_id
        warnings = [StoryConsistencyWarning(planItemId=item_id, relatedPlanItemId=item_id,
                                            direction="downstream", message=message)
                    for message in operation_warnings]
        result.consistency_warnings = result.consistency_warnings[:256 - len(warnings)] + warnings[:256]
    store.read_story_snapshot(project_id).validate_draft(result)
    return result


async def revise_story_plan(store: ProjectStore, project_id: str, request: StoryRevisionRequest,
                            client_factory=OpenAICompatibleClient):
    snapshot = store.read_story_snapshot(project_id)
    snapshot.validate_draft(request.draft)  # Must run before any model call.
    story_authority = store._story_revision_fingerprint(snapshot.manifest)
    response_model = story_revision_response_model(request.scopes)
    operation_warnings = []
    result = await _call(assemble_revision_prompt(snapshot, request, response_model), request.llm,
                         client_factory, revision_model=response_model,
                         operation_warnings=operation_warnings)
    try:
        result = project_revision(request, result)
        result = add_boundary_warnings(request, result)
        if operation_warnings:
            item_id = request.selected_plan_item_ids[0]
            warning = StoryConsistencyWarning(planItemId=item_id, relatedPlanItemId=item_id,
                                              direction="downstream", message=operation_warnings[0])
            result.consistency_warnings = result.consistency_warnings[:255] + [warning]
    except ValidationError as exc:
        raise LLMBackendError(f"Story Planner revision rejected: {_structured_error(exc)}",
                              category="story_schema") from exc
    except ValueError as exc:
        raise LLMBackendError(f"Story Planner revision rejected: {exc}", 502) from exc
    current = store.read_story_snapshot(project_id)
    if current.fingerprint != snapshot.fingerprint:
        if store._story_revision_fingerprint(current.manifest) != story_authority:
            raise StaleProjectSnapshotError("Relevant Story authority changed while Revision was running; revise again")
        # Keep the existing full Project fingerprint contract for subsequent
        # Apply, but do not discard a scoped response for unrelated output state.
        result = result.model_copy(update={
            "base_project_updated_at": current.manifest.updated_at,
            "base_sequence_fingerprint": current.fingerprint,
        })
    current.validate_draft(result)
    return result
