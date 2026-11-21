"""The dashboard's own health: Prometheus text for an internal scraper, checks for admins.

`GET /metrics` answers only inside the stack. Caddy refuses `/api/metrics`, and a request
that carries `X-Forwarded-For` came through a proxy, so it is refused here too. `GET /status`
returns the same numbers as OK/warning checks for owners and admins. Everything is read from
objects the api already keeps (the stream consumer, the socket hub, the tick timer, the system
sampler) plus the newest reconcile run; nothing here touches the bots.
"""

import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.history import latest_reconcile
from app.auth.models import User
from app.auth.permissions import Perm, require
from app.db.engine import get_session
from app.live.aggregator import Aggregator

STALE_EVENT_S = 60.0
LAG_WARN_S = 5.0
TICK_WARN_S = 0.1
AGENT_WARN_S = 1.0
DROP_WINDOW_S = 60.0
NOT_INTERNAL = "Not found."


@dataclass
class Readings:
    """One sample of every number the metrics and the status page show; None when unknown."""

    live: bool = False
    events: int = 0
    dropped: dict[str, int] = field(default_factory=dict)
    gaps: int = 0
    reconnects: int = 0
    lag_s: float | None = None
    last_event_age_s: float | None = None
    ws_clients: int | None = None
    ws_closed_slow: int = 0
    tick_last_s: float | None = None
    tick_max_s: float | None = None
    ingest_lag_s: float | None = None
    ingest_lag_rows: int | None = None
    ingest_lag_alert: bool = False
    reconcile_mismatched: int | None = None
    agent_up: bool | None = None
    agent_latency_s: float | None = None
    publisher_dropped: dict[int, int] = field(default_factory=dict)

    @property
    def drops_total(self) -> int:
        return sum(self.dropped.values()) + self.gaps + sum(self.publisher_dropped.values())


class DropWatch:
    """Whether the drop total grew in the last minute, from the totals seen by each read."""

    def __init__(self, window_s: float = DROP_WINDOW_S) -> None:
        self.window_s = window_s
        self._seen: deque[tuple[float, int]] = deque()

    def grew(self, now: float, total: int) -> bool:
        while len(self._seen) > 1 and now - self._seen[1][0] >= self.window_s:
            self._seen.popleft()
        before = self._seen[0][1] if self._seen else total
        self._seen.append((now, total))
        return total > before


async def read(app: Any, db: AsyncSession, now_ms: float | None = None) -> Readings:
    """Collect the readings from the running app and the newest reconcile run."""
    now_ms = time.time() * 1000 if now_ms is None else now_ms
    r = Readings()
    consumer = app.state.consumer
    if consumer is not None:
        r.live = True
        stats = consumer.stats
        r.events = stats.get("events", 0)
        r.gaps = stats.get("gaps", 0)
        r.reconnects = stats.get("reconnects", 0)
        r.dropped = {
            key.removeprefix("dropped:"): value
            for key, value in stats.items()
            if key.startswith("dropped:")
        }
        if consumer.lag_ms is not None:
            r.lag_s = consumer.lag_ms / 1000
        if consumer.last_event_ms is not None:
            r.last_event_age_s = max(0.0, (now_ms - consumer.last_event_ms) / 1000)
    hub = app.state.hub
    if hub is not None:
        r.ws_clients = len(hub.clients)
        r.ws_closed_slow = hub.closed_slow
        lanes = hub.aggregator.lanes if isinstance(hub.aggregator, Aggregator) else {}
        r.publisher_dropped = {
            number: int(lane.publisher.get("dropped", 0))
            for number, lane in sorted(lanes.items())
            if lane.publisher is not None
        }
    ticks = app.state.ticks
    r.tick_last_s, r.tick_max_s = ticks.last, ticks.max
    system = app.state.system
    if system is not None:
        r.agent_up = system.agent_ok
        if system.agent_ms is not None:
            r.agent_latency_s = system.agent_ms / 1000
    service = app.state.live_service
    if service is not None:
        run = await latest_reconcile(db, service)
        if run is not None:
            r.ingest_lag_s = run.lag_seconds
            r.ingest_lag_rows = run.lag_rows
            r.ingest_lag_alert = run.lag_alert
            r.reconcile_mismatched = run.mismatched
    return r


def _sample(name: str, value: float | int | bool | None, labels: str = "") -> str | None:
    if value is None:
        return None
    number = int(value) if isinstance(value, bool) else value
    return f"{name}{labels} {number}"


def render_prometheus(r: Readings) -> str:
    """Prometheus text exposition 0.0.4; a sample whose value is unknown is left out."""
    families: list[tuple[str, str, str, list[str | None]]] = [
        (
            "dashboard_consumer_events_total",
            "counter",
            "Events read from the bot stream.",
            [_sample("dashboard_consumer_events_total", r.events)],
        ),
        (
            "dashboard_consumer_dropped_total",
            "counter",
            "Events refused, by reason.",
            [
                _sample("dashboard_consumer_dropped_total", n, f'{{reason="{k}"}}')
                for k, n in sorted(r.dropped.items())
            ],
        ),
        (
            "dashboard_consumer_gaps_total",
            "counter",
            "Sequence gaps seen in the stream.",
            [_sample("dashboard_consumer_gaps_total", r.gaps)],
        ),
        (
            "dashboard_consumer_reconnects_total",
            "counter",
            "Stream reconnect attempts.",
            [_sample("dashboard_consumer_reconnects_total", r.reconnects)],
        ),
        (
            "dashboard_consumer_lag_seconds",
            "gauge",
            "Receive time minus send time, last event.",
            [_sample("dashboard_consumer_lag_seconds", r.lag_s)],
        ),
        (
            "dashboard_last_event_age_seconds",
            "gauge",
            "Seconds since the last event arrived.",
            [_sample("dashboard_last_event_age_seconds", r.last_event_age_s)],
        ),
        (
            "dashboard_ws_clients",
            "gauge",
            "Open live sockets.",
            [_sample("dashboard_ws_clients", r.ws_clients)],
        ),
        (
            "dashboard_ws_closed_slow_total",
            "counter",
            "Sockets closed for falling behind.",
            [_sample("dashboard_ws_closed_slow_total", r.ws_closed_slow)],
        ),
        (
            "dashboard_tick_seconds",
            "gauge",
            "Live tick duration: last, and longest lately.",
            [
                _sample("dashboard_tick_seconds", r.tick_last_s, '{q="last"}'),
                _sample("dashboard_tick_seconds", r.tick_max_s, '{q="max"}'),
            ],
        ),
        (
            "dashboard_ingest_lag_seconds",
            "gauge",
            "How far history lags the bot database.",
            [_sample("dashboard_ingest_lag_seconds", r.ingest_lag_s)],
        ),
        (
            "dashboard_ingest_lag_rows",
            "gauge",
            "Bets not yet copied to history.",
            [_sample("dashboard_ingest_lag_rows", r.ingest_lag_rows)],
        ),
        (
            "dashboard_reconcile_mismatched",
            "gauge",
            "Sessions that differ in the last check.",
            [_sample("dashboard_reconcile_mismatched", r.reconcile_mismatched)],
        ),
        (
            "dashboard_agent_up",
            "gauge",
            "1 when the host agent answered the last sample.",
            [_sample("dashboard_agent_up", r.agent_up)],
        ),
        (
            "dashboard_agent_latency_seconds",
            "gauge",
            "Host agent round trip, last sample.",
            [_sample("dashboard_agent_latency_seconds", r.agent_latency_s)],
        ),
        (
            "dashboard_publisher_dropped_total",
            "counter",
            "Events the bot's publisher dropped.",
            [
                _sample("dashboard_publisher_dropped_total", n, f'{{lane="{lane}"}}')
                for lane, n in sorted(r.publisher_dropped.items())
            ],
        ),
    ]
    lines: list[str] = []
    for name, kind, help_text, samples in families:
        present = [s for s in samples if s is not None]
        if not present:
            continue
        lines += [f"# HELP {name} {help_text}", f"# TYPE {name} {kind}", *present]
    return "\n".join(lines) + "\n"


class CheckOut(BaseModel):
    name: str
    ok: bool
    value: float | None
    unit: str
    detail: str


class StatusOut(BaseModel):
    at: datetime
    checks: list[CheckOut]


def checks(r: Readings, drops_grew: bool) -> list[CheckOut]:
    """The status page rows: OK or warning, the number and one line on what it means."""
    if not r.live:
        stream = [
            CheckOut(
                name="Live stream",
                ok=True,
                value=None,
                unit="s",
                detail="Live updates are off on this server.",
            )
        ]
    else:
        age = r.last_event_age_s
        stream = [
            CheckOut(
                name="Live stream",
                ok=age is not None and age <= STALE_EVENT_S,
                value=age,
                unit="s",
                detail="Seconds since the last bot event."
                if age is not None
                else "No bot event received yet.",
            ),
            CheckOut(
                name="Stream lag",
                ok=r.lag_s is None or r.lag_s <= LAG_WARN_S,
                value=r.lag_s,
                unit="s",
                detail="Delay from the bot to the dashboard.",
            ),
        ]
    agent_ok = r.agent_up is True and (r.agent_latency_s or 0) <= AGENT_WARN_S
    return [
        *stream,
        CheckOut(
            name="Dropped events",
            ok=not drops_grew,
            value=r.drops_total,
            unit="count",
            detail="Grew in the last minute." if drops_grew else "None new in the last minute.",
        ),
        CheckOut(
            name="Live viewers",
            ok=True,
            value=r.ws_clients,
            unit="count",
            detail="Open live connections.",
        ),
        CheckOut(
            name="Tick duration",
            ok=r.tick_max_s is None or r.tick_max_s <= TICK_WARN_S,
            value=r.tick_max_s,
            unit="s",
            detail="Longest live update lately.",
        ),
        CheckOut(
            name="Ingest lag",
            ok=not r.ingest_lag_alert,
            value=r.ingest_lag_s,
            unit="s",
            detail="Bets may be purged before they reach history."
            if r.ingest_lag_alert
            else "How far history trails the bot.",
        ),
        CheckOut(
            name="Reconcile",
            ok=not r.reconcile_mismatched,
            value=r.reconcile_mismatched,
            unit="count",
            detail="No check has run yet."
            if r.reconcile_mismatched is None
            else "Sessions that differ between the bot and history.",
        ),
        CheckOut(
            name="Host agent",
            ok=agent_ok,
            value=r.agent_latency_s,
            unit="s",
            detail="Not reachable." if r.agent_up is False else "Round trip to the agent.",
        ),
    ]


router = APIRouter(tags=["status"])


@router.get("/metrics", include_in_schema=False)
async def metrics(
    request: Request, db: Annotated[AsyncSession, Depends(get_session)]
) -> PlainTextResponse:
    if "x-forwarded-for" in request.headers:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_INTERNAL)
    readings = await read(request.app, db)
    return PlainTextResponse(
        render_prometheus(readings), media_type="text/plain; version=0.0.4; charset=utf-8"
    )


@router.get("/status", response_model=StatusOut)
async def status_page(
    _: Annotated[User, Depends(require(Perm.AUDIT_READ))],
    request: Request,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> StatusOut:
    """The dashboard's own health for owners and admins, refreshed by the status page."""
    readings = await read(request.app, db)
    grew = request.app.state.drops.grew(time.monotonic(), readings.drops_total)
    return StatusOut(at=datetime.now(UTC), checks=checks(readings, grew))
