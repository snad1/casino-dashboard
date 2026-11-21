"""The live WebSocket's door: a short-lived ticket over HTTP, then the authenticated socket.

The api can run two live pipelines (the daemon's and a per-user bot's), each with its own hub.
A socket starts on the main hub and joins another hub the first time it subscribes to that
service's topics; its sends go through one lock, so frames from both hubs never interleave.
"""

import asyncio
import contextlib
import re
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

import orjson
import structlog
from fastapi import APIRouter, Depends, FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.websockets import WebSocketState

from app.agent_client import AgentClient, AgentError, StreamEnd
from app.api.control import job_access
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
JOB_UNAVAILABLE = "Job output is unavailable right now."
WS_ACTION = "WS /ws"
MAX_JOB_TOPICS = 4
_JOB_TOPIC = re.compile(r"svc:([a-z0-9-]+):job:([0-9a-f]{32})")
_TOPIC_SERVICE = re.compile(r"svc:([a-z0-9-]+)")

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


async def _forward_job(
    hub: Hub, client: Client, agent: AgentClient | None, topic: str, job_id: str
) -> None:
    """Relay a job's output lines from the start, then its end; the client dedupes by `seq`."""
    try:
        if agent is None:
            raise AgentError("unconfigured")
        async for item in agent.stream("job.stream", {"job_id": job_id, "from_seq": 0}):
            if isinstance(item, StreamEnd):
                end = {k: item.data.get(k) for k in ("status", "exit_code")}
                await hub.put(client, {"op": "job_end", "topic": topic, **end})
            else:
                line = {k: item.get(k) for k in ("seq", "stream", "line")}
                await hub.put(client, {"op": "job", "topic": topic, **line})
    except AgentError as error:
        log.warning("job output unavailable", code=error.code)
        await hub.put(client, {"op": "error", "topic": topic, "detail": JOB_UNAVAILABLE})


def _serialised(send: Callable[[str], Awaitable[None]]) -> Callable[[str], Awaitable[None]]:
    lock = asyncio.Lock()

    async def locked(message: str) -> None:
        async with lock:
            await send(message)

    return locked


async def _serve(
    websocket: WebSocket,
    app: FastAPI,
    user: User,
    clients: dict[str, Client],
    send: Callable[[str], Awaitable[None]],
) -> int | None:
    """Handle client frames until the socket closes; a frame over the cap ends it with 1009."""
    hub: Hub | None = app.state.hub
    client = clients.get(hub.svc) if hub is not None else None
    hubs: dict[str, Hub] = app.state.hubs
    jobs: dict[str, asyncio.Task[None]] = {}

    def joined(name: str) -> tuple[Hub, Client] | None:
        """The hub that serves `name`'s service, with this socket's client on it."""
        match = _TOPIC_SERVICE.match(name)
        target = hubs.get(match.group(1)) if match else None
        if target is None:
            return (hub, client) if hub is not None and client is not None else None
        if target.svc not in clients:
            clients[target.svc] = target.register(send, websocket.close)
        return target, clients[target.svc]

    async def reply(message: dict[str, Any]) -> None:
        if hub is not None and client is not None:
            hub.send(client, message)
        else:
            await websocket.send_text(encode(message))

    async def follow_job(hub: Hub, client: Client, name: str, svc: str, job_id: str) -> None:
        async with app.state.sessionmaker() as db:
            allowed = svc == hub.svc and await job_access(db, user, svc, job_id)
        if not allowed:
            await reply({"op": "error", "topic": name, "detail": NOT_PERMITTED})
        elif name not in jobs and len(jobs) >= MAX_JOB_TOPICS:
            await reply({"op": "error", "topic": name, "detail": TOO_MANY_TOPICS})
        else:
            stop_job(name)
            forward = _forward_job(hub, client, app.state.agent, name, job_id)
            jobs[name] = asyncio.create_task(forward)

    def stop_job(name: str) -> None:
        task = jobs.pop(name, None)
        if task is not None:
            task.cancel()

    try:
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
                stop_job(name)
                for svc, member in clients.items():
                    hubs.get(svc, hub).unsubscribe(member, name)
                continue
            job = _JOB_TOPIC.fullmatch(name)
            if job is not None:
                await follow_job(hub, client, name, job.group(1), job.group(2))
                continue
            target, member = joined(name) or (hub, client)
            topic = parse_topic(target.svc, name)
            async with app.state.sessionmaker() as db:
                allowed = topic is not None and await topic_allowed(db, user.id, name)
            if topic is None or not allowed:
                await reply({"op": "error", "topic": name, "detail": NOT_PERMITTED})
            elif not target.subscribe(member, topic):
                await reply({"op": "error", "topic": name, "detail": TOO_MANY_TOPICS})
    finally:
        for task in jobs.values():
            task.cancel()
        for task in jobs.values():
            with contextlib.suppress(asyncio.CancelledError):
                await task


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
        send = _serialised(websocket.send_text)
        clients: dict[str, Client] = {}
        if hub is not None:
            clients[hub.svc] = hub.register(send, websocket.close)
        reader = asyncio.create_task(_serve(websocket, app, user, clients, send))
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
            hubs: dict[str, Hub] = app.state.hubs
            for svc, member in clients.items():
                await hubs[svc].unregister(member)
    finally:
        registry.release(user.id)
