"""Exact semantic discovery, persistence, offline compilation and mocked Send."""
import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from miniconstruct import execution as ex
from miniconstruct.app import app
from miniconstruct.enum_capabilities import aspect_ratio_capabilities
from miniconstruct.project_store import ProjectStore
from miniconstruct.workflow_preparation import prepare_workflow
from test_legacy_aspect_ratio import CHOICES, MAPPINGS, fixture_profile, offline, send
from test_workflow_preparation import select


def modern_profile(tmp_path):
    config, profile = fixture_profile(tmp_path)
    binding = ex.Binding.model_validate({'source': {'kind': 'aspect_ratio', 'valueMap': MAPPINGS},
                                         'nodeId': '115', 'inputName': 'aspect_ratio'})
    profile = config.save('profiles', ex.ProfileUpdate(name=profile.name, compatibleModes=profile.compatible_modes,
        bindings=[*profile.bindings, binding], exposedControls=[]), profile.id)
    return config, profile


@pytest.mark.parametrize('ratio,expected', [('2:3', '2:3 (Portrait Photo)'), ('21:9', '21:9 (Ultrawide)'), ('3:2', '3:2 (Photo)')])
def test_discover_save_reload_prepare_send(tmp_path, monkeypatch, workspace_factory, ratio, expected):
    config, profile = modern_profile(tmp_path)
    monkeypatch.setenv('MINICONSTRUCT_EXECUTION_ROOT', str(config.path.parent))
    connection = config.save('connections', ex.ConnectionWrite(name='Mock', baseUrl='http://never.test'))
    calls = []
    async def metadata(self, request):
        calls.append(str(request.url))
        assert request.url.path == '/object_info/ResolutionSelector'
        return httpx.Response(200, json={'ResolutionSelector': {'input': {'required': {'aspect_ratio': [CHOICES]}}}})
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', metadata)
    before = config.path.read_bytes()
    with TestClient(app) as client:
        result = client.post(f'/api/execution/profiles/{profile.id}/aspect-ratio-capabilities', json={
            'nodeId': '115', 'inputName': 'aspect_ratio', 'connectionId': str(connection.id)})
        assert result.status_code == 200, result.text
        assert config.path.read_bytes() == before
        proposed = result.json()
        assert len(profile.bindings[-1].source.value_map) == 6
        assert len(proposed['valueMap']) == 8
        draft = profile.model_dump(mode='json', by_alias=True, include=set(ex.ProfileEdit.model_fields))
        draft['bindings'][-1]['source'].update(valueMap={**proposed['valueMap'], **MAPPINGS}, enumChoices=proposed['choices'])
        saved = client.put(f'/api/execution/profiles/{profile.id}', json=draft)
        assert saved.status_code == 200, saved.text
        reloaded = client.get(f'/api/execution/profiles/{profile.id}').json()
        assert reloaded == saved.json()
    profile = ex.Profile.model_validate(reloaded)
    assert not profile.exposed_controls
    assert all(profile.bindings[-1].source.value_map[k] == v for k, v in MAPPINGS.items())
    store = ProjectStore(tmp_path / 'projects')
    project = store.create_project(workspace_factory(aspectRatio=ratio)); select(store, project)
    prepared = prepare_workflow(store, project.project_id, project.active_generation_id, profile)
    assert len(calls) == 1  # offline Prepare makes no discovery request
    assert prepared.graph['115']['inputs']['aspect_ratio'] == expected
    sent = []
    send(store, config, project, profile, prepared.execution_fingerprint, sent)
    assert sent[0]['prompt']['115']['inputs']['aspect_ratio'] == expected
    assert prepared.graph['115']['inputs']['megapixels'] == 0.6
    assert len(calls) == 1


@pytest.mark.parametrize('failure', ['offline', 'wrong_node', 'wrong_input', 'duplicate', 'ambiguous', 'number', 'freeform'])
def test_bad_metadata_never_mutates_saved_profile(tmp_path, failure):
    config, profile = modern_profile(tmp_path)
    before = config.path.read_bytes()
    def response(request):
        if failure == 'offline': raise httpx.ConnectError('offline')
        choices = CHOICES + ([CHOICES[0]] if failure == 'duplicate' else ['4:3 (Another)'] if failure == 'ambiguous' else [])
        declaration = [[1, 2]] if failure == 'number' else ['STRING'] if failure == 'freeform' else [choices]
        return httpx.Response(200, json={'ResolutionSelector': {'input': {'required': {'aspect_ratio': declaration}}}})
    target = ex.ControlTarget(nodeId='missing' if failure == 'wrong_node' else '115',
                              inputName='other' if failure == 'wrong_input' else 'aspect_ratio')
    with pytest.raises(ex.InvalidBindings):
        asyncio.run(aspect_ratio_capabilities(profile, target, ex.Connection(name='Mock', baseUrl='http://never.test'),
                                              transport=httpx.MockTransport(response)))
    assert config.path.read_bytes() == before


@pytest.mark.parametrize('ratio', ['5:4', 'auto'])
def test_closed_enum_rejects_unsupported_custom(tmp_path, workspace_factory, ratio):
    config, profile = modern_profile(tmp_path)
    profile.bindings[-1].source.enum_choices = CHOICES
    store = ProjectStore(tmp_path / 'projects')
    project = store.create_project(workspace_factory(aspectRatio=ratio)); select(store, project)
    with pytest.raises(ex.InvalidBindings, match='does not support'):
        prepare_workflow(store, project.project_id, project.active_generation_id, profile)


@pytest.mark.parametrize('ratio', ['2:3', '21:9'])
def test_old_six_mapping_profile_blocks_missing_standard(tmp_path, workspace_factory, ratio):
    _, profile = modern_profile(tmp_path)
    store = ProjectStore(tmp_path / 'projects')
    project = store.create_project(workspace_factory(aspectRatio=ratio)); select(store, project)
    with pytest.raises(ex.InvalidBindings, match='mapping missing'):
        prepare_workflow(store, project.project_id, project.active_generation_id, profile)


def test_freeform_custom_preserves_raw_warning(tmp_path, workspace_factory):
    _, profile = modern_profile(tmp_path)
    store = ProjectStore(tmp_path / 'projects')
    project = store.create_project(workspace_factory(aspectRatio='5:4')); select(store, project)
    prepared = prepare_workflow(store, project.project_id, project.active_generation_id, profile)
    assert prepared.graph['115']['inputs']['aspect_ratio'] == '5:4'
    assert any('raw value' in warning for warning in prepared.warnings)


@pytest.mark.parametrize('ratio', ['2:3', '21:9'])
def test_new_ratios_survive_reopen_switch_duplicate_and_h3_authoring(tmp_path, workspace_factory, ratio):
    store = ProjectStore(tmp_path / 'projects')
    first = store.create_project(workspace_factory(aspectRatio=ratio))
    pid, gid = first.project_id, first.active_generation_id
    reopened = ProjectStore(tmp_path / 'projects')
    assert reopened.load_project(pid).workspace.aspect_ratio == ratio
    from miniconstruct.h3.builder import instruction_layers
    assert any(f'Aspect ratio guidance: {ratio}.' in text for _, text in instruction_layers(reopened.load_project(pid).workspace, False))
    second = reopened.create_generation(pid)
    reopened.activate_generation(pid, gid)
    assert reopened.load_project(pid).workspace.aspect_ratio == ratio
    duplicated = reopened.duplicate_generation(pid, gid)
    assert reopened.read_generation(pid, duplicated.active_generation_id).workspace.aspect_ratio == ratio
    reopened.activate_generation(pid, second.active_generation_id)
    reopened.activate_generation(pid, gid)
    assert reopened.load_project(pid).workspace.aspect_ratio == ratio


@pytest.mark.parametrize('ratio', ['2:3', '21:9'])
def test_saved_enum_can_resolve_named_custom_without_explicit_map_entry(tmp_path, workspace_factory, ratio):
    _, profile = modern_profile(tmp_path)
    profile.bindings[-1].source.enum_choices = CHOICES
    store = ProjectStore(tmp_path / 'projects')
    project = store.create_project(workspace_factory(aspectRatio=ratio)); select(store, project)
    prepared = prepare_workflow(store, project.project_id, project.active_generation_id, profile)
    assert prepared.graph['115']['inputs']['aspect_ratio'] == next(v for v in CHOICES if v.startswith(ratio + ' '))
    assert prepared.resolutions[-1].renderer_value == prepared.graph['115']['inputs']['aspect_ratio']


def test_modern_ratio_change_blocks_stale_send_and_recompiles_exactly(tmp_path, workspace_factory):
    config, profile = modern_profile(tmp_path)
    updated = ex.AspectRatioSource(kind='aspect_ratio', valueMap={**MAPPINGS, '2:3': '2:3 (Portrait Photo)', '21:9': '21:9 (Ultrawide)'}, enumChoices=CHOICES)
    profile.bindings[-1].source = updated
    profile = config.save('profiles', ex.ProfileUpdate(name=profile.name, compatibleModes=profile.compatible_modes,
        bindings=profile.bindings, exposedControls=[]), profile.id)
    store = ProjectStore(tmp_path / 'projects')
    project = store.create_project(workspace_factory(aspectRatio='2:3', durationSeconds=8)); select(store, project)
    before = prepare_workflow(store, project.project_id, project.active_generation_id, profile)
    store.sync_execution_setup(project.project_id, project.active_generation_id, 8, project.project_fingerprint, '21:9')
    sent = []
    with pytest.raises(ex.InvalidBindings, match='stale'):
        send(store, config, project, profile, before.execution_fingerprint, sent)
    assert not sent
    after = prepare_workflow(store, project.project_id, project.active_generation_id, profile)
    assert before.execution_fingerprint != after.execution_fingerprint
    send(store, config, project, profile, after.execution_fingerprint, sent)
    assert sent[0]['prompt']['115']['inputs']['aspect_ratio'] == '21:9 (Ultrawide)'


def test_absent_enum_metadata_preserves_old_profile_identity(tmp_path):
    from miniconstruct.native_continuation import profile_identity, digest
    _, profile = modern_profile(tmp_path)
    old = profile.model_dump(mode='json', by_alias=True)
    old.pop('workflowControlPresets', None); old.pop('exposedControls', None)
    for binding in old['bindings']:
        if binding['source']['kind'] == 'aspect_ratio': binding['source'].pop('enumChoices', None)
    assert profile_identity(profile) == digest(old)
    profile.bindings[-1].source.enum_choices = CHOICES
    assert profile_identity(profile) != digest(old)


@pytest.mark.parametrize('requested,wrong', [('3:2', '4:3 (Standard)'), ('5:4', '4:3 (Standard)')])
def test_saved_numeric_conflict_blocks_prepare_and_send(tmp_path, workspace_factory, requested, wrong):
    config, profile = modern_profile(tmp_path)
    source = ex.AspectRatioSource(kind='aspect_ratio', valueMap={**MAPPINGS, requested: wrong}, enumChoices=CHOICES)
    profile.bindings[-1].source = source
    profile = config.save('profiles', ex.ProfileUpdate(name=profile.name, compatibleModes=profile.compatible_modes,
        bindings=profile.bindings, exposedControls=[]), profile.id)
    profile = ex.ExecutionStore(config.path.parent).get('profiles', profile.id)
    connection = config.save('connections', ex.ConnectionWrite(name='Mock', baseUrl='http://never.test'))
    before = config.path.read_bytes()
    store = ProjectStore(tmp_path / 'projects')
    project = store.create_project(workspace_factory(aspectRatio=requested)); select(store, project)
    error = 'Aspect Ratio semantic conflict at 115 / aspect_ratio'
    with pytest.raises(ex.InvalidBindings, match=error) as caught:
        prepare_workflow(store, project.project_id, project.active_generation_id, profile)
    assert requested in str(caught.value) and wrong in str(caught.value)
    assert 'explicitly resolve' in str(caught.value)
    sent = []
    with pytest.raises(ex.InvalidBindings, match=error):
        from miniconstruct.execution_send import ExecutionSendRequest, send_generation
        from test_legacy_aspect_ratio import queue_transport
        send_generation(ExecutionSendRequest(projectId=project.project_id, generationId=project.active_generation_id,
            profileId=profile.id, connectionId=connection.id),
            store=store, config=config, transport=queue_transport(sent))
    assert sent == []
    assert config.path.read_bytes() == before


@pytest.mark.parametrize('label', ['Photo preset', '3:20preset', 'Portrait 4:3', '3:2.0'])
def test_renderer_specific_labels_preserve_manual_behavior_with_warning(tmp_path, workspace_factory, label):
    config, profile = modern_profile(tmp_path)
    profile.bindings[-1].source = ex.AspectRatioSource(kind='aspect_ratio',
        valueMap={**MAPPINGS, '3:2': label}, enumChoices=[*CHOICES, label])
    profile = config.save('profiles', ex.ProfileUpdate(name=profile.name, compatibleModes=profile.compatible_modes,
        bindings=profile.bindings, exposedControls=[]), profile.id)
    store = ProjectStore(tmp_path / 'projects')
    project = store.create_project(workspace_factory(aspectRatio='3:2')); select(store, project)
    prepared = prepare_workflow(store, project.project_id, project.active_generation_id, profile)
    assert prepared.graph['115']['inputs']['aspect_ratio'] == label
    assert any('Numeric ratio equivalence cannot be verified' in warning for warning in prepared.warnings)
    sent = []
    send(store, config, project, profile, prepared.execution_fingerprint, sent)
    assert sent[0]['prompt']['115']['inputs']['aspect_ratio'] == label
