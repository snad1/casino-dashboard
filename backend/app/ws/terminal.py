"""The owner's web terminal: a gated WebSocket bridged to the host agent's PTY.

The socket opens only when all of these hold, checked before the handshake is accepted:
the origin is ours, the session cookie is live, the `ticket` was issued for the terminal to
this session less than 30 s ago and is unused, the user holds the terminal permission (the
owner, with 2FA on), their step-up code is at most 2 min old and, when
`TERMINAL_IP_ALLOWLIST` is set, the client address is inside it. `POST /terminal/ticket`
runs the same checks so the page can say what is missing. Once open, the session and the
permission are re-checked every minute, and the agent ends the PTY after 10 min without
input or 60 min in all.

Every frame is binary with a one-byte op: `0x00` carries terminal bytes (keys in, output
out); from the browser `0x01` is a resize (cols and rows as big-endian u16), from the api
`0x01` is a JSON notice (the session at open, the reason before close). Each open emails
every owner and sends them a Telegram message; open, refusal and close are audited with
the recording's session id, which `GET /terminal/recordings/{id}` replays.
"""

import asyncio
import base64
import binascii
import contextlib
import re
import struct
import time
from collections.abc import AsyncGenerator, AsyncIterator, Coroutine
from datetime import UTC, datetime, timedelta
from ipaddress import IPv4Network, IPv6Network, ip_address
from typing import Annotated, Any, cast

import orjson
import structlog
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Path, Request, WebSocket, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocketState

from app.agent_client import AgentClient, AgentError, StreamEnd
from app.alerts.models import AlertDelivery, AlertEvent, UserChannels
from app.audit import AuditEntry, record
from app.auth.models import Role, User, UserRole
from app.auth.permissions import FORBIDDEN, Perm, denial, require
from app.auth.sessions import current_user, load_session
from app.auth.stepup import STEP_UP_NEEDED, NextStepRequired
from app.db.engine import get_session
from app.mail import templates
from app.mail.outbox import enqueue
from app.ws.auth import (
    CLOSE_FORBIDDEN,
    CLOSE_SIGNED_OUT,
    MAX_FRAME_BYTES,
    TERMINAL_TICKET,
    TICKET_TTL_SECONDS,
    Denied,
    Handshake,
    authenticate,
    issue_ticket,
    still_signed_in,
)
from app.ws.routes import CLOSE_TOO_BIG, Ticket

ACTION = "WS /ws/terminal"
STEP_UP_SECONDS = 120
IDLE_S = 600
MAX_S = 3600
OP_DATA = 0
OP_CONTROL = 1
CLOSE_NORMAL = 1000
CLOSE_UNSUPPORTED = 1003
CLOSE_UNAVAILABLE = 1011
CLOSE_TRY_LATER = 1013
NOTICE_KIND = "terminal_opened"
NOTICE_SERVICE = "host"
DEFAULT_SIZE = (80, 24)
_SESSION_ID = re.compile(r"[0-9a-f]{32}")

OWNER_ONLY = "Only the owner can use the terminal."
NETWORK_REFUSED = "The terminal is not allowed from this network."
RECORDING_MISSING = "That recording is not available."
RECORDING_UNAVAILABLE = "Recordings are unavailable right now."

Network = IPv4Network | IPv6Network
log = structlog.get_logger("app.ws.terminal")
router = APIRouter(tags=["terminal"])


def ip_allowed(allowlist: list[Network], ip: str | None) -> bool:
    """True with no allowlist; otherwise the address must parse and fall inside one network."""
    if not allowlist:
        return True
    try:
        address = ip_address(ip or "")
    except ValueError:
        return False
    return any(address in network for network in allowlist)


async def refusal(
    db: AsyncSession, user: User, step_up_at: datetime | None, ip: str | None, app: FastAPI
) -> str | None:
    """Why `user` may not open the terminal now, or None when they may."""
    detail = await denial(db, user, None, Perm.TERMINAL)
    if detail is not None:
        return OWNER_ONLY if detail == FORBIDDEN else detail
    if not ip_allowed(app.state.terminal_ip_allowlist, ip):
        return NETWORK_REFUSED
    if step_up_at is None or step_up_at < datetime.now(UTC) - timedelta(seconds=STEP_UP_SECONDS):
        return STEP_UP_NEEDED
    return None


def _client_ip(scope_client: Any) -> str | None:
    return scope_client.host if scope_client else None


@router.post("/terminal/ticket", response_model=Ticket)
async def terminal_ticket(
    user: Annotated[User, Depends(current_user)],
    request: Request,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> Ticket:
    """A one-time pass for opening the terminal socket from this session within 30 s."""
    detail = await refusal(
        db, user, request.state.api_session.step_up_at, _client_ip(request.client), request.app
    )
    if detail == STEP_UP_NEEDED:
        raise NextStepRequired(status.HTTP_403_FORBIDDEN, detail, "step_up_required")
    if detail is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail)
    ticket = issue_ticket(
        request.app.state.session_secret, request.state.session_id, purpose=TERMINAL_TICKET
    )
    return Ticket(ticket=ticket, expires_in=TICKET_TTL_SECONDS)


async def _audit(
    websocket: WebSocket, user: User | None, result: str, args: dict[str, Any] | None = None
) -> None:
    entry = AuditEntry(
        action=ACTION,
        result=result,
        actor_id=user.id if user else None,
        actor_email=user.email if user else None,
        args=args or {},
        ip=_client_ip(websocket.client),
        user_agent=websocket.headers.get("user-agent"),
    )
    try:
        await record(websocket.app.state.sessionmaker, entry)
    except Exception:
        log.exception("audit write failed for the terminal")


async def _notify(app: FastAPI, actor: User, ip: str | None, browser: str | None) -> None:
    """Email every owner, and queue a Telegram message for each owner who linked a chat."""
    when = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    where = ip or "unknown"
    try:
        async with app.state.sessionmaker() as db:
            owners = list(
                await db.execute(
                    select(User, UserChannels)
                    .join(UserRole, UserRole.user_id == User.id)
                    .outerjoin(UserChannels, UserChannels.user_id == User.id)
                    .where(
                        UserRole.role == Role.OWNER,
                        UserRole.service_id.is_(None),
                        User.disabled.is_(False),
                    )
                )
            )
            who = actor.email or f"Telegram user {actor.tg_uid}"
            notice = templates.terminal_opened(
                who, when, where, (browser or "unknown browser")[:120]
            )
            for owner, _ in owners:
                enqueue(db, owner.email, notice)
            linked = [
                owner.id
                for owner, prefs in owners
                if prefs is not None and prefs.telegram_enabled and prefs.telegram_chat_id
            ]
            if linked:
                event = AlertEvent(
                    service=NOTICE_SERVICE,
                    kind=NOTICE_KIND,
                    message=f"Web terminal opened by {actor.email} from {where} at {when}.",
                )
                db.add(event)
                await db.flush()
                db.add_all(
                    AlertDelivery(event_id=event.id, user_id=uid, channel="telegram")
                    for uid in linked
                )
            await db.commit()
    except Exception:
        log.exception("terminal open notice could not be queued")


def control(message: dict[str, Any]) -> bytes:
    return bytes([OP_CONTROL]) + orjson.dumps(message)


def _size(query: Any) -> tuple[int, int]:
    try:
        cols, rows = int(query.get("cols", "")), int(query.get("rows", ""))
    except ValueError:
        return DEFAULT_SIZE
    if 1 <= cols <= 500 and 1 <= rows <= 200:
        return cols, rows
    return DEFAULT_SIZE


Outcome = tuple[str, int | None]


async def _keys(websocket: WebSocket, agent: AgentClient, session_id: str) -> Outcome:
    """Browser frames to the PTY until the browser leaves or breaks the frame rules."""
    while True:
        frame = await websocket.receive()
        if frame["type"] == "websocket.disconnect":
            return "closed", None
        data = frame.get("bytes")
        if data is None:
            return "unsupported", CLOSE_UNSUPPORTED
        if len(data) > MAX_FRAME_BYTES:
            return "too_big", CLOSE_TOO_BIG
        if not data:
            continue
        try:
            if data[0] == OP_DATA and len(data) > 1:
                payload = base64.b64encode(data[1:]).decode()
                await agent.call("pty.write", {"session_id": session_id, "data": payload})
            elif data[0] == OP_CONTROL and len(data) == 5:
                cols, rows = struct.unpack(">HH", data[1:])
                if 1 <= cols <= 500 and 1 <= rows <= 200:
                    args = {"session_id": session_id, "cols": cols, "rows": rows}
                    await agent.call("pty.resize", args)
            else:
                return "unsupported", CLOSE_UNSUPPORTED
        except AgentError as error:
            log.info("terminal input not delivered", code=error.code)


async def _output(websocket: WebSocket, agent: AgentClient, session_id: str) -> Outcome:
    """PTY output to the browser until the agent ends the session."""
    try:
        async for item in agent.stream("pty.stream", {"session_id": session_id, "from_seq": 0}):
            if isinstance(item, StreamEnd):
                reason = item.data.get("reason")
                return (reason if isinstance(reason, str) else "closed"), CLOSE_NORMAL
            try:
                chunk = base64.b64decode(item.get("data", ""), validate=True)
            except (binascii.Error, TypeError):
                continue
            await websocket.send_bytes(bytes([OP_DATA]) + chunk)
    except AgentError as error:
        log.warning("terminal output stopped", code=error.code)
    return "unavailable", CLOSE_UNAVAILABLE


async def _watch(app: FastAPI, user: User, raw: bytes) -> Outcome:
    """End the terminal on sign-out, a disabled account, a lost owner role or 2FA turned off."""
    while True:
        await asyncio.sleep(app.state.ws_recheck_seconds)
        async with app.state.sessionmaker() as db:
            if not await still_signed_in(db, raw):
                return "signed_out", CLOSE_SIGNED_OUT
            if await denial(db, user, None, Perm.TERMINAL) is not None:
                return "forbidden", CLOSE_FORBIDDEN


async def _first(*runs: Coroutine[Any, Any, Outcome]) -> Outcome:
    """The outcome of whichever side ends first; the others are cancelled and awaited."""
    tasks = [asyncio.create_task(run) for run in runs]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        return next(iter(done)).result()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _admit(websocket: WebSocket, app: FastAPI) -> tuple[User, bytes]:
    handshake = Handshake(
        origin=websocket.headers.get("origin"),
        cookies=dict(websocket.cookies),
        ticket=websocket.query_params.get("ticket"),
    )
    async with app.state.sessionmaker() as db:
        user, raw = await authenticate(
            handshake,
            db,
            app.state.redis,
            app.state.session_secret,
            app.state.allowed_origins,
            purpose=TERMINAL_TICKET,
        )
        loaded = await load_session(db, raw)
        step_up_at = loaded[0].step_up_at if loaded is not None else None
        detail = await refusal(db, user, step_up_at, _client_ip(websocket.client), app)
    if detail is not None:
        websocket.state.refused_user = user
        raise Denied(status.HTTP_403_FORBIDDEN, detail)
    return user, raw


@router.websocket("/ws/terminal")
async def terminal(websocket: WebSocket) -> None:
    app: FastAPI = websocket.app
    try:
        user, raw = await _admit(websocket, app)
    except Denied as denied:
        await _audit(
            websocket, getattr(websocket.state, "refused_user", None), f"refused:{denied.status}"
        )
        body = JSONResponse({"detail": denied.detail}, status_code=denied.status)
        await websocket.send_denial_response(body)
        return
    agent: AgentClient | None = app.state.agent
    await websocket.accept()
    cols, rows = _size(websocket.query_params)
    try:
        if agent is None:
            raise AgentError("unconfigured")
        opened = await agent.call("pty.open", {"cols": cols, "rows": rows})
        session_id = opened.get("session_id")
        if not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id):
            raise AgentError("bad_reply")
    except AgentError as error:
        failure = "busy" if error.code == "busy" else "unavailable"
        log.warning("terminal did not open", code=error.code)
        await _audit(websocket, user, f"failed:{failure}")
        await websocket.send_bytes(control({"reason": failure}))
        await websocket.close(CLOSE_TRY_LATER if failure == "busy" else CLOSE_UNAVAILABLE)
        return
    started = time.monotonic()
    await _audit(websocket, user, "opened", {"session_id": session_id})
    await _notify(app, user, _client_ip(websocket.client), websocket.headers.get("user-agent"))
    await websocket.send_bytes(
        control({"session_id": session_id, "idle_s": IDLE_S, "max_s": MAX_S})
    )
    reason: str = "error"
    code: int | None = CLOSE_UNAVAILABLE
    try:
        reason, code = await _first(
            _keys(websocket, agent, session_id),
            _output(websocket, agent, session_id),
            _watch(app, user, raw),
        )
    finally:
        try:
            await agent.call("pty.close", {"session_id": session_id})
        except AgentError as error:
            log.info("terminal close not confirmed", code=error.code)
        duration_ms = int((time.monotonic() - started) * 1000)
        args = {"session_id": session_id, "duration_ms": duration_ms}
        await _audit(websocket, user, f"closed:{reason}", args)
        if code is not None and websocket.client_state == WebSocketState.CONNECTED:
            with contextlib.suppress(RuntimeError, OSError):
                await websocket.send_bytes(control({"reason": reason}))
                await websocket.close(code)


@router.get("/terminal/recordings/{session_id}", response_class=StreamingResponse)
async def terminal_recording(
    session_id: Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")],
    user: Annotated[User, Depends(require(Perm.TERMINAL))],
    request: Request,
) -> StreamingResponse:
    """A terminal session's asciicast v2 recording, for the owner with a fresh code."""
    step_up_at = request.state.api_session.step_up_at
    if step_up_at is None or step_up_at < datetime.now(UTC) - timedelta(seconds=STEP_UP_SECONDS):
        raise NextStepRequired(status.HTTP_403_FORBIDDEN, STEP_UP_NEEDED, "step_up_required")
    agent: AgentClient | None = request.app.state.agent
    if agent is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, RECORDING_UNAVAILABLE)
    frames = cast(
        AsyncGenerator[dict[str, Any] | StreamEnd],
        agent.stream("pty.recording", {"session_id": session_id}),
    )
    try:
        first = await anext(frames)
    except AgentError as error:
        await frames.aclose()
        if error.code == "not_found":
            raise HTTPException(status.HTTP_404_NOT_FOUND, RECORDING_MISSING) from None
        log.warning("recording unavailable", code=error.code)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, RECORDING_UNAVAILABLE) from None

    async def body() -> AsyncIterator[bytes]:
        item = first
        try:
            while not isinstance(item, StreamEnd):
                yield base64.b64decode(item.get("data", ""), validate=True)
                item = await anext(frames)
        except (AgentError, binascii.Error, StopAsyncIteration) as error:
            log.warning("recording stream stopped", error=type(error).__name__)
        finally:
            await frames.aclose()

    entry = AuditEntry(
        action="GET /terminal/recordings/{session_id}",
        result="200",
        actor_id=user.id,
        actor_email=user.email,
        args={"session_id": session_id},
        ip=_client_ip(request.client),
        user_agent=request.headers.get("user-agent"),
    )
    try:
        await record(request.app.state.sessionmaker, entry)
    except Exception:
        log.exception("audit write failed for a recording replay")
    return StreamingResponse(
        body(), media_type="text/plain; charset=utf-8", headers={"Cache-Control": "no-store"}
    )
