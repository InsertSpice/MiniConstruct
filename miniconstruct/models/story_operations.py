"""Bounded Story operation transport. Envelope authority never comes from a model."""
from copy import deepcopy
from uuid import UUID

from pydantic import Field, create_model, field_validator, model_validator
from typing import Literal

from .api import LLMSettings
from .story_memory import STORY_MEMORY_NEW_SLOTS, StoryContinuityMemory, StoryMemoryPatch
from .story_planning import StoryModel, StoryBuildItem, StoryPlanItem, StoryReferenceRecommendation
from .planning import RelationshipToPrevious

STORY_NEXT_MAX_GENERATIONS = 4
STORY_INITIAL_RANGE_MIN_GENERATIONS = 2
# A larger initial decomposition is still one bounded model operation.  This
# limit is intentionally independent from the smaller Plan Next batch size.
STORY_INITIAL_RANGE_MAX_GENERATIONS = 12
STORY_LOCAL_CONTEXT_GENERATIONS = 3
STORY_REPLAN_MAX_GENERATIONS = 6


class StoryShotGuidance(StoryModel):
    """Ephemeral per-target Generation shot-count request."""

    mode: Literal["prefer", "exact"]
    count: int = Field(strict=True, gt=0)


class StoryNextRequest(StoryModel):
    anchor_generation_id: str = Field(alias="anchorGenerationId")
    requested_count: int = Field(alias="requestedCount", strict=True, ge=1, le=STORY_NEXT_MAX_GENERATIONS)
    instruction: str = Field(default="", max_length=8000)
    llm: LLMSettings
    shot_guidance: StoryShotGuidance | None = Field(default=None, alias="shotGuidance")


class StoryNextItem(StoryBuildItem):
    shot_count: int = Field(alias="shotCount", strict=True, gt=0)
    memory_patch_after: StoryMemoryPatch = Field(alias="memoryPatchAfter")


class StoryNextResponse(StoryModel):
    slots: list[StoryNextItem] = Field(min_length=1, max_length=STORY_NEXT_MAX_GENERATIONS)
    warnings: list[str] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def valid_slots(self):
        if any(slot.relationship_to_previous is None for slot in self.slots):
            raise ValueError("Every appended Generation requires a relationship")
        if any(len(w) > 4000 for w in self.warnings):
            raise ValueError("Warning is too long")
        return self


class StoryOperationAuthority(StoryModel):
    base_project_id: str = Field(alias="baseProjectId")
    anchor_generation_id: str = Field(alias="anchorGenerationId")
    anchor_ordinal: int = Field(alias="anchorOrdinal", ge=1)
    operation_fingerprint: str = Field(alias="operationFingerprint", pattern=r"^[0-9a-f]{64}$")
    instruction: str = Field(max_length=8000)
    shot_guidance: StoryShotGuidance | None = Field(default=None, alias="shotGuidance")
    slot_ids: list[str] = Field(alias="slotIds", min_length=1, max_length=STORY_NEXT_MAX_GENERATIONS)
    allowed_subject_ids: list[str] = Field(default_factory=list, alias="allowedSubjectIds", max_length=128)
    new_fact_ids: list[str] = Field(alias="newFactIds", min_length=STORY_MEMORY_NEW_SLOTS, max_length=STORY_MEMORY_NEW_SLOTS)
    new_thread_ids: list[str] = Field(alias="newThreadIds", min_length=STORY_MEMORY_NEW_SLOTS, max_length=STORY_MEMORY_NEW_SLOTS)


class StoryNextProposal(StoryNextResponse):
    authority: StoryOperationAuthority
    authority_token: str = Field(alias="authorityToken", pattern=r"^[0-9a-f]{64}$")


class StoryNextApplyRequest(StoryModel):
    draft: StoryNextProposal


class StoryInitialRangeRequest(StoryModel):
    """Create the first bounded Story range from the one saved Generation."""

    requested_total_count: int = Field(
        alias="requestedTotalCount", strict=True,
        ge=STORY_INITIAL_RANGE_MIN_GENERATIONS, le=STORY_INITIAL_RANGE_MAX_GENERATIONS,
    )
    llm: LLMSettings
    shot_guidance: StoryShotGuidance | None = Field(default=None, alias="shotGuidance")


class StoryInitialRangeItem(StoryModel):
    """Model-facing Initial Range authoring, position-bound by the server."""

    scene_plan: str = Field(alias="scenePlan", max_length=50000)
    duration_seconds: int | None = Field(default=None, alias="durationSeconds", strict=True, ge=4, le=15)

    @field_validator("duration_seconds", mode="before")
    @classmethod
    def invalid_duration_uses_g1_fallback(cls, value):
        return value if type(value) is int and 4 <= value <= 15 else None
    dialogue: str = Field(default="", max_length=30000)
    incoming_state: str = Field(alias="incomingState", max_length=20000)
    intended_outgoing_state: str = Field(alias="intendedOutgoingState", max_length=20000)
    relationship_to_previous: RelationshipToPrevious | None = Field(alias="relationshipToPrevious")
    camera_intent: str = Field(alias="cameraIntent", max_length=20000)
    shot_count: int = Field(alias="shotCount", strict=True, gt=0)
    @model_validator(mode="before")
    @classmethod
    def ignore_legacy_tone_suggestion(cls, value):
        if isinstance(value, dict):
            return {key: item for key, item in value.items()
                    if key not in {"tonePerformance", "tone_performance"}}
        return value
    participating_subject_ids: list[str] = Field(
        default_factory=list, alias="participatingSubjectIds", max_length=128,
    )
    reference_recommendations: list[StoryReferenceRecommendation] = Field(
        default_factory=list, alias="referenceRecommendations", max_length=256,
    )
    memory_patch_after: StoryMemoryPatch = Field(alias="memoryPatchAfter")

    @field_validator("participating_subject_ids")
    @classmethod
    def unique_participating_subjects(cls, value: list[str]) -> list[str]:
        if any(not isinstance(subject_id, str) or not subject_id.strip() for subject_id in value):
            raise ValueError("participatingSubjectIds must contain non-empty canonical Subject IDs")
        if len(value) != len(set(value)):
            raise ValueError("participatingSubjectIds must not contain duplicates")
        return value


class StoryInitialRangePlanItem(StoryPlanItem):
    """Public review item. Only the first item identifies existing saved G1."""

    memory_patch_after: StoryMemoryPatch = Field(alias="memoryPatchAfter")


class StoryInitialRangeResponse(StoryModel):
    """Strict model response; server reconstructs saved G1 identity and authority."""

    sequence_context: str = Field(alias="sequenceContext", max_length=20000)
    items: list[StoryInitialRangeItem] = Field(
        min_length=STORY_INITIAL_RANGE_MIN_GENERATIONS, max_length=STORY_INITIAL_RANGE_MAX_GENERATIONS,
    )
    warnings: list[str] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def valid_items(self):
        if self.items[0].relationship_to_previous is not None:
            raise ValueError("Generation 1 relationship must be null")
        if any(item.relationship_to_previous is None for item in self.items[1:]):
            raise ValueError("Later initial-range Generations require a relationship")
        if any(len(warning) > 4000 for warning in self.warnings):
            raise ValueError("Warning is too long")
        return self


class StoryInitialRangeAuthority(StoryModel):
    """Signed identity/capability envelope for Build Initial Range."""

    base_project_id: str = Field(alias="baseProjectId")
    project_fingerprint: str = Field(alias="projectFingerprint", pattern=r"^[0-9a-f]{64}$")
    g1_generation_id: str = Field(alias="g1GenerationId")
    source_generation_count: int = Field(alias="sourceGenerationCount", strict=True, ge=1, le=1)
    requested_total_count: int = Field(
        alias="requestedTotalCount", strict=True,
        ge=STORY_INITIAL_RANGE_MIN_GENERATIONS, le=STORY_INITIAL_RANGE_MAX_GENERATIONS,
    )
    shot_guidance: StoryShotGuidance | None = Field(default=None, alias="shotGuidance")
    slot_ids: list[str] = Field(
        alias="slotIds", min_length=STORY_INITIAL_RANGE_MIN_GENERATIONS,
        max_length=STORY_INITIAL_RANGE_MAX_GENERATIONS,
    )
    allowed_subject_ids: list[str] = Field(default_factory=list, alias="allowedSubjectIds", max_length=128)
    new_fact_ids: list[str] = Field(alias="newFactIds", min_length=STORY_MEMORY_NEW_SLOTS, max_length=STORY_MEMORY_NEW_SLOTS)
    new_thread_ids: list[str] = Field(alias="newThreadIds", min_length=STORY_MEMORY_NEW_SLOTS, max_length=STORY_MEMORY_NEW_SLOTS)
    allowed_reference_pairs: list["StoryReferencePair"] = Field(alias="allowedReferencePairs", max_length=4096)


class StoryInitialRangeProposal(StoryModel):
    """Ephemeral editable authoring output plus a signed non-editable envelope."""

    sequence_context: str = Field(alias="sequenceContext", max_length=20000)
    items: list[StoryInitialRangePlanItem] = Field(
        min_length=STORY_INITIAL_RANGE_MIN_GENERATIONS, max_length=STORY_INITIAL_RANGE_MAX_GENERATIONS,
    )
    warnings: list[str] = Field(default_factory=list, max_length=64)
    authority: StoryInitialRangeAuthority
    authority_token: str = Field(alias="authorityToken", pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def valid_items(self):
        ids = [item.plan_item_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("Initial range plan item identities must be unique")
        if self.items[0].existing_generation_id != self.authority.g1_generation_id:
            raise ValueError("Initial range must retain the signed Generation 1")
        if any(item.existing_generation_id is not None for item in self.items[1:]):
            raise ValueError("Only Generation 1 may be an existing initial-range Generation")
        if self.items[0].relationship_to_previous is not None:
            raise ValueError("Generation 1 relationship must be null")
        if any(item.relationship_to_previous is None for item in self.items[1:]):
            raise ValueError("Later initial-range Generations require a relationship")
        if any(len(warning) > 4000 for warning in self.warnings):
            raise ValueError("Warning is too long")
        return self


class StoryInitialRangeApplyRequest(StoryModel):
    draft: StoryInitialRangeProposal


class StoryInsertRequest(StoryModel):
    """Plan a small, new bridge after A and before its protected successor B."""

    upstream_generation_id: str = Field(alias="upstreamGenerationId")
    requested_count: int = Field(alias="requestedCount", strict=True, ge=1, le=STORY_NEXT_MAX_GENERATIONS)
    instruction: str = Field(default="", max_length=8000)
    llm: LLMSettings
    shot_guidance: StoryShotGuidance | None = Field(default=None, alias="shotGuidance")


class StoryInsertResponse(StoryModel):
    # Deliberately the same model-writable fields and per-slot memory contract as
    # Plan Next. A and B are carried only in the signed server envelope.
    slots: list[StoryNextItem] = Field(min_length=1, max_length=STORY_NEXT_MAX_GENERATIONS)
    warnings: list[str] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def valid_slots(self):
        if any(slot.relationship_to_previous is None for slot in self.slots):
            raise ValueError("Every inserted Generation requires a relationship")
        if any(len(warning) > 4000 for warning in self.warnings):
            raise ValueError("Warning is too long")
        return self


class StoryInsertAuthority(StoryModel):
    """Server-issued identities and exact protected insertion boundary."""

    base_project_id: str = Field(alias="baseProjectId")
    upstream_generation_id: str = Field(alias="upstreamGenerationId")
    downstream_generation_id: str = Field(alias="downstreamGenerationId")
    upstream_ordinal: int = Field(alias="upstreamOrdinal", ge=1)
    downstream_ordinal: int = Field(alias="downstreamOrdinal", ge=2)
    operation_fingerprint: str = Field(alias="operationFingerprint", pattern=r"^[0-9a-f]{64}$")
    instruction: str = Field(max_length=8000)
    shot_guidance: StoryShotGuidance | None = Field(default=None, alias="shotGuidance")
    slot_ids: list[str] = Field(alias="slotIds", min_length=1, max_length=STORY_NEXT_MAX_GENERATIONS)
    allowed_subject_ids: list[str] = Field(default_factory=list, alias="allowedSubjectIds", max_length=128)
    new_fact_ids: list[str] = Field(alias="newFactIds", min_length=STORY_MEMORY_NEW_SLOTS, max_length=STORY_MEMORY_NEW_SLOTS)
    new_thread_ids: list[str] = Field(alias="newThreadIds", min_length=STORY_MEMORY_NEW_SLOTS, max_length=STORY_MEMORY_NEW_SLOTS)


class StoryInsertProposal(StoryInsertResponse):
    authority: StoryInsertAuthority
    authority_token: str = Field(alias="authorityToken", pattern=r"^[0-9a-f]{64}$")


class StoryInsertApplyRequest(StoryModel):
    draft: StoryInsertProposal


class StoryReplanRequest(StoryModel):
    """Rewrite Story-owned planning for one bounded, existing Generation range."""

    start_generation_id: str = Field(alias="startGenerationId")
    end_generation_id: str = Field(alias="endGenerationId")
    instruction: str = Field(default="", max_length=8000)
    llm: LLMSettings
    shot_guidance: StoryShotGuidance | None = Field(default=None, alias="shotGuidance")


class StoryReplanItem(StoryModel):
    """A model patch addressed by the permanent Generation it is allowed to edit."""

    existing_generation_id: str = Field(alias="existingGenerationId")
    scene_plan: str = Field(alias="scenePlan", max_length=50000)
    dialogue: str = Field(alias="dialogue", max_length=30000)
    incoming_state: str = Field(alias="incomingState", max_length=20000)
    intended_outgoing_state: str = Field(alias="intendedOutgoingState", max_length=20000)
    relationship_to_previous: RelationshipToPrevious | None = Field(alias="relationshipToPrevious")
    camera_intent: str = Field(alias="cameraIntent", max_length=20000)
    shot_count: int = Field(alias="shotCount", strict=True, gt=0)
    @model_validator(mode="before")
    @classmethod
    def ignore_legacy_tone_suggestion(cls, value):
        if isinstance(value, dict):
            return {key: item for key, item in value.items()
                    if key not in {"tonePerformance", "tone_performance"}}
        return value
    reference_recommendations: list[StoryReferenceRecommendation] = Field(
        default_factory=list, alias="referenceRecommendations", max_length=256,
    )
    memory_patch_after: StoryMemoryPatch = Field(alias="memoryPatchAfter")

    @field_validator("existing_generation_id")
    @classmethod
    def valid_generation_id(cls, value):
        return str(UUID(value))


class StoryReplanResponse(StoryModel):
    items: list[StoryReplanItem] = Field(min_length=1, max_length=STORY_REPLAN_MAX_GENERATIONS)
    warnings: list[str] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def valid_warnings(self):
        if any(len(warning) > 4000 for warning in self.warnings):
            raise ValueError("Warning is too long")
        return self


class StoryReferencePair(StoryModel):
    """An explicitly visible source pair that a signed operation may materialize."""

    source_generation_id: str = Field(alias="sourceGenerationId")
    asset_id: str = Field(alias="assetId", min_length=1, max_length=128)

    @field_validator("source_generation_id")
    @classmethod
    def valid_generation_id(cls, value):
        return str(UUID(value))


class StoryReplanAuthority(StoryModel):
    """Backend-issued range identity, memory slots, and reference capability list."""

    base_project_id: str = Field(alias="baseProjectId")
    project_fingerprint: str = Field(alias="projectFingerprint", pattern=r"^[0-9a-f]{64}$")
    operation_fingerprint: str = Field(alias="operationFingerprint", pattern=r"^[0-9a-f]{64}$")
    target_generation_ids: list[str] = Field(alias="targetGenerationIds", min_length=1, max_length=STORY_REPLAN_MAX_GENERATIONS)
    start_ordinal: int = Field(alias="startOrdinal", ge=1)
    end_ordinal: int = Field(alias="endOrdinal", ge=1)
    upstream_generation_id: str | None = Field(default=None, alias="upstreamGenerationId")
    downstream_generation_id: str | None = Field(default=None, alias="downstreamGenerationId")
    instruction: str = Field(max_length=8000)
    shot_guidance: StoryShotGuidance | None = Field(default=None, alias="shotGuidance")
    memory_before_range: StoryContinuityMemory = Field(alias="memoryBeforeRange")
    new_fact_ids: list[str] = Field(alias="newFactIds", min_length=STORY_MEMORY_NEW_SLOTS, max_length=STORY_MEMORY_NEW_SLOTS)
    new_thread_ids: list[str] = Field(alias="newThreadIds", min_length=STORY_MEMORY_NEW_SLOTS, max_length=STORY_MEMORY_NEW_SLOTS)
    allowed_reference_pairs: list[StoryReferencePair] = Field(alias="allowedReferencePairs", max_length=4096)


class StoryReplanProposal(StoryReplanResponse):
    authority: StoryReplanAuthority
    authority_token: str = Field(alias="authorityToken", pattern=r"^[0-9a-f]{64}$")


class StoryReplanApplyRequest(StoryModel):
    draft: StoryReplanProposal


def story_participant_response_model(response_model, allowed_subject_ids):
    """Request-local response/parser contract from the operation's signed cast.

    Inherit item/response validators without changing public proposal models or
    mutating shared FieldInfo/schema objects. Empty casts retain the array field.
    """
    collection = "items" if response_model is StoryInitialRangeResponse else "slots"
    item_model = response_model.model_fields[collection].annotation.__args__[0]
    allowed = tuple(allowed_subject_ids)
    participant_type = list[Literal[allowed]] if allowed else list[str]
    item = create_model(
        item_model.__name__ + "CanonicalParticipants", __base__=item_model,
        participating_subject_ids=(participant_type, Field(
            default_factory=list, alias="participatingSubjectIds",
            max_length=128 if allowed else 0,
            description="Ordered unique exact opaque IDs from availableCanonicalSubjectIds; empty allowlist requires [].",
            json_schema_extra={"items": {"type": "string", "enum": list(allowed)}} if allowed else None,
        )),
    )
    field = deepcopy(response_model.model_fields[collection])
    return create_model(response_model.__name__ + "CanonicalParticipants", __base__=response_model,
                        **{collection: (list[item], field)})
