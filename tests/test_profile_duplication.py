"""Independent Profile copies through production draft, validation and save routes."""
from copy import deepcopy
import json
from uuid import UUID, uuid4

import httpx

from miniconstruct import execution as ex
from miniconstruct.execution_send import ExecutionSendRequest, send_generation
from miniconstruct.execution_state import RendererSeedSettings
from miniconstruct.project_store import ProjectStore
from miniconstruct.workflow_preparation import prepare_workflow
from test_execution import client, graph, profile_body, binding
from test_workflow_preparation import select


ROUTE = "/api/execution/profiles"


def rich_body(graph):
    graph = deepcopy(graph)
    graph["prompt:alpha"]["inputs"].update(ratio="wide", seed=9007199254740993)
    graph["native"] = {"class_type": "Native", "inputs": {"enabled": False, "save": "", "load": ""}}
    graph["image"] = {"class_type": "LoadImage", "inputs": {"image": "p.png"}}
    graph["prompt:alpha"]["inputs"]["picture"] = ["image", 0]
    return profile_body(graph, compatibleModes=["Ref2VA", "T2VA"], bindings=[
        binding(), binding("renderer_seed", "seed"),
        binding("aspect_ratio", "ratio", valueMap={"16:9": "wide"}, enumChoices=["wide"]),
        {"source": {"kind": "reference_asset", "assetKind": "image", "ordinal": 1},
         "nodeId": "image", "inputName": "image", "presence": "optional", "routes": [
             {"nodeId": "prompt:alpha", "inputName": "picture", "expectedSourceNodeId": "image",
              "expectedOutputIndex": 0, "channel": "image"}]},
    ], exposedControls=[{"id": "flag", "label": "Flag", "type": "boolean", "defaultValue": True,
                         "target": {"nodeId": "42", "inputName": "flag"}}],
        workflowControlPresets=[{"name": "Off", "values": {"flag": False}}],
        nativeContinuation={"kind": "native_h3_av", "version": 1, "compatibilityKey": "test-v1",
            "contextFrames": 1, "contextGrid": {"minimum": 1, "step": 1, "maximum": 4},
            "inputs": {"enable": {"nodeId": "native", "inputName": "enabled"},
                       "save_key": {"nodeId": "native", "inputName": "save"},
                       "load_key": {"nodeId": "native", "inputName": "load"}}})


def test_duplicate_routes_retain_all_configuration_and_independent_lifecycle(client, graph, tmp_path):
    original = client.post(ROUTE, json=rich_body(graph)).json()
    url = f"{ROUTE}/{original['id']}"
    disk = ex.ExecutionStore().path.read_bytes()
    draft = client.get(url + "/duplicate-draft").json()
    assert ex.ExecutionStore().path.read_bytes() == disk  # cancellation writes nothing
    assert "id" not in draft and "fingerprint" not in draft and "workflow" not in draft
    assert json.loads(draft["workflowJson"]) == original["workflow"]
    assert "9007199254740993" in draft["workflowJson"]
    for key in ex.ProfileEdit.model_fields.values():
        name = key.alias or next(k for k, v in ex.ProfileEdit.model_fields.items() if v is key)
        if name != "name":
            assert draft[name] == original[name]
    assert client.post(ROUTE + "/validate", json=draft).json()["valid"]
    created = client.post(ROUTE, json=draft)
    assert created.status_code == 201, created.text
    duplicate = created.json()
    assert UUID(duplicate["id"]).version == 4 and duplicate["id"] != original["id"]
    assert len(client.get(ROUTE).json()) == 2
    assert client.get(url).json() == original
    assert ex.ExecutionStore().get("profiles", UUID(duplicate["id"])).workflow == original["workflow"]
    assert client.get(url + "/duplicate-draft").json()["name"] == "Ref2VA (Copy 2)"
    # A concurrent same-name save appends a distinct UUID, as existing naming rules allow.
    concurrent = client.post(ROUTE, json=draft).json()
    assert concurrent["id"] not in {original["id"], duplicate["id"]}
    assert client.delete(f"{ROUTE}/{concurrent['id']}").status_code == 204
    changed = deepcopy(draft)
    changed["workflowControlPresets"][0]["values"]["flag"] = True
    changed["description"] = "Duplicate edited"
    assert client.put(f"{ROUTE}/{duplicate['id']}", json=changed).status_code == 200
    assert client.get(url).json() == original
    changed["description"] = "Source edited"
    assert client.put(url, json=changed).status_code == 200
    assert client.get(f"{ROUTE}/{duplicate['id']}").json()["description"] == "Duplicate edited"
    assert client.delete(url).status_code == 204
    assert client.get(f"{ROUTE}/{duplicate['id']}").status_code == 200
    assert client.get(url + "/duplicate-draft").status_code == 404
    assert client.get(f"{ROUTE}/{uuid4()}/duplicate-draft").status_code == 404


def test_duplicate_graph_replacement_keeps_targets_and_diagnoses_missing(client, graph):
    original = client.post(ROUTE, json=rich_body(graph)).json()
    draft = client.get(f"{ROUTE}/{original['id']}/duplicate-draft").json()
    replacement = json.loads(draft["workflowJson"])
    replacement["hyperflow"] = {"class_type": "Hyperflow", "inputs": {"value": True}}
    draft["workflowJson"] = json.dumps(replacement)
    assert client.post(ROUTE + "/validate", json=draft).json()["valid"]
    copy = client.post(ROUTE, json=draft).json()
    assert copy["bindings"] == original["bindings"]
    del replacement["prompt:alpha"]
    draft["workflowJson"] = json.dumps(replacement)
    report = client.post(ROUTE + "/validate", json=draft).json()
    assert not report["valid"] and "prompt:alpha" in " ".join(report["errors"])
    assert client.post(ROUTE, json=draft).status_code == 422
    assert client.get(f"{ROUTE}/{original['id']}").json() == original
    assert len(client.get(ROUTE).json()) == 2


def test_duplicate_prepare_send_and_execution_authority(client, graph, workspace_factory):
    original = client.post(ROUTE, json=rich_body(graph)).json()
    store = ProjectStore()
    project = store.create_project(workspace_factory())
    select(store, project)
    p, g = project.project_id, project.active_generation_id
    store.set_renderer_seed_settings(p, g, RendererSeedSettings(mode="workflow_default"))
    source = ex.Profile.model_validate(original)
    before = prepare_workflow(store, p, g, source, inspection=False)
    calls = []
    def handle(request):
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(200, json={"prompt_id": body["prompt_id"], "number": 1, "node_errors": {}})
    connection = ex.ExecutionStore().save("connections", ex.ConnectionWrite(name="Mock", baseUrl="http://example.test"))
    def send(profile):
        result = send_generation(ExecutionSendRequest(projectId=p, generationId=g, profileId=profile.id,
            connectionId=connection.id), store=store, config=ex.ExecutionStore(), transport=httpx.MockTransport(handle))
        return store.render_submission(p, g, result.render_submission_id)
    first = send(source)
    preference = client.put(f"/api/execution/projects/{p}/generations/{g}/continuation/settings",
                            json={"profileId": str(source.id)})
    assert preference.status_code == 200, preference.text
    store.set_workflow_control_values(p, g, str(source.id), "project", {"flag": False})
    store.set_workflow_control_values(p, g, str(source.id), "generation", {"flag": True})
    snapshot = {str(path): path.read_bytes() for path in store.root.rglob("*") if path.is_file()}
    draft = client.get(f"{ROUTE}/{source.id}/duplicate-draft").json()
    duplicate = ex.Profile.model_validate(client.post(ROUTE, json=draft).json())
    assert {str(path): path.read_bytes() for path in store.root.rglob("*") if path.is_file()} == snapshot
    assert store.workflow_control_values(p, g, str(duplicate.id)) == ({}, {})
    prepared = prepare_workflow(store, p, g, duplicate, inspection=False)
    def reusable(graph):
        graph = deepcopy(graph)
        graph["native"]["inputs"]["save"] = "<execution save key>"
        return graph
    assert reusable(prepared.graph) == reusable(before.graph)
    second = send(duplicate)
    assert second.workflow_profile_id == duplicate.id and first.workflow_profile_id == source.id
    assert reusable(calls[-1]["prompt"]) == reusable(prepared.graph)
    assert calls[-1]["prompt"]["native"]["inputs"]["save"] != calls[0]["prompt"]["native"]["inputs"]["save"]
    assert store.render_submission(p, g, first.id) == first
