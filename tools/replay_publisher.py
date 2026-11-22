"""Record the WolfBet event streams to a file, and replay a file into Redis.

    record  read the bot's three streams (XRANGE) into JSON lines:
            {"stream": ..., "offset_ms": ..., "data": {...}}
    replay  send a file back with XADD, keeping the recorded spacing divided by --speed.

Replayed events look freshly sent: each pass gets a new pid (a new bot process to the
consumer), seq counts again from 0 per lane, `ts_ms` is the send time, and session ids are
shifted per pass so a looped file never reopens a finished session. Replay connects as the
`publisher` Redis user, which may only XADD to `wolfbet:*`.

    uv run --project backend python tools/replay_publisher.py \\
        --redis redis://publisher:<pw>@127.0.0.1:6379/0 replay \\
        --file tools/fixtures/wolfbet-events.jsonl --speed 10 --loop
"""

import argparse
import asyncio
import copy
import json
import os
import random
import sys
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from redis.asyncio import Redis

STREAMS = ("wolfbet:bets", "wolfbet:state", "wolfbet:lifecycle")


@dataclass(frozen=True)
class Recorded:
    stream: str
    offset_ms: int
    data: dict[str, Any]


def load(path: Path) -> list[Recorded]:
    """The file's events in send order; a line that is not a recorded event is an error."""
    records: list[Recorded] = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        item = json.loads(line)
        if item.get("stream") not in STREAMS or not isinstance(item.get("data"), dict):
            raise ValueError(f"{path}:{number}: not a recorded WolfBet event")
        records.append(Recorded(item["stream"], int(item["offset_ms"]), item["data"]))
    return sorted(records, key=lambda r: r.offset_ms)


def _session_ids(data: dict[str, Any]) -> list[dict[str, Any]]:
    """The mappings in one event that carry a `session_id`."""
    payload = data.get("payload")
    if not isinstance(payload, dict):
        return []
    found = [payload]
    inner = payload.get("data")
    if isinstance(inner, dict):
        found.append(inner)
    return [m for m in found if isinstance(m.get("session_id"), int)]


class Rewriter:
    """Makes each pass over a recording look like a new run of the bot."""

    def __init__(self, records: Iterable[Recorded], base_pid: int) -> None:
        ids = [m["session_id"] for r in records for m in _session_ids(r.data)]
        self.stride = max(ids, default=0) + 1
        self.base_pid = base_pid

    def rewrite(self, data: dict[str, Any], loop: int, seq: int, now_ms: int) -> dict[str, Any]:
        event = copy.deepcopy(data)
        event["pid"] = self.base_pid + loop
        event["seq"] = seq
        event["ts_ms"] = now_ms
        for mapping in _session_ids(event):
            mapping["session_id"] += loop * self.stride
        return event


async def replay(
    redis: Redis,
    records: list[Recorded],
    speed: float,
    loop: bool,
    *,
    base_pid: int | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    passes: int | None = None,
) -> int:
    """Send the recording, once or looping; returns how many events were sent."""
    if not records:
        return 0
    rewriter = Rewriter(records, base_pid or random.randint(10_000, 2_000_000))  # noqa: S311  # an id, not a secret
    sent = 0
    run = 0
    while passes is None or run < passes:
        start = clock()
        seqs: dict[int, int] = {}
        for record in records:
            wait = start + record.offset_ms / 1000 / speed - clock()
            if wait > 0:
                await sleep(wait)
            lane = int(record.data.get("lane", 0))
            seq = seqs.get(lane, 0)
            seqs[lane] = seq + 1
            event = rewriter.rewrite(record.data, run, seq, int(time.time() * 1000))
            await redis.xadd(record.stream, {"data": json.dumps(event)})
            sent += 1
        run += 1
        if not loop:
            break
    return sent


def _text(value: bytes | str) -> str:
    return value.decode() if isinstance(value, bytes) else value


async def record(redis: Redis, seconds: float) -> list[Recorded]:
    """Every entry the streams receive during the next `seconds`, with offsets from the first."""
    # Pinned to each stream's current last id: re-reading `$` would skip entries that land
    # between two reads on a stream that has not had one yet.
    last: dict[str, str] = {}
    for stream in STREAMS:
        newest: Any = await redis.xrevrange(stream, count=1)
        last[stream] = _text(newest[0][0]) if newest else "0-0"
    entries: list[tuple[int, str, dict[str, Any]]] = []
    deadline = time.monotonic() + seconds
    while (left := deadline - time.monotonic()) > 0:
        block = max(1, int(min(left, 1.0) * 1000))
        reply: Any = await redis.xread(last, block=block)  # type: ignore[arg-type]  # stream ids are str
        for stream, items in reply or []:
            name = _text(stream)
            for entry_id, fields in items:
                key = _text(entry_id)
                last[name] = key
                raw = fields.get(b"data") or fields.get("data")
                if raw is not None:
                    entries.append((int(key.split("-")[0]), name, json.loads(raw)))
    if not entries:
        return []
    first = min(ms for ms, _, _ in entries)
    ordered = sorted(entries, key=lambda entry: entry[:2])
    return [Recorded(name, ms - first, data) for ms, name, data in ordered]


def dump(records: Iterable[Recorded], path: Path) -> None:
    lines = (
        json.dumps({"stream": r.stream, "offset_ms": r.offset_ms, "data": r.data}) for r in records
    )
    path.write_text("\n".join(lines) + "\n")


async def _run(args: argparse.Namespace) -> int:
    redis = Redis.from_url(args.redis)
    try:
        if args.command == "record":
            records = await record(redis, args.seconds)
            dump(records, args.out)
            print(f"recorded {len(records)} events to {args.out}")
        else:
            sent = await replay(redis, load(args.file), args.speed, args.loop)
            print(f"sent {sent} events")
    finally:
        await redis.aclose()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--redis",
        default=os.environ.get("REPLAY_REDIS_URL"),
        help="redis:// URL (default: $REPLAY_REDIS_URL)",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    rec = commands.add_parser("record", help="record the live streams to a file")
    rec.add_argument("--out", type=Path, required=True)
    rec.add_argument("--seconds", type=float, default=120.0)
    rep = commands.add_parser("replay", help="send a recorded file")
    rep.add_argument("--file", type=Path, required=True)
    rep.add_argument("--speed", type=float, choices=(1.0, 10.0), default=1.0)
    rep.add_argument("--loop", action="store_true")
    args = parser.parse_args(argv)
    if not args.redis:
        parser.error("--redis or REPLAY_REDIS_URL is required")
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
