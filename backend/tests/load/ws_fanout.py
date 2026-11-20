"""Load check for the live socket: N viewers on `svc:wolfbet` while the bot stream runs.

Signs in each account once, opens up to 5 sockets per account (the per-user cap), subscribes
every socket, publishes fake bets to Redis as the bot's `publisher` user, and samples the
api process's CPU and memory every few seconds. Run it against a running api, for example:

    uv run python -m tests.load.ws_fanout --api http://127.0.0.1:8010 \\
        --origin https://localhost --accounts accounts.txt --clients 100 \\
        --publisher redis://publisher:<pw>@127.0.0.1:6379/0 --api-pid <pid> --seconds 300

`accounts.txt` holds one `email password` pair per line. With `--bet-rate 0` it publishes
nothing (something else feeds the stream) and needs no `--publisher`; without `--api-pid` it
skips the CPU and memory sampling. It is not part of the test suite.
"""

import argparse
import asyncio
import json
import random
import statistics
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx2
from redis.asyncio import Redis
from websockets.asyncio.client import connect

SOCKETS_PER_ACCOUNT = 5
COOKIE = "__Host-sid"


@dataclass
class Stats:
    ticks: int = 0
    snapshots: int = 0
    errors: int = 0
    closed: int = 0
    samples: list[tuple[float, float]] = field(default_factory=list)


async def sign_in(api: str, origin: str, email: str, password: str) -> tuple[str, str]:
    """The session cookie and its CSRF token."""
    async with httpx2.AsyncClient(base_url=api, headers={"origin": origin}) as http:
        login = await http.post("/auth/login", json={"email": email, "password": password})
        login.raise_for_status()
        sid = login.cookies[COOKIE]
        me = await http.get("/auth/me", headers={"cookie": f"{COOKIE}={sid}"})
        me.raise_for_status()
        return sid, me.json()["csrf_token"]


async def ticket(api: str, origin: str, sid: str, csrf: str) -> str:
    headers = {"origin": origin, "cookie": f"{COOKIE}={sid}", "x-csrf-token": csrf}
    async with httpx2.AsyncClient(base_url=api) as http:
        response = await http.post("/ws-ticket", headers=headers)
        response.raise_for_status()
        value: str = response.json()["ticket"]
        return value


async def viewer(
    api: str, origin: str, sid: str, csrf: str, stats: Stats, stop: asyncio.Event
) -> None:
    url = api.replace("http", "ws", 1) + "/ws?ticket=" + await ticket(api, origin, sid, csrf)
    headers = {"origin": origin, "cookie": f"{COOKIE}={sid}"}
    async with connect(url, additional_headers=headers) as socket:
        await socket.send(json.dumps({"op": "subscribe", "topic": "svc:wolfbet"}))
        while not stop.is_set():
            try:
                raw = await asyncio.wait_for(socket.recv(), timeout=1)
            except TimeoutError:
                continue
            op = json.loads(raw)["op"]
            if op == "tick":
                stats.ticks += 1
            elif op == "snapshot":
                stats.snapshots += 1
            elif op == "error":
                stats.errors += 1
    stats.closed += 1


async def publish(redis_url: str, rate: float, stop: asyncio.Event) -> int:
    """Fake bot bets on three lanes at `rate` per second; returns how many were sent."""
    redis = Redis.from_url(redis_url)
    pid = random.randint(10_000, 2_000_000)  # noqa: S311  # an id, not a secret
    seq = 0
    try:
        while not stop.is_set():
            lane = seq % 3 + 1
            event = {
                "v": 1, "svc": "wolfbet", "lane": lane, "pid": pid + lane, "seq": seq // 3,
                "ts_ms": int(time.time() * 1000), "type": "bet",
                "payload": {
                    "session_id": 1, "amount": 1e-8, "multiplier": 2.0, "rule": "over",
                    "bet_value": 49.5, "result_value": 75.0, "state": "win", "profit": 1e-8,
                    "balance_after": 1.0, "session_profit": 1e-8 * seq, "total_bets": seq,
                    "current_streak": 1, "api_ms": 40.0,
                },
            }  # fmt: skip
            await redis.xadd("wolfbet:bets", {"data": json.dumps(event)})
            seq += 1
            await asyncio.sleep(1 / rate)
    finally:
        await redis.aclose()
    return seq


async def sample(pid: int, stats: Stats, stop: asyncio.Event, every: float = 5.0) -> None:
    """CPU % and resident memory (MiB) of the api process, from `ps`."""
    while not stop.is_set():
        output = await asyncio.to_thread(
            subprocess.run,
            ["ps", "-o", "%cpu=,rss=", "-p", str(pid)],  # ps from PATH on purpose
            capture_output=True,
            text=True,
            check=False,
        )
        cpu, rss = output.stdout.split()
        stats.samples.append((float(cpu), int(rss) / 1024))
        await asyncio.sleep(every)


def parse_args() -> tuple[argparse.Namespace, list[list[str]]]:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", required=True)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--accounts", required=True, type=Path)
    parser.add_argument("--clients", type=int, default=100)
    parser.add_argument("--publisher")
    parser.add_argument("--api-pid", type=int)
    parser.add_argument("--seconds", type=float, default=300)
    parser.add_argument("--bet-rate", type=float, default=90.0)
    args = parser.parse_args()
    if args.bet_rate > 0 and not args.publisher:
        parser.error("--publisher is needed unless --bet-rate is 0")
    lines = args.accounts.read_text().splitlines()
    return args, [line.split() for line in lines if line.strip()]


async def main(args: argparse.Namespace, accounts: list[list[str]]) -> None:
    needed = -(-args.clients // SOCKETS_PER_ACCOUNT)
    if needed > len(accounts):
        raise SystemExit(f"{args.clients} clients need {needed} accounts, got {len(accounts)}")
    sessions = [await sign_in(args.api, args.origin, *accounts[i]) for i in range(needed)]

    stats, stop = Stats(), asyncio.Event()
    viewers = [
        asyncio.create_task(
            viewer(args.api, args.origin, *sessions[i // SOCKETS_PER_ACCOUNT], stats, stop)
        )
        for i in range(args.clients)
    ]
    sampler = asyncio.create_task(sample(args.api_pid, stats, stop)) if args.api_pid else None
    publisher = (
        asyncio.create_task(publish(args.publisher, args.bet_rate, stop))
        if args.bet_rate > 0
        else None
    )
    await asyncio.sleep(args.seconds)
    stop.set()
    sent = await publisher if publisher is not None else 0
    await asyncio.gather(*viewers, *([sampler] if sampler else []), return_exceptions=True)

    cpu = [c for c, _ in stats.samples]
    rss = [r for _, r in stats.samples]
    print(
        json.dumps(
            {
                "clients": args.clients,
                "seconds": args.seconds,
                "bets_published": sent,
                "ticks_received": stats.ticks,
                "ticks_per_client_per_s": round(stats.ticks / args.clients / args.seconds, 2),
                "snapshots": stats.snapshots,
                "errors": stats.errors,
                "api_cpu_pct_mean": round(statistics.mean(cpu), 1) if cpu else None,
                "api_cpu_pct_max": max(cpu, default=None),
                "api_rss_mib_mean": round(statistics.mean(rss), 1) if rss else None,
                "api_rss_mib_max": round(max(rss), 1) if rss else None,
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main(*parse_args()))
