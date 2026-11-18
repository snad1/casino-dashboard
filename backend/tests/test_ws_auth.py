"""The live socket opens only with a session, our origin and a fresh one-time ticket."""

import time
import uuid
from contextlib import ExitStack
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.testclient import WebSocketDenialResponse
from starlette.websockets import WebSocketDisconnect

from app.audit import AuditLog
from app.auth.models import Role, User, UserRole
from app.auth.sessions import SESSION_COOKIE, decode_session_id
from app.db.models import Service
from app.settings import Settings
from app.ws.auth import (
    CLOSE_SIGNED_OUT,
    MAX_FRAME_BYTES,
    MAX_SOCKETS_PER_USER,
    NOT_ALLOWED,
    NOT_SIGNED_IN,
    TICKET_TTL_SECONDS,
    TOO_MANY,
    issue_ticket,
    topic_allowed,
)
from tests.conftest import PASSWORD, Account, make_client, make_user, run_in_session


def _login(client: TestClient, email: str) -> None:
    body = {"email": email, "password": PASSWORD}
    assert client.post("/auth/login", json=body).status_code == 204


def _ticket(client: TestClient) -> str:
    token = client.get("/auth/me").json()["csrf_token"]
    response = client.post("/ws-ticket", headers={"x-csrf-token": token})
    assert response.status_code == 200
    body = response.json()
    assert body["expires_in"] == TICKET_TTL_SECONDS
    ticket: str = body["ticket"]
    return ticket


def _connect(client: TestClient, ticket: str, **headers: str) -> Any:
    """Open the socket as a browser on wss:// would: the session cookie rides along."""
    cookie = client.cookies.get(SESSION_COOKIE)
    if cookie is not None:
        headers.setdefault("cookie", f"{SESSION_COOKIE}={cookie}")
    return client.websocket_connect(f"/ws?ticket={ticket}", headers=headers)


def _denied(client: TestClient, ticket: str, **headers: str) -> WebSocketDenialResponse:
    with pytest.raises(WebSocketDenialResponse) as caught, _connect(client, ticket, **headers):
        pass
    return caught.value


@pytest.fixture
def signed_in(client: TestClient, account: Account) -> TestClient:
    _login(client, account.email)
    return client


class TestHandshake:
    def test_session_origin_and_fresh_ticket_open_the_socket(self, signed_in: TestClient) -> None:
        with _connect(signed_in, _ticket(signed_in)) as socket:
            socket.send_text("hello")

    def test_without_a_session_the_handshake_is_refused_401(
        self, app_settings: Settings, signed_in: TestClient
    ) -> None:
        ticket = _ticket(signed_in)
        with make_client(app_settings) as stranger:
            denied = _denied(stranger, ticket)
        assert denied.status_code == 401
        assert denied.json() == {"detail": NOT_SIGNED_IN}

    @pytest.mark.parametrize("origin", ["https://evil.test", ""])
    def test_foreign_or_missing_origin_is_refused(self, signed_in: TestClient, origin: str) -> None:
        if not origin:
            del signed_in.headers["origin"]
        denied = _denied(signed_in, _ticket(signed_in), **({"origin": origin} if origin else {}))
        assert denied.status_code == 403
        assert denied.json() == {"detail": NOT_ALLOWED}

    def test_ticket_works_once(self, signed_in: TestClient) -> None:
        ticket = _ticket(signed_in)
        with _connect(signed_in, ticket):
            pass
        assert _denied(signed_in, ticket).status_code == 403

    def test_missing_tampered_and_expired_tickets_are_refused(
        self, app_settings: Settings, signed_in: TestClient
    ) -> None:
        good = _ticket(signed_in)
        tampered = good[:-1] + ("0" if good[-1] != "0" else "1")
        raw = decode_session_id(signed_in.cookies[SESSION_COOKIE])
        assert raw is not None
        secret = app_settings.session_secret.get_secret_value()
        expired = issue_ticket(secret, raw, now=time.time() - TICKET_TTL_SECONDS - 1)
        for ticket in ("", "not-a-ticket", tampered, expired):
            assert _denied(signed_in, ticket).status_code == 403, ticket

    def test_a_ticket_from_another_session_is_refused(
        self, app_settings: Settings, signed_in: TestClient, account: Account
    ) -> None:
        with make_client(app_settings) as other:
            _login(other, account.email)
            ticket = _ticket(other)
        assert _denied(signed_in, ticket).status_code == 403

    def test_connects_and_refusals_are_audited(
        self, app_settings: Settings, signed_in: TestClient, account: Account
    ) -> None:
        agent = f"ws-{uuid.uuid4().hex}"
        with _connect(signed_in, _ticket(signed_in), **{"user-agent": agent}):
            pass
        _denied(signed_in, "bad", **{"user-agent": agent})

        async def rows(session: AsyncSession) -> list[tuple[str, str, str | None]]:
            query = select(AuditLog).where(AuditLog.user_agent == agent).order_by(AuditLog.id)
            return [(r.action, r.result, r.actor_email) for r in await session.scalars(query)]

        assert run_in_session(app_settings, rows) == [
            ("WS /ws", "accepted", account.email),
            ("WS /ws", "403", None),
        ]


class TestOpenSockets:
    def test_sixth_socket_is_refused_until_one_closes(self, signed_in: TestClient) -> None:
        with ExitStack() as stack:
            sockets = [
                stack.enter_context(_connect(signed_in, _ticket(signed_in)))
                for _ in range(MAX_SOCKETS_PER_USER)
            ]
            denied = _denied(signed_in, _ticket(signed_in))
            assert denied.status_code == 429
            assert denied.json() == {"detail": TOO_MANY}
            sockets[0].close()
            with _connect(signed_in, _ticket(signed_in)):
                pass

    def test_oversized_frame_closes_with_1009(self, signed_in: TestClient) -> None:
        with _connect(signed_in, _ticket(signed_in)) as socket:
            socket.send_text("x" * (MAX_FRAME_BYTES + 1))
            with pytest.raises(WebSocketDisconnect) as closed:
                socket.receive_text()
        assert closed.value.code == 1009

    @pytest.mark.parametrize("how", ["logout", "disabled"])
    def test_ending_the_session_closes_the_socket_with_4401(
        self, app_settings: Settings, signed_in: TestClient, account: Account, how: str
    ) -> None:
        signed_in.app.state.ws_recheck_seconds = 0.05  # type: ignore[attr-defined]
        with _connect(signed_in, _ticket(signed_in)) as socket:
            if how == "logout":
                token = signed_in.get("/auth/me").json()["csrf_token"]
                logout = signed_in.post("/auth/logout", headers={"x-csrf-token": token})
                assert logout.status_code == 204
            else:

                async def disable(session: AsyncSession) -> None:
                    query = update(User).where(User.id == account.id).values(disabled=True)
                    await session.execute(query)
                    await session.commit()

                run_in_session(app_settings, disable)
            with pytest.raises(WebSocketDisconnect) as closed:
                socket.receive_text()
        assert closed.value.code == CLOSE_SIGNED_OUT


def _allowed(settings: Settings, user_id: int, topic: str) -> bool:
    async def run(session: AsyncSession) -> bool:
        return await topic_allowed(session, user_id, topic)

    return run_in_session(settings, run)


class TestTopics:
    def test_view_on_the_service_is_required(self, app_settings: Settings) -> None:
        name = f"svc{uuid.uuid4().hex[:8]}"
        viewer = make_user(app_settings, Role.VIEWER, two_factor=False)

        async def scoped_viewer(session: AsyncSession) -> int:
            service = Service(name=name, adapter="wolfbet", host=f"{name}.test")
            session.add(service)
            await session.flush()
            user = User(email=f"scoped-{name}@example.test", password_hash="!")  # noqa: S106
            session.add(user)
            await session.flush()
            session.add(UserRole(user_id=user.id, service_id=service.id, role=Role.VIEWER))
            await session.commit()
            return user.id

        scoped = run_in_session(app_settings, scoped_viewer)

        cases = [
            (viewer.id, f"svc:{name}", True),
            (viewer.id, f"svc:{name}:lane:2", True),
            (scoped, f"svc:{name}:session:42", True),
            (scoped, "svc:some-other-service", False),
            (viewer.id, "audit", False),
            (viewer.id, "svc:Bad Name", False),
        ]
        for user_id, topic, expected in cases:
            assert _allowed(app_settings, user_id, topic) is expected, topic
