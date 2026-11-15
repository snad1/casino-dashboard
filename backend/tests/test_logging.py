"""Log lines are JSON, carry the request id and never hold a secret-named value."""

import json
import logging

import pytest
import structlog
from fastapi.testclient import TestClient

from app.logging import REDACTED, configure_logging, redact
from app.main import create_app
from app.settings import Settings


def test_redacts_sensitive_keys_at_any_depth() -> None:
    event = {
        "event": "login",
        "user": "alice",
        "Password": "hunter2",
        "headers": {"Authorization": "Bearer abc", "Cookie": "s=1", "accept": "json"},
        "items": [{"api_key": "k1"}, {"name": "ok"}],
        "session_secret": "x",
        "bot_token": "t",
    }
    out = redact(None, "info", event)
    assert out["user"] == "alice"
    assert out["Password"] == REDACTED
    assert out["headers"] == {"Authorization": REDACTED, "Cookie": REDACTED, "accept": "json"}
    assert out["items"] == [{"api_key": REDACTED}, {"name": "ok"}]
    assert out["session_secret"] == REDACTED
    assert out["bot_token"] == REDACTED


def test_log_output_is_json_and_redacted(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    configure_logging(settings)
    structlog.get_logger("t").info("connect", password="hunter2", host="redis")  # noqa: S106
    logging.getLogger("stdlib").info("plain %s", "line")
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines[0]["event"] == "connect"
    assert lines[0]["password"] == REDACTED
    assert lines[0]["host"] == "redis"
    assert lines[1]["event"] == "plain line"
    assert "hunter2" not in json.dumps(lines)


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))


def test_request_id_is_generated(client: TestClient) -> None:
    request_id = client.get("/healthz").headers["x-request-id"]
    assert len(request_id) == 32


def test_valid_inbound_request_id_is_kept(client: TestClient) -> None:
    response = client.get("/healthz", headers={"X-Request-ID": "caddy-0123456789"})
    assert response.headers["x-request-id"] == "caddy-0123456789"


@pytest.mark.parametrize("bad", ["short", "has space here", "a" * 65, "evil\tvalue-123"])
def test_invalid_inbound_request_id_is_replaced(client: TestClient, bad: str) -> None:
    response = client.get("/healthz", headers={"X-Request-ID": bad})
    assert response.headers["x-request-id"] != bad


def test_request_line_has_path_but_no_query(
    capsys: pytest.CaptureFixture[str], settings: Settings
) -> None:
    TestClient(create_app(settings)).get("/healthz?token=abc123")
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    request = [line for line in lines if line["logger"] == "app.request"]
    assert request[0]["path"] == "/healthz"
    assert "abc123" not in json.dumps(request)
