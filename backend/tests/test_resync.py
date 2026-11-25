"""Resync seeds lanes from stored totals plus the daemon's state, and tells down from events off."""

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.agent_client import AgentError
from app.live.aggregator import STALE_AFTER_S, Aggregator
from app.live.consumer import Gap
from app.live.resync import RESYNC_EVERY_S, Resync
from app.settings import Settings
from tests.test_aggregator import Clock, bet
from tests.test_history_api import Seed


class FakeAgent:
    """Answers like the agent: `status` per lane and `config_show`, or fails on demand."""

    def __init__(self) -> None:
        self.running: dict[int, bool] = {}
        self.state: dict[int, dict[str, Any]] = {}
        self.events_enabled = True
        self.registry: str | None = None
        self.enabled: str | None = None
        self.failing = False
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, cmd: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((cmd, args or {}))
        if self.failing:
            raise AgentError("unreachable")
        if cmd == "wolfbet.config_show":
            return {"json": {"v": 1, "config": {"events_enabled": self.events_enabled}}}
        if cmd.endswith((".lane_ids", ".lane_ids_enabled")):
            ids = self.registry if cmd.endswith(".lane_ids") else (self.enabled or self.registry)
            if ids is None:
                return {"exit_code": 1, "stdout": "", "stderr": "boom"}
            return {"exit_code": 0, "stdout": ids + "\n", "stderr": ""}
        lane = (args or {})["lane"]
        return {
            "json": {
                "v": 1,
                "running": self.running.get(lane, True),
                "state": self.state.get(lane, {"pid": 1, "session_id": None}),
            }
        }

    def status_calls(self) -> int:
        return sum(1 for cmd, _ in self.calls if cmd == "wolfbet.status")


@pytest.fixture
def seed(app_settings: Settings) -> Iterator[Seed]:
    data = Seed(app_settings)
    yield data
    data.clean()


@pytest.fixture
def sessionmaker(app_settings: Settings) -> Iterator[async_sessionmaker[AsyncSession]]:
    """No pool, so each resync round can run in its own event loop."""
    engine = create_async_engine(app_settings.database_url, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    asyncio.run(engine.dispose())


class Rig:
    def __init__(self, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self.clock = Clock()
        self.aggregator = Aggregator(seed.service, clock=self.clock)
        self.agent = FakeAgent()
        self.reconnects = 0
        self.resync = Resync(
            self.aggregator, sessionmaker, self.agent, reconnects=lambda: self.reconnects
        )

    def sync(self) -> None:
        asyncio.run(self.resync.pass_once())

    def lane(self, number: int) -> dict[str, Any]:
        snapshot = self.aggregator._snapshot(self.aggregator.lanes[number], self.clock.now, None)
        return snapshot


@pytest.fixture
def rig(seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]) -> Rig:
    return Rig(seed, sessionmaker)


def test_restart_mid_session_shows_the_running_totals(rig: Rig, seed: Seed) -> None:
    epoch = seed.epoch()
    seed.session(epoch, 4, lane=1, ended=False, profit=3.5)
    rig.agent.state[1] = {"pid": 9, "session_id": 4, "total_bets": 125, "running": True}
    rig.sync()
    tick = rig.aggregator.tick().snapshot_diff[1]
    assert (tick["session_id"], tick["total_bets"], tick["profit"]) == (4, 125, 3.5)
    rig.aggregator.apply(bet(lane=1, session_id=4, session_profit=4.0))
    lane = rig.lane(1)
    assert (lane["total_bets"], lane["status"]) == (126, "running")


def test_a_quiet_lane_with_events_off_reads_events_off_not_down(rig: Rig, seed: Seed) -> None:
    seed.session(seed.epoch(), 1, lane=1, ended=False)
    rig.aggregator.apply(bet(lane=1))
    rig.agent.events_enabled = False
    rig.clock.now += STALE_AFTER_S + 0.1
    assert rig.lane(1)["status"] == "stale"
    rig.sync()
    assert rig.lane(1)["status"] == "events_off"


def test_a_stopped_unit_reads_down_until_events_return(rig: Rig, seed: Seed) -> None:
    seed.session(seed.epoch(), 1, lane=1, ended=False)
    rig.agent.running[1] = False
    with structlog.testing.capture_logs() as logs:
        rig.sync()
        rig.clock.now += RESYNC_EVERY_S
        rig.sync()
    assert rig.lane(1)["status"] == "down"
    assert [e["event"] for e in logs].count("daemon down") == 1
    rig.aggregator.apply(bet(lane=1))
    assert rig.lane(1)["status"] == "running"


def test_resyncs_are_rate_limited_per_lane(rig: Rig, seed: Seed) -> None:
    seed.session(seed.epoch(), 1, lane=1, ended=False)
    rig.sync()
    assert rig.agent.status_calls() == 1
    rig.clock.now += 1
    rig.aggregator.apply(Gap(seed.service, 7, 1, 3, 5))
    rig.sync()
    assert rig.agent.status_calls() == 1
    rig.clock.now += RESYNC_EVERY_S
    rig.sync()
    assert rig.agent.status_calls() == 2


def test_an_unreachable_agent_still_seeds_and_never_guesses_down(rig: Rig, seed: Seed) -> None:
    seed.session(seed.epoch(), 2, lane=1, ended=False, profit=7.0)
    rig.agent.failing = True
    with structlog.testing.capture_logs() as logs:
        rig.sync()
        rig.clock.now += RESYNC_EVERY_S
        rig.sync()
    lane = rig.lane(1)
    assert (lane["session_id"], lane["profit"], lane["status"]) == (2, 7.0, "stale")
    assert [e["event"] for e in logs].count("agent unavailable") == 1


def test_a_reconnect_resyncs_every_lane(rig: Rig, seed: Seed) -> None:
    epoch = seed.epoch()
    seed.session(epoch, 1, lane=1, ended=False)
    seed.session(epoch, 2, lane=2, ended=False)
    rig.sync()
    assert rig.agent.status_calls() == 2
    rig.clock.now += RESYNC_EVERY_S
    rig.aggregator.apply(bet(lane=1, session_id=1))
    rig.aggregator.apply(bet(lane=2, session_id=2))
    rig.sync()
    assert rig.agent.status_calls() == 2
    rig.reconnects += 1
    rig.sync()
    assert rig.agent.status_calls() == 4


def test_lanes_the_bot_no_longer_has_leave_the_live_view(rig: Rig, seed: Seed) -> None:
    epoch = seed.epoch()
    seed.session(epoch, 7, lane=2, ended=False)
    for lane in (1, 2, 4):
        rig.aggregator.apply(bet(lane=lane, session_id=lane))
    rig.aggregator.tick()
    rig.agent.registry = "1"
    rig.clock.now += STALE_AFTER_S + 1
    rig.sync()
    assert set(rig.aggregator.lanes) == {1}
    tick = rig.aggregator.tick()
    assert tick.gone == [2, 4]
    assert set(rig.aggregator.snapshot()[0]) == {1}
    assert all(args.get("lane") in (None, 1) for _, args in rig.agent.calls)


@pytest.mark.parametrize("trouble", ["failing", "exit", "garbage"])
def test_no_lane_is_dropped_when_the_registry_cannot_be_read(
    rig: Rig, seed: Seed, trouble: str
) -> None:
    for lane in (1, 2):
        rig.aggregator.apply(bet(lane=lane, session_id=lane))
    if trouble == "failing":
        rig.agent.failing = True
    elif trouble == "garbage":
        rig.agent.registry = "lanes: 1"
    rig.clock.now += STALE_AFTER_S + 1
    rig.sync()
    assert set(rig.aggregator.lanes) == {1, 2}
    assert rig.aggregator.tick().gone == []


def test_a_lane_added_after_the_last_registry_read_is_kept(rig: Rig, seed: Seed) -> None:
    rig.agent.registry = "1"
    rig.aggregator.apply(bet(lane=1, session_id=1))
    rig.clock.now += STALE_AFTER_S + 1
    rig.sync()
    rig.agent.registry = "1 5"
    rig.aggregator.apply(bet(lane=5, session_id=1))
    rig.clock.now += 1
    rig.aggregator.apply(bet(lane=1, session_id=1))
    rig.clock.now += STALE_AFTER_S + 1
    rig.sync()
    assert set(rig.aggregator.lanes) == {1, 5}


def test_a_stopped_lane_without_a_current_session_forgets_its_old_session(
    rig: Rig, seed: Seed
) -> None:
    rig.aggregator.apply(bet(lane=1, session_id=10553))
    rig.agent.running[1] = False
    rig.agent.state[1] = None  # type: ignore[assignment]  # the reset removed the state file
    rig.clock.now += STALE_AFTER_S + 1
    rig.sync()
    assert rig.lane(1)["session_id"] is None


def test_an_enabled_lane_that_never_reported_shows_as_down(rig: Rig, seed: Seed) -> None:
    rig.agent.registry = "1 5 6"
    rig.agent.enabled = "1 5"
    rig.agent.running[5] = False
    rig.agent.state[5] = None  # type: ignore[assignment]  # the lane never wrote a state file
    rig.aggregator.apply(bet(lane=1, session_id=1))
    rig.sync()
    assert set(rig.aggregator.lanes) == {1, 5}
    assert rig.lane(5)["status"] == "down"


def test_the_registry_is_read_while_every_lane_bets(rig: Rig, seed: Seed) -> None:
    rig.agent.registry = "1 5"
    rig.agent.running[5] = False
    rig.aggregator.apply(bet(lane=1, session_id=1))
    rig.sync()
    assert 5 in rig.aggregator.lanes
