from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from miniconstruct.app import app


@pytest.fixture(scope="module")
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize("path", [
    "/",
    "/static/js/app.js",
    "/static/js/subject-identity.js",
    "/static/css/app.css",
])
def test_frontend_resources_revalidate_before_reuse(client: TestClient, path: str):
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"


def test_api_responses_do_not_receive_frontend_cache_policy(client: TestClient):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.headers.get("cache-control") != "no-cache"
