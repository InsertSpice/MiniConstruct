from __future__ import annotations

from pathlib import Path


SETUP = Path(__file__).resolve().parents[1] / "setup-miniconstruct.bat"


def setup_text() -> str:
    return SETUP.read_text(encoding="utf-8")


def test_windows_setup_is_repository_relative_and_uses_no_hardcoded_checkout():
    text = setup_text()
    assert SETUP.is_file()
    assert 'set "REPO_DIR=%~dp0"' in text
    assert "I:\\AI-Models\\MiniConstruct" not in text
    assert 'pushd "%REPO_DIR%"' in text


def test_windows_setup_uses_venv_python_for_install_and_guides():
    text = setup_text()
    assert '"%VENV_PY%" -m pip install -e .' in text
    assert '"%VENV_PY%" -m miniconstruct.h3.guide_acquisition' in text
    assert '"%VENV_PY%" -c "from miniconstruct.h3.guide_acquisition import require_guides; require_guides()"' in text
    assert "python -m pip" not in text


def test_windows_setup_reuses_broken_venv_without_destructive_replacement():
    text = setup_text()
    assert 'if exist "%VENV_DIR%\\."' in text
    assert "It was not deleted or replaced" in text
    assert "rmdir" not in text.lower()
    assert "--clear" not in text


def test_windows_setup_verifies_python_before_selection_and_reports_requirement():
    text = setup_text()
    assert 'py -3.12 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)"' in text
    assert 'py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)"' in text
    assert text.index("py -3.12 -c") < text.index("py -3 -c") < text.index("python -c")
    assert 'python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)"' in text
    assert "Python 3.12 or newer was not found" in text
    assert "Microsoft Store" in text


def test_windows_setup_delegates_launch_to_run_ps1():
    text = setup_text()
    assert 'powershell -NoProfile -ExecutionPolicy Bypass -File "%REPO_DIR%\\run.ps1"' in text
    assert "Launch MiniConstruct now? [Y/n]" in text
