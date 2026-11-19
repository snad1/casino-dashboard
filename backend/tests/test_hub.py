"""The hub serialises once per topic, never lets a slow socket hold back others, and the
socket delivers snapshot-then-ticks for allowed topics only."""

import asyncio
import json
import random
from typing import Any

import pytest
from fastapi.testclient import TestClient
from redis import Redis

from app.live.aggregator import Aggregator
from app.settings import Settings
from app.ws.hub import (
    BEHIND_LIMIT_S,
    CLOSE_TOO_SLOW,
    HEARTBEAT_IDLE_S,
    QUEUE_SIZE,
    Client,
    Hub,
    parse_topic,
)
from app.ws.routes import NOT_PERMITTED, UNKNOWN_MESSAGE
from tests.conftest import Account, RedisServer, make_client
from tests.test_aggregator import bet
from tests.test_ws_auth import _connect, _login, _ticket


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Socket:
    """A fake socket; `stuck` ones never finish a send, like a reader that stopped."""

    def __init__(self, stuck: bool = False) -> None:
        self.stuck = stuck
        self.sent: list[str] = []
        self.closed_with: int | None = None

    async def send(self, message: str) -> None:
        if self.stuck:
            await asyncio.Event().wait()
        self.sent.append(message)

    async def close(self, code: int) -> None:
        self.closed_with = code

    def ops(self) -> list[str]:
        return [json.loads(m)["op"] for m in self.sent]


def _hub(clock: Clock | None = None) -> tuple[Hub, Aggregator]:
    aggregator = Aggregator("wolfbet", clock=clock or Clock())
    return Hub("wolfbet", aggregator, clock=clock or Clock()), aggregator


def _join(hub: Hub, socket: Socket, *topics: str) -> Client:
    client = hub.register(socket.send, socket.close)
    for name in topics:
        topic = parse_topic("wolfbet", name)
        assert topic is not None
        assert hub.subscribe(client, topic)
    return client


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("svc:wolfbet", ("service", None)),
        ("svc:wolfbet:lane:2", ("lane", 2)),
        ("svc:wolfbet:session:168", ("session", 168)),
        ("svc:wolfbet:system", ("system", None)),
        ("svc:goated", None),
        ("svc:wolfbet:lane:x", None),
        ("wolfbet", None),
    ],
)
def test_topics(name: str, expected: tuple[str, int | None] | None) -> None:
    topic = parse_topic("wolfbet", name)
    assert (None if topic is None else (topic.kind, topic.key)) == expected


async def test_subscribe_starts_with_a_snapshot_then_ticks_share_one_string() -> None:
    hub, aggregator = _hub()
    a, b = Socket(), Socket()
    _join(hub, a, "svc:wolfbet")
    _join(hub, b, "svc:wolfbet")
    await _settle()
    assert a.ops() == b.ops() == ["snapshot"]
    aggregator.apply(bet(seq=0))
    await hub.publish(aggregator.tick())
    await _settle()
    assert a.ops() == ["snapshot", "tick"]
    assert a.sent[-1] is b.sent[-1]
    tick = json.loads(a.sent[-1])
    assert tick["lanes"]["1"]["total_bets"] == 1
    assert [row["seq"] for row in tick["tape"]] == [0]


async def test_lane_and_session_topics_only_carry_their_lane() -> None:
    hub, aggregator = _hub()
    aggregator.apply(bet(lane=1, seq=0, session_id=11))
    aggregator.apply(bet(lane=2, seq=0, session_id=22))
    aggregator.tick()
    lane2, session11 = Socket(), Socket()
    _join(hub, lane2, "svc:wolfbet:lane:2")
    _join(hub, session11, "svc:wolfbet:session:11")
    aggregator.apply(bet(lane=1, seq=1, session_id=11))
    aggregator.apply(bet(lane=2, seq=1, session_id=22))
    await hub.publish(aggregator.tick())
    await _settle()
    assert list(json.loads(lane2.sent[0])["lanes"]) == ["2"]
    assert list(json.loads(lane2.sent[1])["lanes"]) == ["2"]
    assert list(json.loads(session11.sent[1])["lanes"]) == ["1"]


async def test_a_stuck_socket_never_delays_the_others_and_is_coalesced() -> None:
    hub, aggregator = _hub()
    fast, stuck = Socket(), Socket(stuck=True)
    _join(hub, fast, "svc:wolfbet")
    slow_client = _join(hub, stuck, "svc:wolfbet")
    for seq in range(QUEUE_SIZE + 10):
        aggregator.apply(bet(seq=seq))
        await hub.publish(aggregator.tick())
        await _settle()
    assert fast.ops().count("tick") == QUEUE_SIZE + 10
    assert slow_client.queue.qsize() <= QUEUE_SIZE
    queued = [json.loads(slow_client.queue.get_nowait())["op"] for _ in range(2)]
    assert queued[0] == "snapshot"


async def test_a_socket_still_behind_after_10_s_is_closed() -> None:
    clock = Clock()
    hub, aggregator = _hub(clock)
    stuck = Socket(stuck=True)
    _join(hub, stuck, "svc:wolfbet")
    for seq in range(QUEUE_SIZE + 1):
        aggregator.apply(bet(seq=seq))
        await hub.publish(aggregator.tick())
    assert stuck.closed_with is None
    clock.now += BEHIND_LIMIT_S
    for seq in range(QUEUE_SIZE + 1):
        aggregator.apply(bet(seq=1000 + seq))
        await hub.publish(aggregator.tick())
    await _settle()
    assert stuck.closed_with == CLOSE_TOO_SLOW
    assert hub.closed_slow == 1


async def test_idle_sockets_get_a_heartbeat() -> None:
    clock = Clock()
    hub, _ = _hub(clock)
    socket = Socket()
    _join(hub, socket, "svc:wolfbet")
    await _settle()
    hub.heartbeat()
    await _settle()
    assert socket.ops() == ["snapshot"]
    clock.now += HEARTBEAT_IDLE_S
    hub.heartbeat()
    await _settle()
    assert socket.ops() == ["snapshot", "hb"]


@pytest.fixture
def live_client(app_settings: Settings, account: Account) -> Any:
    live = app_settings.model_copy(update={"live_service": "wolfbet"})
    with make_client(live) as client:
        _login(client, account.email)
        yield client


def _publish_bets(redis_server: RedisServer, count: int) -> None:
    publisher = Redis.from_url(redis_server.url("publisher", redis_server.publisher_password))
    pid = random.randint(10_000, 2_000_000)  # noqa: S311  # an id, not a secret
    for seq in range(count):
        event = bet(lane=3, seq=seq).model_dump(mode="json")
        event["pid"] = pid
        publisher.xadd("wolfbet:bets", {"data": json.dumps(event)})
    publisher.close()


def test_socket_gets_snapshot_then_live_ticks_from_the_bot_stream(
    live_client: TestClient, redis_server: RedisServer
) -> None:
    with _connect(live_client, _ticket(live_client)) as socket:
        socket.send_json({"op": "subscribe", "topic": "svc:wolfbet"})
        assert socket.receive_json()["op"] == "snapshot"
        _publish_bets(redis_server, 5)
        tick = socket.receive_json()
        assert tick["op"] == "tick"
        assert "3" in tick["lanes"]


def test_forbidden_and_malformed_requests_get_an_error_and_the_socket_stays_open(
    live_client: TestClient,
) -> None:
    with _connect(live_client, _ticket(live_client)) as socket:
        socket.send_json({"op": "subscribe", "topic": "svc:goated"})
        assert socket.receive_json() == {
            "op": "error",
            "topic": "svc:goated",
            "detail": NOT_PERMITTED,
        }
        socket.send_text("not json")
        assert socket.receive_json() == {"op": "error", "detail": UNKNOWN_MESSAGE}
        socket.send_json({"op": "subscribe", "topic": "svc:wolfbet:lane:1"})
        assert socket.receive_json()["op"] == "snapshot"
