from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from miniconstruct.app import APP_ROOT, PACKAGED_WEB_ROOT, WEB_ROOT, app, resolve_web_root


def test_checkout_app_import_and_frontend_roots_are_repository_local():
    repository = Path(__file__).resolve().parents[1]
    expected_package = repository / "miniconstruct"
    expected_web = repository / "web"

    import miniconstruct
    import miniconstruct.app as app_module

    assert Path(miniconstruct.__file__).resolve().parent == expected_package
    assert Path(app_module.__file__).resolve() == expected_package / "app.py"
    assert APP_ROOT.resolve() == repository
    assert WEB_ROOT.resolve() == expected_web
    assert PACKAGED_WEB_ROOT == expected_package / "web"

    static_mount = next(route for route in app.routes if getattr(route, "path", None) == "/static")
    assert Path(static_mount.app.directory).resolve() == expected_web


def test_frontend_root_resolver_prefers_checkout_source_and_supports_installed_package(tmp_path: Path):
    checkout = tmp_path / "checkout"
    checkout_package = checkout / "miniconstruct"
    checkout_web = checkout / "web"
    checkout_package.mkdir(parents=True)
    checkout_web.mkdir()
    (checkout / "pyproject.toml").write_text("[project]\nname = 'miniconstruct'\n")
    (checkout_package / "app.py").touch()
    (checkout_web / "index.html").touch()
    stale_copy = checkout_package / "web"
    stale_copy.mkdir()
    (stale_copy / "index.html").write_text("stale")
    assert resolve_web_root(checkout, checkout_package) == checkout_web

    installed_root = tmp_path / "site-packages"
    installed_package = installed_root / "miniconstruct"
    installed_package.mkdir(parents=True)
    (installed_package / "app.py").touch()
    bundled_web = installed_package / "web"
    bundled_web.mkdir()
    (bundled_web / "index.html").touch()
    assert resolve_web_root(installed_root, installed_package) == bundled_web


def test_fastapi_serves_the_runtime_index_with_authoring_and_details_mounts_around_saved_prompts():
    repository = Path(__file__).resolve().parents[1]
    expected_web = repository / "web"
    with TestClient(app) as client:
        response = client.get("/")
        css = client.get("/static/css/app.css")

    assert response.status_code == 200
    assert response.content == (expected_web / "index.html").read_bytes()
    html = response.text
    markers = [
        'id="revision-panel"',
        'class="lower-grid"',
        'id="workflow-preparation-primary"',
        'id="render-live-preview"',
        'id="workflow-preparation-details"',
        'id="rendered-takes"',
    ]
    positions = [html.index(marker) for marker in markers]
    assert positions == sorted(positions)
    assert html.count('id="workflow-preparation-primary"') == 1
    assert html.count('id="workflow-preparation-details"') == 1
    assert html.count('id="validation-list"') == 1
    assert html.count('id="saved-h3-prompts"') == 1
    assert 'id="history-list"' not in html
    assert 'id="load-history"' not in html
    assert 'id="clear-history"' not in html
    assert html.count('id="render-live-preview"') == 1
    assert html.count('id="rendered-takes"') == 1
    assert 'aria-label="ComfyUI Execution actions"' in html
    assert 'aria-label="ComfyUI Execution settings and history"' in html

    assert css.status_code == 200
    assert css.content == (expected_web / "css" / "app.css").read_bytes()
