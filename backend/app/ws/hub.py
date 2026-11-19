"""Fan each live tick out to the sockets subscribed to it.

A tick is turned into JSON once per topic and the same string goes to every subscriber, so
the work per tick follows the number of topics, not the number of viewers. Each socket has a
bounded queue and its own writer task: a slow reader only ever fills its own queue. When it
is full, the queued messages are replaced by one fresh snapshot per topic; a socket that is
still behind after `BEHIND_LIMIT_S` is closed (1013, try again later).

Topics: `svc:<name>` (every lane), `svc:<name>:lane:<k>`, `svc:<name>:session:<id>` and
`svc:<name>:system` (system metrics) and `svc:<name>:comments` (a signal that a comment
changed, so open pages reload it; it has no snapshot).
"""

import asyncio
import contextlib
import re
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

import orjson
import structlog

from app.live.aggregator import Aggregator, Tick

QUEUE_SIZE = 64
MAX_TOPICS = 16
BEHIND_LIMIT_S = 10.0
HEARTBEAT_IDLE_S = 15.0
CLOSE_TOO_SLOW = 1013
_TOPIC = re.compile(r"svc:([a-z0-9-]+)(?::(lane|session):([0-9]{1,18})|:(system|comments))?")

log = structlog.get_logger("app.ws.hub")


@dataclass(frozen=True)
class Topic:
    name: str
    kind: str
    key: int | None = None


def parse_topic(svc: str, name: str) -> Topic | None:
    """The topic, or None when it is malformed or belongs to another service."""
    match = _TOPIC.fullmatch(name)
    if match is None or match.group(1) != svc:
        return None
    if match.group(4):
        return Topic(name, match.group(4))
    if match.group(2):
        return Topic(name, match.group(2), int(match.group(3)))
    return Topic(name, "service")


def encode(message: dict[str, Any]) -> str:
    return orjson.dumps(message, option=orjson.OPT_NON_STR_KEYS).decode()


HEARTBEAT = encode({"op": "hb"})


@dataclass(eq=False)
class Client:
    send: Callable[[str], Awaitable[None]]
    close: Callable[[int], Coroutine[Any, Any, None]]
    queue: asyncio.Queue[str] = field(default_factory=lambda: asyncio.Queue(QUEUE_SIZE))
    topics: dict[str, Topic] = field(default_factory=dict)
    behind_since: float | None = None
    last_sent: float = 0.0
    closing: bool = False
    writer: asyncio.Task[None] | None = None
    closer: asyncio.Task[None] | None = None


class Hub:
    def __init__(
        self, svc: str, aggregator: Aggregator, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.svc = svc
        self.aggregator = aggregator
        self.clock = clock
        self.clients: set[Client] = set()
        self.closed_slow = 0
        self._system: dict[str, Any] = {"agent": "pending"}

    def register(
        self,
        send: Callable[[str], Awaitable[None]],
        close: Callable[[int], Coroutine[Any, Any, None]],
    ) -> Client:
        client = Client(send, close, last_sent=self.clock())
        client.writer = asyncio.create_task(self._write(client))
        self.clients.add(client)
        return client

    async def unregister(self, client: Client) -> None:
        self.clients.discard(client)
        if client.writer is not None:
            client.writer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await client.writer

    async def _write(self, client: Client) -> None:
        while True:
            message = await client.queue.get()
            await client.send(message)
            client.last_sent = self.clock()
            if client.queue.empty():
                client.behind_since = None

    def subscribe(self, client: Client, topic: Topic) -> bool:
        """Start sending `topic`; False when the socket already follows `MAX_TOPICS`."""
        if topic.name not in client.topics and len(client.topics) >= MAX_TOPICS:
            return False
        client.topics[topic.name] = topic
        snapshot = self._snapshot(topic)
        if snapshot is not None:
            self._offer(client, snapshot)
        return True

    def send(self, client: Client, message: dict[str, Any]) -> None:
        """Queue a message for one socket, behind whatever it already has queued."""
        self._offer(client, encode(message))

    def unsubscribe(self, client: Client, name: str) -> None:
        client.topics.pop(name, None)

    @staticmethod
    def _lanes_for(topic: Topic, lanes: dict[int, dict[str, Any]]) -> set[int]:
        if topic.kind == "service":
            return set(lanes)
        if topic.kind == "lane":
            return {topic.key} if topic.key in lanes else set()
        if topic.kind == "session":
            return {n for n, fields in lanes.items() if fields.get("session_id") == topic.key}
        return set()

    def _snapshot(self, topic: Topic) -> str | None:
        if topic.kind == "comments":
            return None
        if topic.kind == "system":
            return encode({"op": "system", "topic": topic.name, **self._system})
        lanes, tape = self.aggregator.snapshot()
        keep = self._lanes_for(topic, lanes)
        return encode(
            {
                "op": "snapshot",
                "topic": topic.name,
                "lanes": {n: f for n, f in lanes.items() if n in keep},
                "tape": [row for row in tape if row["lane"] in keep],
            }
        )

    def _tick_message(
        self, topic: Topic, tick: Tick, current: dict[int, dict[str, Any]]
    ) -> str | None:
        keep = self._lanes_for(topic, current)
        lanes = {n: f for n, f in tick.snapshot_diff.items() if n in keep}
        tape = [row for row in tick.tape_new if row["lane"] in keep]
        candles = [c for c in tick.candles_new if c["lane"] in keep]
        if not (lanes or tape or candles):
            return None
        body = {"op": "tick", "topic": topic.name, "lanes": lanes, "tape": tape, "candles": candles}
        return encode(body)

    async def publish(self, tick: Tick) -> None:
        """Serialise the tick once per subscribed topic and queue it for each subscriber."""
        by_topic: dict[str, list[Client]] = {}
        topics: dict[str, Topic] = {}
        for client in self.clients:
            for name, topic in client.topics.items():
                by_topic.setdefault(name, []).append(client)
                topics[name] = topic
        current = self.aggregator.snapshot()[0]
        for name, subscribers in by_topic.items():
            message = self._tick_message(topics[name], tick, current)
            if message is None:
                continue
            for client in subscribers:
                self._offer(client, message)

    def publish_system(self, body: dict[str, Any]) -> None:
        """Keep the latest system sample and send it to every system subscriber, encoded once."""
        self._system = body
        encoded: dict[str, str] = {}
        for client in self.clients:
            for name, topic in client.topics.items():
                if topic.kind != "system":
                    continue
                if name not in encoded:
                    encoded[name] = encode({"op": "system", "topic": name, **body})
                self._offer(client, encoded[name])

    def publish_comment(self, target: dict[str, Any]) -> None:
        """Tell every comments subscriber that a comment on `target` changed, encoded once."""
        encoded: dict[str, str] = {}
        for client in self.clients:
            for name, topic in client.topics.items():
                if topic.kind != "comments":
                    continue
                if name not in encoded:
                    encoded[name] = encode({"op": "comment", "topic": name, **target})
                self._offer(client, encoded[name])

    def _offer(self, client: Client, message: str) -> None:
        if client.closing:
            return
        try:
            client.queue.put_nowait(message)
            return
        except asyncio.QueueFull:
            pass
        now = self.clock()
        if client.behind_since is None:
            client.behind_since = now
        elif now - client.behind_since >= BEHIND_LIMIT_S:
            client.closing = True
            self.closed_slow += 1
            log.warning("closing a websocket that stayed behind", topics=sorted(client.topics))
            client.closer = asyncio.get_running_loop().create_task(client.close(CLOSE_TOO_SLOW))
            return
        while not client.queue.empty():
            client.queue.get_nowait()
        for topic in client.topics.values():
            snapshot = self._snapshot(topic)
            if snapshot is not None:
                client.queue.put_nowait(snapshot)

    def heartbeat(self) -> None:
        now = self.clock()
        for client in self.clients:
            if client.queue.empty() and now - client.last_sent >= HEARTBEAT_IDLE_S:
                self._offer(client, HEARTBEAT)
                client.last_sent = now

    async def run_heartbeat(self, stop: asyncio.Event, every_s: float = 5.0) -> None:
        while not stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=every_s)
            self.heartbeat()
