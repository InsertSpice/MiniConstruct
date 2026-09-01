from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator


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


class MusicMode(StrEnum):
    AUTO = "auto"
    OFF = "off"
    ON = "on"


class CameraPreference(StrEnum):
    AUTO = "auto"
    AVOID = "avoid"
    PREFER = "prefer"


class ReferenceFidelityLevel(StrEnum):
    AUTO = "auto"
    BALANCED = "balanced"
    STRONG = "strong"
    STRICT = "strict"


class VisualStylePreset(StrEnum):
    AUTO = "auto"
    CINEMATIC = "cinematic"
    LIVE_ACTION = "live_action"
    ANIMATED_2D = "animated_2d"
    ANIMATED_2D_ANIME = "animated_2d_anime"
    CG_3D = "cg_3d"
    CG_3D_STYLIZED = "cg_3d_stylized"
    CLAYMATION = "claymation"
    WATERCOLOR = "watercolor"
    VINTAGE_FILM = "vintage_film"
    CUSTOM = "custom"


class ToneLevel(StrEnum):
    AUTO = "auto"
    SUBTLE = "subtle"
    MODERATE = "moderate"
    STRONG = "strong"
    INTENSE = "intense"


class PerformanceStyle(StrEnum):
    RESTRAINED = "restrained"
    SUBTLE = "subtle"
    AUTO = "auto"
    EXPRESSIVE = "expressive"
    EXAGGERATED = "exaggerated"


class PerformanceEnergy(StrEnum):
    CALM = "calm"
    LOW = "low"
    AUTO = "auto"
    ENERGETIC = "energetic"
    INTENSE = "intense"


class SubjectIdentityFocus(StrEnum):
    GENERAL = "general"
    FACE = "face"
    FULL_BODY = "full_body"
    OUTFIT = "outfit"
    DETAIL = "detail"


class SubjectIdentityView(StrEnum):
    UNSPECIFIED = "unspecified"
    FRONT = "front"
    THREE_QUARTER = "three_quarter"
    PROFILE = "profile"
    REAR = "rear"


class ReferenceLayout(StrEnum):
    """How an identity Picture is arranged, independently of its focus."""

    AUTO = "auto"
    SINGLE_VIEW = "single_view"
    REFERENCE_SHEET = "reference_sheet"


IMAGE_ROLES = {
    "subject_identity", "character_comparison_scale", "environment", "style_appearance", "continuity_state",
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


class SubjectIdentityReference(BaseModel):
    """Typed Picture metadata that is active only with the subject_identity role."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    subject_id: str = Field(default="subject-1", alias="subjectId", min_length=1, max_length=128)
    focus: SubjectIdentityFocus = SubjectIdentityFocus.GENERAL
    view: SubjectIdentityView = SubjectIdentityView.UNSPECIFIED
    layout: ReferenceLayout = ReferenceLayout.AUTO


class SubjectRecord(BaseModel):
    """A stable visible subject, deliberately independent of asset numbering."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128)
    number: int = Field(ge=1)


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
    subject_identity: SubjectIdentityReference = Field(default_factory=SubjectIdentityReference, alias="subjectIdentity")
    comparison_subject_ids: list[str] = Field(default_factory=list, alias="comparisonSubjects")
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


class MusicControls(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: MusicMode = MusicMode.AUTO
    description: str = Field(default="", max_length=8000)


class CameraControls(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    zoom: CameraPreference = CameraPreference.AUTO
    push_pull: CameraPreference = Field(default=CameraPreference.AUTO, alias="pushPull")
    pan: CameraPreference = CameraPreference.AUTO
    truck: CameraPreference = CameraPreference.AUTO
    tilt: CameraPreference = CameraPreference.AUTO
    pedestal: CameraPreference = CameraPreference.AUTO
    arc: CameraPreference = CameraPreference.AUTO
    tracking: CameraPreference = CameraPreference.AUTO
    static: CameraPreference = CameraPreference.AUTO
    shake: CameraPreference = CameraPreference.AUTO
    pov: CameraPreference = CameraPreference.AUTO
    roll: CameraPreference = CameraPreference.AUTO


class SubjectIdentityFidelityControls(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: ReferenceFidelityLevel = ReferenceFidelityLevel.AUTO


class VisualStyleControls(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset: VisualStylePreset = VisualStylePreset.AUTO
    custom: str = Field(default="", max_length=8000)


class TonePerformanceControls(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    sensuality: ToneLevel = ToneLevel.AUTO
    drama: ToneLevel = ToneLevel.AUTO
    horror: ToneLevel = ToneLevel.AUTO
    tension: ToneLevel = ToneLevel.AUTO
    romance: ToneLevel = ToneLevel.AUTO
    whimsy: ToneLevel = ToneLevel.AUTO
    performance_style: PerformanceStyle = Field(default=PerformanceStyle.AUTO, alias="performanceStyle")
    performance_energy: PerformanceEnergy = Field(default=PerformanceEnergy.AUTO, alias="performanceEnergy")


class CreativeControls(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    music: MusicControls = Field(default_factory=MusicControls)
    camera: CameraControls = Field(default_factory=CameraControls)
    visual_style: VisualStyleControls = Field(default_factory=VisualStyleControls, alias="visualStyle")
    subject_identity_fidelity: SubjectIdentityFidelityControls = Field(
        default_factory=SubjectIdentityFidelityControls,
        alias="subjectIdentityFidelity",
        validation_alias=AliasChoices("subjectIdentityFidelity", "referenceFidelity"),
    )
    tone_performance: TonePerformanceControls = Field(default_factory=TonePerformanceControls, alias="tonePerformance")


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
    subjects: list[SubjectRecord] = Field(default_factory=list)
    next_subject_number: int = Field(default=1, alias="nextSubjectNumber", ge=1)
    creative_controls: CreativeControls = Field(default_factory=CreativeControls, alias="creativeControls")

    @field_validator("creative_request")
    @classmethod
    def require_request(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("main prompt / idea is required")
        return value

    @model_validator(mode="after")
    def validate_mode_assets(self) -> "Workspace":
        # Old projects had one implicit identity.  Materialize only the smallest
        # stable registry needed to retain that exact meaning on later saves.
        identity_ids = {
            asset.subject_identity.subject_id
            for asset in self.assets
            if asset.kind == AssetKind.IMAGE and asset.role == "subject_identity"
        }
        known_ids = {subject.id for subject in self.subjects}
        next_number = max([self.next_subject_number - 1, *(subject.number for subject in self.subjects)], default=0) + 1
        for subject_id in sorted(identity_ids - known_ids):
            number = 1 if subject_id == "subject-1" and not self.subjects else next_number
            self.subjects.append(SubjectRecord(id=subject_id, number=number))
            next_number = max(next_number, number + 1)
        self.subjects.sort(key=lambda subject: (subject.number, subject.id))
        numbers = [subject.number for subject in self.subjects]
        if len(set(known_ids | identity_ids)) != len(self.subjects):
            raise ValueError("subject IDs must be unique")
        if len(set(numbers)) != len(numbers):
            raise ValueError("subject numbers must be unique")
        self.next_subject_number = max(self.next_subject_number, *(number + 1 for number in numbers), 1)
        known_ids = {subject.id for subject in self.subjects}
        for asset in self.assets:
            if asset.kind != AssetKind.IMAGE:
                continue
            if asset.role == "subject_identity" and asset.subject_identity.subject_id not in known_ids:
                raise ValueError(f"subject identity reference {asset.id!r} points to an unknown subject")
            if asset.role == "character_comparison_scale":
                selected = asset.comparison_subject_ids
                if len(selected) < 2:
                    raise ValueError("Character Comparison / Scale requires at least two Subjects")
                if len(set(selected)) != len(selected):
                    raise ValueError("Character Comparison / Scale Subjects must be unique")
                unknown = set(selected) - known_ids
                if unknown:
                    raise ValueError(f"Character Comparison / Scale points to unknown Subjects: {sorted(unknown)}")
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
