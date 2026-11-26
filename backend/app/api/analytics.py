"""Analytics for a date range and lane: the bot's own report plus breakdown charts.

The report is the bot's `--analytics --json`, read through the host agent and kept 60 s,
so every screen shows the numbers `wolfctl analytics` prints. When the agent is down,
slow or answers with something that is not a valid report, the same report is rebuilt
from the history copy and marked `source: "history"`; it is kept only a second so the
bot's numbers come back as soon as the agent does. Charts always come from history: hour
of day and daily ups and downs from the hourly rollup over every epoch, profit and length
distributions from the sessions the report counts.

The export is the sessions of that window from the history copy, as CSV or JSON, for
operators: the bot's own export can't filter by date or lane, so this one has the
dashboard's columns, not the bot's file format.
"""

import csv
import io
import json
import re
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import registry
from app.adapters.wolfbet.analytics import (
    AnalyticsReport,
    SessionStats,
    SessionWindow,
    _rows,
    history_report,
    session_stats,
)
from app.adapters.wolfbet.control import LANE_ID_MAX
from app.agent_client import AgentError
from app.api.control import NOT_CONTROLLABLE, ServiceName, agent_or_error
from app.api.history import KEY_ID, SessionDetail, _cache, _session_out
from app.auth.permissions import LaneScope, Perm, scoped
from app.db.engine import get_session
from app.history.epochs import EpochView, epoch_view
from app.history.models import HistorySession

BOT_SECONDS = 60.0
HISTORY_SECONDS = 1.0
BINS = 20
INVALID_WINDOW = "That date range is not valid."
_FORMULA = ("=", "+", "-", "@", "\t", "\r")
_BOUND = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}([ T][0-9]{2}:[0-9]{2}(:[0-9]{2})?)?")

log = structlog.get_logger("app.analytics")
router = APIRouter(tags=["analytics"])
Viewer = Annotated[LaneScope, Depends(scoped(Perm.VIEW))]
Exporter = Annotated[LaneScope, Depends(scoped(Perm.EXPORT))]
Db = Annotated[AsyncSession, Depends(get_session)]
Bound = Annotated[str | None, Query(max_length=19)]


class HourRow(BaseModel):
    hour: int
    bets: int
    wins: int
    wagered: float
    profit: float


class DayRow(BaseModel):
    day: str
    profit: float


class ProfitBin(BaseModel):
    lo: float
    hi: float
    sessions: int


class DurationBin(BaseModel):
    lo_sec: float
    hi_sec: float
    sessions: int


class Charts(BaseModel):
    by_hour: list[HourRow]
    by_day: list[DayRow]
    profit_bins: list[ProfitBin]
    duration_bins: list[DurationBin]


class AnalyticsOut(BaseModel):
    source: Literal["bot", "history"]
    report: AnalyticsReport
    charts: Charts
    sessions: SessionStats
    # The bot's clock, which `since` and `until` are read in.
    zone: str


def _bound(value: str | None) -> str | None:
    """A date or local time as the bot stores it, or a 422 with no detail of why."""
    if value is None:
        return None
    try:
        if not _BOUND.fullmatch(value):
            raise ValueError
        parsed = datetime.fromisoformat(value.replace(" ", "T"))
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, INVALID_WINDOW) from None
    return parsed.date().isoformat() if len(value) == 10 else parsed.isoformat()


async def _bot_report(
    request: Request, service: str, args: dict[str, Any]
) -> AnalyticsReport | None:
    """The bot's report, None when it can't be had; a window the bot refuses is a 422."""
    try:
        data = await agent_or_error(request).call(f"{service}.analytics", args)
    except AgentError as error:
        log.warning("analytics call failed at the agent", code=error.code)
        return None
    doc = data.get("json")
    if not isinstance(doc, dict):
        log.warning("analytics reply had no report", exit_code=data.get("exit_code"))
        return None
    refused = doc.get("error")
    if isinstance(refused, dict):
        if refused.get("code") == "invalid_window":
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, INVALID_WINDOW)
        log.warning("analytics refused by the bot", code=str(refused.get("code"))[:40])
        return None
    if data.get("exit_code") != 0:
        log.warning("analytics exited non-zero", exit_code=data.get("exit_code"))
        return None
    try:
        return AnalyticsReport.model_validate(doc)
    except ValidationError:
        log.warning("analytics reply did not match the schema")
        return None


def _rollup_filter(window: SessionWindow, view: EpochView) -> tuple[str, dict[str, Any]]:
    if not view.ids:
        return "false", {"tz": window.zone.key}
    conds = ["service = :service"]
    params: dict[str, Any] = {"service": window.params["service"], "tz": window.zone.key}
    first, last = view.bounds(timedelta(hours=1))
    if first is not None:
        conds.append("bucket >= :epoch_start")
        params["epoch_start"] = first
    if last is not None:
        conds.append("bucket < :epoch_end")
        params["epoch_end"] = last
    if window.start is not None:
        conds.append("bucket >= :start")
        params["start"] = window.start
    if window.end is not None:
        conds.append("bucket <= :end")
        params["end"] = window.end
    if window.lane is not None:
        conds.append("lane = :lane")
        params["lane"] = window.lane
    return " AND ".join(conds), params


async def _bins(
    db: AsyncSession, value_sql: str, window: SessionWindow
) -> list[tuple[float, float, int]]:
    """`BINS` equal-width bins from the lowest to the highest value; one bin when all match."""
    rows = await _rows(
        db,
        (
            f"WITH s AS (SELECT {value_sql} AS v FROM sessions"  # noqa: S608  # nosec B608: fixed SQL, values are bound
            f" WHERE {window.in_epochs}), r AS (SELECT min(v) AS lo, max(v) AS hi FROM s)"
            f" SELECT least(width_bucket(s.v, r.lo, r.hi, {BINS}), {BINS}) AS bin,"
            " count(*) AS n, r.lo, r.hi FROM s, r WHERE s.v IS NOT NULL AND r.hi > r.lo"
            " GROUP BY bin, r.lo, r.hi"
            " UNION ALL SELECT 1, count(*), r.lo, r.hi FROM s, r"
            " WHERE s.v IS NOT NULL AND r.hi = r.lo GROUP BY r.lo, r.hi"
        ),
        window.params,
    )
    if not rows:
        return []
    lo, hi = float(rows[0]["lo"]), float(rows[0]["hi"])
    if hi == lo:
        return [(lo, hi, int(rows[0]["n"]))]
    width = (hi - lo) / BINS
    counts = {int(row["bin"]): int(row["n"]) for row in rows}
    return [
        (lo + i * width, hi if i == BINS - 1 else lo + (i + 1) * width, counts.get(i + 1, 0))
        for i in range(BINS)
    ]


async def _key_hours_and_days(
    db: AsyncSession, window: SessionWindow
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The hourly and daily figures for one key, from the bets of that key's sessions: the
    rollups are kept per lane, and a lane may have bet with another key before."""
    join = (
        "FROM bets b JOIN sessions s ON s.service = b.service AND s.epoch_id = b.epoch_id"
        f" AND s.src_id = b.session_src_id WHERE {window.on('s.')} AND s.epoch_id = ANY(:epochs)"
    )
    params = {**window.params, "tz": window.zone.key}
    hours = await _rows(
        db,
        (
            "SELECT extract(hour FROM b.ts AT TIME ZONE :tz)::int AS hour,"  # noqa: S608  # nosec B608: fixed SQL, values are bound
            " count(*)::bigint AS bets, count(*) FILTER (WHERE b.state = 'win')::bigint AS wins,"
            " coalesce(sum(b.amount), 0) AS wagered, coalesce(sum(b.profit), 0) AS profit"
            f" {join} GROUP BY 1 ORDER BY 1"
        ),
        params,
    )
    days = await _rows(
        db,
        (
            "SELECT (b.ts AT TIME ZONE :tz)::date AS day,"  # nosec B608: fixed SQL, values are bound
            f" coalesce(sum(b.profit), 0) AS profit {join} GROUP BY 1 ORDER BY 1"
        ),
        params,
    )
    return hours, days


async def chart_data(db: AsyncSession, window: SessionWindow, view: EpochView) -> Charts:
    if window.keys is not None:
        hours, days = await _key_hours_and_days(db, window)
        return await _charts(db, window, hours, days)
    where, params = _rollup_filter(window, view)
    hours = await _rows(
        db,
        (
            "SELECT extract(hour FROM bucket AT TIME ZONE :tz)::int AS hour,"  # noqa: S608  # nosec B608: fixed SQL, values are bound
            " sum(bets)::bigint AS bets, sum(wins)::bigint AS wins,"
            " coalesce(sum(wagered), 0) AS wagered, coalesce(sum(profit), 0) AS profit"
            f" FROM bets_1h WHERE {where} GROUP BY 1 ORDER BY 1"
        ),
        params,
    )
    days = await _rows(
        db,
        (
            "SELECT (bucket AT TIME ZONE :tz)::date AS day,"  # noqa: S608  # nosec B608: fixed SQL, values are bound
            f" coalesce(sum(profit), 0) AS profit FROM bets_1h WHERE {where}"
            " GROUP BY 1 ORDER BY 1"
        ),
        params,
    )
    return await _charts(db, window, hours, days)


async def _charts(
    db: AsyncSession,
    window: SessionWindow,
    hours: Sequence[dict[str, Any]],
    days: Sequence[dict[str, Any]],
) -> Charts:
    profit = await _bins(db, "coalesce(profit, 0)", window)
    length = await _bins(
        db, "extract(epoch FROM coalesce(ended_at, now()) - started_at)::float8", window
    )
    return Charts(
        by_hour=[HourRow.model_validate(row) for row in hours],
        by_day=[DayRow(day=row["day"].isoformat(), profit=row["profit"]) for row in days],
        profit_bins=[ProfitBin(lo=lo, hi=hi, sessions=n) for lo, hi, n in profit],
        duration_bins=[DurationBin(lo_sec=lo, hi_sec=hi, sessions=n) for lo, hi, n in length],
    )


@router.get("/{service}/analytics", response_model=AnalyticsOut)
async def get_analytics(
    service: ServiceName,
    request: Request,
    scope: Viewer,
    db: Db,
    since: Bound = None,
    until: Bound = None,
    lane: Annotated[int | None, Query(ge=1, le=LANE_ID_MAX)] = None,
    epoch: Annotated[int | None, Query(ge=1)] = None,
    key: Annotated[str | None, Query(pattern=KEY_ID)] = None,
) -> BaseModel:
    """The report for sessions started in the window (a bare `until` date counts whole), from
    the bot for the current epoch, or from history for an archived `epoch` or one `key` (the
    bot cannot split by key). A lane-only user gets one of their lanes, never a report over
    others; a key-only user gets their keys' sessions, from history."""
    if not registry.is_enabled(service):
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_CONTROLLABLE)
    lane = scope.one(lane)
    since, until = _bound(since), _bound(until)
    cache, cache_key = _cache(request, scope, "analytics_cache", BOT_SECONDS)
    if (hit := cache.get(cache_key)) is not None:
        return hit
    args = {
        k: v for k, v in {"since": since, "until": until, "lane": lane}.items() if v is not None
    }
    view = await epoch_view(db, service, epoch)
    zone = ZoneInfo(request.app.state.host_tz)
    keys = scope.report_keys(key)
    window = SessionWindow(service, since, until, lane, zone, view.ids, keys)
    report = await _bot_report(request, service, args) if epoch is None and keys is None else None
    source: Literal["bot", "history"] = "bot" if report is not None else "history"
    if report is None:
        report = await history_report(db, window)
    out = AnalyticsOut(
        source=source,
        report=report,
        charts=await chart_data(db, window, view),
        sessions=await session_stats(db, window),
        zone=zone.key,
    )
    cache.put(cache_key, out, None if source == "bot" else HISTORY_SECONDS)
    return out


def _cell(value: Any) -> Any:
    """A CSV cell a spreadsheet will not run: text that starts like a formula gets a `'`."""
    return f"'{value}" if isinstance(value, str) and value.startswith(_FORMULA) else value


def csv_text(model: type[BaseModel], rows: Sequence[BaseModel]) -> str:
    """`rows` as CSV with `model`'s fields as columns, formula-safe."""
    out = io.StringIO()
    writer = csv.writer(out)
    columns = list(model.model_fields)
    writer.writerow(columns)
    for row in rows:
        values = row.model_dump(mode="json")
        writer.writerow([_cell(values[c]) for c in columns])
    return out.getvalue()


@router.get("/{service}/analytics/export", response_class=Response)
async def export_analytics(
    service: ServiceName,
    request: Request,
    scope: Exporter,
    db: Db,
    since: Bound = None,
    until: Bound = None,
    lane: Annotated[int | None, Query(ge=1, le=LANE_ID_MAX)] = None,
    file_format: Annotated[Literal["csv", "json"], Query(alias="format")] = "csv",
    epoch: Annotated[int | None, Query(ge=1)] = None,
    key: Annotated[str | None, Query(pattern=KEY_ID)] = None,
) -> Response:
    """The window's sessions, the ones the report counts, oldest first, as a download."""
    if not registry.is_enabled(service):
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_CONTROLLABLE)
    lane = scope.one(lane)
    since, until = _bound(since), _bound(until)
    view = await epoch_view(db, service, epoch)
    window = SessionWindow(
        service,
        since,
        until,
        lane,
        ZoneInfo(request.app.state.host_tz),
        view.ids,
        scope.report_keys(key),
    )
    query = (
        select(HistorySession)
        .where(text(window.in_epochs).bindparams(**window.params))
        .order_by(HistorySession.started_at, HistorySession.src_id)
    )
    rows = [_session_out(SessionDetail, row) for row in await db.scalars(query)]
    if file_format == "csv":
        body, media = csv_text(SessionDetail, rows), "text/csv; charset=utf-8"
    else:
        body, media = json.dumps([r.model_dump(mode="json") for r in rows]), "application/json"
    name = f"{service}-sessions-{since or 'all'}-{until or 'all'}.{file_format}".replace(":", "")
    return Response(
        body,
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "no-store",
        },
    )
