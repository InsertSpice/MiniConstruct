"""Export the committed public-release tree into a clean public Git worktree."""

from __future__ import annotations

import argparse
import io
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

try:
    from tools.audit_public_release import PublicReleaseAuditError, audit_tree
except ModuleNotFoundError:  # Direct execution: python tools/export_public_snapshot.py
    from audit_public_release import PublicReleaseAuditError, audit_tree


SOURCE_ROOT = Path(__file__).resolve().parents[1]


class PublicSnapshotExportError(RuntimeError):
    """An export precondition or safe snapshot operation failed."""


def _git(root: Path, *args: str, text: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], check=False, capture_output=True, text=text)


def _require_clean_repository(root: Path, label: str) -> None:
    inside = _git(root, "rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise PublicSnapshotExportError(f"{label} is not an existing Git working tree: {root}")
    status = _git(root, "status", "--porcelain")
    if status.returncode != 0:
        raise PublicSnapshotExportError(f"Could not inspect {label} status: {status.stderr.strip()}")
    if status.stdout:
        raise PublicSnapshotExportError(f"Refusing to export because {label} has uncommitted changes: {root}")


def _head_files(root: Path) -> set[Path]:
    listing = _git(root, "ls-tree", "-r", "-z", "--name-only", "HEAD", text=False)
    if listing.returncode != 0:
        raise PublicSnapshotExportError(f"Could not list committed source files: {listing.stderr.decode(errors='replace').strip()}")
    return {Path(item.decode("utf-8")) for item in listing.stdout.split(b"\0") if item}


def _safe_destination_path(destination: Path, relative: Path) -> Path:
    candidate = (destination / relative).resolve()
    if candidate == destination or destination not in candidate.parents or ".git" in relative.parts:
        raise PublicSnapshotExportError(f"Unsafe snapshot path: {relative}")
    return candidate


def _remove_stale_tracked_files(destination: Path, source_files: set[Path]) -> None:
    tracked = _git(destination, "ls-files", "-z", text=False)
    if tracked.returncode != 0:
        raise PublicSnapshotExportError(f"Could not list destination files: {tracked.stderr.decode(errors='replace').strip()}")
    for item in tracked.stdout.split(b"\0"):
        if not item:
            continue
        relative = Path(item.decode("utf-8"))
        if relative not in source_files:
            target = _safe_destination_path(destination, relative)
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            elif target.exists() or target.is_symlink():
                target.unlink()


def _extract_archive(source: Path, destination: Path, source_files: set[Path]) -> None:
    archive = _git(source, "archive", "--format=tar", "HEAD", text=False)
    if archive.returncode != 0:
        raise PublicSnapshotExportError(f"Could not archive public source HEAD: {archive.stderr.decode(errors='replace').strip()}")
    with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as tar:
        for member in tar.getmembers():
            relative = Path(member.name)
            target = _safe_destination_path(destination, relative)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile() or relative not in source_files:
                raise PublicSnapshotExportError(f"Archive contains unsupported or unexpected entry: {member.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            extracted = tar.extractfile(member)
            if extracted is None:
                raise PublicSnapshotExportError(f"Could not read archive entry: {member.name}")
            target.write_bytes(extracted.read())
            os.chmod(target, member.mode)


def export_snapshot(source: Path, destination: Path) -> None:
    source = source.resolve()
    destination = destination.resolve()
    _require_clean_repository(source, "source public staging repository")
    _require_clean_repository(destination, "destination public repository")
    audit_tree(source)
    source_files = _head_files(source)
    _remove_stale_tracked_files(destination, source_files)
    _extract_archive(source, destination, source_files)
    try:
        audit_tree(destination)
    except PublicReleaseAuditError as exc:
        raise PublicSnapshotExportError(f"Exported snapshot failed audit: {exc}") from exc
    print(f"Exported committed public snapshot from {source} to {destination}.")
    print("No commit or push was performed. Inspect `git status` and `git diff` in the destination.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Export committed MiniConstruct-public files into a clean public Git worktree.")
    parser.add_argument("destination", type=Path, help="Existing clean public Git working tree to update.")
    args = parser.parse_args()
    try:
        export_snapshot(SOURCE_ROOT, args.destination)
    except (PublicSnapshotExportError, PublicReleaseAuditError) as exc:
        print(f"PUBLIC SNAPSHOT EXPORT FAILED: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
