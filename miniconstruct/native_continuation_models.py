"""Package-independent native H3 execution contracts; no tensors or filesystem paths."""
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


Semantic = Literal["enable", "chain_id", "generation_id", "predecessor_generation_id",
                   "storage_namespace", "segment_index", "save_segment_index", "load_segment_index", "context_frames", "extension_frames", "extension_seconds",
                   "save_enabled", "save_key", "load_key", "audio_enabled", "source_out_frame"]


class FrameGrid(Contract):
    minimum: int = Field(default=1, ge=1, strict=True)
    step: int = Field(default=1, ge=1, strict=True)
    maximum: int = Field(default=100000, ge=1, strict=True)
    alignment: Literal["reject", "ceil", "floor"] = "reject"

    @model_validator(mode="after")
    def valid_bounds(self):
        if self.maximum < self.minimum:
            raise ValueError("Frame maximum must be at least minimum")
        return self

    def resolve(self, requested: int) -> int:
        if requested < self.minimum or requested > self.maximum:
            raise ValueError(f"Requested frames must be between {self.minimum} and {self.maximum}")
        remainder = (requested - self.minimum) % self.step
        if remainder and self.alignment == "reject":
            raise ValueError(f"Requested frames must follow grid {self.minimum} + n × {self.step}")
        result = requested - remainder + (self.step if remainder and self.alignment == "ceil" else 0)
        if result > self.maximum:
            raise ValueError("Snapped frames exceed profile maximum")
        return result


class NativeInput(Contract):
    node_id: str = Field(alias="nodeId", min_length=1)
    input_name: str = Field(alias="inputName", min_length=1)
    # Some workflows expose booleans as string choices or numeric switches.
    enabled_value: bool | str | int = Field(default=True, alias="enabledValue")
    disabled_value: bool | str | int = Field(default=False, alias="disabledValue")
    unset_value: str | int | None = Field(default=-1, alias="unsetValue")


class NativeSourceLink(Contract):
    """An exact native predecessor edge that is absent for save-only execution."""

    node_id: str = Field(alias="nodeId", min_length=1)
    input_name: str = Field(alias="inputName", min_length=1)
    expected_source_node_id: str = Field(alias="expectedSourceNodeId", min_length=1)
    expected_output_index: int = Field(alias="expectedOutputIndex", ge=0, strict=True)


class PictureFreeI2va(Contract):
    """Author-verified renderer contract, limited to the stock H3/extender graph."""
    version: Literal[1] = 1
    renderer_contract: Literal["minimax_h3_extender_av_v1"] = Field(alias="rendererContract")
    conditioning_node_id: str = Field(alias="conditioningNodeId", min_length=1)


class NativeCapability(Contract):
    kind: Literal["native_h3_av"] = "native_h3_av"
    version: Literal[1] = 1
    adapter: Literal["opaque_keys"] = "opaque_keys"
    compatibility_key: str = Field(alias="compatibilityKey", pattern=r"^[A-Za-z0-9_.-]{1,120}$")
    inputs: dict[Semantic, NativeInput]
    source_links: list[NativeSourceLink] = Field(default_factory=list, alias="sourceLinks", max_length=1000)
    required_inputs: list[Semantic] = Field(default_factory=list, alias="requiredInputs")
    video_continuity: Literal[True] = Field(default=True, alias="videoContinuity")
    audio_continuity: bool = Field(default=False, alias="audioContinuity")
    audio_enabled: bool = Field(default=False, alias="audioEnabled")
    context_frames: int = Field(alias="contextFrames", ge=1, strict=True)
    context_grid: FrameGrid = Field(alias="contextGrid")
    extension_grid: FrameGrid | None = Field(default=None, alias="extensionGrid")
    source_out_grid: FrameGrid | None = Field(default=None, alias="sourceOutGrid")
    frames_per_second: float | None = Field(default=None, alias="framesPerSecond", gt=0, le=1000, allow_inf_nan=False)
    requires_tail_reference: bool = Field(default=False, alias="requiresTailReference")
    allow_endpoint_anchor: bool = Field(default=False, alias="allowEndpointAnchor")
    picture_free_i2va: PictureFreeI2va | None = Field(default=None, alias="pictureFreeI2va")

    @model_validator(mode="after")
    def validate_contract(self):
        required = {"enable", "save_key", "load_key", *self.required_inputs}
        missing = required - self.inputs.keys()
        if missing:
            raise ValueError("Native opaque_keys adapter requires mapped inputs: " + ", ".join(sorted(missing)))
        for semantic in ("enable", "save_enabled", "audio_enabled"):
            mapping = self.inputs.get(semantic)
            if mapping and mapping.enabled_value == mapping.disabled_value:
                raise ValueError(f"Native {semantic} requires distinct enabledValue and disabledValue")
        self.context_grid.resolve(self.context_frames)
        if self.audio_enabled and not self.audio_continuity:
            raise ValueError("Audio continuity cannot be enabled when unsupported")
        if self.audio_continuity and "audio_enabled" not in self.inputs:
            raise ValueError("Audio continuity requires an audio_enabled mapping")
        if "extension_frames" in self.inputs and (self.extension_grid is None or self.frames_per_second is None):
            raise ValueError("extension_frames requires extensionGrid and framesPerSecond")
        if len(self.required_inputs) != len(set(self.required_inputs)):
            raise ValueError("requiredInputs must be distinct")
        targets = [(link.node_id, link.input_name) for link in self.source_links]
        if len(targets) != len(set(targets)):
            raise ValueError("sourceLinks must target distinct graph inputs")
        if self.picture_free_i2va:
            if self.allow_endpoint_anchor or self.requires_tail_reference:
                raise ValueError("Picture-free I2VA cannot require tail media or allow an endpoint anchor")
            if not {"context_frames", "save_segment_index", "load_segment_index"} <= self.inputs.keys():
                raise ValueError("Picture-free I2VA requires context and directional Save/Load slots")
        return self


class ContinuationSettings(Contract):
    profile_id: UUID | None = Field(default=None, alias="profileId")
    context_frames: int | None = Field(default=None, alias="contextFrames", ge=1, strict=True)
    audio_enabled: bool | None = Field(default=None, alias="audioEnabled")
    source_out_frame: int | None = Field(default=None, alias="sourceOutFrame", ge=0, strict=True)


class NativePreparation(Contract):
    id: UUID = Field(default_factory=uuid4)
    project_id: UUID = Field(alias="projectId")
    generation_id: UUID = Field(alias="generationId")
    workflow_profile_id: UUID = Field(alias="workflowProfileId")
    profile_fingerprint: str = Field(alias="profileFingerprint")
    workflow_fingerprint: str = Field(alias="workflowFingerprint")
    compatibility_fingerprint: str = Field(alias="compatibilityFingerprint")
    capability_kind: Literal["native_h3_av"] = Field(default="native_h3_av", alias="capabilityKind")
    capability_version: Literal[1] = Field(default=1, alias="capabilityVersion")
    mode: str
    i2va_state_identity: str | None = Field(default=None, alias="i2vaStateIdentity", pattern=r"^[0-9a-f]{64}$")
    chain_id: str = Field(alias="chainId", pattern=r"^mc-[0-9a-f-]{36}$")
    storage_namespace: str = Field(default="", alias="storageNamespace",
                                   pattern=r"^(|mc/[0-9a-f-]{36}/[0-9a-f-]{36}/[0-9a-f-]{36})$")
    save_key: str = Field(alias="saveKey", pattern=r"^mc(?:-[0-9a-f-]{36}-[0-9a-f-]{36}-[0-9a-f-]{36}|/[0-9a-f-]{36}/[0-9a-f-]{36}/[0-9a-f-]{36}/clip)$")
    source_id: UUID | None = Field(default=None, alias="sourceId")
    source_generation_id: UUID | None = Field(default=None, alias="sourceGenerationId")
    load_key: str = Field(default="", alias="loadKey", pattern=r"^(|mc(?:-[0-9a-f-]{36}-[0-9a-f-]{36}-[0-9a-f-]{36}|/[0-9a-f-]{36}/[0-9a-f-]{36}/[0-9a-f-]{36}))$")
    # `segment_index` is retained for older profiles that use one slot for both
    # directions. Separate slots let a profile address a predecessor exactly.
    segment_index: int = Field(alias="segmentIndex", ge=1)
    save_segment_index: int = Field(default=1, alias="saveSegmentIndex", ge=1)
    load_segment_index: int | None = Field(default=None, alias="loadSegmentIndex", ge=1)
    consume: bool = False
    save: Literal[True] = True
    requested_context_frames: int = Field(alias="requestedContextFrames")
    context_frames: int = Field(alias="contextFrames")
    extension_frames: int | None = Field(default=None, alias="extensionFrames")
    audio_supported: bool = Field(alias="audioSupported")
    audio_enabled: bool = Field(alias="audioEnabled")
    source_out_frame: int | None = Field(default=None, alias="sourceOutFrame")

    @model_validator(mode="before")
    @classmethod
    def legacy_slot_defaults(cls, value):
        # Sidecar v2 records predate directional slots. Their one stored slot is
        # the only truthful fallback; new preparations always write both fields.
        if isinstance(value, dict) and "saveSegmentIndex" not in value and "save_segment_index" not in value:
            value = {**value, "saveSegmentIndex": value.get("segmentIndex", value.get("segment_index", 1))}
        return value


class ContinuationExecution(NativePreparation):
    render_submission_id: UUID = Field(alias="renderSubmissionId")
    connection_id: UUID = Field(alias="connectionId")
    source_fingerprint: str = Field(alias="sourceFingerprint")
    execution_fingerprint: str = Field(alias="executionFingerprint")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat(), alias="createdAt")
    status: Literal["pending", "available", "unavailable"] = "pending"
    # User attestation is independent of renderer availability and default choice.
    confirmed: bool = Field(default=False, strict=True)


class ContinuationDecision(Contract):
    status: Literal["available", "unavailable"]
    accept: bool = True
