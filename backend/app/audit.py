"""Append-only, hash-chained audit log.

Each row's `hash` is sha256 of the previous row's hash followed by the row's canonical JSON,
so changing or removing any row breaks every hash after it. Writers take a transaction-level
advisory lock, so two parallel writes never chain onto the same predecessor. The api's
database role may only INSERT and SELECT here, and a trigger refuses UPDATE, DELETE and
TRUNCATE even for the table owner.
"""

import hashlib
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from ipaddress import IPv4Address, IPv6Address, ip_address
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import BigInteger, DateTime, Identity, LargeBinary, Text, select, text
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.auth.models import User
from app.auth.permissions import Perm, require, roles_for
from app.db.engine import get_session
from app.db.models import Base
from app.logging import REDACTED, current_request_id, scrub

GENESIS = bytes(32)
# Any constant works; it only has to be the same for every writer.
CHAIN_LOCK = 0x617564697400
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
MAX_BODY = 16 * 1024
EXTRA_SECRET_KEYS = frozenset({"code"})

log = structlog.get_logger("app.audit")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actor_id: Mapped[int | None] = mapped_column(BigInteger)
    actor_email: Mapped[str | None] = mapped_column(Text)
    roles: Mapped[str | None] = mapped_column(Text)
    service: Mapped[str | None] = mapped_column(Text, index=True)
    action: Mapped[str] = mapped_column(Text, index=True)
    args: Mapped[dict[str, Any]] = mapped_column(JSONB)
    result: Mapped[str] = mapped_column(Text)
    ip: Mapped[IPv4Address | IPv6Address | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(Text)
    prev_hash: Mapped[bytes] = mapped_column(LargeBinary)
    hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True)


@dataclass
class AuditEntry:
    action: str
    result: str
    actor_id: int | None = None
    actor_email: str | None = None
    roles: str | None = None
    service: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    ip: str | None = None
    user_agent: str | None = None
    request_id: str | None = None


def redact(args: dict[str, Any]) -> dict[str, Any]:
    scrubbed: dict[str, Any] = scrub(args)
    return {key: REDACTED if key in EXTRA_SECRET_KEYS else value for key, value in scrubbed.items()}


def _canonical(row: AuditLog) -> bytes:
    fields = {
        "ts": row.ts.astimezone(UTC).isoformat(),
        "actor_id": row.actor_id,
        "actor_email": row.actor_email,
        "roles": row.roles,
        "service": row.service,
        "action": row.action,
        "args": row.args,
        "result": row.result,
        "ip": str(row.ip) if row.ip is not None else None,
        "user_agent": row.user_agent,
        "request_id": row.request_id,
    }
    return json.dumps(fields, sort_keys=True, separators=(",", ":"), default=str).encode()


def chain_hash(prev_hash: bytes, row: AuditLog) -> bytes:
    return hashlib.sha256(prev_hash + _canonical(row)).digest()


def _ip(value: str | None) -> IPv4Address | IPv6Address | None:
    try:
        return ip_address(value) if value else None
    except ValueError:
        return None


async def record(sessionmaker: async_sessionmaker[AsyncSession], entry: AuditEntry) -> None:
    """Append one row in its own transaction, chained onto the newest row."""
    async with sessionmaker() as db, db.begin():
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": CHAIN_LOCK})
        prev = await db.scalar(select(AuditLog.hash).order_by(AuditLog.id.desc()).limit(1))
        row = AuditLog(
            ts=datetime.now(UTC),
            actor_id=entry.actor_id,
            actor_email=entry.actor_email,
            roles=entry.roles,
            service=entry.service,
            action=entry.action,
            args=redact(entry.args),
            result=entry.result,
            ip=_ip(entry.ip),
            user_agent=(entry.user_agent or "")[:512] or None,
            request_id=entry.request_id,
            prev_hash=prev or GENESIS,
        )
        row.hash = chain_hash(row.prev_hash, row)
        db.add(row)


async def _rows(db: AsyncSession, batch: int = 1000) -> AsyncIterator[AuditLog]:
    last = 0
    while True:
        query = select(AuditLog).where(AuditLog.id > last).order_by(AuditLog.id).limit(batch)
        rows = list(await db.scalars(query))
        if not rows:
            return
        for row in rows:
            yield row
        last = rows[-1].id


async def verify_chain(db: AsyncSession) -> int | None:
    """The id of the first row whose link or content does not match, or None if intact."""
    prev = GENESIS
    async for row in _rows(db):
        if row.prev_hash != prev or row.hash != chain_hash(prev, row):
            return row.id
        prev = row.hash
    return None


def _body_args(body: bytes, content_type: str) -> dict[str, Any]:
    if not body or "json" not in content_type:
        return {}
    try:
        parsed = json.loads(body)
    except ValueError:
        return {"body": "not valid JSON"}
    return parsed if isinstance(parsed, dict) else {"body": "not a JSON object"}


class AuditMiddleware:
    """Record every state-changing HTTP request, whatever its outcome.

    The row is written after the handler finishes, from the status it sent. A failure to
    write is logged and never turns a completed action into an error for the user.
    """

    def __init__(self, app: ASGIApp, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self.app = app
        self.sessionmaker = sessionmaker

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in UNSAFE_METHODS:
            await self.app(scope, receive, send)
            return
        body = bytearray()
        status = 500

        async def capture_receive() -> Message:
            message = await receive()
            if message["type"] == "http.request" and len(body) < MAX_BODY:
                body.extend(message.get("body", b"")[: MAX_BODY - len(body)])
            return message

        async def capture_send(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, capture_receive, capture_send)
        finally:
            await self._write(scope, bytes(body), status)

    async def _write(self, scope: Scope, body: bytes, status: int) -> None:
        try:
            headers = {k.decode().lower(): v.decode("latin-1") for k, v in scope["headers"]}
            route = scope.get("route")
            path = getattr(route, "path_format", None) or scope["path"]
            path_params: dict[str, Any] = scope.get("path_params", {})
            actor: User | None = scope.get("state", {}).get("audit_actor")
            service = path_params.get("service")
            roles = None
            if actor is not None:
                async with self.sessionmaker() as db:
                    roles = ",".join(sorted(await roles_for(db, actor.id, service))) or None
            client = scope.get("client")
            entry = AuditEntry(
                action=f"{scope['method']} {path}",
                result=str(status),
                actor_id=actor.id if actor else None,
                actor_email=actor.email if actor else None,
                roles=roles,
                service=service,
                args={**path_params, **_body_args(body, headers.get("content-type", ""))},
                ip=client[0] if client else None,
                user_agent=headers.get("user-agent"),
                request_id=current_request_id(),
            )
            await record(self.sessionmaker, entry)
        except Exception:
            log.exception("audit write failed", path=scope.get("path"))


class AuditOut(BaseModel):
    id: int
    ts: datetime
    actor_id: int | None
    actor_email: str | None
    roles: str | None
    service: str | None
    action: str
    args: dict[str, Any]
    result: str
    ip: str | None
    request_id: str | None


class AuditPage(BaseModel):
    items: list[AuditOut]
    next_before_id: int | None


router = APIRouter(tags=["audit"])


@router.get("/audit", response_model=AuditPage)
async def list_audit(
    _: Annotated[User, Depends(require(Perm.AUDIT_READ))],
    db: Annotated[AsyncSession, Depends(get_session)],
    actor: Annotated[str | None, Query(max_length=320)] = None,
    action: Annotated[str | None, Query(max_length=200)] = None,
    service: Annotated[str | None, Query(max_length=100)] = None,
    since: datetime | None = None,
    until: datetime | None = None,
    before_id: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> AuditPage:
    """Newest first; pass `next_before_id` back as `before_id` for the next page."""
    query = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit + 1)
    if actor is not None:
        query = query.where(AuditLog.actor_email == actor)
    if action is not None:
        query = query.where(AuditLog.action == action)
    if service is not None:
        query = query.where(AuditLog.service == service)
    if since is not None:
        query = query.where(AuditLog.ts >= since)
    if until is not None:
        query = query.where(AuditLog.ts < until)
    if before_id is not None:
        query = query.where(AuditLog.id < before_id)
    rows = list(await db.scalars(query))
    more = len(rows) > limit
    rows = rows[:limit]
    items = [
        AuditOut(
            id=row.id,
            ts=row.ts,
            actor_id=row.actor_id,
            actor_email=row.actor_email,
            roles=row.roles,
            service=row.service,
            action=row.action,
            args=row.args,
            result=row.result,
            ip=str(row.ip) if row.ip is not None else None,
            request_id=row.request_id,
        )
        for row in rows
    ]
    return AuditPage(items=items, next_before_id=rows[-1].id if more and rows else None)
