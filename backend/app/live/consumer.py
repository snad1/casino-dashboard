"""Read a service's event streams and hand typed events, and lost-event gaps, to a sink.

One task per service reads all of its streams in one `XREAD`, because the bot numbers its
events per process across every stream: a gap can only be judged with the three streams
merged. The bot sends batches without a transaction, so a read can see a later event before
an earlier one from another stream; a missing seq counts as lost only after `GAP_GRACE_S`.

A bot that counts per user (the event's `scope`) has three streams per user. The `discover`
callback names them and is asked again every `DISCOVER_EVERY_S`; a stream found after the
start is read from its beginning, so a new user's first events are not skipped.
"""

import asyncio
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import cast

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError
from redis.typing import KeyT, StreamIdT

from app.adapters.base import Event, InvalidEvent, ServiceAdapter

GAP_GRACE_S = 2.0
BACKOFF_MIN_S = 0.5
BACKOFF_MAX_S = 30.0
DISCOVER_EVERY_S = 60.0

log = structlog.get_logger("app.live.consumer")
# The client speaks RESP2 (redis-py's default): an entry is (id, {field: value}) in bytes.
_Entry = tuple[bytes, dict[bytes, bytes]]


@dataclass(frozen=True)
class Gap:
    """Events `first`..`last` of this process never arrived."""

    svc: str
    pid: int
    lane: int
    first: int
    last: int
    scope: int | None = None


@dataclass
class _Lane:
    expected: int
    early: set[int] = field(default_factory=set)
    hole_since: float | None = None


class GapTracker:
    """Per (pid, lane, scope) seq bookkeeping; a new pid is a restarted process and starts fresh."""

    def __init__(self, svc: str, grace_s: float = GAP_GRACE_S) -> None:
        self.svc = svc
        self.grace_s = grace_s
        self._lanes: dict[tuple[int, int, int | None], _Lane] = {}
        self.late = 0

    def see(self, event: Event, now: float) -> None:
        key = (event.pid, event.lane, event.scope)
        lane = self._lanes.get(key)
        if lane is None:
            self._lanes[key] = _Lane(expected=event.seq + 1)
            return
        if event.seq < lane.expected or event.seq in lane.early:
            self.late += 1
            return
        if event.seq > lane.expected:
            lane.early.add(event.seq)
            if lane.hole_since is None:
                lane.hole_since = now
            return
        lane.expected += 1
        self._drain(lane, now)

    def _drain(self, lane: _Lane, now: float) -> None:
        while lane.expected in lane.early:
            lane.early.remove(lane.expected)
            lane.expected += 1
        lane.hole_since = now if lane.early else None

    def overdue(self, now: float) -> list[Gap]:
        """Holes still open after the grace period, each reported once."""
        gaps = []
        for (pid, lane_no, scope), lane in self._lanes.items():
            if lane.hole_since is None or now - lane.hole_since < self.grace_s:
                continue
            resume = min(lane.early)
            gaps.append(Gap(self.svc, pid, lane_no, lane.expected, resume - 1, scope))
            lane.expected = resume
            self._drain(lane, now)
        return gaps


Sink = Callable[[Event | Gap], Awaitable[None]]
Discover = Callable[[], Awaitable[Iterable[str]]]


class ServiceConsumer:
    """Follows a service's streams from `start_id` (default: only new entries)."""

    def __init__(
        self,
        redis: Redis,
        adapter: ServiceAdapter,
        sink: Sink,
        *,
        start_id: str = "$",
        block_ms: int = 1000,
        count: int = 1000,
        grace_s: float = GAP_GRACE_S,
        backoff_min_s: float = BACKOFF_MIN_S,
        backoff_max_s: float = BACKOFF_MAX_S,
        discover: Discover | None = None,
        discover_every_s: float = DISCOVER_EVERY_S,
    ) -> None:
        self.redis = redis
        self.adapter = adapter
        self.sink = sink
        self.block_ms = block_ms
        self.count = count
        self.backoff_min_s = backoff_min_s
        self.backoff_max_s = backoff_max_s
        self.start_id = start_id
        self.last_ids: dict[str, str] = {}
        self.gaps = GapTracker(adapter.name, grace_s)
        self.stats: Counter[str] = Counter()
        self.last_event_ms: float | None = None
        self.lag_ms: float | None = None
        self.discover = discover
        self.discover_every_s = discover_every_s
        self._discovered_at = 0.0
        self._started = False

    async def _resolve_start(self) -> None:
        """Pin `$` to each stream's current last id, so no stream is ever read from `$` twice;
        re-reading `$` would skip entries that land between two reads."""
        resolved: dict[str, str] = {}
        for stream in (*self.adapter.streams, *await self._discovered()):
            if self.start_id != "$":
                resolved[stream] = self.start_id
                continue
            newest = cast(list[_Entry], await self.redis.xrevrange(stream, count=1))
            resolved[stream] = newest[0][0].decode() if newest else "0-0"
        self.last_ids = resolved
        self._started = True

    async def _discovered(self) -> list[str]:
        """The streams `discover` names now; none when it fails, so the next round asks again."""
        if self.discover is None:
            return []
        self._discovered_at = time.monotonic()
        try:
            return sorted(set(await self.discover()))
        except Exception:
            self.stats["discover_failed"] += 1
            log.exception("stream discovery failed", svc=self.adapter.name)
            return []

    async def _add_new_streams(self) -> None:
        for stream in await self._discovered():
            self.last_ids.setdefault(stream, "0-0")

    async def _read_once(self) -> None:
        if not self._started:
            await self._resolve_start()
        elif (
            self.discover is not None
            and time.monotonic() - self._discovered_at >= self.discover_every_s
        ):
            await self._add_new_streams()
        if not self.last_ids:
            await asyncio.sleep(self.block_ms / 1000)
            return
        streams: dict[KeyT, StreamIdT] = {key: value for key, value in self.last_ids.items()}
        reply = cast(
            list[tuple[bytes, list[_Entry]]] | None,
            await self.redis.xread(streams, count=self.count, block=self.block_ms),
        )
        for stream, entries in reply or []:
            name = stream.decode()
            for entry_id, fields in entries:
                self.last_ids[name] = entry_id.decode()
                await self._handle(fields.get(b"data"))
        for gap in self.gaps.overdue(time.monotonic()):
            self.stats["gaps"] += 1
            await self.sink(gap)

    async def _handle(self, raw: bytes | None) -> None:
        if raw is None:
            self.stats["dropped:shape"] += 1
            return
        try:
            event = self.adapter.parse(raw)
        except InvalidEvent as invalid:
            self.stats[f"dropped:{invalid.reason}"] += 1
            return
        self.stats["events"] += 1
        self.last_event_ms = time.time() * 1000
        self.lag_ms = max(0.0, self.last_event_ms - event.ts_ms)
        self.gaps.see(event, time.monotonic())
        self.stats["late"] = self.gaps.late
        await self.sink(event)

    async def run(self, stop: asyncio.Event) -> None:
        """Read until `stop` is set; a Redis outage is retried with growing waits."""
        backoff = 0.0
        while not stop.is_set():
            try:
                await self._read_once()
            except RedisError as error:
                backoff = min(max(backoff * 2, self.backoff_min_s), self.backoff_max_s)
                self.stats["reconnects"] += 1
                if backoff == self.backoff_min_s:
                    reason = type(error).__name__
                    log.warning("event stream unreachable", svc=self.adapter.name, error=reason)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=backoff)
                except TimeoutError:
                    pass
                continue
            if backoff:
                log.info("event stream back", svc=self.adapter.name)
                backoff = 0.0
