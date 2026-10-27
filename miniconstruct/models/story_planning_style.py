"""Project-owned Story Planner latitude and Scene Plan articulation controls."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StoryPlanningStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    creativity: Literal["faithful", "balanced", "exploratory"] = "balanced"
    scene_plan_detail: Literal["concise", "standard", "detailed"] = Field(
        default="standard", alias="scenePlanDetail",
    )
