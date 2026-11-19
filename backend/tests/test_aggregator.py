"""The live model matches the bot's own numbers and ticks report only what changed."""

import asyncio
import random
import statistics
import time
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.adapters.base import Event
from app.adapters.wolfbet.events import BetEvent, LifecycleEvent, StateEvent
from app.live.aggregator import STALE_AFTER_S, TAPE_SIZE, Aggregator, Tick, run_ticker
from app.live.consumer import Gap


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _envelope(kind: str, lane: int, seq: int, ts_ms: int, pid: int = 7) -> dict[str, Any]:
    return {
        "v": 1,
        "svc": "wolfbet",
        "lane": lane,
        "pid": pid,
        "seq": seq,
        "ts_ms": ts_ms,
        "type": kind,
    }


def bet(
    lane: int = 1,
    seq: int = 0,
    *,
    win: bool = True,
    amount: float = 1.0,
    session_profit: float = 0.0,
    balance: float = 100.0,
    session_id: int = 1,
    ts_ms: int = 1_000_000,
    api_ms: float = 40.0,
) -> BetEvent:
    payload = {
        "session_id": session_id,
        "amount": amount,
        "multiplier": 2.0,
        "rule": "over",
        "bet_value": 49.5,
        "result_value": 75.0 if win else 10.0,
        "state": "win" if win else "loss",
        "profit": amount if win else -amount,
        "balance_after": balance,
        "session_profit": session_profit,
        "total_bets": 0,
        "current_streak": 0,
        "api_ms": api_ms,
    }
    return BetEvent.model_validate({**_envelope("bet", lane, seq, ts_ms), "payload": payload})


def state(lane: int = 1, seq: int = 0, **payload: Any) -> StateEvent:
    return StateEvent.model_validate({**_envelope("state", lane, seq, 0), "payload": payload})


def lifecycle(
    kind: str, lane: int = 1, seq: int = 0, data: dict[str, Any] | None = None
) -> LifecycleEvent:
    payload = {"kind": kind, "data": data or {}}
    body = {**_envelope("lifecycle", lane, seq, 0), "payload": payload}
    return LifecycleEvent.model_validate(body)


@dataclass
class BotState:
    """The bot's own bookkeeping, written out again from `_apply_backtest_result`."""

    session_id: int
    total_bets: int = 0
    wins: int = 0
    losses: int = 0
    wagered: float = 0.0
    profit: float = 0.0
    current_streak: int = 0
    max_win_streak: int = 0
    max_loss_streak: int = 0
    current_balance: float = 50.0
    seq: int = 0
    events: list[Event] = field(default_factory=list)

    def record(self, lane: int, won: bool, amount: float, ts_ms: int) -> None:
        pnl = amount if won else -amount
        self.total_bets += 1
        self.wagered += amount
        self.profit += pnl
        self.current_balance += pnl
        if won:
            self.wins += 1
            self.current_streak = max(self.current_streak, 0) + 1
            self.max_win_streak = max(self.max_win_streak, self.current_streak)
        else:
            self.losses += 1
            self.current_streak = min(self.current_streak, 0) - 1
            self.max_loss_streak = max(self.max_loss_streak, abs(self.current_streak))
        self.events.append(
            bet(
                lane,
                self.seq,
                win=won,
                amount=amount,
                session_profit=self.profit,
                balance=self.current_balance,
                session_id=self.session_id,
                ts_ms=ts_ms,
            )
        )
        self.seq += 1

    def snapshot(self, lane: int) -> StateEvent:
        fields = {
            k: getattr(self, k)
            for k in (
                "session_id",
                "total_bets",
                "wins",
                "losses",
                "wagered",
                "profit",
                "current_streak",
                "max_win_streak",
                "max_loss_streak",
                "current_balance",
            )
        }
        event = state(lane, self.seq, running=True, paused=False, currency="usdt", **fields)
        self.seq += 1
        return event


def _replay(seed: int, bets: int, lanes: int) -> tuple[list[Event], dict[int, BotState]]:
    rng = random.Random(seed)  # noqa: S311  # a reproducible replay, not a secret
    bots = {lane: BotState(session_id=100 + lane) for lane in range(1, lanes + 1)}
    stream: list[Event] = []
    for n in range(bets):
        lane = rng.randint(1, lanes)
        bot = bots[lane]
        before = len(bot.events)
        bot.record(lane, rng.random() < 0.495, rng.choice([1e-8, 2e-8, 4e-8, 8e-8]), 1_000_000 + n)
        stream.extend(bot.events[before:])
        if bot.total_bets % 250 == 0:
            stream.append(bot.snapshot(lane))
    return stream, bots


def test_replay_of_10000_bets_matches_the_bots_final_state_on_every_lane() -> None:
    stream, bots = _replay(seed=20261119, bets=10_000, lanes=3)
    aggregator = Aggregator("wolfbet", clock=Clock())
    for event in stream:
        aggregator.apply(event)
    snapshot = aggregator.tick().snapshot_diff
    for lane, bot in bots.items():
        final = snapshot[lane]
        assert final["total_bets"] == bot.total_bets
        assert (final["wins"], final["losses"]) == (bot.wins, bot.losses)
        assert final["profit"] == pytest.approx(bot.profit, abs=1e-12)
        assert final["wagered"] == pytest.approx(bot.wagered, abs=1e-12)
        assert final["current_streak"] == bot.current_streak
        assert (final["max_win_streak"], final["max_loss_streak"]) == (
            bot.max_win_streak,
            bot.max_loss_streak,
        )
        assert final["balance"] == pytest.approx(bot.current_balance, abs=1e-12)
    assert sum(b.total_bets for b in bots.values()) == 10_000
    assert aggregator.conflicts == 0


class TestFormulas:
    def test_streaks_follow_the_bot(self) -> None:
        aggregator = Aggregator("wolfbet", clock=Clock())
        streaks = []
        for seq, won in enumerate([True, True, False, False, False, True]):
            aggregator.apply(bet(seq=seq, win=won))
            streaks.append(aggregator.lanes[1].current_streak)
        assert streaks == [1, 2, -1, -2, -3, 1]
        lane = aggregator.lanes[1]
        assert (lane.max_win_streak, lane.max_loss_streak) == (2, 3)

    def test_roi_is_profit_over_wagered_in_percent(self) -> None:
        aggregator = Aggregator("wolfbet", clock=Clock())
        assert aggregator.tick().snapshot_diff == {}
        aggregator.apply(bet(amount=4.0, session_profit=1.0))
        assert aggregator.tick().snapshot_diff[1]["roi"] == pytest.approx(25.0)

    def test_bot_bets_per_second_comes_from_its_state(self) -> None:
        aggregator = Aggregator("wolfbet", clock=Clock())
        aggregator.apply(state(bets_per_second=27.5))
        assert aggregator.tick().snapshot_diff[1]["bps_avg"] == 27.5

    def test_live_rates_count_the_last_second_and_smooth_per_tick(self) -> None:
        clock = Clock()
        aggregator = Aggregator("wolfbet", clock=clock)
        aggregator.tick()
        for seq in range(30):
            clock.now += 0.02
            aggregator.apply(bet(seq=seq))
        clock.now += 0.4
        first = aggregator.tick().snapshot_diff[1]
        assert first["bps_1s"] == 30
        assert 0 < first["bps_ewma"] < 30
        for _ in range(20):
            for seq in range(6):
                clock.now += 1 / 30
                aggregator.apply(bet(seq=seq))
            aggregator.tick()
        assert aggregator.lanes[1].bps_ewma == pytest.approx(30, rel=0.05)

    def test_latency_percentiles(self) -> None:
        aggregator = Aggregator("wolfbet", clock=Clock())
        for seq, ms in enumerate(range(1, 101)):
            aggregator.apply(bet(seq=seq, api_ms=float(ms)))
        diff = aggregator.tick().snapshot_diff[1]
        assert (diff["api_ms_p50"], diff["api_ms_p99"]) == (50.0, 99.0)


class TestReconcile:
    def test_state_wins_and_the_mismatch_is_counted(self) -> None:
        aggregator = Aggregator("wolfbet", clock=Clock())
        aggregator.apply(bet(seq=0, session_id=5))
        aggregator.apply(state(seq=1, session_id=5, total_bets=3, wins=1, losses=0))
        lane = aggregator.lanes[1]
        assert lane.total_bets == 3
        assert aggregator.conflicts == 1

    def test_publisher_counters_come_from_state_without_the_error_text(self) -> None:
        aggregator = Aggregator("wolfbet", clock=Clock())
        events = {
            "enabled": True,
            "running": True,
            "connected": False,
            "sent": 900,
            "queued": 3,
            "dropped": 2,
            "overflow": 5,
            "last_error": "redis://user:pw@host refused",
        }
        aggregator.apply(state(lane=2, session_id=1, events=events))
        assert aggregator.lanes[2].publisher == {
            "enabled": True,
            "connected": False,
            "sent": 900,
            "queued": 3,
            "dropped": 7,
        }

    def test_bet_sizes_split_and_run_profit_come_from_state(self) -> None:
        aggregator = Aggregator("wolfbet", clock=Clock())
        aggregator.apply(
            state(
                session_id=1,
                base_bet=0.0001,
                current_bet=0.0004,
                lane_split="equal",
                profit_increment_scope="run",
                run_profit_total=1.25,
            )
        )
        lane = aggregator.tick().snapshot_diff[1]
        assert (lane["base_bet"], lane["current_bet"], lane["lane_split"]) == (
            0.0001,
            0.0004,
            "equal",
        )
        assert lane["run_profit"] == 1.25
        aggregator.apply(
            state(session_id=1, profit_increment_scope="session", run_profit_total=1.25)
        )
        assert aggregator.tick().snapshot_diff[1]["run_profit"] is None

    def test_stop_reason_is_kept_until_the_lane_starts_again(self) -> None:
        aggregator = Aggregator("wolfbet", clock=Clock())
        aggregator.apply(lifecycle("stop", data={"reason": "Max profit reached" + "x" * 300}))
        reason = aggregator.tick().snapshot_diff[1]["stop_reason"]
        assert reason.startswith("Max profit reached") and len(reason) == 120
        aggregator.apply(lifecycle("start"))
        assert aggregator.tick().snapshot_diff[1]["stop_reason"] is None

    def test_new_session_restarts_the_lane(self) -> None:
        aggregator = Aggregator("wolfbet", clock=Clock())
        aggregator.apply(bet(seq=0, session_id=1, session_profit=5.0))
        aggregator.apply(bet(seq=1, session_id=2, win=False, session_profit=-1.0))
        lane = aggregator.lanes[1]
        assert (lane.session_id, lane.total_bets, lane.wins, lane.losses) == (2, 1, 0, 1)

    def test_gaps_are_counted_per_lane(self) -> None:
        aggregator = Aggregator("wolfbet", clock=Clock())
        aggregator.apply(Gap("wolfbet", 7, 2, 10, 12))
        assert aggregator.tick().snapshot_diff[2]["gaps"] == 3


class TestStatus:
    def test_lifecycle_and_staleness(self) -> None:
        clock = Clock()
        aggregator = Aggregator("wolfbet", clock=clock)

        def status() -> str:
            return str(aggregator.tick().snapshot_diff.get(1, {}).get("status"))

        aggregator.apply(bet())
        assert status() == "running"
        aggregator.apply(lifecycle("pause"))
        assert status() == "paused"
        aggregator.apply(lifecycle("resume"))
        assert status() == "running"
        clock.now += STALE_AFTER_S + 0.1
        assert status() == "stale"
        aggregator.apply(lifecycle("stop"))
        assert status() == "stopped"

    def test_a_quiet_lane_is_down_only_when_the_agent_says_so(self) -> None:
        clock = Clock()
        aggregator = Aggregator("wolfbet", clock=clock)

        def status() -> str:
            return str(aggregator._snapshot(aggregator.lanes[1], clock.now, None)["status"])

        aggregator.apply(bet())
        aggregator.seed(1, running=False)
        assert status() == "running"
        clock.now += STALE_AFTER_S + 0.1
        assert status() == "down"
        aggregator.apply(lifecycle("stop"))
        assert status() == "stopped"
        aggregator.apply(lifecycle("start"))
        assert status() == "running"

    def test_a_quiet_lane_with_events_off_is_not_stale_or_down(self) -> None:
        clock = Clock()
        aggregator = Aggregator("wolfbet", clock=clock)
        aggregator.apply(bet())
        aggregator.seed(1, running=True)
        clock.now += STALE_AFTER_S + 0.1
        lane = aggregator.lanes[1]
        assert aggregator._snapshot(lane, clock.now, None)["status"] == "stale"
        aggregator.events_enabled = False
        assert aggregator._snapshot(lane, clock.now, None)["status"] == "events_off"

    def test_seed_sets_stored_totals_then_the_daemons_state_without_conflicts(self) -> None:
        aggregator = Aggregator("wolfbet", clock=Clock())
        stored = {"id": 4, "total_bets": 120, "profit": 3.5, "wins": 60, "end_balance": 90.0}
        aggregator.seed(1, stored, {"session_id": 4, "total_bets": 125, "running": True})
        lane = aggregator.lanes[1]
        assert (lane.session_id, lane.total_bets, lane.profit, lane.balance) == (4, 125, 3.5, 90.0)
        assert aggregator.conflicts == 0


class TestTick:
    def test_only_changed_fields_and_nothing_when_idle(self) -> None:
        aggregator = Aggregator("wolfbet", clock=Clock())
        aggregator.apply(bet(seq=0, session_profit=1.0))
        first = aggregator.tick()
        assert {"total_bets", "profit", "status"} <= set(first.snapshot_diff[1])
        aggregator.apply(bet(seq=1, session_profit=2.0))
        second = aggregator.tick().snapshot_diff[1]
        assert second["profit"] == 2.0
        assert "status" not in second
        assert "session_id" not in second
        assert aggregator.tick().empty

    def test_tape_keeps_the_last_500_and_sends_only_new_rows(self) -> None:
        aggregator = Aggregator("wolfbet", clock=Clock())
        for seq in range(TAPE_SIZE + 20):
            aggregator.apply(bet(seq=seq))
        first = aggregator.tick()
        assert len(first.tape_new) == TAPE_SIZE
        assert first.tape_new[-1]["seq"] == TAPE_SIZE + 19
        assert len(aggregator.tape) == TAPE_SIZE
        aggregator.apply(bet(seq=999))
        assert [row["seq"] for row in aggregator.tick().tape_new] == [999]

    def test_one_second_candles_hold_open_high_low_close(self) -> None:
        aggregator = Aggregator("wolfbet", clock=Clock())
        for seq, (profit, ts) in enumerate(
            [(1.0, 5_000), (3.0, 5_400), (-2.0, 5_900), (0.5, 6_100)]
        ):
            aggregator.apply(bet(seq=seq, session_profit=profit, balance=10 + profit, ts_ms=ts))
        candles = {c["t"]: c for c in aggregator.tick().candles_new}
        assert candles[5]["profit"] == [1.0, 3.0, -2.0, -2.0]
        assert candles[5]["balance"] == [11.0, 13.0, 8.0, 8.0]
        assert candles[6]["profit"] == [0.5, 0.5, 0.5, 0.5]


async def test_ticks_keep_their_rate_under_2000_events_per_second() -> None:
    aggregator = Aggregator("wolfbet")
    ticks: list[Tick] = []
    tick_costs: list[float] = []
    original_tick = aggregator.tick

    def timed_tick() -> Tick:
        start = time.perf_counter()
        result = original_tick()
        tick_costs.append(time.perf_counter() - start)
        return result

    aggregator.tick = timed_tick  # type: ignore[method-assign]

    async def emit(tick: Tick) -> None:
        ticks.append(tick)

    stop = asyncio.Event()
    ticker = asyncio.create_task(run_ticker(aggregator, emit, stop, hz=5))
    loop = asyncio.get_running_loop()
    end = loop.time() + 2.0
    seq = 0
    while loop.time() < end:
        for _ in range(20):
            aggregator.apply(bet(lane=seq % 3 + 1, seq=seq))
            seq += 1
        await asyncio.sleep(0.01)
    stop.set()
    await ticker
    assert seq >= 3_000
    assert 9 <= len(ticks) <= 11
    assert statistics.median(tick_costs) < 0.005
    assert max(tick_costs) < 0.05


async def test_ticker_refuses_rates_outside_4_to_10_hz() -> None:
    async def emit(_: Tick) -> None:
        return None

    with pytest.raises(ValueError, match="between 4 and 10"):
        await run_ticker(Aggregator("wolfbet"), emit, asyncio.Event(), hz=20)
