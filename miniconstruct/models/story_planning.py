"""Ephemeral Story Planner contracts; none of these are Project manifest fields."""
from __future__ import annotations

from copy import deepcopy
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, create_model, field_validator, model_validator
from pydantic_core import PydanticUndefined

from miniconstruct.models.api import LLMSettings, PlanningRevisionScope
from miniconstruct.models.planning import RelationshipToPrevious
class StoryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class StoryReferenceRecommendation(StoryModel):
    source_generation_id: str = Field(alias="sourceGenerationId")
    asset_id: str = Field(alias="assetId", min_length=1, max_length=128)
    recommendation: str = Field(min_length=1, max_length=4000)

    @field_validator("source_generation_id")
    @classmethod
    def uuid_source(cls, value: str) -> str:
        return str(UUID(value))


class StoryBuildItem(StoryModel):
    """Model-authored proposals, addressed by a backend-issued slot ID."""
    plan_item_id: str = Field(alias="planItemId")
    scene_plan: str = Field(alias="scenePlan", max_length=50000)
    duration_seconds: int | None = Field(default=None, alias="durationSeconds", strict=True, ge=4, le=15)

    @field_validator("duration_seconds", mode="before")
    @classmethod
    def invalid_duration_uses_g1_fallback(cls, value):
        return value if type(value) is int and 4 <= value <= 15 else None
    # This is deliberately the same plain, exact text contract as Workspace.dialogue.
    # A default keeps older recorded planner replies compatible and represents silence.
    dialogue: str = Field(default="", max_length=30000)
    incoming_state: str = Field(alias="incomingState", max_length=20000)
    intended_outgoing_state: str = Field(alias="intendedOutgoingState", max_length=20000)
    relationship_to_previous: RelationshipToPrevious | None = Field(alias="relationshipToPrevious")
    camera_intent: str = Field(alias="cameraIntent", max_length=20000)
    shot_count: int = Field(default=1, alias="shotCount", strict=True, gt=0)
    @model_validator(mode="before")
    @classmethod
    def ignore_legacy_tone_suggestion(cls, value):
        if isinstance(value, dict):
            return {key: item for key, item in value.items()
                    if key not in {"tonePerformance", "tone_performance"}}
        return value
    # This is deliberate shot/visual participation, not a name extraction hint.
    # Sequence Subject IDs are opaque and are checked against the operation's
    # signed cast authority before a proposal can be applied.
    participating_subject_ids: list[str] = Field(
        default_factory=list, alias="participatingSubjectIds", max_length=128,
    )
    reference_recommendations: list[StoryReferenceRecommendation] = Field(
        default_factory=list, alias="referenceRecommendations", max_length=256,
    )

    @field_validator("plan_item_id")
    @classmethod
    def uuid_identity(cls, value: str) -> str:
        return str(UUID(value))

    @field_validator("participating_subject_ids")
    @classmethod
    def unique_participating_subjects(cls, value: list[str]) -> list[str]:
        if any(not isinstance(subject_id, str) or not subject_id.strip() for subject_id in value):
            raise ValueError("participatingSubjectIds must contain non-empty canonical Subject IDs")
        if len(value) != len(set(value)):
            raise ValueError("participatingSubjectIds must not contain duplicates")
        return value


class StoryPlanItem(StoryBuildItem):
    existing_generation_id: str | None = Field(alias="existingGenerationId")

    @field_validator("existing_generation_id")
    @classmethod
    def uuid_existing(cls, value: str | None) -> str | None:
        return None if value is None else str(UUID(value))


class StoryConsistencyWarning(StoryModel):
    plan_item_id: str = Field(alias="planItemId")
    related_plan_item_id: str = Field(alias="relatedPlanItemId")
    direction: Literal["upstream", "downstream"]
    message: str = Field(min_length=1, max_length=4000)


class StoryBuildResponse(StoryModel):
    """Internal Build output; the public draft envelope is reconstructed locally."""
    sequence_context: str = Field(alias="sequenceContext", max_length=20000)
    items: list[StoryBuildItem] = Field(min_length=2, max_length=12)
    consistency_warnings: list[StoryConsistencyWarning] = Field(
        default_factory=list, alias="consistencyWarnings", max_length=256,
    )


class LegacyStoryBuildItem(StoryModel):
    """Compatibility-only /build item; Tone/Performance is not model-authorable here."""
    plan_item_id: str = Field(alias="planItemId")
    scene_plan: str = Field(alias="scenePlan", max_length=50000)
    duration_seconds: int | None = Field(default=None, alias="durationSeconds", ge=4, le=15)
    dialogue: str = Field(default="", max_length=30000)
    incoming_state: str = Field(alias="incomingState", max_length=20000)
    intended_outgoing_state: str = Field(alias="intendedOutgoingState", max_length=20000)
    relationship_to_previous: RelationshipToPrevious | None = Field(alias="relationshipToPrevious")
    camera_intent: str = Field(alias="cameraIntent", max_length=20000)
    shot_count: int = Field(alias="shotCount", strict=True, gt=0)
    reference_recommendations: list[StoryReferenceRecommendation] = Field(
        default_factory=list, alias="referenceRecommendations", max_length=256,
    )

    @model_validator(mode="before")
    @classmethod
    def accept_historical_creative_request(cls, value):
        if isinstance(value, dict) and "creativeRequest" in value and "scenePlan" not in value:
            return {**{key: item for key, item in value.items() if key != "creativeRequest"},
                    "scenePlan": value["creativeRequest"]}
        return value

    @field_validator("plan_item_id")
    @classmethod
    def uuid_identity(cls, value: str) -> str:
        return str(UUID(value))


class LegacyStoryBuildResponse(StoryModel):
    """Model-facing legacy Build response; no Creative Controls authority."""
    sequence_context: str = Field(alias="sequenceContext", max_length=20000)
    items: list[LegacyStoryBuildItem] = Field(min_length=2, max_length=12)
    consistency_warnings: list[StoryConsistencyWarning] = Field(
        default_factory=list, alias="consistencyWarnings", max_length=256,
    )


class StoryPlanProposal(StoryModel):
    base_project_id: str = Field(alias="baseProjectId")
    base_project_updated_at: str = Field(alias="baseProjectUpdatedAt", min_length=1, max_length=100)
    base_sequence_fingerprint: str = Field(alias="baseSequenceFingerprint", pattern=r"^[0-9a-f]{64}$")
    sequence_context: str = Field(alias="sequenceContext", max_length=20000)
    items: list[StoryPlanItem] = Field(min_length=2, max_length=12)
    consistency_warnings: list[StoryConsistencyWarning] = Field(
        default_factory=list, alias="consistencyWarnings", max_length=256,
    )

    @field_validator("base_project_id")
    @classmethod
    def uuid_project(cls, value: str) -> str:
        return str(UUID(value))

    @model_validator(mode="after")
    def validate_structure(self):
        ids = [item.plan_item_id for item in self.items]
        existing = [item.existing_generation_id for item in self.items if item.existing_generation_id]
        if len(set(ids)) != len(ids) or len(set(existing)) != len(existing):
            raise ValueError("Story draft identities must be unique")
        for index, item in enumerate(self.items):
            if (index == 0) != (item.relationship_to_previous is None):
                raise ValueError("Generation 1 relationship must be null; later relationships must be specified")
        for warning in self.consistency_warnings:
            if warning.plan_item_id not in ids or warning.related_plan_item_id not in ids:
                raise ValueError("warning references an unknown planItemId")
        return self

    def validate_sources(self, generation_ids: list[str]) -> None:
        expected = generation_ids + [None] * (len(self.items) - len(generation_ids))
        if [item.existing_generation_id for item in self.items] != expected:
            raise ValueError("Existing Generations must appear exactly once in canonical order, followed only by new items")


class StoryBuildRequest(StoryModel):
    idea: str = Field(min_length=1, max_length=20000)
    desired_generation_count: int = Field(alias="desiredGenerationCount", strict=True, ge=2, le=12)
    llm: LLMSettings

    @field_validator("idea")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Sequence Idea must contain non-whitespace content")
        return value


class StoryRevisionScope(StrEnum):
    """Story-only scope kept separate from generic single-Generation planning scopes."""
    SCENE_PLAN = "scene_plan"
    DIALOGUE = "dialogue"


class StoryRevisionRequest(StoryModel):
    draft: StoryPlanProposal
    selected_plan_item_ids: list[str] = Field(alias="selectedPlanItemIds", min_length=1, max_length=12)
    # Keep the Story-specific dialogue enum authoritative for the existing Story
    # response contract now that ordinary Planning Revision also has dialogue.
    scopes: list[StoryRevisionScope | PlanningRevisionScope] = Field(min_length=1, max_length=6)
    instruction: str = Field(min_length=1, max_length=8000)
    llm: LLMSettings

    @model_validator(mode="after")
    def validate_selection(self):
        ids = self.selected_plan_item_ids
        if len(ids) != len(set(ids)) or not set(ids) <= {i.plan_item_id for i in self.draft.items}:
            raise ValueError("Select unique existing planItemIds")
        valid_scopes = {
            "scene_plan", "incoming_state", "intended_outgoing_state",
            "relationship_to_previous", "camera_intent", StoryRevisionScope.DIALOGUE,
        }
        values = {scope.value for scope in self.scopes}
        if len(values) != len(self.scopes) or not values <= valid_scopes or not self.instruction.strip():
            raise ValueError("Select unique scopes and enter a non-empty instruction")
        return self


SCOPE_FIELDS = {
    "scene_plan": "scene_plan", "dialogue": "dialogue",
    "incoming_state": "incoming_state", "intended_outgoing_state": "intended_outgoing_state",
    "relationship_to_previous": "relationship_to_previous", "camera_intent": "camera_intent",
}


class StoryRevisionPatchIdentity(BaseModel):
    """Model transport uses only public aliases, with no extra writable fields."""
    model_config = ConfigDict(extra="forbid", populate_by_name=False)
    plan_item_id: str = Field(alias="planItemId")

    @field_validator("plan_item_id")
    @classmethod
    def uuid_identity(cls, value: str) -> str:
        return str(UUID(value))


def story_revision_response_model(scopes):
    """All selected fields are required, including blank strings and nullable relationship.

    Reuse the authoring field types/limits without importing unrelated fields or defaults
    into the response schema. References are not a selectable Story Revision scope.
    """
    selected = {SCOPE_FIELDS[scope.value] for scope in scopes}
    if "camera_intent" in selected:
        selected.add("shot_count")
    fields = {}
    for name, original in StoryBuildItem.model_fields.items():
        if name in selected:
            field = deepcopy(original)
            field.default = PydanticUndefined
            field.default_factory = None
            fields[name] = (original.annotation, field)
    patch = create_model("StoryRevisionPatch", __base__=StoryRevisionPatchIdentity, **fields)
    return create_model(
        "StoryRevisionResponse",
        __config__=ConfigDict(extra="forbid"),
        patches=(list[patch], Field(min_length=1, max_length=12)),
    )


class StoryApplyRequest(StoryModel):
    draft: StoryPlanProposal
