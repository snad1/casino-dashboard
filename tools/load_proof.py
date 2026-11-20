"""Measure what the dashboard costs the bot: bet gaps and WAL size, off against on.

    sample  watch the bot database for --minutes: its WAL size every 10 s, then the gaps
            between consecutive bets of the same session in that window, gaps of 1 s or
            more left out (recurring pauses), as in the publisher bench guard
    report  compare two samples: gap p50/p90/p99, in-cycle bets per second, WAL max and
            last, a bootstrap 95 % interval on the median-gap change, and the gate (the
            median gap may grow by at most 2 %)

The database is opened read-only and only between bets' worth of reads; the bot is never
touched. Standard library only, so it runs with the host's python3.

    python3 tools/load_proof.py sample --label off --minutes 60
    python3 tools/load_proof.py report results/off.json results/on.json
"""

import argparse
import json
import os
import random
import sqlite3
import statistics
import sys
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

PAUSE_S = 1.0
GATE = 0.02
WAL_EVERY_S = 10.0
RESAMPLES = 2000
DEFAULT_DB = Path.home() / ".wolfbet_autobot.db"
BASELINE = "| baseline: live bot, events off (2026-11-18) | 38.1 | 46.6 | 136.4 | 24.29 | | |"


def bet_gaps(db: Path, start: datetime, end: datetime) -> list[float]:
    """Milliseconds between consecutive bets of one session placed in [start, end)."""
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        rows = conn.execute(
            "SELECT session_id, timestamp FROM bets WHERE timestamp >= ? AND timestamp < ? "
            "ORDER BY session_id, id",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    gaps: list[float] = []
    previous: tuple[Any, datetime] | None = None
    for session_id, stamp in rows:
        at = datetime.fromisoformat(stamp)
        if previous is not None and previous[0] == session_id:
            gap = (at - previous[1]).total_seconds()
            if 0 <= gap < PAUSE_S:
                gaps.append(gap * 1000)
        previous = (session_id, at)
    return gaps


def wal_size(db: Path) -> int:
    wal = Path(f"{db}-wal")
    return wal.stat().st_size if wal.exists() else 0


def _percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))]


def summarize(
    label: str, gaps: list[float], wal: list[int], start: datetime, end: datetime
) -> dict[str, Any]:
    if not gaps:
        raise SystemExit(f"{label}: no bets in the window; is the bot betting?")
    mean = statistics.fmean(gaps)
    return {
        "label": label,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "n": len(gaps),
        "p50_ms": round(_percentile(gaps, 0.5), 1),
        "p90_ms": round(_percentile(gaps, 0.9), 1),
        "p99_ms": round(_percentile(gaps, 0.99), 1),
        "bps": round(1000 / mean, 2),
        "wal_max": max(wal, default=0),
        "wal_last": wal[-1] if wal else 0,
        "gaps": [round(g, 3) for g in gaps],
    }


def sample(
    db: Path,
    label: str,
    minutes: float,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = datetime.now,
) -> dict[str, Any]:
    start = now()
    wal = [wal_size(db)]
    left = minutes * 60
    while left > 0:
        step = min(WAL_EVERY_S, left)
        sleep(step)
        left -= step
        wal.append(wal_size(db))
    end = now()
    return summarize(label, bet_gaps(db, start, end), wal, start, end)


def compare(off: dict[str, Any], on: dict[str, Any], seed: int = 7) -> dict[str, Any]:
    """Median-gap change from off to on, with a bootstrap 95 % interval, and the gate."""
    rng = random.Random(seed)  # noqa: S311  # resampling, not a secret
    a, b = off["gaps"], on["gaps"]
    change = statistics.median(b) / statistics.median(a) - 1
    draws = sorted(
        statistics.median(rng.choices(b, k=len(b))) / statistics.median(rng.choices(a, k=len(a)))
        - 1
        for _ in range(RESAMPLES)
    )
    low, high = draws[int(0.025 * RESAMPLES)], draws[int(0.975 * RESAMPLES) - 1]
    return {"change": change, "low": low, "high": high, "passed": change <= GATE}


def _mib(size: int) -> str:
    return f"{size / 1048576:.1f}"


def render(off: dict[str, Any], on: dict[str, Any], result: dict[str, Any]) -> str:
    def row(s: dict[str, Any]) -> str:
        phase = f"{s['label']} ({s['start'][:16]} to {s['end'][11:16]}, n = {s['n']})"
        gaps = f"{s['p50_ms']} | {s['p90_ms']} | {s['p99_ms']}"
        return f"| {phase} | {gaps} | {s['bps']} | {_mib(s['wal_max'])} | {_mib(s['wal_last'])} |"

    verdict = "PASS" if result["passed"] else "FAIL"
    return "\n".join(
        [
            "| Phase | gap p50 ms | gap p90 ms | gap p99 ms | in-cycle bps "
            "| WAL max MiB | WAL last MiB |",
            "|---|---|---|---|---|---|---|",
            BASELINE,
            row(off),
            row(on),
            "",
            f"Median gap {result['change'] * 100:+.1f} % on against off "
            f"(95 % CI {result['low'] * 100:+.1f} % to {result['high'] * 100:+.1f} %). "
            f"Gate (at most +{GATE * 100:.0f} %): **{verdict}**.",
        ]
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    take = commands.add_parser("sample")
    take.add_argument("--db", type=Path, default=Path(os.environ.get("BOT_DB", DEFAULT_DB)))
    take.add_argument("--label", required=True, choices=("off", "on"))
    take.add_argument("--minutes", type=float, default=60)
    take.add_argument("--out", type=Path, default=Path("results"))
    show = commands.add_parser("report")
    show.add_argument("off", type=Path)
    show.add_argument("on", type=Path)
    args = parser.parse_args(argv)
    if args.command == "sample":
        if not args.db.is_file():
            print(f"no bot database at {args.db} (set --db)", file=sys.stderr)
            return 2
        result = sample(args.db, args.label, args.minutes)
        args.out.mkdir(parents=True, exist_ok=True)
        path = args.out / f"{args.label}.json"
        path.write_text(json.dumps(result))
        summary = f"{result['n']} gaps, p50 {result['p50_ms']} ms, {result['bps']} bps"
        print(f"{args.label}: {summary} -> {path}")
        return 0
    off, on = json.loads(args.off.read_text()), json.loads(args.on.read_text())
    result = compare(off, on)
    print(render(off, on, result))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
