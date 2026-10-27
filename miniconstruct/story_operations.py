"""Shared bounded projection and terminal-anchor Plan Next, with one model call."""
from dataclasses import dataclass
import base64
import hashlib
import hmac
import json
import re
import secrets
from copy import deepcopy
from uuid import uuid4

from pydantic import ValidationError

from miniconstruct.llm.client import OpenAICompatibleClient, LLMBackendError
from miniconstruct.h3.vision_input import prompt_writer_image
from miniconstruct.models.observations import observation_projection
from miniconstruct.models.sequence_subject import (
    character_profile_policy_for, narrative_key, narrative_mapping,
)
from miniconstruct.models.story_memory import STORY_MEMORY_NEW_SLOTS, StoryContinuityMemory, apply_memory_patch
from miniconstruct.models.story_operations import (
    story_participant_response_model, StoryShotGuidance, StoryNextItem, StoryInitialRangeItem, StoryReplanItem,
    STORY_LOCAL_CONTEXT_GENERATIONS, StoryNextResponse, StoryNextProposal, StoryOperationAuthority,
    StoryInsertResponse, StoryInsertProposal, StoryInsertAuthority,
    STORY_REPLAN_MAX_GENERATIONS, StoryReplanResponse, StoryReplanProposal, StoryReplanAuthority,
    StoryReferencePair, StoryInitialRangeRequest, StoryInitialRangeResponse,
    StoryInitialRangePlanItem, StoryInitialRangeProposal, StoryInitialRangeAuthority,
)
from miniconstruct.planning import _asset_metadata
from miniconstruct.project_store import GenerationDocument, ProjectStore, StoryProjectSnapshot, StaleProjectSnapshotError
from miniconstruct.dialogue_language import story_dialogue_policy
from miniconstruct.story_planning_style import story_planning_style_policy
from miniconstruct.story_planning import (
    CREATIVE_DIRECTION_POLICY, SEQUENCE_CONTEXT_POLICY, SCENE_PLAN_POLICY, SAME_SHOT_OPENING_POLICY, STORY_DURATION_POLICY, STORY_SHOT_PLAN_POLICY, _call, _image_parts,
    sanitize_reference_recommendations,
)

# Drafts are ephemeral, like existing Story drafts. Restart requires reproposal.
# Signing prevents clients from changing issued IDs/order or snapshot authority.
_AUTHORITY_KEY = secrets.token_bytes(32)


PARTICIPATING_SUBJECT_POLICY = """
participatingSubjectIds is the ordered, unique set of canonical Sequence Subject IDs physically/visually
participating in that Generation. It may contain only exact opaque IDs from availableCanonicalSubjectIds.
Names, aliases, and Subject N labels in the supplied cast are orientation only. Never invent an ID, derive
one from a Subject number/name, use temporary planner IDs, or create S1/S2-style identifiers.
If availableCanonicalSubjectIds is empty, output [] even if Scene Plan/dialogue describes people, animals,
creatures, or other characters. Characters without canonical Subject records may appear naturally in prose;
they must not appear in participatingSubjectIds. Story Planner cannot create canonical Subjects.
referenceRecommendations are optional source-qualified evidence, not permission to invent Subject IDs.
"""


INITIAL_RANGE_SYSTEM = """You are MiniConstruct's bounded Build Initial Range planner. Return exactly one JSON object
matching responseSchema, without Markdown or commentary. Return exactly requestedTotalCount items in chronological
array order. The server position-binds item 0 to saved Generation 1 and every later item to its signed proposed slot:
do not return planItemId, existingGenerationId, or permanent Generation IDs. Generation 1 has no predecessor; each
later Generation requires a relationship to its immediate predecessor. Plan authoring fields, not a final H3 prompt.
Existing nonblank G1 Scene Plan, exact dialogue, Sequence Context, assets, Subjects, and Workspace settings are stronger
saved authority and must not be silently rewritten. Blank existing dialogue may be filled when speech helps the scene. References are advisory and may
use only the explicit committed G1 sourceGenerationId + assetId pairs; proposed new items own no assets before Apply.
Use current Narrative Names naturally, but Sequence Subject IDs/numbers remain canonical and you must never invent,
renumber, or reuse Subjects. Previous names are recognition-only compatibility metadata and are not output vocabulary.
Story Plan / Premise is narrative intent: treat explicit milestones, outcomes, decisions, and aftermath as
important requested arc unless stronger authority conflicts. Distribute that arc across the available Generations,
reserve space for explicit ending/aftermath intent, and avoid spending scarce beats on travel or transition unless it
carries a meaningful obstacle, decision, reveal, performance, or state change. Extra beats should add meaningful
escalation, complication, characterization, or consequence rather than duplicate events. Story Creative Direction is
soft guidance and cannot override factual Story Memory or saved authority. Start with the supplied empty pre-G1 Story
Memory. Supply memoryPatchAfter for every item, including G1, in chronological order. Use {} when no Story Memory
change is needed. Otherwise use only recentSummary, factUpserts, resolvedFactIds, threadUpserts, and resolvedThreadIds.
Never use generic placeholders such as field, key, name, or value. Keep patches sparse: recentSummary
is the compact current position; durableFacts are only information plausibly useful beyond the local planning window;
openThreads are only genuinely unresolved promises, goals, mysteries, threats, or commitments future planning should
remember. Do not create a fact or thread merely because another Generation occurred, or for transient navigation,
location, or action that only restates the immediately preceding beat. The operation-wide temporary fact/thread IDs are
the only IDs that may create memory entries; each may first appear in any item, later items may update or resolve it,
and a consumed temporary ID can never be reused after resolve.
Creative Controls are Workspace authority. Never propose or edit them in Story operations.
For every new Generation, participatingSubjectIds is the exact canonical visual cast for that shot. Select only IDs
from sequenceNarrativeCast, exactly as supplied; never select a Subject merely because they are discussed, remembered,
or part of the Sequence cast. MiniConstruct automatically carries established subject identity Pictures for selected
Subjects. referenceRecommendations are optional additional committed visual evidence and must not redundantly include
those identity Pictures.
""" + PARTICIPATING_SUBJECT_POLICY


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def authority_token(authority):
    return hmac.new(_AUTHORITY_KEY, _json(authority.model_dump(mode="json", by_alias=True)).encode(), hashlib.sha256).hexdigest()


def shot_guidance_policy(guidance: StoryShotGuidance | None) -> str:
    if guidance is None:
        return ""
    if guidance.mode == "prefer":
        return (f"\nShot-count preference for each TARGET Generation: prefer about {guidance.count} purposeful "
                "ordered shots. Use this for meaningful coverage and pacing, not redundant cuts. "
                "Vary the count when the scene genuinely benefits. shotCount and Camera Intent / Shot Plan "
                "must describe the same planned coverage.\n")
    return (f"\nEvery TARGET Generation must use exactly {guidance.count} planned shots: return "
            f"shotCount = {guidance.count} for every target and distinguish exactly {guidance.count} purposeful "
            "ordered shots in Camera Intent / Shot Plan. Redistribute the intended action, reaction, and "
            "coverage; do not invent unrelated plot to fill cuts. Incoming State anchors the opening of "
            "Shot 1; Intended Outgoing State anchors the end of the final shot. For "
            "continue_previous_shot, Shot 1 continues the predecessor ending composition and state "
            "before any later cut or reframe. Protected boundaries and continuity remain authoritative.\n")


def validate_exact_shots(
    items: list[StoryNextItem] | list[StoryInitialRangeItem] | list[StoryReplanItem],
    guidance: StoryShotGuidance | None,
    first_ordinal: int,
) -> None:
    if guidance is None or guidance.mode != "exact":
        return
    for offset, item in enumerate(items):
        if item.shot_count != guidance.count:
            raise LLMBackendError(
                f"Story Planner response did not honor exact shot guidance for Generation {first_ordinal + offset}: "
                f"expected {guidance.count} shots, received {item.shot_count}.", category="story_schema",
            )


def initial_range_authority_context(snapshot):
    """Return the saved context text supplied to Build Initial Range."""
    g1 = snapshot.generations[0]
    return {
        "projectId": snapshot.manifest.project_id,
        "soleGenerationPrerequisite": True,
        "g1GenerationId": g1.generation_id,
        "storyIdea": ProjectStore._story_idea(snapshot.manifest),
        "fallbackDurationSeconds": g1.workspace.duration_seconds,
        "sequenceContext": snapshot.sequence_context,
        "storyCreativeDirection": ProjectStore._story_creative_direction(snapshot.manifest).model_dump(
            mode="json", by_alias=True,
        ),
        "sequenceNarrativeCast": narrative_mapping(snapshot.sequence_subjects, include_character_profiles=True),
        "generation1": {
            "generationId": g1.generation_id, "ordinal": 1,
            "scenePlan": g1.planning.scene_plan, "dialogue": g1.workspace.dialogue,
            "incomingState": g1.incoming_state, "intendedOutgoingState": g1.intended_outgoing_state,
            "relationshipToPrevious": g1.planning.relationship_to_previous,
            "cameraIntent": g1.planning.camera_intent, "shotCount": g1.planning.shot_count,
            "mode": g1.workspace.mode,
            "durationSeconds": g1.workspace.duration_seconds,
            "subjects": [subject.model_dump(mode="json") for subject in g1.workspace.subjects],
            "assets": _asset_metadata(g1.workspace),
        },
        "emptyPreG1StoryMemory": StoryContinuityMemory().model_dump(mode="json", by_alias=True),
        "allowedReferencePairs": [
            {"sourceGenerationId": source_id, "assetId": asset_id}
            for source_id, asset_id in sorted(snapshot.allowed_refs) if source_id == g1.generation_id
        ],
    }


def initial_range_authority_fingerprint(snapshot):
    authority = initial_range_authority_context(snapshot)
    authority["storyPlanningStyle"] = ProjectStore._story_planning_style(snapshot.manifest).model_dump(mode="json", by_alias=True)
    g1 = snapshot.generations[0]
    authority["generationIds"] = [generation.generation_id for generation in snapshot.generations]
    authority["dialogueLanguage"] = snapshot.dialogue_language.value
    # Apply replaces G1's saved memory as well as its planning fields.
    authority["generation1MemoryAuthority"] = {
        "memory": g1.story_memory_after.model_dump(mode="json", by_alias=True),
        "status": g1.story_memory_status,
    }
    authority["visualInputDigests"] = [
        {"assetId": asset.id, "sha256": hashlib.sha256(asset.image.data_url.encode("utf-8")).hexdigest()}
        for asset in g1.workspace.ordered_assets()
        if asset.kind.value == "image" and asset.image is not None
    ]
    return hashlib.sha256(_json(authority).encode("utf-8")).hexdigest()


def _style_fingerprint_tail(manifest):
    """Preserve default bounded signatures while binding nondefault saved style."""
    style = ProjectStore._story_planning_style(manifest)
    return [] if style == type(style)() else [style.model_dump(mode="json", by_alias=True)]


def sanitize_next_response_data(data, allowed_refs: set[tuple[str, str]]):
    """Remove malformed/unavailable advisory pairs before strict response validation."""
    if not isinstance(data, dict) or not isinstance(data.get("slots"), list):
        return data
    result = deepcopy(data)
    for index, slot in enumerate(result["slots"]):
        if not isinstance(slot, dict) or not isinstance(slot.get("referenceRecommendations"), list):
            continue
        seen = set()
        valid = []
        for recommendation in slot["referenceRecommendations"]:
            if not isinstance(recommendation, dict):
                continue
            pair = (recommendation.get("sourceGenerationId"), recommendation.get("assetId"))
            if pair in allowed_refs and pair not in seen:
                seen.add(pair)
                valid.append(recommendation)
        result["slots"][index]["referenceRecommendations"] = valid
    return result


def sanitize_story_operation_response_data(data, allowed_refs: set[tuple[str, str]]):
    """Keep only explicit source pairs before strict Story operation response validation."""
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return data
    result = deepcopy(data)
    for item in result["items"]:
        if not isinstance(item, dict) or not isinstance(item.get("referenceRecommendations"), list):
            continue
        seen, valid = set(), []
        for recommendation in item["referenceRecommendations"]:
            if not isinstance(recommendation, dict):
                continue
            pair = (recommendation.get("sourceGenerationId"), recommendation.get("assetId"))
            if pair in allowed_refs and pair not in seen:
                seen.add(pair)
                valid.append(recommendation)
        item["referenceRecommendations"] = valid
    return result


def _operation_memory_states(memory_before, new_fact_ids, new_thread_ids, items, subject_ids, *, allocate=False):
    """Project one ordered operation using the shared, operation-wide creation pool."""
    issued = new_fact_ids + new_thread_ids
    if (len(new_fact_ids) != STORY_MEMORY_NEW_SLOTS
            or len(new_thread_ids) != STORY_MEMORY_NEW_SLOTS
            or len(set(issued)) != len(issued)):
        raise ValueError("Memory temporary creation IDs must be unique and operation-wide")
    state = StoryContinuityMemory.model_validate(memory_before)
    if set(issued) & {entry.id for entry in state.durable_facts + state.open_threads}:
        raise ValueError("Temporary slots overlap existing memory IDs")
    states = []
    consumed_fact_ids, consumed_thread_ids = set(), set()
    for item in items:
        state = apply_memory_patch(
            state, item.memory_patch_after, new_fact_ids, new_thread_ids, subject_ids,
            consumed_fact_ids=consumed_fact_ids, consumed_thread_ids=consumed_thread_ids,
        )
        states.append(state)
    if allocate:
        canonical = {temporary: str(uuid4()) for temporary in issued}
        for state in states:
            for entry in state.durable_facts + state.open_threads:
                entry.id = canonical.get(entry.id, entry.id)
    return states


def assemble_initial_range_prompt(snapshot, authority, settings, response_model=None):
    """Expose only saved G1 authority and its real reference evidence to Build Initial Range."""
    g1 = snapshot.generations[0]
    context = {
        **initial_range_authority_context(snapshot),
        "requestedTotalCount": authority.requested_total_count,
        "memoryCreationIds": {"newFactIds": authority.new_fact_ids, "newThreadIds": authority.new_thread_ids},
        "availableCanonicalSubjectIds": list(authority.allowed_subject_ids),
        "responseSchema": (response_model or story_participant_response_model(
            StoryInitialRangeResponse, authority.allowed_subject_ids)).model_json_schema(by_alias=True),
    }
    text = _json(context)
    if not settings.story_vision_enabled:
        content = text
    else:
        content = [{"type": "text", "text": text}]
        content.extend(_image_parts(g1.workspace, f"sourceGenerationId {g1.generation_id} (Generation 1)"))
    return [{"role": "system", "content": INITIAL_RANGE_SYSTEM + "\n" + story_dialogue_policy(snapshot.dialogue_language) + SEQUENCE_CONTEXT_POLICY + SCENE_PLAN_POLICY + SAME_SHOT_OPENING_POLICY + STORY_SHOT_PLAN_POLICY + STORY_DURATION_POLICY + CREATIVE_DIRECTION_POLICY + character_profile_policy_for(snapshot.sequence_subjects) + shot_guidance_policy(getattr(authority, "shot_guidance", None)) + story_planning_style_policy(ProjectStore._story_planning_style(snapshot.manifest))},
            {"role": "user", "content": content}]


def project_initial_range(snapshot, authority, response):
    """Restore saved G1 identity/nonblank authority around model-authored editable fields."""
    if len(response.items) != authority.requested_total_count:
        raise ValueError("Build Initial Range returned the wrong total Generation count")
    g1 = snapshot.generations[0]
    items = []
    protected = {
        "scene_plan": g1.planning.scene_plan,
        "dialogue": g1.workspace.dialogue,
        "incoming_state": g1.incoming_state,
        "intended_outgoing_state": g1.intended_outgoing_state,
        "camera_intent": g1.planning.camera_intent,
    }
    for index, proposed in enumerate(response.items):
        values = proposed.model_dump(mode="json", by_alias=True)
        values["planItemId"] = authority.slot_ids[index]
        values["existingGenerationId"] = g1.generation_id if index == 0 else None
        if index == 0:
            values["durationSeconds"] = (int(g1.workspace.duration_seconds)
                                         if g1.workspace.duration_seconds.is_integer() else None)
            values["relationshipToPrevious"] = None
            # Saved G1 is the authority: expose its actual local structural
            # Subjects in the draft, never the model's proposed participation.
            values["participatingSubjectIds"] = [subject.id for subject in g1.workspace.subjects]
            for field, saved in protected.items():
                if saved is not None and str(saved).strip():
                    alias = StoryInitialRangePlanItem.model_fields[field].alias or field
                    values[alias] = saved
        items.append(StoryInitialRangePlanItem.model_validate(values))
    return StoryInitialRangeProposal(
        sequenceContext=snapshot.sequence_context if snapshot.sequence_context.strip() else response.sequence_context,
        items=items, warnings=response.warnings, authority=authority, authorityToken=authority_token(authority),
    )


def normalize_initial_range_unused_resolutions(response, authority):
    """Ignore only an uncreated, correctly typed issued temporary resolution."""
    result = response.model_copy(deep=True)
    created_facts, created_threads = set(), set()
    additions = []
    for index, item in enumerate(result.items):
        patch = item.memory_patch_after
        fact_upserts = {entry.id for entry in patch.fact_upserts}
        thread_upserts = {entry.id for entry in patch.thread_upserts}
        ignored_facts = {
            entry_id for entry_id in patch.resolved_fact_ids
            if entry_id in authority.new_fact_ids and entry_id not in created_facts and entry_id not in fact_upserts
        }
        ignored_threads = {
            entry_id for entry_id in patch.resolved_thread_ids
            if entry_id in authority.new_thread_ids and entry_id not in created_threads and entry_id not in thread_upserts
        }
        if ignored_facts or ignored_threads:
            item.memory_patch_after = patch.model_copy(update={
                "resolved_fact_ids": [entry_id for entry_id in patch.resolved_fact_ids if entry_id not in ignored_facts],
                "resolved_thread_ids": [entry_id for entry_id in patch.resolved_thread_ids if entry_id not in ignored_threads],
            })
            additions.append(
                f"Generation {index + 1}: Story Memory ignored a resolution for a temporary entry that had not been created."
            )
        created_facts.update(fact_upserts)
        created_threads.update(thread_upserts)
    additions = list(dict.fromkeys(additions))
    if additions:
        result.warnings = result.warnings[:64 - len(additions)] + additions
    return result


def validate_participating_subjects(items, allowed_subject_ids: set[str], *, label: str, skip_first=False):
    """Keep visual cast selection explicit, ordered, and inside signed cast authority."""
    for index, item in enumerate(items):
        if skip_first and index == 0:
            continue
        ids = item.participating_subject_ids
        if len(ids) != len(set(ids)):
            raise ValueError(f"{label} participatingSubjectIds must be unique")
        unknown = set(ids) - allowed_subject_ids
        if unknown:
            raise ValueError(f"{label} participatingSubjectIds contains an unavailable canonical Subject")


def add_initial_range_incomplete_warnings(proposal):
    """Keep blank Initial Range planning fields editable but visible before Apply."""
    labels = {
        "scene_plan": "Scene Plan",
    }
    additions = []
    for index, item in enumerate(proposal.items):
        missing = [label for field, label in labels.items() if not getattr(item, field).strip()]
        if missing:
            additions.append(
                f"Generation {index + 1}: Build left these planning fields incomplete: "
                + ", ".join(missing) + ". Edit the draft before Apply."
            )
    additions = list(dict.fromkeys(additions))
    if additions:
        proposal.warnings = proposal.warnings[:64 - len(additions)] + additions
    return proposal


def initial_range_memory_states(snapshot, proposal, *, allocate=False):
    """G1 starts empty; every later initial-range patch advances its own saved state."""
    authority = proposal.authority
    return _operation_memory_states(
        StoryContinuityMemory(), authority.new_fact_ids, authority.new_thread_ids, proposal.items,
        {subject.id for subject in snapshot.sequence_subjects}, allocate=allocate,
    )


def validate_initial_range_proposal(snapshot, proposal, project_id):
    """Validate signed sole-G1 authority, references and the complete G1→GN memory chain."""
    authority = proposal.authority
    if not hmac.compare_digest(authority_token(authority), proposal.authority_token):
        raise ValueError("Build Initial Range authority is invalid or the server restarted; plan again")
    if (authority.base_project_id != project_id
            or authority.source_generation_count != 1
            or len(snapshot.generations) != 1
            or authority.g1_generation_id != snapshot.generations[0].generation_id):
        raise StaleProjectSnapshotError(
            "Build Initial Range prerequisite changed while planning; review the saved Sequence and try again"
        )
    if len(proposal.items) != authority.requested_total_count or [item.plan_item_id for item in proposal.items] != authority.slot_ids:
        raise ValueError("Build Initial Range item count, identity or order changed")
    if [item.existing_generation_id for item in proposal.items] != [authority.g1_generation_id] + [None] * (len(proposal.items) - 1):
        raise ValueError("Build Initial Range must retain only the signed Generation 1")
    if proposal.items[0].relationship_to_previous is not None or any(
            item.relationship_to_previous is None for item in proposal.items[1:]):
        raise ValueError("Build Initial Range has invalid predecessor relationships")
    allowed = {(pair.source_generation_id, pair.asset_id) for pair in authority.allowed_reference_pairs}
    if not allowed <= snapshot.allowed_refs:
        raise StaleProjectSnapshotError("Build Initial Range reference authority changed; plan again")
    for index, item in enumerate(proposal.items):
        pairs = [(reference.source_generation_id, reference.asset_id) for reference in item.reference_recommendations]
        if len(pairs) != len(set(pairs)) or not set(pairs) <= allowed:
            raise ValueError("Build Initial Range references must be unique eligible G1 source pairs")
        if index == 0 and any(pair[0] != authority.g1_generation_id for pair in pairs):
            raise ValueError("Build Initial Range cannot use a non-G1 reference source")
    current_subject_ids = [subject.id for subject in snapshot.sequence_subjects]
    if authority.allowed_subject_ids and authority.allowed_subject_ids != current_subject_ids:
        raise StaleProjectSnapshotError("Build Initial Range canonical cast changed; plan again")
    if authority.project_fingerprint != initial_range_authority_fingerprint(snapshot):
        raise StaleProjectSnapshotError(
            "Story authority used by Build Initial Range changed while planning; review saved Story fields and try again"
        )
    if snapshot.sequence_context.strip() and proposal.sequence_context != snapshot.sequence_context:
        raise ValueError("Build Initial Range changed authoritative Sequence Context")
    validate_participating_subjects(proposal.items, set(authority.allowed_subject_ids or current_subject_ids),
                                    label="Build Initial Range", skip_first=True)
    return initial_range_memory_states(snapshot, proposal)


async def build_initial_range(store, project_id, request, client_factory=OpenAICompatibleClient, *, item_id_factory=None):
    """Issue one signed 2–12 Generation initial range without persisting it."""
    snapshot = store.read_story_snapshot(project_id)
    if len(snapshot.generations) != 1:
        raise ValueError("Build Initial Range is available only when Generation 1 is the sole canonical Generation")
    if snapshot.generations[0].planning.relationship_to_previous is not None:
        raise ValueError("Save Generation 1 with no Relationship to Previous before Build Initial Range")
    issue = item_id_factory or (lambda: str(uuid4()))
    g1 = snapshot.generations[0]
    authority = StoryInitialRangeAuthority(
        baseProjectId=project_id, projectFingerprint=initial_range_authority_fingerprint(snapshot), g1GenerationId=g1.generation_id,
        sourceGenerationCount=1, requestedTotalCount=request.requested_total_count,
        shotGuidance=request.shot_guidance,
        slotIds=[str(issue()) for _ in range(request.requested_total_count)],
        allowedSubjectIds=[subject.id for subject in snapshot.sequence_subjects],
        newFactIds=[str(issue()) for _ in range(STORY_MEMORY_NEW_SLOTS)],
        newThreadIds=[str(issue()) for _ in range(STORY_MEMORY_NEW_SLOTS)],
        allowedReferencePairs=[StoryReferencePair(sourceGenerationId=source_id, assetId=asset_id)
                               for source_id, asset_id in sorted(snapshot.allowed_refs)
                               if source_id == g1.generation_id],
    )
    response_model = story_participant_response_model(StoryInitialRangeResponse, authority.allowed_subject_ids)
    response = await _call(
        assemble_initial_range_prompt(snapshot, authority, request.llm, response_model), request.llm, client_factory,
        response_model=response_model,
        response_preprocessor=lambda data: sanitize_story_operation_response_data(data, set(snapshot.allowed_refs)),
        stream_response=True,
        memory_generation_start=1,
    )
    response = response.model_copy(update={
        "items": sanitize_reference_recommendations(response.items, set(snapshot.allowed_refs)),
    })
    validate_exact_shots(response.items, request.shot_guidance, 1)
    try:
        response = normalize_initial_range_unused_resolutions(response, authority)
        proposal = project_initial_range(snapshot, authority, response)
        proposal = add_initial_range_incomplete_warnings(proposal)
        validate_initial_range_proposal(snapshot, proposal, project_id)
    except ValidationError as exc:
        raise LLMBackendError("Build Initial Range memory patch exceeds continuity limits", category="story_schema") from exc
    except ValueError as exc:
        raise LLMBackendError(f"Build Initial Range response rejected: {exc}", category="story_authority") from exc
    # A completed model request is never allowed to return a draft that became stale in flight.
    current = store.read_story_snapshot(project_id)
    validate_initial_range_proposal(current, proposal, project_id)
    return proposal


@dataclass(frozen=True)
class BoundedStorySnapshot:
    source: StoryProjectSnapshot
    context: dict
    fingerprint: str
    source_sequence_length: int
    historical_references: tuple["HistoricalReference", ...] = ()


@dataclass(frozen=True)
class HistoricalReference:
    """One selected older Picture, kept separate from recent narrative context."""
    generation: GenerationDocument
    asset_id: str
    payload_hash: str
    subject_ids: tuple[str, ...]


_HISTORICAL_SUBJECT_ROLES = frozenset({"subject_identity", "character_comparison_scale"})


def _reference_subject_ids(asset):
    if isinstance(asset, dict):
        role = asset.get("role")
        identity = asset.get("subjectIdentity", {})
        comparison_subjects = asset.get("comparisonSubjects", [])
    else:
        role = asset.role.value if hasattr(asset.role, "value") else str(asset.role)
        identity = asset.subject_identity.model_dump(mode="json", by_alias=True)
        comparison_subjects = asset.comparison_subject_ids
    if role == "subject_identity":
        value = identity.get("subjectId")
        return (value,) if isinstance(value, str) and value else ()
    if role == "character_comparison_scale":
        return tuple(value for value in comparison_subjects if isinstance(value, str) and value)
    return ()


def _reference_semantic_key(asset):
    """Stable semantic identity for a supported Subject-reference record.

    Payload bytes and filenames are deliberately excluded: transport identity and
    semantic reference identity are separate authorities.
    """
    role = asset.role.value if hasattr(asset.role, "value") else str(asset.role)
    key = {"role": role, "subjectIds": list(_reference_subject_ids(asset))}
    if role == "subject_identity":
        key["identity"] = asset.subject_identity.model_dump(mode="json", by_alias=True)
    elif role == "character_comparison_scale":
        key["comparisonSubjects"] = sorted(set(asset.comparison_subject_ids))
    # Notes/options can materially alter the intended use; filename/order cannot.
    key["notes"] = asset.notes
    key["options"] = asset.options
    return _json(key)


def _prose_mentions(prose, token):
    """Use stable, exact name/alias/technical-label matching, never fuzzy extraction."""
    key = narrative_key(token)
    return bool(key and re.search(r"(?<!\w)" + re.escape(key) + r"(?!\w)", prose))


def _picture_payload_hash(data_url):
    """Content identity for input deduplication; filename is deliberately irrelevant."""
    try:
        return hashlib.sha256(base64.b64decode(data_url.split(",", 1)[1], validate=True)).hexdigest()
    except (AttributeError, IndexError, ValueError, base64.binascii.Error):
        # Hydrated Pictures were already validated.  Keep a deterministic fallback
        # for defensive callers rather than silently treating every failure alike.
        return hashlib.sha256(str(data_url).encode()).hexdigest()


def bounded_story_snapshot(store, project_id, anchor_id, instruction):
    """Build bounded narrative context plus selected stable-Subject Picture evidence.

    The manifest is scanned locally, but only the final three Generations provide
    narrative fields to the model.  Older workspaces are hydrated only after a
    qualifying identity asset has been selected for a relevant canonical Subject.
    """
    directory = store._project_directory(project_id, must_exist=True)
    manifest = store._read_manifest(directory)
    generations, _ = store._generations(manifest)
    if generations[-1].generation_id != anchor_id:
        raise ValueError("Plan Next requires the terminal Generation; use Insert Between for a nonterminal selection")
    if generations[-1].story_memory_status != "trusted":
        raise ValueError("Review and confirm the terminal Generation Story Memory before Plan Next")
    first = max(0, len(generations) - STORY_LOCAL_CONTEXT_GENERATIONS)
    hydrated = tuple(GenerationDocument(
        generation_id=g.generation_id, ordinal=first + i + 1,
        workspace=store._hydrate_workspace(directory, manifest.project_id, manifest.name, g.workspace, g.asset_files),
        incoming_state=g.continuity.incoming_state, intended_outgoing_state=g.continuity.intended_outgoing_state,
        planning=g.planning.model_copy(deep=True),
        story_memory_after=g.story_memory_after.model_copy(deep=True),
        story_memory_status=g.story_memory_status, observed_end_state=observation_projection(g),
    ) for i, g in enumerate(generations[first:]))
    memory = store._story_memory(manifest)
    local = [{
        "generationId": g.generation_id, "ordinal": g.ordinal,
        "scenePlan": g.planning.scene_plan, "dialogue": g.workspace.dialogue,
        "observedEndState": g.observed_end_state,
        "incomingState": g.incoming_state, "intendedOutgoingState": g.intended_outgoing_state,
        "relationshipToPrevious": g.planning.relationship_to_previous, "cameraIntent": g.planning.camera_intent,
        "shotCount": g.planning.shot_count,
        "mode": g.workspace.mode, "durationSeconds": g.workspace.duration_seconds,
        "subjects": [s.model_dump(mode="json") for s in g.workspace.subjects],
        "assets": _asset_metadata(g.workspace),
    } for g in hydrated]
    related = {sid for entry in memory.durable_facts + memory.open_threads for sid in entry.related_subject_ids}
    related |= {s.id for g in hydrated for s in g.workspace.subjects}
    cast, _ = store._sequence_cast(manifest)
    cast_ids = {s.id for s in cast}
    # Names in all supplied prose resolve to stable IDs, including retained aliases.
    prose = narrative_key(_json([local, memory.model_dump(by_alias=True), instruction,
                                  store._story_idea(manifest), store._sequence_context(manifest)]))
    related &= cast_ids
    for subject in cast:
        tokens = [f"Subject {subject.number}", *subject.narrative_aliases]
        if subject.narrative_name:
            tokens.append(subject.narrative_name)
        if any(_prose_mentions(prose, token) for token in tokens):
            related.add(subject.id)

    # Comparison/scale Pictures carry explicit stable Subject associations too.
    # A relevant member therefore makes the other depicted canonical members useful
    # evidence, without making any of them local Generation participants.
    changed = True
    while changed:
        changed = False
        for raw in generations:
            for asset in raw.workspace.get("assets", []):
                if not isinstance(asset, dict) or asset.get("kind") != "image" or asset.get("role") not in _HISTORICAL_SUBJECT_ROLES:
                    continue
                subject_ids = set(_reference_subject_ids(asset)) & cast_ids
                if subject_ids & related:
                    before = len(related)
                    related |= subject_ids
                    changed |= len(related) != before
    relevant = [s for s in cast if s.id in related]

    # Locate only selected *older* identity evidence.  Content hashes are read
    # from canonical content-addressed payloads; filenames are never identity.
    # Semantic records are grouped independently from their pixel payloads.
    local_semantic_keys = {
        _reference_semantic_key(asset)
        for generation in hydrated for asset in generation.workspace.assets
        if asset.kind.value == "image"
        and asset.image is not None
        and asset.role in _HISTORICAL_SUBJECT_ROLES
        and set(_reference_subject_ids(asset)) & related
    }
    candidates = []
    historical_workspaces = {}
    for ordinal, raw in enumerate(generations[:first], start=1):
        files = {entry.asset_id: entry for entry in raw.asset_files}
        for asset in raw.workspace.get("assets", []):
            if not isinstance(asset, dict) or asset.get("kind") != "image" or asset.get("role") not in _HISTORICAL_SUBJECT_ROLES:
                continue
            subject_ids = tuple(sorted(set(_reference_subject_ids(asset)) & related))
            if not subject_ids:
                continue
            asset_id = asset.get("id")
            entry = files.get(asset_id)
            if not isinstance(asset_id, str) or entry is None:
                continue
            payload_path = store._safe_project_relative_path(directory, entry.path)
            payload_hash = hashlib.sha256(payload_path.read_bytes()).hexdigest()
            if raw.generation_id not in historical_workspaces:
                historical_workspaces[raw.generation_id] = store._hydrate_workspace(
                    directory, manifest.project_id, manifest.name, raw.workspace, raw.asset_files
                )
            hydrated_asset = historical_workspaces[raw.generation_id].assets
            source_asset = next(candidate for candidate in hydrated_asset if candidate.id == asset_id)
            candidates.append((
                _reference_semantic_key(source_asset), payload_hash, ordinal, raw, asset_id, subject_ids,
            ))

    # Exact semantic duplicates prefer a qualifying local record; otherwise the
    # newest historical occurrence wins, with source/asset IDs as tie-breakers.
    selected = []
    selected_semantics = set()
    for semantic_key, payload_hash, ordinal, raw, asset_id, subject_ids in sorted(
        candidates, key=lambda item: (-item[2], item[3].generation_id, item[4])
    ):
        if semantic_key in local_semantic_keys or semantic_key in selected_semantics:
            continue
        selected_semantics.add(semantic_key)
        selected.append((semantic_key, payload_hash, ordinal, raw, asset_id, subject_ids))

    selected_by_generation = {}
    for _semantic_key, _payload_hash, ordinal, raw, _asset_id, _subject_ids in selected:
        selected_by_generation.setdefault(raw.generation_id, (ordinal, raw))
    historical_documents = {
        generation_id: GenerationDocument(
            generation_id=raw.generation_id, ordinal=ordinal,
            workspace=store._hydrate_workspace(directory, manifest.project_id, manifest.name, raw.workspace, raw.asset_files),
            incoming_state=raw.continuity.incoming_state, intended_outgoing_state=raw.continuity.intended_outgoing_state,
            planning=raw.planning.model_copy(deep=True),
            story_memory_after=raw.story_memory_after.model_copy(deep=True),
            story_memory_status=raw.story_memory_status, observed_end_state=observation_projection(raw),
        ) for generation_id, (ordinal, raw) in selected_by_generation.items()
    }
    historical_references = tuple(
        HistoricalReference(historical_documents[raw.generation_id], asset_id, payload_hash, subject_ids)
        for _semantic_key, payload_hash, _ordinal, raw, asset_id, subject_ids in selected
    )
    historical_context = []
    for reference in historical_references:
        asset = next(asset for asset in reference.generation.workspace.assets if asset.id == reference.asset_id)
        historical_context.append({
            "sourceGenerationId": reference.generation.generation_id,
            "sourceGenerationOrdinal": reference.generation.ordinal,
            "asset": next(entry for entry in _asset_metadata(reference.generation.workspace) if entry["id"] == asset.id),
            "subjectIds": list(reference.subject_ids),
            "narrativeSubjects": narrative_mapping([subject for subject in relevant if subject.id in reference.subject_ids], include_character_profiles=True),
        })
    context = {
        "projectId": manifest.project_id, "anchorGenerationId": anchor_id, "anchorIsTerminal": True,
        "storyIdea": store._story_idea(manifest), "sequenceContext": store._sequence_context(manifest),
        "storyCreativeDirection": store._story_creative_direction(manifest).model_dump(mode="json", by_alias=True),
        "storyMemory": memory.model_dump(mode="json", by_alias=True),
        "sequenceNarrativeCast": narrative_mapping(relevant, include_character_profiles=True), "localContext": local,
        "historicalReferenceContext": historical_context,
    }
    # Include actual available payload identity, not just filenames. Never send hashes as story prose.
    assets = [{"generationId": g.generation_id,
               "files": [entry.model_dump(mode="json", by_alias=True) for entry in raw.asset_files],
               "pictures": [hashlib.sha256(a.image.data_url.encode()).hexdigest()
                            for a in g.workspace.assets if a.image]}
              for g, raw in zip(hydrated, generations[first:])]
    projected_assets = []
    for reference in historical_references:
        asset = next(candidate for candidate in reference.generation.workspace.assets if candidate.id == reference.asset_id)
        role = asset.role.value if hasattr(asset.role, "value") else str(asset.role)
        projection = {
            "sourceGenerationId": reference.generation.generation_id,
            "assetId": reference.asset_id,
            "payloadHash": reference.payload_hash,
            "role": role,
            "subjectIds": reference.subject_ids,
            "assetMetadata": next(entry for entry in _asset_metadata(reference.generation.workspace)
                                   if entry["id"] == reference.asset_id),
        }
        if role == "subject_identity":
            projection["identity"] = asset.subject_identity.model_dump(mode="json", by_alias=True)
        elif role == "character_comparison_scale":
            projection["comparisonSubjects"] = sorted(set(asset.comparison_subject_ids))
        projected_assets.append(projection)
    fingerprint = hashlib.sha256(_json([context, assets, projected_assets,
                                        [generation.generation_id for generation in generations],
                                        store._dialogue_language(manifest).value, *_style_fingerprint_tail(manifest)]).encode()).hexdigest()
    visible_generations = tuple(sorted((*hydrated, *historical_documents.values()), key=lambda generation: generation.ordinal))
    allowed = frozenset(
        {(g.generation_id, a.id) for g in hydrated for a in g.workspace.assets}
        | {(reference.generation.generation_id, reference.asset_id) for reference in historical_references}
    )
    source = StoryProjectSnapshot(manifest, visible_generations, store._sequence_context(manifest),
                                  store._manifest_fingerprint(manifest), tuple(relevant), allowed,
                                  dialogue_language=store._dialogue_language(manifest))
    return BoundedStorySnapshot(source, context, fingerprint, len(generations), historical_references)


def bounded_insert_snapshot(store, project_id, upstream_id, instruction):
    """Read only the A/B insertion neighbourhood and A's owned memory.

    This intentionally has no relationship to the old temporal projection code.
    A's saved ``storyMemoryAfter`` is the sole memory authority; B is protected
    authoring context only.  Reference evidence has the stricter cutoff of A so
    neither B nor later visuals can reveal a future appearance backwards.
    """
    directory = store._project_directory(project_id, must_exist=True)
    manifest = store._read_manifest(directory)
    generations, _ = store._generations(manifest)
    ids = [generation.generation_id for generation in generations]
    if upstream_id not in ids:
        raise ValueError("Insert Between requires an existing Generation A")
    upstream_index = ids.index(upstream_id)
    if upstream_index == len(generations) - 1:
        raise ValueError("Insert Between requires a nonterminal Generation A with a next Generation B")
    upstream_raw = generations[upstream_index]
    if upstream_raw.story_memory_status != "trusted":
        label = "needs review" if upstream_raw.story_memory_status == "needs_review" else "legacy history unknown"
        raise ValueError(f"Insert Between is blocked because Generation A Story Memory is {label}. Review and confirm it first")

    downstream_index = upstream_index + 1
    # A plus up to two predecessors, B plus one successor.  All documents are
    # hydrated for authoring context, while allowed reference pairs stop at A.
    first, stop = max(0, upstream_index - 2), min(len(generations), downstream_index + 2)
    local_raw = generations[first:stop]
    local_documents = tuple(GenerationDocument(
        generation_id=raw.generation_id, ordinal=ordinal,
        workspace=store._hydrate_workspace(directory, manifest.project_id, manifest.name, raw.workspace, raw.asset_files),
        incoming_state=raw.continuity.incoming_state, intended_outgoing_state=raw.continuity.intended_outgoing_state,
        planning=raw.planning.model_copy(deep=True),
        story_memory_after=raw.story_memory_after.model_copy(deep=True),
        story_memory_status=raw.story_memory_status, observed_end_state=observation_projection(raw),
    ) for ordinal, raw in enumerate(local_raw, start=first + 1))

    def projection(generation):
        # B and B+1 are explicitly prose-only protected boundaries.  This is
        # deliberately more conservative than merely excluding B+1.
        reference_eligible = generation.ordinal <= upstream_index + 1
        return {
            "generationId": generation.generation_id, "ordinal": generation.ordinal,
        "scenePlan": generation.planning.scene_plan, "dialogue": generation.workspace.dialogue,
            "observedEndState": generation.observed_end_state,
            "incomingState": generation.incoming_state, "intendedOutgoingState": generation.intended_outgoing_state,
            "relationshipToPrevious": generation.planning.relationship_to_previous,
            "cameraIntent": generation.planning.camera_intent, "shotCount": generation.planning.shot_count,
            "mode": generation.workspace.mode,
            "durationSeconds": generation.workspace.duration_seconds,
            "subjects": [subject.model_dump(mode="json") for subject in generation.workspace.subjects],
            "assets": _asset_metadata(generation.workspace) if reference_eligible else [],
        }

    by_id = {generation.generation_id: generation for generation in local_documents}
    upstream_context = [projection(by_id[ids[index]]) for index in range(first, upstream_index + 1)]
    downstream_context = [projection(by_id[ids[index]]) for index in range(downstream_index, stop)]
    memory = upstream_raw.story_memory_after.model_copy(deep=True)

    cast, _ = store._sequence_cast(manifest)
    cast_ids = {subject.id for subject in cast}
    related = {subject_id for entry in memory.durable_facts + memory.open_threads
               for subject_id in entry.related_subject_ids}
    related |= {subject.id for generation in local_documents for subject in generation.workspace.subjects}
    prose = narrative_key(_json([upstream_context, downstream_context, memory.model_dump(by_alias=True), instruction,
                                 store._story_idea(manifest), store._sequence_context(manifest)]))
    for subject in cast:
        tokens = [f"Subject {subject.number}", *subject.narrative_aliases]
        if subject.narrative_name:
            tokens.append(subject.narrative_name)
        if any(_prose_mentions(prose, token) for token in tokens):
            related.add(subject.id)
    related &= cast_ids

    # Expand comparison evidence using only history through A.  This is used for
    # stable subject identity selection; it never causes a later asset to enter
    # the prompt or the apply allow-list.
    changed = True
    while changed:
        changed = False
        for raw in generations[:upstream_index + 1]:
            for asset in raw.workspace.get("assets", []):
                if not isinstance(asset, dict) or asset.get("kind") != "image" or asset.get("role") not in _HISTORICAL_SUBJECT_ROLES:
                    continue
                subject_ids = set(_reference_subject_ids(asset)) & cast_ids
                if subject_ids & related:
                    before = len(related)
                    related |= subject_ids
                    changed |= len(related) != before
    relevant = [subject for subject in cast if subject.id in related]

    local_semantic_keys = {
        _reference_semantic_key(asset)
        for generation in local_documents if generation.ordinal <= upstream_index + 1
        for asset in generation.workspace.assets
        if asset.kind.value == "image" and asset.image is not None
        and asset.role in _HISTORICAL_SUBJECT_ROLES and set(_reference_subject_ids(asset)) & related
    }
    candidates, historical_workspaces = [], {}
    for ordinal, raw in enumerate(generations[:first], start=1):
        files = {entry.asset_id: entry for entry in raw.asset_files}
        for asset in raw.workspace.get("assets", []):
            if not isinstance(asset, dict) or asset.get("kind") != "image" or asset.get("role") not in _HISTORICAL_SUBJECT_ROLES:
                continue
            subject_ids = tuple(sorted(set(_reference_subject_ids(asset)) & related))
            asset_id, entry = asset.get("id"), files.get(asset.get("id"))
            if not subject_ids or not isinstance(asset_id, str) or entry is None:
                continue
            payload_hash = hashlib.sha256(store._safe_project_relative_path(directory, entry.path).read_bytes()).hexdigest()
            workspace = historical_workspaces.setdefault(raw.generation_id, store._hydrate_workspace(
                directory, manifest.project_id, manifest.name, raw.workspace, raw.asset_files))
            hydrated_asset = next(candidate for candidate in workspace.assets if candidate.id == asset_id)
            candidates.append((_reference_semantic_key(hydrated_asset), payload_hash, ordinal, raw, asset_id, subject_ids))

    selected, selected_semantics = [], set()
    for candidate in sorted(candidates, key=lambda item: (-item[2], item[3].generation_id, item[4])):
        if candidate[0] not in local_semantic_keys and candidate[0] not in selected_semantics:
            selected_semantics.add(candidate[0])
            selected.append(candidate)
    historical_documents = {}
    for _semantic_key, _payload_hash, ordinal, raw, _asset_id, _subject_ids in selected:
        historical_documents.setdefault(raw.generation_id, GenerationDocument(
            generation_id=raw.generation_id, ordinal=ordinal, workspace=historical_workspaces[raw.generation_id],
            incoming_state=raw.continuity.incoming_state, intended_outgoing_state=raw.continuity.intended_outgoing_state,
            planning=raw.planning.model_copy(deep=True), story_memory_after=raw.story_memory_after.model_copy(deep=True),
            story_memory_status=raw.story_memory_status, observed_end_state=observation_projection(raw),
        ))
    historical_references = tuple(HistoricalReference(historical_documents[raw.generation_id], asset_id, payload_hash, subject_ids)
                                  for _semantic_key, payload_hash, _ordinal, raw, asset_id, subject_ids in selected)
    historical_context = [{
        "sourceGenerationId": reference.generation.generation_id, "sourceGenerationOrdinal": reference.generation.ordinal,
        "asset": next(item for item in _asset_metadata(reference.generation.workspace) if item["id"] == reference.asset_id),
        "subjectIds": list(reference.subject_ids),
        "narrativeSubjects": narrative_mapping([subject for subject in relevant if subject.id in reference.subject_ids], include_character_profiles=True),
    } for reference in historical_references]
    context = {
        "projectId": manifest.project_id,
        "upstreamGenerationId": upstream_id, "downstreamGenerationId": ids[downstream_index],
        "upstreamOrdinal": upstream_index + 1, "downstreamOrdinal": downstream_index + 1,
        "storyIdea": store._story_idea(manifest), "sequenceContext": store._sequence_context(manifest),
        "storyCreativeDirection": store._story_creative_direction(manifest).model_dump(mode="json", by_alias=True),
        # The duplicate, labelled object makes the single authority unambiguous
        # to both model and human inspector without hiding the compatibility key.
        "storyMemory": memory.model_dump(mode="json", by_alias=True),
        "storyMemoryAuthority": {"sourceGenerationId": upstream_id, "afterOrdinal": upstream_index + 1,
                                  "memory": memory.model_dump(mode="json", by_alias=True)},
        "upstreamHistory": upstream_context,
        "insertedTemporarySlots": "Issued slot IDs below are the only editable proposed Generations.",
        "downstreamProtectedBoundary": downstream_context,
        "referenceCutoff": {"inclusiveGenerationId": upstream_id, "inclusiveOrdinal": upstream_index + 1},
        "sequenceNarrativeCast": narrative_mapping(relevant, include_character_profiles=True), "historicalReferenceContext": historical_context,
    }
    # Bind the visual/reference metadata that may actually be used, not the
    # protected future boundary's payloads.
    eligible_documents = [document for document in local_documents if document.ordinal <= upstream_index + 1]
    eligible_raw = generations[first:upstream_index + 1]
    assets = [{"generationId": document.generation_id,
               "files": [entry.model_dump(mode="json", by_alias=True) for entry in raw.asset_files],
               "pictures": [hashlib.sha256(asset.image.data_url.encode()).hexdigest()
                            for asset in document.workspace.assets if asset.image]}
              for document, raw in zip(eligible_documents, eligible_raw)]
    projected_assets = []
    for reference in historical_references:
        asset = next(candidate for candidate in reference.generation.workspace.assets if candidate.id == reference.asset_id)
        role = asset.role.value if hasattr(asset.role, "value") else str(asset.role)
        projection = {"sourceGenerationId": reference.generation.generation_id, "assetId": reference.asset_id,
                      "payloadHash": reference.payload_hash, "role": role, "subjectIds": reference.subject_ids,
                      "assetMetadata": next(item for item in _asset_metadata(reference.generation.workspace)
                                            if item["id"] == reference.asset_id)}
        if role == "subject_identity":
            projection["identity"] = asset.subject_identity.model_dump(mode="json", by_alias=True)
        elif role == "character_comparison_scale":
            projection["comparisonSubjects"] = sorted(set(asset.comparison_subject_ids))
        projected_assets.append(projection)
    fingerprint = hashlib.sha256(_json([context, assets, projected_assets, ids,
                                        store._dialogue_language(manifest).value, *_style_fingerprint_tail(manifest)]).encode()).hexdigest()
    visible = tuple(sorted((*local_documents, *historical_documents.values()), key=lambda generation: generation.ordinal))
    allowed = frozenset(
        {(generation.generation_id, asset.id) for generation in local_documents
         if generation.ordinal <= upstream_index + 1 for asset in generation.workspace.assets}
        | {(reference.generation.generation_id, reference.asset_id) for reference in historical_references}
    )
    source = StoryProjectSnapshot(manifest, visible, store._sequence_context(manifest),
                                  store._manifest_fingerprint(manifest), tuple(relevant), allowed,
                                  dialogue_language=store._dialogue_language(manifest))
    return BoundedStorySnapshot(source, context, fingerprint, len(generations), historical_references)


def bounded_replan_snapshot(store, project_id, start_id, end_id, instruction):
    """Project one existing C..D range with U/B kept as protected boundaries.

    The projection deliberately reads the full manifest only to establish stable
    IDs, adjacency, and selected historical Subject identity evidence.  Its model
    context is bounded to U plus two predecessors, every target, and B plus one
    successor.  The only Story Memory authority is U's saved snapshot (or an
    empty initial state when C is Generation 1).
    """
    directory = store._project_directory(project_id, must_exist=True)
    manifest = store._read_manifest(directory)
    generations, _ = store._generations(manifest)
    ids = [generation.generation_id for generation in generations]
    if start_id not in ids or end_id not in ids:
        raise ValueError("Replan Range requires existing Start and End Generations")
    start_index, end_index = ids.index(start_id), ids.index(end_id)
    if end_index < start_index:
        raise ValueError("Replan Range End Generation must not precede Start Generation")
    count = end_index - start_index + 1
    if count > STORY_REPLAN_MAX_GENERATIONS:
        raise ValueError(f"Replan Range supports at most {STORY_REPLAN_MAX_GENERATIONS} contiguous Generations")

    upstream_index = start_index - 1 if start_index else None
    downstream_index = end_index + 1 if end_index + 1 < len(generations) else None
    if upstream_index is not None and generations[upstream_index].story_memory_status != "trusted":
        label = "needs review" if generations[upstream_index].story_memory_status == "needs_review" else "legacy history unknown"
        raise ValueError(f"Replan Range is blocked because upstream Generation Story Memory is {label}. Review and confirm it first")

    first = max(0, start_index - 3) if upstream_index is not None else 0
    stop = min(len(generations), end_index + 3)
    local_raw = generations[first:stop]
    local_documents = tuple(GenerationDocument(
        generation_id=raw.generation_id, ordinal=ordinal,
        workspace=store._hydrate_workspace(directory, manifest.project_id, manifest.name, raw.workspace, raw.asset_files),
        incoming_state=raw.continuity.incoming_state, intended_outgoing_state=raw.continuity.intended_outgoing_state,
        planning=raw.planning.model_copy(deep=True),
        story_memory_after=raw.story_memory_after.model_copy(deep=True), story_memory_status=raw.story_memory_status, observed_end_state=observation_projection(raw),
    ) for ordinal, raw in enumerate(local_raw, start=first + 1))
    by_id = {generation.generation_id: generation for generation in local_documents}

    def projection(generation, *, reference_eligible):
        return {
            "generationId": generation.generation_id, "ordinal": generation.ordinal,
        "scenePlan": generation.planning.scene_plan, "dialogue": generation.workspace.dialogue,
            "observedEndState": generation.observed_end_state,
            "incomingState": generation.incoming_state, "intendedOutgoingState": generation.intended_outgoing_state,
            "relationshipToPrevious": generation.planning.relationship_to_previous,
            "cameraIntent": generation.planning.camera_intent, "shotCount": generation.planning.shot_count,
            "mode": generation.workspace.mode,
            "durationSeconds": generation.workspace.duration_seconds,
            "subjects": [subject.model_dump(mode="json") for subject in generation.workspace.subjects],
            # B and its successor remain prose-only orientation.  They can never
            # contribute a source pair or a visual payload to this replan.
            "assets": _asset_metadata(generation.workspace) if reference_eligible else [],
        }

    upstream_context = [projection(by_id[ids[index]], reference_eligible=True)
                        for index in range(first, start_index)]
    target_context = [projection(by_id[ids[index]], reference_eligible=True)
                      for index in range(start_index, end_index + 1)]
    downstream_context = [projection(by_id[ids[index]], reference_eligible=False)
                          for index in range(end_index + 1, stop)]
    memory = (generations[upstream_index].story_memory_after.model_copy(deep=True)
              if upstream_index is not None else StoryContinuityMemory())

    cast, _ = store._sequence_cast(manifest)
    cast_ids = {subject.id for subject in cast}
    related = {subject_id for entry in memory.durable_facts + memory.open_threads
               for subject_id in entry.related_subject_ids}
    related |= {subject.id for generation in local_documents
                if generation.ordinal <= end_index + 1 for subject in generation.workspace.subjects}
    prose = narrative_key(_json([
        upstream_context, target_context, downstream_context, memory.model_dump(by_alias=True), instruction,
        store._story_idea(manifest), store._sequence_context(manifest),
    ]))
    for subject in cast:
        tokens = [f"Subject {subject.number}", *subject.narrative_aliases]
        if subject.narrative_name:
            tokens.append(subject.narrative_name)
        if any(_prose_mentions(prose, token) for token in tokens):
            related.add(subject.id)
    related &= cast_ids

    # Resolve stable Subject identity only from C or earlier.  Later identity
    # assets cannot suppress a qualifying historical record by pixel or semantic
    # deduplication because they never enter either candidate set.
    changed = True
    while changed:
        changed = False
        for raw in generations[:end_index + 1]:
            for asset in raw.workspace.get("assets", []):
                if not isinstance(asset, dict) or asset.get("kind") != "image" or asset.get("role") not in _HISTORICAL_SUBJECT_ROLES:
                    continue
                subject_ids = set(_reference_subject_ids(asset)) & cast_ids
                if subject_ids & related:
                    before = len(related)
                    related |= subject_ids
                    changed |= len(related) != before
    relevant = [subject for subject in cast if subject.id in related]

    local_semantic_keys = {
        _reference_semantic_key(asset)
        for generation in local_documents if generation.ordinal <= end_index + 1
        for asset in generation.workspace.assets
        if asset.kind.value == "image" and asset.image is not None
        and asset.role in _HISTORICAL_SUBJECT_ROLES and set(_reference_subject_ids(asset)) & related
    }
    candidates, historical_workspaces = [], {}
    for ordinal, raw in enumerate(generations[:first], start=1):
        files = {entry.asset_id: entry for entry in raw.asset_files}
        for asset in raw.workspace.get("assets", []):
            if not isinstance(asset, dict) or asset.get("kind") != "image" or asset.get("role") not in _HISTORICAL_SUBJECT_ROLES:
                continue
            subject_ids = tuple(sorted(set(_reference_subject_ids(asset)) & related))
            asset_id, entry = asset.get("id"), files.get(asset.get("id"))
            if not subject_ids or not isinstance(asset_id, str) or entry is None:
                continue
            payload_hash = hashlib.sha256(store._safe_project_relative_path(directory, entry.path).read_bytes()).hexdigest()
            workspace = historical_workspaces.setdefault(raw.generation_id, store._hydrate_workspace(
                directory, manifest.project_id, manifest.name, raw.workspace, raw.asset_files))
            hydrated_asset = next(candidate for candidate in workspace.assets if candidate.id == asset_id)
            candidates.append((_reference_semantic_key(hydrated_asset), payload_hash, ordinal, raw, asset_id, subject_ids))
    selected, selected_semantics = [], set()
    for candidate in sorted(candidates, key=lambda item: (-item[2], item[3].generation_id, item[4])):
        if candidate[0] not in local_semantic_keys and candidate[0] not in selected_semantics:
            selected_semantics.add(candidate[0])
            selected.append(candidate)
    historical_documents = {}
    for _semantic_key, _payload_hash, ordinal, raw, _asset_id, _subject_ids in selected:
        historical_documents.setdefault(raw.generation_id, GenerationDocument(
            generation_id=raw.generation_id, ordinal=ordinal, workspace=historical_workspaces[raw.generation_id],
            incoming_state=raw.continuity.incoming_state, intended_outgoing_state=raw.continuity.intended_outgoing_state,
            planning=raw.planning.model_copy(deep=True), story_memory_after=raw.story_memory_after.model_copy(deep=True),
            story_memory_status=raw.story_memory_status, observed_end_state=observation_projection(raw),
        ))
    historical_references = tuple(HistoricalReference(historical_documents[raw.generation_id], asset_id, payload_hash, subject_ids)
                                  for _semantic_key, payload_hash, _ordinal, raw, asset_id, subject_ids in selected)
    historical_context = [{
        "sourceGenerationId": reference.generation.generation_id, "sourceGenerationOrdinal": reference.generation.ordinal,
        "asset": next(item for item in _asset_metadata(reference.generation.workspace) if item["id"] == reference.asset_id),
        "subjectIds": list(reference.subject_ids),
        "narrativeSubjects": narrative_mapping([subject for subject in relevant if subject.id in reference.subject_ids], include_character_profiles=True),
    } for reference in historical_references]
    upstream_id = ids[upstream_index] if upstream_index is not None else None
    downstream_id = ids[downstream_index] if downstream_index is not None else None
    context = {
        "projectId": manifest.project_id, "startGenerationId": start_id, "endGenerationId": end_id,
        "targetGenerationIds": ids[start_index:end_index + 1],
        "startOrdinal": start_index + 1, "endOrdinal": end_index + 1,
        "upstreamGenerationId": upstream_id, "downstreamGenerationId": downstream_id,
        "storyIdea": store._story_idea(manifest), "sequenceContext": store._sequence_context(manifest),
        "storyCreativeDirection": store._story_creative_direction(manifest).model_dump(mode="json", by_alias=True),
        "storyMemory": memory.model_dump(mode="json", by_alias=True),
        "storyMemoryAuthority": {"sourceGenerationId": upstream_id, "afterOrdinal": start_index if upstream_id else 0,
                                  "memory": memory.model_dump(mode="json", by_alias=True)},
        "upstreamProtectedHistory": upstream_context,
        "targetRange": target_context,
        "downstreamProtectedBoundary": downstream_context,
        "referenceCutoff": {"inclusiveGenerationId": end_id, "inclusiveOrdinal": end_index + 1},
        "sequenceNarrativeCast": narrative_mapping(relevant, include_character_profiles=True), "historicalReferenceContext": historical_context,
    }
    eligible_documents = [document for document in local_documents if document.ordinal <= end_index + 1]
    eligible_raw = generations[first:end_index + 1]
    assets = [{"generationId": document.generation_id,
               "files": [entry.model_dump(mode="json", by_alias=True) for entry in raw.asset_files],
               "pictures": [hashlib.sha256(asset.image.data_url.encode()).hexdigest()
                            for asset in document.workspace.assets if asset.image]}
              for document, raw in zip(eligible_documents, eligible_raw)]
    projected_assets = []
    for reference in historical_references:
        asset = next(candidate for candidate in reference.generation.workspace.assets if candidate.id == reference.asset_id)
        role = asset.role.value if hasattr(asset.role, "value") else str(asset.role)
        projection = {"sourceGenerationId": reference.generation.generation_id, "assetId": reference.asset_id,
                      "payloadHash": reference.payload_hash, "role": role, "subjectIds": reference.subject_ids,
                      "assetMetadata": next(item for item in _asset_metadata(reference.generation.workspace)
                                            if item["id"] == reference.asset_id)}
        if role == "subject_identity":
            projection["identity"] = asset.subject_identity.model_dump(mode="json", by_alias=True)
        elif role == "character_comparison_scale":
            projection["comparisonSubjects"] = sorted(set(asset.comparison_subject_ids))
        projected_assets.append(projection)
    target_memory_authority = [
        {"generationId": generation.generation_id,
         "memory": generation.story_memory_after.model_dump(mode="json", by_alias=True),
         "status": generation.story_memory_status}
        for generation in generations[start_index:end_index + 1]
    ]
    fingerprint = hashlib.sha256(_json([context, assets, projected_assets, ids,
                                        store._dialogue_language(manifest).value,
                                        target_memory_authority, *_style_fingerprint_tail(manifest)]).encode()).hexdigest()
    visible = tuple(sorted((*local_documents, *historical_documents.values()), key=lambda generation: generation.ordinal))
    allowed = frozenset(
        {(generation.generation_id, asset.id) for generation in local_documents
         if generation.ordinal <= end_index + 1 for asset in generation.workspace.assets}
        | {(reference.generation.generation_id, reference.asset_id) for reference in historical_references}
    )
    source = StoryProjectSnapshot(manifest, visible, store._sequence_context(manifest),
                                  store._manifest_fingerprint(manifest), tuple(relevant), allowed,
                                  dialogue_language=store._dialogue_language(manifest))
    return BoundedStorySnapshot(source, context, fingerprint, len(generations), historical_references)


OBSERVATION_POLICY = """
Observed End State is read-only evidence from the selected rendered output. Effective observed visual state outranks intended visual outcome where they conflict; current explicit authoring still wins. Do not rewrite observations to match a plan. Do not infer unseen state. Preserve nonvisual Story Memory; observation is not a semantic memory replacement.
"""

NEXT_SYSTEM = """Plan only the issued new slots after the read-only terminal anchor.
Return exactly the issued planItemIds in issued order, with all authoring fields and a memoryPatchAfter on EVERY slot.
Apply each slot patch in order: it describes the state AFTER that Generation.
Never place final-slot developments in an earlier slot. Empty patches explicitly preserve state.
Never return existing Generations, stable Generation UUIDs, settings, mode changes, Subjects,
Project identity, or Sequence Context changes. New Generations inherit predecessor mode.
Do not return Creative Controls; they belong to Workspace authoring.
Exact Dialogue / Lyrics is verbatim spoken/sung text; blank means silence.
Use localContext for immediate continuity and storyMemory for important long-range state.
Memory is selective, bounded continuity state, never a transcript or hidden scratchpad.
recentSummary replaces only the compact synopsis of current position and important developments.
durableFacts hold only facts that may matter beyond the local window. Update a changing fact
using its existing ID rather than creating contradictory copies. openThreads hold unresolved
promises, goals, mysteries and threats. Resolve a thread explicitly when fulfilled; never drop
an unresolved thread merely because this batch does not mention it. Avoid redundant entries.
memoryPatchAfter must be {} when no memory update is needed. Otherwise it may contain ONLY
recentSummary, factUpserts, resolvedFactIds, threadUpserts, and resolvedThreadIds; it may omit unchanged keys.
Never use generic placeholders such as field, key, name, or value. durableFacts and openThreads in the supplied
storyMemory are read-only current memory, not response-field names. memoryCreationIds is one
operation-wide pool: any unused newFactId or newThreadId may first be used in any slot, then later
slots may update or resolve it. A temporary ID cannot be resolved before its first upsert. Fact IDs
and Thread IDs are separate collections: to turn a fulfilled thread into a durable fact, resolve
that thread ID and create a fact with an unused newFactId. Never cross collections. New canonical
IDs are allocated on Apply. relatedSubjectIds use supplied canonical IDs, never names or labels.
Current Narrative Names and technical Subject N labels denote the SAME identity. Newly authored prose must use
current names; previous names are recognition-only and are not output vocabulary. Cast availability and memory association never imply shot
participation. Do not invent Subjects. localContext is immediate narrative history. The separate
historicalReferenceContext contains only relevant older stable-Subject visual identity evidence;
seeing it does not require that Subject to appear. References may be recommended only from explicit
sourceGenerationId + assetId pairs supplied in localContext[].assets or historicalReferenceContext.
localContext[].assets is the complete set of reference assets local to this operation.
If no available pair is supplied,
referenceRecommendations MUST be empty or omitted. Never invent asset IDs from scene prose;
descriptive nouns in Scene Plan or Story Memory are not asset IDs. Apply attaches only
valid available references and their required Subjects. Without participation or a recommendation, no
Subject/reference is materialized; participatingSubjectIds independently materializes visual Subjects and eligible
automatic identity evidence.
participatingSubjectIds is the exact canonical visual cast physically appearing in each slot. Use only supplied
sequenceNarrativeCast IDs exactly; do not include a Subject merely because they are discussed, remembered, or
available in the cast. MiniConstruct automatically carries subject_identity Pictures for participating Subjects.
referenceRecommendations are optional additional evidence and need not repeat those automatic identity Pictures.
Pictures are visible only when transmitted. Audio/Video are metadata-only, never claim to have
heard or watched them. No historical information beyond supplied context can be assumed known.
Return one JSON object matching responseSchema. No markdown or additional fields.
""" + PARTICIPATING_SUBJECT_POLICY


INSERT_SYSTEM = """Plan only the issued temporary slots between protected Generation A and protected Generation B.
Return exactly the issued planItemIds in issued order, with all authoring fields and a memoryPatchAfter on EVERY slot.
A and B are immutable narrative boundaries. Never return, modify, or duplicate either boundary or any other existing
Generation. For the first slot, relationshipToPrevious describes its relationship to A; later slots relate to the
preceding temporary slot. B's existing relationship is not yours to rewrite.
storyMemory and storyMemoryAuthority are the sole Story Memory truth and are the state AFTER A. Do not infer facts
from downstreamProtectedBoundary as if they already happened, and do not use any unprovided future memory. Apply each
slot patch in order: it describes the state AFTER that Generation. Never place later-slot developments in an earlier
slot. Empty patches explicitly preserve state.
Memory is selective, bounded continuity state, never a transcript or hidden scratchpad. recentSummary carries current
position and immediate action. durableFacts must matter beyond the local window; do not store mere current location or
action. openThreads are unresolved commitments, goals, mysteries, threats, or dependencies likely to matter later;
do not create one for every next action, avoid duplicate goals, and resolve one only when a beat actually fulfills it.
Preserve agent, recipient, ownership, direction, negation, and obligation semantics. memoryPatchAfter must be {} when
no memory update is needed. Otherwise it may contain ONLY recentSummary, factUpserts, resolvedFactIds, threadUpserts,
and resolvedThreadIds. Never use generic placeholders such as field, key, name, or value. memoryCreationIds is one
operation-wide pool: any unused newFactId or newThreadId may first be used in any slot, then later slots may update or
resolve it; do not resolve it before its first upsert. Fact IDs and Thread IDs are separate collections: resolve a
fulfilled thread and create a new fact with an unused newFactId, never cross collections. New canonical IDs are allocated
on Apply. relatedSubjectIds use supplied canonical IDs, never names or labels.
New Generations inherit A's mode. Exact Dialogue / Lyrics is verbatim spoken/sung text; blank means silence. Current
Narrative Names and technical Subject N labels denote the SAME identity. Newly authored prose must use current names;
previous names are recognition-only and are not output vocabulary. Cast availability and memory association never imply shot participation. Do not invent Subjects.
participatingSubjectIds is the exact canonical visual cast physically appearing in each inserted slot. Use only
supplied sequenceNarrativeCast IDs exactly; do not include a Subject merely because they are discussed, remembered,
or available in the cast. MiniConstruct automatically carries subject_identity Pictures for participating Subjects.
referenceRecommendations are optional additional evidence and need not repeat those automatic identity Pictures.
Do not return Creative Controls; they belong to Workspace authoring.
upstreamHistory is history, insertedTemporarySlots identifies the writable proposal, and downstreamProtectedBoundary
is narrative orientation only. The latter is not Story Memory authority. historicalReferenceContext contains selected
older stable-Subject visual identity evidence. References may be recommended only from explicit sourceGenerationId +
assetId pairs supplied in upstreamHistory[].assets or historicalReferenceContext; referenceCutoff is inclusive, so B
and every later Generation are forbidden reference sources. If no available pair is supplied, referenceRecommendations
MUST be empty or omitted. Never invent asset IDs from scene prose or Story Memory. Apply attaches only valid available
references and their required Subjects. Pictures are visible only when transmitted. Audio/Video are metadata-only.
No historical information beyond supplied context can be assumed known. Return one JSON object matching responseSchema.
No markdown or additional fields.
""" + PARTICIPATING_SUBJECT_POLICY


REPLAN_SYSTEM = """Rewrite only the existing Generations in targetRange.
Return exactly one item for every targetGenerationId, in exactly that supplied order, and address every item by its
existingGenerationId. Do not return U, B, any outside Generation, any new Generation ID, settings, Subjects, Project
identity, or Sequence Context. Existing Generation UUIDs, order, assets, and creation identity stay unchanged.
All authoring fields on targetRange are editable. Exact Dialogue / Lyrics is verbatim spoken/sung text; blank is
deliberate silence. For the first target, relationshipToPrevious describes its relationship to U when U exists; if
the range starts at Generation 1 it must be null. Later target relationships must use the normal closed enum. Do not
rewrite B.relationshipToPrevious.
Do not return Creative Controls; existing Workspace values remain unchanged.
storyMemory and storyMemoryAuthority are the sole memory truth: they are U's state after its Generation, or the empty
initial state for Generation 1. Never infer memory facts from downstreamProtectedBoundary or use any later Story
Memory. Apply each memoryPatchAfter sequentially to derive the state after each target. Use {} when no memory update
is needed. Otherwise patches may contain only recentSummary, factUpserts, resolvedFactIds, threadUpserts, and
resolvedThreadIds. Never use generic placeholders such as field, key, name, or value. memoryCreationIds is one
operation-wide pool: any unused newFactId or newThreadId may first be used in any target, then later targets may update
or resolve it; do not resolve it before its first upsert. Fact IDs and Thread IDs are separate collections: resolve a
fulfilled thread and create a new fact with an unused newFactId, never cross collections. New canonical IDs are allocated
only on Apply. relatedSubjectIds use supplied canonical IDs, never names or labels. Keep unresolved threads until explicitly resolved.
upstreamProtectedHistory and downstreamProtectedBoundary are read-only orientation. targetRange is the only editable
authoring source. The downstream boundary is prose only, never historical memory truth. References may be recommended
only from explicit sourceGenerationId + assetId pairs supplied in the eligible upstream/target assets or
historicalReferenceContext. referenceCutoff is inclusive: B and every later Generation are forbidden sources.
Existing target assets are retained. Recommendations are additive only. Historical visual identity evidence is for
stable Subjects only; it does not require a Subject to appear. Pictures are visible only when transmitted; audio and
video are metadata-only. Return one JSON object matching responseSchema, with no Markdown or additional fields.
"""


def assemble_next_prompt(snapshot, authority, settings, response_model=None):
    context = {**snapshot.context, "instruction": authority.instruction,
               "requestedCount": len(authority.slot_ids), "slotIds": authority.slot_ids,
               "memoryCreationIds": {"newFactIds": authority.new_fact_ids, "newThreadIds": authority.new_thread_ids},
               "availableCanonicalSubjectIds": list(authority.allowed_subject_ids),
               "responseSchema": (response_model or story_participant_response_model(
                   StoryNextResponse, authority.allowed_subject_ids)).model_json_schema(by_alias=True)}
    text = _json(context)
    content = text
    if settings.story_vision_enabled:
        content = [{"type": "text", "text": text}]
        seen_payloads = set()

        def add_pictures(generation, asset_ids=None, historical=False):
            for asset in generation.workspace.ordered_assets():
                if asset_ids is not None and asset.id not in asset_ids:
                    continue
                if (generation.generation_id, asset.id) not in snapshot.source.allowed_refs:
                    continue
                if asset.kind.value != "image":
                    continue
                if asset.image is None:
                    raise ValueError("Picture payload unavailable; Replace Picture before Story Planner Vision")
                payload_hash = _picture_payload_hash(asset.image.data_url)
                kind = "historical visual identity input" if historical else "visual input"
                if payload_hash in seen_payloads:
                    content.append({"type": "text", "text": f"Additional {kind} metadata for sourceGenerationId {generation.generation_id} (Generation {generation.ordinal}) asset {asset.id} ({asset.filename}), role {asset.role}; it shares pixels with an earlier visual input."})
                    continue
                seen_payloads.add(payload_hash)
                content.extend([
                    {"type": "text", "text": f"{kind} for sourceGenerationId {generation.generation_id} (Generation {generation.ordinal}) asset {asset.id} ({asset.filename}), role {asset.role}:"},
                    {"type": "image_url", "image_url": {"url": prompt_writer_image(asset.image.data_url)[0]}},
                ])

        historical_generation_ids = {reference.generation.generation_id for reference in snapshot.historical_references}
        for generation in snapshot.source.generations:
            if generation.generation_id not in historical_generation_ids:
                add_pictures(generation)
        for reference in snapshot.historical_references:
            add_pictures(reference.generation, {reference.asset_id}, historical=True)
    return [{"role": "system", "content": NEXT_SYSTEM + SEQUENCE_CONTEXT_POLICY + SCENE_PLAN_POLICY + SAME_SHOT_OPENING_POLICY + STORY_SHOT_PLAN_POLICY + STORY_DURATION_POLICY + OBSERVATION_POLICY + "\n" + story_dialogue_policy(snapshot.source.dialogue_language) + CREATIVE_DIRECTION_POLICY + character_profile_policy_for(snapshot.source.sequence_subjects) + shot_guidance_policy(authority.shot_guidance) + story_planning_style_policy(ProjectStore._story_planning_style(snapshot.source.manifest))}, {"role": "user", "content": content}]


def assemble_insert_prompt(snapshot, authority, settings, response_model=None):
    """Use Plan Next's slot/vision machinery with an Insert-specific contract."""
    context = {**snapshot.context, "instruction": authority.instruction,
               "requestedCount": len(authority.slot_ids), "slotIds": authority.slot_ids,
               "memoryCreationIds": {"newFactIds": authority.new_fact_ids, "newThreadIds": authority.new_thread_ids},
               "availableCanonicalSubjectIds": list(authority.allowed_subject_ids),
               "responseSchema": (response_model or story_participant_response_model(
                   StoryInsertResponse, authority.allowed_subject_ids)).model_json_schema(by_alias=True)}
    text = _json(context)
    content = text
    if settings.story_vision_enabled:
        content = [{"type": "text", "text": text}]
        seen_payloads = set()

        def add_pictures(generation, asset_ids=None, historical=False):
            for asset in generation.workspace.ordered_assets():
                if asset_ids is not None and asset.id not in asset_ids:
                    continue
                if (generation.generation_id, asset.id) not in snapshot.source.allowed_refs:
                    continue
                if asset.kind.value != "image":
                    continue
                if asset.image is None:
                    raise ValueError("Picture payload unavailable; Replace Picture before Story Planner Vision")
                payload_hash = _picture_payload_hash(asset.image.data_url)
                kind = "historical visual identity input" if historical else "visual input"
                if payload_hash in seen_payloads:
                    content.append({"type": "text", "text": f"Additional {kind} metadata for sourceGenerationId {generation.generation_id} (Generation {generation.ordinal}) asset {asset.id} ({asset.filename}), role {asset.role}; it shares pixels with an earlier visual input."})
                    continue
                seen_payloads.add(payload_hash)
                content.extend([
                    {"type": "text", "text": f"{kind} for sourceGenerationId {generation.generation_id} (Generation {generation.ordinal}) asset {asset.id} ({asset.filename}), role {asset.role}:"},
                    {"type": "image_url", "image_url": {"url": prompt_writer_image(asset.image.data_url)[0]}},
                ])

        historical_generation_ids = {reference.generation.generation_id for reference in snapshot.historical_references}
        for generation in snapshot.source.generations:
            if generation.generation_id not in historical_generation_ids:
                add_pictures(generation)
        for reference in snapshot.historical_references:
            add_pictures(reference.generation, {reference.asset_id}, historical=True)
    return [{"role": "system", "content": INSERT_SYSTEM + SEQUENCE_CONTEXT_POLICY + SCENE_PLAN_POLICY + SAME_SHOT_OPENING_POLICY + STORY_SHOT_PLAN_POLICY + STORY_DURATION_POLICY + OBSERVATION_POLICY + "\n" + story_dialogue_policy(snapshot.source.dialogue_language) + CREATIVE_DIRECTION_POLICY + character_profile_policy_for(snapshot.source.sequence_subjects) + shot_guidance_policy(authority.shot_guidance) + story_planning_style_policy(ProjectStore._story_planning_style(snapshot.source.manifest))}, {"role": "user", "content": content}]


def _replan_visual_content(snapshot, text):
    """Attach only signed, eligible images; B and later have no allowed pairs."""
    content = [{"type": "text", "text": text}]
    seen_payloads = set()

    def add_pictures(generation, asset_ids=None, historical=False):
        for asset in generation.workspace.ordered_assets():
            if asset_ids is not None and asset.id not in asset_ids:
                continue
            if (generation.generation_id, asset.id) not in snapshot.source.allowed_refs:
                continue
            if asset.kind.value != "image":
                continue
            if asset.image is None:
                raise ValueError("Picture payload unavailable; Replace Picture before Story Planner Vision")
            payload_hash = _picture_payload_hash(asset.image.data_url)
            kind = "historical visual identity input" if historical else "visual input"
            if payload_hash in seen_payloads:
                content.append({"type": "text", "text": f"Additional {kind} metadata for sourceGenerationId {generation.generation_id} (Generation {generation.ordinal}) asset {asset.id} ({asset.filename}), role {asset.role}; it shares pixels with an earlier visual input."})
                continue
            seen_payloads.add(payload_hash)
            content.extend([
                {"type": "text", "text": f"{kind} for sourceGenerationId {generation.generation_id} (Generation {generation.ordinal}) asset {asset.id} ({asset.filename}), role {asset.role}:"},
                {"type": "image_url", "image_url": {"url": prompt_writer_image(asset.image.data_url)[0]}},
            ])

    historical_generation_ids = {reference.generation.generation_id for reference in snapshot.historical_references}
    for generation in snapshot.source.generations:
        if generation.generation_id not in historical_generation_ids:
            add_pictures(generation)
    for reference in snapshot.historical_references:
        add_pictures(reference.generation, {reference.asset_id}, historical=True)
    return content


def assemble_replan_prompt(snapshot, authority, settings):
    context = {
        **snapshot.context,
        "instruction": authority.instruction,
        "memoryCreationIds": {"newFactIds": authority.new_fact_ids, "newThreadIds": authority.new_thread_ids},
        "allowedReferencePairs": [pair.model_dump(mode="json", by_alias=True) for pair in authority.allowed_reference_pairs],
        "responseSchema": StoryReplanResponse.model_json_schema(by_alias=True),
    }
    text = _json(context)
    content = _replan_visual_content(snapshot, text) if settings.story_vision_enabled else text
    return [{"role": "system", "content": REPLAN_SYSTEM + SEQUENCE_CONTEXT_POLICY + SCENE_PLAN_POLICY + SAME_SHOT_OPENING_POLICY + STORY_SHOT_PLAN_POLICY + OBSERVATION_POLICY + "\n" + story_dialogue_policy(snapshot.source.dialogue_language) + CREATIVE_DIRECTION_POLICY + character_profile_policy_for(snapshot.source.sequence_subjects) + shot_guidance_policy(authority.shot_guidance) + story_planning_style_policy(ProjectStore._story_planning_style(snapshot.source.manifest))}, {"role": "user", "content": content}]


def validate_next_proposal(snapshot, proposal, project_id):
    authority = proposal.authority
    if not hmac.compare_digest(authority_token(authority), proposal.authority_token):
        raise ValueError("Plan Next authority is invalid or the server restarted; plan again")
    if authority.base_project_id != project_id or authority.operation_fingerprint != snapshot.fingerprint:
        raise StaleProjectSnapshotError("Plan Next context or Story Memory changed; plan again")
    if [s.plan_item_id for s in proposal.slots] != authority.slot_ids:
        raise ValueError("Plan Next slot count, identity or order changed")
    for slot in proposal.slots:
        pairs = [(r.source_generation_id, r.asset_id) for r in slot.reference_recommendations]
        if len(pairs) != len(set(pairs)) or not set(pairs) <= snapshot.source.allowed_refs:
            raise ValueError("Plan Next references must be unique available source pairs")
    current_subject_ids = [subject.id for subject in snapshot.source.sequence_subjects]
    if authority.allowed_subject_ids and authority.allowed_subject_ids != current_subject_ids:
        raise StaleProjectSnapshotError("Plan Next canonical cast changed; plan again")
    validate_participating_subjects(proposal.slots, set(authority.allowed_subject_ids or current_subject_ids), label="Plan Next")
    return next_memory_states(snapshot, proposal)


def next_memory_states(snapshot, proposal, *, allocate=False):
    """Sequential snapshots using one operation-wide creation pool and ID remap."""
    authority = proposal.authority
    return _operation_memory_states(
        snapshot.context["storyMemory"], authority.new_fact_ids, authority.new_thread_ids, proposal.slots,
        {subject.id for subject in snapshot.source.sequence_subjects}, allocate=allocate,
    )


async def plan_next(store, project_id, request, client_factory=OpenAICompatibleClient, *, item_id_factory=None):
    snapshot = store.read_story_operation_snapshot(project_id, request.anchor_generation_id, request.instruction)
    issue = item_id_factory or (lambda: str(uuid4()))
    authority = StoryOperationAuthority(
        baseProjectId=project_id, anchorGenerationId=request.anchor_generation_id,
        anchorOrdinal=snapshot.source_sequence_length, operationFingerprint=snapshot.fingerprint,
        instruction=request.instruction, shotGuidance=request.shot_guidance,
        slotIds=[str(issue()) for _ in range(request.requested_count)],
        allowedSubjectIds=[subject.id for subject in snapshot.source.sequence_subjects],
        newFactIds=[str(issue()) for _ in range(STORY_MEMORY_NEW_SLOTS)],
        newThreadIds=[str(issue()) for _ in range(STORY_MEMORY_NEW_SLOTS)],
    )
    response_model = story_participant_response_model(StoryNextResponse, authority.allowed_subject_ids)
    messages = assemble_next_prompt(snapshot, authority, request.llm, response_model)
    result = await _call(messages, request.llm, client_factory, response_model=response_model,
        response_preprocessor=lambda data: sanitize_next_response_data(data, snapshot.source.allowed_refs),
        memory_generation_start=authority.anchor_ordinal + 1)
    result = result.model_copy(update={
        "slots": sanitize_reference_recommendations(result.slots, snapshot.source.allowed_refs),
    })
    validate_exact_shots(result.slots, request.shot_guidance, authority.anchor_ordinal + 1)
    proposal = StoryNextProposal(**result.model_dump(), authority=authority, authorityToken=authority_token(authority))
    try:
        validate_next_proposal(snapshot, proposal, project_id)
    except ValidationError as exc:
        raise LLMBackendError("Plan Next memory patch exceeds continuity limits", category="story_schema") from exc
    except ValueError as exc:
        raise LLMBackendError(f"Plan Next response rejected: {exc}", category="story_authority") from exc
    try:
        current = store.read_story_operation_snapshot(project_id, request.anchor_generation_id, request.instruction)
    except ValueError as exc:
        raise StaleProjectSnapshotError("Plan Next sequence or memory authority changed while planning; plan again") from exc
    validate_next_proposal(current, proposal, project_id)
    return proposal


def replan_memory_states(snapshot, proposal, *, allocate=False):
    """Apply C..D memory deltas in order from U's owned snapshot (or empty G1)."""
    authority = proposal.authority
    return _operation_memory_states(
        snapshot.context["storyMemory"], authority.new_fact_ids, authority.new_thread_ids, proposal.items,
        {subject.id for subject in snapshot.source.sequence_subjects}, allocate=allocate,
    )


def validate_replan_proposal(snapshot, proposal, project_id):
    """Verify signed range identity, boundary adjacency, references and memory chain."""
    authority = proposal.authority
    if not hmac.compare_digest(authority_token(authority), proposal.authority_token):
        raise ValueError("Replan Range authority is invalid or the server restarted; plan again")
    if authority.base_project_id != project_id:
        raise StaleProjectSnapshotError("Replan Range Project identity changed; plan again")
    context = snapshot.context
    if (context["targetGenerationIds"] != authority.target_generation_ids
            or context["startOrdinal"] != authority.start_ordinal
            or context["endOrdinal"] != authority.end_ordinal
            or context["upstreamGenerationId"] != authority.upstream_generation_id
            or context["downstreamGenerationId"] != authority.downstream_generation_id):
        raise StaleProjectSnapshotError("Replan Range boundaries are no longer contiguous; plan again",
                                        category="sequence_structure_changed")
    if authority.memory_before_range.model_dump(mode="json") != StoryContinuityMemory.model_validate(
            context["storyMemory"]).model_dump(mode="json"):
        raise StaleProjectSnapshotError("Replan Range memory authority changed; plan again",
                                        category="memory_authority_changed")
    # project_fingerprint remains signed provenance. The bounded fingerprint is
    # the authority for this operation: a timestamp or unrelated execution edit
    # must not invalidate minutes of provider work.
    if authority.operation_fingerprint != snapshot.fingerprint:
        raise StaleProjectSnapshotError("Replan Range context changed; relevant Story authority differs; plan again",
                                        category="story_authority_changed")
    if [item.existing_generation_id for item in proposal.items] != authority.target_generation_ids:
        raise ValueError("Replan Range response must cover every target Generation once in canonical order")
    if authority.start_ordinal == 1:
        if proposal.items[0].relationship_to_previous is not None:
            raise ValueError("Generation 1 Replan relationship must be null")
    elif proposal.items[0].relationship_to_previous is None:
        raise ValueError("First Replan target requires a relationship to protected upstream Generation")
    if any(item.relationship_to_previous is None for item in proposal.items[1:]):
        raise ValueError("Later Replan targets require a relationship to their predecessor")
    allowed = {(pair.source_generation_id, pair.asset_id) for pair in authority.allowed_reference_pairs}
    if not allowed <= snapshot.source.allowed_refs:
        raise StaleProjectSnapshotError("Replan Range reference authority changed; plan again")
    for item in proposal.items:
        pairs = [(reference.source_generation_id, reference.asset_id) for reference in item.reference_recommendations]
        if len(pairs) != len(set(pairs)) or not set(pairs) <= allowed:
            raise ValueError("Replan Range references must be unique allowed source pairs")
    return replan_memory_states(snapshot, proposal)


async def plan_replan(store, project_id, request, client_factory=OpenAICompatibleClient, *, item_id_factory=None):
    """Issue a signed rewrite proposal for existing C..D without touching the Project."""
    snapshot = store.read_story_replan_snapshot(project_id, request.start_generation_id, request.end_generation_id,
                                                request.instruction)
    issue = item_id_factory or (lambda: str(uuid4()))
    authority = StoryReplanAuthority(
        baseProjectId=project_id, projectFingerprint=snapshot.source.fingerprint,
        operationFingerprint=snapshot.fingerprint, targetGenerationIds=snapshot.context["targetGenerationIds"],
        startOrdinal=snapshot.context["startOrdinal"], endOrdinal=snapshot.context["endOrdinal"],
        upstreamGenerationId=snapshot.context["upstreamGenerationId"],
        downstreamGenerationId=snapshot.context["downstreamGenerationId"], instruction=request.instruction,
        shotGuidance=request.shot_guidance,
        memoryBeforeRange=StoryContinuityMemory.model_validate(snapshot.context["storyMemory"]),
        newFactIds=[str(issue()) for _ in range(STORY_MEMORY_NEW_SLOTS)],
        newThreadIds=[str(issue()) for _ in range(STORY_MEMORY_NEW_SLOTS)],
        allowedReferencePairs=[StoryReferencePair(sourceGenerationId=source_id, assetId=asset_id)
                               for source_id, asset_id in sorted(snapshot.source.allowed_refs)],
    )
    result = await _call(assemble_replan_prompt(snapshot, authority, request.llm), request.llm, client_factory,
                         response_model=StoryReplanResponse,
                         response_preprocessor=lambda data: sanitize_story_operation_response_data(data, snapshot.source.allowed_refs),
                         memory_generation_start=authority.start_ordinal)
    result = result.model_copy(update={
        "items": sanitize_reference_recommendations(result.items, snapshot.source.allowed_refs),
    })
    validate_exact_shots(result.items, request.shot_guidance, authority.start_ordinal)
    proposal = StoryReplanProposal(**result.model_dump(), authority=authority, authorityToken=authority_token(authority))
    try:
        validate_replan_proposal(snapshot, proposal, project_id)
    except ValidationError as exc:
        raise LLMBackendError("Replan Range memory patch exceeds continuity limits", category="story_schema") from exc
    except ValueError as exc:
        raise LLMBackendError(f"Replan Range response rejected: {exc}", category="story_authority") from exc
    try:
        current = store.read_story_replan_snapshot(project_id, request.start_generation_id, request.end_generation_id,
                                                    request.instruction)
    except ValueError as exc:
        raise StaleProjectSnapshotError("Replan Range sequence or memory authority changed while planning; plan again",
                                        category="story_authority_changed") from exc
    try:
        validate_replan_proposal(current, proposal, project_id)
    except StaleProjectSnapshotError as exc:
        raise StaleProjectSnapshotError(f"Replan Range context changed while the provider was running: {exc}",
                                        category=exc.category) from exc
    return proposal


def validate_insert_proposal(snapshot, proposal, project_id):
    """Validate signed A/B authority, slot order, references, and memory chain."""
    authority = proposal.authority
    if not hmac.compare_digest(authority_token(authority), proposal.authority_token):
        raise ValueError("Insert Between authority is invalid or the server restarted; plan again")
    if (snapshot.context["upstreamGenerationId"] != authority.upstream_generation_id
            or snapshot.context["downstreamGenerationId"] != authority.downstream_generation_id):
        raise StaleProjectSnapshotError("Insert Between boundary adjacency changed; plan again")
    if authority.base_project_id != project_id or authority.operation_fingerprint != snapshot.fingerprint:
        raise StaleProjectSnapshotError("Insert Between boundary context changed; Story authority or sequence structure differs; plan again")
    if [slot.plan_item_id for slot in proposal.slots] != authority.slot_ids:
        raise ValueError("Insert Between slot count, identity or order changed")
    for slot in proposal.slots:
        pairs = [(reference.source_generation_id, reference.asset_id) for reference in slot.reference_recommendations]
        if len(pairs) != len(set(pairs)) or not set(pairs) <= snapshot.source.allowed_refs:
            raise ValueError("Insert Between references must be unique available source pairs")
    current_subject_ids = [subject.id for subject in snapshot.source.sequence_subjects]
    if authority.allowed_subject_ids and authority.allowed_subject_ids != current_subject_ids:
        raise StaleProjectSnapshotError("Insert Between canonical cast changed; plan again")
    validate_participating_subjects(proposal.slots, set(authority.allowed_subject_ids or current_subject_ids), label="Insert Between")
    return next_memory_states(snapshot, proposal)


async def plan_insert(store, project_id, request, client_factory=OpenAICompatibleClient, *, item_id_factory=None):
    """Issue a signed, bounded Insert Between proposal without writing a Project."""
    snapshot = store.read_story_insert_snapshot(project_id, request.upstream_generation_id, request.instruction)
    issue = item_id_factory or (lambda: str(uuid4()))
    authority = StoryInsertAuthority(
        baseProjectId=project_id, upstreamGenerationId=request.upstream_generation_id,
        downstreamGenerationId=snapshot.context["downstreamGenerationId"],
        upstreamOrdinal=snapshot.context["upstreamOrdinal"], downstreamOrdinal=snapshot.context["downstreamOrdinal"],
        operationFingerprint=snapshot.fingerprint, instruction=request.instruction,
        shotGuidance=request.shot_guidance,
        slotIds=[str(issue()) for _ in range(request.requested_count)],
        allowedSubjectIds=[subject.id for subject in snapshot.source.sequence_subjects],
        newFactIds=[str(issue()) for _ in range(STORY_MEMORY_NEW_SLOTS)],
        newThreadIds=[str(issue()) for _ in range(STORY_MEMORY_NEW_SLOTS)],
    )
    response_model = story_participant_response_model(StoryInsertResponse, authority.allowed_subject_ids)
    result = await _call(assemble_insert_prompt(snapshot, authority, request.llm, response_model), request.llm, client_factory,
                         response_model=response_model,
                         response_preprocessor=lambda data: sanitize_next_response_data(data, snapshot.source.allowed_refs),
                         memory_generation_start=authority.upstream_ordinal + 1)
    result = result.model_copy(update={
        "slots": sanitize_reference_recommendations(result.slots, snapshot.source.allowed_refs),
    })
    validate_exact_shots(result.slots, request.shot_guidance, authority.upstream_ordinal + 1)
    proposal = StoryInsertProposal(**result.model_dump(), authority=authority, authorityToken=authority_token(authority))
    try:
        validate_insert_proposal(snapshot, proposal, project_id)
    except ValidationError as exc:
        raise LLMBackendError("Insert Between memory patch exceeds continuity limits", category="story_schema") from exc
    except ValueError as exc:
        raise LLMBackendError(f"Insert Between response rejected: {exc}", category="story_authority") from exc
    # Re-read immediately so an asynchronous caller never receives a proposal
    # that was made stale while its model request was in flight.
    try:
        current = store.read_story_insert_snapshot(project_id, request.upstream_generation_id, request.instruction)
    except ValueError as exc:
        raise StaleProjectSnapshotError("Insert Between sequence or memory authority changed while planning; plan again") from exc
    validate_insert_proposal(current, proposal, project_id)
    return proposal
