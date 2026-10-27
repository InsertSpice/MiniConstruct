"""Transient, saved-Project H3 authority; never a second persistence model."""
from pydantic import BaseModel, ConfigDict, Field


class H3ContinuityContext(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    sequence_context: str = Field(default="", alias="sequenceContext")
    incoming_state: str = Field(default="", alias="incomingState")
    intended_outgoing_state: str = Field(default="", alias="intendedOutgoingState")
    relationship_to_previous: str | None = Field(default=None, alias="relationshipToPrevious")
    camera_intent: str = Field(default="", alias="cameraIntent")
    predecessor_generation_id: str | None = Field(default=None, alias="predecessorGenerationId")
    recent_summary: str = Field(default="", alias="recentSummary", max_length=1500)
    durable_facts: list[str] = Field(default_factory=list, alias="durableFacts", max_length=8)
    predecessor_intended_outgoing_state: str = Field(default="", alias="predecessorIntendedOutgoingState", max_length=20000)
    predecessor_observation: dict | None = Field(default=None, alias="predecessorObservation")
    # Warnings are not authoring facts and do not fingerprint omitted memory.
    warnings: list[str] = Field(default_factory=list, exclude=True)

