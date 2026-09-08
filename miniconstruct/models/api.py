from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from .planning import GenerationPlanning, ReferenceRecommendation, RelationshipToPrevious
from miniconstruct.execution_routing import GenerationRouting, EffectiveReference
from .workspace import AssetKind, H3Mode, ReferenceAsset, SubjectIdentityReference, SubjectRecord, Workspace, validate_authoring_workspace


class EndpointSource(StrEnum):
    MANUAL = "manual"
    LM_STUDIO = "lm_studio"
    OLLAMA = "ollama"
    UNSLOTH_STUDIO = "unsloth_studio"


class ReasoningMode(StrEnum):
    OFF = "off"
    DEFAULT = "default"
    ON = "on"


class SeedMode(StrEnum):
    BACKEND_DEFAULT = "backend_default"
    RANDOM = "random"
    FIXED = "fixed"


SEED_MAX = 2_147_483_647


class EndpointProfile(BaseModel):
    """One OpenAI-compatible endpoint and its user-owned connection details."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(min_length=1, max_length=160)
    display_name: str = Field(alias="displayName", min_length=1, max_length=160)
    base_url: str = Field(alias="baseUrl", min_length=1, max_length=2000)
    source: EndpointSource = EndpointSource.MANUAL
    api_key: SecretStr | None = Field(default=None, alias="apiKey")

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError("base URL must begin with http:// or https://")
        return value

    def public_copy(self) -> "EndpointProfile":
        return self.model_copy(update={"api_key": None})


class DiscoveredModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    endpoint_id: str = Field(alias="endpointId", min_length=1)
    model_id: str = Field(alias="modelId", min_length=1, max_length=500)
    display_name: str = Field(alias="displayName", min_length=1, max_length=700)


class LLMSettings(BaseModel):
    """The selected endpoint/provider paired with its exact model ID."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    endpoint: EndpointProfile
    model_id: str = Field(default="", alias="modelId", max_length=500)
    model: str = Field(default="", max_length=500)
    temperature: float = Field(default=0.4, ge=0, le=2)
    max_tokens: int = Field(default=4096, alias="maxTokens", ge=128, le=65536)
    timeout_seconds: float = Field(default=120, alias="timeoutSeconds", ge=1, le=600)
    supports_vision: bool | None = Field(default=None, alias="supportsVision")
    reasoning_mode: ReasoningMode = Field(default=ReasoningMode.OFF, alias="reasoningMode")
    seed_mode: SeedMode = Field(default=SeedMode.BACKEND_DEFAULT, alias="seedMode")
    fixed_seed: int | None = Field(default=None, alias="fixedSeed", ge=0, le=SEED_MAX)
    seed: int | None = Field(default=None, ge=0, le=SEED_MAX)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_shape(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        if "endpoint" not in normalized and "baseUrl" in normalized:
            normalized["endpoint"] = {
                "id": "manual-endpoint",
                "displayName": "Manual endpoint",
                "baseUrl": normalized.pop("baseUrl"),
                "apiKey": normalized.pop("apiKey", None),
                "source": "manual",
            }
        if "modelId" not in normalized and isinstance(normalized.get("model"), str):
            normalized["modelId"] = normalized["model"]
        return normalized

    @model_validator(mode="after")
    def validate_seed_settings(self) -> "LLMSettings":
        if self.seed_mode == SeedMode.FIXED and self.fixed_seed is None:
            raise ValueError("fixedSeed is required when seedMode is fixed")
        return self

    @property
    def selected_model_id(self) -> str:
        return self.model_id or self.model


class EndpointDiscoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    manual_endpoint: EndpointProfile | None = Field(default=None, alias="manualEndpoint")


class EndpointDiscoveryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    endpoint: EndpointProfile
    connected: bool
    models: list[DiscoveredModel] = Field(default_factory=list)
    discovery_state: Literal[
        "catalog_available", "api_key_required", "authentication_failed", "catalog_unavailable", "unavailable"
    ] = Field(default="unavailable", alias="discoveryState")
    message: str = ""


class EndpointDiscoveryResponse(BaseModel):
    endpoints: list[EndpointDiscoveryResult]


class RoutingPromptRequest(BaseModel):
    # Request-only projection; neither Workspace nor authoring fingerprints include routing.
    execution_routing: "GenerationRouting" = Field(default_factory=lambda: GenerationRouting(), alias="executionRouting")

    @model_validator(mode="after")
    def validate_execution_labels(self):
        self.reference_map
        return self

    @property
    def prompt_workspace(self):
        from miniconstruct.execution_routing import prompt_projection
        return prompt_projection(self.workspace, self.execution_routing)

    @property
    def reference_map(self):
        from miniconstruct.execution_routing import effective_references
        return effective_references(self.workspace, self.execution_routing)


class GenerationRequest(RoutingPromptRequest):
    model_config = ConfigDict(extra="forbid")
    workspace: Workspace
    llm: LLMSettings
    resolved_seeds: list[int | None] = Field(default_factory=list, alias="resolvedSeeds")

    @model_validator(mode="after")
    def validate_resolved_seeds(self) -> "GenerationRequest":
        if self.resolved_seeds and len(self.resolved_seeds) != self.workspace.variations:
            raise ValueError("resolvedSeeds must contain one seed per variation")
        return self


class RevisionSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    full_prompt: str = Field(alias="fullPrompt", min_length=1)
    before_selection: str = Field(alias="beforeSelection")
    selected_text: str = Field(alias="selectedText", min_length=1)
    after_selection: str = Field(alias="afterSelection")

    @model_validator(mode="after")
    def verify_snapshot(self) -> "RevisionSelection":
        if not self.selected_text.strip():
            raise ValueError("selected text must contain non-whitespace content")
        if self.before_selection + self.selected_text + self.after_selection != self.full_prompt:
            raise ValueError("selection snapshot does not reconstruct the full prompt")
        return self


class RevisionRequest(RoutingPromptRequest):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    workspace: Workspace
    llm: LLMSettings
    output_index: int = Field(alias="outputIndex", ge=0)
    selection: RevisionSelection
    instruction: str = Field(min_length=1, max_length=8000)

    @field_validator("instruction")
    @classmethod
    def validate_instruction(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("revision instruction must contain non-whitespace content")
        return value


class ValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    execution_routing: GenerationRouting = Field(default_factory=GenerationRouting, alias="executionRouting")
    prompt: str
    mode: H3Mode
    duration_seconds: float = Field(alias="durationSeconds", ge=4, le=15)
    shots: int | None = Field(default=None, gt=0, strict=True)
    assets: list[ReferenceAsset] = Field(default_factory=list)
    dialogue: str = ""


class ReferenceManifestAsset(BaseModel):
    """Metadata-only asset shape used to refresh the derived H3 input map."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(min_length=1, max_length=128)
    kind: AssetKind
    filename: str = Field(min_length=1, max_length=512)
    role: str
    order: int = Field(default=0, ge=0)
    subject_identity: SubjectIdentityReference = Field(default_factory=SubjectIdentityReference, alias="subjectIdentity")
    comparison_subject_ids: list[str] = Field(default_factory=list, alias="comparisonSubjects")


class ReferenceManifestRequest(BaseModel):
    """Small transient request; the result is never persisted in a workspace."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    mode: H3Mode
    assets: list[ReferenceManifestAsset] = Field(default_factory=list)
    subjects: list[SubjectRecord] = Field(default_factory=list)
    execution_routing: "GenerationRouting" = Field(default_factory=lambda: GenerationRouting(), alias="executionRouting")


class RepairRequest(GenerationRequest):
    prompt: str
    findings: list[dict] = Field(default_factory=list)


class ValidationFinding(BaseModel):
    severity: Literal["ERROR", "WARNING", "INFO"]
    code: str
    message: str
    category: Literal["structural", "workspace_consistency"] = "structural"


class ValidationResult(BaseModel):
    valid: bool
    findings: list[ValidationFinding]


class GeneratedVariation(BaseModel):
    reference_map: list["EffectiveReference"] | None = Field(default=None, alias="referenceMap")
    source_fingerprint: str | None = Field(default=None, alias="sourceFingerprint")
    prompt: str
    validation: ValidationResult


class GenerationResponse(BaseModel):
    variations: list[GeneratedVariation]
    warnings: list[str] = Field(default_factory=list)


class ProjectEnvelope(BaseModel):
    story_idea: str = Field(default="", alias="storyIdea", max_length=20000)
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    format: Literal["MiniConstruct Project"]
    schema_version: Literal[1] = Field(alias="schemaVersion")
    exported_at: str | None = Field(default=None, alias="exportedAt")
    workspace: Workspace

    @field_validator("workspace", mode="plain")
    @classmethod
    def validate_authoring_state(cls, value: Any) -> Workspace:
        return validate_authoring_workspace(value)


class FilesystemProjectWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    story_idea: str | None = Field(default=None, alias="storyIdea", max_length=20000)
    workspace: Workspace
    continuity: "ContinuityWriteRequest | None" = None
    planning: "PlanningWriteRequest | None" = None

    @field_validator("workspace", mode="plain")
    @classmethod
    def validate_authoring_state(cls, value: Any) -> Workspace:
        return validate_authoring_workspace(value)


class ContinuityWriteRequest(BaseModel):
    """Optional authoring metadata for the active Generation and Sequence."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    sequence_context: str | None = Field(default=None, alias="sequenceContext", max_length=20000)
    incoming_state: str | None = Field(default=None, alias="incomingState", max_length=20000)
    intended_outgoing_state: str | None = Field(default=None, alias="intendedOutgoingState", max_length=20000)


class PlanningWriteRequest(BaseModel):
    """Optional patch for active-Generation planning metadata."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    relationship_to_previous: RelationshipToPrevious | None = Field(default=None, alias="relationshipToPrevious")
    camera_intent: str | None = Field(default=None, alias="cameraIntent", max_length=20000)


class PlanningRevisionScope(StrEnum):
    MAIN_PROMPT = "main_prompt"
    INCOMING_STATE = "incoming_state"
    INTENDED_OUTGOING_STATE = "intended_outgoing_state"
    RELATIONSHIP_TO_PREVIOUS = "relationship_to_previous"
    CAMERA_INTENT = "camera_intent"


class AssistedGenerationPlan(BaseModel):
    """Strict, atomic planner output shared by inference and plan revision."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    creative_request: str = Field(alias="creativeRequest", max_length=50000)
    incoming_state: str = Field(alias="incomingState", max_length=20000)
    intended_outgoing_state: str = Field(alias="intendedOutgoingState", max_length=20000)
    relationship_to_previous: RelationshipToPrevious | None = Field(alias="relationshipToPrevious")
    camera_intent: str = Field(alias="cameraIntent", max_length=20000)
    reference_recommendations: list[ReferenceRecommendation] = Field(
        default_factory=list, alias="referenceRecommendations"
    )


class PlanningInferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    workspace: Workspace
    llm: LLMSettings
    continuity: ContinuityWriteRequest = Field(default_factory=ContinuityWriteRequest)
    planning: GenerationPlanning = Field(default_factory=GenerationPlanning)

    @field_validator("workspace", mode="plain")
    @classmethod
    def validate_authoring_state(cls, value: Any) -> Workspace:
        return validate_authoring_workspace(value)


class PlanningRevisionRequest(PlanningInferenceRequest):
    instruction: str = Field(min_length=1, max_length=8000)
    scopes: list[PlanningRevisionScope] = Field(min_length=1)

    @field_validator("instruction")
    @classmethod
    def validate_instruction(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("plan revision instruction must contain non-whitespace content")
        return value


class GenerationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    insert_index: int | None = Field(default=None, alias="insertIndex", ge=0)
    workspace: Workspace | None = None

    @field_validator("workspace", mode="plain")
    @classmethod
    def validate_authoring_state(cls, value: Any) -> Workspace | None:
        return None if value is None else validate_authoring_workspace(value)


class GenerationOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    generation_ids: list[str] = Field(alias="generationIds")


class ProjectSaveAsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(min_length=1, max_length=160)
    story_idea: str | None = Field(default=None, alias="storyIdea", max_length=20000)
    workspace: Workspace | None = None
    continuity: ContinuityWriteRequest | None = None
    planning: PlanningWriteRequest | None = None

    @field_validator("workspace", mode="plain")
    @classmethod
    def validate_authoring_state(cls, value: Any) -> Workspace | None:
        return None if value is None else validate_authoring_workspace(value)


class FilesystemProjectSummary(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class GenerationSummary(BaseModel):
    reference_assets: list[dict[str, str]] = Field(default_factory=list, alias="referenceAssets")
    model_config = ConfigDict(populate_by_name=True)

    generation_id: str = Field(alias="generationId")
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class SequenceSummary(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    active_generation_id: str = Field(alias="activeGenerationId")
    generations: list[GenerationSummary]


class FilesystemProjectResponse(FilesystemProjectSummary):
    story_idea: str = Field(default="", alias="storyIdea", max_length=20000)
    workspace: Workspace
    sequence: SequenceSummary
    continuity: "ContinuityResponse"
    planning: GenerationPlanning

    @field_validator("workspace", mode="plain")
    @classmethod
    def validate_authoring_state(cls, value: Any) -> Workspace:
        return validate_authoring_workspace(value)


class ContinuityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    sequence_context: str = Field(alias="sequenceContext")
    incoming_state: str = Field(alias="incomingState")
    intended_outgoing_state: str = Field(alias="intendedOutgoingState")


FilesystemProjectWriteRequest.model_rebuild()
FilesystemProjectResponse.model_rebuild()


class FilesystemProjectListResponse(BaseModel):
    projects: list[FilesystemProjectSummary]
