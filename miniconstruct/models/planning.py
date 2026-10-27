from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field
from .visual_handoff import VisualHandoffPolicy


class RelationshipToPrevious(StrEnum):
    CONTINUE_PREVIOUS_SHOT = "continue_previous_shot"
    NEW_SHOT_PRESERVE_CONTINUITY = "new_shot_preserve_continuity"
    MATCH_PREVIOUS_ENDING_FRAME = "match_previous_ending_frame"
    INDEPENDENT = "independent"


class RecommendationSource(StrEnum):
    PREVIOUS_GENERATION = "previous_generation"
    CURRENT_GENERATION = "current_generation"


class GenerationPlanning(BaseModel):
    """Per-Generation planning metadata, intentionally outside Workspace."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    scene_plan: str = Field(default="", alias="scenePlan", max_length=50000)

    relationship_to_previous: RelationshipToPrevious | None = Field(
        default=None, alias="relationshipToPrevious"
    )
    camera_intent: str = Field(default="", alias="cameraIntent", max_length=20000)
    shot_count: int | None = Field(default=None, alias="shotCount", strict=True, gt=0)
    visual_handoff: VisualHandoffPolicy = Field(default_factory=VisualHandoffPolicy, alias="visualHandoff")


class ReferenceRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    source: RecommendationSource
    asset_id: str = Field(alias="assetId", min_length=1, max_length=128)
    recommendation: str = Field(min_length=1, max_length=4000)
