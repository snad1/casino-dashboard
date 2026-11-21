"""The live model of one service, and the fixed-rate tick that reports what changed.

Events update the model as they arrive (O(1) each); browsers never see raw events. A tick, a
few times a second, emits only the changed fields per lane, the bets added to the tape and
the 1 s candles touched since the last tick, so the cost of fan-out follows the tick rate,
not the bet rate. Resync seeds a lane from durable totals and the agent's view of the
daemon, which also tells a dead daemon (`down`) apart from a bot with events turned off.
Everything is computed from the events and an injected clock, so a replay gives the same
output every time.

The numbers follow the bot (`wolfbet/wolfbet.py`): ROI % is profit / wagered x 100
(`build_dashboard_screen`); a win sets the streak to max(streak, 0) + 1 and a loss to
min(streak, 0) - 1 (`_apply_backtest_result`); the bot's own bets/s is the session average
total_bets / elapsed (`betting_loop`), shown as `bps_avg` from its state events. When a state
event disagrees with what the bets added up to, the state wins and the mismatch is counted.
"""

import asyncio
import math
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import structlog

from app.adapters.base import Event
from app.adapters.wolfbet.events import BetEvent, LifecycleEvent, StateEvent
from app.live.consumer import Gap

TAPE_SIZE = 500
LATENCY_WINDOW = 1000
STALE_AFTER_S = 6.0
EWMA_ALPHA = 0.3
MIN_HZ, MAX_HZ, DEFAULT_HZ = 4, 10, 5
FLOAT_TOLERANCE = 1e-9
STOP_REASON_MAX = 120
MAX_RELOAD_KEYS = 64

log = structlog.get_logger("app.live.aggregator")

# Bot state field -> lane counter it confirms.
_RECONCILED = {
    "total_bets": "total_bets",
    "wins": "wins",
    "losses": "losses",
    "profit": "profit",
    "wagered": "wagered",
    "current_streak": "current_streak",
    "max_win_streak": "max_win_streak",
    "max_loss_streak": "max_loss_streak",
    "current_balance": "balance",
}

# Stored session column -> lane counter a resync sets.
_SEEDED = (
    "total_bets",
    "wins",
    "losses",
    "wagered",
    "profit",
    "max_win_streak",
    "max_loss_streak",
)


@dataclass
class Candle:
    lane: int
    t: int
    profit: list[float]
    balance: list[float]

    def add(self, profit: float, balance: float) -> None:
        for ohlc, value in ((self.profit, profit), (self.balance, balance)):
            ohlc[1] = max(ohlc[1], value)
            ohlc[2] = min(ohlc[2], value)
            ohlc[3] = value

    def as_dict(self) -> dict[str, Any]:
        return {"lane": self.lane, "t": self.t, "profit": self.profit, "balance": self.balance}


@dataclass
class Lane:
    lane: int
    session_id: int | None = None
    total_bets: int = 0
    wins: int = 0
    losses: int = 0
    wagered: float = 0.0
    profit: float = 0.0
    current_streak: int = 0
    max_win_streak: int = 0
    max_loss_streak: int = 0
    balance: float | None = None
    running: bool | None = None
    paused: bool | None = None
    stopped: bool = False
    currency: str | None = None
    strategy: str | None = None
    bps_avg: float | None = None
    bps_ewma: float = 0.0
    gaps: int = 0
    agent_running: bool | None = None
    publisher: dict[str, Any] | None = None
    base_bet: float | None = None
    current_bet: float | None = None
    lane_split: str | None = None
    run_profit: float | None = None
    stop_reason: str | None = None
    last_reload: dict[str, Any] | None = None
    last_event_ts_ms: int | None = None
    last_seen: float | None = None
    latencies: deque[float] = field(default_factory=lambda: deque(maxlen=LATENCY_WINDOW))
    recent_bets: deque[float] = field(default_factory=deque)
    bets_since_tick: int = 0
    candle: Candle | None = None
    latency_p50: float | None = None
    latency_p99: float | None = None
    latency_dirty: bool = False

    def new_session(self, session_id: int) -> None:
        self.session_id = session_id
        self.total_bets = self.wins = self.losses = 0
        self.wagered = self.profit = 0.0
        self.current_streak = self.max_win_streak = self.max_loss_streak = 0
        self.stopped = False


@dataclass(frozen=True)
class Tick:
    snapshot_diff: dict[int, dict[str, Any]]
    tape_new: list[dict[str, Any]]
    candles_new: list[dict[str, Any]]

    @property
    def empty(self) -> bool:
        return not (self.snapshot_diff or self.tape_new or self.candles_new)


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _publisher(events: dict[str, Any]) -> dict[str, Any]:
    """The bot's event-publisher counters; its error text stays out of the dashboard."""
    return {
        "enabled": events.get("enabled") is True,
        "connected": events.get("connected") is True,
        "sent": _count(events.get("sent")),
        "queued": _count(events.get("queued")),
        "dropped": _count(events.get("dropped")) + _count(events.get("overflow")),
    }


def _percentile(sorted_values: list[float], fraction: float) -> float:
    index = max(0, math.ceil(fraction * len(sorted_values)) - 1)
    return sorted_values[index]


class Aggregator:
    def __init__(self, svc: str, clock: Callable[[], float] = time.monotonic) -> None:
        self.svc = svc
        self.clock = clock
        self.lanes: dict[int, Lane] = {}
        self.conflicts = 0
        self._tape: deque[dict[str, Any]] = deque(maxlen=TAPE_SIZE)
        self._tape_new: deque[dict[str, Any]] = deque(maxlen=TAPE_SIZE)
        self._candles_new: dict[tuple[int, int], Candle] = {}
        self._sent: dict[int, dict[str, Any]] = {}
        self._last_tick: float | None = None
        self._conflict_fields: set[str] = set()
        self.events_enabled: bool | None = None

    def _lane(self, number: int) -> Lane:
        lane = self.lanes.get(number)
        if lane is None:
            lane = self.lanes[number] = Lane(number)
        return lane

    async def accept(self, item: Event | Gap) -> None:
        """Sink for the stream consumer."""
        self.apply(item)

    def apply(self, item: Event | Gap) -> None:
        now = self.clock()
        lane = self._lane(item.lane)
        if isinstance(item, Gap):
            lane.gaps += item.last - item.first + 1
            return
        lane.last_seen = now
        lane.last_event_ts_ms = item.ts_ms
        lane.agent_running = True
        if isinstance(item, BetEvent):
            self._bet(lane, item, now)
        elif isinstance(item, StateEvent):
            self._state(lane, item.payload)
        elif isinstance(item, LifecycleEvent):
            self._lifecycle(lane, item.payload.kind, item.payload.data, item.ts_ms)

    def _bet(self, lane: Lane, event: BetEvent, now: float) -> None:
        bet = event.payload
        if bet.session_id != lane.session_id:
            lane.new_session(bet.session_id)
        lane.total_bets += 1
        lane.wagered += bet.amount
        lane.profit = bet.session_profit
        lane.balance = bet.balance_after
        if bet.state == "win":
            lane.wins += 1
            lane.current_streak = max(lane.current_streak, 0) + 1
            lane.max_win_streak = max(lane.max_win_streak, lane.current_streak)
        else:
            lane.losses += 1
            lane.current_streak = min(lane.current_streak, 0) - 1
            lane.max_loss_streak = max(lane.max_loss_streak, abs(lane.current_streak))
        lane.latencies.append(bet.api_ms)
        lane.latency_dirty = True
        lane.recent_bets.append(now)
        lane.bets_since_tick += 1
        second = event.ts_ms // 1000
        if lane.candle is None or lane.candle.t != second:
            p, b = lane.profit, bet.balance_after
            lane.candle = Candle(lane.lane, second, [p, p, p, p], [b, b, b, b])
        else:
            lane.candle.add(lane.profit, bet.balance_after)
        self._candles_new[(lane.lane, second)] = lane.candle
        row = {
            "lane": lane.lane,
            "seq": event.seq,
            "ts_ms": event.ts_ms,
            "amount": bet.amount,
            "multiplier": bet.multiplier,
            "result": bet.result_value,
            "state": bet.state,
            "profit": bet.profit,
            "balance": bet.balance_after,
        }
        self._tape.append(row)
        self._tape_new.append(row)

    def seed(
        self,
        number: int,
        session: dict[str, Any] | None = None,
        state: dict[str, Any] | None = None,
        running: bool | None = None,
    ) -> None:
        """Set a lane from its stored session totals, then the daemon's own state on top.

        Used after a restart, a gap or a reconnect, so seeded values never count as a
        mismatch. `running` is whether the agent found the lane's daemon alive.
        """
        lane = self._lane(number)
        if session is not None:
            if session["id"] != lane.session_id:
                lane.new_session(session["id"])
            for attr in _SEEDED:
                if session.get(attr) is not None:
                    setattr(lane, attr, type(getattr(lane, attr))(session[attr]))
            if session.get("end_balance") is not None:
                lane.balance = session["end_balance"]
            for key in ("currency", "strategy"):
                if session.get(key) is not None:
                    setattr(lane, key, session[key])
        if state is not None:
            self._state(lane, state, count_conflicts=False)
        if running is not None:
            lane.agent_running = running

    def _state(self, lane: Lane, state: dict[str, Any], count_conflicts: bool = True) -> None:
        session_id = state.get("session_id")
        if isinstance(session_id, int) and session_id != lane.session_id:
            lane.new_session(session_id)
        for key, attr in _RECONCILED.items():
            value = state.get(key)
            if not isinstance(value, int | float) or isinstance(value, bool):
                continue
            current = getattr(lane, attr)
            if (
                count_conflicts
                and current is not None
                and not math.isclose(current, value, rel_tol=0, abs_tol=FLOAT_TOLERANCE)
            ):
                self.conflicts += 1
                self._conflict_fields.add(key)
            setattr(lane, attr, type(current)(value) if current is not None else value)
        lane.running = state.get("running") if isinstance(state.get("running"), bool) else None
        lane.paused = state.get("paused") if isinstance(state.get("paused"), bool) else None
        lane.stopped = lane.running is False
        for key in ("currency", "strategy"):
            if isinstance(state.get(key), str):
                setattr(lane, key, state[key])
        events = state.get("events")
        if isinstance(events, dict):
            lane.publisher = _publisher(events)
        for key in ("base_bet", "current_bet"):
            if _is_number(state.get(key)):
                setattr(lane, key, float(state[key]))
        if isinstance(state.get("lane_split"), str):
            lane.lane_split = state["lane_split"]
        run_total = state.get("run_profit_total")
        per_run = state.get("profit_increment_scope") == "run"
        lane.run_profit = (
            float(run_total) if per_run and isinstance(run_total, int | float) else None
        )
        bps = state.get("bets_per_second")
        if isinstance(bps, int | float) and not isinstance(bps, bool):
            lane.bps_avg = float(bps)

    def _lifecycle(self, lane: Lane, kind: str, data: dict[str, Any], ts_ms: int) -> None:
        if kind == "reload":
            applied = data.get("applied")
            keys = (
                sorted(str(k) for k in applied)[:MAX_RELOAD_KEYS]
                if isinstance(applied, dict)
                else []
            )
            lane.last_reload = {"ts_ms": ts_ms, "keys": keys}
        elif kind == "stop":
            lane.stopped = True
            reason = data.get("reason")
            lane.stop_reason = (
                reason[:STOP_REASON_MAX] if isinstance(reason, str) and reason else None
            )
        elif kind in ("start", "resume"):
            lane.stopped = False
            lane.paused = False
            lane.stop_reason = None
        elif kind == "pause":
            lane.paused = True

    def _status(self, lane: Lane, now: float) -> str:
        quiet = lane.last_seen is None or now - lane.last_seen > STALE_AFTER_S
        if quiet and lane.agent_running is False:
            return "down"
        if lane.stopped:
            return "stopped"
        if quiet:
            return "events_off" if self.events_enabled is False else "stale"
        return "paused" if lane.paused else "running"

    def _snapshot(self, lane: Lane, now: float, elapsed: float | None) -> dict[str, Any]:
        while lane.recent_bets and now - lane.recent_bets[0] > 1.0:
            lane.recent_bets.popleft()
        if elapsed:
            rate = lane.bets_since_tick / elapsed
            lane.bps_ewma = EWMA_ALPHA * rate + (1 - EWMA_ALPHA) * lane.bps_ewma
        lane.bets_since_tick = 0
        if lane.latency_dirty and lane.latencies:
            ordered = sorted(lane.latencies)
            lane.latency_p50 = _percentile(ordered, 0.50)
            lane.latency_p99 = _percentile(ordered, 0.99)
            lane.latency_dirty = False
        roi = lane.profit / lane.wagered * 100 if lane.wagered > 0 else 0.0
        return {
            "session_id": lane.session_id,
            "status": self._status(lane, now),
            "total_bets": lane.total_bets,
            "wins": lane.wins,
            "losses": lane.losses,
            "profit": lane.profit,
            "wagered": lane.wagered,
            "roi": roi,
            "current_streak": lane.current_streak,
            "max_win_streak": lane.max_win_streak,
            "max_loss_streak": lane.max_loss_streak,
            "balance": lane.balance,
            "currency": lane.currency,
            "strategy": lane.strategy,
            "bps_avg": lane.bps_avg,
            "bps_ewma": round(lane.bps_ewma, 3),
            "bps_1s": len(lane.recent_bets),
            "api_ms_p50": lane.latency_p50,
            "api_ms_p99": lane.latency_p99,
            "gaps": lane.gaps,
            "last_event_age_s": None if lane.last_seen is None else round(now - lane.last_seen, 1),
            "base_bet": lane.base_bet,
            "current_bet": lane.current_bet,
            "lane_split": lane.lane_split,
            "run_profit": lane.run_profit,
            "stop_reason": lane.stop_reason,
            "last_reload": lane.last_reload,
        }

    def tick(self) -> Tick:
        """What changed since the last tick: work per lane, never per event."""
        now = self.clock()
        elapsed = None if self._last_tick is None else now - self._last_tick
        self._last_tick = now
        diff: dict[int, dict[str, Any]] = {}
        for number, lane in self.lanes.items():
            current = self._snapshot(lane, now, elapsed)
            previous = self._sent.get(number, {})
            changed = {k: v for k, v in current.items() if previous.get(k) != v}
            if changed:
                diff[number] = changed
                self._sent[number] = current
        if self._conflict_fields:
            log.warning(
                "state disagreed with the bets; state kept",
                svc=self.svc,
                fields=sorted(self._conflict_fields),
                conflicts=self.conflicts,
            )
            self._conflict_fields.clear()
        tape_new = list(self._tape_new)
        self._tape_new.clear()
        candles = [candle.as_dict() for candle in self._candles_new.values()]
        self._candles_new.clear()
        return Tick(diff, tape_new, candles)

    @property
    def tape(self) -> list[dict[str, Any]]:
        return list(self._tape)

    def snapshot(self) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]]]:
        """Every lane as of the last tick, and the tape; what a new subscriber starts from."""
        return {lane: dict(fields) for lane, fields in self._sent.items()}, list(self._tape)


class TickTimer:
    """How long ticks take: the last one, and the longest in this window and the one before."""

    def __init__(self, window_s: float = 60.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.window_s = window_s
        self.clock = clock
        self.last: float | None = None
        self._max = 0.0
        self._previous_max = 0.0
        self._since = clock()

    def record(self, seconds: float) -> None:
        now = self.clock()
        if now - self._since >= self.window_s:
            self._previous_max = self._max if now - self._since < 2 * self.window_s else 0.0
            self._max = 0.0
            self._since = now
        self.last = seconds
        self._max = max(self._max, seconds)

    @property
    def max(self) -> float | None:
        return None if self.last is None else max(self._max, self._previous_max)


class _Ticks(Protocol):
    @property
    def empty(self) -> bool: ...


class _Ticking[T: _Ticks](Protocol):
    def tick(self) -> T: ...


async def run_ticker[T: _Ticks](
    aggregator: _Ticking[T],
    emit: Callable[[T], Awaitable[None]],
    stop: asyncio.Event,
    hz: int = DEFAULT_HZ,
    timer: TickTimer | None = None,
) -> None:
    """Tick at `hz` on a fixed schedule (no drift); empty ticks are not emitted."""
    if not MIN_HZ <= hz <= MAX_HZ:
        raise ValueError(f"hz must be between {MIN_HZ} and {MAX_HZ}")
    loop = asyncio.get_running_loop()
    period = 1 / hz
    next_at = loop.time() + period
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=max(0.0, next_at - loop.time()))
        except TimeoutError:
            pass
        if stop.is_set():
            return
        next_at += period
        started = loop.time()
        tick = aggregator.tick()
        if not tick.empty:
            await emit(tick)
        if timer is not None:
            timer.record(loop.time() - started)
