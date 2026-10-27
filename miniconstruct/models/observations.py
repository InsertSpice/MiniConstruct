"""Bounded, hash-bound visual evidence, separate from immutable render history."""
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from miniconstruct.execution import StrictModel

Text = Annotated[str, Field(max_length=800)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ObservedSubject(StrictModel):
    subject_id: str = Field(alias="subjectId", min_length=1, max_length=128)
    visibility: Literal["visible", "partially_visible", "not_visible", "uncertain"]
    appearance: Text
    wardrobe: Text
    condition: Text
    position_pose: Text = Field(alias="positionPose")
    held_objects: Text = Field(alias="heldObjects")
    current_action: Text = Field(alias="currentAction")


class OutcomeAssessment(StrictModel):
    status: Literal["matches", "partial", "conflicts", "not_verifiable"]
    explanation: Text


class ObservedEndState(StrictModel):
    summary: Text
    environment: Text
    subject_states: list[ObservedSubject] = Field(alias="subjectStates", max_length=16)
    props_objects: list[Text] = Field(alias="propsObjects", max_length=12)
    action_momentum: Text = Field(alias="actionMomentum")
    camera_state: Text = Field(alias="cameraState")
    lighting_conditions: Text = Field(alias="lightingConditions")
    planned_outcome_assessment: OutcomeAssessment = Field(alias="plannedOutcomeAssessment")
    unresolved_visible_entities: list[Text] = Field(alias="unresolvedVisibleEntities", max_length=8)

    @model_validator(mode="after")
    def unique_subjects(self):
        ids = [s.subject_id for s in self.subject_states]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate observed Subject IDs")
        return self

    def validate_subjects(self, offered):
        if any(s.subject_id not in offered for s in self.subject_states):
            raise ValueError("Observation contains a Subject ID outside the offered candidate set")
        return self


class ObservationEvidence(StrictModel):
    extraction_contract_version: Literal[1] = Field(default=1, alias="extractionContractVersion")
    sampled_timestamps: list[Annotated[float, Field(ge=0, allow_inf_nan=False)]] = Field(alias="sampledTimestamps", min_length=1, max_length=4)
    endpoint_timestamp: float = Field(alias="endpointTimestamp", ge=0, allow_inf_nan=False)
    endpoint_sha256: Digest = Field(alias="endpointSha256")
    contact_sheet_sha256: Digest = Field(alias="contactSheetSha256")

    @model_validator(mode="after")
    def tail_window(self):
        times = self.sampled_timestamps
        if times != sorted(set(times)) or times[-1] != self.endpoint_timestamp:
            raise ValueError("Evidence must end at the final decoded timestamp")
        if times[0] < max(0, self.endpoint_timestamp - 2):
            raise ValueError("Evidence must stay within the final two seconds")
        return self


class TakeObservation(StrictModel):
    take_id: UUID = Field(alias="takeId")
    take_sha256: Digest = Field(alias="takeSha256")
    analyzed_at: str = Field(alias="analyzedAt", max_length=100)
    analysis_contract_version: Literal[1] = Field(default=1, alias="analysisContractVersion")
    analyzer_model: str = Field(alias="analyzerModel", max_length=500)
    offered_subject_ids: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(alias="offeredSubjectIds", max_length=16)
    subject_labels: dict[str, Annotated[str, Field(max_length=300)]] = Field(default_factory=dict, alias="subjectLabels", max_length=16)
    evidence: ObservationEvidence
    model_observed_state: ObservedEndState = Field(alias="modelObservedState")
    effective_observed_state: ObservedEndState = Field(alias="effectiveObservedState")
    status: Literal["model_observed", "user_edited"] = "model_observed"

    @model_validator(mode="after")
    def valid_states(self):
        if set(self.subject_labels) - set(self.offered_subject_ids):
            raise ValueError("Subject labels must belong to the offered candidate set")
        for state in (self.model_observed_state, self.effective_observed_state):
            state.validate_subjects(set(self.offered_subject_ids))
        if self.status == "model_observed" and self.model_observed_state != self.effective_observed_state:
            raise ValueError("Unedited observation must equal the model result")
        return self


def active_observation(generation):
    take = next((t for t in generation.takes if t.id == generation.selected_take_id), None)
    return next((o for o in generation.take_observations
                 if take and o.take_id == take.id and o.take_sha256 == take.sha256), None)


def observation_projection(generation):
    observation = active_observation(generation)
    if observation is None:
        return None
    return {"takeId": str(observation.take_id), "takeSha256": observation.take_sha256,
            "status": observation.status,
            "effectiveObservedState": observation.effective_observed_state.model_dump(mode="json", by_alias=True)}


def observation_subject_dependencies(observation: TakeObservation) -> set[str]:
    """Only observed/corrected state creates canonical Subject retention."""
    return {
        subject.subject_id
        for state in (observation.model_observed_state, observation.effective_observed_state)
        for subject in state.subject_states
    }
