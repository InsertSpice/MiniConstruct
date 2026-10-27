"""Saved authoring policy and typed transient predecessor evidence."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class VisualHandoffPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    strategy: Literal["auto", "none", "endpoint_frame", "endpoint_then_immediate_cut", "tail_reference_continuation", "native_h3_continuation", "native_i2va_continuation"] = "auto"
    tail_seconds: float = Field(default=1.5, alias="tailSeconds", ge=.5, le=2, allow_inf_nan=False)
    immediate_cut_milliseconds: int = Field(default=100, alias="immediateCutMilliseconds", ge=0, le=500)

    @field_validator("strategy", mode="before")
    @classmethod
    def migrate_legacy_tail_name(cls, value):
        # v15 persisted this spelling before rendered testing corrected its promise.
        return "tail_reference_continuation" if value == "short_tail_prefix" else value


class ResolvedVisualHandoff(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    relationship: str | None = None
    strategy: Literal["endpoint_frame", "endpoint_then_immediate_cut", "tail_reference_continuation"]
    source_generation_id: str = Field(alias="sourceGenerationId")
    take_id: str = Field(alias="takeId")
    take_sha256: str = Field(alias="takeSha256")
    artifact_identity: str = Field(alias="artifactIdentity")
    media_kind: Literal["image", "video"] = Field(alias="mediaKind")
    tail_seconds: float | None = Field(default=None, alias="tailSeconds")
    immediate_cut_milliseconds: int | None = Field(default=None, alias="immediateCutMilliseconds")

    @model_validator(mode="before")
    @classmethod
    def discard_legacy_overlap_metadata(cls, value):
        if isinstance(value, dict):
            value = dict(value)
            value.pop("overlapSeconds", None)
            if value.get("strategy") == "short_tail_prefix":
                value["strategy"] = "tail_reference_continuation"
        return value
