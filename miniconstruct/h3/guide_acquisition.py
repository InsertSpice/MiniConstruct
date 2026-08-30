"""Obtain the official MiniMax H3 reference guides used at runtime.

The public MiniConstruct repository deliberately records provenance without
redistributing these third-party files. Downloads occur only when this module
is explicitly run. Application startup checks for the required files and
provides setup instructions if they are missing.
"""

from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable
from urllib.error import URLError
from urllib.request import urlopen

from miniconstruct.h3.guide_snapshot import GUIDE_SNAPSHOT, UPSTREAM_COMMIT, UPSTREAM_REPOSITORY


H3_ROOT = Path(__file__).resolve().parent
GUIDES_DIR = H3_ROOT / "guides"


@dataclass(frozen=True)
class GuideSpec:
    local_name: str
    upstream_path: str
    source_url: str
    sha256: str


def _source_url(upstream_path: str) -> str:
    return f"https://raw.githubusercontent.com/MiniMax-AI/MiniMax-H3/{UPSTREAM_COMMIT}/{upstream_path}"


GUIDE_SPECS = tuple(
    GuideSpec(local_name, upstream_path, _source_url(upstream_path), sha256)
    for local_name, upstream_path, sha256 in GUIDE_SNAPSHOT
)


class GuideAcquisitionError(RuntimeError):
    """An official reference guide is unavailable or was not authentic."""


def missing_guide_specs(
    guides_dir: Path = GUIDES_DIR, specs: Iterable[GuideSpec] = GUIDE_SPECS
) -> list[GuideSpec]:
    return [spec for spec in specs if not (guides_dir / spec.local_name).is_file()]


def guide_setup_message(paths: Iterable[Path]) -> str:
    names = "\n".join(f"  - {path}" for path in paths)
    return (
        "Required MiniMax H3 reference guides are missing:\n"
        f"{names}\n\n"
        "Run `python -m miniconstruct.h3.guide_acquisition` to download the exact "
        "official MiniMax snapshot, or manually place the official files at those paths.\n"
        f"Source: {UPSTREAM_REPOSITORY} commit {UPSTREAM_COMMIT}."
    )


def require_guides(guides_dir: Path = GUIDES_DIR) -> None:
    missing = missing_guide_specs(guides_dir)
    if missing:
        raise GuideAcquisitionError(guide_setup_message(guides_dir / spec.local_name for spec in missing))


def acquire_missing_guides(
    guides_dir: Path = GUIDES_DIR,
    specs: Iterable[GuideSpec] = GUIDE_SPECS,
    opener: Callable[..., object] = urlopen,
    timeout: float = 30.0,
) -> list[Path]:
    """Download only missing guides, preserving the exact verified response bytes."""
    specs = tuple(specs)
    missing = missing_guide_specs(guides_dir, specs)
    downloaded: list[Path] = []
    for spec in missing:
        target = guides_dir / spec.local_name
        try:
            with opener(spec.source_url, timeout=timeout) as response:
                data = response.read()
        except (OSError, TimeoutError, URLError) as exc:
            raise GuideAcquisitionError(
                f"Could not download required MiniMax H3 guide {spec.local_name} from {spec.source_url}: {exc}"
            ) from exc
        actual_hash = hashlib.sha256(data).hexdigest()
        if actual_hash != spec.sha256:
            raise GuideAcquisitionError(
                f"Downloaded MiniMax H3 guide {spec.local_name} did not match the recorded SHA-256 "
                f"for upstream commit {UPSTREAM_COMMIT}."
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        downloaded.append(target)
    return downloaded


def main() -> int:
    try:
        downloaded = acquire_missing_guides()
    except GuideAcquisitionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if downloaded:
        print("Downloaded official MiniMax H3 reference guides:")
        for path in downloaded:
            print(f"  {path}")
    else:
        print("Official MiniMax H3 reference guides are already present; nothing was downloaded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
