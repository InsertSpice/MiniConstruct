"""Offline legacy Profile compatibility; no persisted migration or guessed node semantics."""
import json
from copy import deepcopy

import httpx
import pytest

from miniconstruct import execution as ex
from miniconstruct.legacy_aspect_ratio import legacy_aspect_ratio_control, legacy_renderer_value
from miniconstruct.project_store import ProjectStore
from miniconstruct.workflow_preparation import prepare_workflow
from miniconstruct.execution_send import ExecutionSendRequest, send_generation
from test_workflow_preparation import capacity_profile, select

CHOICES = ["4:3 (Standard)", "1:1 (Square)", "2:3 (Portrait Photo)", "3:2 (Photo)",
           "3:4 (Portrait Standard)", "9:16 (Portrait Widescreen)", "16:9 (Widescreen)", "21:9 (Ultrawide)"]
MAPPINGS = {"4:3": "4:3 (Standard)", "9:16": "9:16 (Portrait Widescreen)",
            "16:9": "16:9 (Widescreen)", "1:1": "1:1 (Square)", "3:4": "3:4 (Portrait Standard)", "3:2": "3:2 (Photo)"}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Live HTTP is forbidden")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)


@pytest.fixture
def store(tmp_path):
    return ProjectStore(tmp_path / "projects")


def fixture_profile(tmp_path, *, choices=None, control_id="aspect_ratio", input_name="aspect_ratio", modern=False):
    draft = capacity_profile(0)
    graph = json.loads(draft.workflow_json)
    graph["115"] = {"class_type": "ResolutionSelector", "inputs": {input_name: "4:3 (Standard)", "megapixels": 0.6}}
    declared = CHOICES if choices is None else choices
    controls = [ex.ExposedControl.model_validate({"id": control_id, "label": "Aspect Ratio",
        "target": {"nodeId": "115", "inputName": input_name}, "type": "enum",
        "defaultValue": "4:3 (Standard)", "choices": declared}),
        ex.ExposedControl.model_validate({"id": "megapixels", "label": "Megapixels", "type": "float",
            "target": {"nodeId": "115", "inputName": "megapixels"}, "defaultValue": 0.6})]
    bindings = list(draft.bindings)
    if modern:
        graph["modern"] = {"class_type": "Other", "inputs": {"ratio": "old"}}
        bindings.append(ex.Binding.model_validate({"source": {"kind": "aspect_ratio", "valueMap": MAPPINGS},
                                                   "nodeId": "modern", "inputName": "ratio"}))
    config = ex.ExecutionStore(tmp_path / "execution")
    profile = config.save("profiles", draft.model_copy(update={"workflow_json": json.dumps(graph),
                                                              "bindings": bindings, "exposed_controls": controls}))
    return config, profile


def queue_transport(sent):
    def queue(request):
        assert request.url.path == "/prompt"
        body = json.loads(request.content); sent.append(body)
        return httpx.Response(200, json={"prompt_id": body["prompt_id"], "number": 1, "node_errors": {}})
    return httpx.MockTransport(queue)


def send(store, config, project, profile, fingerprint, sent):
    connection = config.save("connections", ex.ConnectionWrite(name="Offline", baseUrl="http://never.test"))
    return send_generation(ExecutionSendRequest(projectId=project.project_id, generationId=project.active_generation_id,
        profileId=profile.id, connectionId=connection.id, expectedExecutionFingerprint=fingerprint),
        store=store, config=config, transport=queue_transport(sent))


@pytest.mark.parametrize("ratio,expected", MAPPINGS.items())
@pytest.mark.parametrize("input_name", ["aspect_ratio", "aspectRatio"])
def test_legacy_declared_enum_projects_current_setup_without_persisting_control_selection(
        store, tmp_path, workspace_factory, ratio, expected, input_name):
    config, profile = fixture_profile(tmp_path, input_name=input_name)
    project = store.create_project(workspace_factory(aspectRatio=ratio)); select(store, project)
    store.set_workflow_control_values(project.project_id, project.active_generation_id, str(profile.id), "project",
                                     {"aspect_ratio": "16:9 (Widescreen)", "megapixels": 0.8})
    store.set_workflow_control_values(project.project_id, project.active_generation_id, str(profile.id), "generation",
                                     {"aspect_ratio": "4:3 (Standard)"})
    values_before = deepcopy(store.workflow_control_values(project.project_id, project.active_generation_id, str(profile.id)))
    profile_before = config.path.read_bytes()
    result = prepare_workflow(store, project.project_id, project.active_generation_id, profile)
    assert result.graph["115"]["inputs"][input_name] == expected
    assert result.graph["115"]["inputs"]["megapixels"] == 0.8
    inspected = next(item for item in result.resolutions if item.node_id == "115")
    assert (inspected.source_value, inspected.renderer_value) == (ratio, expected)
    assert next(item for item in result.workflow_controls if item["id"] == "aspect_ratio") == {
        "id": "aspect_ratio", "label": "Aspect Ratio", "value": expected, "source": "h3_setup_compatibility"}
    assert any("is driven by H3 Setup" in info for info in result.information)
    assert not any("Replace this control" in warning for warning in result.warnings)
    assert config.path.read_bytes() == profile_before
    sent = []; send(store, config, project, profile, result.execution_fingerprint, sent)
    assert sent[0]["prompt"]["115"]["inputs"][input_name] == expected
    assert store.workflow_control_values(project.project_id, project.active_generation_id, str(profile.id)) == values_before
    # Creating the Connection changed config, but reading/compiling never normalized the saved Profile.
    persisted = ex.ExecutionStore(config.path.parent).get("profiles", profile.id)
    assert persisted.model_dump() == profile.model_dump()


@pytest.mark.parametrize("choices", [
    [choice for choice in CHOICES if not choice.startswith("9:16")],
    [*CHOICES, "9:16 (Second Portrait)"],
])
def test_missing_or_ambiguous_choice_cannot_prepare_or_queue(store, tmp_path, workspace_factory, choices):
    config, profile = fixture_profile(tmp_path, choices=choices)
    project = store.create_project(workspace_factory(aspectRatio="9:16")); select(store, project)
    sent = []
    with pytest.raises(ex.InvalidBindings, match="legacy Aspect Ratio.*(missing|ambiguous)"):
        prepare_workflow(store, project.project_id, project.active_generation_id, profile)
    with pytest.raises(ex.InvalidBindings, match="legacy Aspect Ratio.*(missing|ambiguous)"):
        send(store, config, project, profile, None, sent)
    assert not sent


@pytest.mark.parametrize("option", ["19:16 (Wrong)", "some 9:16 mode", "Portrait Widescreen", "9:160 (Wrong)", "9:16x", "9:16.0"])
def test_parser_does_not_guess_ratio_substrings(tmp_path, option):
    _, profile = fixture_profile(tmp_path, choices=["4:3 (Standard)", option])
    with pytest.raises(ex.InvalidBindings, match="missing"):
        legacy_renderer_value(legacy_aspect_ratio_control(profile), "9:16")


@pytest.mark.parametrize("ratio,choice", [("9:16", "9:16"), ("9:16", "9:16(Portrait)"),
                                         ("1:1", "1:1 (Square)"), ("auto", "auto"), ("2.39:1", "2.39:1")])
def test_parser_accepts_only_one_explicit_matching_choice(tmp_path, ratio, choice):
    _, profile = fixture_profile(tmp_path, choices=["4:3 (Standard)", choice])
    assert legacy_renderer_value(legacy_aspect_ratio_control(profile), ratio) == choice


@pytest.mark.parametrize("ratio", ["auto", "2.39:1", "1:1", "2:1", "5:4"])
def test_legacy_enum_never_receives_undeclared_automatic_or_custom_values(tmp_path, ratio):
    _, profile = fixture_profile(tmp_path, choices=["4:3 (Standard)", "11:1 (Wrong)"])
    with pytest.raises(ex.InvalidBindings, match="missing"):
        legacy_renderer_value(legacy_aspect_ratio_control(profile), ratio)


def test_similar_arbitrary_control_is_not_promoted(store, tmp_path, workspace_factory):
    _, profile = fixture_profile(tmp_path, control_id="ratio")
    assert legacy_aspect_ratio_control(profile) is None
    project = store.create_project(workspace_factory(aspectRatio="9:16")); select(store, project)
    with pytest.raises(ex.InvalidBindings, match="no safe authority"):
        prepare_workflow(store, project.project_id, project.active_generation_id, profile)


def test_resolution_node_without_explicit_metadata_is_not_inferred(store, tmp_path, workspace_factory):
    config, profile = fixture_profile(tmp_path)
    profile = config.save("profiles", ex.ProfileUpdate(name=profile.name, compatibleModes=profile.compatible_modes,
        bindings=profile.bindings, exposedControls=[]), profile.id)
    assert legacy_aspect_ratio_control(profile) is None
    project = store.create_project(workspace_factory(aspectRatio="9:16")); select(store, project)
    result = prepare_workflow(store, project.project_id, project.active_generation_id, profile)
    assert result.graph["115"]["inputs"]["aspect_ratio"] == "4:3 (Standard)"
    assert any("does not control renderer dimensions" in warning for warning in result.warnings)


def test_modern_binding_wins_and_legacy_looking_arbitrary_control_is_not_projected(store, tmp_path, workspace_factory):
    _, profile = fixture_profile(tmp_path, control_id="ratio", modern=True)
    assert legacy_aspect_ratio_control(profile) is None
    project = store.create_project(workspace_factory(aspectRatio="9:16")); select(store, project)
    result = prepare_workflow(store, project.project_id, project.active_generation_id, profile)
    assert result.graph["modern"]["inputs"]["ratio"] == MAPPINGS["9:16"]
    assert not any(d.code == "aspect_ratio.legacy_authority" for d in result.diagnostics)
    assert result.graph["115"]["inputs"]["aspect_ratio"] == "4:3 (Standard)"


def test_explicit_modern_and_legacy_targets_cannot_silently_compete(tmp_path):
    _, profile = fixture_profile(tmp_path, modern=True)
    with pytest.raises(ex.InvalidBindings, match="target different inputs"):
        legacy_aspect_ratio_control(profile)


def test_existing_same_target_collision_validation_still_rejects(tmp_path):
    config, profile = fixture_profile(tmp_path)
    binding = ex.Binding.model_validate({"source": {"kind": "aspect_ratio", "valueMap": MAPPINGS},
                                        "nodeId": "115", "inputName": "aspect_ratio"})
    with pytest.raises(ex.InvalidBindings, match="owned by a semantic binding"):
        config.save("profiles", ex.ProfileUpdate(name=profile.name, compatibleModes=profile.compatible_modes,
            bindings=[*profile.bindings, binding], exposedControls=profile.exposed_controls), profile.id)


def test_legacy_ratio_only_change_rejects_stale_expected_fingerprint(store, tmp_path, workspace_factory):
    config, profile = fixture_profile(tmp_path)
    project = store.create_project(workspace_factory(aspectRatio="4:3", durationSeconds=8)); select(store, project)
    original = prepare_workflow(store, project.project_id, project.active_generation_id, profile)
    store.sync_execution_setup(project.project_id, project.active_generation_id, 8, project.project_fingerprint, "9:16")
    sent = []
    with pytest.raises(ex.InvalidBindings, match="stale"):
        send(store, config, project, profile, original.execution_fingerprint, sent)
    assert not sent
    current = prepare_workflow(store, project.project_id, project.active_generation_id, profile)
    send(store, config, project, profile, current.execution_fingerprint, sent)
    assert sent[0]["prompt"]["115"]["inputs"]["aspect_ratio"] == MAPPINGS["9:16"]


def test_custom_32_projects_to_the_single_declared_photo_option(store, tmp_path, workspace_factory):
    config, profile = fixture_profile(tmp_path)
    project = store.create_project(workspace_factory(aspectRatio="3:2")); select(store, project)
    result = prepare_workflow(store, project.project_id, project.active_generation_id, profile)
    assert result.graph["115"]["inputs"]["aspect_ratio"] == "3:2 (Photo)"
    sent = []; send(store, config, project, profile, result.execution_fingerprint, sent)
    assert sent[0]["prompt"]["115"]["inputs"]["aspect_ratio"] == "3:2 (Photo)"


def test_ambiguous_32_options_reject(store, tmp_path, workspace_factory):
    config, profile = fixture_profile(tmp_path, choices=[*CHOICES, "3:2 Photo Variant"])
    project = store.create_project(workspace_factory(aspectRatio="3:2")); select(store, project)
    sent = []
    with pytest.raises(ex.InvalidBindings, match="ambiguous"):
        prepare_workflow(store, project.project_id, project.active_generation_id, profile)
    with pytest.raises(ex.InvalidBindings, match="ambiguous"):
        send(store, config, project, profile, None, sent)
    assert not sent


def test_control_status_marks_only_the_recognized_legacy_editor_as_h3_controlled(store, tmp_path, workspace_factory, monkeypatch):
    from miniconstruct.api import execution_routes
    config, profile = fixture_profile(tmp_path)
    project = store.create_project(workspace_factory(aspectRatio="9:16"))
    monkeypatch.setattr(execution_routes, "ProjectStore", lambda: store)
    monkeypatch.setattr(execution_routes, "ExecutionStore", lambda: config)
    before = config.path.read_bytes()
    status = execution_routes.control_status(project.project_id, project.active_generation_id, profile.id)
    assert next(item for item in status["controls"] if item["id"] == "aspect_ratio")["h3SetupControlled"] is True
    assert next(item for item in status["controls"] if item["id"] == "megapixels")["h3SetupControlled"] is False
    assert config.path.read_bytes() == before


@pytest.mark.parametrize("mutation", ["wrong_type", "wrong_input", "numeric_enum", "missing_target", "graph_link", "duplicate_id", "binding_conflict", "control_conflict"])
def test_unsafe_canonical_control_metadata_fails_closed(tmp_path, mutation):
    _, profile = fixture_profile(tmp_path)
    control = profile.exposed_controls[0]
    graph = deepcopy(profile.workflow)
    controls = list(profile.exposed_controls)
    bindings = list(profile.bindings)
    if mutation == "wrong_type":
        controls[0] = control.model_copy(update={"type": "boolean"})
    elif mutation == "wrong_input":
        controls[0] = control.model_copy(update={"target": control.target.model_copy(update={"input_name": "ratio"})})
    elif mutation == "numeric_enum":
        controls[0] = control.model_copy(update={"choices": [1, 2]})
    elif mutation == "missing_target":
        graph.pop("115")
    elif mutation == "graph_link":
        graph["115"]["inputs"]["aspect_ratio"] = ["h3", 0]
    elif mutation == "duplicate_id":
        controls.append(control)
    elif mutation == "binding_conflict":
        bindings.append(ex.Binding.model_validate({"source": {"kind": "generated_prompt"}, "nodeId": "115", "inputName": "aspect_ratio"}))
    else:
        controls.append(control.model_copy(update={"id": "other"}))
    unsafe = profile.model_copy(update={"workflow": graph, "exposed_controls": controls, "bindings": bindings})
    with pytest.raises(ex.InvalidBindings):
        legacy_aspect_ratio_control(unsafe)
