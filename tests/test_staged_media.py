"""Real Send orchestration with an original-byte, collision-aware fake ComfyUI."""
from concurrent.futures import ThreadPoolExecutor
from email.parser import BytesParser
from email.policy import default
import json
from threading import Event

import httpx
import pytest

from miniconstruct import execution as ex, execution_send as send
from miniconstruct.workflow_preparation import prepare_workflow
from test_execution_send import env, no_live
from test_send_verification import media_env, expected_body, mutate
from test_reference_authority import picture
from test_workflow_preparation import capacity_profile, select, _video_asset, VIDEO_PROMPT
from test_execution_routing import audio_asset
from test_validator import valid_ref_prompt


class Remote:
    def __init__(self, root=None):
        self.root = root
        self.files, self.calls = {}, []
        self.before = None

    def handle(self, request):
        self.calls.append(request)
        if self.before:
            result = self.before(request)
            if result is not None:
                return result
        if request.url.path.endswith('/upload/image'):
            message = BytesParser(policy=default).parsebytes(
                b'Content-Type: ' + request.headers['content-type'].encode() + b'\r\n\r\n' + request.content)
            part = next(p for p in message.iter_parts() if p.get_filename())
            name, data = part.get_filename(), part.get_payload(decode=True)
            # Authoritative response may rename and move the requested location.
            name = 'returned-' + name
            while name in self.files and self.files[name] != data:
                name = 'collision-' + name
            self.files[name] = data
            if self.root is not None:
                target = self.root / "actual" / "nested" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            return httpx.Response(200, json={'name': name, 'subfolder': 'actual/nested', 'type': 'input'})
        if request.url.path.endswith('/view'):
            assert request.url.params['type'] == 'input'
            assert request.url.params['subfolder'] == 'actual/nested'
            assert 'preview' not in request.url.params and 'channel' not in request.url.params
            assert 'no-cache' in request.headers['cache-control']
            data = self.files.get(request.url.params['filename'])
            return httpx.Response(404) if data is None else httpx.Response(200, content=data)
        assert request.url.path.endswith('/prompt')
        body = json.loads(request.content)
        for node in body['prompt'].values():
            for value in node['inputs'].values():
                if isinstance(value, str) and value.startswith('actual/nested/'):
                    assert value.removeprefix('actual/nested/') in self.files
        return httpx.Response(200, json={'prompt_id': body['prompt_id']})

    def counts(self):
        return tuple(sum(r.url.path.endswith(path) for r in self.calls)
                     for path in ('/upload/image', '/view', '/prompt'))


def dispatch(env, remote, body=None):
    return send.send_generation(body or expected_body(env), store=env[0], config=env[1],
                                transport=httpx.MockTransport(remote.handle))


@pytest.mark.parametrize("local", [False, True])
def test_five_pictures_three_sends(env, workspace_factory, tmp_path, local, monkeypatch):
    from miniconstruct import local_staging
    hashed = []
    monkeypatch.setattr(local_staging, "count", lambda name, size: hashed.append(size))
    store, config, _, connection, _, make = env
    profile = config.save('profiles', capacity_profile(count=5))
    assets = [picture(color, f'pic-{index}')[0].model_copy(update={'order': index})
              for index, color in enumerate(['red', 'blue', 'green', 'white', 'black'])]
    project = store.create_project(workspace_factory(mode='Ref2VA', assets=assets))
    select(store, project, valid_ref_prompt())
    current = store, config, profile, connection, project, lambda: make(project, profile)
    body = expected_body(current)  # Ordinary Prepare must remain offline (no_live).
    remote = mapped_remote(current, tmp_path, local)
    results = []
    for expected in [(5, 0, 1), (5, 0 if local else 5, 2), (5, 0 if local else 10, 3)]:
        hashed.clear()
        results.append(dispatch(current, remote, body))
        assert remote.counts() == expected
        if local:
            assert sum(hashed) == sum(len(data) for data in remote.files.values())
            assert not results[-1].staging_warning
    assert len({r.prompt_id for r in results}) == 3
    assert len({r.render_submission_id for r in results}) == 3
    assert results[0].uploaded_inputs == results[1].uploaded_inputs == results[2].uploaded_inputs
    for result in results:
        saved = store.render_submission(project.project_id, project.active_generation_id, result.render_submission_id)
        assert saved.comfy_prompt_id == result.prompt_id
    latest = store.load_project(project.project_id)
    replacement = picture("purple", "pic-0")[0]
    store.sync_reference_assets(project.project_id, project.active_generation_id,
        [replacement.model_dump(mode="json", by_alias=True),
         *[asset.model_dump(mode="json", by_alias=True) for asset in assets[1:]]],
        latest.workspace.subjects, latest.project_fingerprint)
    select(store, store.load_project(project.project_id), valid_ref_prompt())
    changed = dispatch(current, remote)
    assert remote.counts() == (6, 0 if local else 14, 4)
    assert changed.uploaded_inputs[0].filename != results[-1].uploaded_inputs[0].filename
    assert changed.uploaded_inputs[1:] == results[-1].uploaded_inputs[1:]


@pytest.mark.parametrize('change', ['delete', 'same-size', 'larger', 'shorter', 'restart-retains', 'unsupported'])
def test_remote_revalidation_and_collision_recovery(media_env, change):
    remote = Remote()
    first = dispatch(media_env, remote)
    name, data = next(iter(remote.files.items()))
    if change == 'delete':
        remote.files.clear()
    elif change == 'same-size':
        remote.files[name] = b'x' * len(data)
    elif change == 'larger':
        remote.files[name] = data + b'x'
    elif change == 'shorter':
        remote.files[name] = data[:-1]
    elif change == 'unsupported':
        remote.before = lambda req: httpx.Response(405) if req.url.path.endswith('/view') else None
    second = dispatch(media_env, remote)
    assert remote.counts() == (1 if change == 'restart-retains' else 2, 1, 2)
    assert remote.files[second.uploaded_inputs[0].filename.removeprefix('actual/nested/')] == data
    assert first.prompt_id != second.prompt_id


@pytest.mark.parametrize("local", [False, True])
def test_replacement_same_filename_changes_identity(media_env, tmp_path, local):
    remote = mapped_remote(media_env, tmp_path, local)
    first = dispatch(media_env, remote)
    mutate(media_env, 'reference')
    select(media_env[0], media_env[0].load_project(media_env[4].project_id), valid_ref_prompt())
    second = dispatch(media_env, remote)
    assert remote.counts() == (2, 0, 2)
    assert first.uploaded_inputs[0].filename != second.uploaded_inputs[0].filename


@pytest.mark.parametrize("local", [False, True])
@pytest.mark.parametrize('change', ['id', 'url', 'name', 'root'])
def test_connection_scope(media_env, change, tmp_path, local):
    remote = mapped_remote(media_env, tmp_path, local)
    dispatch(media_env, remote)
    store, config, profile, connection, project, make = media_env
    new = config.save('connections', ex.ConnectionWrite(
        name='Renamed' if change == 'name' else connection.name,
        baseUrl='http://different.test' if change == 'url' else connection.base_url,
        localInputDirectory=str(tmp_path / "other-root") if change == "root" else str(remote.root) if local else None),
        None if change == 'id' else connection.id)
    body = expected_body(media_env).model_copy(update={'connection_id': new.id})
    dispatch(media_env, remote, body)
    assert remote.counts() == (2, 0, 2)


@pytest.mark.parametrize('new_project', [True, False])
@pytest.mark.parametrize("local", [False, True])
def test_shared_content_requires_current_project_authority(media_env, new_project, tmp_path, local):
    remote = mapped_remote(media_env, tmp_path, local)
    first = dispatch(media_env, remote)
    store, config, profile, connection, project, make = media_env
    other = (store.create_project(project.workspace) if new_project else
             store.create_generation(project.project_id, workspace=project.workspace))
    select(store, other, valid_ref_prompt())
    prepared = prepare_workflow(store, other.project_id, other.active_generation_id, profile)
    body = send.ExecutionSendRequest(projectId=other.project_id, generationId=other.active_generation_id,
        profileId=profile.id, connectionId=connection.id, expectedExecutionFingerprint=prepared.execution_fingerprint)
    second = dispatch(media_env, remote, body)
    assert remote.counts() == (1, 0 if local else 1, 2)
    assert first.render_submission_id != second.render_submission_id


@pytest.mark.parametrize("local", [False, True])
def test_video_audio_reuse(env, workspace_factory, tmp_path, local):
    store, config, _, connection, _, make = env
    profile = config.save('profiles', capacity_profile(av=True))
    project = store.create_project(workspace_factory(mode='Ref2VA', assets=[_video_asset(store), audio_asset(store)]))
    select(store, project, VIDEO_PROMPT)
    current = store, config, profile, connection, project, lambda: make(project, profile)
    remote = mapped_remote(current, tmp_path, local)
    dispatch(current, remote)
    dispatch(current, remote)
    assert remote.counts() == (2, 0 if local else 2, 2)


@pytest.mark.parametrize("local", [False, True])
def test_concurrent_sends_share_staging_but_not_prompt(media_env, tmp_path, local):
    remote, entered, release = mapped_remote(media_env, tmp_path, local), Event(), Event()
    body = expected_body(media_env)
    def defer(request):
        if request.url.path.endswith('/upload/image'):
            entered.set()
            assert release.wait(10)
    remote.before = defer
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(dispatch, media_env, remote, body)
        assert entered.wait(10)
        second = pool.submit(dispatch, media_env, remote, body)
        release.set()
        results = [first.result(10), second.result(10)]
    assert remote.counts() == (1, 0 if local else 1, 2)
    assert results[0].prompt_id != results[1].prompt_id


@pytest.mark.parametrize('phase', ['upload', 'view', 'prompt'])
def test_transport_failure_never_retries(media_env, phase):
    remote = Remote()
    if phase == 'view':
        dispatch(media_env, remote)
        remote.calls.clear()
    def fail(request):
        if request.url.path.endswith('/' + ('upload/image' if phase == 'upload' else phase)):
            raise httpx.ReadTimeout('deferred transport lost', request=request)
    remote.before = fail
    with pytest.raises(send.SendError, match='unknown' if phase == 'prompt' else 'no prompt|No prompt'):
        dispatch(media_env, remote)
    assert remote.counts() == {'upload': (1, 0, 0), 'view': (0, 1, 0), 'prompt': (1, 0, 1)}[phase]


def test_local_corruption_never_uses_remote_receipt(media_env):
    remote = Remote()
    dispatch(media_env, remote)
    mutate(media_env, 'bytes')
    with pytest.raises((ex.InvalidBindings, ValueError)):
        dispatch(media_env, remote)
    assert remote.counts() == (1, 0, 1)


@pytest.mark.parametrize("local", [False, True])
def test_reused_media_preserves_independent_random_seeds(media_env, monkeypatch, tmp_path, local):
    from miniconstruct.execution_state import RendererSeedSettings
    store, config, profile, connection, project, make = media_env
    graph = json.loads(json.dumps(profile.workflow))
    graph['h3']['inputs']['seed'] = 10
    profile = config.save('profiles', ex.ProfileCreate(name='Seed', compatibleModes=profile.compatible_modes,
        workflowJson=json.dumps(graph), bindings=[*profile.bindings,
            {'source': {'kind': 'renderer_seed'}, 'nodeId': 'h3', 'inputName': 'seed'}]))
    store.set_renderer_seed_settings(project.project_id, project.active_generation_id, RendererSeedSettings(mode='random'))
    body = expected_body((store, config, profile, connection, project,
                          lambda: make().model_copy(update={'profile_id': profile.id})))
    seeds = iter([31, 42, 53])
    monkeypatch.setattr(send.secrets, 'randbelow', lambda _: next(seeds))
    remote = mapped_remote(media_env, tmp_path, local)
    results = [dispatch(media_env, remote, body) for _ in range(3)]
    assert remote.counts() == (1, 0 if local else 2, 3)
    assert [r.renderer_seed for r in results] == [31, 42, 53]
    assert [store.render_submission(project.project_id, project.active_generation_id,
            r.render_submission_id).renderer_seed for r in results] == [31, 42, 53]


def test_execution_changed_during_reuse_never_queues(media_env):
    remote = Remote()
    dispatch(media_env, remote)
    def change(request):
        if request.url.path.endswith('/view'):
            mutate(media_env, 'prompt')
    remote.before = change
    with pytest.raises(ex.InvalidBindings, match='Execution changed'):
        dispatch(media_env, remote)
    assert remote.counts() == (1, 1, 1)


def mapped_remote(current, tmp_path, local=True):
    root = tmp_path / "comfy-input"
    if local:
        root.mkdir(exist_ok=True)
        connection = current[1].get("connections", current[3].id)
        current[1].save("connections", ex.ConnectionWrite(name=connection.name,
            baseUrl=connection.base_url, localInputDirectory=str(root)), connection.id)
    return Remote(root if local else None)


@pytest.mark.parametrize("change", ["missing", "same-size", "shorter", "larger"])
def test_local_invalid_file_restages(media_env, tmp_path, change):
    remote = mapped_remote(media_env, tmp_path)
    first = dispatch(media_env, remote)
    path = remote.root / first.uploaded_inputs[0].filename
    data = path.read_bytes()
    if change == "missing":
        path.unlink()
    else:
        path.write_bytes(b"x" * (len(data) + {"same-size": 0, "shorter": -1, "larger": 1}[change]))
        remote.files[path.name] = path.read_bytes()  # ComfyUI sees these changed bytes too.
    second = dispatch(media_env, remote)
    assert remote.counts() == (2, 0, 2)
    assert (remote.root / second.uploaded_inputs[0].filename).read_bytes() == data
    if change != "missing":
        assert first.uploaded_inputs[0].filename != second.uploaded_inputs[0].filename


@pytest.mark.parametrize("kind", ["missing-root", "file-root", "wrong-mapping", "permission"])
def test_local_mapping_fallback(media_env, tmp_path, monkeypatch, kind):
    from miniconstruct import local_staging
    remote = mapped_remote(media_env, tmp_path)
    if kind == "missing-root":
        remote.root.rmdir()
        remote.root = None
    elif kind == "file-root":
        remote.root.rmdir()
        remote.root.write_bytes(b"not a directory")
        remote.root = None
    elif kind == "wrong-mapping":
        remote.root = None
    else:
        def denied(*args):
            raise PermissionError("inaccessible")
        monkeypatch.setattr(local_staging.os, "scandir", denied)
        remote.root = None
    results = [dispatch(media_env, remote) for _ in range(3)]
    assert all(r.staging_warning for r in results)
    assert remote.counts() == (1, 3 if kind == "wrong-mapping" else 2, 3)


def test_local_mapping_recovers_only_after_exact_verification(media_env, tmp_path):
    remote = mapped_remote(media_env, tmp_path)
    root, remote.root = remote.root, None
    first = dispatch(media_env, remote)
    assert first.staging_warning and remote.counts() == (1, 1, 1)
    path = root / first.uploaded_inputs[0].filename
    path.parent.mkdir(parents=True)
    path.write_bytes(remote.files[path.name])
    second = dispatch(media_env, remote)
    assert not second.staging_warning and remote.counts() == (1, 1, 2)
    path.unlink()
    remote.root = root
    dispatch(media_env, remote)
    assert remote.counts() == (2, 1, 3)  # Once mapped, a missing file must restage.


@pytest.mark.parametrize("payload", [
    {"name": "../secret", "subfolder": "", "type": "input"},
    {"name": "secret", "subfolder": "../", "type": "input"},
    {"name": "C:secret", "subfolder": "", "type": "input"},
    {"name": "secret", "subfolder": "C:/outside", "type": "input"},
    {"name": "secret", "subfolder": "/outside", "type": "input"},
    {"name": "NUL.png", "subfolder": "", "type": "input"},
    {"name": "secret", "subfolder": "", "type": "output"},
])
def test_local_unsafe_response_never_queues(media_env, tmp_path, payload):
    remote = mapped_remote(media_env, tmp_path)
    remote.before = lambda r: httpx.Response(200, json=payload) if r.url.path.endswith("/upload/image") else None
    with pytest.raises(send.SendError):
        dispatch(media_env, remote)
    assert remote.counts() == (1, 0, 0)


def test_local_verification_still_revalidates_authority(media_env, tmp_path, monkeypatch):
    remote = mapped_remote(media_env, tmp_path)
    dispatch(media_env, remote)
    original = send.verify_local
    def changed(*args):
        result = original(*args)
        mutate(media_env, "prompt")
        return result
    monkeypatch.setattr(send, "verify_local", changed)
    with pytest.raises(ex.InvalidBindings, match="Execution changed"):
        dispatch(media_env, remote)
    assert remote.counts() == (1, 0, 1)


@pytest.mark.parametrize("kind", ["directory-link", "file-link", "opened-handle", "junction"])
def test_local_escape_rejected_before_read(media_env, tmp_path, monkeypatch, kind):
    import os
    from miniconstruct import local_staging
    remote = mapped_remote(media_env, tmp_path)
    first = dispatch(media_env, remote)
    path = remote.root / first.uploaded_inputs[0].filename
    outside = tmp_path / "unrelated"
    outside.mkdir()
    secret = outside / path.name
    secret.write_bytes(path.read_bytes())
    if kind == "opened-handle":
        monkeypatch.setattr(local_staging, "opened_path", lambda stream: secret)
    elif kind == "junction":
        if os.name != "nt":
            pytest.skip("Windows junction")
        import _winapi
        path.unlink()
        path.parent.rmdir()
        _winapi.CreateJunction(str(outside), str(path.parent))
    else:
        path.unlink()
        link, target = (path, secret) if kind == "file-link" else (path.parent, outside)
        if kind == "directory-link":
            path.parent.rmdir()
        try:
            link.symlink_to(target, target_is_directory=kind == "directory-link")
        except OSError:
            pytest.skip("Symlink privilege unavailable; junction and handle checks run separately")
    assert local_staging.verify_local(remote.root, first.uploaded_inputs[0].filename,
        __import__("hashlib").sha256(secret.read_bytes()).hexdigest(), secret.stat().st_size) is None
    # Never hash bytes through the untrusted location. HTTP verification is safe.
    monkeypatch.setattr(local_staging, "count", lambda *args: pytest.fail("read escaped bytes"))
    second = dispatch(media_env, remote)
    assert second.staging_warning
    assert remote.counts() == (1, 1, 2)


def test_local_prompt_ambiguity_no_retry(media_env, tmp_path):
    remote = mapped_remote(media_env, tmp_path)
    dispatch(media_env, remote)
    def fail(request):
        if request.url.path.endswith("/prompt"):
            raise httpx.ReadTimeout("lost acknowledgment", request=request)
    remote.before = fail
    with pytest.raises(send.SendError, match="unknown.*No automatic retry"):
        dispatch(media_env, remote)
    assert remote.counts() == (1, 0, 2)
