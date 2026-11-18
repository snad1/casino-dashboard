"""The consumer reads the bot's streams as the dashboard Redis user and reports lost events."""

import asyncio
import json
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from app.adapters.base import Event
from app.adapters.wolfbet import WolfBetAdapter
from app.adapters.wolfbet.events import BetEvent, LifecycleEvent, StateEvent
from app.live.consumer import Gap, GapTracker, ServiceConsumer
from tests.conftest import RedisServer

FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures" / "wolfbet"
STREAM_OF = {"bet": "wolfbet:bets", "state": "wolfbet:state", "lifecycle": "wolfbet:lifecycle"}


def _event(kind: str, pid: int, seq: int, lane: int = 1) -> dict[str, Any]:
    document: dict[str, Any] = json.loads((FIXTURES / f"{kind}.json").read_text())
    document.update(pid=pid, seq=seq, lane=lane)
    return document


@pytest.fixture
async def publisher(redis_server: RedisServer) -> AsyncIterator[Redis]:
    """Writes as the bot's own ACL user."""
    client = Redis.from_url(redis_server.url("publisher", redis_server.publisher_password))
    yield client
    await client.aclose()


@pytest.fixture
async def dashboard(redis_server: RedisServer) -> AsyncIterator[Redis]:
    client = Redis.from_url(redis_server.url("dashboard", redis_server.dashboard_password))
    yield client
    await client.aclose()


async def _publish(publisher: Redis, *documents: dict[str, Any]) -> None:
    for document in documents:
        await publisher.xadd(STREAM_OF[document["type"]], {"data": json.dumps(document)})


class Collector:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.items: list[Event | Gap] = []

    async def __call__(self, item: Event | Gap) -> None:
        if item.pid == self.pid:
            self.items.append(item)

    def seqs(self) -> list[int]:
        return [i.seq for i in self.items if isinstance(i, Event)]

    def gaps(self) -> list[Gap]:
        return [i for i in self.items if isinstance(i, Gap)]


async def _until(check: Callable[[], bool]) -> None:
    async with asyncio.timeout(5):
        while not check():  # noqa: ASYNC110  # waits on state the consumer task changes
            await asyncio.sleep(0.02)


async def _running(consumer: ServiceConsumer, body: Callable[[], Awaitable[None]]) -> None:
    stop = asyncio.Event()
    task = asyncio.create_task(consumer.run(stop))
    try:
        await _until(lambda: bool(consumer.last_ids) or consumer.stats["reconnects"] > 0)
        await body()
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=5)


def _pid() -> int:
    return random.randint(10_000, 2_000_000)  # noqa: S311  # an id, not a secret


async def test_events_from_all_streams_arrive_typed_and_in_order(
    publisher: Redis, dashboard: Redis
) -> None:
    pid = _pid()
    sink = Collector(pid)
    consumer = ServiceConsumer(dashboard, WolfBetAdapter(), sink, block_ms=100)

    async def body() -> None:
        await _publish(
            publisher,
            _event("lifecycle", pid, 0),
            _event("bet", pid, 1),
            _event("bet", pid, 2),
            _event("state", pid, 3),
        )
        await _until(lambda: len(sink.seqs()) == 4)

    await _running(consumer, body)
    kinds = sorted(type(item).__name__ for item in sink.items)
    assert kinds == sorted(
        [LifecycleEvent.__name__, BetEvent.__name__, BetEvent.__name__, StateEvent.__name__]
    )
    assert sink.gaps() == []


async def test_malformed_entries_are_counted_and_skipped(
    publisher: Redis, dashboard: Redis
) -> None:
    pid = _pid()
    sink = Collector(pid)
    consumer = ServiceConsumer(dashboard, WolfBetAdapter(), sink, block_ms=100)

    async def body() -> None:
        await publisher.xadd("wolfbet:bets", {"data": "{broken"})
        future_version = {**_event("bet", pid, 0), "v": 9}
        await publisher.xadd("wolfbet:bets", {"data": json.dumps(future_version)})
        await publisher.xadd("wolfbet:bets", {"other": "field"})
        await _publish(publisher, _event("bet", pid, 1))
        await _until(lambda: sink.seqs() == [1])

    await _running(consumer, body)
    assert consumer.stats["dropped:json"] >= 1
    assert consumer.stats["dropped:version"] >= 1
    assert consumer.stats["dropped:shape"] >= 1


async def test_a_lost_event_becomes_one_gap(publisher: Redis, dashboard: Redis) -> None:
    pid = _pid()
    sink = Collector(pid)
    consumer = ServiceConsumer(dashboard, WolfBetAdapter(), sink, block_ms=50, grace_s=0.2)

    async def body() -> None:
        await _publish(publisher, _event("bet", pid, 0), _event("bet", pid, 1))
        await _publish(publisher, _event("state", pid, 3), _event("bet", pid, 4))
        await _until(lambda: len(sink.gaps()) == 1)
        await asyncio.sleep(0.3)

    await _running(consumer, body)
    assert sink.gaps() == [Gap("wolfbet", pid, 1, 2, 2)]
    assert consumer.stats["gaps"] >= 1


async def test_flaky_redis_is_retried_without_losing_entries(
    publisher: Redis, dashboard: Redis
) -> None:
    pid = _pid()
    sink = Collector(pid)

    class Flaky:
        """The dashboard client, but the first reads fail like a Redis restart would."""

        def __init__(self, inner: Redis, failures: int) -> None:
            self.inner = inner
            self.failures = failures

        async def xread(self, *args: Any, **kwargs: Any) -> Any:
            if self.failures:
                self.failures -= 1
                raise RedisConnectionError("restarting")
            return await self.inner.xread(*args, **kwargs)

        async def xrevrange(self, *args: Any, **kwargs: Any) -> Any:
            return await self.inner.xrevrange(*args, **kwargs)

    flaky = Flaky(dashboard, failures=3)
    consumer = ServiceConsumer(
        flaky,  # type: ignore[arg-type]
        WolfBetAdapter(),
        sink,
        block_ms=50,
        backoff_min_s=0.01,
        backoff_max_s=0.05,
    )

    async def body() -> None:
        await _publish(publisher, _event("bet", pid, 0), _event("bet", pid, 1))
        await _until(lambda: sink.seqs() == [0, 1])

    await _running(consumer, body)
    assert consumer.stats["reconnects"] == 3


class TestGapTracker:
    def _event(self, pid: int, seq: int, lane: int = 1) -> Event:
        return Event(lane=lane, pid=pid, seq=seq, ts_ms=0)

    def test_reordering_inside_the_grace_period_is_not_a_gap(self) -> None:
        tracker = GapTracker("wolfbet", grace_s=2.0)
        for seq, now in [(5, 0.0), (7, 0.1), (6, 0.5), (8, 0.6)]:
            tracker.see(self._event(1, seq), now)
        assert tracker.overdue(10.0) == []

    def test_a_hole_is_reported_once_after_the_grace_period(self) -> None:
        tracker = GapTracker("wolfbet", grace_s=2.0)
        for seq in (0, 1, 4, 5):
            tracker.see(self._event(1, seq), 0.0)
        assert tracker.overdue(1.0) == []
        assert tracker.overdue(2.5) == [Gap("wolfbet", 1, 1, 2, 3)]
        assert tracker.overdue(5.0) == []
        tracker.see(self._event(1, 3), 5.1)
        assert tracker.late == 1

    def test_lanes_and_new_processes_are_tracked_apart(self) -> None:
        tracker = GapTracker("wolfbet", grace_s=0.0)
        tracker.see(self._event(1, 10, lane=1), 0.0)
        tracker.see(self._event(1, 11, lane=1), 0.0)
        tracker.see(self._event(2, 0, lane=1), 0.0)
        tracker.see(self._event(1, 500, lane=2), 0.0)
        assert tracker.overdue(1.0) == []
