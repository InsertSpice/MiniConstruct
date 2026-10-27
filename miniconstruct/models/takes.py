"""Canonical output history. No observed-state or authoring authority."""
import hashlib
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from pydantic import ConfigDict, Field, model_validator

from miniconstruct.execution import StrictModel
from miniconstruct.execution_routing import EffectiveReference
from miniconstruct.models.workspace import H3Mode


def timestamp():
    return datetime.now(timezone.utc).isoformat()


class TakePromptSnapshot(StrictModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)
    prompt: str = Field(min_length=1, max_length=100000)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_fingerprint: str = Field(alias="sourceFingerprint", pattern=r"^[0-9a-f]{64}$")
    mode: H3Mode | None = None
    selection_id: UUID = Field(alias="selectionId")
    selected_at: str = Field(alias="selectedAt", max_length=100)
    captured_at: str = Field(default_factory=timestamp, alias="capturedAt", max_length=100)
    reference_map: list[EffectiveReference] | None = Field(default=None, alias="referenceMap", max_length=1000)

    @model_validator(mode="after")
    def exact_hash(self):
        if hashlib.sha256(self.prompt.encode("utf-8")).hexdigest() != self.sha256:
            raise ValueError("Prompt SHA-256 does not match exact prompt text")
        return self

    @classmethod
    def capture(cls, selected, mode=None):
        return cls(prompt=selected.prompt, sha256=hashlib.sha256(selected.prompt.encode("utf-8")).hexdigest(),
                   sourceFingerprint=selected.source_fingerprint, mode=selected.mode or mode,
                   selectionId=selected.id, selectedAt=selected.selected_at, referenceMap=selected.reference_map)


class RendererOutput(StrictModel):
    """Only validated relative /view descriptors; no arbitrary node metadata."""
    id: str = Field(pattern=r"^[0-9a-f]{64}$")
    filename: str = Field(min_length=1, max_length=255)
    subfolder: str = Field(max_length=1024)
    # Comfy ``input`` files are uploaded/reference media, never rendered Takes.
    type: Literal["output", "temp"]
    node_id: str | None = Field(default=None, alias="nodeId", max_length=80)
    imported_take_id: UUID | None = Field(default=None, alias="importedTakeId")


class RendererJobState(StrictModel):
    status: Literal["queued", "running", "completed", "failed", "cancelled", "unknown"] = "queued"
    last_checked_at: str | None = Field(default=None, alias="lastCheckedAt", max_length=100)
    started_at: int | None = Field(default=None, alias="startedAt", ge=0, le=9999999999999)
    completed_at: int | None = Field(default=None, alias="completedAt", ge=0, le=9999999999999)
    message: str | None = Field(default=None, max_length=300)
    failed_node_id: str | None = Field(default=None, alias="failedNodeId", max_length=80)
    outputs: list[RendererOutput] = Field(default_factory=list, max_length=128)


class RenderSubmission(StrictModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)
    id: UUID = Field(default_factory=uuid4)
    created_at: str = Field(default_factory=timestamp, alias="createdAt", max_length=100)
    renderer: Literal["comfyui"] = "comfyui"
    connection_id: UUID = Field(alias="connectionId")
    workflow_profile_id: UUID = Field(alias="workflowProfileId")
    workflow_fingerprint: str = Field(alias="workflowFingerprint", pattern=r"^[0-9a-f]{64}$")
    profile_fingerprint: str = Field(alias="profileFingerprint", pattern=r"^[0-9a-f]{64}$")
    comfy_prompt_id: UUID = Field(alias="comfyPromptId")
    renderer_seed: int | None = Field(default=None, alias="rendererSeed", ge=0, le=2147483647)
    execution_fingerprint: str | None = Field(default=None, alias="executionFingerprint", pattern=r"^[0-9a-f]{64}$")
    workflow_control_values: dict[str, bool | int | float | str] = Field(default_factory=dict, alias="workflowControlValues", max_length=128)
    workflow_control_fingerprint: str | None = Field(default=None, alias="workflowControlFingerprint", pattern=r"^[0-9a-f]{64}$")
    retry_of_submission_id: UUID | None = Field(default=None, alias="retryOfSubmissionId")
    prompt_snapshot: TakePromptSnapshot = Field(alias="promptSnapshot")
    job: RendererJobState = Field(default_factory=RendererJobState)

    @model_validator(mode="before")
    @classmethod
    def legacy_job_state(cls, value):
        # Persisted JSON has an id; a new constructor call normally lets Pydantic
        # allocate it. This keeps old records honestly Unknown without changing
        # the queued default used by new Send construction.
        if isinstance(value, dict) and "job" not in value and "id" in value:
            return {**value, "job": {"status": "unknown"}}
        return value


class Take(StrictModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)
    id: UUID = Field(default_factory=uuid4)
    created_at: str = Field(default_factory=timestamp, alias="createdAt", max_length=100)
    original_filename: str = Field(alias="originalFilename", min_length=1, max_length=255)
    mime_type: str = Field(alias="mimeType", max_length=128)
    byte_size: int = Field(alias="byteSize", gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    managed_media_path: str = Field(alias="managedMediaPath", pattern=r"^outputs/media/[0-9a-f]{64}\.(mp4|webm|mov|mkv|avi|m4v|mpeg|mpg)$")
    source_kind: Literal["comfyui_submission", "selected_prompt", "external_unknown"] = Field(alias="sourceKind")
    prompt_snapshot: TakePromptSnapshot | None = Field(default=None, alias="promptSnapshot")
    render_submission_id: UUID | None = Field(default=None, alias="renderSubmissionId")

    @model_validator(mode="after")
    def valid_source(self):
        if self.managed_media_path.split("/")[-1].split(".")[0] != self.sha256:
            raise ValueError("Managed media identity must match SHA-256")
        if (self.source_kind == "external_unknown") != (self.prompt_snapshot is None):
            raise ValueError("Prompt provenance must agree with sourceKind")
        if (self.source_kind == "comfyui_submission") != (self.render_submission_id is not None):
            raise ValueError("Submission provenance must agree with sourceKind")
        return self


class TakeImport(StrictModel):
    source_kind: Literal["comfyui_submission", "selected_prompt", "external_unknown"] = Field(alias="sourceKind")
    render_submission_id: UUID | None = Field(default=None, alias="renderSubmissionId")
    selected_prompt_id: UUID | None = Field(default=None, alias="selectedPromptId")

    @model_validator(mode="after")
    def explicit_source(self):
        if (self.source_kind == "comfyui_submission") != (self.render_submission_id is not None):
            raise ValueError("Choose a recorded submission for this source")
        if (self.source_kind == "selected_prompt") != (self.selected_prompt_id is not None):
            raise ValueError("Choose the selected generated prompt for this source")
        return self
