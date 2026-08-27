from __future__ import annotations

import hashlib
import io
import json
import subprocess
from pathlib import Path
from urllib.error import URLError

import pytest

from miniconstruct.h3 import builder
from miniconstruct.h3.guide_acquisition import (
    GUIDE_SPECS,
    GuideAcquisitionError,
    GuideSpec,
    acquire_missing_guides,
    require_guides,
)


def spec(name: str, path: str, data: bytes) -> GuideSpec:
    return GuideSpec(name, path, f"https://raw.githubusercontent.com/MiniMax-AI/MiniMax-H3/revision/{path}", hashlib.sha256(data).hexdigest())


def test_existing_guides_need_no_network(tmp_path):
    data = b"official guide bytes"
    guide = spec("base-en.txt", "skills/h3-prompt-writing/references/base-en.txt", data)
    (tmp_path / guide.local_name).write_bytes(data)

    def no_network(*args, **kwargs):
        raise AssertionError("network access was not expected")

    assert acquire_missing_guides(tmp_path, [guide], no_network) == []


def test_missing_guides_are_downloaded_to_expected_paths(tmp_path):
    base, ref = b"base guide", b"reference guide"
    specs = [
        spec("base-en.txt", "skills/h3-prompt-writing/references/base-en.txt", base),
        spec("ref-en.txt", "skills/h3-prompt-writing/references/ref-en.txt", ref),
    ]
    responses = iter([io.BytesIO(base), io.BytesIO(ref)])
    urls: list[str] = []

    def opener(url, *, timeout):
        urls.append(url)
        return next(responses)

    downloaded = acquire_missing_guides(tmp_path, specs, opener)
    assert downloaded == [tmp_path / "base-en.txt", tmp_path / "ref-en.txt"]
    assert (tmp_path / "base-en.txt").read_bytes() == base
    assert (tmp_path / "ref-en.txt").read_bytes() == ref
    assert urls == [item.source_url for item in specs]


def test_configured_sources_are_official_minimax_urls():
    for guide in GUIDE_SPECS:
        assert guide.source_url.startswith("https://raw.githubusercontent.com/MiniMax-AI/MiniMax-H3/")
        assert guide.upstream_path in guide.source_url


def test_pinned_hashes_describe_raw_lf_bytes_not_crlf_working_tree_bytes():
    # The expected hashes come from the pinned raw GitHub objects. A Windows
    # checkout that expands LF to CRLF must not be used to regenerate them.
    expected_raw_hashes = {
        "SKILL.md": "a7000443588ca3f145e3b3fd8900f14e0325dc460bd811268fac89a9dc8e56d0",
        "base-en.txt": "2cfebc096a6e08370f288d468d90b60f7f9bcb938f94bf090816e910e48e75fc",
        "ref-en.txt": "1e574f356716ad55612247ffb7bbccbcdb484ad96599d63c7dca1af186b1fab7",
    }
    assert {guide.local_name: guide.sha256 for guide in GUIDE_SPECS} == expected_raw_hashes
    assert hashlib.sha256(b"canonical\nupstream\nbytes\n").hexdigest() != hashlib.sha256(
        b"canonical\r\nupstream\r\nbytes\r\n"
    ).hexdigest()


def test_provenance_hashes_match_the_download_configuration():
    root = Path(__file__).resolve().parents[1]
    provenance = json.loads((root / "miniconstruct/h3/guides/provenance.json").read_text(encoding="utf-8"))
    provenance_hashes = {item["local"]: item["sha256"] for item in provenance["files"]}
    assert provenance_hashes == {guide.local_name: guide.sha256 for guide in GUIDE_SPECS}


def test_upstream_failure_is_clear(tmp_path):
    guide = spec("base-en.txt", "skills/h3-prompt-writing/references/base-en.txt", b"guide")

    def failing(*args, **kwargs):
        raise URLError("offline")

    with pytest.raises(GuideAcquisitionError, match="Could not download required MiniMax H3 guide base-en.txt"):
        acquire_missing_guides(tmp_path, [guide], failing)


def test_partially_missing_sets_download_only_the_missing_file(tmp_path):
    base, ref = b"base", b"ref"
    base_spec = spec("base-en.txt", "skills/h3-prompt-writing/references/base-en.txt", base)
    ref_spec = spec("ref-en.txt", "skills/h3-prompt-writing/references/ref-en.txt", ref)
    (tmp_path / base_spec.local_name).write_bytes(base)
    downloaded = acquire_missing_guides(tmp_path, [base_spec, ref_spec], lambda *args, **kwargs: io.BytesIO(ref))
    assert downloaded == [tmp_path / "ref-en.txt"]
    assert (tmp_path / base_spec.local_name).read_bytes() == base


def test_existing_file_is_not_overwritten(tmp_path):
    guide = spec("base-en.txt", "skills/h3-prompt-writing/references/base-en.txt", b"expected")
    target = tmp_path / guide.local_name
    target.write_bytes(b"manually supplied official guide")
    assert acquire_missing_guides(tmp_path, [guide], lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError())) == []
    assert target.read_bytes() == b"manually supplied official guide"


def test_acquired_guide_bytes_are_used_by_prompt_assembly(tmp_path, monkeypatch, workspace_factory, image_asset_factory):
    base, ref = b"BASE GUIDE EXACT", b"REF GUIDE EXACT"
    base_spec = spec("base-en.txt", "skills/h3-prompt-writing/references/base-en.txt", base)
    ref_spec = spec("ref-en.txt", "skills/h3-prompt-writing/references/ref-en.txt", ref)
    acquire_missing_guides(tmp_path, [base_spec, ref_spec], lambda url, **kwargs: io.BytesIO(base if url == base_spec.source_url else ref))
    monkeypatch.setattr(builder, "GUIDES", tmp_path)
    builder._read_text.cache_clear()
    assert "BASE GUIDE EXACT" in builder.assemble_prompt(workspace_factory(), False).inspector_text
    assert "REF GUIDE EXACT" in builder.assemble_prompt(
        workspace_factory(mode="Ref2VA", assets=[image_asset_factory()]), False
    ).inspector_text


def test_missing_required_guides_have_actionable_message(tmp_path):
    with pytest.raises(GuideAcquisitionError, match="python -m miniconstruct.h3.guide_acquisition"):
        require_guides(tmp_path)


def test_downloaded_guides_are_ignored_but_operating_instructions_are_tracked():
    root = Path(__file__).resolve().parents[1]
    ignored = (root / ".gitignore").read_text(encoding="utf-8")
    for guide in GUIDE_SPECS:
        assert f"/miniconstruct/h3/guides/{guide.local_name}" in ignored
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "miniconstruct/h3/operating/miniconstruct.md"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert tracked.returncode == 0
    attributes = (root / ".gitattributes").read_text(encoding="utf-8")
    for guide in GUIDE_SPECS:
        assert f"/miniconstruct/h3/guides/{guide.local_name} -text" in attributes


def test_windows_launcher_uses_the_repository_venv_and_loopback():
    root = Path(__file__).resolve().parents[1]
    launcher = (root / "start-miniconstruct.bat").read_text(encoding="utf-8")
    assert 'cd /d "%~dp0"' in launcher
    assert 'if not exist ".venv\\Scripts\\python.exe"' in launcher
    assert '".venv\\Scripts\\python.exe" -m miniconstruct --host 127.0.0.1 --port 8743' in launcher
    assert "Press Ctrl+C to stop MiniConstruct." in launcher
    assert "pause" in launcher and "exit /b %EXITCODE%" in launcher
