"""The owner's terminal: who may open it, the binary bridge to the agent PTY, its end,
the audit trail, the open notice and the recording replay."""

import asyncio
import base64
import json
import queue
import time
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from ipaddress import ip_network
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.testclient import WebSocketDenialResponse
from starlette.websockets import WebSocketDisconnect

from app.alerts.models import AlertDelivery, AlertEvent, UserChannels
from app.audit import AuditLog
from app.auth.models import ApiSession, Role
from app.auth.sessions import SESSION_COOKIE, decode_session_id
from app.auth.stepup import STEP_UP_NEEDED
from app.mail.models import OutboxMessage
from app.settings import Settings, load_settings
from app.ws.auth import NOT_ALLOWED, TERMINAL_TICKET, issue_ticket
from app.ws.terminal import (
    ACTION,
    NETWORK_REFUSED,
    NOTICE_KIND,
    OWNER_ONLY,
    RECORDING_MISSING,
    ip_allowed,
)
from tests.conftest import Account, make_client, run_in_session
from tests.control_rig import control_settings, set_role  # noqa: F401
from tests.fake_agent import FakeAgent, Lines, fail, ok
from tests.test_totp import _enrol

SID = "5e" * 16
CAST = b'{"version": 2, "width": 80, "height": 24}\n[0.1, "o", "hi"]\n'


class Pty:
    """The agent's PTY as the test drives it: output and the end are pushed from the test."""

    def __init__(self) -> None:
        self.events: queue.Queue[bytes | str] = queue.Queue()
        self.open_error: str | None = None

    def reply(self, request: dict[str, Any]) -> Lines:
        cmd = request["cmd"]
        if cmd == "pty.open":
            if self.open_error is not None:
                return [fail(request, self.open_error)]
            return [ok(request, {"session_id": SID})]
        if cmd == "pty.stream":
            return self._stream(request)
        if cmd == "pty.close":
            self.events.put("closed")
            return [ok(request, {"session_id": SID, "reason": "closed"})]
        if cmd == "pty.recording":
            if request["args"]["session_id"] != SID:
                return [fail(request, "not_found")]
            chunk = {"id": request["id"], "seq": 0, "data": base64.b64encode(CAST).decode()}
            return [chunk, ok(request, {"session_id": SID, "bytes": len(CAST)})]
        return [ok(request, {})]

    async def _stream(self, request: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        seq = 0
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                item = self.events.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.01)
                continue
            if isinstance(item, str):
                yield ok(request, {"session_id": SID, "reason": item})
                return
            yield {"id": request["id"], "seq": seq, "data": base64.b64encode(item).decode()}
            seq += 1


@pytest.fixture
def pty(fake_agent: FakeAgent) -> Pty:
    rig = Pty()
    fake_agent.reply = rig.reply
    return rig


def _client(settings: Settings, account: Account, role: Role = Role.OWNER) -> TestClient:
    set_role(settings, account.id, role)
    client = make_client(settings).__enter__()
    _enrol(client, account.email)
    client.app.state.ws_recheck_seconds = 0.2  # type: ignore[attr-defined]
    return client


@pytest.fixture
def owner(control_settings: Settings, account: Account, pty: Pty) -> Iterator[TestClient]:  # noqa: F811
    client = _client(control_settings, account)
    yield client
    client.__exit__(None, None, None)


def _csrf(client: TestClient) -> dict[str, str]:
    return {"x-csrf-token": client.get("/auth/me").json()["csrf_token"]}


def _ticket(client: TestClient) -> str:
    response = client.post("/terminal/ticket", headers=_csrf(client))
    assert response.status_code == 200, response.text
    ticket: str = response.json()["ticket"]
    return ticket


def _raw_session(client: TestClient) -> bytes:
    raw = decode_session_id(client.cookies.get(SESSION_COOKIE))
    assert raw is not None
    return raw


def _forged(client: TestClient, settings: Settings, purpose: bytes = TERMINAL_TICKET) -> str:
    """A valid ticket for this session that skips the ticket endpoint's own checks."""
    secret = settings.session_secret.get_secret_value()
    return issue_ticket(secret, _raw_session(client), purpose=purpose)


def _connect(client: TestClient, ticket: str, query: str = "&cols=100&rows=30") -> Any:
    cookie = client.cookies.get(SESSION_COOKIE)
    headers = {"cookie": f"{SESSION_COOKIE}={cookie}"}
    return client.websocket_connect(f"/ws/terminal?ticket={ticket}{query}", headers=headers)


def _denied(client: TestClient, ticket: str) -> WebSocketDenialResponse:
    with pytest.raises(WebSocketDenialResponse) as caught, _connect(client, ticket):
        pass
    return caught.value


def _set_step_up(settings: Settings, user_id: int, at: datetime | None) -> None:
    async def run(session: AsyncSession) -> None:
        query = update(ApiSession).where(ApiSession.user_id == user_id)
        await session.execute(query.values(step_up_at=at))
        await session.commit()

    run_in_session(settings, run)


def _control(socket: Any) -> dict[str, Any]:
    frame: bytes = socket.receive_bytes()
    assert frame[0] == 1
    message: dict[str, Any] = json.loads(frame[1:])
    return message


def _wait_for(fake_agent: FakeAgent, cmd: str) -> dict[str, Any]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        for call in fake_agent.calls:
            if call["cmd"] == cmd:
                return call
        time.sleep(0.01)
    raise AssertionError(f"the agent never got {cmd}: {fake_agent.cmds()}")


def _audit(settings: Settings, email: str) -> list[tuple[str, dict[str, Any]]]:
    async def run(session: AsyncSession) -> list[tuple[str, dict[str, Any]]]:
        query = (
            select(AuditLog.result, AuditLog.args)
            .where(AuditLog.actor_email == email, AuditLog.action == ACTION)
            .order_by(AuditLog.id)
        )
        return [(row.result, row.args) for row in await session.execute(query)]

    return run_in_session(settings, run)


# ── who may open it ──
def test_the_owner_opens_the_terminal(owner: TestClient, fake_agent: FakeAgent) -> None:
    with _connect(owner, _ticket(owner)) as socket:
        hello = _control(socket)
        assert hello == {"session_id": SID, "idle_s": 600, "max_s": 3600}
    assert _wait_for(fake_agent, "pty.open")["args"] == {"cols": 100, "rows": 30}


@pytest.mark.parametrize("role", [Role.ADMIN, Role.OPERATOR, Role.VIEWER])
def test_anyone_but_the_owner_is_refused_before_upgrade(
    control_settings: Settings,  # noqa: F811
    account: Account,
    pty: Pty,
    fake_agent: FakeAgent,
    role: Role,
) -> None:
    client = _client(control_settings, account, role)
    try:
        response = client.post("/terminal/ticket", headers=_csrf(client))
        assert response.status_code == 403 and response.json()["detail"] == OWNER_ONLY
        denied = _denied(client, _forged(client, control_settings))
        assert denied.status_code == 403 and denied.json()["detail"] == OWNER_ONLY
    finally:
        client.__exit__(None, None, None)
    assert "pty.open" not in fake_agent.cmds()
    assert _audit(control_settings, account.email)[-1][0] == "refused:403"


def test_a_stale_code_asks_for_a_new_one(
    owner: TestClient,
    control_settings: Settings,  # noqa: F811
    account: Account,
    fake_agent: FakeAgent,
) -> None:
    _set_step_up(control_settings, account.id, datetime.now(UTC) - timedelta(minutes=3))
    response = owner.post("/terminal/ticket", headers=_csrf(owner))
    assert response.status_code == 403 and response.json()["step_up_required"] is True
    denied = _denied(owner, _forged(owner, control_settings))
    assert denied.status_code == 403 and denied.json()["detail"] == STEP_UP_NEEDED
    assert "pty.open" not in fake_agent.cmds()


def test_a_live_socket_ticket_or_a_reused_one_does_not_open_it(
    owner: TestClient,
    control_settings: Settings,  # noqa: F811
) -> None:
    live = _forged(owner, control_settings, purpose=b"ws")
    assert _denied(owner, live).json()["detail"] == NOT_ALLOWED
    ticket = _ticket(owner)
    with _connect(owner, ticket) as socket:
        _control(socket)
    assert _denied(owner, ticket).json()["detail"] == NOT_ALLOWED


def test_a_foreign_origin_is_refused(owner: TestClient) -> None:
    cookie = owner.cookies.get(SESSION_COOKIE)
    headers = {"cookie": f"{SESSION_COOKIE}={cookie}", "origin": "https://evil.example"}
    with (
        pytest.raises(WebSocketDenialResponse) as caught,
        owner.websocket_connect(f"/ws/terminal?ticket={_ticket(owner)}", headers=headers),
    ):
        pass
    assert caught.value.status_code == 403


@pytest.mark.parametrize(("network", "opens"), [("192.0.2.0/24", False), ("10.0.0.0/8", True)])
def test_the_ip_allowlist(
    control_settings: Settings,  # noqa: F811
    account: Account,
    pty: Pty,
    network: str,
    opens: bool,
) -> None:
    settings = control_settings.model_copy(update={"terminal_ip_allowlist": [ip_network(network)]})
    client = _client(settings, account)
    try:
        response = client.post("/terminal/ticket", headers=_csrf(client))
        if opens:
            assert response.status_code == 200
        else:
            assert response.status_code == 403 and response.json()["detail"] == NETWORK_REFUSED
            assert _denied(client, _forged(client, settings)).json()["detail"] == NETWORK_REFUSED
    finally:
        client.__exit__(None, None, None)


def test_ip_allowed() -> None:
    nets = [ip_network("203.0.113.0/24"), ip_network("2001:db8::/32")]
    assert ip_allowed([], None) and ip_allowed(nets, "203.0.113.9")
    assert ip_allowed(nets, "2001:db8::1") and not ip_allowed(nets, "198.51.100.1")
    assert not ip_allowed(nets, None) and not ip_allowed(nets, "testclient")


@pytest.mark.usefixtures("secrets_dir")
def test_allowlist_setting_takes_a_comma_list(monkeypatch: pytest.MonkeyPatch) -> None:
    assert load_settings().terminal_ip_allowlist == []
    monkeypatch.setenv("TERMINAL_IP_ALLOWLIST", "203.0.113.0/24, 2001:db8::/32")
    loaded = load_settings().terminal_ip_allowlist
    assert [str(n) for n in loaded] == ["203.0.113.0/24", "2001:db8::/32"]


def test_a_second_terminal_is_told_one_is_open(owner: TestClient, pty: Pty) -> None:
    pty.open_error = "busy"
    with _connect(owner, _ticket(owner)) as socket:
        assert _control(socket) == {"reason": "busy"}
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_bytes()
    assert closed.value.code == 1013


# ── the bridge ──
def test_keys_resize_and_output_cross_the_bridge(
    owner: TestClient, pty: Pty, fake_agent: FakeAgent
) -> None:
    with _connect(owner, _ticket(owner)) as socket:
        _control(socket)
        socket.send_bytes(b"\x00ls -la\r")
        write = _wait_for(fake_agent, "pty.write")
        assert write["args"] == {"session_id": SID, "data": base64.b64encode(b"ls -la\r").decode()}
        socket.send_bytes(b"\x01" + (120).to_bytes(2, "big") + (40).to_bytes(2, "big"))
        assert _wait_for(fake_agent, "pty.resize")["args"] == {
            "session_id": SID,
            "cols": 120,
            "rows": 40,
        }
        pty.events.put(b"\x1b[32mtotal 0\x1b[0m\r\n")
        assert socket.receive_bytes() == b"\x00\x1b[32mtotal 0\x1b[0m\r\n"


@pytest.mark.parametrize(
    ("frame", "code"), [("text", 1003), (b"\x07x", 1003), (b"\x00" + b"a" * 16384, 1009)]
)
def test_frames_outside_the_rules_close_the_terminal(
    owner: TestClient, fake_agent: FakeAgent, frame: str | bytes, code: int
) -> None:
    with _connect(owner, _ticket(owner)) as socket:
        _control(socket)
        if isinstance(frame, str):
            socket.send_text(frame)
        else:
            socket.send_bytes(frame)
        assert "reason" in _control(socket)
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_bytes()
    assert closed.value.code == code
    _wait_for(fake_agent, "pty.close")


def test_idle_end_from_the_agent_closes_both_sides_and_is_audited(
    owner: TestClient,
    pty: Pty,
    control_settings: Settings,  # noqa: F811
    account: Account,
) -> None:
    with _connect(owner, _ticket(owner)) as socket:
        _control(socket)
        pty.events.put("idle")
        assert _control(socket) == {"reason": "idle"}
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_bytes()
    assert closed.value.code == 1000
    rows = _audit(control_settings, account.email)
    assert rows[-2] == ("opened", {"session_id": SID})
    result, args = rows[-1]
    assert result == "closed:idle" and args["session_id"] == SID and args["duration_ms"] >= 0


def test_signing_out_ends_the_terminal(owner: TestClient, fake_agent: FakeAgent) -> None:
    with _connect(owner, _ticket(owner)) as socket:
        _control(socket)
        assert owner.post("/auth/logout", headers=_csrf(owner)).status_code == 204
        assert _control(socket) == {"reason": "signed_out"}
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_bytes()
    assert closed.value.code == 4401
    _wait_for(fake_agent, "pty.close")


def test_losing_the_owner_role_ends_the_terminal(
    owner: TestClient,
    control_settings: Settings,  # noqa: F811
    account: Account,
) -> None:
    with _connect(owner, _ticket(owner)) as socket:
        _control(socket)
        set_role(control_settings, account.id, Role.ADMIN)
        assert _control(socket) == {"reason": "forbidden"}
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_bytes()
    assert closed.value.code == 4403


def test_closing_the_tab_closes_the_pty(owner: TestClient, fake_agent: FakeAgent) -> None:
    with _connect(owner, _ticket(owner)) as socket:
        _control(socket)
        socket.close()
        _wait_for(fake_agent, "pty.close")


# ── the open notice ──
def test_opening_emails_the_owner_and_queues_telegram(
    owner: TestClient,
    control_settings: Settings,  # noqa: F811
    account: Account,
) -> None:
    async def link(session: AsyncSession) -> None:
        await session.execute(delete(UserChannels).where(UserChannels.user_id == account.id))
        session.add(
            UserChannels(user_id=account.id, telegram_enabled=True, telegram_chat_id=account.id)
        )
        await session.commit()

    run_in_session(control_settings, link)
    with _connect(owner, _ticket(owner)) as socket:
        _control(socket)

    async def check(session: AsyncSession) -> tuple[list[str], list[str], str]:
        mail = await session.scalars(
            select(OutboxMessage.subject).where(OutboxMessage.to_email == account.email)
        )
        found = (
            await session.execute(
                select(AlertDelivery.channel, AlertEvent.message)
                .join(AlertEvent, AlertEvent.id == AlertDelivery.event_id)
                .where(AlertDelivery.user_id == account.id, AlertEvent.kind == NOTICE_KIND)
            )
        ).all()
        return list(mail), [row.channel for row in found], found[-1].message

    subjects, channels, message = run_in_session(control_settings, check)
    assert "The web terminal was opened" in subjects
    assert channels == ["telegram"]
    assert account.email in message


# ── replay ──
def test_the_owner_replays_a_recording_with_a_fresh_code(
    owner: TestClient,
    control_settings: Settings,  # noqa: F811
    account: Account,
) -> None:
    response = owner.get(f"/terminal/recordings/{SID}")
    assert response.status_code == 200 and response.content == CAST
    assert response.headers["cache-control"] == "no-store"
    assert owner.get(f"/terminal/recordings/{'0' * 32}").json()["detail"] == RECORDING_MISSING
    assert owner.get("/terminal/recordings/not-an-id").status_code == 422
    _set_step_up(control_settings, account.id, datetime.now(UTC) - timedelta(minutes=3))
    stale = owner.get(f"/terminal/recordings/{SID}")
    assert stale.status_code == 403 and stale.json()["step_up_required"] is True


def test_an_admin_cannot_replay(
    control_settings: Settings,  # noqa: F811
    account: Account,
    pty: Pty,
) -> None:
    client = _client(control_settings, account, Role.ADMIN)
    try:
        assert client.get(f"/terminal/recordings/{SID}").status_code == 403
    finally:
        client.__exit__(None, None, None)
