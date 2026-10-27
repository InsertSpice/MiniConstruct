from __future__ import annotations

import base64
from collections.abc import Callable
from datetime import datetime, timezone
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import subprocess
import time
import tempfile
import zipfile
import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator, model_validator

from miniconstruct.generation import ProductionGeneration, generate_workspace_prompts, resolve_narrative_request
from miniconstruct.dialogue_language import resolve_dialogue, workspace_for_h3
from miniconstruct.h3.builder import assemble_prompt, numbered_assets
from miniconstruct.llm.client import LLMBackendError, OpenAICompatibleClient
from miniconstruct.llm.compatibility import build_generation_payload
from miniconstruct.models.story_memory import StoryContinuityMemory
from miniconstruct.models.sequence_subject import CharacterProfile
from miniconstruct.models.story_creative_direction import StoryCreativeDirection
from miniconstruct.models.story_operations import STORY_NEXT_MAX_GENERATIONS, STORY_REPLAN_MAX_GENERATIONS
from miniconstruct.models.api import (
    AssistedGenerationPlan,
    ContinuityWriteRequest,
    EndpointProfile,
    GenerationRequest,
    LLMSettings,
    PlanningInferenceRequest,
    PlanningRevisionRequest,
    PlanningRevisionScope,
    ReasoningMode,
    SEED_MAX,
    SeedMode,
)
from miniconstruct.models.planning import GenerationPlanning, RelationshipToPrevious
from miniconstruct.models.story_planning import StoryRevisionScope
from miniconstruct.models.workspace import AssetKind, H3Mode, ImagePayload, Workspace, validate_authoring_workspace
from miniconstruct.project_store import GenerationDocument
from miniconstruct import planning as planning_service
from miniconstruct.benchmark.media import decode_data_image


RESULT_SCHEMA_VERSION = 1
CASE_SCHEMA_VERSION = 1
PROFILE_SCHEMA_VERSION = 1
BENCHMARK_SOURCE_ROOT_ENV = "MINICONSTRUCT_BENCHMARK_SOURCE_ROOT"

STORY_OPERATION_FIXTURES = {
    "build_story_plan": "build", "build_initial_range": "initial_range", "revise_story_plan": "revision",
    "plan_story_next": "next", "plan_story_insert": "insert", "plan_story_replan": "replan",
    "run_story_longform_stress": "stress",
}


class BenchmarkRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vision: bool = False


class BenchmarkExpectations(BaseModel):
    """Small, deliberately non-programmable deterministic assertion set."""
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    contains: list[str] = Field(default_factory=list)
    not_contains: list[str] = Field(default_factory=list, alias="notContains")
    regex: list[str] = Field(default_factory=list)
    not_regex: list[str] = Field(default_factory=list, alias="notRegex")
    required_finding_codes: list[str] = Field(default_factory=list, alias="requiredFindingCodes")
    forbidden_finding_codes: list[str] = Field(default_factory=list, alias="forbiddenFindingCodes")
    checks: list["BenchmarkCheck"] = Field(default_factory=list)

    @field_validator("regex", "not_regex")
    @classmethod
    def validate_patterns(cls, patterns: list[str]) -> list[str]:
        for pattern in patterns:
            re.compile(pattern)
        return patterns


PLANNER_CHECK_KINDS = {
    "field_equals", "field_not_empty", "field_changed", "field_unchanged", "relationship_equals",
    "recommendation_exists", "recommendation_source_equals", "recommendation_asset_exists",
    "recommendation_pair_valid", "no_duplicate_recommendation_pairs", "forbidden_text_absent",
    "selected_fields_only_changed",
}
STORY_CHECK_KINDS = {
    "story_item_count_equals", "story_existing_generation_count_equals",
    "story_existing_generation_ids_preserved", "story_new_items_have_no_existing_generation_id",
    "story_first_relationship_null", "story_relationship_valid", "story_relationship_equals",
    "story_relationship_not_equals", "story_sequence_context_equals", "story_sequence_context_not_empty",
    "story_field_equals", "story_field_not_empty", "story_field_changed", "story_field_unchanged",
    "story_field_contains", "story_field_matches_regex",
    "story_selected_fields_only_changed", "story_recommendation_pairs_valid",
    "story_no_duplicate_recommendation_pairs", "story_warning_exists", "image_input_count_equals",
    "story_replan_target_generation_ids_preserved",
    "story_initial_range_new_generation_count_equals", "story_initial_range_ordered_slots",
    "story_initial_range_memory_available",
    "story_tone_performance_controls_valid", "story_tone_performance_protected_non_auto_preserved",
    "story_relevant_subject_ids_equals", "story_character_profile_present", "story_character_profile_absent",
    "story_character_profile_policy_present", "story_character_profile_policy_absent",
}

STORY_BEHAVIORAL_CHECK_KINDS = {
    "review", "request_memory_contains", "request_memory_absent", "memory_thread_resolved",
    "memory_fact_id_preserved", "memory_thread_id_preserved", "historical_subject_available", "historical_subject_absent",
    "replan_target_generation_ids_preserved", "insert_apply_projection",
}


class BenchmarkCheck(BaseModel):
    """Declarative checks shared by planner fixtures.

    The runner deliberately keeps this vocabulary small. Values are data, not
    executable expressions, so a fixture cannot add arbitrary Python behavior.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    kind: str
    field: str | None = None
    value: Any = None
    fields: list[str] = Field(default_factory=list)
    source: str | None = None
    asset_id: str | None = Field(default=None, alias="assetId")
    text: str | None = None
    patterns: list[str] = Field(default_factory=list)
    item: int | None = Field(default=None, ge=1, le=12)
    related_item: int | None = Field(default=None, alias="relatedItem", ge=1, le=12)
    direction: Literal["upstream", "downstream"] | None = None

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, value: str) -> str:
        if value not in PLANNER_CHECK_KINDS | STORY_CHECK_KINDS:
            raise ValueError(f"unsupported benchmark check kind {value!r}")
        return value

    @field_validator("patterns")
    @classmethod
    def validate_check_patterns(cls, patterns: list[str]) -> list[str]:
        for pattern in patterns:
            re.compile(pattern)
        return patterns


BenchmarkExpectations.model_rebuild()


class PlannerGenerationFixture(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    workspace_path: str | None = Field(default=None, alias="workspacePath")
    incoming_state: str | None = Field(default=None, alias="incomingState")
    intended_outgoing_state: str | None = Field(default=None, alias="intendedOutgoingState")
    planning: GenerationPlanning = Field(default_factory=GenerationPlanning)


class PlannerRevisionFixture(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    instruction: str = Field(min_length=1, max_length=8000)
    editable_scopes: list[PlanningRevisionScope] = Field(alias="editableScopes", min_length=1)


class PlannerFixture(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    operation: str
    sequence_context: str = Field(default="", alias="sequenceContext", max_length=20000)
    previous_generation: PlannerGenerationFixture | None = Field(default=None, alias="previousGeneration")
    current_generation: PlannerGenerationFixture = Field(default_factory=PlannerGenerationFixture, alias="currentGeneration")
    revision: PlannerRevisionFixture | None = None

    @field_validator("operation")
    @classmethod
    def validate_operation(cls, value: str) -> str:
        if value not in {"infer_plan", "revise_plan"}:
            raise ValueError(f"unsupported planner benchmark operation {value!r}")
        return value

    @model_validator(mode="after")
    def validate_operation_shape(self) -> "PlannerFixture":
        if self.operation == "revise_plan" and self.revision is None:
            raise ValueError("revise_plan planner fixtures require revision")
        if self.operation == "infer_plan" and self.revision is not None:
            raise ValueError("infer_plan planner fixtures must not contain revision")
        if self.operation == "infer_plan" and self.previous_generation is None:
            raise ValueError("infer_plan planner fixtures require previousGeneration")
        return self


class FixtureAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: str = Field(alias="assetId", min_length=1)
    path: str = Field(min_length=1)
    # ``benchmark`` is intentionally read-only shared fixture material already
    # committed under benchmarks/.  It avoids copying a placeholder image into
    # every Story scenario while preserving the normal fixture resolver.
    source: Literal["case", "external", "benchmark"] = "case"


STORY_FIXTURE_MAX_SOURCE_GENERATIONS = 1000


class StoryGenerationFixture(PlannerGenerationFixture):
    workspace_path: str = Field(alias="workspacePath", min_length=1)
    fixture_assets: list[FixtureAsset] = Field(default_factory=list, alias="fixtureAssets")
    repeat: int = Field(default=1, ge=1, le=STORY_FIXTURE_MAX_SOURCE_GENERATIONS)
    scene_plan: str | None = Field(default=None, alias="scenePlan", max_length=50000)
    dialogue: str | None = Field(default=None, max_length=30000)
    story_memory_after: StoryContinuityMemory | None = Field(default=None, alias="storyMemoryAfter")


class StoryNarrativeSubjectFixture(BaseModel):
    """Portable names applied through the normal ProjectStore naming API."""
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    subject_id: str = Field(alias="subjectId", min_length=1, max_length=128)
    narrative_name: str = Field(alias="narrativeName", min_length=1, max_length=80)
    narrative_aliases: list[str] = Field(default_factory=list, alias="narrativeAliases", max_length=100)
    character_profile: CharacterProfile = Field(default_factory=CharacterProfile, alias="characterProfile")


class StoryBuildFixture(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    idea: str = Field(min_length=1, max_length=20000)
    desired_generation_count: int = Field(alias="desiredGenerationCount", strict=True, ge=2, le=12)

    @field_validator("idea")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("idea must not be blank")
        return value


class StoryInitialRangeFixture(BaseModel):
    """Bounded 2–12 Build Initial Range fixture; G1 is the one saved source Generation."""
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    requested_total_count: int = Field(alias="requestedTotalCount", strict=True, ge=2, le=12)


class StoryReferenceFixture(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    source_generation: int = Field(alias="sourceGeneration", ge=1, le=12, strict=True)
    asset_id: str = Field(alias="assetId", min_length=1, max_length=128)
    recommendation: str = Field(min_length=1, max_length=4000)


class StoryItemFixture(BaseModel):
    """Complete authoring draft, with portable one-based source identities."""
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    existing_generation: int | Literal["sequential"] | None = Field(alias="existingGeneration")
    scene_plan: str = Field(alias="scenePlan", max_length=50000)
    dialogue: str = Field(default="", max_length=30000)
    incoming_state: str = Field(alias="incomingState", max_length=20000)
    intended_outgoing_state: str = Field(alias="intendedOutgoingState", max_length=20000)
    relationship_to_previous: RelationshipToPrevious | None = Field(alias="relationshipToPrevious")
    camera_intent: str = Field(alias="cameraIntent", max_length=20000)
    reference_recommendations: list[StoryReferenceFixture] = Field(alias="referenceRecommendations", max_length=256)
    repeat: int = Field(default=1, ge=1, le=12)

    @field_validator("existing_generation")
    @classmethod
    def validate_existing_generation(cls, value):
        if value is not None and value != "sequential" and (isinstance(value, bool) or not 1 <= value <= 12):
            raise ValueError("existingGeneration must be a 1–12 ordinal, sequential, or null")
        return value


class StoryDraftFixture(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    sequence_context: str = Field(alias="sequenceContext", max_length=20000)
    items: list[StoryItemFixture] = Field(min_length=2, max_length=12)

    @model_validator(mode="after")
    def expand_repeated_items(self):
        expanded = []
        next_existing = 1
        for item in self.items:
            for _ in range(item.repeat):
                existing = next_existing if item.existing_generation == "sequential" else item.existing_generation
                if isinstance(existing, int):
                    next_existing = max(next_existing, existing + 1)
                expanded.append(item.model_copy(update={"existing_generation": existing, "repeat": 1}))
        if not 2 <= len(expanded) <= 12:
            raise ValueError("expanded Story draft must contain 2–12 items")
        self.items = expanded
        return self


class StoryRevisionFixture(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    draft: StoryDraftFixture
    selected_items: list[Annotated[int, Field(strict=True, ge=1, le=12)]] = Field(alias="selectedItems", min_length=1, max_length=12)
    scopes: list[StoryRevisionScope | PlanningRevisionScope] = Field(min_length=1, max_length=6)
    instruction: str = Field(min_length=1, max_length=8000)

    @model_validator(mode="after")
    def selection_is_valid(self):
        if len(set(self.selected_items)) != len(self.selected_items) or max(self.selected_items) > len(self.draft.items):
            raise ValueError("selectedItems must be unique draft ordinals")
        if len(set(self.scopes)) != len(self.scopes) or not self.instruction.strip():
            raise ValueError("unique scopes and nonblank instruction required")
        return self


class StoryNextFixture(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    requested_count: int = Field(default=3, alias="requestedCount", strict=True, ge=1, le=STORY_NEXT_MAX_GENERATIONS)
    instruction: str = Field(default="", max_length=8000)


class StoryInsertFixture(BaseModel):
    """Portable one-based A selection for a bounded Insert Between fixture."""
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    upstream_generation: int = Field(alias="upstreamGeneration", strict=True, ge=1, le=STORY_FIXTURE_MAX_SOURCE_GENERATIONS)
    requested_count: int = Field(default=1, alias="requestedCount", strict=True, ge=1, le=STORY_NEXT_MAX_GENERATIONS)
    instruction: str = Field(default="", max_length=8000)


class StoryReplanFixture(BaseModel):
    """Portable one-based inclusive C..D selection for a Replan Range fixture."""
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    start_generation: int = Field(alias="startGeneration", strict=True, ge=1, le=STORY_FIXTURE_MAX_SOURCE_GENERATIONS)
    end_generation: int = Field(alias="endGeneration", strict=True, ge=1, le=STORY_FIXTURE_MAX_SOURCE_GENERATIONS)
    instruction: str = Field(default="", max_length=8000)


class StoryLongformStressFixture(BaseModel):
    """One opt-in, stateful production-path Story scenario.

    This is deliberately a small declarative shape rather than a second
    benchmark framework.  The runner owns the fixed operation order; the case
    supplies only the concise model-facing instructions and stable historical
    target ordinals used while recording evidence.
    """
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    plan_next: list[StoryNextFixture] = Field(alias="planNext", min_length=6, max_length=6)
    replan_instruction: str = Field(alias="replanInstruction", min_length=1, max_length=8000)
    insert_instruction: str = Field(alias="insertInstruction", min_length=1, max_length=8000)
    replan_start_generation: int = Field(default=11, alias="replanStartGeneration", ge=1)
    replan_end_generation: int = Field(default=12, alias="replanEndGeneration", ge=1)
    insert_upstream_generation: int = Field(default=7, alias="insertUpstreamGeneration", ge=1)

    @model_validator(mode="after")
    def exact_stress_shape(self):
        if any(phase.requested_count != 3 for phase in self.plan_next):
            raise ValueError("story-longform-stress Plan Next phases must each request exactly 3 Generations")
        if self.replan_end_generation < self.replan_start_generation:
            raise ValueError("stress Replan end must not precede start")
        if self.replan_end_generation - self.replan_start_generation + 1 > STORY_REPLAN_MAX_GENERATIONS:
            raise ValueError("stress Replan range exceeds the production maximum")
        if self.insert_upstream_generation >= self.replan_start_generation:
            raise ValueError("stress Insert boundary must precede its Replan range")
        return self


class StoryBehavioralCheck(BaseModel):
    """Small declarative continuity evidence contract; it never judges prose with an LLM."""
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")
    description: str = Field(min_length=1, max_length=1000)
    kind: str = "review"
    text: str | None = Field(default=None, max_length=500)
    memory_id: str | None = Field(default=None, alias="memoryId")
    subject_id: str | None = Field(default=None, alias="subjectId")
    expected_concepts: list[str] = Field(default_factory=list, alias="expectedConcepts", max_length=32)
    forbidden_concepts: list[str] = Field(default_factory=list, alias="forbiddenConcepts", max_length=32)

    @field_validator("kind")
    @classmethod
    def valid_kind(cls, value: str) -> str:
        if value not in STORY_BEHAVIORAL_CHECK_KINDS:
            raise ValueError(f"unsupported Story behavioral check kind {value!r}")
        return value


    @model_validator(mode="after")
    def valid_shape(self):
        if self.kind in {"request_memory_contains", "request_memory_absent"} and not self.text:
            raise ValueError(f"{self.kind} requires text")
        if self.kind in {"memory_thread_resolved", "memory_fact_id_preserved", "memory_thread_id_preserved"} and not self.memory_id:
            raise ValueError(f"{self.kind} requires memoryId")
        if self.kind in {"historical_subject_available", "historical_subject_absent"} and not self.subject_id:
            raise ValueError(f"{self.kind} requires subjectId")
        return self


class StoryFixture(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    sequence_context: str = Field(default="", alias="sequenceContext", max_length=20000)
    generations: list[StoryGenerationFixture] = Field(min_length=1, max_length=STORY_FIXTURE_MAX_SOURCE_GENERATIONS)
    story_idea: str = Field(default="", alias="storyIdea", max_length=20000)
    story_creative_direction: StoryCreativeDirection = Field(
        default_factory=StoryCreativeDirection, alias="storyCreativeDirection",
    )
    story_memory: StoryContinuityMemory = Field(default_factory=StoryContinuityMemory, alias="storyMemory")
    next: StoryNextFixture | None = None
    insert: StoryInsertFixture | None = None
    replan: StoryReplanFixture | None = None
    stress: StoryLongformStressFixture | None = None
    narrative_subjects: list[StoryNarrativeSubjectFixture] = Field(default_factory=list, alias="narrativeSubjects")
    build: StoryBuildFixture | None = None
    initial_range: StoryInitialRangeFixture | None = Field(default=None, alias="initialRange")
    revision: StoryRevisionFixture | None = None

    @model_validator(mode="after")
    def validate_draft(self):
        generations = [generation.model_copy(update={"repeat": 1})
                       for generation in self.generations for _ in range(generation.repeat)]
        if not 1 <= len(generations) <= (STORY_FIXTURE_MAX_SOURCE_GENERATIONS if self.next or self.insert or self.replan or self.stress else 12):
            raise ValueError("expanded Story fixture exceeds its operation source limit")
        self.generations = generations
        if sum(value is not None for value in (self.build, self.initial_range, self.revision, self.next, self.insert, self.replan, self.stress)) != 1:
            raise ValueError("Story fixture requires exactly one of build, initialRange, revision, next, insert, replan or stress")
        if self.generations[0].planning.relationship_to_previous is not None:
            raise ValueError("First canonical Generation relationship must be null")
        if self.build and self.build.desired_generation_count < len(self.generations):
            raise ValueError("desired count cannot be smaller than canonical Sequence")
        if self.initial_range and len(self.generations) != 1:
            raise ValueError("initialRange requires exactly one canonical Generation")
        if self.revision:
            items = self.revision.draft.items
            expected = list(range(1, len(self.generations) + 1)) + [None] * (len(items) - len(self.generations))
            if [i.existing_generation for i in items] != expected:
                raise ValueError("Draft existing Generations must retain canonical order before new items")
            for index, item in enumerate(items):
                if (index == 0) != (item.relationship_to_previous is None):
                    raise ValueError("Only first draft relationship must be null")
                if any(ref.source_generation > len(self.generations) for ref in item.reference_recommendations):
                    raise ValueError("Draft reference source is not a canonical Generation")
        if self.insert and self.insert.upstream_generation >= len(self.generations):
            raise ValueError("Insert upstreamGeneration must have a protected successor")
        if self.replan:
            if self.replan.end_generation < self.replan.start_generation:
                raise ValueError("Replan endGeneration must not precede startGeneration")
            if self.replan.end_generation > len(self.generations):
                raise ValueError("Replan range must select canonical Generations")
            if self.replan.end_generation - self.replan.start_generation + 1 > STORY_REPLAN_MAX_GENERATIONS:
                raise ValueError("Replan range exceeds the bounded operation limit")
        if self.stress:
            if len(self.generations) != 3:
                raise ValueError("story-longform-stress must start with exactly 3 saved Generations")
            if self.stress.replan_end_generation > 21:
                raise ValueError("stress Replan target must exist after six Plan Next phases")
            if self.stress.insert_upstream_generation >= 21:
                raise ValueError("stress Insert boundary must exist before historical editing")
        return self


from miniconstruct.models.observations import ObservedEndState


class H3ProjectFixture(BaseModel):
    """Opt-in saved Project inputs, resolved by the same production authority path."""
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    sequence_context: str = Field(default="", alias="sequenceContext")
    incoming_state: str = Field(default="", alias="incomingState")
    intended_outgoing_state: str = Field(default="", alias="intendedOutgoingState")
    planning: GenerationPlanning = Field(default_factory=GenerationPlanning)
    predecessor_observation: ObservedEndState | None = Field(default=None, alias="predecessorObservation")
    predecessor_visual_fixture: bool = Field(default=False, alias="predecessorVisualFixture")
    predecessor_intended_outgoing_state: str = Field(default="", alias="predecessorIntendedOutgoingState")
    predecessor_memory: StoryContinuityMemory = Field(default_factory=StoryContinuityMemory, alias="predecessorMemory")
    input_contains: list[str] = Field(default_factory=list, alias="inputContains")
    input_absent: list[str] = Field(default_factory=list, alias="inputAbsent")
    expected_task_relationship: Literal["keyframe completion"] | None = Field(default=None, alias="expectedTaskRelationship")


def resolve_h3_fixture(request, fixture, visual_files=None):
    """Freeze a transient benchmark Project before it is removed; no provider call."""
    from miniconstruct.project_store import ProjectStore
    class FixtureStore(ProjectStore):
        def _new_project_id(self):
            return "10000000-0000-4000-8000-000000000001"

        def _new_generation_id(self):
            return str(uuid.UUID(int=next(self.generation_ids)))

    with tempfile.TemporaryDirectory(prefix="miniconstruct-h3-continuity-") as directory:
        store = FixtureStore(Path(directory))
        store.generation_ids = iter([101, 102])
        project = store.create_project(request.workspace, sequence_context=fixture.sequence_context,
                                       story_memory=fixture.predecessor_memory,
                                       intended_outgoing_state=fixture.predecessor_intended_outgoing_state)
        if fixture.predecessor_observation is not None:
            # Explicit synthetic observation fixture, not a claim of video analysis.
            from miniconstruct.models.takes import Take
            from miniconstruct.models.observations import TakeObservation, ObservationEvidence
            directory, manifest, generations, active, generation = store._output_context(project.project_id, project.active_generation_id)
            take = Take(id="10000000-0000-4000-8000-000000000010", createdAt="2026-09-20T00:00:00Z",
                originalFilename="synthetic-observation-fixture.mp4", mimeType="video/mp4", byteSize=1,
                sha256="a" * 64, managedMediaPath="outputs/media/" + "a" * 64 + ".mp4", sourceKind="external_unknown")
            generation.takes = [take]; generation.selected_take_id = take.id
            state = fixture.predecessor_observation
            generation.take_observations = [TakeObservation(takeId=take.id, takeSha256=take.sha256,
                analyzedAt="2026-09-20T00:00:00Z", analyzerModel="synthetic-fixture",
                offeredSubjectIds=[s.id for s in request.workspace.subjects],
                evidence=ObservationEvidence(sampledTimestamps=[0], endpointTimestamp=0, endpointSha256="b" * 64, contactSheetSha256="c" * 64),
                modelObservedState=state, effectiveObservedState=state.model_copy(deep=True))]
            store._commit_sequence(directory, manifest, generations, active)
        if fixture.predecessor_visual_fixture:
            from .visual_handoff import video
            from miniconstruct.models.takes import Take
            directory, manifest, generations, active, generation = store._output_context(project.project_id, project.active_generation_id)
            temporary_video = directory / "synthetic.mp4"
            video(temporary_video)
            with temporary_video.open("rb") as stream:
                sha = hashlib.file_digest(stream, "sha256").hexdigest()
            take = Take(id="10000000-0000-4000-8000-000000000020", createdAt="2026-09-21T00:00:00Z",
                originalFilename="synthetic-subject-1.mp4", mimeType="video/mp4", byteSize=temporary_video.stat().st_size,
                sha256=sha, managedMediaPath=f"outputs/media/{sha}.mp4", sourceKind="external_unknown")
            target = store._output_path(directory, take.managed_media_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary_video.replace(target)
            generation.takes.append(take); generation.selected_take_id = take.id
            store._commit_sequence(directory, manifest, generations, active)
            if visual_files is not None:
                visual_files["predecessor.mp4"] = target.read_bytes()
        project = store.create_generation(project.project_id, workspace=request.workspace)
        project = store.save_project(project.project_id, project.workspace,
            incoming_state=fixture.incoming_state, intended_outgoing_state=fixture.intended_outgoing_state,
            planning=fixture.planning.model_dump(by_alias=True))
        request = request.model_copy(update={"filesystem_project_id": project.project_id,
                                            "generation_id": project.active_generation_id,
                                            "workspace": project.workspace})
        resolved = resolve_narrative_request(request, store)
        if visual_files is not None and resolved._visual_handoff and resolved._visual_handoff.path:
            visual_files["handoff" + resolved._visual_handoff.path.suffix] = resolved._visual_handoff.path.read_bytes()
        return resolved


class BenchmarkCase(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    schema_version: int = Field(alias="schemaVersion")
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")
    version: str = Field(default="1", min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=4000)
    tags: list[str] = Field(default_factory=list)
    requirements: BenchmarkRequirements = Field(default_factory=BenchmarkRequirements)
    expectations: BenchmarkExpectations = Field(default_factory=BenchmarkExpectations)
    behavioral_checks: list[StoryBehavioralCheck] = Field(default_factory=list, alias="behavioralChecks")
    human_review: list[str] = Field(default_factory=list, alias="humanReview")
    fixture_assets: list[FixtureAsset] = Field(default_factory=list, alias="fixtureAssets")
    operation: str | None = None
    planner: PlannerFixture | None = None
    story: StoryFixture | None = None
    h3_project: H3ProjectFixture | None = Field(default=None, alias="h3Project")

    @field_validator("operation")
    @classmethod
    def validate_case_operation(cls, value: str | None) -> str | None:
        if value is not None and value not in {"generate_prompt", "infer_plan", "revise_plan"} | STORY_OPERATION_FIXTURES.keys():
            raise ValueError(f"unsupported benchmark operation {value!r}")
        return value

    @model_validator(mode="after")
    def validate_operation_shape(self) -> "BenchmarkCase":
        if self.h3_project is not None and self.operation not in {None, "generate_prompt"}:
            raise ValueError("h3Project requires ordinary H3 generation")
        if self.operation in STORY_OPERATION_FIXTURES:
            if self.story is None or self.planner is not None:
                raise ValueError("Story operations require story configuration and must not contain planner")
            if getattr(self.story, STORY_OPERATION_FIXTURES[self.operation]) is None:
                raise ValueError("case operation does not match story configuration")
            if any(check.kind not in STORY_CHECK_KINDS | {"forbidden_text_absent"} for check in self.expectations.checks):
                raise ValueError("Story cases require Story-compatible checks")
        elif self.story is not None:
            raise ValueError("Only Story operations may contain story configuration")
        elif self.behavioral_checks:
            raise ValueError("Story behavioral checks require a Story operation")
        elif any(check.kind in STORY_CHECK_KINDS for check in self.expectations.checks):
            raise ValueError("Story checks require a Story operation")
        if self.operation in {"infer_plan", "revise_plan"} and self.planner is None:
            raise ValueError(f"{self.operation} cases require planner configuration")
        if self.operation == "generate_prompt" and self.planner is not None:
            raise ValueError("generate_prompt cases must not contain planner configuration")
        if self.planner is not None and self.operation != self.planner.operation:
            raise ValueError("case operation does not match planner operation")
        return self

    @field_validator("schema_version")
    @classmethod
    def schema_is_supported(cls, value: int) -> int:
        if value != CASE_SCHEMA_VERSION:
            raise ValueError(f"unsupported benchmark case schemaVersion {value}")
        return value


class BenchmarkSuite(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    schema_version: int = Field(alias="schemaVersion")
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")
    title: str = Field(min_length=1)
    cases: list[str] = Field(min_length=1)

    @field_validator("schema_version")
    @classmethod
    def schema_is_supported(cls, value: int) -> int:
        if value != CASE_SCHEMA_VERSION:
            raise ValueError(f"unsupported benchmark suite schemaVersion {value}")
        return value


class BenchmarkProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")
    display_name: str = Field(alias="displayName", min_length=1)
    endpoint: EndpointProfile
    model_id: str = Field(alias="modelId", min_length=1)
    supports_vision: bool = Field(default=False, alias="supportsVision")
    api_key_env: str | None = Field(default=None, alias="apiKeyEnv")
    max_tokens: int = Field(default=4096, alias="maxTokens", ge=128, le=65536)
    context_window: int | None = Field(default=None, alias="contextWindow", ge=1, le=10_000_000)
    timeout_seconds: float = Field(default=120, alias="timeoutSeconds", ge=1, le=3600)
    temperature: float = Field(default=0.4, ge=0, le=2)
    reasoning_mode: ReasoningMode = Field(default=ReasoningMode.OFF, alias="reasoningMode")
    seed_mode: SeedMode = Field(default=SeedMode.FIXED, alias="seedMode")
    fixed_seed: int | None = Field(default=1, alias="fixedSeed", ge=0, le=SEED_MAX)

    @model_validator(mode="after")
    def require_fixed_seed_value(self):
        if self.seed_mode == SeedMode.FIXED and self.fixed_seed is None:
            raise ValueError("fixedSeed is required when seedMode is fixed")
        return self

    def to_settings(self) -> LLMSettings:
        endpoint = self.endpoint.public_copy()
        if self.api_key_env:
            value = os.environ.get(self.api_key_env)
            if value:
                endpoint = endpoint.model_copy(update={"api_key": SecretStr(value)})
        return LLMSettings(
            endpoint=endpoint,
            modelId=self.model_id,
            supportsVision=self.supports_vision,
            storyPlannerVision=self.supports_vision,
            maxTokens=self.max_tokens,
            # Values below the production settings minimum are retained for benchmark
            # utilization math but do not become an invalid production request setting.
            contextWindow=self.context_window if self.context_window is None or self.context_window >= 1024 else None,
            timeoutSeconds=self.timeout_seconds,
            reasoningMode=self.reasoning_mode,
            temperature=self.temperature,
            seedMode=self.seed_mode,
            fixedSeed=self.fixed_seed,
        )

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "displayName": self.display_name,
            "endpoint": self.endpoint.public_copy().model_dump(mode="json", by_alias=True, exclude={"api_key"}),
            "modelId": self.model_id,
            "supportsVision": self.supports_vision,
            "maxTokens": self.max_tokens,
            "contextWindow": self.context_window,
            "timeoutSeconds": self.timeout_seconds,
            "temperature": self.temperature,
            "reasoningMode": self.reasoning_mode.value,
            "seedMode": self.seed_mode.value,
            "fixedSeed": self.fixed_seed,
        }


class ProfileFile(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    schema_version: int = Field(alias="schemaVersion")
    profiles: list[BenchmarkProfile]

    @field_validator("schema_version")
    @classmethod
    def schema_is_supported(cls, value: int) -> int:
        if value != PROFILE_SCHEMA_VERSION:
            raise ValueError(f"unsupported model profile schemaVersion {value}")
        return value


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def benchmark_root(root: Path | None = None) -> Path:
    return (root or Path.cwd() / "benchmarks").resolve()


def benchmark_source_root(source_root: Path | None = None) -> Path | None:
    configured = str(source_root) if source_root is not None else os.environ.get(BENCHMARK_SOURCE_ROOT_ENV, "")
    return Path(configured).expanduser().resolve() if configured.strip() else None


def load_profiles(path: Path) -> dict[str, BenchmarkProfile]:
    try:
        profiles = ProfileFile.model_validate(_read_json(path)).profiles
    except ValidationError as exc:
        raise ValueError(f"Invalid benchmark profile file {path}: {exc}") from exc
    result = {profile.id: profile for profile in profiles}
    if len(result) != len(profiles):
        raise ValueError(f"Duplicate model profile ID in {path}")
    return result


def _load_case_definition(root: Path, case_id: str) -> tuple[BenchmarkCase, Path]:
    case_dir = root / "cases" / case_id
    case_path = case_dir / "case.json"
    try:
        case = BenchmarkCase.model_validate(_read_json(case_path))
    except (OSError, ValidationError, ValueError) as exc:
        raise ValueError(f"Invalid benchmark case {case_id}: {exc}") from exc
    if case.id != case_id:
        raise ValueError(f"Benchmark case directory {case_id} does not match case ID {case.id}")
    return case, case_dir


def load_case(root: Path, case_id: str, source_root: Path | None = None) -> tuple[BenchmarkCase, Workspace, Path, bytes]:
    case, case_dir = _load_case_definition(root, case_id)
    workspace_path = case_dir / "workspace.json"
    try:
        workspace_bytes = workspace_path.read_bytes()
        workspace = Workspace.model_validate_json(workspace_bytes)
    except (OSError, ValidationError, ValueError) as exc:
        raise ValueError(f"Invalid benchmark case {case_id}: {exc}") from exc
    return case, _resolve_fixture_assets(case, workspace, case_dir, source_root), case_dir, workspace_bytes


def _load_workspace_file(case: BenchmarkCase, case_dir: Path, relative: str) -> Workspace:
    path = (case_dir / relative).resolve()
    resolved_case_dir = case_dir.resolve()
    if resolved_case_dir not in path.parents:
        raise ValueError(f"Benchmark case {case.id} workspace path escapes its case directory")
    try:
        return validate_authoring_workspace(_read_json(path))
    except (OSError, ValidationError, ValueError) as exc:
        raise ValueError(f"Invalid planner workspace {relative} for {case.id}: {exc}") from exc


def load_planner_case(
    root: Path, case_id: str, source_root: Path | None = None,
) -> tuple[BenchmarkCase, Workspace, Workspace | None, Path, bytes]:
    """Load the current Workspace and optional committed predecessor fixture."""
    case, case_dir = _load_case_definition(root, case_id)
    if case.planner is None:
        raise ValueError(f"Benchmark case {case_id} is not a planner case")
    planner = case.planner
    current_path = planner.current_generation.workspace_path or "workspace.json"
    try:
        current_path_value = (case_dir / current_path).resolve()
        resolved_case_dir = case_dir.resolve()
        if resolved_case_dir not in current_path_value.parents:
            raise ValueError(f"Benchmark case {case.id} workspace path escapes its case directory")
        workspace_bytes = current_path_value.read_bytes()
        current = validate_authoring_workspace(json.loads(workspace_bytes))
        current = _resolve_fixture_assets(case, current, case_dir, source_root, require_all=False)
    except (OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise ValueError(f"Invalid planner workspace {current_path} for {case.id}: {exc}") from exc
    previous = None
    if planner.previous_generation is not None:
        previous_path = planner.previous_generation.workspace_path or "previous-workspace.json"
        previous = _load_workspace_file(case, case_dir, previous_path)
        previous = _resolve_fixture_assets(case, previous, case_dir, source_root, require_all=False)
    known_asset_ids = {asset.id for asset in current.assets}
    if previous is not None:
        known_asset_ids |= {asset.id for asset in previous.assets}
    unknown_fixture_assets = {fixture.asset_id for fixture in case.fixture_assets} - known_asset_ids
    if unknown_fixture_assets:
        raise ValueError(f"Benchmark case {case.id} has fixture paths for unknown assets: {sorted(unknown_fixture_assets)}")
    return case, current, previous, case_dir, workspace_bytes


def load_suite(root: Path, suite_id: str) -> BenchmarkSuite:
    path = root / "suites" / f"{suite_id}.json"
    try:
        suite = BenchmarkSuite.model_validate(_read_json(path))
    except (ValidationError, ValueError) as exc:
        raise ValueError(f"Invalid benchmark suite {suite_id}: {exc}") from exc
    if suite.id != suite_id:
        raise ValueError(f"Benchmark suite filename {suite_id} does not match suite ID {suite.id}")
    return suite


def _fixture_path(case: BenchmarkCase, fixture: FixtureAsset, case_dir: Path, source_root: Path | None) -> Path:
    if fixture.source == "external":
        if source_root is None:
            raise ValueError(
                f"Benchmark case {case.id} uses external fixture {fixture.path!r}; "
                f"set {BENCHMARK_SOURCE_ROOT_ENV} or pass --source-root"
            )
        base = source_root.resolve()
    elif fixture.source == "benchmark":
        base = case_dir.parents[1].resolve()
    else:
        base = case_dir.resolve()
    path = (base / fixture.path).resolve()
    if base not in path.parents:
        raise ValueError(f"Benchmark fixture path {fixture.path} escapes its source root")
    return path


def _resolve_fixture_assets(
    case: BenchmarkCase, workspace: Workspace, case_dir: Path, source_root: Path | None = None,
    *, require_all: bool = True,
) -> Workspace:
    """Load optional relative image fixture paths into the real Workspace model."""
    by_id = {asset.asset_id: asset for asset in case.fixture_assets}
    if len(by_id) != len(case.fixture_assets):
        raise ValueError(f"Benchmark case {case.id} has duplicate fixture asset IDs")
    assets = []
    workspace_ids = {asset.id for asset in workspace.assets}
    unknown = set(by_id) - workspace_ids
    if unknown and require_all:
        raise ValueError(f"Benchmark case {case.id} has fixture paths for unknown assets: {sorted(unknown)}")
    for asset in workspace.assets:
        fixture = by_id.get(asset.id)
        if fixture is None:
            assets.append(asset)
            continue
        file_path = _fixture_path(case, fixture, case_dir, source_root)
        data = file_path.read_bytes()
        if asset.kind != AssetKind.IMAGE:
            # Audio/Video fixture bytes are retained only for a manual-render
            # bundle. They are not converted into inline prompt-writer inputs.
            assets.append(asset)
            continue
        encoded = base64.b64encode(data).decode("ascii")
        image = (
            asset.image.model_copy(update={"data_url": f"data:{asset.mime_type};base64,{encoded}"})
            if asset.image is not None
            else ImagePayload(data_url=f"data:{asset.mime_type};base64,{encoded}")
        )
        assets.append(asset.model_copy(update={"image": image}))
    return workspace.model_copy(update={"assets": assets})


def list_definitions(root: Path) -> dict[str, list[str]]:
    root = benchmark_root(root)
    cases = sorted(path.name for path in (root / "cases").iterdir() if path.is_dir() and (path / "case.json").exists())
    suites = sorted(path.stem for path in (root / "suites").glob("*.json"))
    return {"cases": cases, "suites": suites}


def _git_info(repository: Path) -> dict[str, Any]:
    def run(*args: str) -> str | None:
        result = subprocess.run(["git", *args], cwd=repository, capture_output=True, text=True, check=False)
        return result.stdout.strip() if result.returncode == 0 else None
    status = run("status", "--porcelain")
    return {"commit": run("rev-parse", "HEAD"), "dirty": bool(status) if status is not None else None}


def _workspace_image_mappings(case: BenchmarkCase, workspace: Workspace, *, h3_derivative: bool = False) -> dict[str, list[dict[str, str]]]:
    """Map only exact image bytes; filenames are never used as an identity heuristic."""
    fixtures = {fixture.asset_id: fixture for fixture in case.fixture_assets}
    mappings: dict[str, list[dict[str, str]]] = {}
    for asset in workspace.assets:
        if asset.kind != AssetKind.IMAGE or asset.image is None:
            continue
        decoded = decode_data_image(asset.image.data_url)
        if decoded is None:
            continue
        if h3_derivative:
            from miniconstruct.h3.vision_input import prompt_writer_image
            model_url = prompt_writer_image(asset.image.data_url)[0]
            decoded = decode_data_image(model_url)
        digest = hashlib.sha256(decoded[0]).hexdigest()
        mappings.setdefault(digest, []).append({
            "assetId": asset.id, "filename": asset.filename,
            **({"fixturePath": fixtures[asset.id].path} if asset.id in fixtures else {}),
            **({"fixtureSource": "external"} if asset.id in fixtures and fixtures[asset.id].source == "external" else {}),
        })
    return mappings


def _bundle_extension(mime_type: str, kind: str) -> str:
    known = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
             "video/mp4": ".mp4", "video/webm": ".webm", "audio/mpeg": ".mp3",
             "audio/wav": ".wav", "audio/ogg": ".ogg"}
    return known.get(mime_type.lower(), mimetypes.guess_extension(mime_type) or f".{kind}")


def _manual_render_inputs(case: BenchmarkCase, case_dir: Path, source_root: Path | None, request, visual_files: dict[str, bytes]):
    """Capture every final effective H3 reference with no source-path disclosure."""
    files: dict[str, bytes] = {}
    references = []
    fixtures = {fixture.asset_id: fixture for fixture in case.fixture_assets}
    for asset, label in numbered_assets(request.prompt_workspace):
        handoff = asset.options.get("visualHandoff") if isinstance(asset.options, dict) else None
        origin = "continuity_handoff" if handoff else "ordinary_reference"
        payload = None
        if handoff:
            suffix = _bundle_extension(asset.mime_type, asset.kind.value)
            payload = visual_files.get("handoff" + suffix)
        elif asset.kind == AssetKind.IMAGE and asset.image is not None:
            decoded = decode_data_image(asset.image.data_url)
            payload = decoded[0] if decoded else None
        elif asset.id in fixtures:
            payload = _fixture_path(case, fixtures[asset.id], case_dir, source_root).read_bytes()
        if payload is None:
            raise ValueError(f"Manual-render bundle cannot resolve final <{label}> bytes")
        stem = label.lower().replace(" ", "-")
        archive_path = f"references/{stem}{_bundle_extension(asset.mime_type, asset.kind.value)}"
        files[archive_path] = payload
        reference = {"h3Label": label, "path": archive_path, "kind": asset.kind.value,
                    "mimeType": asset.mime_type, "origin": origin,
                    "sha256": hashlib.sha256(payload).hexdigest(), "byteSize": len(payload),
                    "role": asset.role}
        if asset.role == "first_frame_anchor" and request.prompt_workspace.mode == H3Mode.I2VA:
            reference["authority"] = "required_first_frame"
        fixture = fixtures.get(asset.id)
        if fixture is not None:
            reference["fixture"] = {"source": fixture.source, "path": fixture.path}
        references.append(reference)
    return files, {"schemaVersion": 1, "h3Mode": request.prompt_workspace.mode.value,
                   "promptFile": "prompt.txt", "references": references}


def _messages_provenance(
    messages: list[dict[str, Any]], image_mappings: dict[str, list[dict[str, str]]] | None = None,
) -> dict[str, Any]:
    system = "\n\n".join(message["content"] for message in messages if message.get("role") == "system" and isinstance(message.get("content"), str))
    user_text: list[str] = []
    images: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            user_text.append(content)
        elif isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text" and isinstance(part.get("text"), str):
                    user_text.append(part["text"])
                elif part.get("type") == "image_url":
                    image_url = part.get("image_url")
                    url = image_url.get("url") if isinstance(image_url, dict) else None
                    decoded = decode_data_image(url) if isinstance(url, str) else None
                    image = {"index": len(images) + 1, "sha256": None, "mimeType": None}
                    if decoded is not None:
                        image["sha256"] = hashlib.sha256(decoded[0]).hexdigest()
                        image["mimeType"] = decoded[1]
                        matches = (image_mappings or {}).get(image["sha256"], [])
                        if len(matches) == 1:
                            image.update(matches[0])
                    images.append(image)
    user = "\n\n".join(user_text)
    # These are byte/character measurements of the exact captured text, never
    # estimates of provider tokenisation.
    return {
        "system": system, "user": user, "imageInputCount": len(images), "images": images,
        "systemTextCharacters": len(system), "systemTextBytes": len(system.encode("utf-8")),
        "userTextCharacters": len(user), "userTextBytes": len(user.encode("utf-8")),
        "totalTextInputCharacters": len(system) + len(user),
        "totalTextInputBytes": len(system.encode("utf-8")) + len(user.encode("utf-8")),
    }


def _effective_request_parameters(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep output-affecting request fields, never messages, media, credentials, or headers."""
    allowed = {
        "model", "temperature", "max_tokens", "max_completion_tokens", "seed", "stream",
        "stream_options", "chat_template_kwargs", "reasoning_effort", "top_p", "presence_penalty", "frequency_penalty",
    }
    return {key: payload[key] for key in allowed if key in payload}


def _seed_provenance(
    *, requested_seed: int | None, mode: str, effective_seed: int | None,
    seed_unsupported: bool, known_unsupported: bool, final_request_included_seed: bool,
) -> dict[str, Any]:
    """The single seed-result contract shared by Generation and Story runs."""
    if requested_seed is None:
        status = "not_requested"
    elif not final_request_included_seed or seed_unsupported or known_unsupported:
        status = "dropped_unsupported"
    else:
        status = "accepted_by_provider"
    return {
        "requested": requested_seed, "mode": mode, "effective": effective_seed,
        "status": status, "unsupportedFallback": seed_unsupported,
        "knownUnsupported": known_unsupported,
        "finalRequestIncludedSeed": final_request_included_seed,
    }


def _reference_inputs(
    case: BenchmarkCase, workspace: Workspace, case_dir: Path, source_root: Path | None = None,
) -> list[dict[str, Any]]:
    fixtures = {fixture.asset_id: fixture for fixture in case.fixture_assets}
    labels = {asset.id: label for asset, label in numbered_assets(workspace)}
    result = []
    for asset in workspace.assets:
        entry: dict[str, Any] = {
            "assetId": asset.id, "label": labels.get(asset.id), "kind": asset.kind.value,
            "filename": asset.filename, "mimeType": asset.mime_type, "role": asset.role,
            "attached": asset.attached, "fixturePath": fixtures[asset.id].path if asset.id in fixtures else None,
        }
        if asset.id in fixtures:
            fixture = fixtures[asset.id]
            data = _fixture_path(case, fixture, case_dir, source_root).read_bytes()
            entry["fixtureSha256"] = hashlib.sha256(data).hexdigest()
            if fixture.source == "external":
                entry["fixtureSource"] = "external"
        result.append(entry)
    return result


def evaluate_expectations(expectations: BenchmarkExpectations, output: str, finding_codes: set[str]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    checks += [{"kind": "contains", "target": item, "passed": item in output} for item in expectations.contains]
    checks += [{"kind": "notContains", "target": item, "passed": item not in output} for item in expectations.not_contains]
    checks += [{"kind": "regex", "target": item, "passed": bool(re.search(item, output))} for item in expectations.regex]
    checks += [{"kind": "notRegex", "target": item, "passed": not bool(re.search(item, output))} for item in expectations.not_regex]
    checks += [{"kind": "requiredFindingCode", "target": item, "passed": item in finding_codes} for item in expectations.required_finding_codes]
    checks += [{"kind": "forbiddenFindingCode", "target": item, "passed": item not in finding_codes} for item in expectations.forbidden_finding_codes]
    return checks


_PLAN_FIELDS = {
    "creativeRequest": "creative_request",
    "incomingState": "incoming_state",
    "intendedOutgoingState": "intended_outgoing_state",
    "relationshipToPrevious": "relationship_to_previous",
    "cameraIntent": "camera_intent",
}


def _plan_values(plan: AssistedGenerationPlan, request: PlanningInferenceRequest | PlanningRevisionRequest) -> tuple[dict[str, Any], dict[str, Any]]:
    actual = {field: getattr(plan, attr) for field, attr in _PLAN_FIELDS.items()}
    current = {
        "creativeRequest": request.workspace.creative_request,
        "incomingState": request.continuity.incoming_state or "",
        "intendedOutgoingState": request.continuity.intended_outgoing_state or "",
        "relationshipToPrevious": request.planning.relationship_to_previous,
        "cameraIntent": request.planning.camera_intent,
    }
    return actual, current


def _recommendation_pairs(plan: AssistedGenerationPlan) -> list[tuple[str, str]]:
    return [(item.source.value, item.asset_id) for item in plan.reference_recommendations]


def _raw_recommendation_pairs(raw: str | None) -> list[tuple[str, str]] | None:
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    recommendations = value.get("referenceRecommendations") if isinstance(value, dict) else None
    if not isinstance(recommendations, list):
        return []
    pairs: list[tuple[str, str]] = []
    for item in recommendations:
        if not isinstance(item, dict):
            pairs.append(("", ""))
            continue
        source = item.get("source") if isinstance(item.get("source"), str) else ""
        asset_id = item.get("assetId") if isinstance(item.get("assetId"), str) else ""
        pairs.append((source, asset_id))
    return pairs


def _check_result(kind: str, target: Any, passed: bool, detail: str = "") -> dict[str, Any]:
    result = {"kind": kind, "target": target, "passed": passed}
    if detail:
        result["detail"] = detail
    return result


def evaluate_planner_checks(
    checks: list[BenchmarkCheck],
    plan: AssistedGenerationPlan,
    request: PlanningInferenceRequest | PlanningRevisionRequest,
    allowed_pairs: frozenset[tuple[str, str]],
    raw_recommendation_pairs: list[tuple[str, str]] | None = None,
    raw_model_output: str | None = None,
) -> list[dict[str, Any]]:
    """Evaluate declarative planner invariants without semantic interpretation."""
    actual, current = _plan_values(plan, request)
    pairs = raw_recommendation_pairs if raw_recommendation_pairs is not None else _recommendation_pairs(plan)
    serialized = json.dumps(plan.model_dump(mode="json", by_alias=True), ensure_ascii=False)
    searchable_output = serialized if raw_model_output is None else f"{serialized}\n{raw_model_output}"
    results: list[dict[str, Any]] = []
    for check in checks:
        kind = check.kind
        if kind == "field_equals":
            passed = check.field in actual and actual[check.field] == check.value
            results.append(_check_result(kind, {"field": check.field, "value": check.value}, passed))
        elif kind == "field_not_empty":
            value = actual.get(check.field or "")
            passed = value is not None and bool(str(value).strip())
            results.append(_check_result(kind, check.field, passed))
        elif kind in {"field_changed", "field_unchanged"}:
            field = check.field or ""
            passed = field in actual and ((actual.get(field) != current.get(field)) if kind == "field_changed" else (actual.get(field) == current.get(field)))
            results.append(_check_result(kind, field, passed))
        elif kind == "relationship_equals":
            passed = actual.get("relationshipToPrevious") == check.value
            results.append(_check_result(kind, check.value, passed))
        elif kind == "recommendation_exists":
            pair = (check.source or "", check.asset_id or "")
            passed = pair in pairs
            results.append(_check_result(kind, {"source": pair[0], "assetId": pair[1]}, passed))
        elif kind == "recommendation_source_equals":
            matching = [source for source, asset_id in pairs if asset_id == (check.asset_id or "")]
            passed = bool(matching) and all(source == check.source for source in matching)
            results.append(_check_result(kind, {"assetId": check.asset_id, "source": check.source}, passed))
        elif kind == "recommendation_asset_exists":
            asset_ids = {asset_id for _source, asset_id in pairs}
            passed = (check.asset_id or "") in asset_ids
            results.append(_check_result(kind, check.asset_id, passed))
        elif kind == "recommendation_pair_valid":
            invalid = [pair for pair in pairs if pair not in allowed_pairs]
            results.append(_check_result(kind, "all", not invalid, f"invalid pairs: {invalid}" if invalid else ""))
        elif kind == "no_duplicate_recommendation_pairs":
            results.append(_check_result(kind, "all", len(pairs) == len(set(pairs))))
        elif kind == "forbidden_text_absent":
            needles = ([check.text] if check.text else []) + check.patterns
            passed = all(not (needle and (re.search(needle, searchable_output, re.IGNORECASE) if needle in check.patterns else needle.lower() in searchable_output.lower())) for needle in needles)
            results.append(_check_result(kind, needles, passed))
        elif kind == "selected_fields_only_changed":
            selected = set(check.fields)
            changed = {field for field in actual if actual[field] != current[field]}
            passed = changed <= selected and selected <= changed
            results.append(_check_result(kind, sorted(selected), passed, f"changed fields: {sorted(changed)}"))
    return results


class BenchmarkRunner:
    def __init__(
        self,
        root: Path,
        client_factory: Callable[[LLMSettings], OpenAICompatibleClient] = OpenAICompatibleClient,
        source_root: Path | None = None,
    ) -> None:
        self.root = benchmark_root(root)
        self.client_factory = client_factory
        self.source_root = benchmark_source_root(source_root)

    async def run(self, profile: BenchmarkProfile, case_ids: list[str], suite_id: str | None = None) -> dict[str, Any]:
        run_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
        started = datetime.now(timezone.utc).isoformat()
        result: dict[str, Any] = {
            "schemaVersion": RESULT_SCHEMA_VERSION, "runId": run_id, "timestamp": started,
            "git": _git_info(self.root.parent), "suite": suite_id, "profile": profile.public_dict(),
            "baseline": {"temperature": 0.4, "seedMode": "fixed", "fixedSeed": 1, "variations": 1},
            "generationTransport": "non_streaming",
            "cases": [],
        }
        for case_id in case_ids:
            result["cases"].append(await self._run_case(profile, case_id))
        return result

    async def _run_case(self, profile: BenchmarkProfile, case_id: str) -> dict[str, Any]:
        case, _case_dir = _load_case_definition(self.root, case_id)
        if case.story is not None:
            if case.requirements.vision and not profile.supports_vision:
                result = self._planner_skip_result(profile, case, settings=self._story_settings(profile))
                result["settings"].update(repair="never", automaticRetry="never", llmJudge="none")
                return result
            from .story import run_story_case, run_story_longform_stress
            if case.story.stress is not None:
                return await run_story_longform_stress(self, profile, case, _case_dir)
            return await run_story_case(self, profile, case, _case_dir)
        if case.planner is not None or case.operation in {"infer_plan", "revise_plan"}:
            if case.requirements.vision and not profile.supports_vision:
                return self._planner_skip_result(profile, case)
            return await self._run_planner_case(profile, case_id)
        case, workspace, case_dir, workspace_bytes = load_case(self.root, case_id, self.source_root)
        base = {
            "caseId": case.id, "caseVersion": case.version, "title": case.title,
            "workspace": {"schemaVersion": workspace.schema_version, "sourceSha256": hashlib.sha256(workspace_bytes).hexdigest()},
            "humanReview": case.human_review, "reviewNotes": "", "referenceInputs": _reference_inputs(case, workspace, case_dir, self.source_root),
            "_expectations": case.expectations,
        }
        if case.requirements.vision and not profile.supports_vision:
            base.pop("_expectations")
            return {**base, "status": "skipped", "generationTransport": "non_streaming", "skip": {"code": "vision_capability_mismatch", "message": "Case requires vision but selected profile does not declare vision support."}}
        settings = profile.to_settings()
        try:
            dialogue_evidence = None
            if workspace.dialogue_language.value != "as_written":
                source_dialogue = workspace.dialogue
                resolution = await resolve_dialogue(workspace, settings, self.client_factory)
                resolved_workspace = workspace.model_copy(update={"dialogue_resolution": resolution})
                cached_resolution = await resolve_dialogue(resolved_workspace, settings, self.client_factory)
                workspace = resolved_workspace
                dialogue_evidence = {
                    "sourceDialogue": source_dialogue,
                    "resolvedDialogue": resolution.resolved_text if resolution else "",
                    "targetLanguage": workspace.dialogue_language.value,
                    "resolverInvoked": resolution is not None,
                    "cacheReused": cached_resolution == resolution,
                }
            request = GenerationRequest(workspace=workspace.model_copy(update={"variations": 1}), llm=settings, resolvedSeeds=[1])
            visual_files = {}
            if case.h3_project is not None:
                request = resolve_h3_fixture(request, case.h3_project, visual_files)
                if visual_files:
                    base["visualHandoff"] = request._visual_handoff.summary()
                bundle_files, bundle_manifest = _manual_render_inputs(case, case_dir, self.source_root, request, visual_files)
                base["_manualRenderFiles"] = bundle_files
                base["_manualRenderManifest"] = bundle_manifest
            # This is the same outgoing preparation used by OpenAICompatibleClient.
            assembled = assemble_prompt(workspace_for_h3(request.prompt_workspace), settings.supports_vision,
                                        request.narrative_subjects, request.continuity_context)
            expected_payload, _ = build_generation_payload(settings.model_copy(update={"seed": 1}), assembled.messages, stream=False)
            generated = await generate_workspace_prompts(request, self.client_factory)
        except LLMBackendError as exc:
            base.pop("_expectations")
            error_result = {**base, "status": "error", "generationTransport": "non_streaming", "error": {"type": "provider", "message": str(exc), "statusCode": exc.status_code, "upstreamStatus": exc.upstream_status}}
            if dialogue_evidence is not None:
                error_result["dialogueResolution"] = dialogue_evidence
            return error_result
        completed = self._completed_case(base, generated, expected_payload, _workspace_image_mappings(case, workspace, h3_derivative=settings.supports_vision))
        if case.h3_project is not None:
            text = generated.assembled.inspector_text
            checks = [{"kind": "model_input_contains", "text": value, "passed": value in text}
                      for value in case.h3_project.input_contains]
            checks += [{"kind": "model_input_absent", "text": value, "passed": value not in text}
                       for value in case.h3_project.input_absent]
            completed["expectations"]["checks"].extend(checks)
            completed["expectations"]["passed"] &= all(check["passed"] for check in checks)
            relationship = case.h3_project.expected_task_relationship
            if relationship:
                prompt = completed["rawModelOutput"].lower()
                has_continuation = any(asset.kind == AssetKind.VIDEO and asset.role in {"continuation_source", "tail_reference_continuation"}
                                       for asset, _ in numbered_assets(request.prompt_workspace))
                task_checks = [
                    {"kind": "task_relationship_equals", "text": relationship, "passed": relationship in prompt},
                    {"kind": "endpoint_without_video_continuation", "passed": has_continuation or "video continuation" not in prompt},
                ]
                completed["expectations"]["checks"].extend(task_checks)
                completed["expectations"]["passed"] &= all(check["passed"] for check in task_checks)
        if dialogue_evidence is not None:
            completed["dialogueResolution"] = dialogue_evidence
        return completed

    def _planner_skip_result(
        self,
        profile: BenchmarkProfile,
        case: BenchmarkCase,
        *,
        settings: LLMSettings | None = None,
    ) -> dict[str, Any]:
        """Return a capability skip before resolving planner fixtures or creating a client."""
        planner = case.planner
        operation = planner.operation if planner is not None else case.operation
        settings = settings or self._planner_settings(profile)
        return {
            "caseId": case.id,
            "caseVersion": case.version,
            "title": case.title,
            "operation": operation,
            "humanReview": case.human_review,
            "reviewNotes": "",
            "status": "skipped",
            "generationTransport": "non_streaming",
            "settings": {
                "temperature": settings.temperature,
                "seedMode": settings.seed_mode.value,
                "fixedSeed": settings.fixed_seed,
                "variations": 1,
                "reasoning": settings.reasoning_mode.value,
                "transport": "non_streaming",
            },
            "referenceInputs": [],
            "skip": {
                "code": "vision_capability_mismatch",
                "message": "Case requires vision but selected profile does not declare vision support.",
            },
        }

    def _planner_settings(self, profile: BenchmarkProfile) -> LLMSettings:
        # Planner benchmarks use the canonical harness settings even if a local
        # profile was copied from an interactive configuration with reasoning on.
        return profile.to_settings().model_copy(update={
            "temperature": 0.4,
            "seed_mode": SeedMode.FIXED,
            "fixed_seed": 1,
            "seed": 1,
            "reasoning_mode": ReasoningMode.OFF,
        })

    def _story_settings(self, profile: BenchmarkProfile) -> LLMSettings:
        """Return controlled Story benchmark settings with profile reasoning preserved."""
        return profile.to_settings().model_copy(update={
            "temperature": 0.4,
            "seed_mode": SeedMode.FIXED,
            "fixed_seed": 1,
            "seed": 1,
            "reasoning_mode": profile.reasoning_mode,
        })

    @staticmethod
    def _planner_document(workspace: Workspace, fixture: PlannerGenerationFixture, ordinal: int, prefix: str) -> GenerationDocument:
        return GenerationDocument(
            generation_id=f"benchmark-{prefix}-{ordinal}", ordinal=ordinal, workspace=workspace,
            incoming_state=fixture.incoming_state or "", intended_outgoing_state=fixture.intended_outgoing_state or "",
            planning=fixture.planning,
        )

    @staticmethod
    def _planner_request(workspace: Workspace, fixture: PlannerGenerationFixture, planner: PlannerFixture, settings: LLMSettings) -> PlanningInferenceRequest:
        return PlanningInferenceRequest(
            workspace=workspace,
            llm=settings,
            continuity=ContinuityWriteRequest(
                sequenceContext=planner.sequence_context,
                incomingState=fixture.incoming_state,
                intendedOutgoingState=fixture.intended_outgoing_state,
            ),
            planning=fixture.planning,
        )

    @staticmethod
    def _planner_reference_inputs(
        case: BenchmarkCase, previous: Workspace | None, current: Workspace,
        case_dir: Path, source_root: Path | None,
    ) -> list[dict[str, Any]]:
        fixtures = {fixture.asset_id: fixture for fixture in case.fixture_assets}
        result: list[dict[str, Any]] = []
        for source, workspace in (("previous_generation", previous), ("current_generation", current)):
            if workspace is None:
                continue
            for asset in workspace.assets:
                entry: dict[str, Any] = {
                    "source": source, "assetId": asset.id, "kind": asset.kind.value,
                    "filename": asset.filename, "mimeType": asset.mime_type, "role": asset.role,
                    "attached": asset.attached,
                }
                if asset.id in fixtures:
                    fixture = fixtures[asset.id]
                    entry["fixturePath"] = fixture.path
                    entry["fixtureSha256"] = hashlib.sha256(
                        _fixture_path(case, fixture, case_dir, source_root).read_bytes()
                    ).hexdigest()
                    if fixture.source == "external":
                        entry["fixtureSource"] = "external"
                result.append(entry)
        return result

    async def _run_planner_case(self, profile: BenchmarkProfile, case_id: str) -> dict[str, Any]:
        case, current, previous, case_dir, workspace_bytes = load_planner_case(
            self.root, case_id, self.source_root,
        )
        assert case.planner is not None
        planner = case.planner
        operation = planner.operation
        settings = self._planner_settings(profile)
        base: dict[str, Any] = {
            "caseId": case.id, "caseVersion": case.version, "title": case.title,
            "operation": operation,
            "workspace": {"schemaVersion": current.schema_version, "sourceSha256": hashlib.sha256(workspace_bytes).hexdigest()},
            "humanReview": case.human_review, "reviewNotes": "",
            "referenceInputs": self._planner_reference_inputs(
                case, previous, current, case_dir, self.source_root,
            ),
            "settings": {
                "temperature": settings.temperature, "seedMode": settings.seed_mode.value,
                "fixedSeed": settings.fixed_seed, "variations": 1,
                "reasoning": settings.reasoning_mode.value, "transport": "non_streaming",
            },
        }
        if planner.operation == "infer_plan":
            assert planner.previous_generation is not None and previous is not None
            previous_document = self._planner_document(previous, planner.previous_generation, 1, case.id)
            request = self._planner_request(current, planner.current_generation, planner, settings)
            assembly = planning_service.assemble_inference_prompt(previous_document, request, supports_vision=settings.supports_vision)
            allowed_pairs = assembly.allowed_asset_refs
            invoke = lambda factory: planning_service.infer_plan(previous_document, request, factory)
        else:
            inference_request = self._planner_request(current, planner.current_generation, planner, settings)
            request = PlanningRevisionRequest(
                workspace=inference_request.workspace,
                llm=inference_request.llm,
                continuity=inference_request.continuity,
                planning=inference_request.planning,
                instruction=planner.revision.instruction,
                scopes=planner.revision.editable_scopes,
            )
            assembly = planning_service.assemble_revision_plan_prompt(request)
            allowed_pairs = assembly.allowed_asset_refs
            invoke = lambda factory: planning_service.revise_plan(request, factory)

        expected_payload, _ = build_generation_payload(
            settings, assembly.messages, stream=False, response_instruction=None,
        )
        image_mappings = _workspace_image_mappings(case, current, h3_derivative=settings.story_vision_enabled)
        if previous is not None:
            previous_case = case.model_copy(update={"fixture_assets": case.fixture_assets})
            image_mappings = _workspace_image_mappings(previous_case, previous, h3_derivative=settings.story_vision_enabled) | image_mappings
        captured: list[Any] = []
        raw_outputs: list[str] = []

        from .recording import RecordingClient

        def client_factory(client_settings: LLMSettings) -> Any:
            client = RecordingClient(self.client_factory(client_settings), raw_outputs)
            captured.append(client)
            return client

        started = time.perf_counter()
        try:
            plan = await invoke(client_factory)
        except LLMBackendError as exc:
            base.update({
                "status": "error", "modelInput": _messages_provenance(expected_payload["messages"], image_mappings),
                "effectiveRequest": _effective_request_parameters(expected_payload),
                "latencyMs": round((time.perf_counter() - started) * 1000, 2),
                "rawModelOutput": raw_outputs[-1] if raw_outputs else None,
                "error": {"type": "provider_or_validation", "message": str(exc), "statusCode": exc.status_code, "upstreamStatus": exc.upstream_status},
            })
            return base

        machine_checks = evaluate_planner_checks(
            case.expectations.checks, plan, request, allowed_pairs,
            _raw_recommendation_pairs(raw_outputs[-1] if raw_outputs else None),
            raw_outputs[-1] if raw_outputs else None,
        )
        base.update({
            "status": "completed", "modelInput": _messages_provenance(expected_payload["messages"], image_mappings),
            "effectiveRequest": _effective_request_parameters(expected_payload),
            "structuredPlannerOutput": plan.model_dump(mode="json", by_alias=True),
            "rawModelOutput": raw_outputs[-1] if raw_outputs else None,
            "machineChecks": {"checks": machine_checks, "passed": all(item["passed"] for item in machine_checks)},
            "expectations": {"checks": machine_checks, "passed": all(item["passed"] for item in machine_checks)},
            "latencyMs": round((time.perf_counter() - started) * 1000, 2),
            "provider": {
                "usage": getattr(captured[-1], "last_usage", {}) if captured else {},
                "requestDurationMs": round((time.perf_counter() - started) * 1000, 2),
                "compatibilityFallback": bool(getattr(captured[-1], "compatibility_fallback", False)) if captured else False,
            },
        })
        return base

    def _completed_case(self, base: dict[str, Any], generated: ProductionGeneration, expected_payload: dict[str, Any], image_mappings: dict[str, list[dict[str, str]]]) -> dict[str, Any]:
        variation = generated.variations[0]
        payload = variation.model_payload or expected_payload
        findings = variation.generated.validation.findings
        finding_codes = {finding.code for finding in findings}
        # Raw model output is deliberately evaluated before any repair. There is no repair call here.
        expectations = base.pop("_expectations")
        checks = evaluate_expectations(expectations, variation.generated.prompt, finding_codes)
        final_request_includes_seed = "seed" in payload and not variation.seed_unsupported
        return {
            **base, "status": "completed", "generationTransport": "non_streaming", "warnings": generated.warnings,
            "modelInput": _messages_provenance(payload["messages"], image_mappings),
            "effectiveRequest": _effective_request_parameters(payload),
            "rawModelOutput": variation.generated.prompt,
            "normalizedOutput": None,
            "validation": variation.generated.validation.model_dump(mode="json"),
            "structuralValid": variation.generated.validation.valid,
            "seed": _seed_provenance(
                requested_seed=variation.requested_seed, mode="fixed", effective_seed=variation.effective_seed,
                seed_unsupported=variation.seed_unsupported, known_unsupported=variation.seed_known_unsupported,
                final_request_included_seed=final_request_includes_seed,
            ),
            "provider": {"compatibilityFallback": variation.compatibility_fallback, "usage": variation.usage or None,
                         "requestDurationMs": variation.request_duration_ms},
            "expectations": {"checks": checks, "passed": all(check["passed"] for check in checks)},
        }


def write_run(root: Path, result: dict[str, Any]) -> Path:
    directory = benchmark_root(root) / "runs" / result["runId"]
    cases_directory = directory / "cases"
    cases_directory.mkdir(parents=True, exist_ok=False)
    run_copy = dict(result)
    cases = run_copy.pop("cases")
    run_copy["caseFiles"] = [f"cases/{case['caseId']}.json" for case in cases]
    (directory / "run.json").write_text(json.dumps(run_copy, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lines = [f"# Benchmark run {result['runId']}", "", f"Profile: `{result['profile']['id']}`", "", "| Case | Status | Valid | Expectations |", "| --- | --- | --- | --- |"]
    for case in cases:
        case = dict(case)
        files = case.pop("_visualHandoffFiles", {})
        if files:
            media_dir = cases_directory / case["caseId"]
            media_dir.mkdir()
            case["visualHandoffMedia"] = []
            for filename, payload in files.items():
                if filename not in {"predecessor.mp4", "handoff.mp4", "handoff.jpg"}:
                    raise ValueError("Unknown benchmark handoff artifact")
                (media_dir / filename).write_bytes(payload)
                case["visualHandoffMedia"].append({"path": f"{case['caseId']}/{filename}",
                    "sha256": hashlib.sha256(payload).hexdigest(), "byteSize": len(payload)})
        manual_files = case.pop("_manualRenderFiles", {})
        manual_manifest = case.pop("_manualRenderManifest", None)
        if manual_files and manual_manifest:
            bundle_name = f"{case['caseId']}.manual-render.zip"
            bundle = cases_directory / bundle_name
            manifest = dict(manual_manifest)
            manifest["promptSha256"] = hashlib.sha256(case.get("rawModelOutput", "").encode("utf-8")).hexdigest()
            with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("prompt.txt", case.get("rawModelOutput", ""))
                archive.writestr("manual-render-manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
                for path, payload in sorted(manual_files.items()):
                    archive.writestr(path, payload)
            case["manualRenderBundle"] = {"path": bundle_name, "manifest": "manual-render-manifest.json",
                                          "referenceCount": len(manifest["references"])}
        (cases_directory / f"{case['caseId']}.json").write_text(json.dumps(case, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        validation = str(case.get("structuralValid", "—"))
        expectations = str(case.get("expectations", {}).get("passed", "—"))
        lines.append(f"| {case['caseId']} | {case['status']} | {validation} | {expectations} |")
        if case.get("humanReview"):
            lines.extend(["", f"## {case['caseId']} review questions", *[f"- {item}" for item in case["humanReview"]]])
    stress_cases = [case for case in cases if isinstance(case.get("stressSummary"), dict)]
    if stress_cases:
        lines.extend(["", "## Stateful Story stress summary"])
        for case in stress_cases:
            summary = case["stressSummary"]
            lines.extend([
                "", f"### {case['caseId']}",
                "| Phase | Status | Operation | Source | Local | Slots | Pictures | Prompt | Total | Prompt % | Total % | Request ms |",
                "| --- | --- | --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
            ])
            def stress_display(value):
                return "unavailable" if value is None else str(value)
            for phase in summary.get("phases", []):
                slots = f"{stress_display(phase.get('requestedSlots'))}/{stress_display(phase.get('returnedSlots'))}"
                lines.append("| {phase} | {status} | {operation} | {source} | {local} | {slots} | {pictures} | {prompt} | {total} | {prompt_pct} | {total_pct} | {duration} |".format(
                    phase=phase.get("phase", "unknown"), status=str(phase.get("status", "review")).upper(),
                    operation=stress_display(phase.get("operation")), source=stress_display(phase.get("canonicalSourceGenerationCount")),
                    local=stress_display(phase.get("localContextGenerationCount")), slots=slots,
                    pictures=stress_display(phase.get("pictures")), prompt=stress_display(phase.get("promptTokens")),
                    total=stress_display(phase.get("totalTokens")), prompt_pct=stress_display(phase.get("promptContextUtilization")),
                    total_pct=stress_display(phase.get("totalContextUtilization")), duration=stress_display(phase.get("requestDurationMs")),
                ))
            lines.append("- Completed phases: {completed}; failed phase: {failed}; final canonical Generations: {count}.".format(
                completed=", ".join(summary.get("completedPhases", [])) or "none",
                failed=summary.get("failedPhase") or "none",
                count=stress_display(summary.get("finalCanonicalGenerationCount")),
            ))
    story_cases = [case for case in cases if isinstance(case.get("storyMetrics"), dict)]
    if story_cases:
        lines.extend([
            "", "## Story performance", "",
            "| Case | Operation | Source | Desired/Draft | Response | Patches | Prompt | Completion | Total | Prompt % | Total % | Request ms | Pictures |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ])
        def display(value):
            return "unavailable" if value is None else str(value)
        for case in story_cases:
            metric = case["storyMetrics"]
            provider = case.get("provider") if isinstance(case.get("provider"), dict) else {}
            desired_or_draft = metric.get("desiredGenerationCount")
            if desired_or_draft is None:
                desired_or_draft = metric.get("requestDraftItemCount")
            lines.append("| {case} | {op} | {source} | {requested} | {response} | {patches} | {prompt} | {completion} | {total} | {prompt_pct} | {total_pct} | {duration} | {pictures} |".format(
                case=case["caseId"], op=display(metric.get("operation")), source=display(metric.get("sourceGenerationCount", metric.get("generationCount"))),
                requested=display(desired_or_draft), response=display(metric.get("responseItemCount")),
                patches=display(metric.get("responsePatchCount")),
                prompt=display(metric.get("providerPromptTokens")), completion=display(metric.get("providerCompletionTokens")),
                total=display(metric.get("providerTotalTokens")), prompt_pct=display(metric.get("promptContextUtilization")),
                total_pct=display(metric.get("totalContextUtilization")), duration=display(provider.get("requestDurationMs")),
                pictures=display(metric.get("sourcePictureInputCount")),
            ))
    behavioral_cases = [case for case in cases if case.get("behavioralChecks")]
    if behavioral_cases:
        lines.extend(["", "## Story behavioral evidence"])
        behavioral_counts = {status: 0 for status in ("pass", "fail", "review")}
        for case in behavioral_cases:
            for check in case["behavioralChecks"]:
                status = str(check.get("status", "review")).lower()
                behavioral_counts[status] = behavioral_counts.get(status, 0) + 1
        lines.append("Behavioral checks: PASS={pass_count} | FAIL={fail_count} | REVIEW={review_count}.".format(
            pass_count=behavioral_counts.get("pass", 0),
            fail_count=behavioral_counts.get("fail", 0),
            review_count=behavioral_counts.get("review", 0),
        ))
        for case in behavioral_cases:
            lines.extend(["", f"### {case['caseId']}"])
            lines.append(f"- Operation: `{case.get('operation', 'unknown')}`; structural status: `{case.get('structuralValid')}`.")
            for check in case["behavioralChecks"]:
                evidence = check.get("evidence", {})
                excerpt = evidence.get("generatedProse", "") if isinstance(evidence, dict) else ""
                lines.append(f"- [{str(check.get('status', 'review')).upper()}] {check.get('description', '')}")
                if excerpt:
                    lines.append(f"  - Generated evidence: {excerpt.replace(chr(10), ' ')}")
            memory = case.get("memoryHygieneMetrics")
            if isinstance(memory, dict):
                before, after = memory.get("before", {}), memory.get("after", {})
                lines.append("- Memory: facts {before_facts} → {after_facts}; threads {before_threads} → {after_threads}; suspicious normalized duplicates: {duplicates}.".format(
                    before_facts=before.get("factCount", "unavailable"), after_facts=after.get("factCount", "unavailable"),
                    before_threads=before.get("threadCount", "unavailable"), after_threads=after.get("threadCount", "unavailable"),
                    duplicates=memory.get("suspiciousDuplicateCount", "unavailable"),
                ))
            if isinstance(case.get("storyMemoryBefore"), dict):
                projected = case.get("projectedStoryMemoryAfter", [])
                lines.extend([
                    "<details><summary>Story Memory before and projected after each slot</summary>", "",
                    "```json", json.dumps({
                        "before": case["storyMemoryBefore"], "afterEachSlot": projected,
                    }, ensure_ascii=False, indent=2), "```", "</details>",
                ])
            references = case.get("referenceMetrics")
            if isinstance(references, dict):
                lines.append("- References: historical semantic records {historical}; transmitted unique Picture payloads {pictures}; recommended pairs {recommended}.".format(
                    historical=references.get("historicalReferenceSemanticCount", 0),
                    pictures=references.get("transmittedUniquePicturePayloadCount", 0),
                    recommended=references.get("recommendedReferencePairCount", 0),
                ))
                lines.extend([
                    "<details><summary>Relevant references offered and recommended</summary>", "",
                    "```json", json.dumps({
                        "relevantSubjects": references.get("relevantSubjects", []),
                        "historicalCandidates": references.get("historicalReferenceCandidates", []),
                        "offeredPairs": references.get("allowedReferencePairs", []),
                        "recommendedPairs": references.get("recommendedReferencePairs", []),
                    }, ensure_ascii=False, indent=2), "```", "</details>",
                ])
            lines.append("- Full raw response, parsed proposal, memory snapshots, reference pairs, and provider usage are in this case's JSON result.")
    (directory / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return directory


def compare_runs(left: Path, right: Path) -> str:
    def read_cases(directory: Path) -> dict[str, dict[str, Any]]:
        return {path.stem: _read_json(path) for path in (directory / "cases").glob("*.json")}
    old, new = read_cases(left), read_cases(right)
    lines = [f"# Benchmark comparison", "", f"Old: `{left}`", f"New: `{right}`", ""]
    for case_id in sorted(set(old) | set(new)):
        if case_id not in old or case_id not in new:
            lines.append(f"- {case_id}: {'added' if case_id in new else 'removed'}")
            continue
        before, after = old[case_id], new[case_id]
        changes = []
        for key, label in (("status", "status"), ("structuralValid", "validity"), ("rawModelOutput", "output")):
            if before.get(key) != after.get(key): changes.append(label)
        before_validation = before.get("validation") if isinstance(before.get("validation"), dict) else {}
        after_validation = after.get("validation") if isinstance(after.get("validation"), dict) else {}
        before_expectations = before.get("expectations") if isinstance(before.get("expectations"), dict) else {}
        after_expectations = after.get("expectations") if isinstance(after.get("expectations"), dict) else {}
        before_provider = before.get("provider") if isinstance(before.get("provider"), dict) else {}
        after_provider = after.get("provider") if isinstance(after.get("provider"), dict) else {}
        if before_validation.get("findings") != after_validation.get("findings"): changes.append("validator findings")
        if before_expectations.get("passed") != after_expectations.get("passed"): changes.append("expectations")
        if before_provider.get("usage") != after_provider.get("usage"): changes.append("usage")
        if before_provider.get("requestDurationMs") != after_provider.get("requestDurationMs"): changes.append("latency")
        before_metrics, after_metrics = before.get("storyMetrics"), after.get("storyMetrics")
        if isinstance(before_metrics, dict) or isinstance(after_metrics, dict):
            metric_changes = []
            for key, label in (
                ("providerPromptTokens", "prompt tokens"),
                ("providerCompletionTokens", "completion tokens"),
                ("providerTotalTokens", "total tokens"),
                ("promptContextUtilization", "context utilization"),
                ("totalContextUtilization", "total context utilization"),
                ("sourceGenerationCount", "source Generations"),
                ("desiredGenerationCount", "desired Generations"),
                ("requestDraftItemCount", "draft items"),
                ("sourcePictureInputCount", "transmitted images"),
                ("responseCharacters", "response size"),
                ("responseItemCount", "response item count"),
                ("responsePatchCount", "response patch count"),
            ):
                old_value = before_metrics.get(key) if isinstance(before_metrics, dict) else None
                new_value = after_metrics.get(key) if isinstance(after_metrics, dict) else None
                if old_value != new_value:
                    metric_changes.append(f"{label} {old_value!r} → {new_value!r}")
            if metric_changes:
                changes.extend(metric_changes)
        lines.append(f"- {case_id}: {', '.join(changes) if changes else 'unchanged'}")
    return "\n".join(lines) + "\n"
