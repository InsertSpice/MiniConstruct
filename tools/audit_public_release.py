"""Deterministic path-level audit for a MiniConstruct public-release snapshot."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path


FORBIDDEN_GUIDES = (
    Path("miniconstruct/h3/guides/SKILL.md"),
    Path("miniconstruct/h3/guides/base-en.txt"),
    Path("miniconstruct/h3/guides/ref-en.txt"),
)
REQUIRED_PUBLIC_FILES = (
    Path(".gitattributes"),
    Path(".gitignore"),
    Path("LICENSE"),
    Path("THIRD_PARTY_NOTICES.md"),
    Path("README.md"),
    Path("miniconstruct/h3/guide_acquisition.py"),
    Path("miniconstruct/h3/guide_snapshot.py"),
    Path("miniconstruct/h3/guides/provenance.json"),
    Path("miniconstruct/h3/operating/miniconstruct.md"),
)


class PublicReleaseAuditError(RuntimeError):
    """The tree cannot be distributed as a MiniConstruct public snapshot."""


def _expected_snapshot(root: Path) -> tuple[str, dict[str, tuple[str, str]]]:
    """Load metadata directly, without importing the application package."""
    module_path = root / "miniconstruct/h3/guide_snapshot.py"
    spec = importlib.util.spec_from_file_location("public_release_guide_snapshot", module_path)
    if spec is None or spec.loader is None:
        raise PublicReleaseAuditError("Could not load pinned guide snapshot metadata.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        commit = module.UPSTREAM_COMMIT
        snapshot = module.GUIDE_SNAPSHOT
        expected = {name: (upstream_path, sha256) for name, upstream_path, sha256 in snapshot}
    except (AttributeError, TypeError, ValueError) as exc:
        raise PublicReleaseAuditError(f"Pinned guide snapshot metadata is malformed: {exc}") from exc
    if not isinstance(commit, str) or not commit or len(expected) != len(snapshot):
        raise PublicReleaseAuditError("Pinned guide snapshot metadata is malformed.")
    return commit, expected


def _audit_provenance(root: Path) -> None:
    expected_commit, expected_files = _expected_snapshot(root)
    try:
        provenance = json.loads((root / "miniconstruct/h3/guides/provenance.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PublicReleaseAuditError(f"Could not read guide provenance: {exc}") from exc
    if not isinstance(provenance, dict) or provenance.get("upstreamCommit") != expected_commit:
        raise PublicReleaseAuditError("Guide provenance has an unexpected upstream commit.")
    entries = provenance.get("files")
    if not isinstance(entries, list):
        raise PublicReleaseAuditError("Guide provenance files must be a list.")
    actual: dict[str, tuple[str, str, str]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise PublicReleaseAuditError("Guide provenance contains a malformed file entry.")
        local = entry.get("local")
        upstream_path = entry.get("upstreamPath")
        source_url = entry.get("sourceUrl")
        sha256 = entry.get("sha256")
        if not all(isinstance(value, str) and value for value in (local, upstream_path, source_url, sha256)):
            raise PublicReleaseAuditError("Guide provenance contains a malformed file entry.")
        if local in actual:
            raise PublicReleaseAuditError(f"Guide provenance duplicates {local}.")
        actual[local] = (upstream_path, source_url, sha256)
    if set(actual) != set(expected_files):
        raise PublicReleaseAuditError("Guide provenance does not contain exactly the expected guide files.")
    for local, (upstream_path, sha256) in expected_files.items():
        actual_path, actual_url, actual_hash = actual[local]
        expected_url = f"https://raw.githubusercontent.com/MiniMax-AI/MiniMax-H3/{expected_commit}/{upstream_path}"
        if (actual_path, actual_url, actual_hash) != (upstream_path, expected_url, sha256):
            raise PublicReleaseAuditError(f"Guide provenance does not match the pinned snapshot for {local}.")


def audit_tree(root: Path) -> None:
    """Raise a concise error when required public-release paths are invalid."""
    root = root.resolve()
    forbidden = [path for path in FORBIDDEN_GUIDES if (root / path).exists()]
    if forbidden:
        raise PublicReleaseAuditError(
            "Forbidden MiniMax guide path(s) are present: " + ", ".join(str(path) for path in forbidden)
        )
    missing = [path for path in REQUIRED_PUBLIC_FILES if not (root / path).is_file()]
    if missing:
        raise PublicReleaseAuditError(
            "Required public-release path(s) are missing: " + ", ".join(str(path) for path in missing)
        )
    _audit_provenance(root)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit a MiniConstruct public-release tree by path and provenance.")
    parser.add_argument("path", nargs="?", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        audit_tree(args.path)
    except PublicReleaseAuditError as exc:
        print(f"PUBLIC RELEASE AUDIT FAILED: {exc}")
        return 1
    print(f"PUBLIC RELEASE AUDIT PASSED: {args.path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
