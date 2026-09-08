"""Optional multi-Generation planning: read, propose, validate; never persist."""
from __future__ import annotations

import json
import re
from uuid import uuid4

from pydantic import ValidationError

from miniconstruct.llm.client import LLMBackendError, OpenAICompatibleClient
from miniconstruct.models.story_planning import (
    StoryBuildRequest, StoryBuildResponse, StoryConsistencyWarning, StoryPlanItem, StoryPlanProposal, StoryRevisionRequest,
    StoryReferenceRecommendation,
)
from miniconstruct.planning import (
    PLANNER_MEDIA_POLICY, RELATIONSHIP_SEMANTICS, _image_parts, _workspace_context,
)
from miniconstruct.project_store import ProjectStore, StoryProjectSnapshot


STORY_SYSTEM = """You are MiniConstruct's optional Story Planner. Plan authoring fields,
not a finished H3 prompt. Return exactly one JSON object matching the supplied JSON
schema, without Markdown or commentary. Preserve the supplied base snapshot fields,
planItemIds, existingGenerationIds, item order and exact item count.
Each item is one coherent, feasible H3 shot/segment. Distribute the high-level idea
across the Sequence, respecting duration, mode, Subjects, controls and asset metadata.
Distinguish semantic continuity from exact composition/frame continuity. Use deliberate
camera variety for new shots when useful. Adjacent Incoming/Outgoing states should
make narrative sense, but need not be identical strings. Never blindly copy endpoints.
Generation 1 relationship is null; all later relationships use the closed enum.
References are advisory: only recommend committed sourceGenerationId + assetId pairs
present in sourceGenerations. Proposed new items own no assets. Never invent, attach,
copy assets, invent Subjects or change Workspace settings/modes.
Use consistencyWarnings for transitions that may need manual reconciliation. Warnings
reference planItemId and relatedPlanItemId, direction upstream/downstream, and message.
""" + PLANNER_MEDIA_POLICY + RELATIONSHIP_SEMANTICS

BUILD_AUTHORITY = """BUILD field ownership: use the high-level idea to complete the plan.
The sparse draft is read-only source authority, not a response template. Missing text
fields are unfilled planning work, not instructions to stay blank: you MUST fill each
with a useful proposal, including on Generation 1. Author creativeRequest, incomingState,
intendedOutgoingState and cameraIntent for every item. Do not invent fallback filler.
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

REVISION_AUTHORITY = """REVISION authority: the full CURRENT draft includes manual edits.
Change only selected fields of selected planItemIds according to the instruction.
All unselected items and unselected fields must remain exactly unchanged. Sequence
Context, identities, base snapshot and order are read-only. You may recompute valid
referenceRecommendations only for selected items. Never rewrite an unselected neighbor;
warn instead. main_prompt scope corresponds to creativeRequest.
"""

SCOPE_FIELDS = {
    "main_prompt": "creative_request", "incoming_state": "incoming_state",
    "intended_outgoing_state": "intended_outgoing_state",
    "relationship_to_previous": "relationship_to_previous", "camera_intent": "camera_intent",
}

_JSON_FENCE = re.compile(r"\A```(?P<language>json)?\r?\n(?P<body>[\s\S]*?)\r?\n```\Z")


def normalize_story_model_json(raw: str) -> dict:
    """Accept raw JSON or one complete JSON Markdown fence, with no prose."""
    if not isinstance(raw, str):
        raise ValueError("structured Story Planner response must be text")
    candidate = raw.strip()
    fenced = _JSON_FENCE.fullmatch(candidate)
    if fenced:
        candidate = fenced.group("body")
    elif "```" in candidate:
        raise ValueError("Story Planner response must be raw JSON or one complete JSON fence")
    try:
        value = json.loads(candidate)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("Story Planner response is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("Story Planner response must be a JSON object")
    return value


def sanitize_recommendations(proposal: StoryPlanProposal, allowed_refs: set[tuple[str, str]]) -> StoryPlanProposal:
    """Drop invalid advisory pairs and keep the first valid qualified pair."""
    items = []
    for item in proposal.items:
        seen: set[tuple[str, str]] = set()
        recommendations = []
        for recommendation in item.reference_recommendations:
            pair = (recommendation.source_generation_id, recommendation.asset_id)
            if pair in allowed_refs and pair not in seen:
                seen.add(pair)
                recommendations.append(recommendation)
        items.append(item.model_copy(update={"reference_recommendations": recommendations}))
    return proposal.model_copy(update={"items": items})


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
            creativeRequest=g.workspace.creative_request if g else "",
            incomingState=g.incoming_state if g else "",
            intendedOutgoingState=g.intended_outgoing_state if g else "",
            relationshipToPrevious=(g.planning.relationship_to_previous if g else None) or ("independent" if index else None),
            cameraIntent=g.planning.camera_intent if g else "",
        ))
    return StoryPlanProposal(
        baseProjectId=snapshot.manifest.project_id, baseProjectUpdatedAt=snapshot.manifest.updated_at,
        baseSequenceFingerprint=snapshot.fingerprint, sequenceContext=snapshot.sequence_context, items=items,
    )


def assemble_story_prompt(snapshot, draft, settings, *, idea=None, revision=None):
    sources = [{
        "sourceGenerationId": g.generation_id, "label": f"Generation {g.ordinal}",
        **_workspace_context(g.workspace, planning=g.planning, incoming=g.incoming_state, outgoing=g.intended_outgoing_state),
    } for g in snapshot.generations]
    context = {
        "draft": draft.model_dump(mode="json", by_alias=True),
        "sourceGenerations": sources,
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
            for field in ("creativeRequest", "incomingState", "intendedOutgoingState", "cameraIntent"):
                if not item[field].strip():
                    item.pop(field)
            if index and (index >= len(snapshot.generations)
                          or snapshot.generations[index].planning.relationship_to_previous is None):
                item.pop("relationshipToPrevious")
            item.pop("referenceRecommendations")
        context.update(idea=idea, desiredGenerationCount=len(draft.items),
                       responseSchema=StoryBuildResponse.model_json_schema(by_alias=True))
    else:
        context.update(instruction=revision.instruction, selectedPlanItemIds=revision.selected_plan_item_ids,
                       editableScopes=[s.value for s in revision.scopes])
    text = json.dumps(context, ensure_ascii=False, indent=2, default=str)
    parts = [{"type": "text", "text": text}]
    if settings.supports_vision is True:
        for g in snapshot.generations:
            parts.extend(_image_parts(g.workspace, f"sourceGenerationId {g.generation_id} (Generation {g.ordinal})"))
    system = STORY_SYSTEM
    if revision is None:
        system = system.replace(
            "References are advisory: only recommend committed sourceGenerationId + assetId pairs\n"
            "present in sourceGenerations. Proposed new items own no assets. Never invent, attach,\n"
            "copy assets, invent Subjects or change Workspace settings/modes.",
            "Only recommend committed sourceGenerationId + assetId pairs present in sourceGenerations.\n"
            "On new slots, Apply will attach each recommended reference and its required saved Subjects.\n"
            "New slots inherit the immediately preceding Generation's mode. Existing-slot references\n"
            "remain advisory. Select only useful references for each shot; never invent asset or Subject\n"
            "metadata or change Workspace settings/modes yourself.",
        )
        system = system.replace(
            "Preserve the supplied base snapshot fields,\nplanItemIds, existingGenerationIds, item order and exact item count.",
            "Use the supplied slot IDs, item order and exact item count.",
        )
    return [
        {"role": "system", "content": system + (BUILD_AUTHORITY if revision is None else REVISION_AUTHORITY)},
        {"role": "user", "content": parts if settings.supports_vision is True else text},
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
            "creative_request": g.workspace.creative_request,
            "incoming_state": g.incoming_state, "intended_outgoing_state": g.intended_outgoing_state,
            "relationship_to_previous": g.planning.relationship_to_previous,
            "camera_intent": g.planning.camera_intent,
        }
        for field, value in authoritative.items():
            if value is not None and str(value).strip() and getattr(item, field) != value:
                raise ValueError(f"Model changed authoritative {field} in Generation {g.ordinal}")


def project_revision(request, result):
    """Project only selected authoring fields onto the authoritative draft."""
    before = request.draft
    validate_identity(before, result)
    selected_items = set(request.selected_plan_item_ids)
    selected_fields = {SCOPE_FIELDS[scope.value] for scope in request.scopes}
    projected = before.model_copy(deep=True)
    result_by_id = {item.plan_item_id: item for item in result.items}
    for item in projected.items:
        if item.plan_item_id not in selected_items:
            continue
        model_item = result_by_id[item.plan_item_id]
        for field in selected_fields:
            setattr(item, field, getattr(model_item, field))
        # Recommendations are advisory and may be recomputed for selected items.
        item.reference_recommendations = model_item.reference_recommendations
    # Sequence Context is request-draft authority, even if the model paraphrased it.
    return projected


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
                                            or old.creative_request != new.creative_request):
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


def _structured_error(exc):
    # Pydantic's str(exc) includes arbitrary model input, potentially image/key echoes.
    known = {key for model in (StoryBuildResponse, StoryPlanProposal, StoryPlanItem,
                              StoryConsistencyWarning, StoryReferenceRecommendation)
             for name, f in model.model_fields.items() for key in (name, f.alias or name)}
    safe_checks = {
        "Value error, Story draft identities must be unique",
        "Value error, Generation 1 relationship must be null; later relationships must be specified",
        "Value error, warning references an unknown planItemId",
    }
    errors = exc.errors(include_input=False, include_context=False, include_url=False)
    summaries = []
    for error in errors[:5]:
        path = ".".join(str(p) if isinstance(p, int) or p in known else "field" for p in error["loc"]) or "response"
        detail = error["msg"] if error["msg"] in safe_checks else error["type"]
        summaries.append(f"{path}: {detail}")
    return f"{len(errors)} validation error(s): " + "; ".join(summaries)


async def _call(messages, settings, client_factory, *, build_initial=None):
    try:
        async with client_factory(settings) as client:
            raw = await client.generate(messages, response_instruction=None)
    except LLMBackendError as exc:
        # Provider bodies can echo a request. Story reports only bounded safe metadata.
        raise LLMBackendError(exc.safe_message, exc.status_code, exc.upstream_status,
                              category=exc.category, safe_message=exc.safe_message) from exc
    try:
        data = normalize_story_model_json(raw)
    except (ValueError, TypeError) as exc:
        raise LLMBackendError(f"Story Planner returned invalid structured JSON: {exc}",
                              category="story_json") from exc
    try:
        if build_initial is None:
            return StoryPlanProposal.model_validate(data)
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
            for item in data["items"]:
                item.pop("existingGenerationId")
        return StoryBuildResponse.model_validate(data)
    except ValidationError as exc:
        raise LLMBackendError(f"Story Planner returned invalid structured JSON: {_structured_error(exc)}",
                              category="story_schema") from exc


TEXT_FIELDS = ("creative_request", "incoming_state", "intended_outgoing_state", "camera_intent")


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
    labels = dict(zip(TEXT_FIELDS, ("Creative Request", "Incoming State", "Intended Outgoing State", "Camera Intent")))
    warnings = []
    for item in result.items:
        missing = [labels[field] for field in TEXT_FIELDS if not getattr(item, field).strip()]
        if item is result.items[0] and not result.sequence_context.strip():
            missing.append("Sequence Context")
        if missing:
            warnings.append(StoryConsistencyWarning(
                planItemId=item.plan_item_id, relatedPlanItemId=item.plan_item_id, direction="downstream",
                message="Build left these planning fields incomplete: " + ", ".join(missing) + ". Edit the draft before Apply.",
            ))
    # Reserve room for deterministic warnings within the existing public limit.
    result.consistency_warnings = result.consistency_warnings[:256 - len(warnings)] + warnings
    return result


async def build_story_plan(store: ProjectStore, project_id: str, request: StoryBuildRequest,
                           client_factory=OpenAICompatibleClient, *, item_id_factory=None):
    snapshot = store.read_story_snapshot(project_id)
    initial = initial_story_draft(snapshot, request.desired_generation_count, item_id_factory)
    proposals = await _call(assemble_story_prompt(snapshot, initial, request.llm, idea=request.idea),
                            request.llm, client_factory, build_initial=initial)
    # Validate the trusted request snapshot, independently of anything the model echoes.
    store.read_story_snapshot(project_id).validate_draft(initial)
    result = project_build(snapshot, initial, proposals)
    result = sanitize_recommendations(result, snapshot.allowed_refs)
    result = add_incomplete_build_warnings(result)
    store.read_story_snapshot(project_id).validate_draft(result)
    return result


async def revise_story_plan(store: ProjectStore, project_id: str, request: StoryRevisionRequest,
                            client_factory=OpenAICompatibleClient):
    snapshot = store.read_story_snapshot(project_id)
    snapshot.validate_draft(request.draft)  # Must run before any model call.
    result = await _call(assemble_story_prompt(snapshot, request.draft, request.llm, revision=request), request.llm, client_factory)
    result = sanitize_recommendations(result, snapshot.allowed_refs)
    try:
        result.validate_sources([g.generation_id for g in snapshot.generations])
        result = project_revision(request, result)
        result = add_boundary_warnings(request, result)
    except ValueError as exc:
        raise LLMBackendError(f"Story Planner revision rejected: {exc}", 502) from exc
    store.read_story_snapshot(project_id).validate_draft(result)
    return result
