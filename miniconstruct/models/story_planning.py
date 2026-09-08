"""Ephemeral Story Planner contracts; none of these are Project manifest fields."""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
    creative_request: str = Field(alias="creativeRequest", max_length=50000)
    incoming_state: str = Field(alias="incomingState", max_length=20000)
    intended_outgoing_state: str = Field(alias="intendedOutgoingState", max_length=20000)
    relationship_to_previous: RelationshipToPrevious | None = Field(alias="relationshipToPrevious")
    camera_intent: str = Field(alias="cameraIntent", max_length=20000)
    reference_recommendations: list[StoryReferenceRecommendation] = Field(
        default_factory=list, alias="referenceRecommendations", max_length=256,
    )

    @field_validator("plan_item_id")
    @classmethod
    def uuid_identity(cls, value: str) -> str:
        return str(UUID(value))


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


class StoryRevisionRequest(StoryModel):
    draft: StoryPlanProposal
    selected_plan_item_ids: list[str] = Field(alias="selectedPlanItemIds", min_length=1, max_length=12)
    scopes: list[PlanningRevisionScope] = Field(min_length=1, max_length=5)
    instruction: str = Field(min_length=1, max_length=8000)
    llm: LLMSettings

    @model_validator(mode="after")
    def validate_selection(self):
        ids = self.selected_plan_item_ids
        if len(ids) != len(set(ids)) or not set(ids) <= {i.plan_item_id for i in self.draft.items}:
            raise ValueError("Select unique existing planItemIds")
        if len(set(self.scopes)) != len(self.scopes) or not self.instruction.strip():
            raise ValueError("Select unique scopes and enter a non-empty instruction")
        return self


class StoryApplyRequest(StoryModel):
    draft: StoryPlanProposal
