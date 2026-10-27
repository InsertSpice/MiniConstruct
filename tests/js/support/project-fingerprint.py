"""Offline real persistence/renderer bridge for mounted browser regressions."""
import asyncio
import json
from pathlib import Path
import sys
import os
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

import httpx
from miniconstruct.execution import ExecutionStore, ConnectionWrite, Binding, ExposedControl
from miniconstruct.execution_send import ExecutionSendRequest, send_generation
from miniconstruct.execution_state import ManualEditAdoptionWrite, SavedPromptWrite, SelectionWrite
from miniconstruct.models.workspace import Workspace, SubjectRecord
from miniconstruct.models.takes import RendererJobState
from miniconstruct.project_store import ProjectStore
from miniconstruct.render_reconciliation import reconcile_submission
from miniconstruct.workflow_preparation import prepare_workflow
from test_workflow_preparation import capacity_profile
from test_validator import VALID_BASE
from test_reference_authority import picture


def forbidden(*args, **kwargs):
    raise AssertionError("Live HTTP is forbidden")


httpx.HTTPTransport.handle_request = forbidden
httpx.AsyncHTTPTransport.handle_async_request = forbidden


with TemporaryDirectory(prefix="mc-fingerprint-") as temporary:
    store = ProjectStore(Path(temporary) / "projects")
    config = ExecutionStore(Path(temporary) / "execution")
    definition = capacity_profile(0)
    graph = json.loads(definition.workflow_json)
    # Complete-enum compatibility fixture. The --stale scenario below uses captured saved declarations.
    graph["ratio"] = {"class_type": "ResolutionSelector", "inputs": {"aspect_ratio": "4:3 (Standard)"}}
    definition = definition.model_copy(update={"workflow_json": json.dumps(graph), "bindings": [*definition.bindings,
        Binding.model_validate({
            "source": {"kind": "aspect_ratio", "valueMap": {"16:9": "16:9 (Widescreen)", "4:3": "4:3 (Standard)", "9:16": "9:16 (Portrait Widescreen)", "1:1": "1:1 (Square)", "3:4": "3:4 (Portrait Standard)", "3:2": "3:2 (Photo)"}},
            "nodeId": "ratio", "inputName": "aspect_ratio"})]})
    if "--legacy" in sys.argv:
        graph.pop("ratio")
        graph["115"] = {"class_type": "ResolutionSelector", "inputs": {"aspect_ratio": "4:3 (Standard)"}}
        definition = definition.model_copy(update={"workflow_json": json.dumps(graph),
            "bindings": [b for b in definition.bindings if b.source.kind != "aspect_ratio"],
            "exposed_controls": [ExposedControl.model_validate({"id": "aspect_ratio", "label": "Aspect Ratio",
                "target": {"nodeId": "115", "inputName": "aspect_ratio"}, "type": "enum",
                "defaultValue": "4:3 (Standard)", "choices": ["4:3 (Standard)", "1:1 (Square)",
                    "2:3 (Portrait Photo)", "3:2 (Photo)", "3:4 (Portrait Standard)",
                    "9:16 (Portrait Widescreen)", "16:9 (Widescreen)", "21:9 (Ultrawide)"]})]})
    if "--stale" in sys.argv:
        capture = json.loads((ROOT / 'tests/fixtures/saved-legacy-aspect-ratio.json').read_text())
        definition = definition.model_copy(update={'exposed_controls': [ExposedControl.model_validate(capture['control'])]})
    if "--discovery" in sys.argv:
        graph["115"] = graph.pop("ratio")
        definition.bindings[-1].node_id = "115"
        definition = definition.model_copy(update={"workflow_json": json.dumps(graph)})
    profile = config.save("profiles", definition)
    connection = config.save("connections", ConnectionWrite(name="Offline", baseUrl="http://never.test"))
    project = store.create_project(Workspace.model_validate({"projectName": "Fingerprint regression",
        "mode": "T2VA", "durationSeconds": 8, "aspectRatio": "16:9", "variations": 1,
        "creativeRequest": "A cyclist crosses a wet street.", "assets": []}))
    pid, first = project.project_id, project.active_generation_id
    second = store.create_generation(pid).active_generation_id
    store.activate_generation(pid, first)

    def queue(request):
        body = json.loads(request.content)
        submitted.append(body)
        return httpx.Response(200, json={"prompt_id": body["prompt_id"], "number": 7, "node_errors": {}})

    submitted = []
    if "--stale" in sys.argv or "--discovery" in sys.argv:
        from fastapi.testclient import TestClient
        from miniconstruct.app import app
        os.environ['MINICONSTRUCT_EXECUTION_ROOT'] = str(config.path.parent)
        os.environ['MINICONSTRUCT_PROJECTS_ROOT'] = str(Path(temporary) / 'projects')
        route_client = TestClient(app)
        async def metadata(self, request):
            assert request.url.path == '/object_info/ResolutionSelector'
            return httpx.Response(200, json={'ResolutionSelector': {'input': {'required': {'aspect_ratio': (capture['liveInput'] if '--stale' in sys.argv else [["4:3 (Standard)", "1:1 (Square)", "2:3 (Portrait Photo)", "3:2 (Photo)", "3:4 (Portrait Standard)", "9:16 (Portrait Widescreen)", "16:9 (Widescreen)", "21:9 (Ultrawide)"]])}}}})
        httpx.AsyncHTTPTransport.handle_async_request = metadata

        def route(method, path, body=None):
            response = route_client.request(method, '/api/execution' + path, json=body)
            if response.status_code >= 400:
                raise ValueError(response.json()['detail'])
            return response.json()

    def handle(request):
        operation, gid, body = request["op"], request.get("generationId", first), request.get("body", {})
        if operation == 'profileGet':
            return route('GET', f'/profiles/{profile.id}')
        if operation == 'profileSave':
            return route('PUT', f'/profiles/{profile.id}', body)
        if operation == 'aspectCapabilities':
            return route('POST', f'/profiles/{profile.id}/aspect-ratio-capabilities', body)
        if operation == 'enumCapabilities':
            return route('POST', f'/profiles/{profile.id}/controls/aspect_ratio/enum-capabilities', body)
        if operation == "init":
            return {"projectId": pid, "generationId": first, "secondGenerationId": second,
                **store.project_revision(pid),
                "sourceFingerprint": store.execution_snapshot(pid, first)[1].current_fingerprint,
                "prompt": VALID_BASE, "profile": profile.model_dump(mode="json", by_alias=True),
                "ratioNodeId": "115" if "--legacy" in sys.argv or "--discovery" in sys.argv else "ratio",
                "ratioMap": {"4:3": "4:3 (Standard)", "9:16": "9:16 (Portrait Widescreen)", "16:9": "16:9 (Widescreen)", "1:1": "1:1 (Square)", "3:4": "3:4 (Portrait Standard)", "3:2": "3:2 (Photo)", **({"2:3": "2:3 (Portrait Photo)", "21:9": "21:9 (Ultrawide)"} if "--discovery" in sys.argv or "--legacy" in sys.argv else {})},
                "connection": connection.model_dump(mode="json", by_alias=True)}
        if operation == "outputs":
            return store.generation_outputs(pid, gid)
        if operation == "submissions":
            return [store.render_submission(pid, gid, row["id"]).model_dump(mode="json", by_alias=True)
                    for row in store.generation_outputs(pid, gid)["renderSubmissions"]]
        if operation == "submitted":
            return submitted
        if operation == "setupSaved":
            current = store.load_project(pid)
            result = store.save_project(pid, current.workspace.model_copy(update={"aspect_ratio": "4:3", "duration_seconds": 6}),
                expected_project_fingerprint=current.project_fingerprint)
            return {"projectFingerprint": result.project_fingerprint}
        if operation == "workspace":
            return store.read_generation(pid, gid).workspace.model_dump(mode="json", by_alias=True)
        if operation in {"running", "cancel"}:
            store.update_render_job(pid, gid, request["submissionId"],
                RendererJobState(status="running" if operation == "running" else "cancelled"))
            return store.generation_outputs(pid, gid)
        if operation == "controls":
            from miniconstruct.workflow_controls import resolve_controls
            from miniconstruct.legacy_aspect_ratio import legacy_aspect_ratio_control
            values, warnings = resolve_controls(store, pid, gid, profile)
            legacy = legacy_aspect_ratio_control(profile)
            return {"profileId": str(profile.id), "controls": [{**item, "type": control.type,
                "choices": control.choices, "h3SetupControlled": legacy is not None and control.id == legacy.id,
                "hasProjectOverride": False, "hasGenerationOverride": False}
                for control, item in zip(profile.exposed_controls, values)], "warnings": warnings}
        if operation == "selection":
            return store.execution_snapshot(pid, gid)[1].model_dump(mode="json", by_alias=True)
        if operation == "adopt":
            return store.adopt_manual_edit_execution_prompt(pid, gid,
                ManualEditAdoptionWrite(**body)).model_dump(mode="json", by_alias=True)
        if operation == "select":
            return store.select_execution_prompt(pid, gid, SelectionWrite(**body)).model_dump(mode="json", by_alias=True)
        if operation == "references":
            return store.sync_reference_assets(pid, gid, body["assets"], [SubjectRecord(**s) for s in body["subjects"]], body["expectedProjectFingerprint"],
                body.get("expectedProjectEditFingerprint"))
        if operation == "duration":
            return store.sync_execution_setup(pid, gid, body["durationSeconds"], body["expectedProjectFingerprint"], body["aspectRatio"],
                body.get("expectedProjectEditFingerprint"))
        if operation == "duplicate":
            result = store.duplicate_generation(pid, gid)
            return {"generationId": result.active_generation_id, "projectFingerprint": result.project_fingerprint}
        if operation == "activate":
            result = store.activate_generation(pid, gid, body.get("expectedProjectFingerprint"))
            return {"id": pid, **store.project_revision(pid), "generationId": result.active_generation_id,
                "sequence": {"activeGenerationId": result.active_generation_id,
                    "generations": [{"generationId": first}, {"generationId": second}]}}
        if operation == "load":
            result = store.load_project(pid)
            return {"id": pid, **store.project_revision(pid),
                "sequence": {"activeGenerationId": result.active_generation_id,
                    "generations": [{"generationId": first}, {"generationId": second}]}}
        if operation == "prepare":
            if '--stale' in sys.argv:
                return route('POST', '/prepare', {'projectId': pid, 'generationId': gid, 'profileId': str(profile.id)})
            return prepare_workflow(store, pid, gid, config.get("profiles", profile.id)).model_dump(mode="json", by_alias=True)
        if operation == "save":
            return store.save_prompt(pid, gid, SavedPromptWrite(**body)).model_dump(mode="json", by_alias=True)
        if operation == "saved":
            return store.saved_prompt_status(pid, gid).model_dump(mode="json", by_alias=True)
        if operation == "loadSaved":
            return store.activate_saved_prompt(pid, gid, body["id"]).model_dump(mode="json", by_alias=True)
        if operation == "externalReference":
            asset, _ = picture("blue")
            return store.sync_reference_assets(pid, gid, [asset.model_dump(mode="json", by_alias=True)],
                [SubjectRecord(id="subject-1", number=1)],
                store.load_project(pid).project_fingerprint)
        if operation == "picture":
            asset, _ = picture("blue")
            return {"assets": [asset.model_dump(mode="json", by_alias=True)], "subjects": [{"id": "subject-1", "number": 1}]}
        if operation == "externalAuthoring":
            current = store.load_project(pid)
            store.save_project(pid, current.workspace.model_copy(update={"creative_request": "External authoring edit"}),
                expected_project_fingerprint=current.project_fingerprint)
            return store.project_revision(pid)
        if operation == "externalName":
            current = store.load_project(pid)
            store.save_project(pid, current.workspace.model_copy(update={"project_name": "Renamed elsewhere"}),
                expected_project_fingerprint=current.project_fingerprint)
            return store.project_revision(pid)
        if operation == "seed":
            from miniconstruct.execution_state import RendererSeedSettings
            # Test-only Profile fixture configuration; real Send handles allocation.
            graph = json.loads(definition.workflow_json)
            graph["seed"] = {"class_type": "Sampler", "inputs": {"seed": 123}}
            updated = definition.model_copy(update={"workflow_json": json.dumps(graph),
                "bindings": [*definition.bindings, Binding.model_validate({"source": {"kind": "renderer_seed"},
                    "nodeId": "seed", "inputName": "seed"})]})
            updated_profile = config.save("profiles", updated, profile.id)
            store.set_renderer_seed_settings(pid, gid, RendererSeedSettings(**body))
            return {**store.project_revision(pid), "profile": updated_profile.model_dump(mode="json", by_alias=True)}
        if operation == "send":
            from unittest.mock import patch
            if '--stale' in sys.argv:
                with patch.object(httpx.HTTPTransport, 'handle_request', lambda self, request: queue(request)):
                    return route('POST', '/send', {'projectId': pid, 'generationId': gid,
                        'profileId': str(profile.id), 'connectionId': str(connection.id),
                        'expectedExecutionFingerprint': body.get('expectedExecutionFingerprint')})
            with patch("miniconstruct.execution_send.secrets.randbelow", return_value=1000 + len(submitted)):
                result = send_generation(ExecutionSendRequest(projectId=pid, generationId=gid,
                    profileId=profile.id, connectionId=connection.id,
                    expectedExecutionFingerprint=body.get("expectedExecutionFingerprint")),
                    store=store, config=config, transport=httpx.MockTransport(queue))
            return result.model_dump(mode="json", by_alias=True)
        if operation == "reconcile":
            submission = store.render_submission(pid, gid, request["submissionId"])
            def completed(request):
                if request.url.path == "/view":
                    return httpx.Response(200, content=b"offline video", headers={"Content-Type": "video/mp4"})
                return httpx.Response(200, json={"id": str(submission.comfy_prompt_id), "status": "completed",
                    "outputs": {"7": {"gifs": [{"filename": "exact.mp4", "subfolder": "", "type": "output"}]}}})
            return asyncio.run(reconcile_submission(pid, gid, submission.id, store=store, config=config,
                transport=httpx.MockTransport(completed)))
        raise AssertionError(operation)

    for line in sys.stdin:
        try:
            result = {"result": handle(json.loads(line))}
        except Exception as error:
            result = {"error": str(error), "type": type(error).__name__}
        print(json.dumps(result), flush=True)
