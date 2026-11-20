"""The daily summary: the day's numbers, DST-length days, and one send per user and day."""

from collections.abc import AsyncIterator, Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg  # type: ignore[import-untyped]  # ships no type hints
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.digest import day_bounds, lines, run_once, summarise
from app.alerts.models import AlertDelivery, AlertEvent, DigestSend, UserChannels
from app.auth.models import Role, User, UserRole
from app.mail.models import OutboxMessage
from tests.conftest import NO_LOGIN, new_email

UTC_ZONE = ZoneInfo("UTC")
PARIS = ZoneInfo("Europe/Paris")
DAY = date(2026, 11, 19)
Sessions = async_sessionmaker[AsyncSession]


def at(hour: float, day: date = DAY) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(hours=hour)


class History:
    def __init__(self, pg: asyncpg.Connection, service: str, epoch: int) -> None:
        self.pg, self.service, self.epoch = pg, service, epoch
        self.next_bet = 1
        self.next_session = 1

    async def bet(self, when: datetime, profit: float, win: bool, lane: int = 1) -> None:
        await self.pg.execute(
            "INSERT INTO bets (service, epoch_id, src_id, ts, lane, amount, state, profit)"
            " VALUES ($1, $2, $3, $4, $5, 1.0, $6, $7)",
            self.service,
            self.epoch,
            self.next_bet,
            when,
            lane,
            "win" if win else "loss",
            profit,
        )
        self.next_bet += 1

    async def session(
        self, lane: int, start: datetime, end: datetime | None, profit: float
    ) -> None:
        await self.pg.execute(
            "INSERT INTO sessions (service, epoch_id, src_id, lane, started_at, ended_at, profit)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7)",
            self.service,
            self.epoch,
            self.next_session,
            lane,
            start,
            end,
            profit,
        )
        self.next_session += 1

    async def alert(self, when: datetime, kind: str = "down") -> None:
        await self.pg.execute(
            "INSERT INTO alert_events (service, kind, message, fired_at) VALUES ($1, $2, 'x', $3)",
            self.service,
            kind,
            when,
        )


@pytest.fixture
async def history(pg: asyncpg.Connection, service: str) -> AsyncIterator[History]:
    epoch = await pg.fetchval(
        "INSERT INTO db_epochs (service, source_path, inode, first_started_at)"
        " VALUES ($1, '/botdata/x.db', 7, now()) RETURNING epoch_id",
        service,
    )
    yield History(pg, service, epoch)
    await pg.execute("DELETE FROM alert_events WHERE service = $1", service)


async def test_the_days_totals_come_from_the_rollup_and_sessions(
    sessions: Sessions, history: History, service: str
) -> None:
    for hour, profit, win in ((8.5, 0.5, True), (9.5, -0.25, False), (12.1, 0.1, True)):
        await history.bet(at(hour), profit, win)
    await history.bet(at(12.2), -0.05, False, lane=2)
    await history.bet(at(-1), 9.0, True)
    await history.bet(at(24.5), 9.0, True)
    await history.session(1, at(8), at(10), 0.25)
    await history.session(2, at(12), at(12.5), -0.05)
    await history.session(1, at(-1), at(1), 3.0)
    await history.session(3, at(23), None, 0.0)
    await history.alert(at(9))
    await history.alert(at(13), "profit")
    await history.alert(at(14), "test")
    await history.alert(at(25))

    async with sessions() as db:
        summary = await summarise(db, service, DAY, UTC_ZONE, at(30))
    assert summary is not None
    assert (summary.bets, summary.wins, summary.losses, summary.lanes) == (4, 2, 2, 2)
    assert summary.wagered == pytest.approx(4.0)
    assert summary.profit == pytest.approx(0.3)
    assert summary.roi == pytest.approx(7.5)
    assert summary.sessions == 3, "only sessions started that day"
    assert (summary.best and summary.best.profit, summary.worst and summary.worst.lane) == (0.25, 2)
    assert summary.running_s == pytest.approx((2 + 0.5 + 1 + 1) * 3600)
    assert summary.alerts == 2
    assert lines(summary)[:2] == ["Bets: 4 (wins 2, losses 2)", "Profit: +0.30000000"]
    assert "Bot running time: 4h 30m 0s" in lines(summary)


async def test_a_quiet_day_has_no_summary(
    sessions: Sessions, history: History, service: str
) -> None:
    await history.bet(at(-2), 1.0, True)
    async with sessions() as db:
        assert await summarise(db, service, DAY, UTC_ZONE, at(30)) is None


@pytest.mark.parametrize(("day", "hours"), [(date(2026, 10, 25), 25), (date(2026, 3, 29), 23)])
def test_days_where_the_clocks_change_are_longer_or_shorter(day: date, hours: int) -> None:
    start, end = day_bounds(day, PARIS)
    assert end - start == timedelta(hours=hours)


async def test_the_long_autumn_day_counts_its_extra_hour(
    sessions: Sessions, history: History, service: str
) -> None:
    day = date(2026, 10, 25)
    start, end = day_bounds(day, PARIS)
    await history.bet(start + timedelta(minutes=30), 0.1, True)
    await history.bet(end - timedelta(minutes=30), 0.2, True)
    await history.bet(end + timedelta(minutes=30), 5.0, True)
    await history.bet(start - timedelta(minutes=30), 5.0, True)
    async with sessions() as db:
        summary = await summarise(db, service, day, PARIS, end + timedelta(hours=2))
    assert summary is not None and summary.bets == 2
    assert summary.profit == pytest.approx(0.3)


@pytest.fixture
async def people(sessions: Sessions) -> AsyncIterator[Callable[..., Any]]:
    created: list[int] = []

    async def make(
        hour: int | None, *, role: bool = True, disabled: bool = False, telegram: bool = False
    ) -> int:
        async with sessions() as db:
            user = User(email=new_email(), password_hash=NO_LOGIN, disabled=disabled)
            db.add(user)
            await db.flush()
            if role:
                db.add(UserRole(user_id=user.id, service_id=None, role=Role.VIEWER))
            db.add(
                UserChannels(
                    user_id=user.id,
                    digest_hour=hour,
                    telegram_enabled=telegram,
                    telegram_chat_id=880000 + user.id if telegram else None,
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


async def mail_for(sessions: Sessions, user_id: int) -> list[OutboxMessage]:
    async with sessions() as db:
        email = await db.scalar(select(User.email).where(User.id == user_id))
        return list(await db.scalars(select(OutboxMessage).where(OutboxMessage.to_email == email)))


async def test_each_user_gets_one_summary_at_their_hour(
    sessions: Sessions, history: History, service: str, people: Any
) -> None:
    await history.bet(at(9), 0.5, True)
    mailed = await people(8)
    chatty = await people(8, telegram=True)
    later = await people(20)
    disabled = await people(8, disabled=True)
    roleless = await people(8, role=False)
    off = await people(None)
    morning = at(32)

    assert await run_once(sessions, service, UTC_ZONE, morning) == 2
    assert await run_once(sessions, service, UTC_ZONE, morning + timedelta(minutes=1)) == 0
    [mail] = await mail_for(sessions, mailed)
    assert mail.subject == f"{service.capitalize()} daily summary for 2026-11-19"
    assert "Bets: 1 (wins 1, losses 0)" in (mail.text_body or "")
    assert len(await mail_for(sessions, chatty)) == 1
    for user_id in (later, disabled, roleless, off):
        assert await mail_for(sessions, user_id) == []

    async with sessions() as db:
        telegram = list(
            await db.execute(
                select(AlertDelivery.user_id, AlertEvent.kind, AlertEvent.message)
                .join(AlertEvent, AlertEvent.id == AlertDelivery.event_id)
                .where(AlertEvent.service == service)
            )
        )
    assert [(u, k) for u, k, _ in telegram] == [(chatty, "digest")]
    assert telegram[0][2].startswith(f"{service.capitalize()} daily summary for 2026-11-19\nBets:")

    assert await run_once(sessions, service, UTC_ZONE, at(44)) == 1, "the later user at 20:00"
    assert await run_once(sessions, service, UTC_ZONE, at(44.5)) == 0


async def test_a_quiet_day_is_marked_and_nothing_is_sent(
    sessions: Sessions, history: History, service: str, people: Any
) -> None:
    user = await people(8)
    assert await run_once(sessions, service, UTC_ZONE, at(32)) == 0
    async with sessions() as db:
        row = await db.get(DigestSend, (user, service, DAY))
    assert row is not None and row.outcome == "skipped"
    assert await mail_for(sessions, user) == []
