from __future__ import annotations

import base64
from collections.abc import Callable
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import unquote_to_bytes
import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator, model_validator

from miniconstruct.generation import ProductionGeneration, generate_workspace_prompts
from miniconstruct.h3.builder import assemble_prompt, numbered_assets
from miniconstruct.llm.client import LLMBackendError, OpenAICompatibleClient
from miniconstruct.llm.compatibility import build_generation_payload
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
    SeedMode,
)
from miniconstruct.models.planning import GenerationPlanning, RelationshipToPrevious
from miniconstruct.models.workspace import AssetKind, ImagePayload, Workspace, validate_authoring_workspace
from miniconstruct.project_store import GenerationDocument
from miniconstruct import planning as planning_service


RESULT_SCHEMA_VERSION = 1
CASE_SCHEMA_VERSION = 1
PROFILE_SCHEMA_VERSION = 1
BENCHMARK_SOURCE_ROOT_ENV = "MINICONSTRUCT_BENCHMARK_SOURCE_ROOT"


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
    "story_selected_fields_only_changed", "story_recommendation_pairs_valid",
    "story_no_duplicate_recommendation_pairs", "story_warning_exists", "image_input_count_equals",
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
    source: Literal["case", "external"] = "case"


class StoryGenerationFixture(PlannerGenerationFixture):
    workspace_path: str = Field(alias="workspacePath", min_length=1)
    fixture_assets: list[FixtureAsset] = Field(default_factory=list, alias="fixtureAssets")


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


class StoryReferenceFixture(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    source_generation: int = Field(alias="sourceGeneration", ge=1, le=12, strict=True)
    asset_id: str = Field(alias="assetId", min_length=1, max_length=128)
    recommendation: str = Field(min_length=1, max_length=4000)


class StoryItemFixture(BaseModel):
    """Complete authoring draft, with portable one-based source identities."""
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    existing_generation: int | None = Field(alias="existingGeneration", ge=1, le=12, strict=True)
    creative_request: str = Field(alias="creativeRequest", max_length=50000)
    incoming_state: str = Field(alias="incomingState", max_length=20000)
    intended_outgoing_state: str = Field(alias="intendedOutgoingState", max_length=20000)
    relationship_to_previous: RelationshipToPrevious | None = Field(alias="relationshipToPrevious")
    camera_intent: str = Field(alias="cameraIntent", max_length=20000)
    reference_recommendations: list[StoryReferenceFixture] = Field(alias="referenceRecommendations", max_length=256)


class StoryDraftFixture(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    sequence_context: str = Field(alias="sequenceContext", max_length=20000)
    items: list[StoryItemFixture] = Field(min_length=2, max_length=12)


class StoryRevisionFixture(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    draft: StoryDraftFixture
    selected_items: list[Annotated[int, Field(strict=True, ge=1, le=12)]] = Field(alias="selectedItems", min_length=1, max_length=12)
    scopes: list[PlanningRevisionScope] = Field(min_length=1, max_length=5)
    instruction: str = Field(min_length=1, max_length=8000)

    @model_validator(mode="after")
    def selection_is_valid(self):
        if len(set(self.selected_items)) != len(self.selected_items) or max(self.selected_items) > len(self.draft.items):
            raise ValueError("selectedItems must be unique draft ordinals")
        if len(set(self.scopes)) != len(self.scopes) or not self.instruction.strip():
            raise ValueError("unique scopes and nonblank instruction required")
        return self


class StoryFixture(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    sequence_context: str = Field(default="", alias="sequenceContext", max_length=20000)
    generations: list[StoryGenerationFixture] = Field(min_length=1, max_length=12)
    build: StoryBuildFixture | None = None
    revision: StoryRevisionFixture | None = None

    @model_validator(mode="after")
    def validate_draft(self):
        if (self.build is None) == (self.revision is None):
            raise ValueError("Story fixture requires exactly one of build or revision")
        if self.generations[0].planning.relationship_to_previous is not None:
            raise ValueError("First canonical Generation relationship must be null")
        if self.build and self.build.desired_generation_count < len(self.generations):
            raise ValueError("desired count cannot be smaller than canonical Sequence")
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
        return self


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
    human_review: list[str] = Field(default_factory=list, alias="humanReview")
    fixture_assets: list[FixtureAsset] = Field(default_factory=list, alias="fixtureAssets")
    operation: str | None = None
    planner: PlannerFixture | None = None
    story: StoryFixture | None = None

    @field_validator("operation")
    @classmethod
    def validate_case_operation(cls, value: str | None) -> str | None:
        if value is not None and value not in {"generate_prompt", "infer_plan", "revise_plan", "build_story_plan", "revise_story_plan"}:
            raise ValueError(f"unsupported benchmark operation {value!r}")
        return value

    @model_validator(mode="after")
    def validate_operation_shape(self) -> "BenchmarkCase":
        if self.operation in {"build_story_plan", "revise_story_plan"}:
            if self.story is None or self.planner is not None:
                raise ValueError("Story operations require story configuration and must not contain planner")
            if (self.operation == "build_story_plan") != (self.story.build is not None):
                raise ValueError("case operation does not match story configuration")
            if any(check.kind not in STORY_CHECK_KINDS | {"forbidden_text_absent"} for check in self.expectations.checks):
                raise ValueError("Story cases require Story-compatible checks")
        elif self.story is not None:
            raise ValueError("Only Story operations may contain story configuration")
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
    timeout_seconds: float = Field(default=120, alias="timeoutSeconds", ge=1, le=600)
    reasoning_mode: ReasoningMode = Field(default=ReasoningMode.OFF, alias="reasoningMode")

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
            maxTokens=self.max_tokens,
            timeoutSeconds=self.timeout_seconds,
            reasoningMode=self.reasoning_mode,
            temperature=0.4,
            seedMode=SeedMode.FIXED,
            fixedSeed=1,
        )

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "displayName": self.display_name,
            "endpoint": self.endpoint.public_copy().model_dump(mode="json", by_alias=True),
            "modelId": self.model_id,
            "supportsVision": self.supports_vision,
            "maxTokens": self.max_tokens,
            "timeoutSeconds": self.timeout_seconds,
            "reasoningMode": self.reasoning_mode.value,
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
            raise ValueError(f"Benchmark fixture path {fixture.path} is only valid for an image asset")
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


def _decode_data_image(url: str) -> tuple[bytes, str | None] | None:
    """Decode MiniConstruct's data URL image input without retaining its source."""
    if not url.startswith("data:") or "," not in url:
        return None
    header, payload = url[5:].split(",", 1)
    mime_type = header.split(";", 1)[0] or None
    try:
        data = base64.b64decode(payload, validate=True) if ";base64" in header.lower() else unquote_to_bytes(payload)
    except (ValueError, UnicodeEncodeError):
        return None
    return data, mime_type


def _workspace_image_mappings(case: BenchmarkCase, workspace: Workspace) -> dict[str, list[dict[str, str]]]:
    """Map only exact image bytes; filenames are never used as an identity heuristic."""
    fixtures = {fixture.asset_id: fixture for fixture in case.fixture_assets}
    mappings: dict[str, list[dict[str, str]]] = {}
    for asset in workspace.assets:
        if asset.kind != AssetKind.IMAGE or asset.image is None:
            continue
        decoded = _decode_data_image(asset.image.data_url)
        if decoded is None:
            continue
        digest = hashlib.sha256(decoded[0]).hexdigest()
        mappings.setdefault(digest, []).append({
            "assetId": asset.id, "filename": asset.filename,
            **({"fixturePath": fixtures[asset.id].path} if asset.id in fixtures else {}),
            **({"fixtureSource": "external"} if asset.id in fixtures and fixtures[asset.id].source == "external" else {}),
        })
    return mappings


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
                    decoded = _decode_data_image(url) if isinstance(url, str) else None
                    image = {"index": len(images) + 1, "sha256": None, "mimeType": None}
                    if decoded is not None:
                        image["sha256"] = hashlib.sha256(decoded[0]).hexdigest()
                        image["mimeType"] = decoded[1]
                        matches = (image_mappings or {}).get(image["sha256"], [])
                        if len(matches) == 1:
                            image.update(matches[0])
                    images.append(image)
    return {"system": system, "user": "\n\n".join(user_text), "imageInputCount": len(images), "images": images}


def _effective_request_parameters(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep output-affecting request fields, never messages, media, credentials, or headers."""
    allowed = {
        "model", "temperature", "max_tokens", "max_completion_tokens", "seed", "stream",
        "stream_options", "chat_template_kwargs", "reasoning_effort", "top_p", "presence_penalty", "frequency_penalty",
    }
    return {key: payload[key] for key in allowed if key in payload}


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
                result = self._planner_skip_result(profile, case)
                result["settings"].update(repair="never", automaticRetry="never", llmJudge="none")
                return result
            from .story import run_story_case
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
        request = GenerationRequest(workspace=workspace.model_copy(update={"variations": 1}), llm=settings, resolvedSeeds=[1])
        # This is the same outgoing preparation used by OpenAICompatibleClient.
        assembled = assemble_prompt(workspace, settings.supports_vision)
        expected_payload, _ = build_generation_payload(settings.model_copy(update={"seed": 1}), assembled.messages, stream=False)
        try:
            generated = await generate_workspace_prompts(request, self.client_factory)
        except LLMBackendError as exc:
            base.pop("_expectations")
            return {**base, "status": "error", "generationTransport": "non_streaming", "modelInput": _messages_provenance(expected_payload["messages"], _workspace_image_mappings(case, workspace)), "effectiveRequest": _effective_request_parameters(expected_payload), "error": {"type": "provider", "message": str(exc), "statusCode": exc.status_code, "upstreamStatus": exc.upstream_status}}
        return self._completed_case(base, generated, expected_payload, _workspace_image_mappings(case, workspace))

    def _planner_skip_result(
        self,
        profile: BenchmarkProfile,
        case: BenchmarkCase,
    ) -> dict[str, Any]:
        """Return a capability skip before resolving planner fixtures or creating a client."""
        planner = case.planner
        operation = planner.operation if planner is not None else case.operation
        settings = self._planner_settings(profile)
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
        image_mappings = _workspace_image_mappings(case, current)
        if previous is not None:
            previous_case = case.model_copy(update={"fixture_assets": case.fixture_assets})
            image_mappings = _workspace_image_mappings(previous_case, previous) | image_mappings
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
        seed_dropped = variation.requested_seed is not None and not final_request_includes_seed
        seed_status = "not_requested" if variation.requested_seed is None else ("dropped_unsupported" if seed_dropped else "accepted_by_provider")
        return {
            **base, "status": "completed", "generationTransport": "non_streaming", "warnings": generated.warnings,
            "modelInput": _messages_provenance(payload["messages"], image_mappings),
            "effectiveRequest": _effective_request_parameters(payload),
            "rawModelOutput": variation.generated.prompt,
            "normalizedOutput": None,
            "validation": variation.generated.validation.model_dump(mode="json"),
            "structuralValid": variation.generated.validation.valid,
            "seed": {"requested": variation.requested_seed, "mode": "fixed", "effective": variation.effective_seed,
                     "status": seed_status, "unsupportedFallback": variation.seed_unsupported,
                     "knownUnsupported": variation.seed_known_unsupported,
                     "finalRequestIncludedSeed": final_request_includes_seed},
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
        (cases_directory / f"{case['caseId']}.json").write_text(json.dumps(case, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        validation = str(case.get("structuralValid", "—"))
        expectations = str(case.get("expectations", {}).get("passed", "—"))
        lines.append(f"| {case['caseId']} | {case['status']} | {validation} | {expectations} |")
        if case.get("humanReview"):
            lines.extend(["", f"## {case['caseId']} review questions", *[f"- {item}" for item in case["humanReview"]]])
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
        if before.get("validation", {}).get("findings") != after.get("validation", {}).get("findings"): changes.append("validator findings")
        if before.get("expectations", {}).get("passed") != after.get("expectations", {}).get("passed"): changes.append("expectations")
        if before.get("provider", {}).get("usage") != after.get("provider", {}).get("usage"): changes.append("usage")
        if before.get("provider", {}).get("requestDurationMs") != after.get("provider", {}).get("requestDurationMs"): changes.append("latency")
        lines.append(f"- {case_id}: {', '.join(changes) if changes else 'unchanged'}")
    return "\n".join(lines) + "\n"
