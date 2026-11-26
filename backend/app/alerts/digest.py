"""The daily summary: yesterday's numbers for each user, once, at the hour they chose.

Days and hours are the bot host's (`HOST_TZ`), as quiet hours are; a day is midnight to
midnight there, so it is 23 or 25 hours long when the clocks change. Bet totals come from the
hourly rollup, sessions from the history copy. A day with no bets and no sessions sends
nothing. Each user and day is recorded before anything is queued, in the same transaction, so
a restart neither repeats a summary nor loses one; the api that was down at the chosen hour
still sends that day's summary when it comes back, later the same day.

Email goes into the mail outbox. Telegram, for users who linked it and keep it on, goes
through the alert outbox on one shared `digest` event per service and day, so it gets the
same retries and quiet hours as alerts; the alert history leaves those events out.
"""

import asyncio
import time as clock
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import registry
from app.alerts.channels import _can_view
from app.alerts.models import AlertDelivery, AlertEvent, DigestSend, UserChannels
from app.amounts import amount, signed
from app.auth.models import User
from app.mail import templates
from app.mail.outbox import enqueue

DIGEST_KIND = "digest"
CHECK_EVERY_S = 60.0

log = structlog.get_logger("app.alerts.digest")


@dataclass(frozen=True)
class Session:
    lane: int
    profit: float


@dataclass(frozen=True)
class Summary:
    day: date
    bets: int
    wins: int
    losses: int
    wagered: float
    profit: float
    lanes: int
    sessions: int
    best: Session | None
    worst: Session | None
    running_s: float
    alerts: int

    @property
    def roi(self) -> float:
        return self.profit / self.wagered * 100 if self.wagered > 0 else 0.0


def day_bounds(day: date, zone: ZoneInfo) -> tuple[datetime, datetime]:
    """Local midnight to the next local midnight, as UTC instants."""
    start = datetime.combine(day, time(), zone)
    end = datetime.combine(day + timedelta(days=1), time(), zone)
    return start.astimezone(UTC), end.astimezone(UTC)


def label(service: str) -> str:
    return registry.label(service)


def _duration(seconds: float) -> str:
    left = max(0, int(seconds))
    parts: list[str] = []
    for name, size in (("d", 86400), ("h", 3600), ("m", 60)):
        count, left = divmod(left, size)
        if count or parts:
            parts.append(f"{count}{name}")
    parts.append(f"{left}s")
    return " ".join(parts)


async def summarise(
    db: AsyncSession, service: str, day: date, zone: ZoneInfo, now: datetime
) -> Summary | None:
    """The day's numbers for `service`, or None when nothing ran."""
    start, end = day_bounds(day, zone)
    window = {"service": service, "start": start, "end": end}
    bets = (
        await db.execute(
            text(
                "SELECT coalesce(sum(bets), 0)::bigint AS bets,"
                " coalesce(sum(wins), 0)::bigint AS wins,"
                " coalesce(sum(wagered), 0) AS wagered, coalesce(sum(profit), 0) AS profit,"
                " count(DISTINCT lane) AS lanes FROM bets_1h"
                " WHERE service = :service AND bucket >= :start AND bucket < :end"
            ),
            window,
        )
    ).one()
    rows = (
        await db.execute(
            text(
                "SELECT lane, coalesce(profit, 0) AS profit, started_at, ended_at FROM sessions"
                " WHERE service = :service AND started_at < :end"
                " AND (ended_at IS NULL OR ended_at > :start)"
            ),
            window,
        )
    ).all()
    started = [r for r in rows if r.started_at >= start]
    if not bets.bets and not rows:
        return None
    running = 0.0
    for r in rows:
        until = min(r.ended_at or min(now, end), end)
        running += max(0.0, (until - max(r.started_at, start)).total_seconds())
    ranked = sorted(
        (Session(int(r.lane), float(r.profit)) for r in started), key=lambda s: s.profit
    )
    alerts = await db.scalar(
        text(
            "SELECT count(*) FROM alert_events WHERE service = :service"
            " AND fired_at >= :start AND fired_at < :end AND kind NOT IN ('test', 'digest')"
        ),
        window,
    )
    return Summary(
        day=day,
        bets=int(bets.bets),
        wins=int(bets.wins),
        losses=int(bets.bets) - int(bets.wins),
        wagered=float(bets.wagered),
        profit=float(bets.profit),
        lanes=int(bets.lanes),
        sessions=len(started),
        best=ranked[-1] if ranked else None,
        worst=ranked[0] if ranked else None,
        running_s=running,
        alerts=int(alerts or 0),
    )


def lines(summary: Summary) -> list[str]:
    """The summary as plain lines, the same for email and Telegram."""
    out = [
        f"Bets: {summary.bets:,} (wins {summary.wins:,}, losses {summary.losses:,})",
        f"Profit: {signed(summary.profit)}",
        f"ROI: {summary.roi:+.2f}%",
        f"Wagered: {amount(summary.wagered)}",
        f"Sessions started: {summary.sessions:,} on {summary.lanes} lane(s)",
    ]
    if summary.best is not None and summary.worst is not None:
        out.append(f"Best session: {signed(summary.best.profit)} (lane {summary.best.lane})")
        out.append(f"Worst session: {signed(summary.worst.profit)} (lane {summary.worst.lane})")
    out.append(f"Bot running time: {_duration(summary.running_s)}")
    out.append(f"Alerts fired: {summary.alerts:,}")
    return out


async def _shared_event(db: AsyncSession, service: str, day: date, message: str) -> int:
    """The one `digest` event for this service and day, created the first time it is needed."""
    found = await db.scalar(
        select(AlertEvent.id).where(
            AlertEvent.service == service,
            AlertEvent.kind == DIGEST_KIND,
            AlertEvent.message == message,
        )
    )
    if found is not None:
        return int(found)
    event = AlertEvent(service=service, kind=DIGEST_KIND, message=message)
    db.add(event)
    await db.flush()
    return event.id


async def run_once(
    sessionmaker: async_sessionmaker[AsyncSession],
    service: str,
    zone: ZoneInfo,
    now: datetime,
) -> int:
    """Send every summary that is due; returns how many were sent."""
    local = now.astimezone(zone)
    day = local.date() - timedelta(days=1)
    async with sessionmaker() as db:
        due = list(
            await db.execute(
                select(User.id, User.email, UserChannels)
                .join(UserChannels, UserChannels.user_id == User.id)
                .outerjoin(
                    DigestSend,
                    (DigestSend.user_id == User.id)
                    & (DigestSend.service == service)
                    & (DigestSend.day == day),
                )
                .where(
                    User.disabled.is_(False),
                    UserChannels.digest_hour.is_not(None),
                    UserChannels.digest_hour <= local.hour,
                    DigestSend.user_id.is_(None),
                )
            )
        )
        if not due:
            return 0
        summary = await summarise(db, service, day, zone, now)
    sent = 0
    heading = f"{label(service)} daily summary for {day.isoformat()}"
    body = lines(summary) if summary is not None else []
    message = "\n".join([heading, *body])
    for user_id, email, prefs in due:
        async with sessionmaker() as db, db.begin():
            if not await _can_view(db, user_id, service):
                continue
            claimed = await db.execute(
                insert(DigestSend)
                .values(
                    user_id=user_id,
                    service=service,
                    day=day,
                    outcome="sent" if summary is not None else "skipped",
                )
                .on_conflict_do_nothing()
                .returning(DigestSend.user_id)
            )
            if claimed.first() is None or summary is None:
                continue
            enqueue(db, email, templates.digest(heading, body))
            if prefs.telegram_enabled and prefs.telegram_chat_id is not None:
                event_id = await _shared_event(db, service, day, message)
                db.add(
                    AlertDelivery(
                        event_id=event_id, user_id=user_id, channel="telegram", next_attempt_at=now
                    )
                )
            sent += 1
    return sent


async def run(
    stop: asyncio.Event,
    sessionmaker: async_sessionmaker[AsyncSession],
    service: str,
    host_tz: str,
    now: Callable[[], datetime] = lambda: datetime.fromtimestamp(clock.time(), UTC),
) -> None:
    """Check for due summaries once a minute until stop is set."""
    zone = ZoneInfo(host_tz)
    while not stop.is_set():
        try:
            count = await run_once(sessionmaker, service, zone, now())
            if count:
                log.info("daily summaries sent", svc=service, count=count)
        except Exception:
            log.exception("daily summary round failed", svc=service)
        try:
            await asyncio.wait_for(stop.wait(), timeout=CHECK_EVERY_S)
        except TimeoutError:
            pass
