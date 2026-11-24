"""Deliver alerts by email, Telegram and Web Push, from the mailer process.

The engine writes one delivery per channel; `fan_out` turns each into one delivery per user
who has `view` on the service, is not disabled and has that channel on (for push, one per
device the user turned it on for). `deliver_once` sends
the oldest due one: inside the user's quiet hours it is held until they end, past the
channel's hourly limit it waits for room, and a failure backs off until it is dead. Email
goes into the mail outbox, so the mailer's own retries handle it from there.

Telegram messages are plain text (no parse mode), so nothing in an alert can become markup
or a link. The bot token sits in every request URL, so no error or log line ever carries a
URL, and the HTTP client's own request log is turned down to warnings. A user links a chat
by sending `/start <code>` to the bot in a private chat; the code is one-time, stored
hashed and good for ten minutes.
"""

import asyncio
import hashlib
import logging
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx2
import structlog
from sqlalchemy import func, literal, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.models import AlertDelivery, AlertEvent, PushSubscription, UserChannels
from app.alerts.push import PushError, PushSender, Subscription, payload
from app.auth.models import User
from app.auth.permissions import ROLE_PERMS, Perm, roles_for
from app.mail import templates
from app.mail.models import DEAD, PENDING, SENT
from app.mail.outbox import MAX_ATTEMPTS, enqueue, retry_delay

SENT_CHANNELS = ("email", "telegram", "push")
HOURLY_LIMIT = {"email": 10, "telegram": 30, "push": 30}
LIMIT_WINDOW = timedelta(hours=1)
FAN_OUT_BATCH = 50
LINK_TTL = timedelta(minutes=10)
TELEGRAM_MAX = 4096
TELEGRAM_URL = "https://api.telegram.org"
TIMEOUT_SECONDS = 10.0
POLL_TIMEOUT_S = 25
POLL_RETRY_S = 5.0
URL_LOGGERS = ("httpx2", "httpcore2")

LINKED = "Linked to the casino dashboard. Alerts will arrive here."
REFUSED = "That link is not valid any more. Make a new one in the dashboard."
OPTED_OUT = "the recipient turned this channel off"

_CONTROL = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")
_CODE = re.compile(r"[A-Za-z0-9_-]{16,64}")

log = structlog.get_logger("app.alerts.channels")


def plain(text: str, limit: int) -> str:
    """Text with control characters (other than newlines) removed, cut to `limit`."""
    return _CONTROL.sub("", text)[:limit]


class TelegramError(Exception):
    """A failed Bot API call; `permanent` when retrying cannot help (blocked, bad chat)."""

    def __init__(self, reason: str, permanent: bool = False, retry_after: float | None = None):
        super().__init__(reason)
        self.permanent = permanent
        self.retry_after = retry_after


class TelegramClient:
    def __init__(self, token: str, transport: httpx2.AsyncBaseTransport | None = None) -> None:
        for name in URL_LOGGERS:
            logging.getLogger(name).setLevel(logging.WARNING)
        self._base = f"{TELEGRAM_URL}/bot{token}"
        self._transport = transport

    async def _call(self, method: str, payload: dict[str, Any], wait_s: float) -> Any:
        try:
            async with httpx2.AsyncClient(timeout=wait_s, transport=self._transport) as client:
                response = await client.post(f"{self._base}/{method}", json=payload)
        except httpx2.HTTPError as error:
            raise TelegramError(f"telegram unreachable: {type(error).__name__}") from None
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code == 429:
            retry = (body.get("parameters") or {}).get("retry_after")
            wait = float(retry) if isinstance(retry, int | float) else None
            raise TelegramError("telegram rate limited", retry_after=wait)
        if response.status_code in (400, 403, 404):
            raise TelegramError(f"telegram refused: HTTP {response.status_code}", permanent=True)
        if response.status_code >= 300 or not body.get("ok"):
            raise TelegramError(f"telegram failed: HTTP {response.status_code}")
        return body.get("result")

    async def send(self, chat_id: int, text: str) -> None:
        await self._call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": plain(text, TELEGRAM_MAX),
                "disable_web_page_preview": True,
            },
            TIMEOUT_SECONDS,
        )

    async def updates(self, offset: int | None) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": POLL_TIMEOUT_S, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        result = await self._call("getUpdates", payload, POLL_TIMEOUT_S + TIMEOUT_SECONDS)
        return [u for u in result if isinstance(u, dict)] if isinstance(result, list) else []


def quiet_until(channels: UserChannels, now: datetime, zone: ZoneInfo) -> datetime | None:
    """When the user's quiet hours end, if `now` is inside them; None otherwise."""
    start, end = channels.quiet_start, channels.quiet_end
    if start is None or end is None or start == end:
        return None
    local = now.astimezone(zone)
    clock = local.time().replace(tzinfo=None)
    inside = start <= clock < end if start < end else clock >= start or clock < end
    if not inside:
        return None
    ends = datetime.combine(local.date(), end, zone)
    if ends <= local:
        ends += timedelta(days=1)
    return ends.astimezone(UTC)


async def _can_view(db: AsyncSession, user_id: int, service: str, lane: int | None = None) -> bool:
    """Whether the user may see `service`, or (with `lane`) that lane of it."""
    roles = await roles_for(db, user_id, service, lane)
    return any(Perm.VIEW in ROLE_PERMS[role] for role in roles)


async def _recipients(
    db: AsyncSession, service: str, channel: str, lane: int | None = None
) -> list[tuple[int, int | None]]:
    """(user, push subscription) pairs to deliver to: one per user, or one per device for push.

    An event on a lane also reaches users who may see only that lane.
    """
    query = (
        select(User.id, PushSubscription.id if channel == "push" else literal(None))
        .join(UserChannels, UserChannels.user_id == User.id)
        .where(User.disabled.is_(False))
    )
    if channel == "email":
        query = query.where(UserChannels.email_enabled.is_(True))
    elif channel == "telegram":
        query = query.where(
            UserChannels.telegram_enabled.is_(True), UserChannels.telegram_chat_id.is_not(None)
        )
    else:
        query = query.join(PushSubscription, PushSubscription.user_id == User.id)
    pairs = [(uid, sub) for uid, sub in (await db.execute(query)).all()]
    allowed = {uid for uid in {u for u, _ in pairs} if await _can_view(db, uid, service, lane)}
    return [(uid, sub) for uid, sub in pairs if uid in allowed]


async def fan_out(db: AsyncSession, now: datetime) -> int:
    """Expand waiting channel deliveries into one per recipient; returns how many expanded."""
    rows = list(
        await db.execute(
            select(AlertDelivery, AlertEvent.service, AlertEvent.lane)
            .join(AlertEvent, AlertEvent.id == AlertDelivery.event_id)
            .where(
                AlertDelivery.user_id.is_(None),
                AlertDelivery.status == PENDING,
                AlertDelivery.channel.in_(SENT_CHANNELS),
            )
            .order_by(AlertDelivery.id)
            .limit(FAN_OUT_BATCH)
            .with_for_update(of=AlertDelivery, skip_locked=True)
        )
    )
    for row, service, lane in rows:
        targets = await _recipients(db, service, row.channel, lane)
        db.add_all(
            AlertDelivery(
                event_id=row.event_id,
                user_id=uid,
                subscription_id=sub,
                channel=row.channel,
                next_attempt_at=now,
            )
            for uid, sub in targets
        )
        row.status, row.sent_at = SENT, now
    return len(rows)


def _fail(row: AlertDelivery, error: TelegramError | PushError, now: datetime) -> None:
    row.attempts += 1
    row.last_error = str(error)[:200]
    if error.permanent or row.attempts >= MAX_ATTEMPTS:
        row.status = DEAD
        log.error("alert delivery dead", delivery=row.id, attempts=row.attempts, error=str(error))
        return
    wait = timedelta(seconds=error.retry_after) if error.retry_after else retry_delay(row.attempts)
    row.next_attempt_at = now + wait
    log.warning("alert delivery failed", delivery=row.id, attempts=row.attempts, error=str(error))


async def _room_at(db: AsyncSession, row: AlertDelivery, now: datetime) -> datetime | None:
    """When the user has room again on this channel, or None when they have room now.

    Alerts are counted, not deliveries, so one alert on three devices counts once.
    """
    limit = HOURLY_LIMIT[row.channel]
    sent = (
        select(func.min(AlertDelivery.sent_at).label("at"))
        .where(
            AlertDelivery.user_id == row.user_id,
            AlertDelivery.channel == row.channel,
            AlertDelivery.status == SENT,
            AlertDelivery.sent_at > now - LIMIT_WINDOW,
            AlertDelivery.event_id != row.event_id,
        )
        .group_by(AlertDelivery.event_id)
        .subquery()
    )
    recent = await db.scalar(select(func.count()).select_from(sent)) or 0
    if recent < limit:
        return None
    oldest = await db.scalar(select(sent.c.at).order_by(sent.c.at).offset(recent - limit).limit(1))
    return (oldest or now) + LIMIT_WINDOW


async def deliver_once(
    db: AsyncSession,
    telegram: TelegramClient | None,
    zone: ZoneInfo,
    now: datetime,
    push: PushSender | None = None,
) -> bool:
    """Handle the oldest due per-user delivery. Returns False when nothing was due."""
    channels = ["email"]
    if telegram is not None:
        channels.append("telegram")
    if push is not None:
        channels.append("push")
    found = (
        await db.execute(
            select(AlertDelivery, AlertEvent, User, UserChannels)
            .join(AlertEvent, AlertEvent.id == AlertDelivery.event_id)
            .join(User, User.id == AlertDelivery.user_id)
            .join(UserChannels, UserChannels.user_id == AlertDelivery.user_id)
            .where(
                AlertDelivery.user_id.is_not(None),
                AlertDelivery.status == PENDING,
                AlertDelivery.channel.in_(channels),
                AlertDelivery.next_attempt_at <= now,
            )
            .order_by(AlertDelivery.next_attempt_at, AlertDelivery.id)
            .limit(1)
            .with_for_update(of=AlertDelivery, skip_locked=True)
        )
    ).first()
    if found is None:
        return False
    row, event, user, prefs = found
    on = {
        "email": prefs.email_enabled,
        "telegram": prefs.telegram_enabled and prefs.telegram_chat_id is not None,
        "push": row.subscription_id is not None,
    }[row.channel]
    if user.disabled or not on:
        row.status, row.last_error = DEAD, OPTED_OUT
        return True
    held = quiet_until(prefs, now, zone) or await _room_at(db, row, now)
    if held is not None:
        row.next_attempt_at = held
        return True
    text = plain(event.message, TELEGRAM_MAX)
    if row.channel == "email":
        enqueue(db, user.email, templates.alert(plain(event.message, 2000)))
    elif row.channel == "telegram":
        if telegram is None or prefs.telegram_chat_id is None:
            return True
        try:
            await telegram.send(prefs.telegram_chat_id, text)
        except TelegramError as error:
            _fail(row, error, now)
            return True
    else:
        device = await db.get(PushSubscription, row.subscription_id)
        if push is None or device is None:
            return True
        target = Subscription(device.endpoint, device.p256dh, device.auth)
        try:
            await push.send(target, payload(plain(event.message, 500), prefs.push_private))
        except PushError as error:
            if error.gone:
                log.info(
                    "push subscription removed: the push service no longer has it", device=device.id
                )
                await db.delete(device)
                return True
            _fail(row, error, now)
            return True
        device.last_used_at = now
    row.status, row.sent_at, row.last_error = SENT, now, None
    return True


def _hash(code: str) -> bytes:
    return hashlib.sha256(code.encode()).digest()


async def issue_link_code(db: AsyncSession, user_id: int, now: datetime) -> str:
    """A new one-time Telegram link code for `user_id`; any older one stops working."""
    code = secrets.token_urlsafe(24)
    values = {
        "telegram_link_hash": _hash(code),
        "telegram_link_expires_at": now + LINK_TTL,
        "updated_at": now,
    }
    statement = insert(UserChannels).values(user_id=user_id, **values)
    await db.execute(
        statement.on_conflict_do_update(index_elements=[UserChannels.user_id], set_=values)
    )
    return code


async def handle_update(
    db: AsyncSession, update: dict[str, Any], telegram: TelegramClient, now: datetime
) -> None:
    """Link a chat from `/start <code>`; anything else is ignored. Replies are fixed texts."""
    message = update.get("message")
    if not isinstance(message, dict):
        return
    chat = message.get("chat")
    text = message.get("text")
    if not isinstance(chat, dict) or not isinstance(text, str) or not text.startswith("/start"):
        return
    chat_id = chat.get("id")
    if not isinstance(chat_id, int) or isinstance(chat_id, bool):
        return
    parts = text.split(maxsplit=1)
    code = parts[1].strip() if len(parts) == 2 else ""
    prefs = None
    if chat.get("type") == "private" and _CODE.fullmatch(code):
        prefs = await db.scalar(
            select(UserChannels)
            .where(UserChannels.telegram_link_hash == _hash(code))
            .with_for_update()
        )
    if (
        prefs is not None
        and prefs.telegram_link_expires_at
        and prefs.telegram_link_expires_at > now
    ):
        others = await db.scalars(
            select(UserChannels).where(
                UserChannels.telegram_chat_id == chat_id, UserChannels.user_id != prefs.user_id
            )
        )
        for other in others:
            other.telegram_chat_id, other.telegram_enabled = None, False
        await db.flush()
        prefs.telegram_chat_id, prefs.telegram_enabled = chat_id, True
        prefs.telegram_link_hash = prefs.telegram_link_expires_at = None
        prefs.updated_at = now
        await db.flush()
        reply = LINKED
    else:
        linked = await db.scalar(
            select(UserChannels.user_id).where(UserChannels.telegram_chat_id == chat_id)
        )
        if linked is not None:
            return
        reply = REFUSED
    try:
        await telegram.send(chat_id, reply)
    except TelegramError as error:
        log.warning("telegram reply failed", error=str(error))


async def poll(
    stop: asyncio.Event,
    telegram: TelegramClient,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Long-poll the bot for link messages until stop is set."""
    offset: int | None = None
    while not stop.is_set():
        try:
            updates = await telegram.updates(offset)
        except TelegramError as error:
            log.warning("telegram updates failed", error=str(error))
            try:
                await asyncio.wait_for(stop.wait(), timeout=POLL_RETRY_S)
            except TimeoutError:
                pass
            continue
        for update in updates:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                offset = update_id + 1
            try:
                async with sessionmaker() as db, db.begin():
                    await handle_update(db, update, telegram, datetime.now(UTC))
            except Exception:
                log.exception("telegram update failed")
