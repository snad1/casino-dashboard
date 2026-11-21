"""One soak day: the numbers that decide whether the dashboard can replace the old web.

    run    read the last 24 h and append one record to the results file:
           bot   gaps between bets (bot database, read-only), the in-cycle bps and the
                 median gap against the events-off baseline of the load proof
           ingest reconcile runs, sessions mismatched, and mismatches seen in two runs
                  in a row (the ones that alert)
           bus   events the bot's publisher and the dashboard's consumer dropped, as the
                 change since the previous record; a counter that went down means its
                 process restarted, and then its current value is the day's count
           api   log lines with a 5xx status or an error level
           redis whether Redis restarted in the window (drops are expected then)
    table  every recorded day as a markdown table plus the verdict

Containers are found by their compose labels, as deploy/backup.sh does. Standard library
only, so it runs with the host's python3. The bot is never touched.
    python3 tools/soak_day.py run --db ~/.wolfbet_autobot.db --results tools/soak-results/soak.jsonl
    python3 tools/soak_day.py table --results tools/soak-results/soak.jsonl
"""

import argparse
import importlib.util
import json
import re
import shutil
import statistics
import subprocess  # nosec B404: fixed docker argv, no shell
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

DAY = timedelta(hours=24)
SOAK_DAYS = 7
METRICS_URL = "http://127.0.0.1:8000/metrics"
COUNTERS = ("dashboard_publisher_dropped_total", "dashboard_consumer_dropped_total")
RECONCILE_SQL = (
    "SELECT count(*), coalesce(sum(mismatched), 0), coalesce(max(persisting), 0) "
    "FROM reconcile_runs WHERE started_at > now() - interval '24 hours'"
)
_FETCH = (
    "import urllib.request; "
    f"print(urllib.request.urlopen('{METRICS_URL}', timeout=10).read().decode())"
)
METRIC_LINE = re.compile(r"^([a-z_]+)(?:\{[^}]*\})? ([0-9.eE+-]+)$")

Docker = Callable[[Sequence[str]], str | None]


def _load_proof() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "load_proof", Path(__file__).with_name("load_proof.py")
    )
    if spec is None or spec.loader is None:
        raise ImportError("tools/load_proof.py is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


proof = _load_proof()
BASELINE_P50_MS = float(proof.BASELINE.split("|")[2])


def docker(argv: Sequence[str]) -> str | None:
    """stdout of `docker argv`, or None when docker fails or is missing."""
    binary = shutil.which("docker")
    if binary is None:
        return None
    try:
        done = subprocess.run(  # noqa: S603  # nosec B603: fixed docker argv, no shell
            [binary, *argv], capture_output=True, text=True, timeout=120, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


def container(run: Docker, project: str, service: str) -> str | None:
    out = run(
        [
            "ps",
            "-q",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--filter",
            f"label=com.docker.compose.service={service}",
        ]
    )
    ids = (out or "").split()
    return ids[0] if ids else None


def bot_day(db: Path, start: datetime, end: datetime) -> dict[str, Any]:
    gaps = proof.bet_gaps(db, start, end) if db.exists() else []
    if not gaps:
        return {"bets": 0, "bps": None, "p50_ms": None, "bps_ok": True}
    p50 = round(proof._percentile(gaps, 0.5), 1)
    return {
        "bets": len(gaps) + 1,
        "bps": round(1000 / statistics.fmean(gaps), 2),
        "p50_ms": p50,
        "bps_ok": p50 <= BASELINE_P50_MS * (1 + proof.GATE),
    }


def counters(text: str) -> dict[str, float]:
    """Each wanted counter summed over its labels (the publisher counts per lane)."""
    totals = dict.fromkeys(COUNTERS, 0.0)
    for line in text.splitlines():
        match = METRIC_LINE.match(line)
        if match and match.group(1) in totals:
            totals[match.group(1)] += float(match.group(2))
    return totals


def api_errors(logs: str) -> int:
    count = 0
    for line in logs.splitlines():
        if line.startswith("Traceback"):
            count += 1
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        status = entry.get("status")
        if (isinstance(status, int) and status >= 500) or entry.get("level") in (
            "error",
            "critical",
        ):
            count += 1
    return count


def redis_restarted(run: Docker, redis_id: str, start: datetime) -> bool:
    started = (run(["inspect", "-f", "{{.State.StartedAt}}", redis_id]) or "").strip()
    if not started:
        return False
    stamp = datetime.fromisoformat(re.sub(r"\.(\d{6})\d*", r".\1", started).replace("Z", "+00:00"))
    return stamp > start.astimezone(UTC)


def _delta(now: float, before: float | None) -> float:
    return now if before is None or now < before else now - before


def collect(
    db: Path,
    project: str,
    previous: dict[str, Any] | None,
    run: Docker = docker,
    now: Callable[[], datetime] = datetime.now,
) -> dict[str, Any]:
    end = now()
    start = end - DAY
    record: dict[str, Any] = {"date": end.date().isoformat(), "at": end.isoformat()}
    record.update(bot_day(db, start, end))
    ids = {s: container(run, project, s) for s in ("api", "timescale", "redis")}
    if not all(ids.values()):
        down = [s for s, i in ids.items() if not i]
        record.update(stack_up=False, failed=[f"stack down: {', '.join(down)}"])
        return record
    metrics = run(["exec", str(ids["api"]), "python", "-c", _FETCH]) or ""
    totals = counters(metrics)
    before = (previous or {}).get("counters") or {}
    reconcile = (
        run(
            [
                "exec",
                str(ids["timescale"]),
                "psql",
                "-U",
                "dashboard",
                "-d",
                "dashboard",
                "-At",
                "-F",
                " ",
                "-c",
                RECONCILE_SQL,
            ]
        )
        or ""
    ).split()
    runs, mismatched, persisting = (int(v) for v in reconcile) if len(reconcile) == 3 else (0, 0, 0)
    logs = run(["logs", "--since", "24h", str(ids["api"])]) or ""
    record.update(
        stack_up=True,
        counters=totals,
        publisher_dropped=int(_delta(totals[COUNTERS[0]], before.get(COUNTERS[0]))),
        consumer_dropped=int(_delta(totals[COUNTERS[1]], before.get(COUNTERS[1]))),
        reconcile_runs=runs,
        mismatched=mismatched,
        persisting=persisting,
        api_errors=api_errors(logs),
        redis_restarted=redis_restarted(run, str(ids["redis"]), start),
        metrics_read=bool(metrics),
    )
    record["failed"] = gates(record)
    return record


def gates(r: dict[str, Any]) -> list[str]:
    failed: list[str] = []
    if not r["metrics_read"]:
        failed.append("metrics unreadable")
    if r["reconcile_runs"] == 0:
        failed.append("no reconcile runs")
    if r["persisting"]:
        failed.append(f"{r['persisting']} persisting mismatches")
    if not r["bps_ok"]:
        failed.append(f"median gap {r['p50_ms']} ms over {BASELINE_P50_MS} ms + 2 %")
    if (r["publisher_dropped"] or r["consumer_dropped"]) and not r["redis_restarted"]:
        failed.append("events dropped without a Redis restart")
    if r["api_errors"]:
        failed.append(f"{r['api_errors']} api errors")
    return failed


def read_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _cell(value: Any) -> str:
    return "-" if value is None else str(value)


def render(records: list[dict[str, Any]]) -> str:
    lines = [
        f"Baseline median gap {BASELINE_P50_MS} ms (bot alone, events off).",
        "",
        "| Day | Date | Bets | In-cycle bps | Median gap ms | Mismatched / persisting "
        "| Dropped publisher / consumer | Redis restart | API errors | Result |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for day, r in enumerate(records, 1):
        result = "PASS" if not r["failed"] else "FAIL: " + "; ".join(r["failed"])
        if not r.get("stack_up"):
            lines.append(f"| {day} | {r['date']} | {r['bets']} | | | | | | | {result} |")
            continue
        lines.append(
            f"| {day} | {r['date']} | {r['bets']} | {_cell(r['bps'])} | {_cell(r['p50_ms'])} "
            f"| {r['mismatched']} / {r['persisting']} "
            f"| {r['publisher_dropped']} / {r['consumer_dropped']} "
            f"| {'yes' if r['redis_restarted'] else 'no'} | {r['api_errors']} | {result} |"
        )
    failing = [str(d) for d, r in enumerate(records, 1) if r["failed"]]
    if failing:
        verdict = f"Verdict: FAIL on day {', '.join(failing)}."
    elif len(records) < SOAK_DAYS:
        verdict = f"Verdict: {len(records)} of {SOAK_DAYS} days so far, all PASS."
    else:
        verdict = f"Verdict: {len(records)} days, all PASS."
    return "\n".join([*lines, "", verdict])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    take = commands.add_parser("run")
    take.add_argument("--db", type=Path, default=proof.DEFAULT_DB)
    take.add_argument("--results", type=Path, required=True)
    take.add_argument("--project", required=True)
    show = commands.add_parser("table")
    show.add_argument("--results", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "table":
        print(render(read_records(args.results)))
        return 0
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", args.project):
        parser.error("--project must be a compose project name")
    records = read_records(args.results)
    record = collect(args.db, args.project, records[-1] if records else None, run=docker)
    args.results.parent.mkdir(parents=True, exist_ok=True)
    with args.results.open("a") as out:
        out.write(json.dumps(record, sort_keys=True) + "\n")
    day = len(records) + 1
    print(
        f"soak day {day}: "
        + ("PASS" if not record["failed"] else "FAIL: " + "; ".join(record["failed"]))
    )
    return 0 if not record["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
