"""Project-local saved prompt library and selected output provenance. Never part of Workspace."""
from datetime import datetime, timezone
import hashlib
from typing import Any, Literal
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

from pydantic import Field, model_validator
from miniconstruct.native_continuation_models import ContinuationExecution, ContinuationSettings

from miniconstruct.execution import StrictModel, InvalidBindings
from miniconstruct.models.workspace import H3Mode
from miniconstruct.execution_routing import GenerationRouting, EffectiveReference


class SelectionWrite(StrictModel):
    reference_map: list[EffectiveReference] | None = Field(default=None, alias="referenceMap", max_length=1000)
    prompt: str = Field(min_length=1, max_length=100000)
    source_fingerprint: str = Field(alias="sourceFingerprint", pattern=r"^[0-9a-f]{64}$")
    authoring_path: Literal["one_shot", "story"] | None = Field(default=None, alias="authoringPath")


class HistoryAdoptionWrite(StrictModel):
    prompt: str = Field(min_length=1, max_length=100000)
    authoring_path: Literal["one_shot", "story"] | None = Field(default=None, alias="authoringPath")


class ManualEditAdoptionWrite(StrictModel):
    prompt: str = Field(min_length=1, max_length=100000)
    authoring_path: Literal["one_shot", "story"] | None = Field(default=None, alias="authoringPath")


class SelectedPrompt(SelectionWrite):
    mode: H3Mode | None = None
    source_kind: Literal["generated_prompt", "adopted_history", "manual_edit"] = Field(default="generated_prompt", alias="sourceKind")
    id: UUID = Field(default_factory=uuid4)
    selected_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat(), alias="selectedAt")


class SavedPromptWrite(StrictModel):
    prompt: str = Field(min_length=1, max_length=100000)
    authoring_path: Literal["one_shot", "story"] | None = Field(default=None, alias="authoringPath")
    saved_prompt_id: UUID | None = Field(default=None, alias="savedPromptId")
    save_as_new: bool = Field(default=False, alias="saveAsNew", strict=True)

    @model_validator(mode="after")
    def explicit_operation(self):
        if self.save_as_new and self.saved_prompt_id is not None:
            raise ValueError("Save As New cannot specify an update target")
        return self


class SavedPromptDraft(StrictModel):
    prompt: str = Field(min_length=1, max_length=100000)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    saved_at: str = Field(alias="savedAt", max_length=100)
    source_fingerprint: str = Field(alias="sourceFingerprint", pattern=r"^[0-9a-f]{64}$")
    authoring_path: Literal["one_shot", "story"] = Field(alias="authoringPath")

    @model_validator(mode="after")
    def exact_hash(self):
        if hashlib.sha256(self.prompt.encode("utf-8")).hexdigest() != self.sha256:
            raise ValueError("Saved prompt SHA-256 does not match exact text")
        return self


SAVED_PROMPT_LIMIT = 100


class SavedPrompt(SavedPromptDraft):
    save_number: int = Field(alias="saveNumber", ge=1, strict=True)
    id: UUID = Field(default_factory=uuid4)


class SavedPromptCollection(StrictModel):
    prompts: list[SavedPrompt] = Field(default_factory=list, max_length=SAVED_PROMPT_LIMIT)
    active_saved_prompt_id: UUID | None = Field(default=None, alias="activeSavedPromptId")
    next_save_number: int = Field(default=1, alias="nextSaveNumber", ge=1, strict=True)

    @model_validator(mode="before")
    @classmethod
    def number_early_v4(cls, value):
        # Provisional v4 collections used canonical oldest-first append order.
        # Adapt copies only; ProjectStore writes them on the next real mutation.
        if isinstance(value, dict):
            value = dict(value)
            original = value.get("prompts", [])
            if not isinstance(original, list):
                return value  # Let the field validator report malformed collections.
            prompts = []
            for index, item in enumerate(original, 1):
                if isinstance(item, dict):
                    item = dict(item)
                    if "saveNumber" not in item and "save_number" not in item:
                        item["saveNumber"] = index
                prompts.append(item)
            value["prompts"] = prompts
            if "nextSaveNumber" not in value and "next_save_number" not in value:
                numbers = [item.get("saveNumber", item.get("save_number", 0)) if isinstance(item, dict)
                           else getattr(item, "save_number", None) for item in prompts]
                if all(type(number) is int for number in numbers):
                    value["nextSaveNumber"] = max(numbers, default=0) + 1
        return value

    @model_validator(mode="after")
    def valid_collection(self):
        ids = [item.id for item in self.prompts]
        if len(set(ids)) != len(ids):
            raise ValueError("Saved prompts must have unique IDs")
        numbers = [item.save_number for item in self.prompts]
        if numbers != sorted(set(numbers)) or self.next_save_number <= max(numbers, default=0):
            raise ValueError("Saved prompt numbers must increase in append order and nextSaveNumber must exceed them")
        if self.active_saved_prompt_id is not None and self.active_saved_prompt_id not in ids:
            raise ValueError("Active saved prompt must belong to this Generation's collection")
        return self


class SavedPromptEntryStatus(SavedPrompt):
    stale: bool = False


class SavedPromptStatus(StrictModel):
    prompts: list[SavedPromptEntryStatus] = Field(default_factory=list)
    active_saved_prompt_id: UUID | None = Field(default=None, alias="activeSavedPromptId")
    next_save_number: int = Field(default=1, alias="nextSaveNumber")
    already_saved: bool = Field(default=False, alias="alreadySaved")
    limit: int = SAVED_PROMPT_LIMIT


class RendererSeedSettings(StrictModel):
    mode: Literal["workflow_default", "random", "fixed"]
    fixed_seed: int | None = Field(default=None, alias="fixedSeed", ge=0, le=2147483647, strict=True)

    @model_validator(mode="after")
    def require_fixed_value(self):
        if (self.mode == "fixed") != (self.fixed_seed is not None):
            raise ValueError("Fixed renderer seed mode requires one seed; other modes must leave it blank")
        return self


def effective_renderer_seed_settings(saved: RendererSeedSettings | None, controlled: bool) -> RendererSeedSettings:
    return saved or RendererSeedSettings(mode="random" if controlled else "workflow_default")


class ProjectExecutionState(StrictModel):
    schema_version: Literal[4] = Field(default=4, alias="schemaVersion")
    continuation_settings: dict[str, ContinuationSettings] = Field(default_factory=dict, alias="continuationSettings")
    continuation_executions: list[ContinuationExecution] = Field(default_factory=list, alias="continuationExecutions")
    accepted_continuations: dict[str, UUID] = Field(default_factory=dict, alias="acceptedContinuations")
    selections: dict[str, SelectedPrompt] = Field(default_factory=dict)
    saved_prompts: dict[str, SavedPromptCollection] = Field(default_factory=dict, alias="savedPrompts")
    routing: dict[str, GenerationRouting] = Field(default_factory=dict)
    renderer_seed_settings: dict[str, RendererSeedSettings] = Field(default_factory=dict, alias="rendererSeedSettings")
    workflow_control_defaults: dict[str, dict[str, Any]] = Field(default_factory=dict, alias="workflowControlDefaults")
    workflow_control_overrides: dict[str, dict[str, dict[str, Any]]] = Field(default_factory=dict, alias="workflowControlOverrides")

    @model_validator(mode="before")
    @classmethod
    def upgrade(cls, value):
        if isinstance(value, dict) and value.get("schemaVersion", 1) in (1, 2, 3):
            value = {**value, "schemaVersion": 4}
            drafts = value.pop("savedPromptDrafts", {})
            collections = dict(value.get("savedPrompts", {}))
            for generation_id, draft in drafts.items():
                draft = SavedPromptDraft.model_validate(draft).model_dump(mode="json", by_alias=True)
                prompt_id = str(uuid5(NAMESPACE_URL, f"miniconstruct/saved-prompt/{generation_id}/{draft['sha256']}"))
                collections[generation_id] = {"prompts": [{**draft, "id": prompt_id}],
                                               "activeSavedPromptId": prompt_id}
            value["savedPrompts"] = collections
        return value

    @model_validator(mode="before")
    @classmethod
    def legacy_native_confirmation(cls, value):
        # Older sidecars retain evidence of only the current accepted attempt.
        # Adapt copies on read; never infer trust from availability alone, nor
        # resurrect an explicitly false confirmation from a stale pointer.
        if isinstance(value, dict):
            accepted = value.get("acceptedContinuations", value.get("accepted_continuations", {}))
            key = "continuationExecutions" if "continuationExecutions" in value else "continuation_executions"
            records = value.get(key, [])
            if isinstance(accepted, dict) and isinstance(records, list):
                value = {**value, key: [
                    {**record, "confirmed": (
                        record.get("status") == "available"
                        and str(accepted.get(str(record.get("generationId", record.get("generation_id")))))
                        == str(record.get("id"))
                    )} if isinstance(record, dict) and "confirmed" not in record else record
                    for record in records
                ]}
        return value


class SelectionStatus(StrictModel):
    current_fingerprint: str = Field(alias="currentFingerprint")
    current_fingerprints: dict[str, str] = Field(default_factory=dict, alias="currentFingerprints")
    selected: SelectedPrompt | None = None
    stale: bool = False


def require_execution_prompt(prompt: str):
    if not prompt.strip():
        raise InvalidBindings(["Execution prompt must not be blank"])
