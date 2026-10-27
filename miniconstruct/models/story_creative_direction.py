"""Project-level soft creative guidance for Story planning operations."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class StoryCreativeDirection(BaseModel):
    """User-authored sequence guidance, deliberately separate from Story Memory."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    tone_mood: str = Field(default="", alias="toneMood", max_length=2000)
    performance_energy: str = Field(default="", alias="performanceEnergy", max_length=2000)
    pacing_rhythm: str = Field(default="", alias="pacingRhythm", max_length=2000)
    beat_duration_density: str = Field(default="", alias="beatDurationDensity", max_length=2000)
