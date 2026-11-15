"""Error bodies are generic in production and detailed only in local, development, testing."""

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.errors import GENERIC_ERROR
from app.main import create_app
from app.settings import Settings

LEAKY = "connect failed for postgres://dashboard:hunter2@timescale"


class Login(BaseModel):
    username: str
    password: int


def _client(settings: Settings) -> TestClient:
    app = create_app(settings)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError(LEAKY)

    @app.post("/login", status_code=204)
    async def login(body: Login) -> None:
        return None

    return TestClient(app)


@pytest.fixture
def production(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(settings, "app_env", "production")
    return _client(settings)


def test_production_500_is_generic(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    response = _client(settings).get("/boom")
    assert response.status_code == 500
    body = response.json()
    assert body == {"error": GENERIC_ERROR, "request_id": response.headers["x-request-id"]}
    assert "hunter2" not in response.text
    logged = capsys.readouterr().out
    assert "unhandled_error" in logged
    assert "RuntimeError" in logged
    assert response.headers["x-request-id"] in logged


def test_testing_500_carries_detail(settings: Settings) -> None:
    body = _client(settings).get("/boom").json()
    assert body["error"] == GENERIC_ERROR
    assert body["detail"].startswith("RuntimeError:")


def test_production_422_does_not_echo_input(production: TestClient) -> None:
    response = production.post("/login", json={"username": "a", "password": "s3cret-value"})
    assert response.status_code == 422
    assert "s3cret-value" not in response.text
    assert response.json()["detail"][0]["loc"] == ["body", "password"]


def test_testing_422_keeps_input_for_debugging(settings: Settings) -> None:
    response = _client(settings).post("/login", json={"username": "a", "password": "x"})
    assert response.json()["detail"][0]["input"] == "x"
