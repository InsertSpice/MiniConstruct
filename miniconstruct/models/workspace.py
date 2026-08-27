from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class H3Mode(StrEnum):
    T2VA = "T2VA"
    I2VA = "I2VA"
    FL2VA = "FL2VA"
    L2VA = "L2VA"
    REF2VA = "Ref2VA"


class AssetKind(StrEnum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"


IMAGE_ROLES = {
    "subject_identity", "environment", "style_appearance", "continuity_state",
    "first_frame_anchor", "keyframe_anchor", "last_frame_anchor",
    "storyboard_composition", "general_visual",
}
VIDEO_ROLES = {
    "continuation_source", "seamless_overlap_continuation", "editing_source",
    "motion_action", "camera_movement", "cut_pacing_rhythm", "general_video",
}
AUDIO_ROLES = {
    "full_reuse", "partial_reuse", "music_beat_rhythm", "voice_timbre",
    "dialogue_spoken_content", "sound_audio_style", "general_audio",
}
ROLE_SETS = {
    AssetKind.IMAGE: IMAGE_ROLES,
    AssetKind.VIDEO: VIDEO_ROLES,
    AssetKind.AUDIO: AUDIO_ROLES,
}


class ImagePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data_url: str = Field(min_length=16, description="Processed image data URL")
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)

    @field_validator("data_url")
    @classmethod
    def validate_image_url(cls, value: str) -> str:
        if not value.startswith("data:image/"):
            raise ValueError("image data must be an image data URL")
        return value


class ReferenceAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128)
    kind: AssetKind
    filename: str = Field(min_length=1, max_length=512)
    mime_type: str = Field(alias="mimeType", min_length=1, max_length=128)
    duration_seconds: float | None = Field(default=None, alias="durationSeconds", ge=0)
    role: str
    notes: str = Field(default="", max_length=8000)
    options: dict[str, Any] = Field(default_factory=dict)
    order: int = Field(default=0, ge=0)
    image: ImagePayload | None = None
    attached: bool = True

    @model_validator(mode="after")
    def validate_role_and_payload(self) -> "ReferenceAsset":
        if self.role not in ROLE_SETS[self.kind]:
            raise ValueError(f"role {self.role!r} is not valid for {self.kind.value}")
        if self.kind == AssetKind.IMAGE:
            if self.image is None:
                raise ValueError("image assets require a processed image payload")
            if not self.mime_type.startswith("image/"):
                raise ValueError("image asset MIME type must begin with image/")
        elif self.image is not None:
            raise ValueError("video/audio assets must not contain image data")
        return self


class Workspace(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    project_id: str | None = Field(default=None, alias="projectId", max_length=128)
    project_name: str = Field(default="Untitled Project", alias="projectName", max_length=160)
    mode: H3Mode = H3Mode.T2VA
    duration_seconds: float = Field(default=6.0, alias="durationSeconds", ge=4, le=15)
    shots: int | None = Field(default=None, gt=0, strict=True)
    aspect_ratio: str = Field(default="auto", alias="aspectRatio", min_length=1, max_length=40)
    variations: int = Field(default=1, ge=1, le=8, strict=True)
    creative_request: str = Field(default="", alias="creativeRequest", max_length=50000)
    dialogue: str = Field(default="", max_length=30000)
    reference_labels: str = Field(default="", alias="referenceLabels", max_length=30000)
    assets: list[ReferenceAsset] = Field(default_factory=list)

    @field_validator("creative_request")
    @classmethod
    def require_request(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("main prompt / idea is required")
        return value

    @model_validator(mode="after")
    def validate_mode_assets(self) -> "Workspace":
        images = [asset for asset in self.assets if asset.kind == AssetKind.IMAGE]
        roles = [asset.role for asset in images]
        if self.mode == H3Mode.I2VA and roles.count("first_frame_anchor") != 1:
            raise ValueError("I2VA requires exactly one first-frame anchor image")
        if self.mode == H3Mode.FL2VA:
            if roles.count("first_frame_anchor") != 1 or roles.count("last_frame_anchor") != 1:
                raise ValueError("FL2VA requires exactly one first-frame and one last-frame anchor image")
        if self.mode == H3Mode.L2VA and roles.count("last_frame_anchor") != 1:
            raise ValueError("L2VA requires exactly one last-frame anchor image")
        if self.mode == H3Mode.REF2VA and not self.assets:
            raise ValueError("Ref2VA requires at least one reference asset")
        return self

    def ordered_assets(self) -> list[ReferenceAsset]:
        kind_order = {AssetKind.IMAGE: 0, AssetKind.VIDEO: 1, AssetKind.AUDIO: 2}
        return sorted(self.assets, key=lambda item: (kind_order[item.kind], item.order, item.id))
