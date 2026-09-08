"""Project-local selected output provenance. Never part of Workspace."""
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field

from miniconstruct.execution import StrictModel, InvalidBindings
from miniconstruct.h3.validator import validate_workspace_prompt
from miniconstruct.models.workspace import Workspace
from miniconstruct.execution_routing import GenerationRouting, EffectiveReference


class SelectionWrite(StrictModel):
    reference_map: list[EffectiveReference] | None = Field(default=None, alias="referenceMap", max_length=1000)
    prompt: str = Field(min_length=1, max_length=100000)
    source_fingerprint: str = Field(alias="sourceFingerprint", pattern=r"^[0-9a-f]{64}$")


class SelectedPrompt(SelectionWrite):
    id: UUID = Field(default_factory=uuid4)
    selected_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat(), alias="selectedAt")


class ProjectExecutionState(StrictModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    selections: dict[str, SelectedPrompt] = Field(default_factory=dict)
    routing: dict[str, GenerationRouting] = Field(default_factory=dict)


class SelectionStatus(StrictModel):
    current_fingerprint: str = Field(alias="currentFingerprint")
    selected: SelectedPrompt | None = None
    stale: bool = False


def validate_selected_prompt(prompt: str, workspace: Workspace):
    if not prompt.strip():
        raise InvalidBindings(["Generated H3 prompt must not be blank"])
    # Reuse normal generation readiness, not the permissive authoring validator.
    try:
        ready = Workspace.model_validate(workspace.model_dump(mode="json", by_alias=True))
    except ValueError as exc:
        raise InvalidBindings(["Saved Generation is not ready for H3: " + "; ".join(
            error["msg"] for error in exc.errors(include_input=False))]) from exc
    validation = validate_workspace_prompt(prompt, ready)
    if not validation.valid:
        raise InvalidBindings([finding.message for finding in validation.findings if finding.severity == "ERROR"])
