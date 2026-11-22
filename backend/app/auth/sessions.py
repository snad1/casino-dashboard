"""Server-side sessions: a random id in a `__Host-` cookie, only its sha256 in the database."""

import base64
import binascii
import hashlib
import hmac
import ipaddress
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import ApiSession, AuditActor, User
from app.db.engine import get_session

SESSION_COOKIE = "__Host-sid"
ABSOLUTE_TTL = timedelta(hours=12)
IDLE_TTL = timedelta(minutes=30)
# Refreshing last_seen_at on every request would add a write per call for no real gain.
TOUCH_AFTER = timedelta(seconds=60)
MAX_USER_AGENT = 512
NOT_SIGNED_IN = "Sign in to continue."


def _encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def decode_session_id(cookie: str | None) -> bytes | None:
    """The raw session id from a cookie value, or None when it is missing or malformed."""
    if not cookie or len(cookie) != 43:
        return None
    try:
        raw = base64.urlsafe_b64decode(cookie + "=")
    except (binascii.Error, ValueError):
        return None
    return raw if len(raw) == 32 else None


def _id_hash(raw: bytes) -> bytes:
    return hashlib.sha256(raw).digest()


def csrf_token(secret: str, raw: bytes) -> str:
    """The CSRF token bound to one session; derived, so nothing extra is stored."""
    return hmac.new(secret.encode(), b"csrf:" + raw, hashlib.sha256).hexdigest()


def _ip_or_none(host: str | None) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host) if host else None
    except ValueError:
        return None


async def new_session(
    db: AsyncSession,
    user_id: int,
    ip: str | None,
    user_agent: str | None,
    *,
    stepped_up: bool = False,
) -> bytes:
    """Store a new session; `stepped_up` when a code was just checked, so it counts as fresh."""
    raw = secrets.token_bytes(32)
    db.add(
        ApiSession(
            id_hash=_id_hash(raw),
            user_id=user_id,
            ip=_ip_or_none(ip),
            user_agent=(user_agent or "")[:MAX_USER_AGENT] or None,
            step_up_at=datetime.now(UTC) if stepped_up else None,
        )
    )
    await db.flush()
    return raw


async def revoke(db: AsyncSession, raw: bytes) -> None:
    await db.execute(delete(ApiSession).where(ApiSession.id_hash == _id_hash(raw)))


async def revoke_all(db: AsyncSession, user_id: int) -> None:
    await db.execute(delete(ApiSession).where(ApiSession.user_id == user_id))


def set_session_cookie(response: Response, raw: bytes) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        _encode(raw),
        max_age=int(ABSOLUTE_TTL.total_seconds()),
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="strict")


async def load_session(db: AsyncSession, raw: bytes) -> tuple[ApiSession, User] | None:
    """The live session and its user; an expired, idle or disabled one is deleted instead."""
    row = (
        await db.execute(
            select(ApiSession, User)
            .join(User, User.id == ApiSession.user_id)
            .where(ApiSession.id_hash == _id_hash(raw))
        )
    ).first()
    if row is None:
        return None
    session, user = row
    now = datetime.now(UTC)
    expired = now - session.created_at > ABSOLUTE_TTL or now - session.last_seen_at > IDLE_TTL
    if expired or user.disabled:
        await db.delete(session)
        await db.commit()
        return None
    if now - session.last_seen_at > TOUCH_AFTER:
        session.last_seen_at = now
        await db.commit()
    return session, user


async def current_user(request: Request, db: Annotated[AsyncSession, Depends(get_session)]) -> User:
    """The signed-in user; an expired, idle or disabled session is deleted and refused."""
    raw = decode_session_id(request.cookies.get(SESSION_COOKIE))
    loaded = await load_session(db, raw) if raw is not None else None
    if raw is None or loaded is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, NOT_SIGNED_IN)
    session, user = loaded
    request.state.session_id = raw
    request.state.api_session = session
    request.state.audit_actor = AuditActor.of(user)
    return user
