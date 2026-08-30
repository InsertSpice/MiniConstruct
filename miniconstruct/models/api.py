from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from .workspace import H3Mode, ReferenceAsset, Workspace


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


class GenerationRequest(BaseModel):
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


class RevisionRequest(BaseModel):
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
    prompt: str
    mode: H3Mode
    duration_seconds: float = Field(alias="durationSeconds", ge=4, le=15)
    shots: int | None = Field(default=None, gt=0, strict=True)
    assets: list[ReferenceAsset] = Field(default_factory=list)
    dialogue: str = ""


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
    prompt: str
    validation: ValidationResult


class GenerationResponse(BaseModel):
    variations: list[GeneratedVariation]
    warnings: list[str] = Field(default_factory=list)


class ProjectEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    format: Literal["MiniConstruct Project"]
    schema_version: Literal[1] = Field(alias="schemaVersion")
    exported_at: str | None = Field(default=None, alias="exportedAt")
    workspace: Workspace
