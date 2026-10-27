"""Profile-declared scalar workflow controls and execution-side inheritance."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from miniconstruct.execution import control_value
from miniconstruct.native_continuation import digest


def definitions_fingerprint(profile) -> str:
    return digest([control.model_dump(mode="json", by_alias=True) for control in profile.exposed_controls])


def resolve_controls(store, project_id: str, generation_id: str, profile,
                     retry_values: dict[str, Any] | None = None, *, diagnostics=None):
    project, generation = store.workflow_control_values(project_id, generation_id, str(profile.id))
    declared = {control.id: control for control in profile.exposed_controls}
    from miniconstruct.preparation_diagnostics import PreparationDiagnostics
    diagnostics = diagnostics if diagnostics is not None else PreparationDiagnostics()
    warnings = []
    for scope, values in (("Project default", project), ("Generation override", generation)):
        for key in sorted(values.keys() - declared.keys()):
            diagnostics.add("control.removed_default", f"{scope} for removed Workflow Control {key} was ignored.", "info")
    if retry_values is not None and set(retry_values) != set(declared):
        # Exact Retry must use the values recorded by the original attempt. A
        # declaration edit can add, remove, or rename IDs, so never synthesize
        # values from today's defaults while compiling a historical attempt.
        from miniconstruct.workflow_preparation import InvalidBindings
        raise InvalidBindings(["The Workflow Control declaration changed after this render. Use normal Prepare / Send."])
    result = []
    for control in profile.exposed_controls:
        value, source = control.default_value, "profile_default"
        for candidate, name in ((project, "project_default"), (generation, "generation_override")):
            if control.id not in candidate:
                continue
            try:
                value = control_value(control, candidate[control.id])
                source = name
            except ValueError:
                message = f"{name} for Workflow Control {control.id} is stale; inherited value was used."
                warnings.append(message)
                diagnostics.add("control.stale_value", message)
        if retry_values is not None:
            value = control_value(control, retry_values[control.id])
            source = "generation_override"
        result.append({"id": control.id, "label": control.label, "value": value, "source": source})
    return result, warnings


def apply_controls(graph: dict, profile, resolved: list[dict]):
    # Graph is already a deep copy. Recopy structured inputs before touching a leaf.
    by_id = {item["id"]: item["value"] for item in resolved}
    for control in profile.exposed_controls:
        target = control.target
        inputs = graph[target.node_id]["inputs"]
        if not target.path:
            inputs[target.input_name] = by_id[control.id]
            continue
        inputs[target.input_name] = deepcopy(inputs[target.input_name])
        parent = inputs[target.input_name]
        for key in target.path[:-1]:
            parent = parent[key]
        parent[target.path[-1]] = by_id[control.id]
