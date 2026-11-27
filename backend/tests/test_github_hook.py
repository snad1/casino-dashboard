"""`POST /hooks/github/{service}`: a signed green CI run on main starts the rolling update,
only when the owner switched auto-update on, and only once per commit."""

import hashlib
import hmac
import json
import secrets
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import structlog
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import AuditLog
from app.db.models import Service
from app.main import create_app
from app.settings import Settings
from tests.conftest import make_client, run_in_session
from tests.fake_agent import JOB_ID, FakeAgent

SECRET = secrets.token_urlsafe(32)
URL = "/hooks/github/wolfbet"


@pytest.fixture
def hooks_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "hooks"
    directory.mkdir()
    (directory / "github_webhook_secret.txt").write_text(SECRET + "\n")
    return directory


@pytest.fixture
def hook_settings(control_settings: Settings, hooks_dir: Path) -> Settings:
    return control_settings.model_copy(update={"hooks_dir": hooks_dir})


def _set_auto(settings: Settings, enabled: bool) -> None:
    async def run(session: AsyncSession) -> None:
        await session.execute(
            update(Service).where(Service.name == "wolfbet").values(auto_update=enabled)
        )
        await session.commit()

    run_in_session(settings, run)


@pytest.fixture
def client(hook_settings: Settings) -> Iterator[TestClient]:
    with make_client(hook_settings) as test_client:
        _set_auto(hook_settings, True)
        yield test_client
    _set_auto(hook_settings, False)


def _run(sha: str, **changes: Any) -> dict[str, Any]:
    run = {"conclusion": "success", "head_branch": "main", "event": "push", "head_sha": sha}
    return {"action": "completed", "workflow_run": {**run, **changes}}


def _send(
    client: TestClient,
    payload: Any,
    *,
    event: str = "workflow_run",
    secret: str = SECRET,
    signature: str | None = None,
    agent: str = "GitHub-Hookshot/test",
) -> int:
    body = json.dumps(payload).encode()
    signed = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    headers = {"content-type": "application/json", "x-github-event": event, "user-agent": agent}
    if signature != "":
        headers["x-hub-signature-256"] = signature or signed
    return client.post(URL, content=body, headers=headers).status_code


def _sha() -> str:
    return uuid.uuid4().hex + uuid.uuid4().hex[:8]


def _updates(fake_agent: FakeAgent) -> list[dict[str, Any]]:
    return [c for c in fake_agent.calls if c["cmd"] == "job.start"]


def _audit(settings: Settings, sha: str) -> list[tuple[str, str, dict[str, Any], int | None]]:
    async def run(session: AsyncSession) -> list[tuple[str, str, dict[str, Any], int | None]]:
        rows = await session.execute(
            select(AuditLog.action, AuditLog.result, AuditLog.args, AuditLog.actor_id).where(
                AuditLog.args["sha"].astext == sha[:12]
            )
        )
        return [tuple(row) for row in rows]

    return run_in_session(settings, run)


def _hook_rows(settings: Settings, agent: str) -> list[tuple[str, dict[str, Any]]]:
    """The middleware's rows for hook calls sent with user agent `agent`."""

    async def run(session: AsyncSession) -> list[tuple[str, dict[str, Any]]]:
        rows = await session.execute(
            select(AuditLog.result, AuditLog.args).where(
                AuditLog.user_agent == agent, AuditLog.action == "POST /hooks/github/{service}"
            )
        )
        return [(result, args) for result, args in rows]

    return run_in_session(settings, run)


def test_a_green_run_on_main_starts_the_rolling_update_once(
    client: TestClient, fake_agent: FakeAgent, hook_settings: Settings
) -> None:
    sha = _sha()
    assert _send(client, _run(sha)) == 202
    started = _updates(fake_agent)
    assert [(c["args"]["cmd"], c["args"]["args"]) for c in started] == [("wolfbet.update", {})]
    assert _audit(hook_settings, sha) == [
        ("control.update", f"job:{JOB_ID}", {"trigger": "github", "sha": sha[:12]}, None)
    ]
    assert _send(client, _run(sha)) == 204, "a redelivery of the same commit runs nothing"
    assert len(_updates(fake_agent)) == 1


@pytest.mark.parametrize(
    ("payload", "event"),
    [
        (_run("a" * 40, head_branch="feature"), "workflow_run"),
        (_run("b" * 40, conclusion="failure"), "workflow_run"),
        (_run("c" * 40, event="pull_request"), "workflow_run"),
        ({**_run("d" * 40), "action": "requested"}, "workflow_run"),
        (_run("e" * 40), "push"),
        (["not", "an", "object"], "workflow_run"),
    ],
)
def test_anything_but_a_green_push_run_on_main_runs_nothing(
    client: TestClient, fake_agent: FakeAgent, payload: Any, event: str
) -> None:
    assert _send(client, payload, event=event) == 204
    assert _updates(fake_agent) == []


def test_auto_update_off_runs_nothing(
    client: TestClient, fake_agent: FakeAgent, hook_settings: Settings
) -> None:
    _set_auto(hook_settings, False)
    assert _send(client, _run(_sha())) == 204
    assert _updates(fake_agent) == []


@pytest.mark.parametrize("signature", ["", "sha256=" + "0" * 64, "nonsense"])
def test_a_bad_or_missing_signature_is_refused_and_logged_without_the_body(
    client: TestClient, fake_agent: FakeAgent, hook_settings: Settings, signature: str
) -> None:
    sha, agent = _sha(), f"hook-{uuid.uuid4().hex}"
    with structlog.testing.capture_logs() as logs:
        assert _send(client, _run(sha), signature=signature, agent=agent) == 401
    assert _updates(fake_agent) == []
    assert _audit(hook_settings, sha) == []
    refused = [e for e in logs if e["event"] == "github hook refused"]
    assert refused == [{"event": "github hook refused", "svc": "wolfbet", "log_level": "warning"}]
    assert sha not in json.dumps(logs, default=str)
    assert _hook_rows(hook_settings, agent) == [("401", {"service": "wolfbet"})]


def test_a_body_signed_with_another_secret_is_refused(
    client: TestClient, fake_agent: FakeAgent
) -> None:
    assert _send(client, _run(_sha()), secret=secrets.token_urlsafe(32)) == 401
    assert _updates(fake_agent) == []


def test_without_a_secret_the_hook_does_not_exist(
    control_settings: Settings, fake_agent: FakeAgent
) -> None:
    with make_client(control_settings) as plain:
        assert _send(plain, _run(_sha())) == 404
    assert _updates(fake_agent) == []


def test_a_service_this_deployment_does_not_serve_is_not_found(client: TestClient) -> None:
    body = json.dumps(_run(_sha())).encode()
    assert client.post("/hooks/github/goated", content=body).status_code == 404


def test_an_oversize_body_is_refused(client: TestClient, fake_agent: FakeAgent) -> None:
    body = b"x" * (1024 * 1024 + 1)
    signed = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    reply = client.post(URL, content=body, headers={"x-hub-signature-256": signed})
    assert reply.status_code == 413
    assert _updates(fake_agent) == []


def test_a_weak_secret_stops_the_api(control_settings: Settings, hooks_dir: Path) -> None:
    (hooks_dir / "github_webhook_secret.txt").write_text("changeme")
    with pytest.raises(ValueError, match="github_webhook_secret"):
        create_app(control_settings.model_copy(update={"hooks_dir": hooks_dir}))
