"""Sign-in through the Telegram bot's `/web` link: one use, five minutes, one Telegram user.

The bot signs `base64url(payload).base64url(mac)`, where the payload is compact JSON with sorted
keys `{"exp", "id", "uid", "v"}` and the mac is HMAC-SHA256 over the first segment with the
secret both sides share (`contracts/fixtures/tg_login_v1/`). The link carries the token in the
URL fragment, so it reaches neither a server log nor a Referer header.

A valid token signs the browser into the account tied to that Telegram user, made on the first
visit: no email, no password, the `tg_user` role. Each link id is recorded once used. Every
refusal reads the same, so a caller learns nothing about why.
"""

import base64
import binascii
import hashlib
import hmac
import json
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import AuditActor, Role, TgLoginToken, User, UserRole
from app.auth.ratelimit import check_login_rate
from app.auth.sessions import (
    SESSION_COOKIE,
    decode_session_id,
    new_session,
    revoke,
    set_session_cookie,
)
from app.db.engine import get_session

VERSION = 1
TTL_S = 300
SKEW_S = 30
KEEP_USED = timedelta(days=1)
LINK_INVALID = "This link is invalid or expired. Send /web to the bot again."
NO_PASSWORD = "!"  # noqa: S105  # not a hash, so no password ever verifies against it
_ID = re.compile(r"[A-Za-z0-9_-]{16,64}")

router = APIRouter(prefix="/auth", tags=["auth"])
Db = Annotated[AsyncSession, Depends(get_session)]


class TelegramLoginIn(BaseModel):
    token: str = Field(min_length=1, max_length=512)


def _b64decode(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def read_token(secret: bytes, token: str, now: float) -> tuple[int, str] | None:
    """The (uid, link id) a valid token names, or None for anything else."""
    body, _, mac = token.partition(".")
    if not body or not mac:
        return None
    try:
        expected = hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64decode(mac), expected):
            return None
        payload: Any = json.loads(_b64decode(body))
    except (UnicodeEncodeError, binascii.Error, ValueError):
        return None
    if not isinstance(payload, dict) or set(payload) != {"v", "uid", "id", "exp"}:
        return None
    uid, link_id, exp = payload["uid"], payload["id"], payload["exp"]
    if payload["v"] != VERSION or type(uid) is not int or type(exp) is not int:
        return None
    if not 1 <= uid < 2**63 or not isinstance(link_id, str) or not _ID.fullmatch(link_id):
        return None
    if not now - SKEW_S <= exp <= now + TTL_S + SKEW_S:
        return None
    return uid, link_id


async def _account(db: AsyncSession, uid: int) -> User:
    user = await db.scalar(select(User).where(User.tg_uid == uid))
    if user is None:
        user = User(email=None, tg_uid=uid, password_hash=NO_PASSWORD)
        db.add(user)
        await db.flush()
        db.add(UserRole(user_id=user.id, service_id=None, role=Role.TG_USER))
    return user


@router.post("/telegram", status_code=status.HTTP_204_NO_CONTENT)
async def telegram_login(
    body: TelegramLoginIn, request: Request, response: Response, db: Db
) -> None:
    """Exchange the bot's one-time link for a session (404 while no link secret is set)."""
    secret = request.app.state.tg_login_secret
    if secret is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    ip = request.client.host if request.client else None
    await check_login_rate(request.app.state.redis, f"telegram-link:{ip}", ip)
    found = read_token(secret.encode(), body.token, time.time())
    if found is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, LINK_INVALID)
    uid, link_id = found
    now = datetime.now(UTC)
    await db.execute(delete(TgLoginToken).where(TgLoginToken.used_at < now - KEEP_USED))
    burned = await db.execute(
        insert(TgLoginToken)
        .values(id_hash=hashlib.sha256(link_id.encode()).digest())
        .on_conflict_do_nothing()
        .returning(TgLoginToken.id_hash)
    )
    if burned.first() is None:
        await db.rollback()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, LINK_INVALID)
    user = await _account(db, uid)
    if user.disabled:
        await db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, LINK_INVALID)
    if old := decode_session_id(request.cookies.get(SESSION_COOKIE)):
        await revoke(db, old)
    raw = await new_session(db, user.id, ip, request.headers.get("user-agent"))
    await db.commit()
    set_session_cookie(response, raw)
    request.state.audit_actor = AuditActor.of(user)
