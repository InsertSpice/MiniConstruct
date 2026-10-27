"""Regression from the actual two-choice Ref2VA declaration, not an ideal enum."""
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from miniconstruct.app import app
from miniconstruct import execution as ex
from miniconstruct.project_store import ProjectStore
from test_legacy_aspect_ratio import fixture_profile, offline
from test_workflow_preparation import select

CAPTURE = json.loads((Path(__file__).parent / 'fixtures/saved-legacy-aspect-ratio.json').read_text())


def test_saved_two_choice_profile_repaired_through_explicit_preview_and_save(tmp_path, monkeypatch, workspace_factory):
    config, profile = fixture_profile(tmp_path, choices=CAPTURE['control']['choices'])
    monkeypatch.setenv('MINICONSTRUCT_EXECUTION_ROOT', str(config.path.parent))
    monkeypatch.setenv('MINICONSTRUCT_PROJECTS_ROOT', str(tmp_path / 'projects'))
    store = ProjectStore()
    project = store.create_project(workspace_factory(aspectRatio='3:2'))
    select(store, project)
    connection = config.save('connections', ex.ConnectionWrite(name='Mock only', baseUrl='http://never.test'))
    profile = config.save('profiles', ex.ProfileUpdate(**{
        **profile.model_dump(include=set(ex.ProfileEdit.model_fields)),
        'workflow_control_presets': [{'name': 'Keep me', 'values': {'aspect_ratio': '4:3 (Standard)', 'megapixels': 0.6}}]
    }), profile.id)
    calls = []
    async def metadata(self, request):
        calls.append(str(request.url))
        assert request.method == 'GET' and request.url.path == '/object_info/ResolutionSelector'
        return httpx.Response(200, json={'ResolutionSelector': {'input': {'required': {'aspect_ratio': CAPTURE['liveInput']}}}})
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', metadata)
    body = dict(projectId=project.project_id, generationId=project.active_generation_id, profileId=str(profile.id))
    with TestClient(app) as client:
        before = config.path.read_bytes()
        failed = client.post('/api/execution/prepare', json=body)
        assert failed.status_code == 422
        assert 'missing declared choice' in failed.json()['detail']
        assert '4:3 (Standard)' in failed.json()['detail']
        assert str(profile.id) in failed.json()['detail']
        assert config.path.read_bytes() == before
        assert calls == []
        project_before = {str(p): p.read_bytes() for p in (tmp_path / 'projects').rglob('*.json')}
        # The configuration operation must exist, fetch the exact class/input,
        # and leave persistence to the normal explicit Profile save.
        response = client.post(f'/api/execution/profiles/{profile.id}/controls/aspect_ratio/enum-capabilities',
                               json={'connectionId': str(connection.id)})
        assert response.status_code == 200, response.text
        assert len(calls) == 1
        assert config.path.read_bytes() == before
        data = response.json()
        assert data['savedChoices'] == CAPTURE['control']['choices']
        assert '3:2 (Photo)' in data['choices']
        draft = profile.model_dump(mode='json', by_alias=True, include=set(ex.ProfileEdit.model_fields))
        draft['exposedControls'][0]['choices'] = data['choices']
        url = f'/api/execution/profiles/{profile.id}'
        saved = client.put(url, json=draft)
        assert saved.status_code == 200, saved.text
        assert client.get(url).json() == saved.json()
        expected = profile.model_dump(mode='json', by_alias=True)
        expected['exposedControls'][0]['choices'] = data['choices']
        assert saved.json() == expected  # entire graph, other controls, defaults, presets survive
        before = config.path.read_bytes()
        prepared = client.post('/api/execution/prepare', json=body)
        assert prepared.status_code == 200, prepared.text
        assert prepared.json()['profileId'] == str(profile.id)
        assert prepared.json()['graph']['115']['inputs']['aspect_ratio'] == '3:2 (Photo)'
        store.load_project(project.project_id)  # reopen and use canonical saved declarations
        again = client.post('/api/execution/prepare', json=body).json()
        assert again['graph']['115']['inputs']['aspect_ratio'] == '3:2 (Photo)'
        assert config.path.read_bytes() == before and len(calls) == 1
        assert {str(p): p.read_bytes() for p in (tmp_path / 'projects').rglob('*.json')} == project_before
        sent = []
        def queue(self, request):
            assert request.url.path == '/prompt'
            payload = json.loads(request.content); sent.append(payload)
            return httpx.Response(200, json={'prompt_id': payload['prompt_id'], 'number': 1, 'node_errors': {}})
        monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', queue)
        result = client.post('/api/execution/send', json={**body, 'connectionId': str(connection.id),
            'expectedExecutionFingerprint': again['executionFingerprint']})
        assert result.status_code == 200, result.text
        assert sent[0]['prompt']['115']['inputs']['aspect_ratio'] == '3:2 (Photo)'


@pytest.mark.parametrize('declaration,ok', [
    (CAPTURE['liveInput'], True),
    ([CAPTURE['liveInput'][1]['options']], True),
    ([['4:3 (Standard)', '16:9 (Widescreen)']], True),
    ([['4:3 (Standard)', '3:2 (Photo)', '3:2 Other']], True),
    ([['4:3 (Standard)', '3:2 (Photo)', '3:2 (Photo)']], False),
    (['STRING', {}], False),
    (['COMBO', {'options': ['3:2 (Photo)']}], False),  # old default unsupported
    (['COMBO', {'options': ['4:3 (Standard)'], 'multiselect': True}], False),
    (None, False),
])
def test_verified_options_fail_closed(tmp_path, declaration, ok):
    import asyncio
    from miniconstruct.enum_capabilities import enum_capabilities
    from miniconstruct.legacy_aspect_ratio import legacy_renderer_value
    config, profile = fixture_profile(tmp_path, choices=CAPTURE['control']['choices'])
    connection = ex.Connection(name='Mock', baseUrl='http://never.test')
    def respond(request):
        return httpx.Response(200, json={'ResolutionSelector': {'input': {'required': {'aspect_ratio': declaration}}}})
    before = config.path.read_bytes()
    def run():
        return asyncio.run(enum_capabilities(profile, 'aspect_ratio', connection, transport=httpx.MockTransport(respond)))
    if not ok:
        with pytest.raises(ex.InvalidBindings, match='Could not verify'):
            run()
    else:
        result = run()
        control = profile.exposed_controls[0].model_copy(update={'choices': result['choices']})
        matches = [v for v in control.choices if v.startswith('3:2')]
        if len(matches) != 1:
            with pytest.raises(ex.InvalidBindings, match='missing|ambiguous'):
                legacy_renderer_value(control, '3:2')
        else:
            assert legacy_renderer_value(control, '3:2') == '3:2 (Photo)'
    assert config.path.read_bytes() == before


@pytest.mark.parametrize('failure', ['offline', 'redirect', 'wrong_class', 'wrong_input', 'ambiguous_input', 'malformed', 'oversize'])
def test_unverified_metadata_never_changes_profile(tmp_path, failure):
    import asyncio
    from miniconstruct.enum_capabilities import enum_capabilities
    config, profile = fixture_profile(tmp_path, choices=CAPTURE['control']['choices'])
    before = config.path.read_bytes()
    def respond(request):
        if failure == 'offline':
            raise httpx.ConnectError('offline')
        if failure == 'redirect':
            return httpx.Response(302, headers={'Location': 'http://other.test'})
        if failure == 'oversize':
            return httpx.Response(200, content=b' ' * (1024 * 1024 + 1))
        inputs = {'required': {'aspect_ratio': CAPTURE['liveInput']}}
        if failure == 'wrong_input': inputs = {'required': {'other': CAPTURE['liveInput']}}
        if failure == 'ambiguous_input': inputs['optional'] = inputs['required']
        if failure == 'malformed': inputs = None
        return httpx.Response(200, json={('Other' if failure == 'wrong_class' else 'ResolutionSelector'): {'input': inputs}})
    with pytest.raises(ex.InvalidBindings, match='Could not verify'):
        asyncio.run(enum_capabilities(profile, 'aspect_ratio', ex.Connection(name='Mock', baseUrl='http://never.test'),
                                      transport=httpx.MockTransport(respond)))
    assert config.path.read_bytes() == before
