from copy import deepcopy
import json
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import httpx
from pydantic import ValidationError
import pytest

from miniconstruct.app import app
from miniconstruct import execution as ex


@pytest.fixture
def graph():
    return {
        "prompt:alpha": {"class_type": "UnregisteredCustomNode", "inputs": {"text": "original", "duration": 8,
            "picture": "p.png", "audio": "a.wav", "video": "v.mp4", "link": ["42", 0], "custom": {"nested": [True, None]}},
            "_meta": {"title": "<img src=x onerror=alert(1)>", "unknown": "preserved"}, "unknown": [1, 2]},
        "42": {"class_type": "Anything", "inputs": {"flag": True}},
    }


def binding(kind="generated_prompt", target="text", **source):
    return {"source": {"kind": kind, **source}, "nodeId": "prompt:alpha", "inputName": target,
            **({"presence": "optional", "controlledConsumers": []} if kind == "reference_asset" else {})}


def profile_body(graph, **overrides):
    return {"name": "Ref2VA", "description": "Reusable", "compatibleModes": ["Ref2VA"],
            "workflowJson": json.dumps(graph), "bindings": [binding()], **overrides}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MINICONSTRUCT_EXECUTION_ROOT", str(tmp_path / "execution"))
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(tmp_path / "projects"))
    # Any accidental real transport, including background startup, fails offline.
    def forbidden(*args, **kwargs):
        raise AssertionError("Live HTTP transport is forbidden")
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    with TestClient(app) as client:
        yield client


def test_connection_crud_reload_and_independence(client, tmp_path):
    route = "/api/execution/connections"
    assert client.get(route).json() == []
    created = client.post(route, json={"name": " Local ", "baseUrl": "http://127.0.0.1:9090///"})
    assert created.status_code == 201
    saved = created.json()
    assert UUID(saved["id"]).version == 4
    assert saved["name"] == "Local" and saved["baseUrl"] == "http://127.0.0.1:9090"
    url = f"{route}/{saved['id']}"
    assert client.get(url).json() == saved
    assert client.get(route).json() == [saved]
    assert ex.ExecutionStore().get("connections", UUID(saved["id"])).name == "Local"
    updated = client.put(url, json={"name": "Remote", "baseUrl": "https://machine.tailnet.ts.net/comfy/"})
    assert updated.status_code == 200 and updated.json()["id"] == saved["id"]
    # Duplicate display names are explicitly allowed, IDs remain authoritative.
    second = client.post(route, json={"name": "Remote", "baseUrl": "http://localhost"}).json()
    assert second["id"] != saved["id"]
    assert client.delete(url).status_code == 204
    assert client.get(url).status_code == 404
    assert client.delete(url).status_code == 404
    assert client.put(url, json={"name": "x", "baseUrl": "http://localhost"}).status_code == 404
    assert client.post(url + "/test").status_code == 404
    assert not (tmp_path / "projects").exists()
    assert (tmp_path / "execution" / "execution.json").is_file()


def test_local_input_configuration_persists_and_checks_without_http(client, tmp_path):
    root = tmp_path / "input"
    root.mkdir()
    route = "/api/execution/connections"
    saved = client.post(route, json={"name": "Mapped", "baseUrl": "http://never.test",
        "localInputDirectory": str(root)}).json()
    url = f"{route}/{saved['id']}"
    assert client.get(url).json()["localInputDirectory"] == str(root)
    assert client.get(url + "/local-input-status").json()["status"] == "local"
    root.rmdir()
    assert client.get(url + "/local-input-status").json()["status"] == "warning"
    assert client.put(url, json={"name": "Remote", "baseUrl": "http://never.test"}).json()["localInputDirectory"] is None
    assert client.get(url + "/local-input-status").json()["status"] == "http"
    for invalid in ["relative/input", "C:relative", "bad\u0000path"]:
        assert client.put(url, json={"name": "Bad", "baseUrl": "http://never.test",
            "localInputDirectory": invalid}).status_code == 422


@pytest.mark.parametrize("base_url", ["http://127.0.0.1:8188", "http://localhost:8188"])
def test_connection_api_accepts_normal_local_comfyui_urls(client, base_url):
    response = client.post("/api/execution/connections", json={"name": "Local ComfyUI", "baseUrl": base_url})
    assert response.status_code == 201, response.text
    assert response.json()["name"] == "Local ComfyUI"
    assert response.json()["baseUrl"] == base_url


def test_profile_api_persists_native_source_links_across_fresh_reads(client, graph, tmp_path):
    graph = deepcopy(graph)
    graph["native"] = {"class_type": "Native", "inputs": {
        "enabled": False, "save": "", "load": "", "previous": ["source", 0],
    }}
    graph["source"] = {"class_type": "Source", "inputs": {}}
    native = {
        "kind": "native_h3_av", "version": 1, "compatibilityKey": "generic-v1",
        "contextFrames": 1, "contextGrid": {"minimum": 1, "step": 1, "maximum": 4},
        "inputs": {
            "enable": {"nodeId": "native", "inputName": "enabled"},
            "save_key": {"nodeId": "native", "inputName": "save"},
            "load_key": {"nodeId": "native", "inputName": "load"},
        },
        "sourceLinks": [{"nodeId": "native", "inputName": "previous",
                         "expectedSourceNodeId": "source", "expectedOutputIndex": 0}],
    }
    original_native = {**native, "sourceLinks": []}
    created = client.post("/api/execution/profiles", json=profile_body(graph, nativeContinuation=original_native))
    assert created.status_code == 201, created.text
    profile_id = created.json()["id"]
    assert created.json()["nativeContinuation"]["sourceLinks"] == []
    updated = client.put(f"/api/execution/profiles/{profile_id}", json={
        "name": "Ref2VA", "description": "Updated", "compatibleModes": ["Ref2VA"],
        "bindings": [binding()], "nativeContinuation": native,
    })
    assert updated.status_code == 200, updated.text
    assert updated.json()["nativeContinuation"]["sourceLinks"] == native["sourceLinks"]
    payload = json.loads((tmp_path / "execution" / "execution.json").read_bytes())
    persisted = next(p for p in payload["profiles"] if p["id"] == profile_id)
    assert persisted["nativeContinuation"]["sourceLinks"] == native["sourceLinks"]
    fresh_store = ex.ExecutionStore()
    assert fresh_store.get("profiles", UUID(profile_id)).model_dump(by_alias=True)["nativeContinuation"]["sourceLinks"] == native["sourceLinks"]
    with TestClient(app) as fresh_client:
        assert fresh_client.get(f"/api/execution/profiles/{profile_id}").json() == updated.json()
        summary = fresh_client.get("/api/execution/profiles").json()[0]
        assert "workflow" not in summary and "bindings" not in summary
        assert summary["nativeContinuation"]["sourceLinks"] == native["sourceLinks"]
    assert client.get(f"/api/execution/profiles/{profile_id}").json()["nativeContinuation"]["sourceLinks"] == native["sourceLinks"]


@pytest.mark.parametrize("url,expected", [
    ("http://localhost/", "http://localhost"), ("http://192.168.1.20:1234///", "http://192.168.1.20:1234"),
    (" https://comfy.example.net/prefix/ ", "https://comfy.example.net/prefix"),
    ("http://100.64.0.2:8000", "http://100.64.0.2:8000"), ("http://[::1]:8188/", "http://[::1]:8188"),
])
def test_url_normalization(url, expected):
    assert ex.ConnectionWrite(name="test", baseUrl=url).base_url == expected


@pytest.mark.parametrize("url", ["localhost:8188", "ftp://host", "http://", "http://user:pass@host", "http://user@host",
    "http://host?token=secret", "http://host/#fragment", "http://host:0", "http://host:65536", "http://host:no",
    "http://host:", "http://bad host", "http://host\\evil", "http://host/\n", "http://host/%ZZ", "http://-host", "http://host..com"])
def test_invalid_urls(url):
    # Exterior whitespace is intentionally harmless; an internal newline is not.
    if url == "http://host/\n":
        url = "http://ho\nst/path"
    with pytest.raises(ValidationError):
        ex.ConnectionWrite(name="test", baseUrl=url)


def test_atomic_failures_preserve_disk_and_reload(tmp_path, monkeypatch, graph):
    store = ex.ExecutionStore(tmp_path)
    connection = store.save("connections", ex.ConnectionWrite(name="original", baseUrl="http://localhost"))
    profile = store.save("profiles", ex.ProfileCreate(**profile_body(graph)))
    original = store.path.read_bytes()
    def fail(*args):
        raise OSError("private filesystem details")
    for operation in ("replace", "fsync"):
        with monkeypatch.context() as patch:
            patch.setattr(ex.os, operation, fail)
            for action in (
                lambda: store.save("connections", ex.ConnectionWrite(name="changed", baseUrl="http://host"), connection.id),
                lambda: store.delete("connections", connection.id),
                lambda: store.save("profiles", ex.ProfileCreate(**profile_body(graph, name="changed")), profile.id),
                lambda: store.delete("profiles", profile.id),
            ):
                with pytest.raises(ex.ExecutionError, match="previous configuration was preserved"):
                    action()
                assert store.path.read_bytes() == original
                assert ex.ExecutionStore(tmp_path).get("connections", connection.id).name == "original"
                assert not list(tmp_path.glob("*.tmp"))


def test_multiple_store_instances_reload_and_detached_graph(tmp_path, graph):
    first, second = ex.ExecutionStore(tmp_path), ex.ExecutionStore(tmp_path)
    created = first.save("profiles", ex.ProfileCreate(**profile_body(graph)))
    created.workflow["42"]["inputs"]["flag"] = False
    second.save("connections", ex.ConnectionWrite(name="second", baseUrl="http://host"))
    assert first.get("profiles", created.id).workflow == graph
    assert len(first.list("connections")) == 1
    content = json.loads(first.path.read_text())
    content["profiles"][0]["workflow"]["42"]["inputs"]["flag"] = False
    first.path.write_text(json.dumps(content))
    with pytest.raises(ex.ExecutionError, match="could not be read"):
        first.delete("connections", first.list("connections")[0].id)
    assert json.loads(first.path.read_text()) == content


@pytest.mark.parametrize("outcome,expected", [("success", "connected"), ("refused", "unreachable"), ("timeout", "timeout"),
    ("malformed", "invalid_response"), ("shape", "invalid_response"), ("oversized", "invalid_response"), ("redirect", "invalid_response"), ("http_error", "invalid_response")])
async def test_connection_fake_transport_only(outcome, expected):
    calls = []
    def handle(request):
        calls.append((request.method, str(request.url)))
        if outcome == "refused": raise httpx.ConnectError("private internal response", request=request)
        if outcome == "timeout": raise httpx.ReadTimeout("private internal response", request=request)
        if outcome == "malformed": return httpx.Response(200, text="not JSON")
        if outcome == "shape": return httpx.Response(200, json={"system": {}, "devices": []})
        if outcome == "oversized": return httpx.Response(200, content=b" " * (ex.MAX_STATS_BYTES + 1))
        if outcome == "redirect": return httpx.Response(302, headers={"Location": "http://elsewhere/prompt"})
        if outcome == "http_error": return httpx.Response(500, text="private internal response")
        return httpx.Response(200, json={"system": {"os": "linux", "argv": ["private arguments"]}, "devices": []})
    result = await ex.test_connection(ex.Connection(name="test", baseUrl="http://fake.test:9999/comfy"), transport=httpx.MockTransport(handle))
    assert result["status"] == expected
    assert "private" not in str(result)
    assert calls == [("GET", "http://fake.test:9999/comfy/system_stats")]
    assert len(str(result)) < 250


async def test_connection_has_total_deadline(monkeypatch):
    import asyncio
    monkeypatch.setattr(ex, "TEST_TIMEOUT_SECONDS", 0.01)
    async def slow(request):
        await asyncio.sleep(1)
    result = await ex.test_connection(ex.Connection(name="test", baseUrl="http://fake.test"), transport=httpx.MockTransport(slow))
    assert result["status"] == "timeout"


def test_connection_route_uses_saved_destination(client, monkeypatch):
    import miniconstruct.api.execution_routes as routes
    async def fake(connection):
        return await ex.test_connection(connection, transport=httpx.MockTransport(lambda req:
            httpx.Response(200, json={"system": {"os": "nt"}, "devices": []})))
    monkeypatch.setattr(routes, "test_connection", fake)
    saved = client.post("/api/execution/connections", json={"name": "test", "baseUrl": "http://fake.test"}).json()
    assert client.post(f"/api/execution/connections/{saved['id']}/test").json()["status"] == "connected"


def test_profile_crud_preservation_hash_and_replacement(client, graph):
    base = "/api/execution/profiles"
    created = client.post(base, json=profile_body(graph))
    assert created.status_code == 201, created.text
    saved = created.json(); url = f"{base}/{saved['id']}"
    assert UUID(saved["id"]).version == 4
    assert saved["workflow"] == graph
    assert saved["fingerprint"] == ex.workflow_fingerprint(graph)
    assert client.get(url).json() == saved
    assert "workflow" not in client.get(base).json()[0]
    assert client.post(url + "/validate").json()["valid"] is True
    metadata = {key: saved[key] for key in ("name", "description", "compatibleModes", "bindings")}
    metadata["name"] = "renamed"
    assert client.put(url, json=metadata).json()["name"] == "renamed"
    changed = deepcopy(graph); changed["42"]["inputs"]["flag"] = False
    replaced = client.put(url + "/workflow", json={"workflowJson": json.dumps(changed)})
    assert replaced.status_code == 200
    assert replaced.json()["fingerprint"] != saved["fingerprint"]
    stale = deepcopy(changed); del stale["prompt:alpha"]["inputs"]["text"]
    rejected = client.put(url + "/workflow", json={"workflowJson": json.dumps(stale)})
    assert rejected.status_code == 422 and "target input does not exist" in rejected.text
    assert client.get(url).json()["workflow"] == changed
    assert client.get(url).json()["fingerprint"] == replaced.json()["fingerprint"]
    # Atomically fix draft mappings and replace the graph using PUT profile.
    metadata.update(workflowJson=json.dumps(stale), bindings=[])
    assert client.put(url, json=metadata).status_code == 200
    assert client.get(url).json()["workflow"] == stale
    assert client.delete(url).status_code == 204
    for method, suffix, body in [("get", "", None), ("delete", "", None), ("post", "/validate", None),
                                  ("put", "", metadata), ("put", "/workflow", {"workflowJson": json.dumps(graph)})]:
        assert client.request(method, url + suffix, json=body).status_code == 404


def test_fingerprint_formatting_and_semantics(graph):
    pretty = json.dumps(graph, indent=4, ensure_ascii=True)
    reordered = {key: dict(reversed(list(value.items()))) for key, value in reversed(list(graph.items()))}
    assert ex.workflow_fingerprint(ex.parse_workflow(pretty)) == ex.workflow_fingerprint(reordered)
    changed = deepcopy(graph); changed["prompt:alpha"]["inputs"]["text"] += "!"
    assert ex.workflow_fingerprint(changed) != ex.workflow_fingerprint(graph)


def test_metadata_update_preserves_large_custom_integers_and_validates_draft(client, graph):
    graph["42"]["inputs"]["custom_seed"] = 18446744073709551617
    saved = client.post("/api/execution/profiles", json=profile_body(graph)).json()
    url = f"/api/execution/profiles/{saved['id']}"
    metadata = {"name": "Renamed", "description": "", "compatibleModes": ["Ref2VA"], "bindings": [binding()]}
    updated = client.put(url, json=metadata)
    assert updated.status_code == 200, updated.text
    assert updated.json()["workflow"] == graph
    assert updated.json()["fingerprint"] == saved["fingerprint"]
    assert client.get(url).json()["workflow"] == graph
    metadata["bindings"][0]["nodeId"] = "missing"
    report = client.post(url + "/validate", json=metadata)
    assert report.status_code == 200 and report.json()["valid"] is False
    assert client.get(url).json()["bindings"] == [binding()]


@pytest.mark.parametrize("raw,message", [
    ("{", "Invalid workflow JSON"), ("[]", "API-format object"), ("null", "API-format object"), ("{}", "non-empty"),
    ('{"nodes":[],"links":[]}', "editor workflow"), ('{"1":{}}', "class_type"),
    ('{"1":{"class_type":"Custom","inputs":[]}}', "inputs object"), ('{"1":4}', "node must be an object"),
    ('{"1":{"class_type":"X","inputs":{},"x":NaN}}', "finite JSON numbers"),
    ('{"1":{"class_type":"X","inputs":{},"x":1e999}}', "JSON compliant"),
    ('{"1":{"class_type":"X","inputs":{},"inputs":{}}}', "Duplicate JSON"),
])
def test_invalid_import(raw, message):
    with pytest.raises(ValueError, match=message):
        ex.parse_workflow(raw)


def test_size_and_depth_bounds(client, graph):
    with pytest.raises(ValueError, match="2 MiB"):
        ex.parse_workflow(" " * (ex.MAX_WORKFLOW_BYTES + 1))
    oversized = deepcopy(graph); oversized["42"]["inputs"]["text"] = "é" * ex.MAX_WORKFLOW_BYTES
    response = client.post("/api/execution/profiles", json=profile_body(oversized))
    assert response.status_code in (413, 422)
    assert len(response.content) < 2000  # error never echoes enormous workflow
    response = client.post("/api/execution/profiles", content=b" " * (8 * 1024 * 1024 + 1), headers={"Content-Type": "application/json"})
    assert response.status_code == 413
    with pytest.raises(ValueError):
        ex.parse_workflow('{"1":{"class_type":"X","inputs":{"x":' + "[" * 2000 + "0" + "]" * 2000 + "}}}")


def test_valid_all_semantic_sources_and_canonical_enums(client, graph):
    bindings = [binding(), binding("duration", "duration"), binding("reference_asset", "picture", assetKind="image", ordinal=1),
                binding("reference_asset", "audio", assetKind="audio", ordinal=2), binding("reference_asset", "video", assetKind="video", ordinal=1)]
    result = client.post("/api/execution/profiles", json=profile_body(graph, bindings=bindings, compatibleModes=list(ex.H3Mode)))
    assert result.status_code == 201, result.text
    assert result.json()["bindings"] == bindings
    assert client.get("/api/execution/capabilities").json()["compatibleModes"] == list(ex.H3Mode)


@pytest.mark.parametrize("change,expected", [
    ({"nodeId": "missing"}, "target node does not exist"), ({"inputName": "missing"}, "target input does not exist"),
    ({"inputName": "link"}, "graph link"), ({"inputName": "custom"}, "structured input"),
])
def test_binding_target_errors(client, graph, change, expected):
    body = profile_body(graph, bindings=[{**binding(), **change}])
    report = client.post("/api/execution/profiles/validate", json=body)
    assert report.status_code == 200 and report.json()["valid"] is False
    assert expected in str(report.json()["errors"])
    assert client.post("/api/execution/profiles", json=body).status_code == 422


def test_duplicate_binding_target(client, graph):
    body = profile_body(graph, bindings=[binding(), binding("duration")])
    assert "duplicate target" in client.post("/api/execution/profiles/validate", json=body).text


@pytest.mark.parametrize("source", [{"kind": "creative_request"}, {"kind": "render_seed"},
    *[{"kind": "reference_asset", "assetKind": "image", "ordinal": value} for value in (0, -1, 1.5, True, "1")],
    {"kind": "reference_asset", "assetKind": "Picture", "ordinal": 1},
    {"kind": "generated_prompt", "assetId": "project-specific"}])
def test_invalid_semantic_sources(client, graph, source):
    body = profile_body(graph, bindings=[{**binding(), "source": source}])
    assert client.post("/api/execution/profiles", json=body).status_code == 422


@pytest.mark.parametrize("modes", [["REF2VA"], [], ["Ref2VA", "Ref2VA"], ["invented"]])
def test_invalid_modes(client, graph, modes):
    assert client.post("/api/execution/profiles", json=profile_body(graph, compatibleModes=modes)).status_code == 422


def test_strict_requests_no_paths_or_secrets(client, graph):
    assert client.post("/api/execution/connections", json={"name": "x", "baseUrl": "http://host", "apiKey": "secret"}).status_code == 422
    assert client.post("/api/execution/profiles", json=profile_body(graph, path="../project.json")).status_code == 422
    assert client.post("/api/execution/profiles", json=profile_body(graph, name=" ")).status_code == 422
    missing = str(uuid4())
    assert client.get(f"/api/execution/profiles/{missing}").status_code == 404
