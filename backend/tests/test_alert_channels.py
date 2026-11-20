"""Alert delivery by email and Telegram: who gets it, when, retries, and chat linking."""

import json
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx2
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.channels import (
    LINKED,
    REFUSED,
    TELEGRAM_MAX,
    TelegramClient,
    deliver_once,
    fan_out,
    handle_update,
    issue_link_code,
    quiet_until,
)
from app.alerts.models import AlertDelivery, AlertEvent, UserChannels
from app.auth.models import Role, User, UserRole
from app.mail.models import OutboxMessage
from app.mail.outbox import MAX_ATTEMPTS
from tests.conftest import NO_LOGIN, new_email

UTC_ZONE = ZoneInfo("UTC")
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
TOKEN = "123456789:AAH-alert-bot-token-x9Yz"  # noqa: S105  # nosec B105: fake, for the mock Bot API
Sessions = async_sessionmaker[AsyncSession]


class FakeTelegram:
    """Answers the Bot API from a list of replies and records every request."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.replies: list[httpx2.Response] = []

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append((request.url.path, json.loads(request.content)))
        if self.replies:
            return self.replies.pop(0)
        return httpx2.Response(200, json={"ok": True, "result": {}})

    def client(self) -> TelegramClient:
        return TelegramClient(TOKEN, httpx2.MockTransport(self.handler))

    @property
    def sent(self) -> list[dict[str, Any]]:
        return [body for path, body in self.requests if path.endswith("/sendMessage")]


@pytest.fixture
async def people(sessions: Sessions) -> AsyncIterator[Callable[..., Any]]:
    created: list[int] = []

    async def make(
        role: Role | None = Role.VIEWER,
        *,
        email: bool = False,
        chat: int | None = None,
        disabled: bool = False,
        quiet: tuple[time, time] | None = None,
    ) -> int:
        async with sessions() as db:
            user = User(email=new_email(), password_hash=NO_LOGIN, disabled=disabled)
            db.add(user)
            await db.flush()
            if role is not None:
                db.add(UserRole(user_id=user.id, service_id=None, role=role))
            db.add(
                UserChannels(
                    user_id=user.id,
                    email_enabled=email,
                    telegram_enabled=chat is not None,
                    telegram_chat_id=chat,
                    quiet_start=quiet[0] if quiet else None,
                    quiet_end=quiet[1] if quiet else None,
                )
            )
            await db.commit()
            created.append(user.id)
            return user.id

    yield make
    async with sessions() as db:
        for user in await db.scalars(select(User).where(User.id.in_(created))):
            await db.delete(user)
        await db.commit()


async def fire(sessions: Sessions, message: str = "Bot down: bot down for 60 s on lane 1") -> int:
    async with sessions() as db:
        event = AlertEvent(service="wolfbet", lane=1, kind="down", message=message)
        db.add(event)
        await db.flush()
        db.add_all(
            AlertDelivery(event_id=event.id, channel=c) for c in ("email", "telegram", "push")
        )
        await db.commit()
        return event.id


async def deliveries(sessions: Sessions, event_id: int) -> list[AlertDelivery]:
    async with sessions() as db:
        return list(
            await db.scalars(
                select(AlertDelivery)
                .where(AlertDelivery.event_id == event_id)
                .order_by(AlertDelivery.id)
            )
        )


async def run_all(sessions: Sessions, telegram: TelegramClient | None, now: datetime = NOW) -> None:
    async with sessions() as db, db.begin():
        await fan_out(db, now)
    for _ in range(50):
        async with sessions() as db, db.begin():
            if not await deliver_once(db, telegram, UTC_ZONE, now):
                return


async def mail_to(sessions: Sessions, user_id: int) -> list[OutboxMessage]:
    async with sessions() as db:
        email = await db.scalar(select(User.email).where(User.id == user_id))
        return list(await db.scalars(select(OutboxMessage).where(OutboxMessage.to_email == email)))


async def test_each_alert_reaches_only_users_who_can_view_and_opted_in(
    sessions: Sessions,
    people: Any,
) -> None:
    mailed = await people(email=True)
    chatty = await people(chat=777001)
    disabled = await people(email=True, disabled=True)
    roleless = await people(None, email=True)
    silent = await people()
    telegram = FakeTelegram()
    event_id = await fire(sessions)

    await run_all(sessions, telegram.client())

    rows = await deliveries(sessions, event_id)
    per_user = {(r.user_id, r.channel): r.status for r in rows if r.user_id is not None}
    assert per_user == {(mailed, "email"): "sent", (chatty, "telegram"): "sent"}
    channel_rows = {r.channel: r.status for r in rows if r.user_id is None}
    assert channel_rows == {"email": "sent", "telegram": "sent", "push": "sent"}
    [mail] = await mail_to(sessions, mailed)
    assert mail.subject == "Alert: Bot down: bot down for 60 s on lane 1"
    assert "Bot down: bot down for 60 s on lane 1" in (mail.text_body or "")
    for other in (disabled, roleless, silent):
        assert await mail_to(sessions, other) == []
    assert telegram.sent == [
        {
            "chat_id": 777001,
            "text": "Bot down: bot down for 60 s on lane 1",
            "disable_web_page_preview": True,
        }
    ]


async def test_telegram_text_is_plain_and_capped(sessions: Sessions, people: Any) -> None:
    await people(chat=777002)
    telegram = FakeTelegram()
    await fire(sessions, "Rule <b>x</b>\x1b[31m\r\n" + "y" * 5000)
    await run_all(sessions, telegram.client())
    [body] = telegram.sent
    assert "parse_mode" not in body
    assert body["text"].startswith("Rule <b>x</b>[31m\n")
    assert len(body["text"]) == TELEGRAM_MAX
    assert "\x1b" not in body["text"] and "\r" not in body["text"]


async def test_failures_back_off_then_die_and_a_blocked_bot_dies_at_once(
    sessions: Sessions,
    people: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    user = await people(chat=777003)
    telegram = FakeTelegram()
    event_id = await fire(sessions)
    telegram.replies = [httpx2.Response(502, json={"ok": False})]
    await run_all(sessions, telegram.client())
    [row] = [r for r in await deliveries(sessions, event_id) if r.user_id == user]
    assert (row.status, row.attempts, row.next_attempt_at) == (
        "pending",
        1,
        NOW + timedelta(seconds=30),
    )

    telegram.replies = [httpx2.Response(429, json={"ok": False, "parameters": {"retry_after": 7}})]
    later = NOW + timedelta(minutes=1)
    await run_all(sessions, telegram.client(), later)
    [row] = [r for r in await deliveries(sessions, event_id) if r.user_id == user]
    assert (row.attempts, row.next_attempt_at) == (2, later + timedelta(seconds=7))

    async with sessions() as db:
        await db.execute(
            update(AlertDelivery)
            .where(AlertDelivery.id == row.id)
            .values(attempts=MAX_ATTEMPTS - 1)
        )
        await db.commit()
    telegram.replies = [httpx2.Response(500, json={"ok": False})]
    await run_all(sessions, telegram.client(), later + timedelta(minutes=1))
    [row] = [r for r in await deliveries(sessions, event_id) if r.user_id == user]
    assert row.status == "dead"

    second = await fire(sessions)
    telegram.replies = [httpx2.Response(403, json={"ok": False})]
    await run_all(sessions, telegram.client(), later + timedelta(minutes=2))
    [row] = [r for r in await deliveries(sessions, second) if r.user_id == user]
    assert (row.status, row.attempts) == ("dead", 1)
    assert TOKEN not in caplog.text and TOKEN.split(":")[1] not in caplog.text


async def test_the_hourly_limit_holds_the_eleventh_email(sessions: Sessions, people: Any) -> None:
    user = await people(email=True)
    events = [await fire(sessions) for _ in range(11)]
    await run_all(sessions, None)
    rows = [r for e in events for r in await deliveries(sessions, e) if r.user_id == user]
    assert [r.status for r in rows].count("sent") == 10
    [held] = [r for r in rows if r.status == "pending"]
    assert held.next_attempt_at == NOW + timedelta(hours=1)
    assert len(await mail_to(sessions, user)) == 10


@pytest.mark.parametrize(
    ("window", "now", "until"),
    [
        (
            (time(23), time(7)),
            datetime(2026, 11, 20, 2, 0, tzinfo=UTC),
            datetime(2026, 11, 20, 7, 0, tzinfo=UTC),
        ),
        (
            (time(23), time(7)),
            datetime(2026, 11, 20, 23, 30, tzinfo=UTC),
            datetime(2026, 11, 21, 7, 0, tzinfo=UTC),
        ),
        (
            (time(12), time(14)),
            datetime(2026, 11, 20, 13, 0, tzinfo=UTC),
            datetime(2026, 11, 20, 14, 0, tzinfo=UTC),
        ),
        ((time(12), time(14)), datetime(2026, 11, 20, 14, 0, tzinfo=UTC), None),
        ((time(23), time(7)), datetime(2026, 11, 20, 8, 0, tzinfo=UTC), None),
    ],
)
def test_quiet_hours_end_where_expected(
    window: tuple[time, time], now: datetime, until: datetime | None
) -> None:
    prefs = UserChannels(quiet_start=window[0], quiet_end=window[1])
    assert quiet_until(prefs, now, UTC_ZONE) == until


def test_quiet_hours_use_the_host_zone() -> None:
    prefs = UserChannels(quiet_start=time(23), quiet_end=time(7))
    paris = ZoneInfo("Europe/Paris")
    assert quiet_until(prefs, datetime(2026, 11, 20, 5, 30, tzinfo=UTC), paris) == datetime(
        2026, 11, 20, 6, 0, tzinfo=UTC
    )


async def test_an_alert_in_quiet_hours_waits_for_morning(sessions: Sessions, people: Any) -> None:
    user = await people(email=True, quiet=(time(23), time(7)))
    event_id = await fire(sessions)
    night = datetime(2026, 11, 20, 2, 0, tzinfo=UTC)
    await run_all(sessions, None, night)
    [row] = [r for r in await deliveries(sessions, event_id) if r.user_id == user]
    assert (row.status, row.next_attempt_at) == (
        "pending",
        datetime(2026, 11, 20, 7, 0, tzinfo=UTC),
    )
    await run_all(sessions, None, datetime(2026, 11, 20, 7, 0, tzinfo=UTC))
    assert len(await mail_to(sessions, user)) == 1


async def test_turning_a_channel_off_stops_waiting_deliveries(
    sessions: Sessions, people: Any
) -> None:
    user = await people(email=True, quiet=(time(23), time(7)))
    event_id = await fire(sessions)
    await run_all(sessions, None, datetime(2026, 11, 20, 2, 0, tzinfo=UTC))
    async with sessions() as db:
        await db.execute(
            update(UserChannels).where(UserChannels.user_id == user).values(email_enabled=False)
        )
        await db.commit()
    await run_all(sessions, None, datetime(2026, 11, 20, 7, 0, tzinfo=UTC))
    [row] = [r for r in await deliveries(sessions, event_id) if r.user_id == user]
    assert row.status == "dead"
    assert await mail_to(sessions, user) == []


def start(chat_id: int, text: str, kind: str = "private") -> dict[str, Any]:
    return {"update_id": 1, "message": {"chat": {"id": chat_id, "type": kind}, "text": text}}


async def link(sessions: Sessions, user_id: int, now: datetime = NOW) -> str:
    async with sessions() as db, db.begin():
        return await issue_link_code(db, user_id, now)


async def handle(
    sessions: Sessions, telegram: FakeTelegram, update_: dict[str, Any], now: datetime = NOW
) -> None:
    async with sessions() as db, db.begin():
        await handle_update(db, update_, telegram.client(), now)


async def channels_of(sessions: Sessions, user_id: int) -> UserChannels:
    async with sessions() as db:
        row = await db.get(UserChannels, user_id)
        assert row is not None
        return row


async def test_a_start_code_links_the_chat_once(sessions: Sessions, people: Any) -> None:
    user = await people()
    telegram = FakeTelegram()
    code = await link(sessions, user)
    await handle(sessions, telegram, start(555001, f"/start {code}"))
    prefs = await channels_of(sessions, user)
    assert (prefs.telegram_chat_id, prefs.telegram_enabled, prefs.telegram_link_hash) == (
        555001,
        True,
        None,
    )
    assert telegram.sent == [{"chat_id": 555001, "text": LINKED, "disable_web_page_preview": True}]

    await handle(sessions, telegram, start(555001, f"/start {code}"))
    assert len(telegram.sent) == 1, "a re-delivered update for a linked chat gets no reply"
    await handle(sessions, telegram, start(555002, f"/start {code}"))
    assert telegram.sent[-1]["text"] == REFUSED


@pytest.mark.parametrize("case", ["expired", "wrong", "group", "no code", "not a command"])
async def test_bad_start_messages_link_nothing(sessions: Sessions, people: Any, case: str) -> None:
    user = await people()
    telegram = FakeTelegram()
    code = await link(sessions, user)
    text, kind, now = f"/start {code}", "private", NOW
    if case == "expired":
        now = NOW + timedelta(minutes=11)
    elif case == "wrong":
        text = "/start " + "A" * 32
    elif case == "group":
        kind = "group"
    elif case == "no code":
        text = "/start"
    else:
        text = "hello"
    await handle(sessions, telegram, start(555100, text, kind), now)
    assert (await channels_of(sessions, user)).telegram_chat_id is None
    assert telegram.sent == (
        []
        if case == "not a command"
        else [{"chat_id": 555100, "text": REFUSED, "disable_web_page_preview": True}]
    )


async def test_a_chat_moves_to_the_user_who_linked_it_last(sessions: Sessions, people: Any) -> None:
    first = await people(chat=555200)
    second = await people()
    code = await link(sessions, second)
    await handle(sessions, FakeTelegram(), start(555200, f"/start {code}"))
    assert (await channels_of(sessions, first)).telegram_chat_id is None
    assert (await channels_of(sessions, second)).telegram_chat_id == 555200
