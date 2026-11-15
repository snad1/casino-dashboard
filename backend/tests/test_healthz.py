"""Liveness endpoint used by the container healthcheck."""

from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings


def test_healthz_reports_ok(settings: Settings) -> None:
    response = TestClient(create_app(settings)).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_healthz_rejects_writes(settings: Settings) -> None:
    response = TestClient(create_app(settings)).post("/healthz")
    assert response.status_code == 405


def test_docs_are_not_exposed(settings: Settings) -> None:
    client = TestClient(create_app(settings))
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
