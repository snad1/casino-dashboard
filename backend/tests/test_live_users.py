"""A per-user bot is followed live per user: own model, own gaps, own topics, new users found."""

import asyncio
import json
import random
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from redis import Redis as SyncRedis
from redis.asyncio import Redis
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import Event
from app.adapters.wolfbet_tg import WolfBetTgAdapter
from app.adapters.wolfbet_tg.events import TgBetEvent
from app.auth.models import Role, Totp, UserRole
from app.history.models import DbEpoch
from app.live.aggregator import run_ticker
from app.live.consumer import Gap, GapTracker, ServiceConsumer
from app.live.users import UserAggregators, UserTicks
from app.settings import Settings
from app.ws.hub import Hub, parse_topic
from app.ws.routes import NOT_PERMITTED
from tests.conftest import Account, RedisServer, make_client, run_in_session
from tests.test_hub import Clock, Socket, _settle
from tests.test_ws_auth import _connect, _login, _ticket
from tests.tg_links import TgUsers, new_uid, token

pytest_plugins = ["tests.fake_agent", "tests.control_rig", "tests.tg_rig"]

FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures" / "wolfbet_tg"
SVC = "wolfbet-tg"


def _document(kind: str, uid: int, seq: int, **payload: Any) -> dict[str, Any]:
    document: dict[str, Any] = json.loads((FIXTURES / f"{kind}.json").read_text())
    document["seq"] = seq
    document["payload"].update(uid=uid, **payload)
    return document


def _bet(uid: int, seq: int, session_id: int = 1, balance: float = 100.0) -> TgBetEvent:
    document = _document("bet", uid, seq, session_id=session_id, balance_after=balance)
    return TgBetEvent.model_validate(document)


def _join(hub: Hub, socket: Socket, *names: str) -> None:
    client = hub.register(socket.send, socket.close)
    for name in names:
        topic = parse_topic(SVC, name)
        assert topic is not None, name
        assert hub.subscribe(client, topic)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("svc:wolfbet-tg:user:42", ("service", None, 42)),
        ("svc:wolfbet-tg:user:42:lane:1", ("lane", 1, 42)),
        ("svc:wolfbet-tg:user:42:session:3", ("session", 3, 42)),
        ("svc:wolfbet-tg", None),
        ("svc:wolfbet-tg:lane:1", None),
        ("svc:wolfbet-tg:user:42:system", None),
        ("svc:wolfbet-tg:user:042", None),
        ("svc:wolfbet-tg:user:0", None),
        ("svc:wolfbet-tg:user:x", None),
    ],
)
def test_per_user_topics_must_name_a_user(
    name: str, expected: tuple[str, int | None, int] | None
) -> None:
    topic = parse_topic(SVC, name)
    assert (None if topic is None else (topic.kind, topic.key, topic.user)) == expected


def test_daemon_topics_cannot_name_a_user() -> None:
    assert parse_topic("wolfbet", "svc:wolfbet:user:42") is None
    topic = parse_topic("wolfbet", "svc:wolfbet:lane:2")
    assert topic is not None and topic.user is None


async def test_two_users_sharing_pid_and_session_ids_stay_apart() -> None:
    users = UserAggregators(SVC)
    await users.accept(_bet(42, 0, session_id=1, balance=10.0))
    await users.accept(_bet(43, 0, session_id=1, balance=99.0))
    await users.accept(_bet(43, 1, session_id=1, balance=98.0))
    ticks = users.tick()
    assert set(ticks.by_user) == {42, 43}
    lanes_42, tape_42 = users.users[42].snapshot()
    lanes_43, tape_43 = users.users[43].snapshot()
    assert lanes_42[1]["balance"] == 10.0
    assert lanes_43[1]["balance"] == 98.0
    assert (len(tape_42), len(tape_43)) == (1, 2)
    assert users.tick().empty


def test_seq_per_user_gives_no_false_gap_and_a_real_one_names_its_user() -> None:
    tracker = GapTracker(SVC, grace_s=0.0)
    for uid, seq in ((42, 0), (43, 0), (42, 1), (43, 1), (42, 2)):
        tracker.see(_bet(uid, seq), now=1.0)
    assert tracker.overdue(now=5.0) == []
    tracker.see(_bet(43, 4), now=6.0)
    gaps = tracker.overdue(now=7.0)
    assert gaps == [Gap(SVC, 4242, 1, 2, 3, scope=43)]


async def test_a_gap_reaches_only_its_users_model() -> None:
    users = UserAggregators(SVC)
    await users.accept(_bet(42, 0))
    await users.accept(_bet(43, 0))
    await users.accept(Gap(SVC, 4242, 1, 1, 2, scope=43))
    assert users.users[42].lanes[1].gaps == 0
    assert users.users[43].lanes[1].gaps == 2


async def test_an_item_without_a_user_is_dropped() -> None:
    users = UserAggregators(SVC)
    await users.accept(Gap(SVC, 4242, 1, 1, 2))
    assert users.users == {}


async def test_hub_sends_each_user_only_their_own_snapshot_and_ticks() -> None:
    users = UserAggregators(SVC)
    hub = Hub(SVC, users, clock=Clock())
    await users.accept(_bet(42, 0, balance=10.0))
    await users.accept(_bet(43, 0, balance=99.0))
    await hub.publish(users.tick())
    alice, bob, nobody = Socket(), Socket(), Socket()
    _join(hub, alice, "svc:wolfbet-tg:user:42")
    _join(hub, bob, "svc:wolfbet-tg:user:43:lane:1")
    _join(hub, nobody, "svc:wolfbet-tg:user:44")
    await _settle()
    first = [json.loads(m) for m in (alice.sent[0], bob.sent[0], nobody.sent[0])]
    assert [m["op"] for m in first] == ["snapshot"] * 3
    assert first[0]["lanes"]["1"]["balance"] == 10.0
    assert first[1]["lanes"]["1"]["balance"] == 99.0
    assert first[2]["lanes"] == {} and first[2]["tape"] == []

    await users.accept(_bet(42, 1, balance=11.0))
    await hub.publish(users.tick())
    await _settle()
    assert alice.ops() == ["snapshot", "tick"]
    assert json.loads(alice.sent[1])["tape"][0]["balance"] == 11.0
    assert bob.ops() == ["snapshot"]
    assert nobody.ops() == ["snapshot"]


async def test_the_ticker_drives_per_user_ticks() -> None:
    users = UserAggregators(SVC)
    seen: list[UserTicks] = []

    async def emit(tick: UserTicks) -> None:
        seen.append(tick)

    stop = asyncio.Event()
    task = asyncio.create_task(run_ticker(users, emit, stop, hz=10))
    await users.accept(_bet(42, 0))
    async with asyncio.timeout(5):
        while not seen:  # noqa: ASYNC110  # waits on the ticker task
            await asyncio.sleep(0.01)
    stop.set()
    await task
    assert set(seen[0].by_user) == {42}


@pytest.fixture
async def publisher(redis_server: RedisServer) -> AsyncIterator[Redis]:
    client = Redis.from_url(redis_server.url("publisher", redis_server.publisher_password))
    yield client
    await client.aclose()


@pytest.fixture
async def dashboard(redis_server: RedisServer) -> AsyncIterator[Redis]:
    client = Redis.from_url(redis_server.url("dashboard", redis_server.dashboard_password))
    yield client
    await client.aclose()


async def test_a_user_found_later_is_read_from_the_start_of_their_stream(
    publisher: Redis, dashboard: Redis
) -> None:
    first = random.randint(10_000, 2_000_000_000)  # noqa: S311  # an id, not a secret
    later = first + 1
    adapter = WolfBetTgAdapter()
    known = [first]
    seen: list[Event | Gap] = []

    async def sink(item: Event | Gap) -> None:
        seen.append(item)

    async def discover() -> tuple[str, ...]:
        return adapter.streams_for(known)

    def published(uid: int) -> list[int]:
        return [i.seq for i in seen if isinstance(i, Event) and i.scope == uid]

    old = _document("bet", first, 0)
    await publisher.xadd(f"wolfbet-tg:{first}:bets", {"data": json.dumps(old)})
    consumer = ServiceConsumer(
        dashboard, adapter, sink, block_ms=50, discover=discover, discover_every_s=0.1
    )
    stop = asyncio.Event()
    task = asyncio.create_task(consumer.run(stop))
    try:
        async with asyncio.timeout(5):
            while not consumer.last_ids:  # noqa: ASYNC110  # waits on the consumer task
                await asyncio.sleep(0.02)
        for seq in (0, 1):
            document = _document("bet", later, seq)
            await publisher.xadd(f"wolfbet-tg:{later}:bets", {"data": json.dumps(document)})
        new = _document("bet", first, 1)
        await publisher.xadd(f"wolfbet-tg:{first}:bets", {"data": json.dumps(new)})
        known.append(later)
        async with asyncio.timeout(5):
            while published(later) != [0, 1] or published(first) != [1]:  # noqa: ASYNC110  # waits on the consumer task
                await asyncio.sleep(0.02)
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=5)
    assert published(first) == [1]
    assert [g for g in seen if isinstance(g, Gap)] == []


def test_the_api_follows_a_known_telegram_user_for_user_managers_only(
    app_settings: Settings, migrated: Settings, account: Account, redis_server: RedisServer
) -> None:
    uid = random.randint(10_000, 2_000_000_000)  # noqa: S311  # an id, not a secret

    async def known_user(session: AsyncSession) -> None:
        session.add(
            DbEpoch(
                service=SVC,
                source_path=f"/botdata/.wolfbot_tg/{uid}/wolfbet.db",
                inode=uid,
                first_started_at=datetime.now(UTC),
                tg_uid=uid,
            )
        )
        await session.commit()

    async def promote(session: AsyncSession) -> None:
        query = update(UserRole).where(UserRole.user_id == account.id).values(role=Role.ADMIN)
        await session.execute(query)
        session.add(Totp(user_id=account.id, secret_ciphertext=b"x", enabled_at=datetime.now(UTC)))
        await session.commit()

    async def forget_user(session: AsyncSession) -> None:
        await session.execute(delete(DbEpoch).where(DbEpoch.tg_uid == uid))
        await session.commit()

    run_in_session(migrated, known_user)
    topic = f"svc:wolfbet-tg:user:{uid}"
    try:
        with make_client(app_settings.model_copy(update={"live_service": SVC})) as client:
            _login(client, account.email)
            with _connect(client, _ticket(client)) as socket:
                socket.send_json({"op": "subscribe", "topic": topic})
                assert socket.receive_json() == {
                    "op": "error",
                    "topic": topic,
                    "detail": NOT_PERMITTED,
                }
                run_in_session(migrated, promote)
                socket.send_json({"op": "subscribe", "topic": topic})
                assert socket.receive_json()["op"] == "snapshot"
                publisher = SyncRedis.from_url(
                    redis_server.url("publisher", redis_server.publisher_password)
                )
                for seq in range(3):
                    document = _document("bet", uid, seq)
                    publisher.xadd(f"wolfbet-tg:{uid}:bets", {"data": json.dumps(document)})
                publisher.close()
                tick = socket.receive_json()
                assert tick["op"] == "tick"
                assert tick["topic"] == topic
                assert "1" in tick["lanes"]
    finally:
        run_in_session(migrated, forget_user)


def test_one_api_serves_the_daemon_and_the_telegram_users_on_one_socket(
    tg_settings: Settings, tg_users: TgUsers, redis_server: RedisServer
) -> None:
    uid = new_uid()
    tg_users.history(uid)
    both = tg_settings.model_copy(update={"live_service": "wolfbet", "live_user_service": SVC})
    mine = f"svc:wolfbet-tg:user:{uid}"
    with make_client(both) as client:
        assert client.post("/auth/telegram", json={"token": token(uid)}).status_code == 204
        with _connect(client, _ticket(client)) as socket:
            socket.send_json({"op": "subscribe", "topic": "svc:wolfbet"})
            assert socket.receive_json()["op"] == "error"
            socket.send_json({"op": "subscribe", "topic": mine})
            assert socket.receive_json()["op"] == "snapshot"
            publisher = SyncRedis.from_url(
                redis_server.url("publisher", redis_server.publisher_password)
            )
            for seq in range(2):
                document = _document("bet", uid, seq)
                publisher.xadd(f"wolfbet-tg:{uid}:bets", {"data": json.dumps(document)})
            publisher.close()
            tick = socket.receive_json()
            assert (tick["op"], tick["topic"]) == ("tick", mine)
