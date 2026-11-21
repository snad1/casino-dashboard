"""The `wolfbet.py --analytics --json` report, and the same report rebuilt from history.

The models mirror `contracts/schemas/wolfbet-cli-analytics-v1.json` field for field and
refuse anything else, so a drifted or tampered reply never reaches the browser.

`history_report` answers from the dashboard's copy of the sessions with the bot's own SQL
rules (`wolfbet.py` `_analytics_data`): one epoch only, as the bot reads only its live
database (the current epoch unless an archived one is asked for); `started_at` compared
in the host's local time, a bare `until` date counting the whole day; missing values
counted as 0 and a missing lane as lane 1. Times come back as the naive local ISO strings
the bot prints.
"""

from datetime import UTC, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingest.tailer import aware

TOP_SESSIONS = 5


class _Doc(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Window(_Doc):
    since: str | None
    until: str | None
    lane: int | None


class Lifetime(_Doc):
    sessions_total: float | None
    bets_total: float | None
    wins_total: float | None
    losses_total: float | None
    wagered_total: float | None
    profit_total: float | None
    best_profit: float | None
    worst_profit: float | None
    sessions_in_profit: float | None
    sessions_in_loss: float | None
    win_rate_pct: float | None


class Span(_Doc):
    first: str | None
    last: str | None
    span_sec: float
    bet_sec: float


class _Group(_Doc):
    sessions: int
    bets: float
    wins: float
    losses: float
    profit: float
    wagered: float


class GameRow(_Group):
    game: str


class StrategyRow(_Group):
    strategy: str


class LaneRow(_Group):
    lane: str


class TopSession(_Doc):
    id: int
    game: str
    strategy: str
    bets: float | None
    wins: float | None
    losses: float | None
    profit: float | None
    wagered: float | None
    started_at: str | None


class AnalyticsReport(_Doc):
    v: Literal[1]
    window: Window
    lifetime: Lifetime
    span: Span
    by_game: list[GameRow]
    by_strategy: list[StrategyRow]
    by_lane: list[LaneRow]
    top_best: list[TopSession]
    top_worst: list[TopSession]


class SessionWindow:
    """The bot's `_stats_date_window` as SQL over the history `sessions` table."""

    def __init__(
        self,
        service: str,
        since: str | None,
        until: str | None,
        lane: int | None,
        zone: ZoneInfo,
        epochs: list[int],
    ) -> None:
        self.since, self.until, self.lane = since, until, lane
        self.zone = zone
        self.epochs = epochs
        self.start = aware(since, zone) if since else None
        self.end = (
            aware(until if len(until) > 10 else until + "T23:59:59.999999", zone) if until else None
        )
        conds = ["service = :service"]
        self.params: dict[str, Any] = {"service": service, "epochs": epochs}
        if self.start is not None:
            conds.append("started_at >= :start")
            self.params["start"] = self.start
        if self.end is not None:
            conds.append("started_at <= :end")
            self.params["end"] = self.end
        if lane is not None:
            conds.append("lane = :lane")
            self.params["lane"] = lane
        self.conditions = " AND ".join(conds)

    @property
    def in_epochs(self) -> str:
        """The filter limited to the window's epochs: what that bot database holds."""
        return f"{self.conditions} AND epoch_id = ANY(:epochs)"

    def local(self, value: datetime | None) -> str | None:
        return value.astimezone(self.zone).replace(tzinfo=None).isoformat() if value else None


_GROUP_COLUMNS = (
    "count(*) AS sessions, coalesce(sum(total_bets), 0)::bigint AS bets,"
    " coalesce(sum(wins), 0)::bigint AS wins, coalesce(sum(losses), 0)::bigint AS losses,"
    " coalesce(sum(profit), 0) AS profit,"
    " coalesce(sum(wagered), 0) AS wagered"
)


async def _rows(db: AsyncSession, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(row._mapping) for row in (await db.execute(text(sql), params)).all()]


async def history_report(db: AsyncSession, window: SessionWindow) -> AnalyticsReport:
    """The bot's analytics report computed from the history copy of its sessions."""
    where, params = window.in_epochs, window.params
    [lifetime] = await _rows(
        db,
        "SELECT count(*) AS sessions_total, coalesce(sum(total_bets), 0)::bigint AS bets_total,"  # noqa: S608  # nosec B608: fixed SQL, values are bound
        " coalesce(sum(wins), 0)::bigint AS wins_total,"
        " coalesce(sum(losses), 0)::bigint AS losses_total,"
        " coalesce(sum(wagered), 0) AS wagered_total, coalesce(sum(profit), 0) AS profit_total,"
        " coalesce(max(profit), 0) AS best_profit, coalesce(min(profit), 0) AS worst_profit,"
        " count(*) FILTER (WHERE profit >= 0) AS sessions_in_profit,"
        " count(*) FILTER (WHERE profit < 0) AS sessions_in_loss"
        f" FROM sessions WHERE {where}",  # nosec B608: fixed SQL, values are bound
        params,
    )
    bets = lifetime["bets_total"]
    lifetime["win_rate_pct"] = lifetime["wins_total"] / bets * 100 if bets > 0 else 0.0

    by_game = await _rows(
        db,
        f"SELECT coalesce(game, '?') AS game, {_GROUP_COLUMNS} FROM sessions"  # noqa: S608  # nosec B608: fixed SQL, values are bound
        f" WHERE {where} GROUP BY game ORDER BY profit DESC",
        params,
    )
    by_strategy = await _rows(
        db,
        f"SELECT coalesce(strategy, '?') AS strategy, {_GROUP_COLUMNS} FROM sessions"  # noqa: S608  # nosec B608: fixed SQL, values are bound
        f" WHERE {where} GROUP BY strategy ORDER BY profit DESC",
        params,
    )
    by_lane = await _rows(
        db,
        f"SELECT 'Lane ' || lane AS lane, {_GROUP_COLUMNS} FROM sessions"  # noqa: S608  # nosec B608: fixed SQL, values are bound
        f" WHERE {where} GROUP BY sessions.lane ORDER BY sessions.lane",
        params,
    )
    top = (
        "SELECT src_id AS id, coalesce(game, '?') AS game, coalesce(strategy, '?') AS strategy,"  # noqa: S608  # nosec B608: fixed SQL, values are bound
        " coalesce(total_bets, 0) AS bets, coalesce(wins, 0) AS wins,"
        " coalesce(losses, 0) AS losses, coalesce(profit, 0) AS profit,"
        " coalesce(wagered, 0) AS wagered, started_at"
        f" FROM sessions WHERE {where} ORDER BY profit {{order}} LIMIT {TOP_SESSIONS}"
    )
    top_best = await _rows(db, top.format(order="DESC NULLS LAST"), params)
    top_worst = await _rows(db, top.format(order="ASC NULLS FIRST"), params)
    for row in (*top_best, *top_worst):
        row["started_at"] = window.local(row["started_at"])

    times = await _rows(db, f"SELECT started_at, ended_at FROM sessions WHERE {where}", params)  # noqa: S608  # nosec B608: fixed SQL, values are bound
    span = _span(times, window)

    return AnalyticsReport(
        v=1,
        window=Window(since=window.since, until=window.until, lane=window.lane),
        lifetime=Lifetime.model_validate(lifetime),
        span=span,
        by_game=[GameRow.model_validate(r) for r in by_game],
        by_strategy=[StrategyRow.model_validate(r) for r in by_strategy],
        by_lane=[LaneRow.model_validate(r) for r in by_lane],
        top_best=[TopSession.model_validate(r) for r in top_best],
        top_worst=[TopSession.model_validate(r) for r in top_worst],
    )


def _span(rows: list[dict[str, Any]], window: SessionWindow) -> Span:
    """First start to last end, and the summed session lengths; a running session ends now."""
    now = datetime.now(UTC)
    first = last = None
    bet = 0.0
    for row in rows:
        started, ended = row["started_at"], row["ended_at"] or now
        if started is None:
            continue
        bet += max(0.0, (ended - started).total_seconds())
        first = started if first is None or started < first else first
        last = ended if last is None or ended > last else last
    if first is None or last is None:
        return Span(first=None, last=None, span_sec=0, bet_sec=0)
    return Span(
        first=window.local(first),
        last=window.local(last),
        span_sec=max(0.0, (last - first).total_seconds()),
        bet_sec=bet,
    )
