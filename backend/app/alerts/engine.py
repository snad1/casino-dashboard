"""Decide when an alert fires, and run that decision on every live tick.

`evaluate` is pure: a rule, one signal, the time and the rule's saved state in; whether it
fires and the new state out. A threshold fires once, then waits until its condition clears
before it can fire again (and never inside its cooldown). A milestone fires each time profit
passes the next multiple of its step. A duration rule fires once its condition has held for
the whole `for_s`. State is plain JSON, saved with every firing in the same transaction as
the event, so a restart neither forgets a firing nor repeats it.

`Engine` reads the aggregator's model as of its latest tick, in memory and with no database
read, and writes from its own task. Day profit is the sum of each lane's profit changes since
host-local midnight, seeded once at start from the hourly rollup.
"""

import asyncio
import math
import re
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts import store
from app.alerts.models import SERVICE_LANE
from app.alerts.rules import (
    INGEST_KINDS,
    BpsBelowParams,
    DownParams,
    DrawdownParams,
    Firing,
    IngestLagParams,
    Kind,
    LossParams,
    ProfitParams,
    Rule,
    StopReasonParams,
)
from app.amounts import amount, signed
from app.live.aggregator import Aggregator

STOPPED_PREFIX = "STOPPED: "
FLUSH_EVERY_S = 0.5
STATE_EVERY_S = 10.0

log = structlog.get_logger("app.alerts")

State = dict[str, Any]
Signal = Mapping[str, Any]


def service_level(rule: Rule) -> bool:
    """Rules about the whole service: ingest health and profit or loss for the day."""
    return rule.kind in INGEST_KINDS or getattr(rule.params, "period", None) == "day"


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and math.isfinite(value) else None


def _period_value(params: ProfitParams | LossParams, signal: Signal) -> tuple[float | None, Any]:
    """The profit the rule watches, and what identifies its period (a new one resets steps)."""
    if params.period == "session":
        return _number(signal.get("profit")), signal.get("session_id")
    if params.period == "run":
        return _number(signal.get("run_profit")), "run"
    return _number(signal.get("day_profit")), signal.get("day")


def _stop_matches(params: StopReasonParams, signal: Signal) -> bool:
    if signal.get("status") != "stopped":
        return False
    reason = str(signal.get("stop_reason") or "").removeprefix(STOPPED_PREFIX)
    if not params.reasons and params.pattern is None:
        return True
    if any(reason.startswith(r) for r in params.reasons):
        return True
    return params.pattern is not None and re.search(params.pattern, reason) is not None


def _lane_label(lane: int | None) -> str:
    return "" if lane is None else f" on lane {lane}"


def _cooled(rule: Rule, state: State, now: float) -> bool:
    last = state.get("last_fired")
    return last is None or now - float(last) >= rule.cooldown_s


def _threshold(
    rule: Rule, holds: bool, state: State, now: float, fire: Callable[[], Firing]
) -> tuple[Firing | None, State]:
    """Fire once when the condition holds; re-arm only after it clears."""
    armed = state.get("armed", True)
    if not holds:
        return None, {**state, "armed": True}
    if armed and _cooled(rule, state, now):
        return fire(), {**state, "armed": False, "last_fired": now}
    return None, state


def _duration(holds: bool, state: State, now: float, for_s: int) -> tuple[bool, State]:
    """Whether the condition has held for `for_s`, tracking when it started."""
    if not holds:
        return False, {**state, "since": None}
    since = state.get("since")
    if since is None:
        return False, {**state, "since": now}
    return now - float(since) >= for_s, state


def evaluate(
    rule: Rule, signal: Signal, now: float, state: State, lane: int | None = None
) -> tuple[Firing | None, State]:
    """Whether `rule` fires on `signal` at `now`, and its state afterwards; never raises."""
    where = _lane_label(lane)

    def firing(value: float | None, text: str) -> Firing:
        return Firing(rule.id, lane, rule.kind.value, value, f"{rule.name}: {text}{where}")

    params = rule.params
    if isinstance(params, ProfitParams):
        value, period = _period_value(params, signal)
        if value is None:
            return None, state
        if params.step is not None:
            return _milestone(rule, params.step, value, period, state, now, firing)
        at = params.at or 0.0
        text = f"profit {signed(value)}"
        return _threshold(rule, value >= at, state, now, lambda: firing(value, text))
    if isinstance(params, LossParams):
        value, _ = _period_value(params, signal)
        if value is None:
            return None, state
        return _threshold(
            rule, value <= -params.at, state, now, lambda: firing(value, f"profit {signed(value)}")
        )
    if isinstance(params, DrawdownParams):
        return _drawdown(rule, params, signal, state, now, firing)
    if isinstance(params, DownParams):
        held, state = _duration(signal.get("status") in params.statuses, state, now, params.for_s)
        status = str(signal.get("status"))
        return _threshold(
            rule, held, state, now, lambda: firing(None, f"bot {status} for {params.for_s} s")
        )
    if isinstance(params, StopReasonParams):
        reason = str(signal.get("stop_reason") or "").removeprefix(STOPPED_PREFIX)
        return _threshold(
            rule,
            _stop_matches(params, signal),
            state,
            now,
            lambda: firing(None, f"stopped ({reason or 'no reason given'})"),
        )
    if isinstance(params, BpsBelowParams):
        bps = _number(signal.get("bps_ewma"))
        slow = signal.get("status") == "running" and bps is not None and bps < params.bps
        held, state = _duration(slow, state, now, params.for_s)
        return _threshold(
            rule,
            held,
            state,
            now,
            lambda: firing(bps, f"{bps or 0:.1f} bets/s for {params.for_s} s"),
        )
    if rule.kind is Kind.PUBLISHER_DROPPED:
        return _dropped(rule, signal, state, now, firing)
    if isinstance(params, IngestLagParams):
        lag = _number(signal.get("lag_seconds")) or 0.0
        behind = lag >= params.seconds if params.seconds else signal.get("lag_alert") is True
        return _threshold(
            rule, behind, state, now, lambda: firing(lag, f"ingest {lag:.0f} s behind")
        )
    if rule.kind is Kind.RECONCILE_MISMATCH:
        count = _number(signal.get("mismatched")) or 0.0
        return _threshold(
            rule,
            count > 0,
            state,
            now,
            lambda: firing(count, f"{count:.0f} sessions differ from the bot's database"),
        )
    return None, state


def _milestone(
    rule: Rule,
    step: float,
    value: float,
    period: Any,
    state: State,
    now: float,
    firing: Callable[[float, str], Firing],
) -> tuple[Firing | None, State]:
    """Fire when profit passes the next multiple of `step`; a new period, or a fall below the
    last milestone, starts counting again from the current profit without firing."""
    reached = math.floor(max(value, 0.0) / step) * step
    next_step = state.get("next_step")
    if next_step is None or state.get("period") != period or value < float(next_step) - 2 * step:
        return None, {**state, "period": period, "next_step": reached + step}
    if value < float(next_step):
        return None, state
    new_state = {**state, "next_step": reached + step}
    if not _cooled(rule, state, now):
        return None, new_state
    return firing(reached, f"profit passed {signed(reached)}"), {**new_state, "last_fired": now}


def _drawdown(
    rule: Rule,
    params: DrawdownParams,
    signal: Signal,
    state: State,
    now: float,
    firing: Callable[[float, str], Firing],
) -> tuple[Firing | None, State]:
    balance = _number(signal.get("balance"))
    if balance is None:
        return None, state
    session = signal.get("session_id")
    peak = state.get("peak")
    if peak is None or state.get("period") != session:
        peak = balance
    peak = max(float(peak), balance)
    state = {**state, "period": session, "peak": peak}
    drop = (peak - balance) / peak * 100 if peak > 0 else 0.0
    return _threshold(
        rule,
        drop >= params.pct,
        state,
        now,
        lambda: firing(drop, f"balance {drop:.1f}% below its peak {amount(peak)}"),
    )


def _dropped(
    rule: Rule,
    signal: Signal,
    state: State,
    now: float,
    firing: Callable[[float, str], Firing],
) -> tuple[Firing | None, State]:
    """Fire when the bot's publisher dropped more events since last seen; a restart resets."""
    dropped = _number(signal.get("dropped"))
    if dropped is None:
        return None, state
    seen = state.get("dropped_seen")
    new_state = {**state, "dropped_seen": dropped}
    if seen is None or dropped <= float(seen) or not _cooled(rule, state, now):
        return None, new_state
    more = dropped - float(seen)
    return firing(more, f"the bot dropped {more:.0f} live events"), {
        **new_state,
        "last_fired": now,
    }


def track_day(day: State, lanes: Mapping[int, tuple[Any, float]], today: str) -> State:
    """Add each lane's profit change since the last tick to today's total.

    A lane seen for the first time adds nothing (the seed covers what came before), and a new
    session counts its profit from zero. At midnight the total starts again from zero.
    """
    if day.get("date") != today:
        day = {"date": today, "total": 0.0, "last": {}}
    total = float(day.get("total", 0.0))
    last = dict(day.get("last", {}))
    for number, (session, profit) in lanes.items():
        previous = last.get(str(number))
        if previous is not None:
            total += profit if previous[0] != session else profit - float(previous[1])
        last[str(number)] = [session, profit]
    return {"date": today, "total": total, "last": last}


def _lane_signal(fields: Mapping[str, Any], publisher: Mapping[str, Any] | None) -> dict[str, Any]:
    signal = dict(fields)
    if publisher is not None:
        signal["dropped"] = publisher.get("dropped")
    return signal


class Engine:
    """Evaluates the live rules of one service after every aggregator tick."""

    def __init__(
        self,
        service: str,
        sessionmaker: async_sessionmaker[AsyncSession],
        host_tz: str,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.service = service
        self.sessionmaker = sessionmaker
        self.zone = ZoneInfo(host_tz)
        self.clock = clock
        self.rules: list[Rule] = []
        self.states: dict[tuple[int, int], State] = {}
        self.day: State = {}
        self._firings: list[tuple[Firing, int, State]] = []
        self._dirty: dict[tuple[int, int], State] = {}
        self._state_at = 0.0

    def today(self) -> str:
        return datetime.fromtimestamp(self.clock(), self.zone).date().isoformat()

    async def start(self) -> None:
        """Load the enabled live rules and their saved state, and seed today's profit."""
        async with self.sessionmaker() as db:
            rules, states = await store.load(db, self.service, live=True)
            day = next(
                (s["day"] for s in states.values() if s.get("day", {}).get("date") == self.today()),
                None,
            )
            if day is None and any(service_level(r) for r in rules):
                midnight = datetime.fromtimestamp(self.clock(), self.zone).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                total = await store.day_profit(db, self.service, midnight)
                day = {"date": self.today(), "total": total, "last": {}}
        self.rules, self.states = rules, states
        self.day = day or {}
        log.info("alert rules loaded", svc=self.service, rules=len(rules))

    async def reload(self) -> None:
        """Pick up edited rules; saved state of rules that still exist is kept."""
        await self.flush(force=True)
        await self.start()

    def on_tick(self, aggregator: Aggregator) -> None:
        """Evaluate every rule against the model as of the latest tick; in memory only."""
        if not self.rules:
            return
        now = self.clock()
        lanes, _ = aggregator.snapshot()
        signals = {
            number: _lane_signal(fields, getattr(aggregator.lanes.get(number), "publisher", None))
            for number, fields in lanes.items()
        }
        self.day = track_day(
            self.day,
            {n: (s.get("session_id"), float(s.get("profit") or 0.0)) for n, s in signals.items()},
            self.today(),
        )
        day_signal = {"day_profit": self.day["total"], "day": self.day["date"]}
        for rule in self.rules:
            if service_level(rule):
                self._apply(rule, SERVICE_LANE, None, day_signal, now)
                continue
            for number, signal in signals.items():
                if rule.lane is None or rule.lane == number:
                    self._apply(rule, number, number, signal, now)

    def _apply(
        self, rule: Rule, key_lane: int, lane: int | None, signal: Signal, now: float
    ) -> None:
        key = (rule.id, key_lane)
        old = self.states.get(key, {})
        fired, new = evaluate(rule, signal, now, old, lane)
        if service_level(rule) and rule.kind not in INGEST_KINDS:
            new = {**new, "day": self.day}
        if new == old and fired is None:
            return
        self.states[key] = new
        if fired is not None:
            self._firings.append((fired, key_lane, new))
            self._dirty.pop(key, None)
        else:
            self._dirty[key] = new

    async def flush(self, force: bool = False) -> None:
        """Write firings now, and changed state at most every `STATE_EVERY_S`."""
        while self._firings:
            fired, key_lane, state = self._firings[0]
            async with self.sessionmaker() as db:
                await store.record(db, self.service, fired, key_lane, state)
                await db.commit()
            self._firings.pop(0)
        now = self.clock()
        if not self._dirty or (not force and now - self._state_at < STATE_EVERY_S):
            return
        dirty, self._dirty = self._dirty, {}
        try:
            async with self.sessionmaker() as db:
                await store.save_states(db, dirty)
                await db.commit()
        except Exception:
            self._dirty = {**dirty, **self._dirty}
            raise
        self._state_at = now

    async def run(self, stop: asyncio.Event, aggregator: Aggregator) -> None:
        """Evaluate the latest tick and write what fired, twice a second until stop is set.

        Running on its own schedule means a lane that stays down (so its ticks are empty)
        is still checked, and an alert failure never touches the live feed.
        """
        while not stop.is_set():
            try:
                self.on_tick(aggregator)
            except Exception:
                log.exception("alert evaluation failed", svc=self.service)
            try:
                await self.flush()
            except Exception:
                log.exception("alert write failed; retrying", svc=self.service)
            try:
                await asyncio.wait_for(stop.wait(), timeout=FLUSH_EVERY_S)
            except TimeoutError:
                pass
        try:
            await self.flush(force=True)
        except Exception:
            log.exception("alert write failed at shutdown", svc=self.service)


async def after_reconcile(
    sessionmaker: async_sessionmaker[AsyncSession],
    service: str,
    run: Mapping[str, Any],
    now: float | None = None,
) -> int:
    """Evaluate the ingest rules against one reconcile run and write what fired; returns
    how many fired. The run is the signal, so ingest health needs no polling."""
    moment = time.time() if now is None else now
    async with sessionmaker() as db:
        rules, states = await store.load(db, service, live=False)
        fired_count = 0
        changed: dict[tuple[int, int], State] = {}
        for rule in rules:
            key = (rule.id, SERVICE_LANE)
            old = states.get(key, {})
            fired, new = evaluate(rule, run, moment, old)
            if fired is not None:
                await store.record(db, service, fired, SERVICE_LANE, new)
                fired_count += 1
            elif new != old:
                changed[key] = new
        await store.save_states(db, changed)
        await db.commit()
    return fired_count
