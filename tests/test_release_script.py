"""Real Git integration tests; every push targets a disposable local bare remote."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from tools import release as r
from tools.release_policy import ReleaseError, GUIDES, audit, digest, exclusion

ROOT = Path(__file__).resolve().parents[1]


def git(path, *args):
    return r.git(path, *args)


def commit(path, message='fixture'):
    git(path, 'add', '-f', '.')
    git(path, 'commit', '-qm', message)


def init(path, branch='master'):
    path.mkdir()
    git(path, 'init', '-q', '-b', branch)
    git(path, 'config', 'user.name', 'Release Tests')
    git(path, 'config', 'user.email', 'release@example.invalid')
    git(path, 'config', 'core.autocrlf', 'false')


def passed_checks(snapshot, output):
    # Integration fixtures have no installable app. The production CLI never
    # exposes this dependency injection; its fresh-install runner is exercised separately.
    assert not any((snapshot / name).exists() for name in GUIDES)
    return {name: {'passed': True} for name in r.CHECKS if name != 'tree_audit'}


@pytest.fixture
def repos(tmp_path):
    dev, staging, github, origin = [tmp_path / n for n in ('dev', 'staging', 'github', 'origin.git')]
    init(dev)
    init(staging, 'public-release')
    init(github)
    (staging / 'README.md').write_text('historic staging, preserved\n')
    commit(staging)
    (github / 'README.md').write_text('public baseline\n')
    commit(github)
    git(github, 'tag', '-a', 'v0.4.0', '-m', 'baseline')
    subprocess.run(['git', 'init', '--bare', '-q', str(origin)], check=True)
    git(github, 'remote', 'add', 'origin', str(origin))
    git(github, 'push', 'origin', 'master', 'v0.4.0')
    for folder in ('tools', 'release'):
        shutil.copytree(ROOT / folder, dev / folder, ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copy2(ROOT / 'release.ps1', dev / 'release.ps1')
    config = json.loads((dev / 'release/overlay.json').read_text())
    config.update(remote=str(origin), replace=[], append={'README.md': 'files/README-public.md'},
                  version_fields={'pyproject.toml': r'^(version = ")[^"\n]+(")$'})
    # Local bare-remote paths are test infrastructure, never public snapshot data.
    config['copy']['release/overlay.json'] = 'files/public-config.json'
    (dev / 'release/files/public-config.json').write_bytes(b'{}\n')
    r.save_json(dev / 'release/overlay.json', config)
    r.write_files(dev, {'README.md': b'modern development\n', 'pyproject.toml': b'version = "0.4.0"\n',
                        'miniconstruct/app.py': b'# modern app\n', '.gitignore': b'.local/\n.venv/\n'})
    for name in ('guide_snapshot.py', 'guides/provenance.json'):
        target = dev / 'miniconstruct/h3' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / 'miniconstruct/h3' / name, target)
    for path in GUIDES:
        (dev / path).write_bytes(b'official content must not be exported')
    commit(dev)
    return dev, staging, github, origin


def prepare(repos, path, **kwargs):
    return r.prepare(*repos[:3], path, validator=passed_checks, **kwargs)


def publish(path):
    return r.publish(path, confirm=lambda prompt: 'PUBLISH ' + json.loads((path / 'manifest.json').read_text())['tag'])


def states(repos):
    return [r.status(p) for p in repos[:3]]


def test_prepare_dry_run_and_exact_atomic_publication(repos, tmp_path):
    before = states(repos)
    preview = tmp_path / 'preview'
    dry = prepare(repos, preview, dry_run=True)
    assert states(repos) == before
    assert dry['status'] == 'verified' and not dry['publishable']
    with pytest.raises(ReleaseError, match='not publishable'):
        publish(preview)
    candidate = tmp_path / 'candidate'
    prepared = prepare(repos, candidate)
    assert prepared['version'] == '0.5.0'
    assert prepared['public_tree'] == dry['public_tree']
    assert states(repos) == before
    result = publish(candidate)
    refs = r.remote_refs(repos[2])
    assert refs['refs/heads/master'] == result['commit']
    assert refs['refs/tags/v0.5.0^{}'] == result['commit']
    assert git(repos[2], 'rev-parse', 'HEAD^{tree}') == prepared['public_tree']
    assert git(repos[2], 'rev-parse', 'HEAD^') == prepared['public_base']
    assert not any(p in r.read_tree(repos[2], 'HEAD')[0] for p in GUIDES)
    assert git(repos[0], 'rev-parse', 'HEAD') == before[0]['head']
    assert git(repos[1], 'rev-parse', 'HEAD') == before[1]['head']
    assert publish(candidate) == result  # lost acknowledgement / repeat is idempotent
    with pytest.raises(ReleaseError, match='newer than'):
        prepare(repos, tmp_path / 'repeat', version='0.5.0')


def test_successive_releases_keep_modern_readme_and_app(repos, tmp_path):
    first = tmp_path / 'first'
    prepare(repos, first)
    publish(first)
    dev = repos[0]
    (dev / 'README.md').write_text('new feature documentation\n')
    (dev / 'miniconstruct/app.py').write_text('# new route preserved\n')
    commit(dev)
    second = tmp_path / 'second'
    prepare(repos, second)
    assert (second / 'snapshot/miniconstruct/app.py').read_text() == '# new route preserved\n'
    assert (second / 'snapshot/README.md').read_text().startswith('new feature documentation')
    result = publish(second)
    assert r.remote_refs(repos[2])['refs/tags/v0.6.0^{}'] == result['commit']


@pytest.mark.parametrize('which', [0, 1, 2])
def test_dirty_repositories_are_preserved(repos, tmp_path, which):
    (repos[which] / 'README.md').write_text('preserve dirty work\n')
    before = states(repos)
    with pytest.raises(ReleaseError, match='uncommitted'):
        prepare(repos, tmp_path / 'candidate')
    assert states(repos) == before


def test_review_preview_is_real_but_never_publishable(repos, tmp_path):
    (repos[0] / 'README.md').write_text('review edits\n')
    before = states(repos)
    path = tmp_path / 'review'
    manifest = prepare(repos, path, dry_run=True, review=True)
    assert manifest['blockers'] and not manifest['publishable']
    assert (path / 'snapshot/README.md').read_text().startswith('review edits')
    assert states(repos) == before


@pytest.mark.parametrize('name', ['.venv/Lib/site.py', 'benchmarks/runs/result.json', 'benchmarks/send-local-staged.json',
                                 '.local/private/Hyperflow/workflow.json', 'Projects/project.json', 'web/node_modules/module.js'])
def test_tracked_private_and_generated_files_are_excluded(repos, tmp_path, name):
    r.write_files(repos[0], {name: b'private runtime bytes'})
    commit(repos[0])
    target = tmp_path / 'candidate'
    prepare(repos, target)
    assert name not in r.read_tree(target / 'snapshot', git(target / 'snapshot', 'write-tree'))[0]
    assert name in json.loads((target / 'inventory.json').read_text())['excluded']


@pytest.mark.parametrize('name,data,message', [
    ('personal-notes.txt', b'private', 'Unclassified'),
    ('tests/fixtures/api.json', ('{"api_key":"sk-' + 'A' * 32 + '"}').encode(), 'credential'),
    ('docs/account.md', ('path: C:' + '/' + 'Users/alice/private').encode(), 'personal absolute'),
    ('web/assets/unexpected.png', b'new image', 'unreviewed binary'),
    ('README.md', ('<' * 7 + ' ours\n').encode(), 'conflict marker'),
])
def test_fail_closed_content_audit(repos, tmp_path, name, data, message):
    r.write_files(repos[0], {name: data})
    commit(repos[0])
    with pytest.raises(ReleaseError, match=message):
        prepare(repos, tmp_path / 'candidate')


def test_overlay_conflict_leaves_repositories_intact(repos, tmp_path):
    path = repos[0] / 'release/overlay.json'
    config = json.loads(path.read_text())
    config['replace'] = [dict(path='miniconstruct/app.py', before='missing old anchor', after='new code')]
    r.save_json(path, config)
    commit(repos[0])
    before = states(repos)
    with pytest.raises(ReleaseError, match='Overlay conflict: miniconstruct/app.py'):
        prepare(repos, tmp_path / 'candidate')
    assert states(repos) == before


def test_public_only_readme_change_requires_integration(repos, tmp_path):
    (repos[2] / 'README.md').write_text('public-only editorial fix\n')
    commit(repos[2])
    git(repos[2], 'push', 'origin', 'master')
    with pytest.raises(ReleaseError, match='Public-only changes.*README.md'):
        prepare(repos, tmp_path / 'candidate')


def test_divergent_remote_before_prepare(repos, tmp_path):
    git(repos[2], 'commit', '--allow-empty', '-qm', 'unpublished local work')
    with pytest.raises(ReleaseError, match='remote branch differ'):
        prepare(repos, tmp_path / 'candidate')


@pytest.mark.parametrize('change,match', [('source', 'Source or staging'), ('overlay', 'Overlay/tool'),
                                         ('snapshot', 'Snapshot bytes'), ('extra', 'file set'),
                                         ('manifest', 'manifest changed'), ('identity', 'author identity'),
                                         ('remote', 'Unexpected origin'), ('index', 'index changed')])
def test_publish_rejects_changed_preparation(repos, tmp_path, change, match):
    target = tmp_path / 'candidate'
    prepare(repos, target)
    if change == 'source':
        (repos[0] / 'README.md').write_text('new source\n')
        commit(repos[0])
    elif change == 'overlay':
        with (repos[0] / 'release/files/README-public.md').open('a') as f:
            f.write('overlay changed\n')
    elif change in ('snapshot', 'extra'):
        (target / 'snapshot' / ('README.md' if change == 'snapshot' else 'extra.txt')).write_text('tampered\n')
    elif change == 'manifest':
        with (target / 'manifest.json').open('a') as f:
            f.write(' ')
    elif change == 'identity':
        git(repos[2], 'config', 'user.email', 'changed@example.invalid')
    elif change == 'remote':
        git(repos[2], 'remote', 'set-url', '--push', 'origin', str(tmp_path / 'wrong.git'))
    elif change == 'index':
        git(target / 'snapshot', 'read-tree', '--empty')
    before = r.remote_refs(repos[2])
    with pytest.raises(ReleaseError, match=match):
        publish(target)
    assert r.remote_refs(repos[2]) == before


def test_failed_tests_and_export_do_not_mutate_inputs(repos, tmp_path):
    before = states(repos)
    def fail(snapshot, output):
        raise ReleaseError('injected test failure')
    target = tmp_path / 'failed'
    with pytest.raises(ReleaseError, match='test failure'):
        r.prepare(*repos[:3], target, validator=fail)
    manifest = json.loads((target / 'manifest.json').read_text())
    assert manifest['status'] == 'blocked' and not manifest['publishable']
    assert states(repos) == before
    with pytest.raises(ReleaseError, match='already exists'):
        prepare(repos, target)
    assert states(repos) == before


def test_interrupted_merge_is_reported_and_preserved(repos, tmp_path):
    repo = repos[1]
    git(repo, 'checkout', '-qb', 'side')
    (repo / 'README.md').write_text('side\n')
    commit(repo)
    git(repo, 'checkout', 'public-release')
    (repo / 'README.md').write_text('main\n')
    commit(repo)
    with pytest.raises(ReleaseError):
        git(repo, 'merge', 'side')
    before = states(repos)
    with pytest.raises(ReleaseError, match='Interrupted Git operation'):
        prepare(repos, tmp_path / 'candidate')
    assert states(repos) == before
    assert git(repo, 'ls-files', '-u')


def test_existing_local_tag_is_a_blocker(repos, tmp_path):
    git(repos[0], 'tag', 'v0.5.0')
    with pytest.raises(ReleaseError, match='already exists'):
        prepare(repos, tmp_path / 'candidate')


def test_publish_confirmation_and_remote_race(repos, tmp_path):
    target = tmp_path / 'candidate'
    prepare(repos, target)
    before = states(repos)
    with pytest.raises(ReleaseError, match='Confirmation'):
        r.publish(target, confirm=lambda _: 'no')
    assert states(repos) == before
    # Advance the actual bare remote without changing the publication checkout.
    other = tmp_path / 'other'
    subprocess.run(['git', 'clone', '-q', str(repos[3]), str(other)], check=True)
    git(other, 'config', 'user.name', 'Other')
    git(other, 'config', 'user.email', 'other@example.invalid')
    git(other, 'commit', '--allow-empty', '-qm', 'remote advance')
    git(other, 'push', 'origin', 'master')
    with pytest.raises(ReleaseError, match='Remote branch diverged'):
        publish(target)


def test_actual_atomic_push_rejection_is_retryable(repos, tmp_path):
    target = tmp_path / 'candidate'
    prepare(repos, target)
    hook = repos[3] / 'hooks/pre-receive'
    hook.write_text('#!/bin/sh\nexit 1\n')
    hook.chmod(0o755)
    before = r.remote_refs(repos[2])
    with pytest.raises(ReleaseError, match='Command failed'):
        publish(target)
    assert r.remote_refs(repos[2]) == before
    hook.unlink()
    result = publish(target)
    assert r.remote_refs(repos[2])['refs/heads/master'] == result['commit']


def test_branch_only_partial_publication_recovers_same_objects(repos, tmp_path):
    target = tmp_path / 'candidate'
    prepare(repos, target)
    # Declining computes deterministic objects only in the disposable candidate.
    with pytest.raises(ReleaseError, match='Confirmation'):
        r.publish(target, confirm=lambda _: 'no')
    # Trigger a real rejected push to retain the exact release refs in the candidate.
    hook = repos[3] / 'hooks/pre-receive'
    hook.write_text('#!/bin/sh\nexit 1\n')
    hook.chmod(0o755)
    with pytest.raises(ReleaseError):
        publish(target)
    hook.unlink()
    snapshot = target / 'snapshot'
    expected = git(snapshot, 'rev-parse', 'release')
    git(snapshot, 'push', str(repos[3]), 'release:refs/heads/master')
    result = publish(target)
    assert result['commit'] == expected


def test_policy_protects_required_guides_and_unknown_git_entries():
    config = json.loads((ROOT / 'release/overlay.json').read_text())
    for guide in GUIDES:
        assert exclusion(guide, config)
        with pytest.raises(ReleaseError, match='excluded path'):
            audit({guide: b'guide'}, config)
    for name in ('../escape', '.git/config', 'C:/escape', 'tests/../escape'):
        with pytest.raises(ReleaseError, match='Unsafe'):
            exclusion(name, config)


@pytest.mark.parametrize('runtime', ['pwsh', 'powershell'])
def test_powershell_parser_and_execution(runtime, tmp_path):
    exe = shutil.which(runtime)
    if not exe:
        pytest.skip(f'{runtime} not available')
    source = ROOT / 'release.ps1'
    result = subprocess.run([exe, '-NoProfile', '-Command',
        '& { $tokens=$null; $errors=$null; [void][System.Management.Automation.Language.Parser]::ParseFile($args[0],[ref]$tokens,[ref]$errors); if($errors.Count){ $errors; exit 1 } }',
        str(source)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    result = subprocess.run([exe, '-NoProfile', '-File', str(source), '-Operation', 'Prepare',
        '-DevRepo', str(tmp_path / 'absent'), '-PythonExe', sys.executable, '-DryRun'], capture_output=True, text=True)
    assert result.returncode != 0 and 'RELEASE BLOCKED' in result.stderr
    result = subprocess.run([exe, '-NoProfile', '-File', str(source), '-Operation', 'Publish',
        '-PythonExe', sys.executable, '-DryRun'], capture_output=True, text=True)
    assert result.returncode != 0 and 'requires -Manifest' in result.stderr


def test_missing_guides_in_source_are_supported(repos, tmp_path):
    for name in GUIDES:
        (repos[0] / name).unlink()
    commit(repos[0])
    result = prepare(repos, tmp_path / 'candidate')
    assert result['status'] == 'verified'


def test_changed_source_during_confirmation_is_rejected(repos, tmp_path):
    target = tmp_path / 'candidate'
    prepare(repos, target)
    before = r.remote_refs(repos[2])

    def confirm(_):
        (repos[0] / 'README.md').write_text('user edited during review\n')
        return 'PUBLISH v0.5.0'

    with pytest.raises(ReleaseError, match='uncommitted'):
        r.publish(target, confirm=confirm)
    assert r.remote_refs(repos[2]) == before


def test_changed_asset_bytes_are_blocked(repos, tmp_path):
    cfg_path = repos[0] / 'release/overlay.json'
    cfg = json.loads(cfg_path.read_text())
    cfg['binary_sha256']['web/assets/example.png'] = digest(b'reviewed image')
    r.save_json(cfg_path, cfg)
    r.write_files(repos[0], {'web/assets/example.png': b'changed image'})
    commit(repos[0])
    with pytest.raises(ReleaseError, match='changed asset hash'):
        prepare(repos, tmp_path / 'candidate')


def test_png_metadata_sanitization_preserves_pixels_and_source():
    import io
    from PIL import Image
    from tools.release_policy import strip_png_metadata
    name = 'benchmarks/environment-consistency/assets/anime-bedroom.png'
    original = (ROOT / name).read_bytes()
    # Public snapshots already contain the sanitized asset; both inputs are idempotent.
    sanitized = strip_png_metadata(original)
    assert strip_png_metadata(sanitized) == sanitized
    with Image.open(io.BytesIO(original)) as old, Image.open(io.BytesIO(sanitized)) as new:
        assert old.size == new.size and old.mode == new.mode
        assert old.tobytes() == new.tobytes()
        assert not {'prompt', 'workflow', 'exif'} & set(new.info)
    assert (ROOT / name).read_bytes() == original


def test_embedded_png_metadata_is_rejected_without_transformation():
    import struct
    import zlib
    from tools.release_policy import strip_png_metadata
    name = 'web/assets/example.png'
    original = (ROOT / 'web/assets/favicon.png').read_bytes()
    text = b'workflow\0private test metadata'
    body = b'tEXt' + text
    chunk = struct.pack('>I', len(text)) + body + struct.pack('>I', zlib.crc32(body))
    embedded = original[:-12] + chunk + original[-12:]
    with pytest.raises(ReleaseError, match='embedded PNG'):
        audit({name: embedded}, {'binary_sha256': {name: digest(embedded)}})
    assert strip_png_metadata(embedded) == original


def test_linked_staging_worktree_migration_preserves_branches(repos, tmp_path):
    linked = tmp_path / 'linked-staging'
    git(repos[0], 'worktree', 'add', '-b', 'public-release', str(linked), 'HEAD')
    (linked / 'README.md').write_text('historic public overlay on a linked worktree\n')
    commit(linked)
    layout = (repos[0], linked, repos[2], repos[3])
    before = states(layout)
    manifest = prepare(layout, tmp_path / 'candidate', dry_run=True)
    assert manifest['status'] == 'verified'
    assert states(layout) == before
    assert (linked / 'README.md').read_text().startswith('historic public overlay')


def test_staging_artifact_directory_does_not_dirty_staging(repos):
    before = states(repos)
    prepare(repos, repos[1] / '.release-artifacts' / 'candidate', dry_run=True)
    assert states(repos) == before
