from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from miniconstruct.h3.guide_snapshot import GUIDE_SNAPSHOT, UPSTREAM_COMMIT
from tools.audit_public_release import FORBIDDEN_GUIDES, REQUIRED_PUBLIC_FILES, PublicReleaseAuditError, audit_tree
from tools.export_public_snapshot import PublicSnapshotExportError, _extract_archive, _head_files, _safe_destination_path, export_snapshot


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def expected_provenance():
    return {
        "upstreamCommit": UPSTREAM_COMMIT,
        "files": [
            {
                "local": local,
                "upstreamPath": upstream_path,
                "sourceUrl": f"https://raw.githubusercontent.com/MiniMax-AI/MiniMax-H3/{UPSTREAM_COMMIT}/{upstream_path}",
                "sha256": sha256,
            }
            for local, upstream_path, sha256 in GUIDE_SNAPSHOT
        ],
    }


def make_public_tree(root):
    for path in REQUIRED_PUBLIC_FILES:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.name == "provenance.json":
            target.write_text(json.dumps(expected_provenance()), encoding="utf-8")
        elif target.name == "guide_snapshot.py":
            shutil.copyfile(PROJECT_ROOT / path, target)
        else:
            target.write_text("placeholder", encoding="utf-8")


def test_audit_accepts_minimum_public_release_paths(tmp_path):
    make_public_tree(tmp_path)
    audit_tree(tmp_path)


def test_audit_rejects_a_forbidden_guide(tmp_path):
    make_public_tree(tmp_path)
    forbidden = tmp_path / FORBIDDEN_GUIDES[0]
    forbidden.parent.mkdir(parents=True, exist_ok=True)
    forbidden.write_text("not guide contents", encoding="utf-8")
    with pytest.raises(PublicReleaseAuditError, match="Forbidden MiniMax guide"):
        audit_tree(tmp_path)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda provenance: provenance.update(upstreamCommit="wrong"), "unexpected upstream commit"),
        (lambda provenance: provenance["files"].__setitem__(0, {**provenance["files"][0], "sha256": "wrong"}), "pinned snapshot"),
        (lambda provenance: provenance["files"].pop(), "exactly the expected guide files"),
        (lambda provenance: provenance.update(files=[{}]), "malformed file entry"),
    ],
)
def test_audit_rejects_invalid_provenance(tmp_path, change, message):
    make_public_tree(tmp_path)
    provenance_path = tmp_path / "miniconstruct/h3/guides/provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    change(provenance)
    provenance_path.write_text(json.dumps(provenance), encoding="utf-8")
    with pytest.raises(PublicReleaseAuditError, match=message):
        audit_tree(tmp_path)


def test_audit_rejects_missing_required_file(tmp_path):
    make_public_tree(tmp_path)
    (tmp_path / "LICENSE").unlink()
    with pytest.raises(PublicReleaseAuditError, match="Required public-release"):
        audit_tree(tmp_path)


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)


def init_repo(root):
    root.mkdir()
    git(root, "init")
    git(root, "config", "user.email", "tests@example.invalid")
    git(root, "config", "user.name", "Release Tool Tests")


def commit_all(root, message):
    git(root, "add", ".")
    git(root, "commit", "-m", message)


def make_committed_public_source(root):
    init_repo(root)
    make_public_tree(root)
    (root / "source.txt").write_text("committed source", encoding="utf-8")
    (root / "added.txt").write_text("new", encoding="utf-8")
    commit_all(root, "public source")


def make_committed_destination(root):
    init_repo(root)
    (root / "destination-base.txt").write_text("base", encoding="utf-8")
    commit_all(root, "destination base")


def test_export_mirrors_committed_snapshot_without_committing_destination(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "destination"
    make_committed_public_source(source)
    make_committed_destination(destination)
    (destination / "stale.txt").write_text("remove", encoding="utf-8")
    commit_all(destination, "stale file")
    destination_head = git(destination, "rev-parse", "HEAD").stdout.strip()

    export_snapshot(source, destination)

    assert (destination / "source.txt").read_text(encoding="utf-8") == "committed source"
    assert (destination / "added.txt").is_file()
    assert not (destination / "stale.txt").exists()
    assert (destination / ".git" / "HEAD").is_file()
    assert git(destination, "rev-parse", "HEAD").stdout.strip() == destination_head
    assert git(destination, "status", "--porcelain").stdout


def test_export_rejects_dirty_source_and_destination(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "destination"
    make_committed_public_source(source)
    make_committed_destination(destination)
    (source / "source.txt").write_text("dirty", encoding="utf-8")
    with pytest.raises(PublicSnapshotExportError, match="source.*uncommitted"):
        export_snapshot(source, destination)
    (source / "source.txt").write_text("committed source", encoding="utf-8")
    (destination / "dirty.txt").write_text("dirty", encoding="utf-8")
    with pytest.raises(PublicSnapshotExportError, match="destination.*uncommitted"):
        export_snapshot(source, destination)


def test_export_rejects_forbidden_source_snapshot(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "destination"
    make_committed_public_source(source)
    make_committed_destination(destination)
    forbidden = source / FORBIDDEN_GUIDES[0]
    forbidden.parent.mkdir(parents=True, exist_ok=True)
    forbidden.write_text("forbidden fixture", encoding="utf-8")
    commit_all(source, "invalid source")
    with pytest.raises(PublicReleaseAuditError, match="Forbidden MiniMax guide"):
        export_snapshot(source, destination)


def test_archive_export_uses_committed_head_not_dirty_source_file(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "destination"
    make_committed_public_source(source)
    init_repo(destination)
    (source / "source.txt").write_text("uncommitted", encoding="utf-8")
    _extract_archive(source, destination, _head_files(source))
    assert (destination / "source.txt").read_text(encoding="utf-8") == "committed source"


def test_unsafe_export_paths_cannot_reach_destination_git(tmp_path):
    destination = tmp_path / "destination"
    destination.mkdir()
    with pytest.raises(PublicSnapshotExportError, match="Unsafe snapshot path"):
        _safe_destination_path(destination.resolve(), Path(".git/HEAD"))
    with pytest.raises(PublicSnapshotExportError, match="Unsafe snapshot path"):
        _safe_destination_path(destination.resolve(), Path("../outside"))
