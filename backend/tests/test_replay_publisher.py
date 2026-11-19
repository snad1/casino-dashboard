"""The replay publisher: the e2e recording is valid bot output, and a replay looks like a
fresh bot run (new pid per pass, seq from 0, current time, shifted session ids)."""

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from redis.asyncio import Redis

from app.adapters.wolfbet import WolfBetAdapter
from tests.conftest import RedisServer

TOOLS = Path(__file__).resolve().parents[2] / "tools"
FIXTURE = TOOLS / "fixtures" / "wolfbet-events.jsonl"


def _load_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("replay_publisher", TOOLS / "replay_publisher.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["replay_publisher"] = module
    spec.loader.exec_module(module)
    return module


tool = _load_tool()


class FakeRedis:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    async def xadd(self, stream: str, fields: dict[str, str]) -> None:
        self.sent.append((stream, json.loads(fields["data"])))


def test_the_recording_is_valid_bot_output_with_a_stop_reason() -> None:
    adapter = WolfBetAdapter()
    records = tool.load(FIXTURE)
    events = [adapter.parse(json.dumps(r.data).encode()) for r in records]
    assert {e.lane for e in events} == {1, 2, 3}
    assert {r.stream for r in records} == set(adapter.streams)
    stops = [e for e in events if e.type == "lifecycle" and e.payload.kind == "stop"]
    assert [s.payload.data["reason"] for s in stops] == ["Max profit reached"]
    assert records == sorted(records, key=lambda r: r.offset_ms)


def test_each_pass_looks_like_a_new_bot_run() -> None:
    records = tool.load(FIXTURE)
    redis = FakeRedis()
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    sent = asyncio.run(
        tool.replay(
            redis, records, 10.0, True, base_pid=500, clock=lambda: 0.0, sleep=sleep, passes=2
        )
    )
    assert sent == 2 * len(records)
    first, second = redis.sent[: len(records)], redis.sent[len(records) :]
    assert {e["pid"] for _, e in first} == {500}
    assert {e["pid"] for _, e in second} == {501}
    lane_one = [e["seq"] for _, e in first if e["lane"] == 1]
    assert lane_one == list(range(len(lane_one)))
    stride = tool.Rewriter(records, 1).stride
    bets = [(a, b) for (_, a), (_, b) in zip(first, second, strict=True) if a["type"] == "bet"]
    assert all(b["payload"]["session_id"] == a["payload"]["session_id"] + stride for a, b in bets)
    assert max(waits) <= records[-1].offset_ms / 1000 / 10
    for _, event in redis.sent:
        WolfBetAdapter().parse(json.dumps(event).encode())


def test_unknown_lines_are_refused(tmp_path: Path) -> None:
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"stream": "other", "offset_ms": 0, "data": {}}\n')
    try:
        tool.load(bad)
    except ValueError as error:
        assert "not a recorded WolfBet event" in str(error)
    else:
        raise AssertionError("expected a refusal")


def test_record_then_replay_through_redis(redis_server: RedisServer, tmp_path: Path) -> None:
    records = tool.load(FIXTURE)[:30]
    publisher_url = redis_server.url("publisher", redis_server.publisher_password)
    reader_url = redis_server.url("dashboard", redis_server.dashboard_password)

    async def run() -> list[Any]:
        reader, writer = Redis.from_url(reader_url), Redis.from_url(publisher_url)
        try:
            recording = asyncio.create_task(tool.record(reader, 2.0))
            await asyncio.sleep(0.3)
            await tool.replay(writer, records, 10.0, False, base_pid=900)
            recorded: list[Any] = await recording
            return recorded
        finally:
            await reader.aclose()
            await writer.aclose()

    recorded = asyncio.run(run())
    assert len(recorded) == len(records)
    assert recorded[0].offset_ms == 0
    assert {r.data["pid"] for r in recorded} == {900}
    out = tmp_path / "round.jsonl"
    tool.dump(recorded, out)
    assert [r.stream for r in tool.load(out)] == [r.stream for r in recorded]
