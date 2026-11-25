"""Read-only bet history: sessions, a session's bets, chart series and current lane totals.

Every route needs `view` on the `{service}` in its path; a user with `view` on some lanes
only sees those lanes, and service-wide health is refused to them. Sessions are keyed by
`(epoch_id, id)` because the bot's ids restart after a reset. Lists page newest first with a
keyset, so rows arriving between two pages never shift or repeat a page.

Series come from the raw bets at 1 s and from the rollups at 1 m, 1 h and 1 d, never more
than 5 000 points per lane. Identical requests within a second share one result, so a
dashboard open on many screens costs one query.
"""

import math
import time
from collections.abc import Collection, Sequence
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import ColumnElement, Select, func, or_, select, text, tuple_
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.wolfbet.control import LANE_ID_MAX
from app.auth.key_lanes import MAIN_KEY
from app.auth.permissions import FORBIDDEN, LaneScope, Perm, scoped
from app.db.engine import get_session
from app.history.epochs import epoch_view
from app.history.models import Bet, DbEpoch, HistorySession, ReconcileRun

MAX_POINTS = 5000
DEFAULT_RANGE = timedelta(hours=1)
CACHE_SECONDS = 1.0
CACHE_ENTRIES = 256
RESOLUTIONS: dict[str, timedelta] = {
    "1s": timedelta(seconds=1),
    "1m": timedelta(minutes=1),
    "1h": timedelta(hours=1),
    "1d": timedelta(days=1),
}
SESSION_NOT_FOUND = "Session not found."
RANGE_TOO_LONG = "That range has too many points for this resolution; pick a coarser one."
EMPTY_RANGE = "The start of the range must be before its end."

Resolution = Literal["1s", "1m", "1h", "1d"]
CURSOR = r"^\d{1,18}:\d{1,18}$"
KEY_ID = r"^[a-z0-9][a-z0-9_-]{0,31}$"

router = APIRouter(prefix="/{service}", tags=["history"])
Viewer = Annotated[LaneScope, Depends(scoped(Perm.VIEW))]
Db = Annotated[AsyncSession, Depends(get_session)]


class SessionOut(BaseModel):
    epoch_id: int
    id: int
    lane: int
    started_at: datetime | None
    ended_at: datetime | None
    currency: str | None
    game: str | None
    strategy: str | None
    total_bets: int | None
    wins: int | None
    losses: int | None
    profit: float | None
    wagered: float | None
    start_balance: float | None
    end_balance: float | None
    max_win_streak: int | None
    max_loss_streak: int | None


class SessionDetail(SessionOut):
    base_bet: float | None
    multiplier: float | None
    highest_balance: float | None
    lowest_balance: float | None
    highest_win: float | None
    biggest_loss: float | None
    bets_per_minute: float | None
    bets_per_second: float | None
    peak_bps: float | None
    low_bps: float | None
    peak_bpm: float | None
    low_bpm: float | None


class SessionPage(BaseModel):
    items: list[SessionOut]
    next_cursor: str | None
    total: int | None = None


class BetOut(BaseModel):
    id: int
    ts: datetime
    amount: float | None
    multiplier: float | None
    rule: str | None
    bet_value: float | None
    result_value: float | None
    state: str | None
    profit: float | None
    balance_after: float | None


class BetPage(BaseModel):
    items: list[BetOut]
    next_before: int | None


class LaneSeries(BaseModel):
    lane: int
    points: list[tuple[datetime, float | None]]


class SeriesOut(BaseModel):
    res: Resolution
    series: list[LaneSeries]


class ReconcileOut(BaseModel):
    finished_at: datetime
    kind: str
    mismatched: int
    persisting: int
    purged_upstream: int
    lag_rows: int
    lag_seconds: float
    lag_alert: bool


class IngestOut(BaseModel):
    last_run: ReconcileOut | None


class CurrentOut(BaseModel):
    lanes: list[SessionOut]


class EpochOut(BaseModel):
    epoch_id: int
    first_started_at: datetime
    last_started_at: datetime | None
    current: bool


class EpochList(BaseModel):
    items: list[EpochOut]


class _Cache:
    """A tiny per-process cache; the api runs one worker, so this is shared by every client."""

    def __init__(self, ttl: float | None = None) -> None:
        self.ttl = ttl
        self.entries: dict[str, tuple[float, BaseModel]] = {}

    def get(self, key: str) -> BaseModel | None:
        hit = self.entries.get(key)
        if hit is None or hit[0] < time.monotonic():
            return None
        return hit[1]

    def put(self, key: str, value: BaseModel, ttl: float | None = None) -> None:
        now = time.monotonic()
        if len(self.entries) >= CACHE_ENTRIES:
            self.entries = {k: v for k, v in self.entries.items() if v[0] >= now}
            while len(self.entries) >= CACHE_ENTRIES:
                self.entries.pop(next(iter(self.entries)))
        ttl = ttl if ttl is not None else self.ttl if self.ttl is not None else CACHE_SECONDS
        self.entries[key] = (now + ttl, value)


def _cache(
    request: Request, scope: LaneScope, name: str = "history_cache", ttl: float | None = None
) -> tuple[_Cache, str]:
    cache: _Cache | None = getattr(request.app.state, name, None)
    if cache is None:
        cache = _Cache(ttl)
        setattr(request.app.state, name, cache)
    key = (
        request.url.path
        + "?"
        + "&".join(sorted(f"{k}={v}" for k, v in request.query_params.multi_items()))
        + scope.cache_tag()
    )
    return cache, key


def _session_out[T: SessionOut](model: type[T], row: HistorySession) -> T:
    fields = {name: getattr(row, name) for name in model.model_fields if name != "id"}
    return model(id=row.src_id, **fields)


@router.get("/sessions", response_model=SessionPage)
async def list_sessions(
    service: str,
    request: Request,
    scope: Viewer,
    db: Db,
    lane: Annotated[int | None, Query(ge=1, le=LANE_ID_MAX)] = None,
    since: datetime | None = None,
    until: datetime | None = None,
    session_status: Annotated[Literal["running", "ended"] | None, Query(alias="status")] = None,
    cursor: Annotated[str | None, Query(pattern=CURSOR)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    epoch: Annotated[int | None, Query(ge=1)] = None,
    key: Annotated[str | None, Query(pattern=KEY_ID)] = None,
) -> BaseModel:
    """Newest first, from the current epoch or `epoch`; pass `next_cursor` back as `cursor`.
    `key` keeps only the sessions that bet with that vault key."""
    scope.check(lane)
    scope.check_key(key)
    cache, cache_key = _cache(request, scope)
    if (hit := cache.get(cache_key)) is not None:
        return hit
    view = await epoch_view(db, service, epoch)
    page = await session_page(
        db,
        service,
        view.ids,
        lane,
        since,
        until,
        session_status,
        cursor,
        limit,
        with_total=cursor is None,
        within=scope_condition(scope),
        key=key,
    )
    cache.put(cache_key, page)
    return page


async def session_page(
    db: AsyncSession,
    service: str,
    epochs: Select[int] | Sequence[int] | None,
    lane: int | None,
    since: datetime | None,
    until: datetime | None,
    session_status: Literal["running", "ended"] | None,
    cursor: str | None,
    limit: int,
    with_total: bool = False,
    within: ColumnElement[bool] | None = None,
    key: str | None = None,
) -> SessionPage:
    """One page of sessions, newest first; `epochs` limits it to those databases and `within`
    to a caller's scope (`scope_condition`).

    With `with_total`, the page also carries how many sessions match the filters.
    """
    filters: list[Any] = [HistorySession.service == service]
    if epochs is not None:
        filters.append(HistorySession.epoch_id.in_(epochs))
    if lane is not None:
        filters.append(HistorySession.lane == lane)
    if within is not None:
        filters.append(within)
    if key is not None:
        filters.append(func.coalesce(HistorySession.key_ref, MAIN_KEY) == key)
    if since is not None:
        filters.append(HistorySession.started_at >= since)
    if until is not None:
        filters.append(HistorySession.started_at < until)
    if session_status == "running":
        filters.append(HistorySession.ended_at.is_(None))
    elif session_status == "ended":
        filters.append(HistorySession.ended_at.is_not(None))
    query = (
        select(HistorySession)
        .where(*filters)
        .order_by(HistorySession.epoch_id.desc(), HistorySession.src_id.desc())
        .limit(limit + 1)
    )
    if cursor is not None:
        epoch_id, src_id = (int(part) for part in cursor.split(":"))
        query = query.where(
            tuple_(HistorySession.epoch_id, HistorySession.src_id) < tuple_(epoch_id, src_id)
        )
    rows = list(await db.scalars(query))
    more = len(rows) > limit
    rows = rows[:limit]
    total = (
        await db.scalar(select(func.count()).select_from(HistorySession).where(*filters))
        if with_total
        else None
    )
    return SessionPage(
        items=[_session_out(SessionOut, row) for row in rows],
        next_cursor=f"{rows[-1].epoch_id}:{rows[-1].src_id}" if more else None,
        total=total,
    )


def scope_condition(scope: LaneScope) -> ColumnElement[bool] | None:
    """The sessions a scope may see, as a filter on `sessions`: None for every session."""
    if scope.keys is not None:
        keyed = func.coalesce(HistorySession.key_ref, MAIN_KEY).in_(sorted(scope.keys))
        if not scope.own_lanes:
            return keyed
        return or_(keyed, HistorySession.lane.in_(sorted(scope.own_lanes)))
    if scope.lanes is not None:
        return HistorySession.lane.in_(sorted(scope.lanes))
    return None


async def _visible_session(
    db: AsyncSession, scope: LaneScope, service: str, epoch_id: int, session_id: int
) -> HistorySession:
    """The session, or 404 when it does not exist or is outside the scope."""
    row = await find_session(db, service, epoch_id, session_id)
    if not scope.allows_session(row.lane, row.key_ref):
        raise HTTPException(status.HTTP_404_NOT_FOUND, SESSION_NOT_FOUND)
    return row


async def find_session(
    db: AsyncSession,
    service: str,
    epoch_id: int,
    session_id: int,
    epochs: Select[int] | None = None,
) -> HistorySession:
    """The session, or 404; with `epochs`, one outside those databases is not found either."""
    query = select(HistorySession).where(
        HistorySession.service == service,
        HistorySession.epoch_id == epoch_id,
        HistorySession.src_id == session_id,
    )
    if epochs is not None:
        query = query.where(HistorySession.epoch_id.in_(epochs))
    row = await db.scalar(query)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, SESSION_NOT_FOUND)
    return row


@router.get("/sessions/{epoch_id}/{session_id}", response_model=SessionDetail)
async def get_session_detail(
    service: str,
    epoch_id: Annotated[int, Path(ge=1)],
    session_id: Annotated[int, Path(ge=1)],
    scope: Viewer,
    db: Db,
) -> SessionDetail:
    return _session_out(
        SessionDetail, await _visible_session(db, scope, service, epoch_id, session_id)
    )


@router.get("/sessions/{epoch_id}/{session_id}/bets", response_model=BetPage)
async def list_session_bets(
    service: str,
    epoch_id: Annotated[int, Path(ge=1)],
    session_id: Annotated[int, Path(ge=1)],
    request: Request,
    scope: Viewer,
    db: Db,
    before: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> BaseModel:
    """Newest first; pass `next_before` back as `before` for the next page."""
    cache, key = _cache(request, scope)
    if (hit := cache.get(key)) is not None:
        return hit
    await _visible_session(db, scope, service, epoch_id, session_id)
    page = await bet_page(db, service, epoch_id, session_id, before, limit)
    cache.put(key, page)
    return page


def bet_out(row: Bet) -> BetOut:
    return BetOut(
        id=row.src_id,
        ts=row.ts,
        amount=row.amount,
        multiplier=row.multiplier,
        rule=row.rule,
        bet_value=row.bet_value,
        result_value=row.result_value,
        state=row.state,
        profit=row.profit,
        balance_after=row.balance_after,
    )


async def bet_page(
    db: AsyncSession, service: str, epoch_id: int, session_id: int, before: int | None, limit: int
) -> BetPage:
    """One page of a session's bets, newest first."""
    query = (
        select(Bet)
        .where(Bet.service == service, Bet.epoch_id == epoch_id, Bet.session_src_id == session_id)
        .order_by(Bet.src_id.desc())
        .limit(limit + 1)
    )
    if before is not None:
        query = query.where(Bet.src_id < before)
    rows = list(await db.scalars(query))
    more = len(rows) > limit
    rows = rows[:limit]
    return BetPage(
        items=[bet_out(row) for row in rows], next_before=rows[-1].src_id if more else None
    )


def pick_resolution(span: timedelta, wanted: Resolution | None) -> Resolution:
    """The requested resolution, or the finest one that fits the point budget."""
    if wanted is not None:
        if math.ceil(span / RESOLUTIONS[wanted]) > MAX_POINTS:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, RANGE_TOO_LONG)
        return wanted
    for name, step in RESOLUTIONS.items():
        if math.ceil(span / step) <= MAX_POINTS:
            return name  # type: ignore[return-value]
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, RANGE_TOO_LONG)


_SERIES_SQL = {
    "1s": (
        "SELECT time_bucket(INTERVAL '1 second', ts) AS t, lane,"
        " sum(profit) AS profit, last(balance_after, ts) AS balance FROM bets"
    ),
    "1m": "SELECT bucket AS t, lane, profit, last_balance AS balance FROM bets_1m",
    "1h": "SELECT bucket AS t, lane, profit, last_balance AS balance FROM bets_1h",
    "1d": "SELECT bucket AS t, lane, profit, last_balance AS balance FROM bets_1d",
}
_EPOCH_SERIES_SQL = (
    "SELECT time_bucket(:step, ts) AS t, lane, sum(profit) AS profit,"
    " last(balance_after, ts) AS balance FROM bets"
    " WHERE service = :service AND epoch_id = ANY(:epochs) AND ts >= :start AND ts < :end"
)
_KEY_SERIES_SQL = (
    "SELECT time_bucket(:step, b.ts) AS t, b.lane AS lane, sum(b.profit) AS profit,"
    " last(b.balance_after, b.ts) AS balance FROM bets b JOIN sessions s"
    " ON s.service = b.service AND s.epoch_id = b.epoch_id AND s.src_id = b.session_src_id"
    " WHERE b.service = :service AND b.ts >= :start AND b.ts < :end"
    " AND (coalesce(s.key_ref, :main_key) = ANY(:keys) OR b.lane = ANY(:own))"
)
_TIME_COLUMN = {"1s": "ts", "1m": "bucket", "1h": "bucket", "1d": "bucket"}


@router.get("/series", response_model=SeriesOut)
async def get_series(
    service: str,
    request: Request,
    scope: Viewer,
    db: Db,
    metric: Literal["profit", "balance"] = "profit",
    res: Resolution | None = None,
    since: Annotated[datetime | None, Query(alias="from")] = None,
    until: Annotated[datetime | None, Query(alias="to")] = None,
    lane: Annotated[int | None, Query(ge=1, le=LANE_ID_MAX)] = None,
    epoch: Annotated[int | None, Query(ge=1)] = None,
) -> BaseModel:
    """Per-lane points `[t, value]`: profit summed per bucket, or the bucket's last balance,
    from the current epoch or `epoch`, cut to that epoch's whole buckets."""
    scope.check(lane)
    cache, key = _cache(request, scope)
    if (hit := cache.get(key)) is not None:
        return hit
    end = until or datetime.now(UTC)
    start = since or end - DEFAULT_RANGE
    if start >= end:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, EMPTY_RANGE)
    chosen = pick_resolution(end - start, res)
    view = await epoch_view(db, service, epoch)
    first, last = view.bounds(RESOLUTIONS[chosen])
    start = max(start, first) if first else start
    end = min(end, last) if last else end
    if not view.ids or start >= end:
        out = SeriesOut(res=chosen, series=[])
    else:
        out = await series(
            db, service, None, metric, chosen, start, end, lane, scope.lanes, scope=scope
        )
    cache.put(key, out)
    return out


async def series(
    db: AsyncSession,
    service: str,
    epochs: list[int] | None,
    metric: Literal["profit", "balance"],
    res: Resolution | None,
    since: datetime | None,
    until: datetime | None,
    lane: int | None,
    lanes: Collection[int] | None = None,
    scope: LaneScope | None = None,
) -> SeriesOut:
    """Per-lane points over a range, of `lane` or of `lanes` when given. With `epochs`, only
    those databases' bets count; the rollups mix every database, so that reads the raw bets
    at any resolution. A key scope reads raw bets too, joined to their sessions' keys: the
    rollups are kept per lane, and a lane may have bet with another key before."""
    end = until or datetime.now(UTC)
    start = since or end - DEFAULT_RANGE
    if start >= end:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, EMPTY_RANGE)
    chosen = pick_resolution(end - start, res)
    lane_filter = " AND lane = :lane" if lane is not None else ""
    if lanes is not None:
        lane_filter += " AND lane = ANY(:lanes)"
    params: dict[str, Any] = {"service": service, "start": start, "end": end}
    if scope is not None and scope.keys is not None:
        statement, params = _key_series(service, start, end, chosen, lane, scope)
    elif epochs is not None:
        # nosemgrep: python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text
        statement = text(
            f"{_EPOCH_SERIES_SQL}{lane_filter} GROUP BY 1, lane ORDER BY lane, t"  # nosec B608: fixed SQL
        )
        params |= {"step": RESOLUTIONS[chosen], "epochs": epochs}
    else:
        column = _TIME_COLUMN[chosen]
        grouping = " GROUP BY 1, lane" if chosen == "1s" else ""
        statement = text(
            f"{_SERIES_SQL[chosen]} WHERE service = :service"  # nosec B608: fixed SQL per resolution
            f" AND {column} >= :start AND {column} < :end{lane_filter}{grouping} ORDER BY lane, t"
        )
    if lane is not None:
        params["lane"] = lane
    if lanes is not None and "keys" not in params:
        params["lanes"] = sorted(lanes)
    rows = (await db.execute(statement, params)).all()
    by_lane: dict[int, list[tuple[datetime, float | None]]] = {}
    for row in rows:
        values = row._mapping
        by_lane.setdefault(values["lane"], []).append((values["t"], values[metric]))
    return SeriesOut(
        res=chosen,
        series=[LaneSeries(lane=k, points=v) for k, v in sorted(by_lane.items())],
    )


def _key_series(
    service: str, start: datetime, end: datetime, chosen: str, lane: int | None, scope: LaneScope
) -> tuple[Any, dict[str, Any]]:
    lane_filter = " AND b.lane = :lane" if lane is not None else ""
    # nosemgrep: python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text
    statement = text(
        f"{_KEY_SERIES_SQL}{lane_filter} GROUP BY 1, b.lane ORDER BY lane, t"  # nosec B608: fixed SQL
    )
    params: dict[str, Any] = {
        "service": service,
        "start": start,
        "end": end,
        "step": RESOLUTIONS[chosen],
        "main_key": MAIN_KEY,
        "keys": sorted(scope.keys or ()),
        "own": sorted(scope.own_lanes),
    }
    return statement, params


async def current_sessions(db: AsyncSession, service: str) -> list[HistorySession]:
    """Each lane's latest session in the current epoch; none right after a reset."""
    view = await epoch_view(db, service)
    query = (
        select(HistorySession)
        .where(HistorySession.service == service, HistorySession.epoch_id.in_(view.ids))
        .ext(distinct_on(HistorySession.lane))
        .order_by(HistorySession.lane, HistorySession.src_id.desc())
    )
    return list(await db.scalars(query))


@router.get("/current", response_model=CurrentOut)
async def get_current(service: str, request: Request, scope: Viewer, db: Db) -> BaseModel:
    """Each lane's latest session in the current epoch, with its totals, for live resync."""
    cache, key = _cache(request, scope)
    if (hit := cache.get(key)) is not None:
        return hit
    rows = await current_sessions(db, service)
    out = CurrentOut(
        lanes=[
            _session_out(SessionOut, row)
            for row in rows
            if scope.allows(row.lane) and scope.allows_session(row.lane, row.key_ref)
        ]
    )
    cache.put(key, out)
    return out


@router.get("/epochs", response_model=EpochList)
async def list_epochs(service: str, scope: Viewer, db: Db) -> EpochList:
    """The bot's databases, newest first: the current one and each one a reset archived.

    For a lane-only user the last start counts only their lanes' sessions.
    """
    view = await epoch_view(db, service)
    joined = (HistorySession.service == DbEpoch.service) & (
        HistorySession.epoch_id == DbEpoch.epoch_id
    )
    if (within := scope_condition(scope)) is not None:
        joined &= within
    rows = (
        await db.execute(
            select(
                DbEpoch.epoch_id,
                DbEpoch.first_started_at,
                func.max(HistorySession.started_at).label("last_started_at"),
            )
            .outerjoin(HistorySession, joined)
            .where(DbEpoch.service == service, DbEpoch.tg_uid.is_(None))
            .group_by(DbEpoch.epoch_id)
            .order_by(DbEpoch.epoch_id.desc())
        )
    ).all()
    return EpochList(
        items=[
            EpochOut(
                epoch_id=row.epoch_id,
                first_started_at=row.first_started_at,
                last_started_at=row.last_started_at,
                current=row.epoch_id in view.ids,
            )
            for row in rows
        ]
    )


async def latest_reconcile(db: AsyncSession, service: str) -> ReconcileRun | None:
    """The service's newest reconcile run: how far ingest lags and whether history matches."""
    run: ReconcileRun | None = await db.scalar(
        select(ReconcileRun)
        .where(ReconcileRun.service == service)
        .order_by(ReconcileRun.id.desc())
        .limit(1)
    )
    return run


@router.get("/ingest", response_model=IngestOut)
async def get_ingest(service: str, request: Request, scope: Viewer, db: Db) -> BaseModel:
    """The latest reconcile run: how far ingest lags and whether the history copy matches.

    Service-wide health, so a lane-only user is refused.
    """
    if scope.lanes is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, FORBIDDEN)
    cache, key = _cache(request, scope)
    if (hit := cache.get(key)) is not None:
        return hit
    run = await latest_reconcile(db, service)
    out = IngestOut(
        last_run=None
        if run is None
        else ReconcileOut(
            finished_at=run.finished_at,
            kind=run.kind,
            mismatched=run.mismatched,
            persisting=run.persisting,
            purged_upstream=run.purged_upstream,
            lag_rows=run.lag_rows,
            lag_seconds=run.lag_seconds,
            lag_alert=run.lag_alert,
        )
    )
    cache.put(key, out)
    return out
