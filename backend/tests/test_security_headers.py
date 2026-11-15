"""Security headers reach every API response, including 404s and 500s."""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings
from tests.conftest import PUBLIC_HOST


@pytest.fixture
def client(settings: Settings) -> TestClient:
    app = create_app(settings)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("boom")

    return TestClient(app)


@pytest.mark.parametrize(("path", "status"), [("/healthz", 200), ("/missing", 404), ("/boom", 500)])
def test_headers_on_every_response(client: TestClient, path: str, status: int) -> None:
    response = client.get(path)
    assert response.status_code == status
    headers = response.headers
    csp = headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert f"connect-src 'self' wss://{PUBLIC_HOST}" in csp
    assert "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp
    assert headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"
    assert headers["referrer-policy"] == "no-referrer"
    assert "camera=()" in headers["permissions-policy"]
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-request-id"]
