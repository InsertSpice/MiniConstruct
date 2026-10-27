"""Provider-free generic Workflow Control contracts."""
from copy import deepcopy
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from miniconstruct import execution as ex
from miniconstruct.execution_authoring import execution_authoring_fingerprint
from miniconstruct.execution_state import ProjectExecutionState, SelectionWrite
from miniconstruct.execution_send import ExecutionSendRequest, send_generation, retry_generation
from miniconstruct.models.takes import RendererJobState
from miniconstruct.project_store import ProjectStore
from miniconstruct.app import app
from miniconstruct.workflow_preparation import prepare_workflow
from miniconstruct.native_continuation import profile_identity, digest
from test_validator import VALID_BASE


def control(id, node, input_name, value, kind, path=None, **extra):
    return {"id": id, "label": id.replace("_", " ").title(),
            "target": {"nodeId": node, "inputName": input_name, "path": path or []},
            "type": kind, "defaultValue": value, **extra}


def fixture():
    graph = {
        "h3": {"class_type": "SyntheticH3", "inputs": {"prompt": "old"}},
        "42": {"class_type": "SyntheticLoader", "inputs": {
            "model": ["h3", 0],
            "lora_1": {"on": True, "lora": "turbo.safetensors", "strength": 1.0},
            "lora_2": {"on": False, "lora": "other.safetensors", "strength": 0.7}}},
        "spectrum": {"class_type": "SyntheticSpectrum", "inputs": {"enabled": True, "blend_weight": 0.5, "warmup_steps": 1}},
        "sampling": {"class_type": "SyntheticSampling", "inputs": {"steps": 20, "sampler_name": "res_multistep", "scheduler": "simple", "shift_video": 3.0, "shift_audio": 2.0}},
        "preview": {"class_type": "SyntheticPreview", "inputs": {"max_resolution": 512, "jpeg_quality": 90}},
    }
    controls = [
        control("turbo_enabled", "42", "lora_1", True, "boolean", ["on"]),
        control("turbo_strength", "42", "lora_1", 1.0, "float", ["strength"], minimum=0.0, maximum=2.0),
        control("selected_lora", "42", "lora_1", "turbo.safetensors", "enum", ["lora"], choices=["turbo.safetensors", "other.safetensors"]),
        control("spectrum_enabled", "spectrum", "enabled", True, "boolean"),
        control("spectrum_blend", "spectrum", "blend_weight", 0.5, "float"),
        control("steps", "sampling", "steps", 20, "integer", minimum=1, maximum=100),
        control("sampler", "sampling", "sampler_name", "res_multistep", "enum", choices=["res_multistep", "euler"]),
        control("scheduler", "sampling", "scheduler", "simple", "enum", choices=["simple", "normal"]),
        control("shift_video", "sampling", "shift_video", 3.0, "float"),
        control("shift_audio", "sampling", "shift_audio", 2.0, "float"),
        control("max_resolution", "preview", "max_resolution", 512, "integer"),
        control("jpeg_quality", "preview", "jpeg_quality", 90, "integer"),
    ]
    return graph, controls


def saved(tmp_path, workspace_factory):
    graph, controls = fixture()
    config = ex.ExecutionStore(tmp_path / "execution")
    profile = config.save("profiles", ex.ProfileCreate(name="Generic", compatibleModes=list(ex.H3Mode),
        workflowJson=json.dumps(graph), bindings=[{"source": {"kind": "generated_prompt"}, "nodeId": "h3", "inputName": "prompt"}], exposedControls=controls))
    connection = config.save("connections", ex.ConnectionWrite(name="Fake", baseUrl="http://example.test"))
    store = ProjectStore(tmp_path / "projects")
    project = store.create_project(workspace_factory())
    store.select_execution_prompt(project.project_id, project.active_generation_id,
        SelectionWrite(prompt=VALID_BASE, sourceFingerprint=execution_authoring_fingerprint(project.workspace)))
    return store, config, profile, connection, project


def test_migration_v4_and_sidecar_v2(tmp_path):
    for version in range(1, 8):
        assert ex.ExecutionConfig.model_validate({"schemaVersion": version}).schema_version == 7
    state = ProjectExecutionState.model_validate({"schemaVersion": 2})
    assert state.schema_version == 4 and state.workflow_control_defaults == {} and state.workflow_control_overrides == {}


def test_zero_controls_preserve_legacy_profile_identity(tmp_path):
    graph = {"h3": {"class_type": "Synthetic", "inputs": {"prompt": "old"}}}
    profile = ex.ExecutionStore(tmp_path).save("profiles", ex.ProfileCreate(name="Legacy",
        compatibleModes=list(ex.H3Mode), workflowJson=json.dumps(graph),
        bindings=[{"source": {"kind": "generated_prompt"}, "nodeId": "h3", "inputName": "prompt"}]))
    old_shape = profile.model_dump(mode="json", by_alias=True)
    old_shape.pop("exposedControls")
    old_shape.pop("workflowControlPresets")
    assert profile_identity(profile) == digest(old_shape)


def test_zero_controls_reject_even_empty_valued_preset():
    base = dict(name="Legacy", compatibleModes=list(ex.H3Mode),
        workflowJson=json.dumps({"h3": {"class_type": "Synthetic", "inputs": {"prompt": "old"}}}),
        bindings=[{"source": {"kind": "generated_prompt"}, "nodeId": "h3", "inputName": "prompt"}],
        exposedControls=[])
    assert ex.ProfileCreate(**base, workflowControlPresets=[]).workflow_control_presets == []
    with pytest.raises(ValueError, match="require exposed controls"):
        ex.ProfileCreate(**base, workflowControlPresets=[{"name": "Empty", "values": {}}])


@pytest.mark.parametrize("change,expected", [
    (lambda g,c: c.append(deepcopy(c[0])), "duplicate ID"),
    (lambda g,c: c.append({**deepcopy(c[0]), "id": "copy"}), "duplicate target"),
    (lambda g,c: c[0]["target"].update(nodeId="missing"), "node or input"),
    (lambda g,c: c[0]["target"].update(inputName="missing"), "node or input"),
    (lambda g,c: c[0]["target"].update(path=["unknown"]), "path does not exist"),
    (lambda g,c: c[0]["target"].update(inputName="model", path=[]), "graph link"),
    (lambda g,c: c[0]["target"].update(nodeId="h3", inputName="prompt", path=[]), "semantic binding"),
])
def test_invalid_targets(change, expected):
    graph, controls = fixture(); change(graph, controls)
    errors = ex.control_errors(graph, [ex.ExposedControl.model_validate(c) for c in controls],
        [ex.Binding.model_validate({"source": {"kind": "generated_prompt"}, "nodeId": "h3", "inputName": "prompt"})], None)
    assert expected in " ".join(errors)


def test_invalid_values():
    controls = [ex.ExposedControl.model_validate(c) for c in fixture()[1]]
    by_id = {c.id: c for c in controls}
    for id, value in [("turbo_enabled", 1), ("steps", True), ("steps", 101), ("turbo_strength", float("nan")),
                      ("turbo_strength", float("inf")), ("sampler", "unknown")]:
        with pytest.raises(ValueError): ex.control_value(by_id[id], value)


def test_complete_preset_validation_and_v6_roundtrip(tmp_path, workspace_factory):
    _, config, profile, _, _ = saved(tmp_path, workspace_factory)
    definitions = [item.model_dump(mode="json", by_alias=True) for item in profile.exposed_controls]
    values = {item.id: item.default_value for item in profile.exposed_controls}
    def update(presets):
        return ex.ProfileUpdate(name=profile.name, compatibleModes=list(profile.compatible_modes),
                                bindings=profile.bindings, exposedControls=definitions,
                                workflowControlPresets=presets)
    good = {"name": "Turbo 8-step", "values": {**values, "steps": 8}}
    changed = config.save("profiles", update([good]), profile.id)
    assert changed.workflow_control_presets[0].values["steps"] == 8
    assert config.get("profiles", profile.id).workflow_control_presets[0].name == "Turbo 8-step"
    assert json.loads(config.path.read_text())["schemaVersion"] == 7
    assert profile_identity(changed) == profile_identity(profile)
    assert changed.fingerprint == profile.fingerprint
    reversed_profile = config.save("profiles", update([
        {"name": "Quality 20-step", "values": values}, good]), profile.id)
    assert [item.name for item in reversed_profile.workflow_control_presets] == ["Quality 20-step", "Turbo 8-step"]
    assert profile_identity(reversed_profile) == profile_identity(profile)
    reordered = config.save("profiles", update([good,
        {"name": "Quality 20-step", "values": values}]), profile.id)
    assert profile_identity(reordered) == profile_identity(profile)
    for presets in ([good, {**good, "name": "turbo 8-STEP"}],
                    [{**good, "values": {key: value for key, value in values.items() if key != "steps"}}],
                    [{**good, "values": {**values, "unknown": 1}}],
                    [{**good, "values": {**values, "steps": True}}],
                    [{**good, "values": {**values, "steps": 101}}],
                    [{**good, "values": {**values, "sampler": "unknown"}}]):
        with pytest.raises(ValueError): update(presets)
    with pytest.raises(ValueError): ex.ProfileCreate(name="Empty", compatibleModes=list(ex.H3Mode),
        workflowJson=json.dumps({"h3": {"class_type": "Synthetic", "inputs": {"prompt": "old"}}}),
        workflowControlPresets=[{"name": "No", "values": {"steps": 8}}])


def test_preset_copy_has_no_execution_identity_or_retry_dependency(tmp_path, workspace_factory):
    store, config, profile, connection, project = saved(tmp_path, workspace_factory)
    p, g, f = project.project_id, project.active_generation_id, str(profile.id)
    definitions = [item.model_dump(mode="json", by_alias=True) for item in profile.exposed_controls]
    defaults = {item.id: item.default_value for item in profile.exposed_controls}
    turbo = {**defaults, "steps": 8, "turbo_enabled": True, "turbo_strength": 1.0,
             "selected_lora": "turbo.safetensors"}
    quality = {**defaults, "turbo_enabled": False, "spectrum_enabled": True,
               "spectrum_blend": 0.5, "steps": 20, "sampler": "euler"}
    def edit(presets):
        return config.save("profiles", ex.ProfileUpdate(name=profile.name,
            compatibleModes=list(profile.compatible_modes), bindings=profile.bindings,
            exposedControls=definitions, workflowControlPresets=presets), profile.id)
    initial = prepare_workflow(store, p, g, profile, inspection=False)
    edited = edit([{"name": "Turbo 8-step", "values": turbo}, {"name": "Quality + Spectrum", "values": quality}])
    assert profile_identity(edited) == profile_identity(profile)
    assert prepare_workflow(store, p, g, edited, inspection=False).execution_fingerprint == initial.execution_fingerprint
    store.set_workflow_control_values(p, g, f, "project", dict(turbo))
    applied = prepare_workflow(store, p, g, edited, inspection=False)
    assert applied.graph["42"]["inputs"]["lora_1"] == {"on": True, "lora": "turbo.safetensors", "strength": 1.0}
    assert applied.graph["42"]["inputs"]["lora_2"] == profile.workflow["42"]["inputs"]["lora_2"]
    assert applied.graph["sampling"]["inputs"]["steps"] == 8
    changed = edit([{"name": "Renamed", "values": {**turbo, "steps": 10}}])
    assert profile_identity(changed) == profile_identity(profile)
    assert prepare_workflow(store, p, g, changed, inspection=False).execution_fingerprint == applied.execution_fingerprint
    assert store.workflow_control_values(p, g, f)[0] == turbo
    changed = edit([])
    assert prepare_workflow(store, p, g, changed, inspection=False).execution_fingerprint == applied.execution_fingerprint
    store.set_workflow_control_values(p, g, f, "generation", dict(quality))
    prepared = prepare_workflow(store, p, g, changed, inspection=False)
    assert prepared.graph["spectrum"]["inputs"]["enabled"] is True
    assert prepared.graph["spectrum"]["inputs"]["blend_weight"] == 0.5
    assert prepared.graph["sampling"]["inputs"]["steps"] == 20
    assert prepared.graph["sampling"]["inputs"]["sampler_name"] == "euler"
    assert store.workflow_control_values(p, g, f)[1] == quality
    store.set_workflow_control_values(p, g, f, "generation", {})
    assert prepare_workflow(store, p, g, changed, inspection=False).graph["sampling"]["inputs"]["steps"] == 8


def test_native_mapping_and_whole_input_ownership_reject_control():
    graph, controls = fixture()
    native = SimpleNamespace(inputs={"enable": SimpleNamespace(node_id="sampling", input_name="steps")}, source_links=[])
    assert "native mapping" in " ".join(ex.control_errors(graph,
        [ex.ExposedControl.model_validate(controls[5])], [], native))
    binding = ex.Binding.model_validate({"source": {"kind": "generated_prompt"}, "nodeId": "42", "inputName": "lora_1"})
    assert "semantic binding" in " ".join(ex.control_errors(graph,
        [ex.ExposedControl.model_validate(controls[0])], [binding], None))


def test_profile_edit_changes_identity_and_replacement_revalidates(tmp_path, workspace_factory):
    _, config, profile, _, _ = saved(tmp_path, workspace_factory)
    graph, controls = fixture()
    controls[0]["defaultValue"] = False
    changed = config.save("profiles", ex.ProfileUpdate(name=profile.name, compatibleModes=list(ex.H3Mode),
        bindings=profile.bindings, exposedControls=controls), profile.id)
    assert changed.fingerprint == profile.fingerprint
    assert profile_identity(changed) != profile_identity(profile)
    del graph["42"]["inputs"]["lora_1"]
    with pytest.raises(ex.InvalidBindings, match="target node or input"):
        config.save("profiles", ex.WorkflowImport(workflowJson=json.dumps(graph)), profile.id)
    assert config.get("profiles", profile.id).fingerprint == profile.fingerprint


def test_precedence_prepare_send_provenance_and_retry(tmp_path, workspace_factory):
    store, config, profile, connection, project = saved(tmp_path, workspace_factory)
    p, g, f = project.project_id, project.active_generation_id, str(profile.id)
    original = deepcopy(profile.workflow)
    initial = prepare_workflow(store, p, g, profile, inspection=False)
    store.set_workflow_control_values(p, g, f, "project", {"steps": 8, "spectrum_enabled": False,
        "sampler": "euler", "scheduler": "normal", "shift_audio": 5.0})
    second = store.create_generation(p, workspace=project.workspace).active_generation_id
    store.select_execution_prompt(p, second, SelectionWrite(prompt=VALID_BASE,
        sourceFingerprint=execution_authoring_fingerprint(project.workspace)))
    assert prepare_workflow(store, p, second, profile, inspection=False).graph["sampling"]["inputs"]["steps"] == 8
    store.set_workflow_control_values(p, g, f, "generation", {"steps": 12, "turbo_enabled": False,
        "turbo_strength": 0.4, "selected_lora": "other.safetensors", "shift_video": 4.0,
        "scheduler": "simple", "spectrum_blend": 0.8})
    prepared = prepare_workflow(store, p, g, profile, inspection=False)
    assert prepared.graph["42"]["inputs"]["lora_1"] == {"on": False, "lora": "other.safetensors", "strength": 0.4}
    assert prepared.graph["42"]["inputs"]["lora_2"] == original["42"]["inputs"]["lora_2"]
    assert prepared.graph["spectrum"]["inputs"]["enabled"] is False
    assert prepared.graph["spectrum"]["inputs"]["blend_weight"] == 0.8
    assert prepared.graph["sampling"]["inputs"]["steps"] == 12
    assert prepared.graph["sampling"]["inputs"]["shift_video"] == 4.0
    assert prepared.graph["sampling"]["inputs"]["shift_audio"] == 5.0
    assert prepared.graph["sampling"]["inputs"]["sampler_name"] == "euler"
    assert prepared.graph["sampling"]["inputs"]["scheduler"] == "simple"
    assert prepared.graph["preview"]["inputs"] == original["preview"]["inputs"]
    assert profile.workflow == original and prepared.execution_fingerprint != initial.execution_fingerprint
    assert prepared.current_fingerprint == initial.current_fingerprint
    assert prepared.profile_fingerprint == initial.profile_fingerprint
    calls = []
    def handle(request):
        body = json.loads(request.content); calls.append(body)
        return httpx.Response(200, json={"prompt_id": body["prompt_id"], "number": 1, "node_errors": {}})
    body = ExecutionSendRequest(projectId=p, generationId=g, profileId=profile.id, connectionId=connection.id)
    result = send_generation(body, store=store, config=config, transport=httpx.MockTransport(handle))
    submission = store.render_submission(p, g, result.render_submission_id)
    assert calls[0]["prompt"]["42"]["inputs"]["lora_1"]["on"] is False
    assert calls[0]["prompt"]["spectrum"]["inputs"]["enabled"] is False
    assert calls[0]["prompt"]["sampling"]["inputs"]["sampler_name"] == "euler"
    assert calls[0]["prompt"]["sampling"]["inputs"]["shift_audio"] == 5.0
    assert submission.workflow_control_values["steps"] == 12
    assert submission.workflow_control_fingerprint == prepared.workflow_control_fingerprint
    definitions = [item.model_dump(mode="json", by_alias=True) for item in profile.exposed_controls]
    preset = {"name": "Turbo 8-step", "values": {item.id: item.default_value for item in profile.exposed_controls}}
    preset["values"]["steps"] = 8
    for presets in ([preset], [{**preset, "name": "Renamed", "values": {**preset["values"], "steps": 10}}], []):
        profile = config.save("profiles", ex.ProfileUpdate(name=profile.name, compatibleModes=list(profile.compatible_modes),
            bindings=profile.bindings, exposedControls=definitions, workflowControlPresets=presets), profile.id)
        assert profile_identity(profile) == prepared.profile_fingerprint
    store.set_workflow_control_values(p, g, f, "generation", {"steps": 15})
    retried = prepare_workflow(store, p, g, profile, inspection=False, retry_control_values=submission.workflow_control_values)
    assert retried.graph["sampling"]["inputs"]["steps"] == 12
    assert retried.execution_fingerprint == prepared.execution_fingerprint
    store.update_render_job(p, g, submission.id, RendererJobState(status="failed"))
    retry = retry_generation(p, g, submission.id, store=store, config=config, transport=httpx.MockTransport(handle))
    assert retry.retry_of_submission_id == submission.id
    assert calls[-1]["prompt"]["sampling"]["inputs"]["steps"] == 12
    store.set_workflow_control_values(p, g, f, "generation", {})
    assert prepare_workflow(store, p, g, profile, inspection=False).graph["sampling"]["inputs"]["steps"] == 8
    store.set_workflow_control_values(p, g, f, "project", {})
    assert prepare_workflow(store, p, g, profile, inspection=False).graph["sampling"]["inputs"]["steps"] == 20


@pytest.mark.parametrize("change", ["added", "removed", "renamed"])
def test_exact_retry_declaration_drift_rejects_cleanly_before_prompt(tmp_path, workspace_factory, change):
    store, config, profile, connection, project = saved(tmp_path, workspace_factory)
    p, g = project.project_id, project.active_generation_id
    calls = []
    def handle(request):
        calls.append(request)
        body = json.loads(request.content)
        return httpx.Response(200, json={"prompt_id": body.get("prompt_id", "upload"), "number": 1, "node_errors": {}})
    body = ExecutionSendRequest(projectId=p, generationId=g, profileId=profile.id, connectionId=connection.id)
    first = send_generation(body, store=store, config=config, transport=httpx.MockTransport(handle))
    source = store.update_render_job(p, g, first.render_submission_id, RendererJobState(status="failed"))
    definitions = [control.model_dump(mode="json", by_alias=True) for control in profile.exposed_controls]
    if change == "added":
        definitions.append(control("second_lora_enabled", "42", "lora_2", False, "boolean", ["on"]))
    elif change == "removed":
        definitions.pop()
    else:
        definitions[-1]["id"] = "renamed_control"
    config.save("profiles", ex.ProfileUpdate(name=profile.name, compatibleModes=list(profile.compatible_modes),
        bindings=profile.bindings, exposedControls=definitions), profile.id)
    with pytest.raises(ex.InvalidBindings, match="Workflow Control declaration changed after this render"):
        retry_generation(p, g, source.id, store=store, config=config, transport=httpx.MockTransport(handle))
    assert len([request for request in calls if request.url.path.endswith("/prompt")]) == 1


def test_stale_values_warn_and_inherit(tmp_path, workspace_factory):
    store, _, profile, _, project = saved(tmp_path, workspace_factory)
    p, g = project.project_id, project.active_generation_id
    store.set_workflow_control_values(p, g, str(profile.id), "project", {"removed": True, "steps": 8})
    store.set_workflow_control_values(p, g, str(profile.id), "generation", {"steps": "bad"})
    prepared = prepare_workflow(store, p, g, profile)
    assert prepared.graph["sampling"]["inputs"]["steps"] == 8
    assert any("removed" in info for info in prepared.information)
    assert not any("removed" in warning for warning in prepared.warnings)
    assert next(d for d in prepared.diagnostics if d.code == "control.removed_default").severity == "info"
    assert next(d for d in prepared.diagnostics if d.code == "control.stale_value").severity == "warning"
    assert any("stale" in warning for warning in prepared.warnings)


def test_control_api_accepts_only_declared_ids_and_valid_values(tmp_path, workspace_factory, monkeypatch):
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("MINICONSTRUCT_EXECUTION_ROOT", str(tmp_path / "execution"))
    store, _, profile, _, project = saved(tmp_path, workspace_factory)
    base = f"/api/execution/projects/{project.project_id}/generations/{project.active_generation_id}/profiles/{profile.id}/controls"
    with TestClient(app) as client:
        assert client.get(base).json()["controls"][5]["value"] == 20
        assert client.put(base + "/project", json={"values": {"steps": 8}}).status_code == 200
        response = client.put(base + "/generation", json={"values": {"steps": 12}})
        assert response.json()["controls"][5]["value"] == 12
        assert client.put(base + "/generation", json={"values": {"unknown": 1}}).status_code == 422
        assert client.put(base + "/generation", json={"values": {"steps": True}}).status_code == 422
        assert client.put(base + "/generation", json={"values": {"steps": 12}, "nodeId": "42"}).status_code == 422
        assert client.delete(base + "/generation").json()["controls"][5]["value"] == 8
        assert client.delete(base + "/project").json()["controls"][5]["value"] == 20


def test_control_edit_after_prepare_rejects_send_before_transport(tmp_path, workspace_factory):
    store, config, profile, connection, project = saved(tmp_path, workspace_factory)
    p, g = project.project_id, project.active_generation_id
    prepared = prepare_workflow(store, p, g, profile)
    store.set_workflow_control_values(p, g, str(profile.id), "generation", {"steps": 12})
    calls = []
    def forbidden(request):
        calls.append(request)
        raise AssertionError("Stale preparation reached ComfyUI")
    body = ExecutionSendRequest(projectId=p, generationId=g, profileId=profile.id,
        connectionId=connection.id, expectedExecutionFingerprint=prepared.execution_fingerprint)
    with pytest.raises(ex.InvalidBindings, match="Prepared execution is stale"):
        send_generation(body, store=store, config=config, transport=httpx.MockTransport(forbidden))
    assert calls == []
    assert prepare_workflow(store, p, g, profile).graph["sampling"]["inputs"]["steps"] == 12


def test_aspect_ratio_semantic_binding_validates_scalar_target_and_ownership(tmp_path):
    graph = {"h3": {"class_type": "SyntheticH3", "inputs": {"prompt": "old", "ratio": "auto", "linked": ["7", 0]}}}
    base = {"name": "Aspect", "compatibleModes": list(ex.H3Mode), "workflowJson": json.dumps(graph),
        "bindings": [{"source": {"kind": "generated_prompt"}, "nodeId": "h3", "inputName": "prompt"},
                     {"source": {"kind": "aspect_ratio"}, "nodeId": "h3", "inputName": "ratio"}]}
    execution_store = ex.ExecutionStore(tmp_path / "execution")
    valid = execution_store.save("profiles", ex.ProfileCreate(**base))
    assert valid.bindings[1].source.kind == "aspect_ratio"
    missing = deepcopy(base); missing["bindings"][1]["inputName"] = "missing"
    with pytest.raises(ex.InvalidBindings, match="target input does not exist"):
        execution_store.save("profiles", ex.ProfileCreate(**missing))
    linked = deepcopy(base); linked["bindings"][1]["inputName"] = "linked"
    with pytest.raises(ex.InvalidBindings, match="graph link cannot be bound"):
        execution_store.save("profiles", ex.ProfileCreate(**linked))
    duplicate = deepcopy(base); duplicate["bindings"].append({"source": {"kind": "duration"}, "nodeId": "h3", "inputName": "ratio"})
    with pytest.raises(ex.InvalidBindings, match="duplicate target"):
        execution_store.save("profiles", ex.ProfileCreate(**duplicate))
    control_collision = deepcopy(base)
    control_collision["exposedControls"] = [control("ratio_control", "h3", "ratio", "auto", "enum", choices=["auto", "16:9"])]
    with pytest.raises(ex.InvalidBindings, match="owned by a semantic binding"):
        execution_store.save("profiles", ex.ProfileCreate(**control_collision))
    numeric = deepcopy(base); graph["h3"]["inputs"]["ratio"] = 1; numeric["workflowJson"] = json.dumps(graph)
    with pytest.raises(ex.InvalidBindings, match="scalar string input"):
        execution_store.save("profiles", ex.ProfileCreate(**numeric))


def test_aspect_ratio_binding_serializes_in_execution_config_v7(tmp_path):
    graph = {"h3": {"class_type": "SyntheticH3", "inputs": {"prompt": "old", "ratio": "auto"}}}
    profile = ex.ProfileCreate(name="Aspect", compatibleModes=list(ex.H3Mode), workflowJson=json.dumps(graph),
        bindings=[{"source": {"kind": "generated_prompt"}, "nodeId": "h3", "inputName": "prompt"},
                  {"source": {"kind": "aspect_ratio"}, "nodeId": "h3", "inputName": "ratio"}])
    assert ex.ExecutionConfig().schema_version == 7
    assert profile.bindings[1].model_dump(mode="json", by_alias=True)["source"] == {"kind": "aspect_ratio", "valueMap": {}}
    store = ex.ExecutionStore(tmp_path / "roundtrip")
    saved = store.save("profiles", profile)
    assert store._read().schema_version == 7
    assert store.get("profiles", saved.id).bindings[1].source.kind == "aspect_ratio"


def test_aspect_ratio_value_map_is_bounded_and_strictly_string_only():
    with pytest.raises(Exception):
        ex.AspectRatioSource(kind="aspect_ratio", valueMap={"16:9": 123})
    with pytest.raises(Exception):
        ex.AspectRatioSource(kind="aspect_ratio", valueMap={"16:9": "x" * 257})
    with pytest.raises(Exception):
        ex.AspectRatioSource(kind="aspect_ratio", valueMap={f"{i}:1": str(i) for i in range(17)})


def test_v6_aspect_binding_migrates_to_identity_value_map():
    config = ex.ExecutionConfig.model_validate({"schemaVersion": 6, "profiles": []})
    assert config.schema_version == 7
