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


def last_bet(db: Path) -> str | None:
    """The newest bet's timestamp, or None when the table is empty or missing."""
    try:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
            row = conn.execute("SELECT max(timestamp) FROM bets").fetchone()
    except sqlite3.OperationalError:
        return None
    stamp: str | None = row[0]
    return stamp


def wal_size(db: Path) -> int:
    wal = Path(f"{db}-wal")
    return wal.stat().st_size if wal.exists() else 0


def _percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))]


def summarize(
    label: str, gaps: list[float], wal: list[int], start: datetime, end: datetime
) -> dict[str, Any]:
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


def _mib(size: int) -> str:
    return f"{size / 1048576:.1f}"


def _last_seen(db: Path, at: datetime) -> str:
    stamp = last_bet(db)
    if stamp is None:
        return f"no bets in {db} yet"
    age = (at - datetime.fromisoformat(stamp)).total_seconds()
    if age < 90:
        ago = f"{age:.0f} s"
    elif age < 5400:
        ago = f"{age / 60:.0f} min"
    else:
        ago = f"{age / 3600:.0f} h"
    return f"last bet {stamp[:19].replace('T', ' ')} ({ago} ago)"


def _stderr(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


def sample(
    db: Path,
    label: str,
    minutes: float,
    sleep: Callable[[float], None] | None = None,
    now: Callable[[], datetime] | None = None,
    say: Callable[[str], None] = _stderr,
) -> dict[str, Any]:
    """Watch for `minutes`, saying how far it got each minute; Ctrl-C ends the window early."""
    sleep = sleep or time.sleep
    now = now or datetime.now
    start = now()
    say(
        f"{label}: sampling {db} for {minutes:g} min; {_last_seen(db, start)}; "
        "Ctrl-C stops early and keeps what was measured"
    )
    wal = [wal_size(db)]
    left = minutes * 60
    done = 0.0
    try:
        while left > 0:
            step = min(WAL_EVERY_S, left)
            sleep(step)
            left -= step
            done += step
            wal.append(wal_size(db))
            if done % 60 == 0 and left > 0:
                say(f"{label}: {done / 60:g} of {minutes:g} min, WAL {_mib(wal[-1])} MiB")
    except KeyboardInterrupt:
        say(f"{label}: stopped after {done / 60:.1f} of {minutes:g} min")
    end = now()
    gaps = bet_gaps(db, start, end)
    if not gaps:
        raise SystemExit(
            f"{label}: no bets in the window in {db}; {_last_seen(db, end)}. "
            "Is the bot betting, and is WOLFBET_DB_PATH this file?"
        )
    return summarize(label, gaps, wal, start, end)


def _resampled_median(ordered: Sequence[float], rng: random.Random) -> float:
    """The median of one bootstrap resample, drawn without building the resample.

    Resampling n values picks indices floor(n * u) for n uniform u, so the resample's k-th
    smallest value sits at floor(n * U(k)), where U(k), the k-th smallest of n uniforms, is
    Beta(k, n - k + 1). The next one up is the least of the n - k uniforms above U(k). Same
    distribution as statistics.median(rng.choices(...)), in constant time: an hour's samples
    hold about 100 000 gaps, and resampling them 2000 times took minutes.
    """
    n = len(ordered)
    k = (n + 1) // 2
    u = rng.betavariate(k, n - k + 1)
    low = ordered[min(n - 1, int(u * n))]
    if n % 2:
        return low
    v = u + (1 - u) * rng.betavariate(1, n - k)
    return (low + ordered[min(n - 1, int(v * n))]) / 2


def compare(off: dict[str, Any], on: dict[str, Any], seed: int = 7) -> dict[str, Any]:
    """Median-gap change from off to on, with a bootstrap 95 % interval, and the gate."""
    rng = random.Random(seed)  # noqa: S311  # resampling, not a secret
    a, b = sorted(off["gaps"]), sorted(on["gaps"])
    change = statistics.median(b) / statistics.median(a) - 1
    draws = sorted(
        _resampled_median(b, rng) / _resampled_median(a, rng) - 1 for _ in range(RESAMPLES)
    )
    low, high = draws[int(0.025 * RESAMPLES)], draws[int(0.975 * RESAMPLES) - 1]
    return {"change": change, "low": low, "high": high, "passed": change <= GATE}


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
