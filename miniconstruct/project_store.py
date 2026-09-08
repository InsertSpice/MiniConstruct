from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid
import time
from functools import wraps
from threading import RLock
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator, model_serializer

from miniconstruct.models.planning import GenerationPlanning
from miniconstruct.models.workspace import Workspace, MediaPayload, validate_authoring_workspace


PROJECT_SCHEMA_VERSION = 5
SUPPORTED_PROJECT_SCHEMA_VERSIONS = (1, 2, 3, 4, 5)
PROJECTS_ROOT_ENV = "MINICONSTRUCT_PROJECTS_ROOT"
_PROJECT_DIRECTORY_PREFIX = "project-"
_MIGRATED_GENERATION_NAME = "initial-generation"

# All instances used by the server share the short filesystem transaction lock.
# No model call holds this lock.
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
    pass


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


class GenerationManifest(BaseModel):
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
    def validate_asset_entries(self) -> "GenerationManifest":
        asset_ids = [entry.asset_id for entry in self.asset_files]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("assetFiles contains duplicate asset IDs")
        return self


class SequenceManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    active_generation_id: str = Field(alias="activeGenerationId", min_length=1, max_length=128)
    generations: list[GenerationManifest] = Field(min_length=1)
    continuity_context: str = Field(default="", alias="continuityContext", max_length=20000)

    @field_validator("active_generation_id")
    @classmethod
    def validate_active_generation_id(cls, value: str) -> str:
        return _canonical_uuid(value, "activeGenerationId")

    @model_validator(mode="after")
    def validate_sequence(self) -> "SequenceManifest":
        generation_ids = [generation.generation_id for generation in self.generations]
        if len(generation_ids) != len(set(generation_ids)):
            raise ValueError("sequence contains duplicate generation IDs")
        if self.active_generation_id not in generation_ids:
            raise ValueError("activeGenerationId does not reference a Generation")
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
    sequence: SequenceManifest

    @field_validator("project_id")
    @classmethod
    def validate_project_id(cls, value: str) -> str:
        return _canonical_uuid(value, "projectId")


class ProjectManifest(ProjectManifestV4):
    """Canonical v5 manifest. Only the Story idea is durable Story authoring data."""

    schema_version: Literal[5] = Field(alias="schemaVersion")
    story_idea: str = Field(default="", alias="storyIdea", max_length=20000)


ProjectManifestAny = ProjectManifestV1 | ProjectManifestV2 | ProjectManifestV3 | ProjectManifestV4 | ProjectManifest


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


@dataclass(frozen=True)
class SequenceSummary:
    active_generation_id: str
    generations: tuple[GenerationSummary, ...]


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


@dataclass(frozen=True)
class GenerationDocument:
    """A hydrated Generation read without changing the active Generation."""

    generation_id: str
    ordinal: int
    workspace: Workspace
    incoming_state: str
    intended_outgoing_state: str
    planning: GenerationPlanning


@dataclass(frozen=True)
class StoryProjectSnapshot:
    manifest: ProjectManifestAny
    generations: tuple[GenerationDocument, ...]
    sequence_context: str
    fingerprint: str

    @property
    def allowed_refs(self) -> set[tuple[str, str]]:
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


def _sequence_summary(generations: list[GenerationManifest], active_id: str) -> SequenceSummary:
    return SequenceSummary(
        active_generation_id=active_id,
        generations=tuple(
            GenerationSummary(generation.generation_id, generation.created_at, generation.updated_at,
                              tuple({"id": a["id"], "filename": a["filename"].replace("\\", "/").split("/")[-1],
                                     "role": a["role"]} for a in generation.workspace.get("assets", [])))
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


class ProjectStore:
    """Filesystem-backed storage for the canonical v4 Project/Sequence format."""

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

    def _resolve_media(self, reference: MediaPayload, kind: str, mime_type: str) -> Path:
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
        self._verify_media(path, reference.sha256, reference.byte_length)
        return path

    def execution_payload(self, project_id: str, generation_id: str, asset_id: str,
                          kind: str, digest: str) -> tuple[Path, str]:
        """Resolve only a saved Generation's manifest entry, never upload staging."""
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
        size = entry.byte_length if entry.byte_length is not None else path.stat().st_size
        self._verify_media(path, digest, size)
        return path, entry.mime_type

    @staticmethod
    def _copy_media(source: Path, destination: Path, *, digest: str) -> None:
        """Bounded memory and atomic publication, with harmless orphan retention."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        size = source.stat().st_size
        ProjectStore._verify_media(source, digest, size)
        if destination.exists():
            ProjectStore._verify_media(destination, digest, size)
            return
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        try:
            with source.open("rb") as incoming, temporary.open("xb") as outgoing:
                shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
                outgoing.flush()
                os.fsync(outgoing.fileno())
            ProjectStore._verify_media(temporary, digest, size)
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
    def execution_snapshot(self, project_id: str, generation_id: str):
        from miniconstruct.execution_authoring import execution_authoring_fingerprint
        from miniconstruct.execution_state import SelectionStatus
        generation = self.read_generation(project_id, generation_id)
        directory = self._project_directory(project_id, must_exist=True)
        selected = self._read_execution_state(directory).selections.get(generation.generation_id)
        fingerprint = execution_authoring_fingerprint(generation.workspace)
        return generation, SelectionStatus(currentFingerprint=fingerprint, selected=selected,
                                           stale=selected is not None and selected.source_fingerprint != fingerprint)

    @_project_transaction
    def select_execution_prompt(self, project_id: str, generation_id: str, request):
        from miniconstruct.execution import InvalidBindings
        from miniconstruct.execution_state import SelectedPrompt, validate_selected_prompt
        generation, status = self.execution_snapshot(project_id, generation_id)
        if request.source_fingerprint != status.current_fingerprint:
            raise InvalidBindings(["This prompt was generated for a different Generation state. Save/regenerate "
                                   "or select a prompt produced for the current saved Generation."])
        from miniconstruct.execution_routing import prompt_projection, routing_from_references
        try:
            prompt_workspace = prompt_projection(generation.workspace, routing_from_references(generation.workspace, request.reference_map))
        except ValueError as exc:
            raise InvalidBindings([str(exc)]) from exc
        validate_selected_prompt(request.prompt, prompt_workspace)
        directory = self._project_directory(project_id, must_exist=True)
        state = self._read_execution_state(directory)
        state.selections[generation.generation_id] = SelectedPrompt(**request.model_dump())
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
        from miniconstruct.execution_routing import GenerationRouting
        generation = self.read_generation(project_id, generation_id)
        directory = self._project_directory(project_id, must_exist=True)
        routing = self._read_execution_state(directory).routing.get(generation_id, GenerationRouting())
        ids = {asset.id for asset in generation.workspace.assets}
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
                f"Unsupported project schema version {version!r}; supported versions are 1, 2, 3, 4, and 5"
            )
        try:
            manifest: ProjectManifestAny = (
                ProjectManifestV1.model_validate(raw)
                if version == 1
                else ProjectManifestV2.model_validate(raw)
                if version == 2
                else ProjectManifestV3.model_validate(raw)
                if version == 3
                else ProjectManifestV4.model_validate(raw)
                if version == 4
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
        return manifest

    @staticmethod
    def _generations(manifest: ProjectManifestAny) -> tuple[list[GenerationManifest], str]:
        if isinstance(manifest, ProjectManifestV1):
            generation = GenerationManifest(
                generationId=_migrated_generation_id(manifest.project_id),
                createdAt=manifest.created_at,
                updatedAt=manifest.updated_at,
                workspace=manifest.workspace,
                assetFiles=manifest.asset_files,
                planning=GenerationPlanning(),
            )
            return [generation], generation.generation_id
        if isinstance(manifest, ProjectManifestV2):
            return [
                GenerationManifest(
                    generationId=generation.generation_id,
                    createdAt=generation.created_at,
                    updatedAt=generation.updated_at,
                    workspace=generation.workspace,
                    assetFiles=generation.asset_files,
                    planning=GenerationPlanning(),
                )
                for generation in manifest.sequence.generations
            ], manifest.sequence.active_generation_id
        if isinstance(manifest, ProjectManifestV3):
            return [
                GenerationManifest(
                    generationId=generation.generation_id,
                    createdAt=generation.created_at,
                    updatedAt=generation.updated_at,
                    workspace=generation.workspace,
                    assetFiles=generation.asset_files,
                    continuity=generation.continuity.model_copy(deep=True),
                    planning=GenerationPlanning(),
                )
                for generation in manifest.sequence.generations
            ], manifest.sequence.active_generation_id
        return manifest.sequence.generations, manifest.sequence.active_generation_id

    @staticmethod
    def _story_idea(manifest: ProjectManifestAny) -> str:
        return manifest.story_idea if isinstance(manifest, ProjectManifest) else ""

    @staticmethod
    def _sequence_context(manifest: ProjectManifestAny) -> str:
        return manifest.sequence.continuity_context if isinstance(manifest, (ProjectManifestV3, ProjectManifestV4)) else ""

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

    def _validate_persisted_workspace(self, generation: GenerationManifest, project_id: str, name: str) -> None:
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
            validate_authoring_workspace(workspace_data)
        except Exception as exc:
            raise MalformedProjectError("project.json contains an invalid Generation Workspace") from exc

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

    def _hydrate_workspace(
        self,
        directory: Path,
        project_id: str,
        name: str,
        workspace_payload: dict[str, Any],
        asset_files: list[AssetFileManifest],
    ) -> Workspace:
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
            if isinstance(asset, dict) and asset.get("kind") in {"audio", "video"}:
                entry = assets_by_id.get(asset.get("id"))
                asset.pop("mediaPayload", None)
                if entry is not None:
                    path = self._safe_project_relative_path(directory, entry.path)
                    self._verify_media(path, entry.content_sha256, entry.byte_length)
                    asset["mediaPayload"] = {"sha256": entry.content_sha256,
                        "byteLength": entry.byte_length, "projectId": project_id}
                continue
            if not isinstance(asset, dict) or asset.get("kind") != "image":
                continue
            asset_file = assets_by_id.get(asset.get("id"))
            if asset_file is None:
                raise MalformedProjectError(f"image asset {asset.get('id')!r} has no stored payload")
            payload_path = self._safe_project_relative_path(directory, asset_file.path)
            if payload_path.is_symlink() or not payload_path.is_file():
                raise MalformedProjectError(f"stored payload for image asset {asset_file.asset_id!r} is missing")
            try:
                payload = payload_path.read_bytes()
            except OSError as exc:
                raise MalformedProjectError(
                    f"stored payload for image asset {asset_file.asset_id!r} cannot be read"
                ) from exc
            asset["image"] = {
                "data_url": _data_url(asset_file.mime_type, payload),
                "width": asset_file.width,
                "height": asset_file.height,
            }

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
        return ProjectDocument(
            manifest.project_id, manifest.name, manifest.created_at, manifest.updated_at,
            workspace, active_id, len(generations),
            _sequence_summary(generations, active_id),
            self._sequence_context(manifest),
            active.continuity.incoming_state,
            active.continuity.intended_outgoing_state,
            active.planning.model_copy(deep=True),
            self._story_idea(manifest),
        )

    def _externalize_workspace(
        self, directory: Path, workspace: Workspace, project_id: str, name: str,
    ) -> tuple[dict[str, Any], list[AssetFileManifest], list[tuple[Path, bytes | Path]], Workspace]:
        normalized_workspace = workspace.model_copy(update={"project_id": project_id, "project_name": name})
        workspace_data = normalized_workspace.model_dump(mode="json", by_alias=True)
        # Project identity is authoritative at the manifest level. It is injected
        # back during hydration for the existing API/UI contract.
        workspace_data.pop("projectId", None)
        workspace_data.pop("projectName", None)
        asset_files: list[AssetFileManifest] = []
        pending_blobs: list[tuple[Path, bytes | Path]] = []
        for asset in workspace_data.get("assets", []):
            media = asset.pop("mediaPayload", None)
            if asset.get("kind") != "image":
                if media is not None:
                    reference = MediaPayload.model_validate(media)
                    source = self._resolve_media(reference, asset["kind"], asset["mimeType"])
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
        # Project identity belongs to the manifest. Canonicalize every
        # carried-forward snapshot at the final write boundary so structural
        # upgrades from v1 cannot leak the old embedded identity fields.
        canonical_generations = [
            generation.model_copy(update={
                "workspace": {
                    key: value
                    for key, value in generation.workspace.items()
                    if key not in {"projectId", "projectName"}
                },
            })
            for generation in manifest.sequence.generations
        ]
        manifest = manifest.model_copy(update={
            "sequence": manifest.sequence.model_copy(update={"generations": canonical_generations}),
        })
        for payload_path, payload in pending_blobs:
            payload_path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(payload, Path):
                self._copy_media(payload, payload_path, digest=payload_path.stem)
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
        story_idea: str = "",
        sequence_context: str = "",
        incoming_state: str = "",
        intended_outgoing_state: str = "",
        planning: GenerationPlanning | dict[str, Any] | None = None,
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
            generation = GenerationManifest(
                generationId=generation_id, createdAt=created_at, updatedAt=created_at,
                workspace=workspace_data, assetFiles=asset_files,
                continuity=GenerationContinuityManifest(
                    incomingState=incoming_state,
                    intendedOutgoingState=intended_outgoing_state,
                ),
                planning=_merged_planning(None, planning),
            )
            self._validate_generation_asset_mapping(generation)
            manifest = ProjectManifest(
                schemaVersion=5, storyIdea=story_idea, projectId=project_id, name=name,
                createdAt=created_at, updatedAt=created_at,
                sequence=SequenceManifest(
                    activeGenerationId=generation_id,
                    generations=[generation],
                    continuityContext=sequence_context,
                ),
            )
            self._write_manifest(staging, manifest, pending_blobs)
            if directory.exists():
                raise ProjectStoreError(f"project directory for {project_id} already exists")
            os.replace(staging, directory)
            return ProjectDocument(
                project_id, name, created_at, created_at, normalized_workspace, generation_id, 1,
                _sequence_summary([generation], generation_id),
                sequence_context, incoming_state, intended_outgoing_state, generation.planning, story_idea,
            )
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def load_project(self, project_id: str) -> ProjectDocument:
        return self._document(self._project_directory(project_id, must_exist=True))

    @staticmethod
    def _manifest_fingerprint(manifest: ProjectManifestAny) -> str:
        payload = json.dumps(manifest.model_dump(mode="json", by_alias=True), sort_keys=True, separators=(",", ":"))
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
        ) for index, g in enumerate(generations))
        return StoryProjectSnapshot(manifest, hydrated, self._sequence_context(manifest), self._manifest_fingerprint(manifest))

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
                self._materialize_story_references(generation, item, snapshot, originals)

            generation.workspace["creativeRequest"] = item.creative_request
            generation.continuity = GenerationContinuityManifest(
                incomingState=item.incoming_state, intendedOutgoingState=item.intended_outgoing_state,
            )
            generation.planning = GenerationPlanning(
                relationshipToPrevious=item.relationship_to_previous, cameraIntent=item.camera_intent,
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

    def _materialize_story_references(self, target, item, snapshot, originals) -> None:
        """Copy committed metadata and share stored Pictures; never write payloads."""
        sources = {g.generation_id: g for g in originals}
        hydrated = {g.generation_id: g.workspace for g in snapshot.generations}
        subjects = {}
        for recommendation in item.reference_recommendations:
            source = sources[recommendation.source_generation_id]
            workspace = hydrated[source.generation_id]
            matches = [a for a in workspace.assets if a.id == recommendation.asset_id]
            if len(matches) != 1:
                raise ProjectStoreError("Planned reference does not resolve to one source asset")
            asset = matches[0]
            required = ([asset.subject_identity.subject_id] if asset.role == "subject_identity"
                        else asset.comparison_subject_ids if asset.role == "character_comparison_scale" else [])
            authority = {subject.id: subject for subject in workspace.subjects}
            for subject_id in required:
                subject = authority.get(subject_id)
                if subject is None:
                    raise ProjectStoreError("Planned reference requires a missing source Subject")
                record = subject.model_dump(mode="json")
                if subject_id in subjects and subjects[subject_id] != record:
                    raise ProjectStoreError("Planned references have conflicting Subject authority")
                if any(other["number"] == record["number"] and other["id"] != subject_id
                       for other in subjects.values()):
                    raise ProjectStoreError("Planned references have conflicting Subject numbers")
                subjects[subject_id] = record
            # Namespace every copied identity by the new target and exact source pair.
            target_id = str(uuid.uuid5(uuid.UUID(target.generation_id),
                                      json.dumps([source.generation_id, asset.id])))
            copied = json.loads(json.dumps(next(a for a in source.workspace["assets"] if a["id"] == asset.id)))
            copied["id"] = target_id
            target.workspace["assets"].append(copied)
            for entry in source.asset_files:
                if entry.asset_id == asset.id:
                    target.asset_files.append(entry.model_copy(update={"asset_id": target_id}, deep=True))
        target.workspace["subjects"] = sorted(subjects.values(), key=lambda s: (s["number"], s["id"]))
        target.workspace["nextSubjectNumber"] = max((s["number"] + 1 for s in subjects.values()), default=1)
        ids = [a["id"] for a in target.workspace["assets"]]
        if len(ids) != len(set(ids)):
            raise ProjectStoreError("Planned references produced conflicting asset identities")

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
            workspace=self._hydrate_workspace(
                directory, manifest.project_id, manifest.name, generation.workspace, generation.asset_files,
            ),
            incoming_state=generation.continuity.incoming_state,
            intended_outgoing_state=generation.continuity.intended_outgoing_state,
            planning=generation.planning.model_copy(deep=True),
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
            workspace=self._hydrate_workspace(
                directory, manifest.project_id, manifest.name, predecessor.workspace, predecessor.asset_files,
            ),
            incoming_state=predecessor.continuity.incoming_state,
            intended_outgoing_state=predecessor.continuity.intended_outgoing_state,
            planning=predecessor.planning.model_copy(deep=True),
        )

    @_project_transaction
    def delete_project(self, project_id: str) -> None:
        canonical_id = _canonical_project_id(project_id)
        directory = self._project_directory(canonical_id, must_exist=True)
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
        story_idea: str | None = None,
        sequence_context: str | None = None,
        incoming_state: str | None = None,
        intended_outgoing_state: str | None = None,
        planning: GenerationPlanning | dict[str, Any] | None = None,
    ) -> ProjectDocument:
        canonical_id = _canonical_project_id(project_id)
        directory = self._project_directory(canonical_id, must_exist=True)
        existing = self._read_manifest(directory)
        generations, active_id = self._generations(existing)
        active = next(generation for generation in generations if generation.generation_id == active_id)
        name = workspace.project_name.strip() or "Untitled Project"
        workspace_data, asset_files, pending_blobs, normalized_workspace = self._externalize_workspace(
            directory, workspace, canonical_id, name
        )
        replacement = GenerationManifest(
            generationId=active.generation_id,
            createdAt=active.created_at,
            updatedAt=self._now(),
            workspace=workspace_data,
            assetFiles=asset_files,
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
        manifest = ProjectManifest(
            schemaVersion=5, storyIdea=self._story_idea(existing) if story_idea is None else story_idea, projectId=canonical_id, name=name,
            createdAt=existing.created_at, updatedAt=self._now(),
            sequence=SequenceManifest(
                activeGenerationId=active_id,
                generations=updated_generations,
                continuityContext=(
                    self._sequence_context(existing)
                    if sequence_context is None else sequence_context
                ),
            ),
        )
        self._write_manifest(directory, manifest, pending_blobs)
        return ProjectDocument(
            canonical_id, name, existing.created_at, manifest.updated_at,
            normalized_workspace, active_id, len(updated_generations),
            _sequence_summary(updated_generations, active_id),
            manifest.sequence.continuity_context,
            replacement.continuity.incoming_state,
            replacement.continuity.intended_outgoing_state,
            replacement.planning.model_copy(deep=True),
            manifest.story_idea,
        )

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
        sequence_context: str | None = None,
        updated_at: str | None = None,
    ) -> ProjectDocument:
        project_name = (name if name is not None else existing.name).strip() or "Untitled Project"
        manifest = ProjectManifest(
            schemaVersion=5,
            storyIdea=self._story_idea(existing) if story_idea is None else story_idea,
            projectId=existing.project_id,
            name=project_name,
            createdAt=existing.created_at,
            updatedAt=updated_at or self._now(),
            sequence=SequenceManifest(
                activeGenerationId=active_id,
                generations=generations,
                continuityContext=self._sequence_context(existing) if sequence_context is None else sequence_context,
            ),
        )
        self._write_manifest(directory, manifest, pending_blobs or [])
        return self._document(directory)

    def _mutation_context(
        self, project_id: str,
    ) -> tuple[str, Path, ProjectManifestAny, list[GenerationManifest], str]:
        canonical_id = _canonical_project_id(project_id)
        directory = self._project_directory(canonical_id, must_exist=True)
        existing = self._read_manifest(directory)
        generations, active_id = self._generations(existing)
        return canonical_id, directory, existing, list(generations), active_id

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
        generations.insert(insert_index, generation)
        return self._commit_sequence(directory, existing, generations, generation.generation_id, pending_blobs=pending_blobs)

    @_project_transaction
    def activate_generation(self, project_id: str, generation_id: str) -> ProjectDocument:
        _canonical_id, directory, existing, generations, _active_id = self._mutation_context(project_id)
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
        )
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
        execution_state = self._read_execution_state(directory)
        execution_state.selections.pop(requested_id, None)
        execution_state.routing.pop(requested_id, None)
        result = self._commit_sequence(directory, existing, generations, replacement_id)
        self._write_execution_state(directory, execution_state)
        return result

    @_project_transaction
    def duplicate_project(
        self,
        project_id: str,
        name: str,
        *,
        active_workspace: Workspace | None = None,
        story_idea: str | None = None,
        sequence_context: str | None = None,
        active_incoming_state: str | None = None,
        active_intended_outgoing_state: str | None = None,
        active_planning: GenerationPlanning | dict[str, Any] | None = None,
    ) -> ProjectDocument:
        """Create a new identity domain containing a deep Sequence clone."""
        _source_id, source_directory, existing, source_generations, source_active = self._mutation_context(project_id)
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
        try:
            staging.mkdir()
            for relative in ("assets/pictures", "assets/videos", "assets/audio", "outputs", "workflows", "provenance"):
                (staging / relative).mkdir(parents=True, exist_ok=True)
            for source in source_generations:
                new_id = str(uuid.uuid4())
                id_map[source.generation_id] = new_id
                if source.generation_id == source_active and active_workspace is not None:
                    workspace_data, copied_assets, override_blobs, _normalized = self._externalize_workspace(
                        staging, active_workspace, new_project_id, project_name,
                    )
                    pending_blobs.extend(override_blobs)
                else:
                    workspace_data = json.loads(json.dumps(source.workspace))
                    copied_assets = [entry.model_copy(deep=True) for entry in source.asset_files]
                    for asset_file in copied_assets:
                        source_path = self._safe_project_relative_path(source_directory, asset_file.path)
                        destination_path = self._safe_project_relative_path(staging, asset_file.path)
                        if source_path.is_symlink() or not source_path.is_file():
                            raise MalformedProjectError(f"stored payload for image asset {asset_file.asset_id!r} is missing")
                        destination_path.parent.mkdir(parents=True, exist_ok=True)
                        if asset_file.kind != "image":
                            self._verify_media(source_path, asset_file.content_sha256, asset_file.byte_length)
                            self._copy_media(source_path, destination_path, digest=asset_file.content_sha256)
                            continue
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
            new_active = id_map[source_active]
            now = _utc_now()
            manifest = ProjectManifest(
                schemaVersion=5,
                storyIdea=self._story_idea(existing) if story_idea is None else story_idea,
                projectId=new_project_id,
                name=project_name,
                createdAt=now,
                updatedAt=now,
                sequence=SequenceManifest(
                    activeGenerationId=new_active,
                    generations=new_generations,
                    continuityContext=(
                        self._sequence_context(existing)
                        if sequence_context is None else sequence_context
                    ),
                ),
            )
            self._write_manifest(staging, manifest, pending_blobs)
            execution_state = self._read_execution_state(source_directory)
            execution_state.selections = {id_map[key]: value for key, value in execution_state.selections.items() if key in id_map}
            execution_state.routing = {id_map[key]: value for key, value in execution_state.routing.items() if key in id_map}
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
            manifest = self._read_manifest(directory, validate_workspaces=False)
            summaries.append(ProjectSummary(manifest.project_id, manifest.name, manifest.created_at, manifest.updated_at))
        return sorted(summaries, key=lambda item: item.updated_at, reverse=True)
