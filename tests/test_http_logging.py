from __future__ import annotations

import json
import logging
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient
import uvicorn

from miniconstruct import __main__ as cli
from miniconstruct.http_logging import HTTPErrorLogging, quiet_app
from tools.release import replace_once

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("verbose", [False, True])
@pytest.mark.parametrize("options", [[], ["--host", "0.0.0.0", "--port", "9123", "--reload"]])
def test_cli(monkeypatch, verbose, options):
    run = Mock()
    monkeypatch.setattr(cli.uvicorn, "run", run)
    monkeypatch.setattr(sys, "argv", ["miniconstruct", *options, *(["--verbose-http"] if verbose else [])])
    cli.main()
    run.assert_called_once_with(
        "miniconstruct.app:app" if verbose else "miniconstruct.http_logging:quiet_app",
        host="0.0.0.0" if options else "127.0.0.1", port=9123 if options else 8743,
        reload=bool(options), factory=not verbose, access_log=verbose,
    )


@pytest.mark.parametrize("verbose", [False, True])
def test_real_uvicorn_logging(verbose, caplog, monkeypatch):
    async def app(scope, receive, send):
        path = scope["path"]
        if path == "/raise":
            raise RuntimeError("diagnostic sentinel")
        status = int(path.strip("/")) if path != "/stream" else 200
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": b"" if status == 304 else b"first", "more_body": True})
        if path == "/stream":
            await send({"type": "http.response.body", "body": b"second", "more_body": True})
        await send({"type": "http.response.body", "body": b""})

    caplog.set_level(logging.INFO)
    access_logger = logging.getLogger("uvicorn.access")
    monkeypatch.setattr(access_logger, "handlers", [caplog.handler])
    monkeypatch.setattr(access_logger, "propagate", False)
    config = uvicorn.Config(app if verbose else HTTPErrorLogging(app), access_log=verbose,
                            log_config=None, lifespan="off", http="h11")
    server = uvicorn.Server(config)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert server.started
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False) as client:
                for status in (200, 304, 404, 422, 503):
                    response = client.get(f"/{status}?token=secret-query", headers={"Authorization": "secret-header"})
                    assert response.status_code == status
                    assert response.content == (b"" if status == 304 else b"first")
                assert client.get("/stream").content == b"firstsecond"
                assert client.get("/raise").status_code == 500
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive()

    access = [r for r in caplog.records if r.name == "uvicorn.access"]
    errors = [r for r in caplog.records if r.name == "uvicorn.error"]
    if verbose:
        assert {r.args[-1] for r in access} >= {200, 304, 404, 422, 503, 500}
        assert not any(r.getMessage().startswith("HTTP ") for r in errors)
    else:
        assert not access
        diagnostics = [r for r in errors if r.getMessage().startswith("HTTP ")]
        assert [r.levelno for r in diagnostics] == [logging.WARNING, logging.WARNING, logging.ERROR]
        assert [r.args[-1] for r in diagnostics] == [404, 422, 503]
        assert "secret-query" not in " ".join(r.getMessage() for r in errors)
        assert "secret-header" not in " ".join(r.getMessage() for r in errors)
    exceptions = [r for r in errors if r.exc_info]
    assert len(exceptions) == 1
    assert str(exceptions[0].exc_info[1]) == "diagnostic sentinel"
    assert "Started server process" in caplog.text
    assert "Finished server process" in caplog.text


async def test_asgi_passthrough_and_stream_exception(caplog):
    messages = [{"type": "http.response.start", "status": 503, "headers": []},
                {"type": "http.response.body", "body": b"chunk", "more_body": True}]
    sent = []

    async def send(message):
        sent.append(message)

    async def app(scope, receive, send):
        for message in messages:
            await send(message)
        raise RuntimeError("broken stream")

    with pytest.raises(RuntimeError, match="broken stream"):
        await HTTPErrorLogging(app)({"type": "http"}, None, send)
    assert sent == messages
    assert not caplog.records

    async def non_http(scope, receive, send_arg):
        assert scope["type"] in {"lifespan", "websocket"}
        assert send_arg is send

    for kind in ("lifespan", "websocket"):
        await HTTPErrorLogging(non_http)({"type": kind}, None, send)


def test_application_responses_unchanged():
    from miniconstruct.app import app

    with TestClient(app) as normal, TestClient(quiet_app()) as quiet:
        for path in ("/api/health", "/", "/static/js/app.js", "/api/does-not-exist"):
            expected, actual = normal.get(path), quiet.get(path)
            assert (actual.status_code, actual.content, actual.headers) == (
                expected.status_code, expected.content, expected.headers)


@pytest.mark.parametrize("verbose", [False, True])
def test_public_cli_overlay(monkeypatch, verbose):
    import miniconstruct.h3.guide_acquisition as guides

    config = json.loads((ROOT / "release/overlay.json").read_text())
    path = "miniconstruct/__main__.py"
    files = {path: (ROOT / path).read_bytes().replace(b"\r\n", b"\n")}
    # Public snapshots already contain the overlay. Exercise those actual bytes;
    # development runs still exercise the strict CLI transformations themselves.
    if b"from miniconstruct.h3.guide_acquisition import" not in files[path]:
        for change in config["replace"]:
            if change["path"] == path:
                replace_once(files, path, change["before"], change["after"])
    namespace = {"__name__": "public_cli"}
    exec(compile(files["miniconstruct/__main__.py"], "public_cli", "exec"), namespace)
    acquire, run = Mock(), Mock()
    monkeypatch.setitem(namespace, "require_guides", acquire)
    monkeypatch.setattr(uvicorn, "run", run)
    monkeypatch.setattr(sys, "argv", ["miniconstruct", *(["--verbose-http"] if verbose else [])])
    namespace["main"]()
    acquire.assert_called_once_with()
    run.assert_called_once_with(
        "miniconstruct.app:app" if verbose else "miniconstruct.http_logging:quiet_app",
        host="127.0.0.1", port=8743, reload=False, factory=not verbose, access_log=verbose,
    )
    acquire.side_effect = guides.GuideAcquisitionError("guide setup required")
    run.reset_mock()
    with pytest.raises(SystemExit) as exc:
        namespace["main"]()
    assert exc.value.code == 2
    run.assert_not_called()


@pytest.mark.parametrize("runtime", ["pwsh", "powershell", "sh"])
@pytest.mark.parametrize("options", [False, True])
def test_launcher_arguments(runtime, options, tmp_path):
    executable = shutil.which(runtime)
    if executable is None and runtime == "sh" and shutil.which("git"):
        git_shell = Path(shutil.which("git")).resolve().parent.parent / "bin/sh.exe"
        if git_shell.is_file():
            executable = str(git_shell)
    if executable is None:
        pytest.skip(f"{runtime} unavailable")
    if runtime == "sh":
        shutil.copy2(ROOT / "run.sh", tmp_path)
        python = tmp_path / ".venv/bin/python"
        python.parent.mkdir(parents=True)
        python.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
        python.chmod(0o755)
        extra = ["--verbose-http", "--reload", "--port", "9123"] if options else []
        command = [executable, "./run.sh", *extra]
        expected = ["-m", "miniconstruct", "--host", "127.0.0.1", "--port", "8743", *extra]
    else:
        shutil.copy2(ROOT / "run.ps1", tmp_path)
        python = tmp_path / ".venv/Scripts/python.exe"
        python.parent.mkdir(parents=True)
        # A PowerShell script stand-in records the forwarded arguments.
        script = (tmp_path / "run.ps1").read_text().replace('".venv\\Scripts\\python.exe"', '"record.ps1"')
        (tmp_path / "run.ps1").write_text(script)
        (tmp_path / "record.ps1").write_text('$args | ForEach-Object { Write-Output $_ }')
        extra = ["-VerboseHttp", "-Reload", "-BindHost", "0.0.0.0", "-Port", "9123"] if options else []
        command = [executable, "-NoProfile", "-File", str(tmp_path / "run.ps1"), *extra]
        expected = ["-m", "miniconstruct", "--host", "0.0.0.0" if options else "127.0.0.1",
                    "--port", "9123" if options else "8743", *(["--reload", "--verbose-http"] if options else [])]
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == expected


def test_development_batch_uses_cli():
    launcher = (ROOT / "start-miniconstruct.bat").read_text()
    assert '\".venv\\Scripts\\python.exe\" -m miniconstruct --host 127.0.0.1 --port 8743' in launcher
    assert "-m uvicorn" not in launcher
