"""Who may open the live WebSocket, and for how long.

A socket opens only when all of these hold, checked before the handshake is accepted: the
`Origin` is ours, the `__Host-sid` cookie names a live session, and the `ticket` query value
was issued to that same session less than 30 s ago and has not been used. The ticket is
signed rather than stored; its single use is recorded in Redis. Open sockets are capped per
user and the session is re-checked every minute, so logging out or being disabled ends them.
"""

import base64
import binascii
import hashlib
import hmac
import re
import secrets
import time
from collections import Counter
from dataclasses import dataclass

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.auth.permissions import ROLE_PERMS, Perm, roles_for
from app.auth.sessions import SESSION_COOKIE, decode_session_id, load_session

TICKET_TTL_SECONDS = 30
MAX_SOCKETS_PER_USER = 5
RECHECK_SECONDS = 60.0
MAX_FRAME_BYTES = 16 * 1024
USED_TICKETS_KEY = "auth:ws:used"
LIVE_TICKET = b"ws"
TERMINAL_TICKET = b"term"
CLOSE_SIGNED_OUT = 4401
CLOSE_FORBIDDEN = 4403
_TOPIC = re.compile(r"svc:([a-z0-9-]+)(?::[A-Za-z0-9:_-]+)?")


NOT_SIGNED_IN = "Sign in to continue."
NOT_ALLOWED = "This live connection is not allowed. Reload the page."
TOO_MANY = "Too many live connections. Close another tab and try again."
UNAVAILABLE = "Live updates are temporarily unavailable."


class Denied(Exception):
    """The handshake is refused with this HTTP status; the body never says more than it must."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _signature(secret: str, raw_session: bytes, payload: bytes, purpose: bytes) -> str:
    message = purpose + b":" + hashlib.sha256(raw_session).digest() + payload
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def issue_ticket(
    secret: str, raw_session: bytes, now: float | None = None, purpose: bytes = LIVE_TICKET
) -> str:
    """A signed one-time pass; `purpose` keeps a live-socket ticket from opening the terminal."""
    expires = int(now if now is not None else time.time()) + TICKET_TTL_SECONDS
    payload = secrets.token_bytes(16) + expires.to_bytes(8, "big")
    return f"{_b64(payload)}.{_signature(secret, raw_session, payload, purpose)}"


def check_ticket(
    secret: str,
    raw_session: bytes,
    ticket: str,
    now: float | None = None,
    purpose: bytes = LIVE_TICKET,
) -> tuple[str, int] | None:
    """(nonce, expiry) for a ticket this session was given for `purpose` and still valid."""
    encoded, _, signature = ticket.partition(".")
    try:
        payload = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    except (binascii.Error, ValueError):
        return None
    if len(payload) != 24:
        return None
    if not hmac.compare_digest(signature, _signature(secret, raw_session, payload, purpose)):
        return None
    expires = int.from_bytes(payload[16:], "big")
    if expires < (now if now is not None else time.time()):
        return None
    return payload[:16].hex(), expires


async def redeem(redis: Redis, nonce: str, expires: int) -> bool:
    """Mark the ticket used; False when it already was. Expired entries are pruned."""
    async with redis.pipeline(transaction=True) as pipe:
        pipe.zremrangebyscore(USED_TICKETS_KEY, 0, int(time.time()) - 1)
        pipe.zadd(USED_TICKETS_KEY, {nonce: expires}, nx=True)
        pipe.pexpire(USED_TICKETS_KEY, (TICKET_TTL_SECONDS + 5) * 1000)
        results = await pipe.execute()
    added: int = results[1]
    return added == 1


class ConnectionRegistry:
    """Open sockets per user in this process (the api runs one worker)."""

    def __init__(self, limit: int = MAX_SOCKETS_PER_USER) -> None:
        self.limit = limit
        self._open: Counter[int] = Counter()

    def claim(self, user_id: int) -> bool:
        if self._open[user_id] >= self.limit:
            return False
        self._open[user_id] += 1
        return True

    def release(self, user_id: int) -> None:
        self._open[user_id] -= 1
        if self._open[user_id] <= 0:
            del self._open[user_id]


@dataclass(frozen=True)
class Handshake:
    origin: str | None
    cookies: dict[str, str]
    ticket: str | None


async def authenticate(
    handshake: Handshake,
    db: AsyncSession,
    redis: Redis,
    secret: str,
    allowed_origins: list[str],
    purpose: bytes = LIVE_TICKET,
) -> tuple[User, bytes]:
    """The user and raw session id behind a handshake, or `Denied`."""
    if handshake.origin not in allowed_origins:
        raise Denied(403, NOT_ALLOWED)
    raw = decode_session_id(handshake.cookies.get(SESSION_COOKIE))
    loaded = await load_session(db, raw) if raw is not None else None
    if raw is None or loaded is None:
        raise Denied(401, NOT_SIGNED_IN)
    checked = check_ticket(secret, raw, handshake.ticket or "", purpose=purpose)
    if checked is None:
        raise Denied(403, NOT_ALLOWED)
    try:
        fresh = await redeem(redis, *checked)
    except RedisError:
        raise Denied(503, UNAVAILABLE) from None
    if not fresh:
        raise Denied(403, NOT_ALLOWED)
    return loaded[1], raw


async def still_signed_in(db: AsyncSession, raw: bytes) -> bool:
    """Re-check an open socket's session; the check also counts as activity."""
    return await load_session(db, raw) is not None


async def topic_allowed(db: AsyncSession, user_id: int, topic: str) -> bool:
    """`svc:<name>` and its sub-topics need `view` on that service; nothing else is offered."""
    match = _TOPIC.fullmatch(topic)
    if match is None:
        return False
    roles = await roles_for(db, user_id, match.group(1))
    return any(Perm.VIEW in ROLE_PERMS[role] for role in roles)
