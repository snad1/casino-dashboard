"""The live WebSocket's door: a short-lived ticket over HTTP, then the authenticated socket."""

import asyncio
from typing import Annotated, Any

import orjson
import structlog
from fastapi import APIRouter, Depends, FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.websockets import WebSocketState

from app.audit import AuditEntry, record
from app.auth.models import User
from app.auth.sessions import current_user
from app.ws.auth import (
    CLOSE_SIGNED_OUT,
    MAX_FRAME_BYTES,
    TICKET_TTL_SECONDS,
    TOO_MANY,
    ConnectionRegistry,
    Denied,
    Handshake,
    authenticate,
    issue_ticket,
    still_signed_in,
    topic_allowed,
)
from app.ws.hub import Client, Hub, encode, parse_topic

CLOSE_TOO_BIG = 1009
UNKNOWN_MESSAGE = "Unknown message."
NOT_PERMITTED = "You do not have permission to see that."
TOO_MANY_TOPICS = "Too many live topics on one connection."
LIVE_OFF = "Live updates are off."
WS_ACTION = "WS /ws"

log = structlog.get_logger("app.ws")
router = APIRouter(tags=["live"])


class Ticket(BaseModel):
    ticket: str
    expires_in: int


@router.post("/ws-ticket", response_model=Ticket)
async def ws_ticket(_: Annotated[User, Depends(current_user)], request: Request) -> Ticket:
    """A one-time pass for opening the live socket from this session within 30 s."""
    secret: str = request.app.state.session_secret
    return Ticket(
        ticket=issue_ticket(secret, request.state.session_id), expires_in=TICKET_TTL_SECONDS
    )


async def _audit(websocket: WebSocket, user: User | None, result: str) -> None:
    client = websocket.client
    entry = AuditEntry(
        action=WS_ACTION,
        result=result,
        actor_id=user.id if user else None,
        actor_email=user.email if user else None,
        ip=client.host if client else None,
        user_agent=websocket.headers.get("user-agent"),
    )
    try:
        await record(websocket.app.state.sessionmaker, entry)
    except Exception:
        log.exception("audit write failed for a websocket connect")


async def _serve(
    websocket: WebSocket, app: FastAPI, user: User, client: Client | None
) -> int | None:
    """Handle client frames until the socket closes; a frame over the cap ends it with 1009."""
    hub: Hub | None = app.state.hub

    async def reply(message: dict[str, Any]) -> None:
        if hub is not None and client is not None:
            hub.send(client, message)
        else:
            await websocket.send_text(encode(message))

    while True:
        frame = await websocket.receive()
        if frame["type"] == "websocket.disconnect":
            return None
        data = frame.get("bytes") or (frame.get("text") or "").encode()
        if len(data) > MAX_FRAME_BYTES:
            return CLOSE_TOO_BIG
        request = _parse(data)
        if request is None:
            await reply({"op": "error", "detail": UNKNOWN_MESSAGE})
            continue
        op, name = request
        if hub is None or client is None:
            await reply({"op": "error", "topic": name, "detail": LIVE_OFF})
            continue
        if op == "unsubscribe":
            hub.unsubscribe(client, name)
            continue
        topic = parse_topic(hub.svc, name)
        async with app.state.sessionmaker() as db:
            allowed = topic is not None and await topic_allowed(db, user.id, name)
        if topic is None or not allowed:
            await reply({"op": "error", "topic": name, "detail": NOT_PERMITTED})
        elif not hub.subscribe(client, topic):
            await reply({"op": "error", "topic": name, "detail": TOO_MANY_TOPICS})


def _parse(data: bytes) -> tuple[str, str] | None:
    try:
        message = orjson.loads(data)
    except orjson.JSONDecodeError:
        return None
    if not isinstance(message, dict):
        return None
    op, topic = message.get("op"), message.get("topic")
    if op not in ("subscribe", "unsubscribe") or not isinstance(topic, str) or len(topic) > 120:
        return None
    return op, topic


async def _watch_session(app: FastAPI, raw: bytes) -> int:
    while True:
        await asyncio.sleep(app.state.ws_recheck_seconds)
        async with app.state.sessionmaker() as db:
            if not await still_signed_in(db, raw):
                return CLOSE_SIGNED_OUT


@router.websocket("/ws")
async def live(websocket: WebSocket) -> None:
    app: FastAPI = websocket.app
    registry: ConnectionRegistry = app.state.ws_registry
    handshake = Handshake(
        origin=websocket.headers.get("origin"),
        cookies=dict(websocket.cookies),
        ticket=websocket.query_params.get("ticket"),
    )
    try:
        async with app.state.sessionmaker() as db:
            user, raw = await authenticate(
                handshake, db, app.state.redis, app.state.session_secret, app.state.allowed_origins
            )
        if not registry.claim(user.id):
            raise Denied(429, TOO_MANY)
    except Denied as denied:
        await _audit(websocket, None, str(denied.status))
        body = JSONResponse({"detail": denied.detail}, status_code=denied.status)
        await websocket.send_denial_response(body)
        return
    try:
        await _audit(websocket, user, "accepted")
        await websocket.accept()
        hub: Hub | None = app.state.hub
        client = hub.register(websocket.send_text, websocket.close) if hub is not None else None
        reader = asyncio.create_task(_serve(websocket, app, user, client))
        watcher = asyncio.create_task(_watch_session(app, raw))
        try:
            done, pending = await asyncio.wait(
                {reader, watcher}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            code = next(iter(done)).result()
            if code is not None and websocket.client_state == WebSocketState.CONNECTED:
                await websocket.close(code)
        finally:
            if hub is not None and client is not None:
                await hub.unregister(client)
    finally:
        registry.release(user.id)
