"""The audit log: every unsafe route writes a row, the chain holds, and nobody can rewrite it."""

import asyncio
import re
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import cli
from app.audit import UNSAFE_METHODS, AuditEntry, AuditLog, record, verify_chain
from app.auth.models import Role
from app.logging import REDACTED
from app.main import create_app
from app.settings import Settings
from tests.conftest import PASSWORD, Account, make_client, make_user, run_in_session

PARAM = re.compile(r"\{[^}]+\}")


def _rows_by_agent(settings: Settings, agent: str) -> list[AuditLog]:
    async def run(session: AsyncSession) -> list[AuditLog]:
        query = select(AuditLog).where(AuditLog.user_agent == agent).order_by(AuditLog.id)
        return list(await session.scalars(query))

    return run_in_session(settings, run)


def _sessionmaker(settings: Settings) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        create_async_engine(settings.database_url, poolclass=NullPool), expire_on_commit=False
    )


def _record(settings: Settings, entry: AuditEntry) -> None:
    asyncio.run(record(_sessionmaker(settings), entry))


def _verify(settings: Settings) -> int | None:
    return run_in_session(settings, verify_chain)


def _unsafe_routes(settings: Settings) -> list[tuple[str, str]]:
    """Every state-changing route the app serves, from FastAPI's own route schema."""
    paths: dict[str, dict[str, Any]] = create_app(settings).openapi()["paths"]
    return sorted(
        (method.upper(), path)
        for path, operations in paths.items()
        for method in operations
        if method.upper() in UNSAFE_METHODS
    )


def test_every_unsafe_route_writes_an_audit_row(app_settings: Settings) -> None:
    routes = _unsafe_routes(app_settings)
    assert ("POST", "/auth/login") in routes
    with make_client(app_settings) as client:
        for method, path in routes:
            agent = f"meta-{uuid.uuid4().hex}"
            url = PARAM.sub("x", path)
            client.request(method, url, json={}, headers={"user-agent": agent})
            actions = [row.action for row in _rows_by_agent(app_settings, agent)]
            assert actions == [f"{method} {path}"], f"{method} {path} wrote no audit row"


class TestRows:
    def test_successful_login_names_the_actor_and_hides_the_password(
        self, app_settings: Settings, account: Account
    ) -> None:
        agent = f"login-{uuid.uuid4().hex}"
        with make_client(app_settings) as client:
            body = {"email": account.email, "password": PASSWORD}
            client.post("/auth/login", json=body, headers={"user-agent": agent})
        [row] = _rows_by_agent(app_settings, agent)
        assert (row.action, row.result) == ("POST /auth/login", "204")
        assert (row.actor_id, row.actor_email, row.roles) == (account.id, account.email, "viewer")
        assert row.args == {"email": account.email, "password": REDACTED}
        assert row.request_id

    def test_failed_and_refused_requests_are_recorded_too(
        self, app_settings: Settings, account: Account
    ) -> None:
        agent = f"fail-{uuid.uuid4().hex}"
        with make_client(app_settings) as client:
            body = {"email": account.email, "password": "wrong-" + PASSWORD}
            client.post("/auth/login", json=body, headers={"user-agent": agent})
            foreign = {"user-agent": agent, "origin": "https://evil.test"}
            client.post("/auth/login", json=body, headers=foreign)
        rows = _rows_by_agent(app_settings, agent)
        assert [(r.result, r.actor_id) for r in rows] == [("401", None), ("403", None)]

    def test_codes_and_nested_secrets_are_redacted(self, app_settings: Settings) -> None:
        agent = f"redact-{uuid.uuid4().hex}"
        args = {"code": "123456", "nested": {"api_key": "k", "keep": 1}, "email": "a@b.test"}
        _record(app_settings, AuditEntry("job.start", "ok", args=args, user_agent=agent))
        [row] = _rows_by_agent(app_settings, agent)
        assert row.args == {
            "code": REDACTED,
            "nested": {"api_key": REDACTED, "keep": 1},
            "email": "a@b.test",
        }


class TestChain:
    def test_parallel_writes_keep_one_valid_chain(self, app_settings: Settings) -> None:
        sessionmaker = _sessionmaker(app_settings)

        async def many() -> None:
            await asyncio.gather(
                *(record(sessionmaker, AuditEntry(f"parallel.{i}", "ok")) for i in range(20))
            )

        asyncio.run(many())
        assert _verify(app_settings) is None

    def test_tampering_is_detected_and_the_cli_fails(
        self,
        app_settings: Settings,
        migrated: Settings,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        agent = f"tamper-{uuid.uuid4().hex}"
        _record(app_settings, AuditEntry("export", "ok", user_agent=agent))
        [row] = _rows_by_agent(app_settings, agent)

        def owner_sql(*statements: str) -> None:
            async def run(session: AsyncSession) -> None:
                for statement in statements:
                    await session.execute(text(statement), {"id": row.id})
                await session.commit()

            run_in_session(migrated, run)

        owner_sql(
            "ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_change",
            "UPDATE audit_log SET result = 'error' WHERE id = :id",
        )
        try:
            assert _verify(app_settings) == row.id
            monkeypatch.setattr(cli, "load_settings", lambda: app_settings)
            assert cli.main(["verify-audit"]) == 1
            assert f"broken at id {row.id}" in capsys.readouterr().err
        finally:
            owner_sql(
                "UPDATE audit_log SET result = 'ok' WHERE id = :id",
                "ALTER TABLE audit_log ENABLE TRIGGER audit_log_no_change",
            )
        assert _verify(app_settings) is None
        assert cli.main(["verify-audit"]) == 0


def _try(settings: Settings, statement: str) -> str:
    async def run(session: AsyncSession) -> str:
        try:
            await session.execute(text(statement))
        except DBAPIError as error:
            return str(error.orig)
        return "allowed"

    return run_in_session(settings, run)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_log SET result = 'x'",
        "DELETE FROM audit_log",
        "TRUNCATE audit_log",
    ],
)
class TestAppendOnly:
    def test_the_api_role_has_no_right_to_change_rows(
        self, app_settings: Settings, statement: str
    ) -> None:
        _record(app_settings, AuditEntry("seed", "ok"))
        assert "permission denied" in _try(app_settings, statement)

    def test_even_the_owner_is_stopped_by_the_trigger(
        self, migrated: Settings, app_settings: Settings, statement: str
    ) -> None:
        _record(app_settings, AuditEntry("seed", "ok"))
        assert "audit_log is append-only" in _try(migrated, statement)


class TestReadApi:
    def test_admin_pages_through_filtered_rows(
        self, app_settings: Settings, as_user: Callable[[Any], TestClient]
    ) -> None:
        action = f"probe.{uuid.uuid4().hex}"
        for _ in range(3):
            _record(app_settings, AuditEntry(action, "ok", service="wolfbet"))
        client = as_user(make_user(app_settings, Role.ADMIN, two_factor=True))
        first = client.get("/audit", params={"action": action, "limit": 2}).json()
        assert [i["action"] for i in first["items"]] == [action, action]
        assert first["items"][0]["id"] > first["items"][1]["id"]
        assert first["next_before_id"] == first["items"][1]["id"]
        rest = client.get(
            "/audit", params={"action": action, "limit": 2, "before_id": first["next_before_id"]}
        ).json()
        assert len(rest["items"]) == 1
        assert rest["next_before_id"] is None
        assert "hash" not in rest["items"][0]

    @pytest.mark.parametrize(("role", "two_factor"), [(Role.VIEWER, False), (Role.ADMIN, False)])
    def test_others_are_refused(
        self,
        app_settings: Settings,
        as_user: Callable[[Any], TestClient],
        role: Role,
        two_factor: bool,
    ) -> None:
        client = as_user(make_user(app_settings, role, two_factor=two_factor))
        assert client.get("/audit").status_code == 403

    def test_limit_is_capped(
        self, app_settings: Settings, as_user: Callable[[Any], TestClient]
    ) -> None:
        client = as_user(make_user(app_settings, Role.ADMIN, two_factor=True))
        assert client.get("/audit", params={"limit": 101}).status_code == 422
