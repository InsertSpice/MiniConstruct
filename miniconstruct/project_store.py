from __future__ import annotations

import base64
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone, timedelta
import hashlib
import json
import logging
import os
import re
from pathlib import Path
import shutil
import uuid
import time
from functools import wraps
from contextlib import contextmanager, ExitStack
from threading import RLock
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator, model_serializer

from miniconstruct.models.sequence_subject import (
    CharacterProfile, SequenceSubject, validate_cast_names, validate_narrative_name,
    narrative_key, local_narrative_subjects,
)
from miniconstruct.models.planning import GenerationPlanning
from miniconstruct.models.workspace import (
    Workspace, MediaPayload, SubjectRecord, DialogueLanguage, structural_subject_ids, validate_authoring_workspace,
)

from miniconstruct.models.story_memory import StoryContinuityMemory
from miniconstruct.models.story_creative_direction import StoryCreativeDirection
from miniconstruct.models.story_planning_style import StoryPlanningStyle
from miniconstruct.dialogue_language import identity_dialogue_resolution
from miniconstruct.models.takes import Take, RenderSubmission
from miniconstruct.take_store import TakeStoreMixin
from miniconstruct.observation_store import ObservationStoreMixin
from miniconstruct.visual_handoff import VisualHandoffStoreMixin
from miniconstruct.native_continuation import NativeContinuationStoreMixin
from miniconstruct.models.observations import TakeObservation, observation_projection


PROJECT_SCHEMA_VERSION = 17
SUPPORTED_PROJECT_SCHEMA_VERSIONS = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17)
PROJECTS_ROOT_ENV = "MINICONSTRUCT_PROJECTS_ROOT"
_PROJECT_DIRECTORY_PREFIX = "project-"
_MIGRATED_GENERATION_NAME = "initial-generation"
_LOG = logging.getLogger(__name__)

# All server instances share canonical transactions. Send also holds this lock
# across its final authority check and /prompt acceptance. No model inference,
# Reference hashing or bulk upload is performed there for content-addressed inputs.
_PROJECT_TRANSACTION_LOCK = RLock()


def _project_transaction(method):
    @wraps(method)
    def locked(*args, **kwargs):
        with _PROJECT_TRANSACTION_LOCK:
            return method(*args, **kwargs)
    return locked


class ProjectStoreError(Exception):
    """Base error for filesystem project storage failures."""


class ProjectNotFoundError(ProjectStoreError):
    pass


class StaleProjectSnapshotError(ProjectStoreError):
    def __init__(self, message: str, *, category: str | None = None):
        super().__init__(message)
        self.category = category


class UnsupportedProjectSchemaError(ProjectStoreError):
    pass


class MalformedProjectError(ProjectStoreError):
    pass


class AssetPayloadError(ProjectStoreError):
    pass


class GenerationNotFoundError(ProjectStoreError):
    pass


class InvalidGenerationOrderError(ProjectStoreError):
    pass


class InvalidGenerationInsertionError(ProjectStoreError):
    pass


class FinalGenerationDeletionError(ProjectStoreError):
    pass


class GenerationNotActiveError(ProjectStoreError):
    pass


class SequenceSubjectInUseError(ProjectStoreError):
    pass


def _canonical_uuid(value: str, label: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"{label} must be a UUID") from exc


class AssetFileManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    asset_id: str = Field(alias="assetId", min_length=1, max_length=128)
    kind: Literal["image", "audio", "video"]
    path: str = Field(min_length=1, max_length=300)
    mime_type: str = Field(alias="mimeType", min_length=1, max_length=128)
    content_sha256: str | None = Field(default=None, alias="contentSha256", pattern=r"^[0-9a-f]{64}$")
    byte_length: int | None = Field(default=None, alias="byteLength", gt=0, le=512 * 1024 * 1024)
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)


    @model_serializer(mode="wrap")
    def serialize_manifest(self, handler):
        data = handler(self)
        # Keep legacy Picture manifest serialization (and Story snapshot identity).
        for key in ("contentSha256", "content_sha256", "byteLength", "byte_length"):
            if data.get(key) is None:
                data.pop(key, None)
        return data


class GenerationContinuityManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    incoming_state: str = Field(default="", alias="incomingState", max_length=20000)
    intended_outgoing_state: str = Field(default="", alias="intendedOutgoingState", max_length=20000)


class GenerationManifestV8(BaseModel):
    """One ordered authoring snapshot; array position is its only ordinal."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    generation_id: str = Field(alias="generationId", min_length=1, max_length=128)
    created_at: str = Field(alias="createdAt", min_length=1, max_length=100)
    updated_at: str = Field(alias="updatedAt", min_length=1, max_length=100)
    workspace: dict[str, Any]
    asset_files: list[AssetFileManifest] = Field(default_factory=list, alias="assetFiles")
    continuity: GenerationContinuityManifest = Field(default_factory=GenerationContinuityManifest)
    planning: GenerationPlanning = Field(default_factory=GenerationPlanning)

    @field_validator("generation_id")
    @classmethod
    def validate_generation_id(cls, value: str) -> str:
        return _canonical_uuid(value, "generationId")

    @model_validator(mode="after")
    def validate_asset_entries(self) -> "GenerationManifestV8":
        asset_ids = [entry.asset_id for entry in self.asset_files]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("assetFiles contains duplicate asset IDs")
        return self


class SequenceManifestV4(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    active_generation_id: str = Field(alias="activeGenerationId", min_length=1, max_length=128)
    generations: list[GenerationManifestV8] = Field(min_length=1)
    continuity_context: str = Field(default="", alias="continuityContext", max_length=20000)

    @field_validator("active_generation_id")
    @classmethod
    def validate_active_generation_id(cls, value: str) -> str:
        return _canonical_uuid(value, "activeGenerationId")

    @model_validator(mode="after")
    def validate_sequence(self) -> "SequenceManifestV4":
        generation_ids = [generation.generation_id for generation in self.generations]
        if len(generation_ids) != len(set(generation_ids)):
            raise ValueError("sequence contains duplicate generation IDs")
        if self.active_generation_id not in generation_ids:
            raise ValueError("activeGenerationId does not reference a Generation")
        return self


class SequenceManifestV6(SequenceManifestV4):
    """Sequence identity authority; Generation registries are local subsets."""

    subjects: list[SubjectRecord]
    next_subject_number: int = Field(alias="nextSubjectNumber", ge=1)

    @model_validator(mode="after")
    def validate_subjects(self) -> "SequenceManifestV6":
        if len({s.id for s in self.subjects}) != len(self.subjects):
            raise ValueError("Sequence Subject IDs must be unique")
        if len({s.number for s in self.subjects}) != len(self.subjects):
            raise ValueError("Sequence Subject numbers must be unique")
        if any(s.number >= self.next_subject_number for s in self.subjects):
            raise ValueError("Sequence nextSubjectNumber must exceed every Subject number")
        return self


class SequenceManifestV7(SequenceManifestV6):
    subjects: list[SequenceSubject]

    @field_validator("subjects", mode="before")
    @classmethod
    def promote_structural_records(cls, values):
        return [value.model_dump(by_alias=True) if isinstance(value, SubjectRecord) else value for value in values]

    @model_validator(mode="after")
    def validate_narrative_cast(self):
        validate_cast_names(self.subjects)
        return self


class SequenceManifestV8(SequenceManifestV7):
    story_memory: StoryContinuityMemory = Field(default_factory=StoryContinuityMemory, alias="storyMemory")

    @model_validator(mode="after")
    def validate_memory_links(self):
        self.story_memory.validate_subjects({s.id for s in self.subjects})
        return self


class GenerationManifest(GenerationManifestV8):
    story_memory_after: StoryContinuityMemory = Field(default_factory=StoryContinuityMemory, alias="storyMemoryAfter")
    story_memory_status: Literal["trusted", "needs_review", "legacy_unknown"] = Field(default="trusted", alias="storyMemoryStatus")
    render_submissions: list[RenderSubmission] = Field(default_factory=list, alias="renderSubmissions")
    takes: list[Take] = Field(default_factory=list)
    take_observations: list[TakeObservation] = Field(default_factory=list, alias="takeObservations", max_length=1000)
    selected_take_id: uuid.UUID | None = Field(default=None, alias="selectedTakeId")

    @model_validator(mode="after")
    def validate_outputs(self):
        take_ids = [take.id for take in self.takes]
        submissions = {s.id: s for s in self.render_submissions}
        if len(take_ids) != len(set(take_ids)) or len(submissions) != len(self.render_submissions):
            raise ValueError("Output IDs must be unique within a Generation")
        if self.selected_take_id is not None and self.selected_take_id not in take_ids:
            raise ValueError("selectedTakeId must refer to a Take owned by this Generation")
        observation_ids = [o.take_id for o in self.take_observations]
        if len(observation_ids) != len(set(observation_ids)):
            raise ValueError("Duplicate Take observations")
        for observation in self.take_observations:
            if not any(t.id == observation.take_id and t.sha256 == observation.take_sha256 for t in self.takes):
                raise ValueError("Observation must bind to an owned Take UUID and SHA-256")
        for take in self.takes:
            if take.render_submission_id is not None:
                submission = submissions.get(take.render_submission_id)
                if submission is None or submission.prompt_snapshot != take.prompt_snapshot:
                    raise ValueError("Take must retain its Generation-owned Submission provenance")
        return self


class SequenceManifest(SequenceManifestV7):
    generations: list[GenerationManifest] = Field(min_length=1)
    # Project-level authority. Workspace.dialogueLanguage is retained only as an
    # effective transport projection for standalone compatibility.
    dialogue_language: DialogueLanguage = Field(default=DialogueLanguage.AS_WRITTEN, alias="dialogueLanguage")

    @model_validator(mode="after")
    def validate_memory_links(self):
        for generation in self.generations:
            generation.story_memory_after.validate_subjects({s.id for s in self.subjects})
        return self


class ProjectManifestV1(BaseModel):
    """The binary-free v1 manifest retained for explicit compatibility."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal[1] = Field(alias="schemaVersion")
    project_id: str = Field(alias="projectId", min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=160)
    created_at: str = Field(alias="createdAt", min_length=1, max_length=100)
    updated_at: str = Field(alias="updatedAt", min_length=1, max_length=100)
    workspace: dict[str, Any]
    asset_files: list[AssetFileManifest] = Field(default_factory=list, alias="assetFiles")

    @field_validator("project_id")
    @classmethod
    def validate_project_id(cls, value: str) -> str:
        return _canonical_uuid(value, "projectId")

    @model_validator(mode="after")
    def validate_asset_entries(self) -> "ProjectManifestV1":
        asset_ids = [entry.asset_id for entry in self.asset_files]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("assetFiles contains duplicate asset IDs")
        return self


class SequenceManifestV2(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    active_generation_id: str = Field(alias="activeGenerationId", min_length=1, max_length=128)
    generations: list["GenerationManifestV2"] = Field(min_length=1)

    @field_validator("active_generation_id")
    @classmethod
    def validate_active_generation_id(cls, value: str) -> str:
        return _canonical_uuid(value, "activeGenerationId")

    @model_validator(mode="after")
    def validate_sequence(self) -> "SequenceManifestV2":
        generation_ids = [generation.generation_id for generation in self.generations]
        if len(generation_ids) != len(set(generation_ids)):
            raise ValueError("sequence contains duplicate generation IDs")
        if self.active_generation_id not in generation_ids:
            raise ValueError("activeGenerationId does not reference a Generation")
        return self


class GenerationManifestV2(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    generation_id: str = Field(alias="generationId", min_length=1, max_length=128)
    created_at: str = Field(alias="createdAt", min_length=1, max_length=100)
    updated_at: str = Field(alias="updatedAt", min_length=1, max_length=100)
    workspace: dict[str, Any]
    asset_files: list[AssetFileManifest] = Field(default_factory=list, alias="assetFiles")

    @field_validator("generation_id")
    @classmethod
    def validate_generation_id(cls, value: str) -> str:
        return _canonical_uuid(value, "generationId")

    @model_validator(mode="after")
    def validate_asset_entries(self) -> "GenerationManifestV2":
        asset_ids = [entry.asset_id for entry in self.asset_files]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("assetFiles contains duplicate asset IDs")
        return self


SequenceManifestV2.model_rebuild()


class ProjectManifestV2(BaseModel):
    """The schema-v2 project manifest retained for explicit compatibility."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal[2] = Field(alias="schemaVersion")
    project_id: str = Field(alias="projectId", min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=160)
    created_at: str = Field(alias="createdAt", min_length=1, max_length=100)
    updated_at: str = Field(alias="updatedAt", min_length=1, max_length=100)
    sequence: SequenceManifestV2

    @field_validator("project_id")
    @classmethod
    def validate_project_id(cls, value: str) -> str:
        return _canonical_uuid(value, "projectId")


class GenerationManifestV3(BaseModel):
    """Frozen schema-v3 Generation shape; planning did not exist yet."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    generation_id: str = Field(alias="generationId", min_length=1, max_length=128)
    created_at: str = Field(alias="createdAt", min_length=1, max_length=100)
    updated_at: str = Field(alias="updatedAt", min_length=1, max_length=100)
    workspace: dict[str, Any]
    asset_files: list[AssetFileManifest] = Field(default_factory=list, alias="assetFiles")
    continuity: GenerationContinuityManifest = Field(default_factory=GenerationContinuityManifest)

    @field_validator("generation_id")
    @classmethod
    def validate_generation_id(cls, value: str) -> str:
        return _canonical_uuid(value, "generationId")

    @model_validator(mode="after")
    def validate_asset_entries(self) -> "GenerationManifestV3":
        asset_ids = [entry.asset_id for entry in self.asset_files]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("assetFiles contains duplicate asset IDs")
        return self


class SequenceManifestV3(BaseModel):
    """Frozen schema-v3 Sequence shape."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    active_generation_id: str = Field(alias="activeGenerationId", min_length=1, max_length=128)
    generations: list[GenerationManifestV3] = Field(min_length=1)
    continuity_context: str = Field(default="", alias="continuityContext", max_length=20000)

    @field_validator("active_generation_id")
    @classmethod
    def validate_active_generation_id(cls, value: str) -> str:
        return _canonical_uuid(value, "activeGenerationId")

    @model_validator(mode="after")
    def validate_sequence(self) -> "SequenceManifestV3":
        generation_ids = [generation.generation_id for generation in self.generations]
        if len(generation_ids) != len(set(generation_ids)):
            raise ValueError("sequence contains duplicate generation IDs")
        if self.active_generation_id not in generation_ids:
            raise ValueError("activeGenerationId does not reference a Generation")
        return self


class ProjectManifestV3(BaseModel):
    """The schema-v3 project manifest retained for explicit compatibility."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal[3] = Field(alias="schemaVersion")
    project_id: str = Field(alias="projectId", min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=160)
    created_at: str = Field(alias="createdAt", min_length=1, max_length=100)
    updated_at: str = Field(alias="updatedAt", min_length=1, max_length=100)
    sequence: SequenceManifestV3

    @field_validator("project_id")
    @classmethod
    def validate_project_id(cls, value: str) -> str:
        return _canonical_uuid(value, "projectId")


class ProjectManifestV4(BaseModel):
    """Frozen binary-free project.json contract for schema version 4."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal[4] = Field(alias="schemaVersion")
    project_id: str = Field(alias="projectId", min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=160)
    created_at: str = Field(alias="createdAt", min_length=1, max_length=100)
    updated_at: str = Field(alias="updatedAt", min_length=1, max_length=100)
    sequence: SequenceManifestV4

    @field_validator("project_id")
    @classmethod
    def validate_project_id(cls, value: str) -> str:
        return _canonical_uuid(value, "projectId")


class ProjectManifestV5(ProjectManifestV4):
    """Frozen schema-v5 contract, before Sequence Subject authority."""

    schema_version: Literal[5] = Field(alias="schemaVersion")
    story_idea: str = Field(default="", alias="storyIdea", max_length=20000)


class ProjectManifestV6(ProjectManifestV5):
    """Canonical v6 manifest with a persistent Sequence cast."""

    schema_version: Literal[6] = Field(alias="schemaVersion")
    sequence: SequenceManifestV6


class ProjectManifestV7(ProjectManifestV6):
    """Canonical v7 manifest; narrative identity belongs only to its Sequence."""
    schema_version: Literal[7] = Field(alias="schemaVersion")
    sequence: SequenceManifestV7


class ProjectManifestV8(ProjectManifestV7):
    """Frozen v8 Sequence-global memory adapter; Workspace schema is unchanged."""
    schema_version: Literal[8] = Field(alias="schemaVersion")
    sequence: SequenceManifestV8


class ProjectManifestV9GenerationOwned(ProjectManifestV7):
    """Frozen Generation-owned v9 compatibility contract."""
    schema_version: Literal[9] = Field(alias="schemaVersion")
    sequence: SequenceManifest


class TemporalCheckpointV9(BaseModel):
    """Frozen read-only checkpoint input from the abandoned temporal v9 format."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str | None = None
    effective_after_generation_id: str = Field(alias="effectiveAfterGenerationId")
    memory_snapshot: StoryContinuityMemory = Field(alias="memorySnapshot")

    @field_validator("effective_after_generation_id")
    @classmethod
    def validate_generation_id(cls, value: str) -> str:
        return _canonical_uuid(value, "effectiveAfterGenerationId")


class TemporalSegmentV9(BaseModel):
    """Frozen migration-only segment. Events are deliberately not runtime authority."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str | None = None
    through_generation_id: str = Field(alias="throughGenerationId")
    checkpoints: list[TemporalCheckpointV9] = Field(min_length=1)
    # Old event records are retained only while reading the legacy file. Exact
    # checkpoints, not event replay, are the safe migration source.
    events: list[dict[str, Any]] = Field(default_factory=list)


class StoryMemoryTemporalV9(BaseModel):
    """Frozen compatibility model for the abandoned temporal-memory v9 shape."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    status: Literal["unavailable", "current", "needs_review"] = "unavailable"
    invalid_from_generation_id: str | None = Field(default=None, alias="invalidFromGenerationId")
    segments: list[TemporalSegmentV9] = Field(default_factory=list)


class SequenceManifestV9Temporal(SequenceManifestV8):
    """Read-only temporal-v9 adapter; never used for canonical writes."""

    story_memory_temporal: StoryMemoryTemporalV9 = Field(alias="storyMemoryTemporal")


class ProjectManifestV9Temporal(ProjectManifestV8):
    """Frozen abandoned temporal-v9 Project format, selected by structure."""

    schema_version: Literal[9] = Field(alias="schemaVersion")
    sequence: SequenceManifestV9Temporal


class ProjectManifestV10(ProjectManifestV7):
    """Canonical v10: each Generation owns the state true after it."""

    schema_version: Literal[10] = Field(alias="schemaVersion")
    sequence: SequenceManifest


class ProjectManifestV11(ProjectManifestV10):
    """Frozen v11 adds Project-owned soft Story creative guidance."""

    schema_version: Literal[11] = Field(alias="schemaVersion")
    story_creative_direction: StoryCreativeDirection = Field(
        default_factory=StoryCreativeDirection, alias="storyCreativeDirection",
    )


class ProjectManifestV12(ProjectManifestV11):
    """Canonical v12 adds stable Character Profiles to canonical Subjects."""

    schema_version: Literal[12] = Field(alias="schemaVersion")


class ProjectManifestV13(ProjectManifestV12):
    """Canonical v13 adds Generation-owned render submissions and actual Takes."""
    schema_version: Literal[13] = Field(alias="schemaVersion")


class ProjectManifestV14(ProjectManifestV13):
    """Canonical v14 adds separate per-Take observations."""
    schema_version: Literal[14] = Field(alias="schemaVersion")


class ProjectManifestV15(ProjectManifestV14):
    """Frozen v15 project shape."""
    schema_version: Literal[15] = Field(alias="schemaVersion")


class ProjectManifestV16(ProjectManifestV15):
    """Frozen v16 persists per-Generation Scene Plans in planning."""
    schema_version: Literal[16] = Field(alias="schemaVersion")


class ProjectManifest(ProjectManifestV16):
    """Canonical v17 adds Project-owned Story Planning Style."""
    schema_version: Literal[17] = Field(alias="schemaVersion")
    story_planning_style: StoryPlanningStyle = Field(
        default_factory=StoryPlanningStyle, alias="storyPlanningStyle",
    )


ProjectManifestAny = (ProjectManifestV1 | ProjectManifestV2 | ProjectManifestV3 | ProjectManifestV4 |
                      ProjectManifestV5 | ProjectManifestV6 | ProjectManifestV7 | ProjectManifestV8 |
                      ProjectManifestV9Temporal | ProjectManifestV9GenerationOwned | ProjectManifestV10 |
                      ProjectManifestV11 | ProjectManifestV12 | ProjectManifestV13 | ProjectManifestV14 |
                      ProjectManifestV15 | ProjectManifestV16 | ProjectManifest)


@dataclass(frozen=True)
class ProjectSummary:
    project_id: str
    name: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class GenerationSummary:
    generation_id: str
    created_at: str
    updated_at: str
    reference_assets: tuple[dict[str, str], ...] = ()
    story_memory_status: str = "trusted"


@dataclass(frozen=True)
class SequenceSummary:
    active_generation_id: str
    generations: tuple[GenerationSummary, ...]
    subjects: tuple[SequenceSubject, ...]
    next_subject_number: int


@dataclass(frozen=True)
class ProjectDocument(ProjectSummary):
    workspace: Workspace
    active_generation_id: str
    generation_count: int
    sequence: SequenceSummary
    sequence_context: str
    incoming_state: str
    intended_outgoing_state: str
    planning: GenerationPlanning
    story_idea: str = ""
    story_creative_direction: StoryCreativeDirection = field(default_factory=StoryCreativeDirection)
    story_planning_style: StoryPlanningStyle = field(default_factory=StoryPlanningStyle)
    story_memory_after: StoryContinuityMemory = field(default_factory=StoryContinuityMemory)
    project_fingerprint: str = ""
    dialogue_language: DialogueLanguage = DialogueLanguage.AS_WRITTEN
    outputs: dict = field(default_factory=dict)
    project_edit_fingerprint: str = ""


@dataclass(frozen=True)
class GenerationDocument:
    """A hydrated Generation read without changing the active Generation."""

    generation_id: str
    ordinal: int
    workspace: Workspace
    incoming_state: str
    intended_outgoing_state: str
    planning: GenerationPlanning
    story_memory_after: StoryContinuityMemory = field(default_factory=StoryContinuityMemory)
    story_memory_status: str = "trusted"
    observed_end_state: dict | None = None


@dataclass(frozen=True)
class StoryProjectSnapshot:
    manifest: ProjectManifestAny
    generations: tuple[GenerationDocument, ...]
    sequence_context: str
    fingerprint: str
    sequence_subjects: tuple[SequenceSubject, ...]
    # A bounded operation can expose a selected subset of historical pairs.
    # Ordinary Story snapshots retain their existing "all hydrated assets" rule.
    allowed_reference_pairs: frozenset[tuple[str, str]] | None = None
    dialogue_language: DialogueLanguage = DialogueLanguage.AS_WRITTEN

    @property
    def allowed_refs(self) -> set[tuple[str, str]]:
        if self.allowed_reference_pairs is not None:
            return set(self.allowed_reference_pairs)
        return {(g.generation_id, a.id) for g in self.generations for a in g.workspace.assets}

    def validate_draft(self, draft) -> None:
        if (draft.base_project_id != self.manifest.project_id
                or draft.base_project_updated_at != self.manifest.updated_at
                or draft.base_sequence_fingerprint != self.fingerprint):
            raise StaleProjectSnapshotError("The underlying Project changed. Rebuild or reconcile the Story Planner draft before continuing.")
        draft.validate_sources([g.generation_id for g in self.generations])


def default_projects_root() -> Path:
    """Return the platform data-root default, with an environment override."""
    configured = os.environ.get(PROJECTS_ROOT_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()

    if os.name == "nt":
        app_data = os.environ.get("LOCALAPPDATA", "").strip()
        base = Path(app_data) if app_data else Path.home() / "AppData" / "Local"
        return base / "MiniConstruct" / "Projects"

    data_home = os.environ.get("XDG_DATA_HOME", "").strip()
    base = Path(data_home) if data_home else Path.home() / ".local" / "share"
    return base / "miniconstruct" / "projects"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _canonical_project_id(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ProjectStoreError("project identifier must be a UUID") from exc


def _migrated_generation_id(project_id: str) -> str:
    """Stable v1 adapter identity; it is never derived from mutable Workspace data."""
    return str(uuid.uuid5(uuid.UUID(project_id), _MIGRATED_GENERATION_NAME))


def _blank_workspace(project_id: str, project_name: str) -> Workspace:
    """Build the canonical empty authoring state without inventing creative text.

    Workspace's prompt validator intentionally requires text for model requests.
    A newly inserted Generation is an authoring draft, so it uses the same field
    defaults while remaining a valid persisted snapshot until the user fills it.
    """
    values: dict[str, Any] = {}
    for field_name, field in Workspace.model_fields.items():
        if not field.is_required():
            values[field_name] = field.get_default(call_default_factory=True)
    values.update(project_id=project_id, project_name=project_name, creative_request="")
    return validate_authoring_workspace(values)


def _sequence_summary(generations: list[GenerationManifest], active_id: str,
                      subjects: list[SequenceSubject], next_subject_number: int) -> SequenceSummary:
    return SequenceSummary(
        active_generation_id=active_id,
        subjects=tuple(s.model_copy(deep=True) for s in subjects),
        next_subject_number=next_subject_number,
        generations=tuple(
            GenerationSummary(generation.generation_id, generation.created_at, generation.updated_at,
                              tuple({"id": a["id"], "filename": a["filename"].replace("\\", "/").split("/")[-1],
                                     "role": a["role"]} for a in generation.workspace.get("assets", [])),
                              generation.story_memory_status)
            for generation in generations
        ),
    )


def _merged_planning(
    existing: GenerationPlanning | None,
    patch: GenerationPlanning | dict[str, Any] | None,
) -> GenerationPlanning:
    """Apply an optional planning patch without erasing omitted fields."""
    current = existing or GenerationPlanning()
    if patch is None:
        return current.model_copy(deep=True)
    if isinstance(patch, GenerationPlanning):
        return patch.model_copy(deep=True)
    values = current.model_dump(mode="json", by_alias=True)
    values.update(patch)
    return GenerationPlanning.model_validate(values)


def _image_extension(mime_type: str) -> str:
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/avif": ".avif",
        "image/bmp": ".bmp",
        "image/tiff": ".tif",
    }.get(mime_type.lower(), ".bin")


def _content_addressed_asset_file_name(mime_type: str, payload: bytes) -> str:
    digest = hashlib.sha256(payload).hexdigest()
    return f"{digest}{_image_extension(mime_type)}"


def _parse_image_data_url(data_url: str, asset_id: str) -> tuple[str, bytes]:
    if not isinstance(data_url, str) or not data_url.startswith("data:image/"):
        raise AssetPayloadError(f"image asset {asset_id!r} does not contain an image data URL")
    try:
        header, encoded = data_url.split(",", 1)
        if ";base64" not in header.lower():
            raise ValueError("image data URL is not base64 encoded")
        mime_type = header[5:].split(";", 1)[0]
        payload = base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise AssetPayloadError(f"image asset {asset_id!r} contains invalid base64 data") from exc
    if not payload:
        raise AssetPayloadError(f"image asset {asset_id!r} contains an empty payload")
    return mime_type, payload


def _data_url(mime_type: str, payload: bytes) -> str:
    return f"data:{mime_type};base64,{base64.b64encode(payload).decode('ascii')}"


class ProjectStore(NativeContinuationStoreMixin, VisualHandoffStoreMixin, ObservationStoreMixin, TakeStoreMixin):
    """Filesystem-backed storage for the canonical v17 Project/Sequence format."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root).expanduser() if root is not None else default_projects_root()
        self.root = self.root.resolve(strict=False)

    MEDIA_LIMITS = {"audio": 128 * 1024 * 1024, "video": 512 * 1024 * 1024}

    async def ingest_media(self, chunks, kind: str, mime_type: str) -> MediaPayload:
        """Stream browser bytes to temporary backend authority, before Project save."""
        if kind not in self.MEDIA_LIMITS or not mime_type.startswith(f"{kind}/") or len(mime_type) > 128:
            raise AssetPayloadError("Choose an Audio/Video file with a matching media MIME type.")
        self._ensure_root()
        uploads = self.root / ".media-uploads"
        if uploads.is_symlink():
            raise AssetPayloadError("Invalid media staging directory")
        uploads.mkdir(exist_ok=True)
        # Unsaved uploads are temporary, never cross-Project canonical storage.
        cutoff = time.time() - 24 * 60 * 60
        for item in uploads.iterdir():
            if item.is_file() and not item.is_symlink() and item.stat().st_mtime < cutoff:
                item.unlink(missing_ok=True)
        token = uuid.uuid4().hex
        path = uploads / token
        digest, size = hashlib.sha256(), 0
        try:
            with path.open("xb") as stream:
                async for chunk in chunks:
                    size += len(chunk)
                    if size > self.MEDIA_LIMITS[kind]:
                        raise AssetPayloadError(f"{kind.title()} file exceeds {self.MEDIA_LIMITS[kind] // (1024 * 1024)} MiB limit.")
                    stream.write(chunk)
                    digest.update(chunk)
                stream.flush()
                os.fsync(stream.fileno())
            if not size:
                raise AssetPayloadError("The selected media file is empty.")
            self._atomic_write(uploads / f"{token}.json", json.dumps({"kind": kind, "mimeType": mime_type,
                "sha256": digest.hexdigest(), "byteLength": size}).encode())
            return MediaPayload(sha256=digest.hexdigest(), byteLength=size, uploadId=token)
        except BaseException:
            path.unlink(missing_ok=True)
            (uploads / f"{token}.json").unlink(missing_ok=True)
            raise

    @staticmethod
    def _verify_media(path: Path, digest: str, size: int) -> None:
        if path.is_symlink() or not path.is_file() or path.stat().st_size != size:
            raise AssetPayloadError("Media file payload unavailable - reattach the file before execution.")
        with path.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != digest:
            raise AssetPayloadError("Media file payload content verification failed - reattach the file.")

    def _media_source_identity(self, reference: MediaPayload, kind: str, mime_type: str) -> Path:
        """Locate server-owned identity. Publication must verify the copy stream."""
        if reference.byte_length > self.MEDIA_LIMITS[kind]:
            raise AssetPayloadError("Media file exceeds the supported size limit.")
        if reference.project_id:
            directory = self._project_directory(reference.project_id, must_exist=True)
            manifest = self._read_manifest(directory)
            generations, _ = self._generations(manifest)
            entry = next((e for g in generations for e in g.asset_files
                if e.kind == kind and e.mime_type == mime_type and e.content_sha256 == reference.sha256
                and e.byte_length == reference.byte_length), None)
            if entry is None:
                raise AssetPayloadError("Media file payload unavailable - reattach the file.")
            path = self._safe_project_relative_path(directory, entry.path)
        elif reference.upload_id:
            uploads = self.root / ".media-uploads"
            path = uploads / reference.upload_id
            metadata = uploads / f"{reference.upload_id}.json"
            if uploads.is_symlink() or metadata.is_symlink():
                raise AssetPayloadError("Invalid media upload authority")
            try:
                record = json.loads(metadata.read_text())
            except (OSError, ValueError) as exc:
                raise AssetPayloadError("Media upload unavailable - reattach the file.") from exc
            if record != {"kind": kind, "mimeType": mime_type, "sha256": reference.sha256, "byteLength": reference.byte_length}:
                raise AssetPayloadError("Media upload identity mismatch")
        else:
            raise AssetPayloadError("Media file payload unavailable - reattach the file.")
        if path.is_symlink() or not path.is_file() or path.stat().st_size != reference.byte_length:
            raise AssetPayloadError("Media file payload unavailable - reattach the file.")
        return path

    def execution_payload(self, project_id: str, generation_id: str, asset_id: str,
                          kind: str, digest: str) -> tuple[Path, str]:
        path, mime, size = self._execution_payload_identity(project_id, generation_id, asset_id, kind, digest)
        self._verify_media(path, digest, size)
        return path, mime

    @contextmanager
    def open_execution_payloads(self, project_id, generation_id, requirements):
        """Verify current handles once per file and keep them open through transport."""
        with ExitStack() as stack:
            handles, payloads = {}, []
            for requirement in requirements:
                digest = requirement.content_sha256
                path, mime, size = self._execution_payload_identity(project_id, generation_id,
                    requirement.asset_id, requirement.asset_kind, digest)
                identity = (path, digest, size)
                if identity not in handles:
                    stream = stack.enter_context(path.open("rb"))
                    if os.fstat(stream.fileno()).st_size != size:
                        raise AssetPayloadError("Saved payload length changed. Reattach it and Prepare again.")
                    from miniconstruct.send_timing import count
                    count("mediaHashPasses")
                    count("mediaHashBytes", size)
                    if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                        raise AssetPayloadError("Saved payload changed or is corrupt. Reattach it and Prepare again.")
                    stream.seek(0)
                    handles[identity] = stream
                payloads.append((requirement, handles[identity], mime))
            yield payloads

    def _execution_payload_identity(self, project_id, generation_id, asset_id, kind, digest):
        """Resolve saved inputs or a canonically re-resolved transient handoff."""
        if asset_id.startswith("handoff:"):
            handoff = self.visual_handoff(project_id, generation_id, materialize=True)
            if handoff.asset is None or handoff.asset.id != asset_id or handoff.asset.kind != kind:
                raise AssetPayloadError("Visual handoff source changed. Prepare again.")
            return handoff.path, handoff.asset.mime_type, handoff.path.stat().st_size
        directory = self._project_directory(project_id, must_exist=True)
        manifest = self._read_manifest(directory)
        generations, _ = self._generations(manifest)
        generation = next((g for g in generations if g.generation_id == generation_id), None)
        if generation is None:
            raise GenerationNotFoundError("Generation not found")
        entry = next((e for e in generation.asset_files if e.asset_id == asset_id and e.kind == kind), None)
        if entry is None or (entry.content_sha256 and entry.content_sha256 != digest):
            raise AssetPayloadError("Saved input payload identity changed. Prepare again.")
        path = self._safe_project_relative_path(directory, entry.path)
        if path.is_symlink() or not path.is_file():
            raise AssetPayloadError("Reference payload unavailable; Replace Picture or reattach file before execution")
        size = entry.byte_length if entry.byte_length is not None else path.stat().st_size
        return path, entry.mime_type, size

    @staticmethod
    def _copy_media(source: Path, destination: Path, *, digest: str, size: int | None = None) -> None:
        """Bounded memory and atomic publication, with harmless orphan retention."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            if source.is_symlink() or not source.is_file():
                raise AssetPayloadError("Media file payload unavailable - reattach the file.")
            source_size = source.stat().st_size
        except OSError as exc:
            raise AssetPayloadError("Media source payload could not be read - reattach the file.") from exc
        expected_size = source_size if size is None else size
        if source_size != expected_size:
            raise AssetPayloadError("Media file payload length verification failed - reattach the file.")
        if destination.exists():
            if source != destination:
                ProjectStore._verify_media(source, digest, expected_size)
            ProjectStore._verify_media(destination, digest, expected_size)
            return
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        try:
            try:
                incoming = source.open("rb")
            except OSError as exc:
                raise AssetPayloadError("Media source payload could not be read - reattach the file.") from exc
            with incoming, temporary.open("xb") as outgoing:
                copied, content = 0, hashlib.sha256()
                while True:
                    try:
                        chunk = incoming.read(1024 * 1024)
                    except OSError as exc:
                        raise AssetPayloadError("Media source payload could not be read - reattach the file.") from exc
                    if not chunk:
                        break
                    outgoing.write(chunk)
                    content.update(chunk)
                    copied += len(chunk)
                if copied != expected_size or content.hexdigest() != digest:
                    raise AssetPayloadError("Media copy content verification failed - reattach the file.")
                outgoing.flush()
                os.fsync(outgoing.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    # Instance seams used by benchmark-only stores. Production callers retain
    # the normal UUID and wall-clock implementations without supplying hooks.
    def _new_project_id(self) -> str:
        return str(uuid.uuid4())

    def _new_generation_id(self) -> str:
        return str(uuid.uuid4())

    def _now(self) -> str:
        return _utc_now()

    @staticmethod
    def _manifest_timestamp(previous: str, proposed: str) -> str:
        previous_time = datetime.fromisoformat(previous)
        if datetime.fromisoformat(proposed) <= previous_time:
            return (previous_time + timedelta(milliseconds=1)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        return proposed

    def _read_execution_state(self, directory: Path):
        from miniconstruct.execution_state import ProjectExecutionState
        path = directory / "execution-state.json"
        if path.is_symlink():
            raise MalformedProjectError("Execution sidecar symlinks are not supported")
        try:
            return ProjectExecutionState.model_validate_json(path.read_bytes())
        except FileNotFoundError:
            return ProjectExecutionState()
        except (OSError, ValueError) as exc:
            raise MalformedProjectError("Project execution state could not be read; existing file preserved") from exc

    def _write_execution_state(self, directory: Path, state):
        self._atomic_write(directory / "execution-state.json", state.model_dump_json(by_alias=True, indent=2).encode("utf-8"))

    @_project_transaction
    def saved_prompt_status(self, project_id: str, generation_id: str):
        from miniconstruct.execution_state import SavedPromptStatus, SavedPromptEntryStatus, SavedPromptCollection

        generation, selection = self.execution_snapshot(project_id, generation_id)
        directory = self._project_directory(project_id, must_exist=True)
        collection = self._read_execution_state(directory).saved_prompts.get(generation.generation_id, SavedPromptCollection())
        return SavedPromptStatus(prompts=[SavedPromptEntryStatus(**item.model_dump(),
            stale=item.source_fingerprint != selection.current_fingerprints.get(item.authoring_path, ""))
            for item in reversed(collection.prompts)], activeSavedPromptId=collection.active_saved_prompt_id,
            nextSaveNumber=collection.next_save_number)

    @_project_transaction
    def save_prompt(self, project_id: str, generation_id: str, request):
        from miniconstruct.execution_state import SavedPrompt, SavedPromptCollection, SAVED_PROMPT_LIMIT

        generation, selection = self.execution_snapshot(project_id, generation_id)
        path = request.authoring_path or "one_shot"
        fingerprint = selection.current_fingerprints.get(path)
        if fingerprint is None:
            path = "one_shot"
            fingerprint = selection.current_fingerprints[path]
        directory = self._project_directory(project_id, must_exist=True)
        state = self._read_execution_state(directory)
        collection = state.saved_prompts.setdefault(generation.generation_id, SavedPromptCollection())
        digest = hashlib.sha256(request.prompt.encode("utf-8")).hexdigest()
        if request.saved_prompt_id is not None:
            existing = next((item for item in collection.prompts if item.id == request.saved_prompt_id), None)
            if existing is None:
                raise ProjectStoreError("Saved H3 prompt does not belong to this Generation")
            # Update the canonical record under the existing Project transaction.
            # savedAt means last saved; append order and stable numbering do not change.
            existing.prompt = request.prompt
            existing.sha256 = digest
            existing.source_fingerprint = fingerprint
            existing.authoring_path = path
            existing.saved_at = self._now()
            duplicate = False
        else:
            existing = None if request.save_as_new else next(
                (item for item in collection.prompts if item.sha256 == digest), None)
            duplicate = existing is not None
        if existing is None:
            if len(collection.prompts) >= SAVED_PROMPT_LIMIT:
                raise ProjectStoreError(f"This Generation has {SAVED_PROMPT_LIMIT} saved H3 prompts. Delete an older entry before saving another.")
            existing = SavedPrompt(prompt=request.prompt, sha256=digest, saveNumber=collection.next_save_number,
                savedAt=self._now(), sourceFingerprint=fingerprint, authoringPath=path)
            collection.prompts.append(existing)
            collection.next_save_number += 1
        collection.active_saved_prompt_id = existing.id
        self._write_execution_state(directory, state)
        status = self.saved_prompt_status(project_id, generation_id)
        # A first Save may reuse an exact duplicate; explicit Save As New never does.
        status.already_saved = duplicate
        return status

    @_project_transaction
    def activate_saved_prompt(self, project_id: str, generation_id: str, prompt_id: str):
        generation, _ = self.execution_snapshot(project_id, generation_id)
        directory = self._project_directory(project_id, must_exist=True)
        state = self._read_execution_state(directory)
        collection = state.saved_prompts.get(generation.generation_id)
        item = next((item for item in collection.prompts if str(item.id) == prompt_id), None) if collection else None
        if item is None:
            raise ProjectStoreError("Saved H3 prompt does not belong to this Generation")
        collection.active_saved_prompt_id = item.id
        self._write_execution_state(directory, state)
        return self.saved_prompt_status(project_id, generation_id)

    @_project_transaction
    def delete_saved_prompt(self, project_id: str, generation_id: str, prompt_id: str):
        generation, _ = self.execution_snapshot(project_id, generation_id)
        directory = self._project_directory(project_id, must_exist=True)
        state = self._read_execution_state(directory)
        collection = state.saved_prompts.get(generation.generation_id)
        if collection is None or not any(str(item.id) == prompt_id for item in collection.prompts):
            raise ProjectStoreError("Saved H3 prompt does not belong to this Generation")
        collection.prompts = [item for item in collection.prompts if str(item.id) != prompt_id]
        if str(collection.active_saved_prompt_id) == prompt_id:
            collection.active_saved_prompt_id = None
        self._write_execution_state(directory, state)
        return self.saved_prompt_status(project_id, generation_id)

    @_project_transaction
    def _invalidate_reference_execution(self, directory, generation_id, asset_ids):
        sidecar = self._read_execution_state(directory)
        sidecar.selections.pop(generation_id, None)
        routing = sidecar.routing.get(generation_id)
        if routing:
            routing.assets = {key: value for key, value in routing.assets.items() if key in asset_ids}
        self._write_execution_state(directory, sidecar)

    @staticmethod
    def _reference_authority(generation):
        return ([generation.workspace.get(key) for key in ("assets", "subjects")], generation.asset_files)

    @_project_transaction
    def sync_reference_assets(self, project_id: str, generation_id: str, assets: list[dict],
                              subjects: list[SubjectRecord], expected_project_fingerprint: str,
                              expected_project_edit_fingerprint: str | None = None) -> dict[str, Any]:
        """Save the reference collection only; never adopt unrelated browser authoring.

        An omitted image URL must name the digest of this Generation's *current*
        slot. Immutable orphan/historical blobs are not an attachment API.
        """
        canonical_id, directory, existing, generations, active_id = self._mutation_context(project_id)
        if not self._execution_sync_matches(existing, expected_project_fingerprint, expected_project_edit_fingerprint):
            raise StaleProjectSnapshotError("Project changed since loading; reload before changing Reference Assets")
        generation = generations[self._generation_index(generations, generation_id)]
        retained_ids = {asset.get("id") for asset in assets
                        if not (asset.get("image") or {}).get("data_url")
                        and not (asset.get("mediaPayload") or {}).get("uploadId")}
        metadata = json.loads(json.dumps(generation.workspace))
        verified_payloads = {}
        for asset in metadata.get("assets", []):
            if asset.get("id") not in retained_ids and asset.get("kind") == "image":
                asset["image"] = None
        current = self._hydrate_workspace(directory, canonical_id, existing.name, metadata,
            [entry for entry in generation.asset_files if entry.asset_id in retained_ids],
            verified_payloads=verified_payloads)
        by_id = {asset.id: asset for asset in current.assets}
        incoming = json.loads(json.dumps(assets))
        for asset in incoming:
            digest = asset.pop("imageSha256", None)
            if asset.get("kind") == "image" and not (asset.get("image") or {}).get("data_url"):
                original = by_id.get(asset.get("id"))
                entry = next((e for e in generation.asset_files if e.asset_id == asset.get("id") and e.kind == "image"), None)
                if original is None or entry is None or digest != self._reference_digest(entry):
                    raise AssetPayloadError("Picture digest differs from current authority; attach the image bytes")
                asset["image"] = original.image.model_dump() if original.image else None
                asset["payloadSha256"] = digest
                asset["payloadAvailability"] = original.payload_availability

        required = structural_subject_ids(incoming)
        # Retain the saved local registry only where it is still structurally used.
        registry = {subject.id: subject for subject in current.subjects}
        registry.update({subject.id: subject for subject in subjects})
        if not required <= registry.keys():
            raise ProjectStoreError("Reference Assets require canonical Subject records")
        records = sorted((registry[key] for key in required), key=lambda subject: (subject.number, subject.id))
        data = current.model_dump(mode="json", by_alias=True)
        data.update(assets=incoming, subjects=[subject.model_dump() for subject in records],
                    nextSubjectNumber=max([current.next_subject_number, *(s.number + 1 for s in records)]))
        workspace = validate_authoring_workspace(data)
        workspace_data, files, blobs, normalized = self._externalize_workspace(directory, workspace, canonical_id, existing.name, generation.asset_files, generation.workspace.get("assets", []))
        changed = workspace_data != generation.workspace or files != generation.asset_files or bool(blobs)
        if changed:
            generation.workspace, generation.asset_files = workspace_data, files
            generation.updated_at = self._now()
            self._validate_generation_asset_mapping(generation)
            self._cast_for_write(existing, generations)
            # Clear active authority before publishing; a sidecar failure cannot
            # leave a new manifest executing a selection built for old labels.
            self._invalidate_reference_execution(directory, generation.generation_id, {asset.id for asset in workspace.assets})
            self._commit_sequence(directory, existing, generations, active_id, pending_blobs=blobs, hydrate_result=False)
            existing = self._read_manifest(directory)
        # Same locked operation and retained immutable identities. New/replaced
        # entries have no result in this local map and are verified normally.
        normalized = self._hydrate_workspace(directory, canonical_id, existing.name,
            generation.workspace, generation.asset_files, verified_payloads=verified_payloads)
        cast, next_number = self._sequence_cast(existing)
        return {"payloadStates": {a.id: {"payloadAvailability": a.payload_availability, "payloadSha256": a.payload_sha256} for a in normalized.assets},
                **self._revision_receipt(existing), "generationId": generation.generation_id,
                "changed": changed, "subjects": [subject.model_dump(mode="json", by_alias=True) for subject in cast],
                "nextSubjectNumber": next_number,
                "mediaPayloads": {asset.id: asset.media_payload.model_dump(mode="json", by_alias=True)
                                  for asset in normalized.assets if asset.media_payload is not None}}

    def sync_execution_duration(self, project_id: str, generation_id: str, duration_seconds: float,
                                 expected_project_fingerprint: str) -> dict[str, Any]:
        """Compatibility entry point for duration-only clients."""
        return self.sync_execution_setup(project_id, generation_id, duration_seconds,
                                         expected_project_fingerprint)

    @_project_transaction
    def sync_execution_setup(self, project_id: str, generation_id: str, duration_seconds: float,
                             expected_project_fingerprint: str, aspect_ratio: str | None = None,
                             expected_project_edit_fingerprint: str | None = None) -> dict[str, Any]:
        """Persist bounded execution H3 Setup, preserving unrelated unsaved authoring."""
        if isinstance(duration_seconds, bool) or not 4 <= duration_seconds <= 15:
            raise ProjectStoreError("H3 Setup duration must be from 4 to 15 seconds")
        if aspect_ratio is not None and (not isinstance(aspect_ratio, str) or not 1 <= len(aspect_ratio) <= 40):
            raise ProjectStoreError("H3 Setup aspect ratio must contain from 1 to 40 characters")
        _canonical_id, directory, existing, generations, active_id = self._mutation_context(project_id)
        if not self._execution_sync_matches(existing, expected_project_fingerprint, expected_project_edit_fingerprint):
            raise StaleProjectSnapshotError("Project changed since loading; reload before preparing")
        index = self._generation_index(generations, generation_id)
        generation = generations[index]
        setup = {"durationSeconds": duration_seconds}
        if aspect_ratio is not None:
            setup["aspectRatio"] = aspect_ratio
        if any(generation.workspace.get(key) != value for key, value in setup.items()):
            generation.workspace = {**generation.workspace, **setup}
            generation.updated_at = self._now()
            self._commit_sequence(directory, existing, generations, active_id, hydrate_result=False)
            existing = self._read_manifest(directory)
        return {**self._revision_receipt(existing),
                "generationId": generation.generation_id, **setup}

    @_project_transaction
    def execution_snapshot(self, project_id: str, generation_id: str):
        """Inspect saved authoring/selection identity; does not promise byte readiness."""
        from miniconstruct.execution_authoring import execution_authoring_fingerprint
        from miniconstruct.execution_state import SelectionStatus
        directory, manifest, generations, _active_id, saved = self._output_context(project_id, generation_id)
        generation = self._generation_metadata(manifest, saved, self._generation_index(generations, generation_id))
        metadata = {asset.id: asset for asset in generation.workspace.assets}
        payloads = {}
        for entry in saved.asset_files:
            digest = self._reference_digest(entry)
            if digest is None and entry.kind == "image":
                # Legacy non-content-addressed Pictures have no stored digest.
                _, payload = self._reference_payload(directory, entry)
                digest = hashlib.sha256(payload).hexdigest() if payload is not None else None
            if digest is not None:
                payloads[entry.asset_id] = {"sha256": digest,
                    "mimeType": entry.mime_type if entry.kind == "image" else metadata[entry.asset_id].mime_type}
                if entry.kind == "image":
                    payloads[entry.asset_id].update(width=entry.width, height=entry.height)
        selected = self._read_execution_state(directory).selections.get(generation.generation_id)
        continuity = self.h3_continuity_context(project_id, generation_id)
        args = (generation.workspace, self.generation_narrative_subjects(project_id, generation.workspace),
                continuity, self.visual_handoff(project_id, generation_id, generation.workspace, strict=False).identity)
        from miniconstruct.h3.continuity import one_shot_continuity_context
        fingerprints = {"one_shot": execution_authoring_fingerprint(
            generation.workspace, args[1], one_shot_continuity_context(continuity), args[3], canonical_payloads=payloads)}
        if generation.planning.scene_plan.strip():
            fingerprints["story"] = execution_authoring_fingerprint(
                *args, authoring_path="story", story_context=self.story_h3_context(project_id, generation_id),
                canonical_payloads=payloads)
        path = selected.authoring_path if selected and selected.authoring_path else "one_shot"
        fingerprint = fingerprints.get(path, "")
        return generation, SelectionStatus(currentFingerprint=fingerprint, currentFingerprints=fingerprints,
                                           selected=selected, stale=selected is not None and selected.source_fingerprint != fingerprint)

    def execution_preparation_snapshot(self, project_id: str, generation_id: str, *, verify_payloads=True):
        """Verify a captured authoring snapshot without holding the Project lock."""
        with _PROJECT_TRANSACTION_LOCK:
            directory, manifest, _, _, saved = self._output_context(project_id, generation_id)
            generation, status = self.execution_snapshot(project_id, generation_id)
            # Send's compiler needs canonical descriptors, not hydrated base64 or
            # another full media read. open_execution_payloads verifies real bytes
            # before uploads AND after uploads, independently of these descriptors.
            if not verify_payloads:
                entries = {entry.asset_id: entry for entry in saved.asset_files}
                for asset in generation.workspace.assets:
                    entry = entries.get(asset.id)
                    if entry is None:
                        continue
                    asset.payload_sha256 = self._reference_digest(entry)
                    if entry.kind != "image":
                        asset.media_payload = MediaPayload(sha256=entry.content_sha256,
                            byteLength=entry.byte_length, projectId=manifest.project_id)
                    elif asset.payload_sha256 is None:
                        # Legacy non-content-addressed Pictures still need a read.
                        state, payload = self._reference_payload(directory, entry)
                        if state is None:
                            asset.payload_sha256 = hashlib.sha256(payload).hexdigest()
                return generation, status
            authority = (status.current_fingerprint, status.selected, saved.asset_files)
        workspace = self._with_effective_dialogue_language(self._hydrate_workspace(
            directory, manifest.project_id, manifest.name, saved.workspace, saved.asset_files),
            self._dialogue_language(manifest))
        with _PROJECT_TRANSACTION_LOCK:
            _, _, _, _, current = self._output_context(project_id, generation_id)
            _, current_status = self.execution_snapshot(project_id, generation_id)
            if (current_status.current_fingerprint, current_status.selected, current.asset_files) != authority:
                raise StaleProjectSnapshotError(
                    "Execution inputs changed during payload verification (authoring, References or selected H3 prompt); Prepare again.")
        return replace(generation, workspace=workspace), status

    @_project_transaction
    def select_execution_prompt(self, project_id: str, generation_id: str, request):
        from miniconstruct.execution_state import SelectedPrompt, require_execution_prompt
        generation, status = self.execution_snapshot(project_id, generation_id)
        from miniconstruct.workflow_preparation import require_current_picture_map
        from miniconstruct.execution_routing import effective_references
        require_current_picture_map(request.reference_map, effective_references(
            self.visual_handoff(project_id, generation_id, generation.workspace, materialize=True).workspace(generation.workspace),
            self.execution_routing(project_id, generation_id)))
        path = request.authoring_path or "one_shot"
        current_fingerprint = status.current_fingerprints.get(path)
        if not current_fingerprint:
            current_fingerprint = status.current_fingerprints["one_shot"]
        if path not in status.current_fingerprints or request.source_fingerprint != current_fingerprint:
            from miniconstruct.execution_state import ManualEditAdoptionWrite
            return self._adopt_execution_prompt(project_id, generation_id,
                ManualEditAdoptionWrite(prompt=request.prompt, authoringPath=request.authoring_path), "manual_edit")
        require_execution_prompt(request.prompt)
        directory = self._project_directory(project_id, must_exist=True)
        state = self._read_execution_state(directory)
        state.selections[generation.generation_id] = SelectedPrompt(**request.model_dump(exclude={"authoring_path"}), mode=generation.workspace.mode, authoringPath=path)
        self._write_execution_state(directory, state)
        return self.execution_snapshot(project_id, generation_id)[1]

    @_project_transaction
    def adopt_history_execution_prompt(self, project_id: str, generation_id: str, request):
        """Bind exact History text to the current saved snapshot."""
        return self._adopt_execution_prompt(project_id, generation_id, request, "adopted_history")

    @_project_transaction
    def adopt_manual_edit_execution_prompt(self, project_id: str, generation_id: str, request):
        """Bind exact manually edited text to the current saved snapshot."""
        return self._adopt_execution_prompt(project_id, generation_id, request, "manual_edit")

    def _adopt_execution_prompt(self, project_id: str, generation_id: str, request, source_kind: str):
        """Bind exact supplied text to current saved authority and create a selection."""
        from miniconstruct.execution_state import SelectedPrompt, require_execution_prompt
        from miniconstruct.execution_routing import effective_references

        generation, status = self.execution_snapshot(project_id, generation_id)
        path = request.authoring_path or "one_shot"
        fingerprint = status.current_fingerprints.get(path)
        if not fingerprint:
            path = "one_shot"
            fingerprint = status.current_fingerprints[path]
        require_execution_prompt(request.prompt)
        workspace = self.visual_handoff(project_id, generation_id, generation.workspace, materialize=True).workspace(generation.workspace)
        references = effective_references(workspace, self.execution_routing(project_id, generation_id))
        directory = self._project_directory(project_id, must_exist=True)
        state = self._read_execution_state(directory)
        state.selections[generation.generation_id] = SelectedPrompt(
            prompt=request.prompt,
            sourceFingerprint=fingerprint,
            authoringPath=path,
            referenceMap=references,
            mode=generation.workspace.mode,
            sourceKind=source_kind,
        )
        self._write_execution_state(directory, state)
        return self.execution_snapshot(project_id, generation_id)[1]

    @_project_transaction
    def clear_execution_prompt(self, project_id: str, generation_id: str):
        generation = self.read_generation(project_id, generation_id)
        directory = self._project_directory(project_id, must_exist=True)
        state = self._read_execution_state(directory)
        state.selections.pop(generation.generation_id, None)
        self._write_execution_state(directory, state)
        return self.execution_snapshot(project_id, generation_id)[1]

    @_project_transaction
    def execution_routing(self, project_id: str, generation_id: str):
        """Return routing for a saved Generation; an absent entry is default state."""
        from miniconstruct.execution_routing import GenerationRouting
        directory, _, _, _, generation = self._output_context(project_id, generation_id)
        # Manifest inspection validates both IDs before the optional sidecar lookup.
        # A missing sidecar or routing entry is normal for older Projects and is
        # deliberately represented by an in-memory default, never a write.
        routing = self._read_execution_state(directory).routing.get(generation.generation_id, GenerationRouting())
        ids = {asset["id"] for asset in generation.workspace.get("assets", [])}
        return GenerationRouting(assets={key: value for key, value in routing.assets.items() if key in ids})

    @_project_transaction
    def set_execution_routing(self, project_id: str, generation_id: str, routing):
        from miniconstruct.execution_routing import effective_references
        from miniconstruct.execution import InvalidBindings
        generation = self.read_generation(project_id, generation_id)
        if set(routing.assets) - {asset.id for asset in generation.workspace.assets}:
            raise InvalidBindings(["Routing contains an asset outside this saved Generation."])
        try:
            effective_references(generation.workspace, routing)
        except ValueError as exc:
            raise InvalidBindings([str(exc)]) from exc
        directory = self._project_directory(project_id, must_exist=True)
        state = self._read_execution_state(directory)
        state.routing[generation_id] = routing
        self._write_execution_state(directory, state)
        return routing

    @_project_transaction
    def renderer_seed_settings(self, project_id: str, generation_id: str):
        directory, _, _, _, current = self._output_context(project_id, generation_id)
        return self._read_execution_state(directory).renderer_seed_settings.get(current.generation_id)

    @_project_transaction
    def set_renderer_seed_settings(self, project_id: str, generation_id: str, settings):
        directory, _, _, _, current = self._output_context(project_id, generation_id)
        state = self._read_execution_state(directory)
        state.renderer_seed_settings[current.generation_id] = settings
        self._write_execution_state(directory, state)
        return settings

    @_project_transaction
    def workflow_control_values(self, project_id: str, generation_id: str, profile_id: str):
        directory, _, _, _, current = self._output_context(project_id, generation_id)
        state = self._read_execution_state(directory)
        return (dict(state.workflow_control_defaults.get(profile_id, {})),
                dict(state.workflow_control_overrides.get(current.generation_id, {}).get(profile_id, {})))

    @_project_transaction
    def set_workflow_control_values(self, project_id: str, generation_id: str, profile_id: str, scope: str, values: dict):
        directory, _, _, _, current = self._output_context(project_id, generation_id)
        state = self._read_execution_state(directory)
        if scope == "project":
            state.workflow_control_defaults[profile_id] = values
        elif scope == "generation":
            state.workflow_control_overrides.setdefault(current.generation_id, {})[profile_id] = values
        else:
            raise ValueError("Unknown Workflow Control scope")
        self._write_execution_state(directory, state)
        return values

    def _ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def _project_directory(self, project_id: str, *, must_exist: bool = False) -> Path:
        canonical_id = _canonical_project_id(project_id)
        self._ensure_root()
        directory = self.root / f"{_PROJECT_DIRECTORY_PREFIX}{canonical_id}"
        resolved = directory.resolve(strict=False)
        if not resolved.is_relative_to(self.root):
            raise ProjectStoreError("project path escapes the configured Projects root")
        if directory.is_symlink():
            raise ProjectStoreError("project directory symlinks are not supported")
        if must_exist and not directory.is_dir():
            raise ProjectNotFoundError(f"project {canonical_id} was not found")
        return directory

    @staticmethod
    def _safe_project_relative_path(directory: Path, relative_path: str) -> Path:
        if not isinstance(relative_path, str) or not relative_path or Path(relative_path).is_absolute():
            raise MalformedProjectError("project asset path must be relative")
        candidate = directory / Path(relative_path)
        resolved = candidate.resolve(strict=False)
        if not resolved.is_relative_to(directory.resolve(strict=False)):
            raise MalformedProjectError("project asset path escapes the project directory")
        return candidate

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _read_manifest(self, directory: Path, *, validate_workspaces: bool = True) -> ProjectManifestAny:
        manifest_path = directory / "project.json"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise ProjectNotFoundError(f"project manifest was not found in {directory.name}")
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MalformedProjectError(f"Malformed project.json in {directory.name}") from exc
        if not isinstance(raw, dict):
            raise MalformedProjectError("project.json must contain a JSON object")
        version = raw.get("schemaVersion")
        if version not in SUPPORTED_PROJECT_SCHEMA_VERSIONS:
            raise UnsupportedProjectSchemaError(
                f"Unsupported project schema version {version!r}; supported versions are 1 through 17"
            )
        # Compatibility readers remain strict about their own frozen shapes.
        # A v11 file manually labelled as an older version must not turn this
        # new optional authority into an incompatibility or invent a value.
        if version < 11:
            raw.pop("storyCreativeDirection", None)
        if version < 17:
            raw.pop("storyPlanningStyle", None)
        # Profiles are canonical v12 authority.  A manually relabelled older
        # file cannot smuggle newer profile content into an older contract.
        if version < 12 and isinstance(raw.get("sequence"), dict):
            raw["sequence"].pop("dialogueLanguage", None)
            for subject in raw["sequence"].get("subjects", []):
                if isinstance(subject, dict):
                    subject.pop("characterProfile", None)
        if version < 15 and isinstance(raw.get("sequence"), dict):
            for generation in raw["sequence"].get("generations", []):
                if isinstance(generation, dict) and isinstance(generation.get("planning"), dict):
                    generation["planning"].pop("visualHandoff", None)
        if version < 16 and isinstance(raw.get("sequence"), dict):
            for generation in raw["sequence"].get("generations", []):
                if isinstance(generation, dict) and isinstance(generation.get("planning"), dict):
                    generation["planning"].pop("scenePlan", None)
        if version < 14 and isinstance(raw.get("sequence"), dict):
            for generation in raw["sequence"].get("generations", []):
                if isinstance(generation, dict):
                    generation.pop("takeObservations", None)
        if version < 13 and isinstance(raw.get("sequence"), dict):
            for generation in raw["sequence"].get("generations", []):
                if isinstance(generation, dict):
                    for key in ("takes", "renderSubmissions", "selectedTakeId"):
                        generation.pop(key, None)
        try:
            # Version 9 was used by two unrelated experiments. Select the
            # frozen adapter by shape, never by the version number alone.
            temporal_v9 = (
                version == 9
                and isinstance(raw.get("sequence"), dict)
                and ("storyMemory" in raw["sequence"] or "storyMemoryTemporal" in raw["sequence"])
                and not any({"storyMemoryAfter", "storyMemoryStatus"} <= generation.keys()
                            for generation in raw["sequence"].get("generations", [])
                            if isinstance(generation, dict))
            )
            if version in (9, 10, 11, 12, 13, 14, 15, 16, 17) and not temporal_v9:
                generations = raw.get("sequence", {}).get("generations", [])
                if (not isinstance(generations, list)
                        or any(not isinstance(generation, dict)
                               or not {"storyMemoryAfter", "storyMemoryStatus"} <= generation.keys()
                               for generation in generations)):
                    raise ValueError("Every Generation requires an explicit memory snapshot and status")
            manifest: ProjectManifestAny = (
                ProjectManifestV1.model_validate(raw)
                if version == 1
                else ProjectManifestV2.model_validate(raw)
                if version == 2
                else ProjectManifestV3.model_validate(raw)
                if version == 3
                else ProjectManifestV4.model_validate(raw)
                if version == 4
                else ProjectManifestV5.model_validate(raw)
                if version == 5
                else ProjectManifestV6.model_validate(raw)
                if version == 6
                else ProjectManifestV7.model_validate(raw)
                if version == 7
                else ProjectManifestV8.model_validate(raw)
                if version == 8
                else ProjectManifestV9Temporal.model_validate(raw)
                if temporal_v9
                else ProjectManifestV9GenerationOwned.model_validate(raw)
                if version == 9
                else ProjectManifestV10.model_validate(raw)
                if version == 10
                else ProjectManifestV11.model_validate(raw)
                if version == 11
                else ProjectManifestV12.model_validate(raw)
                if version == 12
                else ProjectManifestV13.model_validate(raw)
                if version == 13
                else ProjectManifestV14.model_validate(raw)
                if version == 14
                else ProjectManifestV15.model_validate(raw)
                if version == 15
                else ProjectManifestV16.model_validate(raw)
                if version == 16
                else ProjectManifest.model_validate(raw)
            )
        except Exception as exc:
            raise MalformedProjectError(f"Malformed project.json in {directory.name}") from exc
        expected_id = directory.name.removeprefix(_PROJECT_DIRECTORY_PREFIX)
        if manifest.project_id != expected_id:
            raise MalformedProjectError("project.json projectId does not match its project directory")
        self._validate_manifest_structure(directory, manifest)
        if validate_workspaces:
            self._validate_manifest_asset_mappings(manifest)
            subjects, next_number = self._sequence_cast(manifest)
            if isinstance(manifest, ProjectManifestV6):
                self._merge_generation_subjects(subjects, next_number, manifest.sequence.generations,
                                                allow_new=False)
        return manifest

    @staticmethod
    def _generations(manifest: ProjectManifestAny) -> tuple[list[GenerationManifest], str]:
        if isinstance(manifest, (ProjectManifestV9GenerationOwned, ProjectManifestV10)):
            generations = manifest.sequence.generations
            for generation in generations:
                if generation.planning.shot_count is None:
                    saved_shots = generation.workspace.get("shots")
                    generation.planning.shot_count = saved_shots if type(saved_shots) is int and saved_shots > 0 else 1
            return generations, manifest.sequence.active_generation_id
        if isinstance(manifest, ProjectManifestV1):
            values = [dict(generationId=_migrated_generation_id(manifest.project_id),
                           createdAt=manifest.created_at, updatedAt=manifest.updated_at,
                           workspace=manifest.workspace, assetFiles=manifest.asset_files)]
            active_id = values[0]["generationId"]
        else:
            values = [g.model_dump(by_alias=True) for g in manifest.sequence.generations]
            active_id = manifest.sequence.active_generation_id
        generations = [GenerationManifest(**value, storyMemoryStatus="legacy_unknown") for value in values]
        for generation in generations:
            if generation.planning.shot_count is None:
                saved_shots = generation.workspace.get("shots")
                generation.planning.shot_count = saved_shots if type(saved_shots) is int and saved_shots > 0 else 1
        if isinstance(manifest, ProjectManifestV9Temporal):
            # A legacy checkpoint claims an exact state at its explicit boundary.
            # Do not replay old events: that would recreate retired authority and
            # could manufacture history across an incomplete interval.
            exact = {
                checkpoint.effective_after_generation_id: checkpoint.memory_snapshot
                for segment in manifest.sequence.story_memory_temporal.segments
                for checkpoint in segment.checkpoints
            }
            for generation in generations:
                if (memory := exact.get(generation.generation_id)) is not None:
                    generation.story_memory_after = memory.model_copy(deep=True)
                    generation.story_memory_status = "trusted"
            terminal = generations[-1]
            terminal.story_memory_after = manifest.sequence.story_memory.model_copy(deep=True)
            terminal.story_memory_status = {
                "current": "trusted", "needs_review": "needs_review",
            }.get(manifest.sequence.story_memory_temporal.status, "legacy_unknown")
        elif isinstance(manifest, ProjectManifestV8):
            generations[-1].story_memory_after = manifest.sequence.story_memory.model_copy(deep=True)
            generations[-1].story_memory_status = "trusted"
        else:
            generations[-1].story_memory_status = "trusted"
        return generations, active_id

    @staticmethod
    def _story_idea(manifest: ProjectManifestAny) -> str:
        return manifest.story_idea if isinstance(manifest, ProjectManifestV5) else ""

    @staticmethod
    def _story_creative_direction(manifest: ProjectManifestAny) -> StoryCreativeDirection:
        return (manifest.story_creative_direction.model_copy(deep=True)
                if isinstance(manifest, ProjectManifestV11) else StoryCreativeDirection())

    @staticmethod
    def _story_planning_style(manifest: ProjectManifestAny) -> StoryPlanningStyle:
        return (manifest.story_planning_style.model_copy(deep=True)
                if isinstance(manifest, ProjectManifest) else StoryPlanningStyle())

    @staticmethod
    def _story_memory(manifest):
        return ProjectStore._generations(manifest)[0][-1].story_memory_after

    @staticmethod
    def _sequence_context(manifest: ProjectManifestAny) -> str:
        return manifest.sequence.continuity_context if isinstance(manifest, (ProjectManifestV3, ProjectManifestV4)) else ""

    @staticmethod
    def _dialogue_language(manifest: ProjectManifestAny) -> DialogueLanguage:
        """Canonical Project value; old manifests deliberately read as As Written."""
        sequence = getattr(manifest, "sequence", None)
        return sequence.dialogue_language if isinstance(sequence, SequenceManifest) else DialogueLanguage.AS_WRITTEN

    @staticmethod
    def _with_effective_dialogue_language(workspace: Workspace, language: DialogueLanguage) -> Workspace:
        return workspace.model_copy(update={"dialogue_language": language})

    @staticmethod
    def _validate_generation_asset_mapping(generation: GenerationManifest) -> None:
        workspace_assets = generation.workspace.get("assets", [])
        if not isinstance(workspace_assets, list):
            raise MalformedProjectError("Generation Workspace assets must be a list")
        image_ids: set[str] = set()
        for asset in workspace_assets:
            if not isinstance(asset, dict) or asset.get("kind") != "image":
                continue
            asset_id = asset.get("id")
            if not isinstance(asset_id, str) or not asset_id:
                raise MalformedProjectError("image asset has an invalid asset ID")
            image_ids.add(asset_id)
        by_id = {a.get("id"): a for a in workspace_assets if isinstance(a, dict)}
        for entry in generation.asset_files:
            if entry.asset_id not in by_id or by_id[entry.asset_id].get("kind") != entry.kind:
                raise MalformedProjectError("asset manifest kind or ID mismatch")
            if entry.kind != "image" and (not entry.content_sha256 or not entry.byte_length
                    or not entry.mime_type.startswith(f"{entry.kind}/")
                    or entry.byte_length > ProjectStore.MEDIA_LIMITS[entry.kind]):
                raise MalformedProjectError("media manifest lacks valid content identity")
        mapped_ids = {entry.asset_id for entry in generation.asset_files if entry.kind == "image"}
        if image_ids != mapped_ids:
            missing = sorted(image_ids - mapped_ids)
            extra = sorted(mapped_ids - image_ids)
            details = []
            if missing:
                details.append(f"missing {missing!r}")
            if extra:
                details.append(f"unexpected {extra!r}")
            raise MalformedProjectError(
                f"Generation {generation.generation_id} asset mapping is inconsistent ({'; '.join(details)})"
            )

    def _validate_persisted_workspace(self, generation: GenerationManifest, project_id: str, name: str) -> Workspace:
        """Validate every snapshot without reading image bytes during listing."""
        workspace_data = json.loads(json.dumps(generation.workspace))
        if not isinstance(workspace_data, dict):
            raise MalformedProjectError("Generation Workspace must be an object")
        stored_project_id = workspace_data.pop("projectId", None)
        if stored_project_id is not None and stored_project_id != project_id:
            raise MalformedProjectError("project.json projectId does not agree with its Workspace")
        stored_project_name = workspace_data.pop("projectName", None)
        if stored_project_name is not None and stored_project_name != name:
            raise MalformedProjectError("project.json name does not agree with its Workspace")
        workspace_data["projectId"] = project_id
        workspace_data["projectName"] = name
        assets_by_id = {asset_file.asset_id: asset_file for asset_file in generation.asset_files}
        for asset in workspace_data.get("assets", []):
            if not isinstance(asset, dict) or asset.get("kind") != "image":
                continue
            asset_file = assets_by_id[asset["id"]]
            # The actual payload is externalized. A tiny valid placeholder lets
            # Pydantic validate the rest of the persisted Workspace metadata.
            asset["image"] = {
                "data_url": _data_url(asset_file.mime_type, b"0"),
                "width": asset_file.width,
                "height": asset_file.height,
            }
        try:
            return validate_authoring_workspace(workspace_data)
        except Exception as exc:
            raise MalformedProjectError("project.json contains an invalid Generation Workspace") from exc

    def _sequence_cast(self, manifest: ProjectManifestAny) -> tuple[list[SequenceSubject], int]:
        if isinstance(manifest, ProjectManifestV6):
            return [SequenceSubject.model_validate(s.model_dump(by_alias=True)) for s in manifest.sequence.subjects], manifest.sequence.next_subject_number
        # Read-only migration: normalize each legacy Workspace using its own rules,
        # then merge exact mappings. Never infer identity from the spelling of an ID.
        generations, _ = self._generations(manifest)
        normalized = []
        for generation in generations:
            workspace = self._validate_persisted_workspace(generation, manifest.project_id, manifest.name)
            normalized.append(generation.model_copy(update={"workspace": workspace.model_dump(by_alias=True)}))
        return self._merge_generation_subjects([], 1, normalized, migration=True)

    @staticmethod
    def _merge_generation_subjects(
        subjects: list[SequenceSubject], next_number: int, generations: list[GenerationManifest], *,
        allow_new: bool = True, migration: bool = False,
    ) -> tuple[list[SequenceSubject], int]:
        """Validate before any write; allocation joins the same manifest transaction."""
        by_id = {s.id: s.model_copy(deep=True) for s in subjects}
        by_number = {s.number: s.id for s in subjects}
        additions = {}
        watermark = next_number
        for generation in generations:
            local = [SubjectRecord.model_validate(s) for s in generation.workspace.get("subjects", [])]
            if len({s.id for s in local}) != len(local) or len({s.number for s in local}) != len(local):
                raise ProjectStoreError("Sequence cast conflict: duplicate local Subject identity")
            for subject in local:
                existing = by_id.get(subject.id) or additions.get(subject.id)
                if existing is not None and existing.number != subject.number:
                    raise ProjectStoreError(f"Sequence cast conflict: Subject {subject.id!r} has different numbers; no changes saved")
                if subject.number in by_number and by_number[subject.number] != subject.id:
                    raise ProjectStoreError(f"Sequence cast conflict: Subject number {subject.number} has different IDs; no changes saved")
                if existing is None:
                    if not allow_new:
                        raise ProjectStoreError("Sequence cast conflict: local Subject is absent from canonical authority")
                    additions[subject.id] = SequenceSubject(id=subject.id, number=subject.number)
                    by_number[subject.number] = subject.id
            known = {s.id for s in local}
            required = structural_subject_ids(generation.workspace.get("assets", []))
            if not required <= known:
                raise ProjectStoreError("Sequence cast conflict: Picture association requires a local canonical Subject record")
            if migration:
                watermark = max(watermark, generation.workspace.get("nextSubjectNumber", 1))
        for subject in sorted(additions.values(), key=lambda s: (s.number, s.id)):
            if not migration and subject.number != next_number:
                raise ProjectStoreError(f"Sequence Subject allocation conflict: expected Subject {next_number}; reload the Project before creating a Subject")
            by_id[subject.id] = subject
            next_number = max(next_number, subject.number + 1)
        return sorted(by_id.values(), key=lambda s: (s.number, s.id)), max(next_number, watermark)

    def _cast_for_write(self, existing, generations, *, canonical_candidates=None):
        subjects, next_number = self._sequence_cast(existing)
        # Older formats may only have implicit identity records. Persist their own
        # normalized subset on upgrade, never the entire cast.
        if not isinstance(existing, ProjectManifestV6):
            for generation in generations:
                workspace = self._validate_persisted_workspace(generation, existing.project_id, existing.name)
                generation.workspace = {**generation.workspace,
                    "subjects": [s.model_dump() for s in workspace.subjects],
                    "nextSubjectNumber": workspace.next_subject_number}
        # A submitted Workspace can introduce a new canonical identity before
        # it has any structural asset (for example, an author-created dormant
        # Subject). Preserve that identity in the Sequence while the generation
        # snapshot itself is pruned to structural participants.
        if canonical_candidates:
            by_id = {subject.id: subject for subject in subjects}
            by_number = {subject.number: subject.id for subject in subjects}
            validated_candidates = [SubjectRecord.model_validate(candidate) for candidate in canonical_candidates]
            for candidate in sorted(validated_candidates, key=lambda item: (item.number, item.id)):
                existing_subject = by_id.get(candidate.id)
                if existing_subject is not None:
                    if existing_subject.number != candidate.number:
                        raise ProjectStoreError(f"Sequence cast conflict: Subject {candidate.id!r} has different numbers; no changes saved")
                    continue
                if candidate.number in by_number and by_number[candidate.number] != candidate.id:
                    raise ProjectStoreError(f"Sequence cast conflict: Subject number {candidate.number} has different IDs; no changes saved")
                if candidate.number != next_number:
                    raise ProjectStoreError(f"Sequence Subject allocation conflict: expected Subject {next_number}; reload the Project before creating a Subject")
                by_id[candidate.id] = SequenceSubject(id=candidate.id, number=candidate.number)
                by_number[candidate.number] = candidate.id
                next_number = candidate.number + 1
            subjects = sorted(by_id.values(), key=lambda item: (item.number, item.id))
        subjects, next_number = self._merge_generation_subjects(subjects, next_number, generations)
        return {"subjects": subjects, "nextSubjectNumber": next_number}

    @staticmethod
    def _with_character_profiles(cast, character_profiles):
        """Apply explicit author edits without creating a second profile store."""
        subjects = cast["subjects"]
        if any(not isinstance(subject, SequenceSubject) for subject in subjects):
            raise ProjectStoreError("Character Profile application requires canonical SequenceSubject values")
        if character_profiles is None:
            return cast
        updates = {}
        for update in character_profiles:
            subject_id = update["subjectId"] if isinstance(update, dict) else update.subject_id
            profile = update["characterProfile"] if isinstance(update, dict) else update.character_profile
            if subject_id in updates:
                raise ProjectStoreError("Character Profile updates must not repeat a Subject")
            updates[subject_id] = CharacterProfile.model_validate(profile)
        known = {subject.id for subject in cast["subjects"]}
        unknown = set(updates) - known
        if unknown:
            raise ProjectStoreError("Character Profile references a Subject outside this Sequence")
        return {
            **cast,
            "subjects": [
                subject.model_copy(update={"character_profile": updates.get(subject.id, subject.character_profile)})
                for subject in subjects
            ],
        }

    def _validate_manifest_structure(self, directory: Path, manifest: ProjectManifestAny) -> None:
        """Validate summary-safe structure without constructing Workspace models."""
        generations, _ = self._generations(manifest)
        for generation in generations:
            for asset_file in generation.asset_files:
                self._safe_project_relative_path(directory, asset_file.path)

    def _validate_manifest_asset_mappings(self, manifest: ProjectManifestAny) -> None:
        """Full validation used by content-dependent operations."""
        generations, _ = self._generations(manifest)
        for generation in generations:
            self._validate_generation_asset_mapping(generation)
            self._validate_persisted_workspace(generation, manifest.project_id, manifest.name)

    @staticmethod
    def _reference_digest(entry: AssetFileManifest) -> str | None:
        # Pictures already encode SHA-256 in their immutable filename.
        stem = Path(entry.path).stem
        return entry.content_sha256 or (stem if re.fullmatch(r"[0-9a-f]{64}", stem) else None)

    def _reference_payload(self, directory, entry):
        path = self._safe_project_relative_path(directory, entry.path)
        if not path.exists():
            return "missing", None
        try:
            if entry.kind != "image":
                self._verify_media(path, entry.content_sha256, entry.byte_length)
                return None, None
            if path.is_symlink() or not path.is_file():
                return "corrupt", None
            payload = path.read_bytes()
            if self._reference_digest(entry) and hashlib.sha256(payload).hexdigest() != self._reference_digest(entry):
                return "corrupt", None
            return None, payload
        except (OSError, AssetPayloadError):
            return "corrupt", None

    def _hydrate_workspace(
        self,
        directory: Path,
        project_id: str,
        name: str,
        workspace_payload: dict[str, Any],
        asset_files: list[AssetFileManifest],
        *, verified_payloads: dict | None = None,
    ) -> Workspace:
        # Per-hydration snapshot only; sync may share it within its locked operation.
        verified_payloads = {} if verified_payloads is None else verified_payloads
        workspace_data = json.loads(json.dumps(workspace_payload))
        if not isinstance(workspace_data, dict):
            raise MalformedProjectError("project.json Generation workspace must be an object")
        stored_project_id = workspace_data.pop("projectId", None)
        if stored_project_id is not None and stored_project_id != project_id:
            raise MalformedProjectError("project.json projectId does not agree with its Workspace")
        stored_project_name = workspace_data.pop("projectName", None)
        if stored_project_name is not None and stored_project_name != name:
            raise MalformedProjectError("project.json name does not agree with its Workspace")
        workspace_data["projectId"] = project_id
        workspace_data["projectName"] = name
        assets_by_id = {asset_file.asset_id: asset_file for asset_file in asset_files}

        for asset in workspace_data.get("assets", []):
            if not isinstance(asset, dict):
                continue
            entry = assets_by_id.get(asset.get("id"))
            asset.pop("mediaPayload", None)
            if entry is None:
                continue
            identity = (directory, entry.path, self._reference_digest(entry), entry.byte_length)
            if identity in verified_payloads:
                status, payload = verified_payloads[identity]
            else:
                status, payload = self._reference_payload(directory, entry)
                verified_payloads[identity] = status, payload
            if status:
                asset["attached"] = False
                asset["payloadAvailability"] = status
                asset["payloadSha256"] = self._reference_digest(entry)
                asset["image"] = None
            elif entry.kind == "image":
                asset["image"] = {"data_url": _data_url(entry.mime_type, payload),
                                  "width": entry.width, "height": entry.height}
            else:
                asset["mediaPayload"] = {"sha256": entry.content_sha256,
                    "byteLength": entry.byte_length, "projectId": project_id}

        try:
            workspace = validate_authoring_workspace(workspace_data)
        except Exception as exc:
            raise MalformedProjectError("project.json contains an invalid Generation Workspace") from exc
        if workspace.project_id != project_id or workspace.project_name != name:
            raise MalformedProjectError("project.json identity does not agree with its Workspace")
        return workspace

    def _document(self, directory: Path) -> ProjectDocument:
        manifest = self._read_manifest(directory)
        generations, active_id = self._generations(manifest)
        active = next(generation for generation in generations if generation.generation_id == active_id)
        workspace = self._hydrate_workspace(
            directory, manifest.project_id, manifest.name, active.workspace, active.asset_files
        )
        language = self._dialogue_language(manifest)
        workspace = self._with_effective_dialogue_language(workspace, language)
        return ProjectDocument(
            manifest.project_id, manifest.name, manifest.created_at, manifest.updated_at,
            workspace, active_id, len(generations),
            _sequence_summary(generations, active_id, *self._sequence_cast(manifest)),
            self._sequence_context(manifest),
            active.continuity.incoming_state,
            active.continuity.intended_outgoing_state,
            active.planning.model_copy(deep=True),
            self._story_idea(manifest), self._story_creative_direction(manifest), self._story_planning_style(manifest),
            active.story_memory_after.model_copy(deep=True),
            self._manifest_fingerprint(manifest), language, self._output_summary(directory, active),
            self._project_edit_fingerprint(manifest),
        )

    def _externalize_workspace(
        self, directory: Path, workspace: Workspace, project_id: str, name: str,
        canonical_files: list[AssetFileManifest] = (), canonical_assets: list[dict] = (),
        *, canonical_project_id: str | None = None,
    ) -> tuple[dict[str, Any], list[AssetFileManifest], list[tuple[Path, bytes | Path]], Workspace]:
        normalized_workspace = workspace.model_copy(update={"project_id": project_id, "project_name": name})
        workspace_data = normalized_workspace.model_dump(mode="json", by_alias=True)
        # Project identity is authoritative at the manifest level. It is injected
        # back during hydration for the existing API/UI contract.
        workspace_data.pop("projectId", None)
        workspace_data.pop("projectName", None)
        asset_files: list[AssetFileManifest] = []
        pending_blobs: list[tuple[Path, bytes | Path]] = []
        canonical = {entry.asset_id: entry for entry in canonical_files}
        canonical_metadata = {asset["id"]: asset for asset in canonical_assets}
        for asset in workspace_data.get("assets", []):
            availability = asset.pop("payloadAvailability", None)
            historical_digest = asset.pop("payloadSha256", None)
            media = asset.pop("mediaPayload", None)
            entry = canonical.get(asset["id"])
            if availability and asset["id"] in canonical_metadata:
                asset["attached"] = canonical_metadata[asset["id"]].get("attached", True)
            image = asset.get("image")
            digest = None
            if image and image.get("data_url"):
                digest = hashlib.sha256(_parse_image_data_url(image["data_url"], asset["id"])[1]).hexdigest()
            elif media:
                digest = media["sha256"]
            else:
                digest = historical_digest
            legacy_unavailable = (entry is not None and self._reference_digest(entry) is None
                                  and availability and not image and not media
                                  and self._reference_payload(directory, entry)[0] is not None)
            if entry and entry.kind == asset["kind"] and (legacy_unavailable or
                    (digest is not None and digest == self._reference_digest(entry))) and not (media or {}).get("uploadId"):
                # Carry canonical identity, never publish stale browser bytes as a repair.
                if media and (asset["mimeType"] != entry.mime_type or
                        media["byteLength"] != entry.byte_length
                        or media.get("projectId") != (canonical_project_id or project_id)):
                    raise AssetPayloadError("Saved media descriptor differs from current Reference authority")
                asset_files.append(entry.model_copy(deep=True))
                if entry.kind == "image":
                    asset["image"] = {"width": entry.width, "height": entry.height}
                continue
            if availability and not image and not media:
                raise AssetPayloadError("Unavailable payload is not current Reference authority; reload the Project")
            if asset.get("kind") != "image":
                if media is not None:
                    reference = MediaPayload.model_validate(media)
                    source = self._media_source_identity(reference, asset["kind"], asset["mimeType"])
                    folder = "videos" if asset["kind"] == "video" else "audio"
                    relative = f"assets/{folder}/{reference.sha256}.bin"
                    destination = self._safe_project_relative_path(directory, relative)
                    pending_blobs.append((destination, source))
                    asset_files.append(AssetFileManifest(assetId=asset["id"], kind=asset["kind"],
                        path=relative, mimeType=asset["mimeType"], contentSha256=reference.sha256,
                        byteLength=reference.byte_length))
                continue
            image = asset.pop("image", None)
            if not isinstance(image, dict) or not image.get("data_url"):
                raise AssetPayloadError(f"image asset {asset.get('id')!r} has no image payload")
            mime_type, payload = _parse_image_data_url(image["data_url"], asset.get("id", ""))
            relative_path = f"assets/pictures/{_content_addressed_asset_file_name(mime_type, payload)}"
            payload_path = self._safe_project_relative_path(directory, relative_path)
            pending_blobs.append((payload_path, payload))
            asset_files.append(AssetFileManifest(
                assetId=asset["id"], kind="image", path=relative_path, mimeType=mime_type,
                width=image.get("width"), height=image.get("height"),
            ))
            asset["image"] = {"width": image.get("width"), "height": image.get("height")}
        normalized_workspace = normalized_workspace.model_copy(deep=True)
        for asset in normalized_workspace.assets:
            if asset.media_payload is not None:
                asset.media_payload = asset.media_payload.model_copy(update={"upload_id": None, "project_id": project_id})
        return workspace_data, asset_files, pending_blobs, normalized_workspace

    def _write_manifest(
        self, directory: Path, manifest: ProjectManifest, pending_blobs: list[tuple[Path, bytes | Path]],
    ) -> None:
        # Revalidate nested objects at the canonical boundary as callers may have
        # edited a typed instance in memory after its original construction.
        manifest = ProjectManifest.model_validate(manifest.model_dump(by_alias=True))
        # Existing updatedAt is also the ordering stamp on asynchronous receipts.
        # Keep it strictly increasing, including commits in the same clock tick.
        if (directory / "project.json").is_file():
            manifest.updated_at = self._manifest_timestamp(self._read_manifest(directory).updated_at, manifest.updated_at)
        self._merge_generation_subjects(manifest.sequence.subjects, manifest.sequence.next_subject_number,
                                        manifest.sequence.generations, allow_new=False)
        # Project identity belongs to the manifest. Canonicalize every
        # carried-forward snapshot at the final write boundary so structural
        # upgrades from v1 cannot leak the old embedded identity fields.
        canonical_generations = [
            generation.model_copy(update={
                "workspace": {
                    key: value
                    for key, value in generation.workspace.items()
                    if key not in {"projectId", "projectName"}
                } | {"dialogueLanguage": manifest.sequence.dialogue_language.value},
            })
            for generation in manifest.sequence.generations
        ]
        manifest = manifest.model_copy(update={
            "sequence": manifest.sequence.model_copy(update={"generations": canonical_generations}),
        })
        media_sizes = {self._safe_project_relative_path(directory, e.path): e.byte_length
                       for g in manifest.sequence.generations for e in g.asset_files if e.kind != "image"}
        for payload_path, payload in pending_blobs:
            payload_path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(payload, Path):
                self._copy_media(payload, payload_path, digest=payload_path.stem, size=media_sizes.get(payload_path))
                continue
            if payload_path.exists():
                if payload_path.is_symlink() or not payload_path.is_file() or payload_path.read_bytes() != payload:
                    raise ProjectStoreError("content-addressed image path contains different content")
            else:
                self._atomic_write(payload_path, payload)
        self._atomic_write(
            directory / "project.json",
            (json.dumps(manifest.model_dump(mode="json", by_alias=True), ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )

    @_project_transaction
    def create_project(
        self,
        workspace: Workspace,
        *,
        sequence_subjects: list[SequenceSubject] | None = None,
        sequence_next_subject_number: int | None = None,
        character_profiles=None,
        story_idea: str = "",
        story_creative_direction: StoryCreativeDirection | None = None,
        story_planning_style: StoryPlanningStyle | None = None,
        story_memory: StoryContinuityMemory | None = None,
        sequence_context: str = "",
        incoming_state: str = "",
        intended_outgoing_state: str = "",
        planning: GenerationPlanning | dict[str, Any] | None = None,
        dialogue_language: DialogueLanguage | None = None,
    ) -> ProjectDocument:
        self._ensure_root()
        project_id = self._new_project_id()
        generation_id = self._new_generation_id()
        directory = self._project_directory(project_id)
        staging = self.root / f".project-{project_id}.{uuid.uuid4().hex}.creating"
        created_at = self._now()
        name = workspace.project_name.strip() or "Untitled Project"
        try:
            staging.mkdir()
            for relative in ("assets/pictures", "assets/videos", "assets/audio", "outputs", "workflows", "provenance"):
                (staging / relative).mkdir(parents=True, exist_ok=True)
            workspace_data, asset_files, pending_blobs, normalized_workspace = self._externalize_workspace(
                staging, workspace, project_id, name
            )
            initial_planning = _merged_planning(None, planning)
            if initial_planning.shot_count is None:
                initial_planning.shot_count = workspace.shots or 1
            generation = GenerationManifest(
                generationId=generation_id, createdAt=created_at, updatedAt=created_at,
                workspace=workspace_data, assetFiles=asset_files,
                continuity=GenerationContinuityManifest(
                    incomingState=incoming_state,
                    intendedOutgoingState=intended_outgoing_state,
                ),
                planning=initial_planning,
                storyMemoryAfter=(story_memory or StoryContinuityMemory()).model_copy(deep=True),
            )
            self._validate_generation_asset_mapping(generation)
            if sequence_subjects is None:
                if sequence_next_subject_number is not None:
                    raise ProjectStoreError("Sequence allocation watermark requires sequenceSubjects")
                canonical_subjects = [
                    SequenceSubject(id=subject.id, number=subject.number)
                    for subject in workspace.subjects
                ]
                next_subject_number = workspace.next_subject_number
            else:
                canonical_subjects = [subject.model_copy(deep=True) for subject in sequence_subjects]
                minimum_watermark = max([1, *(subject.number + 1 for subject in canonical_subjects)], default=1)
                if sequence_next_subject_number is not None and sequence_next_subject_number < minimum_watermark:
                    raise ProjectStoreError("Sequence allocation watermark is below the canonical Subject range")
                next_subject_number = sequence_next_subject_number or minimum_watermark
                canonical_subjects, next_subject_number = self._merge_generation_subjects(
                    canonical_subjects, next_subject_number, [generation], migration=True,
                )
            cast = self._with_character_profiles(
                {"subjects": canonical_subjects, "nextSubjectNumber": next_subject_number},
                character_profiles,
            )
            effective_language = dialogue_language or workspace.dialogue_language
            manifest = ProjectManifest(
                schemaVersion=PROJECT_SCHEMA_VERSION, storyIdea=story_idea,
                storyCreativeDirection=story_creative_direction or StoryCreativeDirection(),
                storyPlanningStyle=story_planning_style or StoryPlanningStyle(),
                projectId=project_id, name=name,
                createdAt=created_at, updatedAt=created_at,
                sequence=SequenceManifest(
                    activeGenerationId=generation_id,
                    dialogueLanguage=effective_language,
                    **cast,
                    generations=[generation],
                    continuityContext=sequence_context,
                ),
            )
            self._write_manifest(staging, manifest, pending_blobs)
            if directory.exists():
                raise ProjectStoreError(f"project directory for {project_id} already exists")
            os.replace(staging, directory)
            return self._document(directory)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def load_project(self, project_id: str) -> ProjectDocument:
        return self._document(self._project_directory(project_id, must_exist=True))

    @staticmethod
    def _manifest_fingerprint(manifest: ProjectManifestAny) -> str:
        payload = json.dumps(manifest.model_dump(mode="json", by_alias=True), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _project_edit_fingerprint(manifest: ProjectManifestAny) -> str:
        """Execution sync may rebase only over renderer-owned history changes.

        References, all authoring, Sequence/navigation, Selected Take and observed
        continuity remain covered. This is computed metadata, not persisted state.
        """
        data = manifest.model_dump(mode="json", by_alias=True)
        data.pop("updatedAt", None)
        for generation in data.get("sequence", {}).get("generations", []):
            generation.pop("updatedAt", None)
            generation.pop("renderSubmissions", None)
            # Automatic output import appends immutable Takes; selection/evidence
            # remain protected by selectedTakeId/takeObservations and handoff fields.
            generation["takes"] = [take for take in generation.get("takes", [])
                                   if take.get("sourceKind") != "comfyui_submission"]
        payload = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @classmethod
    def _revision_receipt(cls, manifest):
        return {"projectFingerprint": cls._manifest_fingerprint(manifest),
                "projectEditFingerprint": cls._project_edit_fingerprint(manifest),
                "projectUpdatedAt": manifest.updated_at}

    @classmethod
    def _execution_sync_matches(cls, manifest, fingerprint, edit_fingerprint):
        return (fingerprint == cls._manifest_fingerprint(manifest)
                or (edit_fingerprint is not None and edit_fingerprint == cls._project_edit_fingerprint(manifest)))

    @staticmethod
    def _story_revision_fingerprint(manifest: ProjectManifestAny) -> str:
        """Full Story authority for safely rebasing a scoped Revision response."""
        data = manifest.model_dump(mode="json", by_alias=True)
        data.pop("updatedAt", None)
        sequence = data.get("sequence", {})
        sequence.pop("activeGenerationId", None)
        generations, _ = ProjectStore._generations(manifest)
        for saved, generation in zip(sequence.get("generations", []), generations):
            saved.pop("updatedAt", None)
            for field in ("takes", "renderSubmissions", "takeObservations", "selectedTakeId"):
                saved.pop(field, None)
            saved["observedEndState"] = observation_projection(generation)
        payload = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @_project_transaction
    def read_story_snapshot(self, project_id: str) -> StoryProjectSnapshot:
        """Read the full committed Sequence from one manifest without writing it."""
        directory = self._project_directory(project_id, must_exist=True)
        manifest = self._read_manifest(directory)
        generations, _ = self._generations(manifest)
        hydrated = tuple(GenerationDocument(
            generation_id=g.generation_id, ordinal=index + 1,
            workspace=self._hydrate_workspace(directory, manifest.project_id, manifest.name, g.workspace, g.asset_files),
            incoming_state=g.continuity.incoming_state,
            intended_outgoing_state=g.continuity.intended_outgoing_state,
            planning=g.planning.model_copy(deep=True),
            story_memory_after=g.story_memory_after.model_copy(deep=True),
            story_memory_status=g.story_memory_status, observed_end_state=observation_projection(g),
        ) for index, g in enumerate(generations))
        language = self._dialogue_language(manifest)
        hydrated = tuple(item.__class__(**{**item.__dict__, "workspace": self._with_effective_dialogue_language(item.workspace, language)})
                         for item in hydrated)
        return StoryProjectSnapshot(manifest, hydrated, self._sequence_context(manifest),
                                    self._manifest_fingerprint(manifest), tuple(self._sequence_cast(manifest)[0]),
                                    dialogue_language=language)

    def story_h3_context(self, project_id: str, generation_id: str) -> dict[str, Any]:
        """Build Story H3 authority from the saved Project, never browser prose."""
        from miniconstruct.models.sequence_subject import narrative_mapping, narrative_key
        from miniconstruct.story_operations import _prose_mentions
        directory = self._project_directory(project_id, must_exist=True)
        manifest = self._read_manifest(directory)
        generations, _ = self._generations(manifest)
        index = self._generation_index(generations, generation_id)
        current = self._generation_metadata(manifest, generations[index], index)
        if not current.planning.scene_plan.strip():
            raise ProjectStoreError("Save a Scene Plan for this Generation before Generate from Story")
        from miniconstruct.models.h3_continuity import H3ContinuityContext
        continuity = self.h3_continuity_context(project_id, generation_id) or H3ContinuityContext()
        prose = narrative_key(current.planning.scene_plan + " " + self._story_idea(manifest))
        present = {subject.id for subject in current.workspace.subjects}
        # Short ordinary words are ambiguous without a local Subject binding.
        # Historical aliases are recognition metadata, not generative names.
        ambiguous = {"may", "will", "mark", "hope", "rose", "grace"}
        relevant = [subject for subject in self._sequence_cast(manifest)[0]
                    if subject.id in present or any(
                        narrative_key(name) not in ambiguous and _prose_mentions(prose, name)
                        for name in [subject.narrative_name, *subject.narrative_aliases] if name)]
        return {
            "storyPlanPremise": self._story_idea(manifest),
            "sequenceContext": self._sequence_context(manifest),
            "orderedScenePlans": [
                {"generationId": g.generation_id, "ordinal": ordinal,
                 "scenePlan": g.planning.scene_plan, "current": g.generation_id == generation_id}
                for ordinal, g in enumerate(generations, 1)
            ],
            "currentGeneration": {
                "generationId": generation_id, "ordinal": current.ordinal,
                "scenePlan": current.planning.scene_plan,
                "incomingState": current.incoming_state,
                "intendedOutgoingState": current.intended_outgoing_state,
                "relationshipToPrevious": current.planning.relationship_to_previous,
                "shotCount": current.planning.shot_count,
                "cameraIntent": current.planning.camera_intent,
                "dialogue": current.workspace.dialogue,
                "durationSeconds": current.workspace.duration_seconds,
                "participatingSubjects": [s.model_dump(mode="json") for s in current.workspace.subjects],
            },
            "storyMemoryBefore": {
                "recentSummary": continuity.recent_summary,
                "durableFacts": continuity.durable_facts,
            },
            "characterProfiles": narrative_mapping(relevant, include_character_profiles=True),
            "storyCreativeDirection": self._story_creative_direction(manifest).model_dump(mode="json", by_alias=True),
        }

    @_project_transaction
    def read_story_operation_snapshot(self, project_id, anchor_id, instruction=""):
        from miniconstruct.story_operations import bounded_story_snapshot
        return bounded_story_snapshot(self, project_id, anchor_id, instruction)

    @_project_transaction
    def read_story_insert_snapshot(self, project_id, upstream_id, instruction=""):
        from miniconstruct.story_operations import bounded_insert_snapshot
        return bounded_insert_snapshot(self, project_id, upstream_id, instruction)

    @_project_transaction
    def read_story_replan_snapshot(self, project_id, start_id, end_id, instruction=""):
        from miniconstruct.story_operations import bounded_replan_snapshot
        return bounded_replan_snapshot(self, project_id, start_id, end_id, instruction)

    @staticmethod
    def _set_story_authored_dialogue(generation, dialogue: str, language: DialogueLanguage) -> None:
        """Persist the known Story authoring language as an identity resolution."""
        generation.workspace["dialogue"] = dialogue
        resolution = identity_dialogue_resolution(dialogue, language)
        generation.workspace["dialogueResolution"] = (
            resolution.model_dump(mode="json", by_alias=True) if resolution is not None else None
        )

    @staticmethod
    def _sync_story_planned_shots(generation, item, *, is_new_generation: bool) -> None:
        """Apply a planned count to new Generations or when existing Story intent changes."""
        previous_planned_count = generation.planning.shot_count
        if is_new_generation or item.shot_count != previous_planned_count:
            generation.workspace["shots"] = item.shot_count

    @_project_transaction
    def apply_story_plan_next(self, project_id, draft):
        from miniconstruct.models.story_operations import StoryNextProposal
        from miniconstruct.story_operations import validate_next_proposal, next_memory_states
        draft = StoryNextProposal.model_validate(draft.model_dump(by_alias=True))
        authority = draft.authority
        snapshot = self.read_story_operation_snapshot(project_id, authority.anchor_generation_id, authority.instruction)
        validate_next_proposal(snapshot, draft, project_id)
        existing = snapshot.source.manifest
        directory = self._project_directory(project_id, must_exist=True)
        originals, active_id = self._generations(existing)
        memory_states = next_memory_states(snapshot, draft, allocate=True)
        generations = list(originals)
        now = self._now()
        for item, memory_after in zip(draft.slots, memory_states):
            generation, blobs = self._prepare_generation(directory, existing, now=now)
            if blobs:
                raise ProjectStoreError("A blank Generation must not introduce asset blobs")
            generation.story_memory_after = memory_after.model_copy(deep=True)
            generation.story_memory_status = "trusted"
            generation.workspace["mode"] = generations[-1].workspace["mode"]
            self._materialize_story_references(generation, item, snapshot.source, originals)
            self._carry_story_environment(generation, generations[-1], item.relationship_to_previous)
            generation.workspace["durationSeconds"] = item.duration_seconds or originals[0].workspace["durationSeconds"]
            self._sync_story_planned_shots(generation, item, is_new_generation=True)
            self._set_story_authored_dialogue(generation, item.dialogue, self._dialogue_language(existing))
            generation.continuity = GenerationContinuityManifest(
                incomingState=item.incoming_state, intendedOutgoingState=item.intended_outgoing_state)
            generation.planning = GenerationPlanning(
                scenePlan=item.scene_plan, relationshipToPrevious=item.relationship_to_previous, cameraIntent=item.camera_intent,
                shotCount=item.shot_count)
            self._validate_generation_asset_mapping(generation)
            self._validate_persisted_workspace(generation, existing.project_id, existing.name)
            generations.append(generation)
        # Never overwrite an external writer during preparation. Proposal staleness
        # above remains bounded; this full check protects only the commit interval.
        if self._manifest_fingerprint(self._read_manifest(directory)) != snapshot.source.fingerprint:
            raise StaleProjectSnapshotError("Project changed during Apply; retry")
        return self._commit_sequence(directory, existing, generations, active_id,
                                     updated_at=now)

    @_project_transaction
    def apply_story_plan_insert(self, project_id, draft):
        """Atomically place signed, new Generations between protected A and B."""
        from miniconstruct.models.story_operations import StoryInsertProposal
        from miniconstruct.story_operations import next_memory_states, validate_insert_proposal

        draft = StoryInsertProposal.model_validate(draft.model_dump(by_alias=True))
        authority = draft.authority
        try:
            snapshot = self.read_story_insert_snapshot(project_id, authority.upstream_generation_id, authority.instruction)
        except ValueError as exc:
            # A proposal may outlive structural editing. On Apply that is a
            # stale authority conflict, not an invitation to reinterpret A.
            raise StaleProjectSnapshotError("Insert Between Generation A no longer exists or is no longer eligible; plan again") from exc
        validate_insert_proposal(snapshot, draft, project_id)
        existing = snapshot.source.manifest
        directory = self._project_directory(project_id, must_exist=True)
        originals, active_id = self._generations(existing)
        original_ids = [generation.generation_id for generation in originals]
        try:
            upstream_index = original_ids.index(authority.upstream_generation_id)
        except ValueError as exc:  # Defensive: a freshly recomputed snapshot should already catch this.
            raise StaleProjectSnapshotError("Insert Between Generation A no longer exists; plan again") from exc
        if (upstream_index + 1 >= len(originals)
                or originals[upstream_index + 1].generation_id != authority.downstream_generation_id):
            raise StaleProjectSnapshotError("Insert Between Generation B is no longer A's immediate successor; plan again")

        memory_states = next_memory_states(snapshot, draft, allocate=True)
        now, inserted = self._now(), []
        for item, memory_after in zip(draft.slots, memory_states):
            generation, blobs = self._prepare_generation(directory, existing, now=now)
            if blobs:
                raise ProjectStoreError("A blank Generation must not introduce asset blobs")
            generation.workspace["mode"] = originals[upstream_index].workspace["mode"]
            generation.story_memory_after = memory_after.model_copy(deep=True)
            generation.story_memory_status = "trusted"
            self._materialize_story_references(generation, item, snapshot.source, originals)
            self._carry_story_environment(generation, inserted[-1] if inserted else originals[upstream_index], item.relationship_to_previous)
            generation.workspace["durationSeconds"] = item.duration_seconds or originals[0].workspace["durationSeconds"]
            self._sync_story_planned_shots(generation, item, is_new_generation=True)
            self._set_story_authored_dialogue(generation, item.dialogue, self._dialogue_language(existing))
            generation.continuity = GenerationContinuityManifest(
                incomingState=item.incoming_state, intendedOutgoingState=item.intended_outgoing_state)
            generation.planning = GenerationPlanning(
                scenePlan=item.scene_plan, relationshipToPrevious=item.relationship_to_previous, cameraIntent=item.camera_intent,
                shotCount=item.shot_count)
            self._validate_generation_asset_mapping(generation)
            self._validate_persisted_workspace(generation, existing.project_id, existing.name)
            inserted.append(generation)

        # All preparation is read-only. This final full-manifest comparison means
        # an apply can make exactly one manifest replacement or none at all.
        if self._manifest_fingerprint(self._read_manifest(directory)) != snapshot.source.fingerprint:
            raise StaleProjectSnapshotError("Project changed during Insert Between Apply; plan again")
        downstream = [generation.model_copy(deep=True) for generation in originals[upstream_index + 1:]]
        # Preserve each downstream stored snapshot exactly. Insertion changes its
        # causal predecessor, so the normal Generation-memory invalidation path
        # marks known snapshots for review while retaining legacy_unknown.
        self._invalidate_memories(downstream, 0)
        generations = originals[:upstream_index + 1] + inserted + downstream
        return self._commit_sequence(directory, existing, generations, active_id, updated_at=now)

    @_project_transaction
    def apply_story_plan_replan(self, project_id, draft):
        """Atomically rewrite only signed existing C..D Story fields and memory."""
        from miniconstruct.models.story_operations import StoryReplanProposal
        from miniconstruct.story_operations import replan_memory_states, validate_replan_proposal

        draft = StoryReplanProposal.model_validate(draft.model_dump(by_alias=True))
        authority = draft.authority
        try:
            snapshot = self.read_story_replan_snapshot(
                project_id, authority.target_generation_ids[0], authority.target_generation_ids[-1], authority.instruction,
            )
        except ValueError as exc:
            raise StaleProjectSnapshotError("Replan Range targets no longer exist or are no longer eligible; plan again",
                                            category="story_authority_changed") from exc
        try:
            validate_replan_proposal(snapshot, draft, project_id)
        except StaleProjectSnapshotError as exc:
            raise StaleProjectSnapshotError(f"Replan Range context changed before Apply; proposal is stale: {exc}",
                                            category=exc.category) from exc
        existing = snapshot.source.manifest
        directory = self._project_directory(project_id, must_exist=True)
        originals, active_id = self._generations(existing)
        original_ids = [generation.generation_id for generation in originals]
        try:
            start_index = original_ids.index(authority.target_generation_ids[0])
        except ValueError as exc:
            raise StaleProjectSnapshotError("Replan Range start Generation no longer exists; plan again",
                                            category="sequence_structure_changed") from exc
        end_index = start_index + len(authority.target_generation_ids) - 1
        if original_ids[start_index:end_index + 1] != authority.target_generation_ids:
            raise StaleProjectSnapshotError("Replan Range targets are no longer contiguous in the signed order; plan again",
                                            category="sequence_structure_changed")
        upstream_id = original_ids[start_index - 1] if start_index else None
        downstream_id = original_ids[end_index + 1] if end_index + 1 < len(originals) else None
        if upstream_id != authority.upstream_generation_id or downstream_id != authority.downstream_generation_id:
            raise StaleProjectSnapshotError("Replan Range protected boundary adjacency changed; plan again",
                                            category="sequence_structure_changed")

        memory_states = replan_memory_states(snapshot, draft, allocate=True)
        now = self._now()
        generations = [generation.model_copy(deep=True) for generation in originals]
        for index, (item, memory_after) in enumerate(zip(draft.items, memory_states), start=start_index):
            generation = generations[index]
            previous_dialogue = generation.workspace.get("dialogue", "")
            # Existing target Workspace identity and assets are canonical.  The
            # reference helper is explicitly additive in this path.
            self._materialize_story_references(generation, item, snapshot.source, originals, preserve_existing=True)
            if item.dialogue != previous_dialogue:
                self._set_story_authored_dialogue(generation, item.dialogue, self._dialogue_language(existing))
            self._sync_story_planned_shots(generation, item, is_new_generation=False)
            generation.continuity = GenerationContinuityManifest(
                incomingState=item.incoming_state, intendedOutgoingState=item.intended_outgoing_state,
            )
            generation.planning = GenerationPlanning(
                scenePlan=item.scene_plan, relationshipToPrevious=item.relationship_to_previous, cameraIntent=item.camera_intent,
                shotCount=item.shot_count,
                visualHandoff=generation.planning.visual_handoff,
            )
            generation.story_memory_after = memory_after.model_copy(deep=True)
            generation.story_memory_status = "trusted"
            generation.updated_at = now
            self._validate_generation_asset_mapping(generation)
            self._validate_persisted_workspace(generation, existing.project_id, existing.name)

        # Preserve B-through-terminal snapshots exactly while honestly downgrading
        # known causal history to needs_review.  All preparation above is in-memory.
        self._invalidate_memories(generations, end_index + 1)
        if self._manifest_fingerprint(self._read_manifest(directory)) != snapshot.source.fingerprint:
            raise StaleProjectSnapshotError("Project changed during Replan Range Apply; plan again")
        return self._commit_sequence(directory, existing, generations, active_id, updated_at=now)

    @_project_transaction
    def apply_story_initial_range(self, project_id, draft):
        """Atomically retain saved G1 and append a signed bounded initial range."""
        from miniconstruct.models.story_operations import StoryInitialRangeProposal
        from miniconstruct.story_operations import initial_range_memory_states, validate_initial_range_proposal

        draft = StoryInitialRangeProposal.model_validate(draft.model_dump(by_alias=True))
        snapshot = self.read_story_snapshot(project_id)
        validate_initial_range_proposal(snapshot, draft, project_id)
        existing = snapshot.manifest
        directory = self._project_directory(project_id, must_exist=True)
        originals, active_id = self._generations(existing)
        if len(originals) != 1 or originals[0].generation_id != draft.authority.g1_generation_id:
            raise StaleProjectSnapshotError("Build Initial Range no longer has the signed sole Generation 1; plan again")

        memory_states = initial_range_memory_states(snapshot, draft, allocate=True)
        now = self._now()
        generations = []
        for index, (item, memory_after) in enumerate(zip(draft.items, memory_states)):
            if index == 0:
                # G1 remains the exact saved Generation identity and retains its own Pictures/assets.
                generation = originals[0].model_copy(deep=True)
            else:
                generation, blobs = self._prepare_generation(directory, existing, now=now)
                if blobs:
                    raise ProjectStoreError("A blank Generation must not introduce asset blobs")
                generation.workspace["mode"] = originals[0].workspace["mode"]
                self._materialize_story_references(generation, item, snapshot, originals)
                self._carry_story_environment(generation, generations[-1], item.relationship_to_previous)

            if index:
                generation.workspace["durationSeconds"] = item.duration_seconds or originals[0].workspace["durationSeconds"]
            previous_dialogue = generation.workspace.get("dialogue", "")
            if index > 0 or item.dialogue != previous_dialogue:
                self._set_story_authored_dialogue(generation, item.dialogue, self._dialogue_language(existing))
            self._sync_story_planned_shots(generation, item, is_new_generation=index > 0)
            generation.continuity = GenerationContinuityManifest(
                incomingState=item.incoming_state, intendedOutgoingState=item.intended_outgoing_state,
            )
            generation.planning = GenerationPlanning(
                scenePlan=item.scene_plan, relationshipToPrevious=item.relationship_to_previous, cameraIntent=item.camera_intent,
                shotCount=item.shot_count,
                visualHandoff=generation.planning.visual_handoff,
            )
            generation.story_memory_after = memory_after.model_copy(deep=True)
            generation.story_memory_status = "trusted"
            generation.updated_at = now
            self._validate_generation_asset_mapping(generation)
            self._validate_persisted_workspace(generation, existing.project_id, existing.name)
            generations.append(generation)

        # All work above is in-memory. Commit exactly once only if the full saved
        # authority remains unchanged through final preparation.
        if self._manifest_fingerprint(self._read_manifest(directory)) != snapshot.fingerprint:
            raise StaleProjectSnapshotError("Project changed during Build Initial Range Apply; plan again")
        return self._commit_sequence(
            directory, existing, generations, active_id,
            sequence_context=draft.sequence_context, updated_at=now,
        )

    @_project_transaction
    def apply_story_plan(self, project_id: str, draft) -> ProjectDocument:
        """Validate and prepare everything before one atomic manifest replacement."""
        from miniconstruct.models.story_planning import StoryPlanProposal
        from miniconstruct.story_planning import sanitize_recommendations

        draft = StoryPlanProposal.model_validate(draft.model_dump(by_alias=True))
        snapshot = self.read_story_snapshot(project_id)
        draft = sanitize_recommendations(draft, snapshot.allowed_refs)
        snapshot.validate_draft(draft)
        existing = snapshot.manifest
        directory = self._project_directory(project_id, must_exist=True)
        originals, active_id = self._generations(existing)
        generations = []
        now = _utc_now()
        for index, item in enumerate(draft.items):
            if item.existing_generation_id is not None:
                generation = originals[index].model_copy(deep=True)
            else:
                generation, blobs = self._prepare_generation(directory, existing, now=now)
                if blobs:
                    raise ProjectStoreError("A blank Generation must not introduce asset blobs")
                generation.workspace["mode"] = generations[-1].workspace["mode"]
                generation.story_memory_after = generations[-1].story_memory_after.model_copy(deep=True)
                generation.story_memory_status = "trusted" if generations[-1].story_memory_status == "trusted" else "needs_review"
                self._materialize_story_references(generation, item, snapshot, originals)
                self._carry_story_environment(generation, generations[-1], item.relationship_to_previous)

            # Story drafts are ephemeral; this is the one authoritative persisted dialogue field.
            previous_dialogue = generation.workspace.get("dialogue", "")
            if item.existing_generation_id is None or item.dialogue != previous_dialogue:
                self._set_story_authored_dialogue(generation, item.dialogue, self._dialogue_language(existing))
            self._sync_story_planned_shots(
                generation, item, is_new_generation=item.existing_generation_id is None,
            )
            generation.continuity = GenerationContinuityManifest(
                incomingState=item.incoming_state, intendedOutgoingState=item.intended_outgoing_state,
            )
            generation.planning = GenerationPlanning(
                scenePlan=item.scene_plan, relationshipToPrevious=item.relationship_to_previous, cameraIntent=item.camera_intent,
                shotCount=item.shot_count,
                visualHandoff=generation.planning.visual_handoff,
            )
            generation.updated_at = now
            self._validate_generation_asset_mapping(generation)
            self._validate_persisted_workspace(generation, existing.project_id, existing.name)
            generations.append(generation)
        # Detect external edits during preparation as well as changes since Build.
        if self._manifest_fingerprint(self._read_manifest(directory)) != snapshot.fingerprint:
            raise StaleProjectSnapshotError("The underlying Project changed. Rebuild the Story Planner draft.")
        return self._commit_sequence(
            directory, existing, generations, active_id,
            sequence_context=draft.sequence_context, updated_at=now,
        )

    @staticmethod
    def _carry_story_environment(target, predecessor, relationship):
        """Share committed Environment payloads through an ordered new-slot chain.

        Explicit recommendations have already been materialized. Existing targets
        never enter this path. Role isolation prevents composition/anchor carry.
        """
        if relationship not in {"continue_previous_shot", "new_shot_preserve_continuity", "match_previous_ending_frame"}:
            return

        def evidence(generation):
            files = {entry.asset_id: entry for entry in generation.asset_files}
            for asset in generation.workspace.get("assets", []):
                entry = files.get(asset["id"])
                if asset["kind"] == "image" and asset["role"] == "environment" and entry:
                    # Shared content-addressed hash/path plus semantic options.
                    key = (entry.content_sha256 or entry.path, json.dumps({
                        "notes": asset.get("notes", ""), "options": asset.get("options", {}),
                    }, sort_keys=True))
                    yield asset, entry, key

        seen = {key for _, _, key in evidence(target)}
        for asset, entry, key in evidence(predecessor):
            if key in seen:
                continue
            seen.add(key)
            copied = json.loads(json.dumps(asset))
            copied["id"] = str(uuid.uuid5(uuid.UUID(target.generation_id), json.dumps(["environment", *key])))
            copied["order"] = max((a.get("order", 0) for a in target.workspace["assets"]), default=-1) + 1
            target.workspace["assets"].append(copied)
            target.asset_files.append(entry.model_copy(update={"asset_id": copied["id"]}, deep=True))

    def _materialize_story_references(self, target, item, snapshot, originals, *, preserve_existing=False) -> None:
        """Materialize explicit visual cast, automatic identity evidence, then advisory refs.

        This helper handles automatic subject_identity Pictures. New-slot callers
        separately carry the immediate predecessor's Environment-only Pictures.
        Comparison/scale is still opt-in: its multi-Subject semantics are not
        unambiguous when a proposal selects only a subset of the linked cast.
        """
        sources = {g.generation_id: g for g in originals}
        hydrated = {g.generation_id: g.workspace for g in snapshot.generations}
        subjects = ({entry["id"]: json.loads(json.dumps(entry)) for entry in target.workspace.get("subjects", [])}
                    if preserve_existing else {})
        authority = {s.id: s for s in self._sequence_cast(snapshot.manifest)[0]}
        # Existing Generations (Replan/Revision paths) retain asset/Subject
        # authority; participation only grants materialization for new slots.
        participating = [] if preserve_existing else getattr(item, "participating_subject_ids", [])
        for subject_id in participating:
            subject = authority.get(subject_id)
            if subject is None:
                raise ProjectStoreError("Planned participating Subject is not in canonical authority")
            subjects[subject_id] = {"id": subject.id, "number": subject.number}

        # Use the operation's already bounded/selected evidence view. Exact
        # semantic duplicates use the newest legal source; distinct identity
        # records are retained in deterministic source/asset order.
        automatic = []
        semantic_seen = set()
        candidates = []
        for generation in snapshot.generations:
            for asset in generation.workspace.assets:
                pair = (generation.generation_id, asset.id)
                role = asset.role.value if hasattr(asset.role, "value") else asset.role
                kind = asset.kind.value if hasattr(asset.kind, "value") else asset.kind
                if pair not in snapshot.allowed_refs or kind != "image" or role != "subject_identity":
                    continue
                if asset.subject_identity.subject_id not in participating:
                    continue
                semantic = json.dumps({
                    "identity": asset.subject_identity.model_dump(mode="json", by_alias=True),
                    "notes": asset.notes, "options": asset.options,
                }, sort_keys=True, separators=(",", ":"), default=str)
                candidates.append((-generation.ordinal, generation.generation_id, asset.id, semantic, pair))
        for _ordinal, _source_id, _asset_id, semantic, pair in sorted(candidates):
            if semantic not in semantic_seen:
                semantic_seen.add(semantic)
                automatic.append(pair)

        pairs = [*automatic, *((reference.source_generation_id, reference.asset_id)
                                for reference in item.reference_recommendations)]
        seen_pairs = set()
        for source_id, asset_id in pairs:
            if (source_id, asset_id) in seen_pairs:
                continue
            seen_pairs.add((source_id, asset_id))
            source = sources[source_id]
            workspace = hydrated[source.generation_id]
            matches = [a for a in workspace.assets if a.id == asset_id]
            if len(matches) != 1:
                raise ProjectStoreError("Planned reference does not resolve to one source asset")
            asset = matches[0]
            if (
                preserve_existing
                and source.generation_id == target.generation_id
                and any(existing.get("id") == asset_id
                        for existing in target.workspace.get("assets", []))
            ):
                continue
            required = ([asset.subject_identity.subject_id] if asset.role == "subject_identity"
                        else asset.comparison_subject_ids if asset.role == "character_comparison_scale" else [])
            for subject_id in required:
                subject = authority.get(subject_id)
                if subject is None:
                    raise ProjectStoreError("Planned reference requires a missing source Subject")
                record = {"id": subject.id, "number": subject.number}
                if subject_id in subjects and subjects[subject_id] != record:
                    raise ProjectStoreError("Planned references have conflicting Subject authority")
                if any(other["number"] == record["number"] and other["id"] != subject_id
                       for other in subjects.values()):
                    raise ProjectStoreError("Planned references have conflicting Subject numbers")
                subjects[subject_id] = record
            # Namespace every copied identity by the new target and exact source pair.
            target_id = str(uuid.uuid5(uuid.UUID(target.generation_id),
                                      json.dumps([source.generation_id, asset.id])))
            if any(existing.get("id") == target_id for existing in target.workspace.get("assets", [])):
                continue
            copied = json.loads(json.dumps(next(a for a in source.workspace["assets"] if a["id"] == asset.id)))
            copied["id"] = target_id
            target.workspace["assets"].append(copied)
            for entry in source.asset_files:
                if entry.asset_id == asset.id:
                    target.asset_files.append(entry.model_copy(update={"asset_id": target_id}, deep=True))
        target.workspace["subjects"] = sorted(subjects.values(), key=lambda s: (s["number"], s["id"]))
        target.workspace["nextSubjectNumber"] = max(
            target.workspace.get("nextSubjectNumber", 1) if preserve_existing else 1,
            max((s["number"] + 1 for s in subjects.values()), default=1),
        )
        ids = [a["id"] for a in target.workspace["assets"]]
        if len(ids) != len(set(ids)):
            raise ProjectStoreError("Planned references produced conflicting asset identities")

    @_project_transaction
    def h3_continuity_context(self, project_id: str, generation_id: str | None = None):
        """Resolve by saved UUID (saved active UUID for older clients), without writes.

        Use only the immediate predecessor's after-memory. Current after-memory
        describes this Generation's future, so it cannot establish its start.
        """
        from miniconstruct.h3.continuity import H3ContinuityContext
        directory = self._project_directory(project_id, must_exist=True)
        manifest = self._read_manifest(directory)
        generations, active_id = self._generations(manifest)
        index = self._generation_index(generations, generation_id or active_id)
        current = generations[index]
        relation = current.planning.relationship_to_previous if index else None
        predecessor = generations[index - 1] if index and relation != "independent" else None
        context = H3ContinuityContext(
            sequenceContext=self._sequence_context(manifest),
            incomingState=current.continuity.incoming_state,
            intendedOutgoingState=current.continuity.intended_outgoing_state,
            relationshipToPrevious=relation, cameraIntent=current.planning.camera_intent)
        if predecessor:
            if relation in {"continue_previous_shot", "new_shot_preserve_continuity", "match_previous_ending_frame"}:
                context.predecessor_intended_outgoing_state = predecessor.continuity.intended_outgoing_state
                context.predecessor_observation = observation_projection(predecessor)
                if predecessor.selected_take_id:
                    take = self._take(predecessor, predecessor.selected_take_id)
                    if context.predecessor_observation is None:
                        context.warnings.append("The previous Generation has a Selected Take that has not been analyzed. Textual continuity uses planned state; Visual Handoff can independently use the selected media.")
                    elif not self._output_path(directory, take.managed_media_path).is_file():
                        context.warnings.append("Previous Selected Take source media is unavailable for review; its hash-bound observation remains usable.")
            memory = predecessor.story_memory_after
            has_memory = bool(memory.recent_summary or memory.durable_facts)
            if predecessor.story_memory_status == "trusted":
                context.recent_summary = memory.recent_summary[:1500]
                # Deliberately bounded fallback: immediate predecessor facts only.
                # Linked participating Subjects are ranked first, then unlinked facts.
                local_ids = {s["id"] for s in current.workspace.get("subjects", [])}
                relevant = [f for f in memory.durable_facts if not f.related_subject_ids or local_ids.intersection(f.related_subject_ids)]
                relevant.sort(key=lambda f: not bool(local_ids.intersection(f.related_subject_ids)))
                context.durable_facts = [f.text for f in relevant[:8]]
            elif has_memory:
                context.warnings.append(f"Predecessor Story Continuity Memory is {predecessor.story_memory_status}; omitted from H3 authority. Review it before relying on continuity.")
            if relation or context.recent_summary or context.durable_facts:
                context.predecessor_generation_id = predecessor.generation_id
        # Preserve legacy fingerprints when there is no additional authoring input.
        return context if any(context.model_dump().values()) or context.warnings else None

    def _generation_metadata(self, manifest, generation, index) -> GenerationDocument:
        """Authoring metadata only: no materialized payload or readiness assertion."""
        workspace = self._validate_persisted_workspace(generation, manifest.project_id, manifest.name)
        for asset in workspace.assets:
            asset.image = None  # Discard validation-only Picture placeholders.
            asset.media_payload = None
        return GenerationDocument(
            generation_id=generation.generation_id, ordinal=index + 1,
            workspace=self._with_effective_dialogue_language(workspace, self._dialogue_language(manifest)),
            incoming_state=generation.continuity.incoming_state,
            intended_outgoing_state=generation.continuity.intended_outgoing_state,
            planning=generation.planning.model_copy(deep=True),
            story_memory_after=generation.story_memory_after.model_copy(deep=True),
            story_memory_status=generation.story_memory_status, observed_end_state=observation_projection(generation),
        )

    def read_generation(self, project_id: str, generation_id: str) -> GenerationDocument:
        """Read one committed Generation without activating or persisting it."""
        directory = self._project_directory(project_id, must_exist=True)
        manifest = self._read_manifest(directory)
        generations, _active_id = self._generations(manifest)
        index = self._generation_index(generations, generation_id)
        generation = generations[index]
        return GenerationDocument(
            generation_id=generation.generation_id,
            ordinal=index + 1,
            workspace=self._with_effective_dialogue_language(self._hydrate_workspace(
                directory, manifest.project_id, manifest.name, generation.workspace, generation.asset_files,
            ), self._dialogue_language(manifest)),
            incoming_state=generation.continuity.incoming_state,
            intended_outgoing_state=generation.continuity.intended_outgoing_state,
            planning=generation.planning.model_copy(deep=True),
            story_memory_after=generation.story_memory_after.model_copy(deep=True),
            story_memory_status=generation.story_memory_status, observed_end_state=observation_projection(generation),
        )

    def read_predecessor_generation(self, project_id: str, generation_id: str) -> GenerationDocument | None:
        """Return the committed predecessor by current order, without activation."""
        directory = self._project_directory(project_id, must_exist=True)
        manifest = self._read_manifest(directory)
        generations, _active_id = self._generations(manifest)
        index = self._generation_index(generations, generation_id)
        if index == 0:
            return None
        predecessor = generations[index - 1]
        return GenerationDocument(
            generation_id=predecessor.generation_id,
            ordinal=index,
            workspace=self._with_effective_dialogue_language(self._hydrate_workspace(
                directory, manifest.project_id, manifest.name, predecessor.workspace, predecessor.asset_files,
            ), self._dialogue_language(manifest)),
            incoming_state=predecessor.continuity.incoming_state,
            intended_outgoing_state=predecessor.continuity.intended_outgoing_state,
            planning=predecessor.planning.model_copy(deep=True),
        )

    @_project_transaction
    def delete_project(self, project_id: str) -> None:
        canonical_id = _canonical_project_id(project_id)
        directory = self._project_directory(canonical_id, must_exist=True)
        self._ensure_no_take_import(directory)
        self._read_manifest(directory)
        if directory.is_symlink():
            raise ProjectStoreError("project directory symlinks are not supported")
        resolved = directory.resolve(strict=False)
        if resolved.parent != self.root or resolved.name != f"{_PROJECT_DIRECTORY_PREFIX}{canonical_id}":
            raise ProjectStoreError("project path escapes the configured Projects root")
        shutil.rmtree(directory)

    @_project_transaction
    def save_project(
        self,
        project_id: str,
        workspace: Workspace,
        *,
        character_profiles=None,
        story_idea: str | None = None,
        story_creative_direction: StoryCreativeDirection | None = None,
        story_planning_style: StoryPlanningStyle | None = None,
        story_memory: StoryContinuityMemory | None = None,
        story_memory_generation_id: str | None = None,
        expected_project_fingerprint: str | None = None,
        sequence_context: str | None = None,
        incoming_state: str | None = None,
        intended_outgoing_state: str | None = None,
        planning: GenerationPlanning | dict[str, Any] | None = None,
        dialogue_language: DialogueLanguage | None = None,
    ) -> ProjectDocument:
        canonical_id = _canonical_project_id(project_id)
        directory = self._project_directory(canonical_id, must_exist=True)
        existing = self._read_manifest(directory)
        base_fingerprint = self._manifest_fingerprint(existing)
        if expected_project_fingerprint is not None and expected_project_fingerprint != base_fingerprint:
            raise StaleProjectSnapshotError("Project changed since loading; reload before saving")
        generations, active_id = self._generations(existing)
        generations = [g.model_copy(deep=True) for g in generations]
        if story_memory is not None and story_memory_generation_id != active_id:
            raise StaleProjectSnapshotError("Story Memory Save must target the selected Generation ID")
        active = next(generation for generation in generations if generation.generation_id == active_id)
        name = workspace.project_name.strip() or "Untitled Project"
        # A filesystem Project has a higher, canonical Sequence authority.  Its
        # local registry is therefore only the active structural participation.
        # Standalone Workspace validation deliberately continues to retain idle
        # records because it has no equivalent authority.
        materialized_workspace = workspace.model_copy(update={
            "subjects": [subject for subject in workspace.subjects if subject.id in workspace.structural_subject_ids()],
        })
        workspace_data, asset_files, pending_blobs, normalized_workspace = self._externalize_workspace(
            directory, materialized_workspace, canonical_id, name, active.asset_files, active.workspace.get("assets", [])
        )
        replacement = GenerationManifest(
            generationId=active.generation_id,
            createdAt=active.created_at,
            updatedAt=self._now(),
            workspace=workspace_data,
            assetFiles=asset_files,
            storyMemoryAfter=active.story_memory_after.model_copy(deep=True),
            storyMemoryStatus=active.story_memory_status,
            takeObservations=active.take_observations, takes=active.takes, renderSubmissions=active.render_submissions, selectedTakeId=active.selected_take_id,
            continuity=GenerationContinuityManifest(
                incomingState=active.continuity.incoming_state if incoming_state is None else incoming_state,
                intendedOutgoingState=(
                    active.continuity.intended_outgoing_state
                    if intended_outgoing_state is None else intended_outgoing_state
                ),
            ),
            planning=_merged_planning(active.planning, planning),
        )
        self._validate_generation_asset_mapping(replacement)
        updated_generations = [replacement if generation.generation_id == active_id else generation for generation in generations]
        if story_memory is not None:
            self._save_generation_memory(updated_generations, active_id, story_memory)
        cast = self._with_character_profiles(
            self._cast_for_write(existing, updated_generations, canonical_candidates=workspace.subjects),
            character_profiles,
        )
        effective_language = dialogue_language or self._dialogue_language(existing)
        manifest = ProjectManifest(
            schemaVersion=PROJECT_SCHEMA_VERSION,
            storyIdea=self._story_idea(existing) if story_idea is None else story_idea,
            storyCreativeDirection=(self._story_creative_direction(existing)
                                    if story_creative_direction is None else story_creative_direction),
            storyPlanningStyle=(self._story_planning_style(existing)
                                if story_planning_style is None else story_planning_style),
            projectId=canonical_id, name=name,
            createdAt=existing.created_at, updatedAt=self._now(),
            sequence=SequenceManifest(
                activeGenerationId=active_id,
                dialogueLanguage=effective_language,
                **cast,
                generations=updated_generations,
                continuityContext=(
                    self._sequence_context(existing)
                    if sequence_context is None else sequence_context
                ),
            ),
        )
        if self._manifest_fingerprint(self._read_manifest(directory)) != base_fingerprint:
            raise StaleProjectSnapshotError("Project changed during Save; reload before saving")
        if self._reference_authority(active) != self._reference_authority(replacement):
            self._invalidate_reference_execution(directory, active_id, {asset.id for asset in workspace.assets})
        self._write_manifest(directory, manifest, pending_blobs)
        return self._document(directory)

    @staticmethod
    def _invalidate_memories(generations, start):
        for generation in generations[start:]:
            # Unknown history remains honestly unknown until the user supplies it.
            if generation.story_memory_status != "legacy_unknown":
                generation.story_memory_status = "needs_review"

    @classmethod
    def _save_generation_memory(cls, generations, generation_id, memory):
        index = cls._generation_index(generations, generation_id)
        generation = generations[index]
        changed = generation.story_memory_after.model_dump(mode="json") != memory.model_dump(mode="json")
        generation.story_memory_after = memory.model_copy(deep=True)
        generation.story_memory_status = "trusted"
        if changed:
            cls._invalidate_memories(generations, index + 1)

    def _commit_sequence(
        self,
        directory: Path,
        existing: ProjectManifestAny,
        generations: list[GenerationManifest],
        active_id: str,
        *,
        name: str | None = None,
        pending_blobs: list[tuple[Path, bytes | Path]] | None = None,
        story_idea: str | None = None,
        story_creative_direction: StoryCreativeDirection | None = None,
        sequence_context: str | None = None,
        updated_at: str | None = None,
        hydrate_result: bool = True,
    ) -> ProjectDocument | None:
        project_name = (name if name is not None else existing.name).strip() or "Untitled Project"
        manifest = ProjectManifest(
            schemaVersion=PROJECT_SCHEMA_VERSION,
            storyIdea=self._story_idea(existing) if story_idea is None else story_idea,
            storyCreativeDirection=(self._story_creative_direction(existing)
                                    if story_creative_direction is None else story_creative_direction),
            storyPlanningStyle=self._story_planning_style(existing),
            projectId=existing.project_id,
            name=project_name,
            createdAt=existing.created_at,
            updatedAt=updated_at or self._now(),
            sequence=SequenceManifest(
                activeGenerationId=active_id,
                dialogueLanguage=self._dialogue_language(existing),
                **self._cast_for_write(existing, generations),
                generations=generations,
                continuityContext=self._sequence_context(existing) if sequence_context is None else sequence_context,
            ),
        )
        self._write_manifest(directory, manifest, pending_blobs or [])
        # Output-only commands return their own small receipt. Hydrating unrelated
        # authoring media here can fail after a successful commit or hold the lock
        # while hashing large reference Videos.
        return self._document(directory) if hydrate_result else None

    def _mutation_context(
        self, project_id: str,
    ) -> tuple[str, Path, ProjectManifestAny, list[GenerationManifest], str]:
        canonical_id = _canonical_project_id(project_id)
        directory = self._project_directory(canonical_id, must_exist=True)
        existing = self._read_manifest(directory)
        generations, active_id = self._generations(existing)
        return canonical_id, directory, existing, [g.model_copy(deep=True) for g in generations], active_id

    @staticmethod
    def _generation_index(generations: list[GenerationManifest], generation_id: str) -> int:
        try:
            canonical_id = _canonical_uuid(generation_id, "generationId")
        except ValueError as exc:
            raise GenerationNotFoundError("generation identifier must be a UUID") from exc
        for index, generation in enumerate(generations):
            if generation.generation_id == canonical_id:
                return index
        raise GenerationNotFoundError(f"generation {canonical_id} was not found")

    def _prepare_generation(
        self, directory: Path, existing: ProjectManifestAny,
        workspace: Workspace | None = None, *, now: str | None = None,
    ) -> tuple[GenerationManifest, list[tuple[Path, bytes | Path]]]:
        """Shared normal blank/default Generation creation, without persistence."""
        source = workspace if workspace is not None else _blank_workspace(existing.project_id, existing.name)
        workspace_data, asset_files, blobs, _ = self._externalize_workspace(
            directory, source, existing.project_id, existing.name,
        )
        timestamp = now or self._now()
        generation = GenerationManifest(
            generationId=self._new_generation_id(), createdAt=timestamp, updatedAt=timestamp,
            workspace=workspace_data, assetFiles=asset_files,
        )
        self._validate_generation_asset_mapping(generation)
        return generation, blobs

    @_project_transaction
    def continue_selected_take(self, project_id: str, generation_id: str, *,
                               expected_project_fingerprint: str, take_id: str,
                               take_sha256: str) -> ProjectDocument:
        """Insert a fresh segment under exact saved source and Sequence authority."""
        from miniconstruct.take_store import invalid
        from miniconstruct.models.workspace import H3Mode
        from miniconstruct.native_continuation import predecessor_native_records
        from miniconstruct.execution import ExecutionStore, ConfigNotFound

        _, directory, existing, generations, active_id = self._mutation_context(project_id)
        if expected_project_fingerprint != self._manifest_fingerprint(existing):
            raise StaleProjectSnapshotError("Project or Selected Take changed; refresh before continuing")
        index = self._generation_index(generations, generation_id)
        source = generations[index]
        if active_id != source.generation_id:
            raise StaleProjectSnapshotError("Source Generation is no longer active; refresh before continuing")
        if source.selected_take_id is None:
            raise invalid("Select a Take first before continuing")
        take = self._take(source, source.selected_take_id)
        if str(take.id) != take_id or take.sha256 != take_sha256:
            raise StaleProjectSnapshotError("Selected Take changed; refresh before continuing")
        path = self._output_path(directory, take.managed_media_path)
        if not path.is_file():
            raise invalid("Selected Take media is missing; reattach the original file before continuing")
        self._verify_media(path, take.sha256, take.byte_size)

        generation, blobs = self._prepare_generation(directory, existing)
        # Share canonical content-addressed Pictures, never transient handoff media.
        roles = {"subject_identity", "style_appearance", "environment"}
        files = {entry.asset_id: entry for entry in source.asset_files}
        for asset in source.workspace.get("assets", []):
            if asset["kind"] == "image" and asset["role"] in roles and asset.get("attached", True):
                if asset["id"] not in files:
                    raise invalid("Source Picture media is missing; repair it before continuing")
                generation.workspace["assets"].append(json.loads(json.dumps(asset)))
                generation.asset_files.append(files[asset["id"]].model_copy(deep=True))
        for key in ("subjects", "nextSubjectNumber", "durationSeconds", "aspectRatio", "creativeInterpretation"):
            generation.workspace[key] = json.loads(json.dumps(source.workspace[key]))
        controls = source.workspace.get("creativeControls", {})
        for key in ("visualStyle", "subjectIdentityFidelity", "tonePerformance", "promptDetail"):
            if key in controls:
                generation.workspace["creativeControls"][key] = json.loads(json.dumps(controls[key]))
        generation.workspace["mode"] = H3Mode.REF2VA.value
        # Preserve a compatible native-only mode preference; resolution still uses
        # the ordinary exact-execution consumer checks, never inferred video state.
        state = self._read_execution_state(directory)
        settings = next((state.continuation_settings[g.generation_id]
                         for g in reversed(generations[:index + 1])
                         if g.generation_id in state.continuation_settings), None)
        if settings and settings.profile_id:
            try:
                profile = ExecutionStore().get("profiles", settings.profile_id)
            except ConfigNotFound:
                profile = None
            records = predecessor_native_records(self, project_id, source, state)
            mode = source.workspace["mode"]
            accepted = [r for r in records if r.confirmed and r.status == "available" and r.mode == mode]
            if (profile and profile.native_continuation and mode in {"T2VA", "Ref2VA"} and mode in profile.compatible_modes
                    and not profile.native_continuation.requires_tail_reference and len(accepted) == 1):
                generation.workspace["mode"] = mode
        generation.planning = GenerationPlanning(relationshipToPrevious="continue_previous_shot")
        # An explicitly selected picture-free successor can continue again. The
        # initial image Profile is never transformed or silently replaced.
        if (settings and settings.profile_id and profile and profile.native_continuation
                and profile.native_continuation.picture_free_i2va
                and source.workspace["mode"] == "I2VA"
                and source.planning.visual_handoff.strategy == "native_i2va_continuation"
                and len(accepted) == 1):
            from miniconstruct.i2va_contract import state_identity
            if accepted[0].i2va_state_identity == state_identity(profile.workflow, profile.native_continuation):
                generation.workspace["mode"] = "I2VA"
                # I2VA has no additional prompt-visible reference capacity.
                generation.workspace["assets"] = []
                generation.asset_files = []
                generation.planning.visual_handoff.strategy = "native_i2va_continuation"
        self._validate_generation_asset_mapping(generation)
        self._validate_persisted_workspace(generation, existing.project_id, existing.name)
        self._invalidate_memories(generations, index + 1)
        generations.insert(index + 1, generation)
        return self._commit_sequence(directory, existing, generations, generation.generation_id, pending_blobs=blobs)

    @_project_transaction
    def create_generation(
        self,
        project_id: str,
        *,
        insert_index: int | None = None,
        workspace: Workspace | None = None,
    ) -> ProjectDocument:
        """Insert a new blank (or explicitly supplied) Generation and activate it."""
        _canonical_id, directory, existing, generations, _active_id = self._mutation_context(project_id)
        if insert_index is None:
            insert_index = len(generations)
        if not isinstance(insert_index, int) or isinstance(insert_index, bool) or not 0 <= insert_index <= len(generations):
            raise InvalidGenerationInsertionError(
                f"insertIndex must be between 0 and {len(generations)}"
            )
        generation, pending_blobs = self._prepare_generation(directory, existing, workspace)
        if insert_index:
            previous = generations[insert_index - 1]
            generation.story_memory_after = previous.story_memory_after.model_copy(deep=True)
            generation.story_memory_status = "trusted" if previous.story_memory_status == "trusted" else "needs_review"
        self._invalidate_memories(generations, insert_index)
        generations.insert(insert_index, generation)
        return self._commit_sequence(directory, existing, generations, generation.generation_id, pending_blobs=pending_blobs)

    @_project_transaction
    def activate_generation(self, project_id: str, generation_id: str,
                            expected_project_fingerprint: str | None = None) -> ProjectDocument:
        _canonical_id, directory, existing, generations, _active_id = self._mutation_context(project_id)
        if (expected_project_fingerprint is not None
                and expected_project_fingerprint != self._manifest_fingerprint(existing)):
            raise StaleProjectSnapshotError("Project changed since loading; refresh before switching Generation")
        self._generation_index(generations, generation_id)
        active_id = _canonical_uuid(generation_id, "generationId")
        return self._commit_sequence(directory, existing, generations, active_id)

    @_project_transaction
    def duplicate_generation(self, project_id: str, generation_id: str | None = None) -> ProjectDocument:
        _canonical_id, directory, existing, generations, active_id = self._mutation_context(project_id)
        source_id = generation_id or active_id
        source_index = self._generation_index(generations, source_id)
        source = generations[source_index]
        now = _utc_now()
        duplicate = GenerationManifest(
            generationId=str(uuid.uuid4()),
            createdAt=now,
            updatedAt=now,
            workspace=json.loads(json.dumps(source.workspace)),
            assetFiles=[entry.model_copy(deep=True) for entry in source.asset_files],
            continuity=source.continuity.model_copy(deep=True),
            planning=source.planning.model_copy(deep=True),
            storyMemoryAfter=source.story_memory_after.model_copy(deep=True),
            storyMemoryStatus="trusted" if source.story_memory_status == "trusted" else "needs_review",
        )
        self._invalidate_memories(generations, source_index + 1)
        generations.insert(source_index + 1, duplicate)
        return self._commit_sequence(directory, existing, generations, duplicate.generation_id)

    @_project_transaction
    def reorder_generations(self, project_id: str, generation_ids: list[str]) -> ProjectDocument:
        _canonical_id, directory, existing, generations, active_id = self._mutation_context(project_id)
        if not isinstance(generation_ids, list):
            raise InvalidGenerationOrderError("generationIds must be a list")
        try:
            supplied = [_canonical_uuid(value, "generationId") for value in generation_ids]
        except ValueError as exc:
            raise InvalidGenerationOrderError(str(exc)) from exc
        expected = [generation.generation_id for generation in generations]
        if len(supplied) != len(expected) or len(set(supplied)) != len(supplied) or set(supplied) != set(expected):
            raise InvalidGenerationOrderError("generationIds must be an exact permutation of the Project Generations")
        by_id = {generation.generation_id: generation for generation in generations}
        ordered = [by_id[generation_id] for generation_id in supplied]
        boundary = next((i for i, (a, b) in enumerate(zip(expected, supplied)) if a != b), len(ordered))
        self._invalidate_memories(ordered, boundary)
        return self._commit_sequence(directory, existing, ordered, active_id)

    @_project_transaction
    def delete_generation(self, project_id: str, generation_id: str) -> ProjectDocument:
        _canonical_id, directory, existing, generations, active_id = self._mutation_context(project_id)
        index = self._generation_index(generations, generation_id)
        requested_id = generations[index].generation_id
        if requested_id != active_id:
            raise GenerationNotActiveError("only the active Generation can be deleted")
        if len(generations) == 1:
            raise FinalGenerationDeletionError("a Project must contain at least one Generation")
        replacement_id = generations[index - 1].generation_id if index > 0 else generations[index + 1].generation_id
        generations.pop(index)
        self._invalidate_memories(generations, index)
        execution_state = self._read_execution_state(directory)
        execution_state.selections.pop(requested_id, None)
        execution_state.saved_prompts.pop(requested_id, None)
        execution_state.routing.pop(requested_id, None)
        execution_state.renderer_seed_settings.pop(requested_id, None)
        execution_state.workflow_control_overrides.pop(requested_id, None)
        execution_state.continuation_settings.pop(requested_id, None)
        execution_state.accepted_continuations.pop(requested_id, None)
        execution_state.continuation_executions = [r for r in execution_state.continuation_executions if str(r.generation_id) != requested_id]
        result = self._commit_sequence(directory, existing, generations, replacement_id)
        self._prune_take_media(directory, self._generations(existing)[0], generations)
        self._write_execution_state(directory, execution_state)
        return result

    @_project_transaction
    def generation_narrative_subjects(self, project_id, workspace):
        directory = self._project_directory(project_id, must_exist=True)
        cast, _ = self._sequence_cast(self._read_manifest(directory))
        try:
            return local_narrative_subjects(workspace, cast)
        except ValueError as exc:
            raise ProjectStoreError(str(exc)) from exc

    @_project_transaction
    def set_subject_narrative_name(self, project_id: str, subject_id: str, narrative_name: str):
        """Validate a rename and replace one manifest under the shared transaction lock."""
        _, directory, existing, generations, active_id = self._mutation_context(project_id)
        cast = self._cast_for_write(existing, generations)
        subject = next((s for s in cast["subjects"] if s.id == subject_id), None)
        if subject is None:
            raise ProjectStoreError("Subject is not present in this Sequence")
        try:
            name = validate_narrative_name(narrative_name)
            aliases = []
            seen_aliases = {narrative_key(name)}
            for value in subject.narrative_aliases:
                key = narrative_key(value)
                if key not in seen_aliases:
                    aliases.append(value)
                    seen_aliases.add(key)
            if subject.narrative_name and narrative_key(subject.narrative_name) != narrative_key(name):
                old_key = narrative_key(subject.narrative_name)
                if old_key not in seen_aliases:
                    aliases.append(subject.narrative_name)
            replacement = SequenceSubject(id=subject.id, number=subject.number,
                                          narrativeName=name, narrativeAliases=aliases,
                                          characterProfile=subject.character_profile)
            subjects = [replacement if s.id == subject_id else s for s in cast["subjects"]]
            validate_cast_names(subjects)
        except ValueError as exc:
            raise ProjectStoreError(str(exc)) from exc
        manifest = ProjectManifest(
            schemaVersion=PROJECT_SCHEMA_VERSION, projectId=existing.project_id, name=existing.name,
            storyIdea=self._story_idea(existing), storyCreativeDirection=self._story_creative_direction(existing),
            storyPlanningStyle=self._story_planning_style(existing),
            createdAt=existing.created_at, updatedAt=self._now(),
            sequence=SequenceManifest(activeGenerationId=active_id, dialogueLanguage=self._dialogue_language(existing), generations=generations,
                subjects=subjects, nextSubjectNumber=cast["nextSubjectNumber"],
                continuityContext=self._sequence_context(existing)),
        )
        self._write_manifest(directory, manifest, [])
        return self._document(directory)

    @_project_transaction
    def delete_sequence_subject(self, project_id: str, subject_id: str) -> ProjectDocument:
        """Delete an unused canonical Subject without touching its references."""
        if not isinstance(subject_id, str) or not subject_id.strip() or len(subject_id) > 128:
            raise ProjectStoreError("Subject identifier is invalid")
        _canonical_id, directory, existing, generations, active_id = self._mutation_context(project_id)
        subjects, next_subject_number = self._sequence_cast(existing)
        subject = next((item for item in subjects if item.id == subject_id), None)
        if subject is None:
            raise ProjectStoreError("Subject is not present in this Sequence")
        from miniconstruct.models.observations import observation_subject_dependencies
        if any(subject_id in observation_subject_dependencies(observation) for generation in generations
               for observation in generation.take_observations):
            raise SequenceSubjectInUseError("Subject is linked by Take observations; delete the associated Takes before deleting this Subject.")
        if any(subject_id in entry.related_subject_ids for entry in
               [entry for g in generations for entry in g.story_memory_after.durable_facts + g.story_memory_after.open_threads]):
            raise SequenceSubjectInUseError("Subject is linked by Story Memory; remove its memory links first.")
        blockers = [index + 1 for index, generation in enumerate(generations)
                    if subject_id in structural_subject_ids(generation.workspace.get("assets", []))]
        if blockers:
            labels = [f"Generation {number}" for number in blockers[:5]]
            suffix = f" and {len(blockers) - 5} more" if len(blockers) > 5 else ""
            raise SequenceSubjectInUseError(
                f"{subject.display_label} cannot be deleted because it is still used by {', '.join(labels)}{suffix}."
            )
        # Remove only stale, asset-free local copies. References were checked
        # before mutation and are never cascaded, reassigned, or rewritten.
        cleaned = [generation.model_copy(update={
            "workspace": {
                **generation.workspace,
                "subjects": [record for record in generation.workspace.get("subjects", [])
                             if record.get("id") != subject_id],
            },
        }) for generation in generations]
        for generation in cleaned:
            self._validate_generation_asset_mapping(generation)
            self._validate_persisted_workspace(generation, existing.project_id, existing.name)
        manifest = ProjectManifest(
            schemaVersion=PROJECT_SCHEMA_VERSION, storyIdea=self._story_idea(existing),
            storyCreativeDirection=self._story_creative_direction(existing), projectId=existing.project_id,
            storyPlanningStyle=self._story_planning_style(existing),
            name=existing.name, createdAt=existing.created_at, updatedAt=self._now(),
            sequence=SequenceManifest(
                activeGenerationId=active_id,
                dialogueLanguage=self._dialogue_language(existing),
                subjects=[item for item in subjects if item.id != subject_id],
                nextSubjectNumber=next_subject_number,
                generations=cleaned,
                continuityContext=self._sequence_context(existing),
            ),
        )
        self._write_manifest(directory, manifest, [])
        return self._document(directory)

    @_project_transaction
    def clear_generated_state(self, project_id: str, expected_project_fingerprint: str,
                              *, clear_saved_prompts: bool = False) -> ProjectDocument:
        """Commit both metadata projections before reusing safe Project-owned GC."""
        from miniconstruct.generated_state import GeneratedStateReset

        _, directory, existing, previous, active_id = self._mutation_context(project_id)
        if not expected_project_fingerprint or expected_project_fingerprint != self._manifest_fingerprint(existing):
            raise StaleProjectSnapshotError("Project changed since loading; reload before clearing generated state")
        self._ensure_no_take_import(directory)
        generations, state = GeneratedStateReset(keep_saved_prompts=not clear_saved_prompts).project(
            previous, self._read_execution_state(directory))
        # Filesystem transactions use the shared lock and atomic individual writes.
        # Roll back either metadata file on a write failure; GC starts only after
        # both commits succeed. Never inspect renderer-owned output descriptors.
        manifest_path = directory / "project.json"
        sidecar_path = directory / "execution-state.json"
        before_manifest = manifest_path.read_bytes()
        before_sidecar = sidecar_path.read_bytes() if sidecar_path.exists() else None
        try:
            self._write_execution_state(directory, state)
            self._commit_sequence(directory, existing, generations, active_id, hydrate_result=False)
        except Exception:
            self._atomic_write(manifest_path, before_manifest)
            if before_sidecar is None:
                sidecar_path.unlink(missing_ok=True)
            else:
                self._atomic_write(sidecar_path, before_sidecar)
            raise
        self._advance_generated_state_epoch(directory)
        self._prune_take_media(directory, previous, generations)
        return self._document(directory)

    @_project_transaction
    def duplicate_project(
        self,
        project_id: str,
        name: str,
        *,
        active_workspace: Workspace | None = None,
        character_profiles=None,
        story_idea: str | None = None,
        story_creative_direction: StoryCreativeDirection | None = None,
        story_planning_style: StoryPlanningStyle | None = None,
        story_memory: StoryContinuityMemory | None = None,
        story_memory_generation_id: str | None = None,
        expected_project_fingerprint: str | None = None,
        sequence_context: str | None = None,
        active_incoming_state: str | None = None,
        active_intended_outgoing_state: str | None = None,
        active_planning: GenerationPlanning | dict[str, Any] | None = None,
        dialogue_language: DialogueLanguage | None = None,
        copy_mode: Literal["full", "fresh"] = "full",
        keep_saved_prompts: bool = False,
    ) -> ProjectDocument:
        """Create a new identity domain containing a deep Sequence clone."""
        _source_id, source_directory, existing, source_generations, source_active = self._mutation_context(project_id)
        if expected_project_fingerprint is not None and expected_project_fingerprint != self._manifest_fingerprint(existing):
            raise StaleProjectSnapshotError("Project changed since loading; reload before Save As")
        if story_memory is not None and story_memory_generation_id != source_active:
            raise StaleProjectSnapshotError("Story Memory Save As must target the selected Generation ID")
        base_fingerprint = self._manifest_fingerprint(existing)
        if copy_mode not in {"full", "fresh"}:
            raise ProjectStoreError("Unknown Project copy mode")
        execution_state = self._read_execution_state(source_directory)
        if copy_mode == "fresh":
            from miniconstruct.generated_state import GeneratedStateReset
            # Project metadata is projected BEFORE staging or transferring files.
            # The existing transfer layer sees only required canonical references.
            source_generations, execution_state = GeneratedStateReset(keep_saved_prompts).project(
                source_generations, execution_state)
        project_name = name.strip() if isinstance(name, str) else ""
        if not project_name:
            raise ProjectStoreError("Project name cannot be empty")
        new_project_id = str(uuid.uuid4())
        staging = self.root / f".project-{new_project_id}.{uuid.uuid4().hex}.creating"
        destination = self._project_directory(new_project_id)
        id_map: dict[str, str] = {}
        new_generations: list[GenerationManifest] = []
        forked_at = _utc_now()
        pending_blobs: list[tuple[Path, bytes | Path]] = []
        transferred_references = set()
        def copy_reference(entry):
            target = self._safe_project_relative_path(staging, entry.path)
            identity = (target, entry.content_sha256, entry.byte_length)
            if identity in transferred_references:
                return
            try:
                self._copy_media(self._safe_project_relative_path(source_directory, entry.path),
                    target, digest=entry.content_sha256, size=entry.byte_length)
            except AssetPayloadError:
                return  # Unavailable source remains a truthful missing destination.
            transferred_references.add(identity)
        try:
            staging.mkdir()
            for relative in ("assets/pictures", "assets/videos", "assets/audio", "outputs", "workflows", "provenance"):
                (staging / relative).mkdir(parents=True, exist_ok=True)
            for source in source_generations:
                new_id = str(uuid.uuid4())
                id_map[source.generation_id] = new_id
                if source.generation_id == source_active and active_workspace is not None:
                    workspace_data, copied_assets, override_blobs, _normalized = self._externalize_workspace(
                        staging, active_workspace, new_project_id, project_name, source.asset_files, source.workspace.get("assets", []),
                        canonical_project_id=existing.project_id,
                    )
                    pending_blobs.extend(override_blobs)
                    for entry in copied_assets:
                        target = self._safe_project_relative_path(staging, entry.path)
                        if any(path == target for path, _ in override_blobs):
                            continue  # New input is verified by its pending publication stream.
                        if entry.kind != "image":
                            copy_reference(entry)
                            continue
                        status, payload = self._reference_payload(source_directory, entry)
                        if status:
                            continue
                        pending_blobs.append((target, payload))
                else:
                    workspace_data = json.loads(json.dumps(source.workspace))
                    copied_assets = [entry.model_copy(deep=True) for entry in source.asset_files]
                    for asset_file in copied_assets:
                        source_path = self._safe_project_relative_path(source_directory, asset_file.path)
                        destination_path = self._safe_project_relative_path(staging, asset_file.path)
                        if asset_file.kind != "image":
                            copy_reference(asset_file)
                            continue
                        status, _ = self._reference_payload(source_directory, asset_file)
                        if status:
                            continue
                        destination_path.parent.mkdir(parents=True, exist_ok=True)
                        payload = source_path.read_bytes()
                        if destination_path.exists():
                            if destination_path.read_bytes() != payload:
                                raise ProjectStoreError("content-addressed image path contains different content")
                        else:
                            self._atomic_write(destination_path, payload)
                new_generations.append(GenerationManifest(
                    generationId=new_id,
                    createdAt=forked_at,
                    updatedAt=forked_at,
                    workspace=workspace_data,
                    assetFiles=copied_assets,
                    storyMemoryAfter=source.story_memory_after.model_copy(deep=True),
                    storyMemoryStatus=source.story_memory_status,
                    takeObservations=source.take_observations, takes=source.takes, renderSubmissions=source.render_submissions, selectedTakeId=source.selected_take_id,
                    continuity=(
                        GenerationContinuityManifest(
                            incomingState=(
                                source.continuity.incoming_state
                                if active_incoming_state is None else active_incoming_state
                            ),
                            intendedOutgoingState=(
                                source.continuity.intended_outgoing_state
                                if active_intended_outgoing_state is None
                                else active_intended_outgoing_state
                            ),
                        )
                        if source.generation_id == source_active
                        else source.continuity.model_copy(deep=True)
                    ),
                    planning=(
                        _merged_planning(source.planning, active_planning)
                        if source.generation_id == source_active
                        else source.planning.model_copy(deep=True)
                    ),
                ))
            if copy_mode == "full":
                self._fork_take_media(source_directory, staging, source_generations)
            new_active = id_map[source_active]
            if story_memory is not None:
                self._save_generation_memory(new_generations, new_active, story_memory)
            now = _utc_now()
            cast = self._with_character_profiles(
                self._cast_for_write(existing, new_generations), character_profiles,
            )
            manifest = ProjectManifest(
                schemaVersion=PROJECT_SCHEMA_VERSION,
                storyIdea=self._story_idea(existing) if story_idea is None else story_idea,
                storyCreativeDirection=(self._story_creative_direction(existing)
                                        if story_creative_direction is None else story_creative_direction),
                storyPlanningStyle=(self._story_planning_style(existing)
                                    if story_planning_style is None else story_planning_style),
                projectId=new_project_id,
                name=project_name,
                createdAt=now,
                updatedAt=now,
                sequence=SequenceManifest(
                    activeGenerationId=new_active,
                    dialogueLanguage=dialogue_language or self._dialogue_language(existing),
                    **cast,
                    generations=new_generations,
                    continuityContext=(
                        self._sequence_context(existing)
                        if sequence_context is None else sequence_context
                    ),
                ),
            )
            if self._manifest_fingerprint(self._read_manifest(source_directory)) != base_fingerprint:
                raise StaleProjectSnapshotError("Project changed during Save As; reload before saving")
            self._write_manifest(staging, manifest, pending_blobs)
            execution_state.selections = {id_map[key]: value for key, value in execution_state.selections.items() if key in id_map}
            execution_state.saved_prompts = {id_map[key]: value for key, value in execution_state.saved_prompts.items() if key in id_map}
            execution_state.routing = {id_map[key]: value for key, value in execution_state.routing.items() if key in id_map}
            execution_state.renderer_seed_settings = {id_map[key]: value for key, value in execution_state.renderer_seed_settings.items() if key in id_map}
            execution_state.workflow_control_overrides = {id_map[key]: value for key, value in execution_state.workflow_control_overrides.items() if key in id_map}
            # A cloned Project cannot claim access to artifacts owned by the original chain.
            execution_state.continuation_executions = []
            execution_state.accepted_continuations = {}
            execution_state.continuation_settings = {id_map[key]: value for key, value in execution_state.continuation_settings.items() if key in id_map}
            self._write_execution_state(staging, execution_state)
            if destination.exists():
                raise ProjectStoreError(f"project directory for {new_project_id} already exists")
            os.replace(staging, destination)
            return self._document(destination)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def list_projects(self) -> list[ProjectSummary]:
        self._ensure_root()
        summaries: list[ProjectSummary] = []
        for directory in sorted(self.root.iterdir(), key=lambda item: item.name):
            if not directory.is_dir() or directory.is_symlink() or not directory.name.startswith(_PROJECT_DIRECTORY_PREFIX):
                continue
            try:
                manifest = self._read_manifest(directory, validate_workspaces=False)
                summaries.append(ProjectSummary(manifest.project_id, manifest.name, manifest.created_at, manifest.updated_at))
            except (ProjectStoreError, OSError) as exc:
                # A user may have a half-written, corrupt, or unsupported Project
                # beside healthy ones. Listing must remain a best-effort read and
                # never alters the skipped directory.
                _LOG.warning("Skipping Project directory %s while listing: %s", directory, exc)
        return sorted(summaries, key=lambda item: item.updated_at, reverse=True)
