"""tools/soak_day.py and tools/soak.sh: one record per day, its gates, and the table.

docker is a fake that answers from a dict, so no container runs; the bot database is a
temp SQLite file shaped like the bot's.
"""

import importlib.util
import json
import os
import sqlite3
import subprocess
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

TOOLS = Path(__file__).resolve().parents[2] / "tools"
NOW = datetime(2026, 11, 21, 23, 50, 0)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("soak_day", TOOLS / "soak_day.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


soak = _load()


def _bot_db(path: Path, gap_ms: float, bets: int = 50) -> Path:
    path.unlink(missing_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE bets (id INTEGER PRIMARY KEY, session_id INTEGER, timestamp TEXT)"
        )
        start = NOW - timedelta(hours=1)
        conn.executemany(
            "INSERT INTO bets (session_id, timestamp) VALUES (1, ?)",
            [((start + timedelta(milliseconds=i * gap_ms)).isoformat(),) for i in range(bets)],
        )
    return path


def _metrics(publisher: float, consumer: float = 0) -> str:
    return (
        "# TYPE dashboard_publisher_dropped_total counter\n"
        f'dashboard_publisher_dropped_total{{lane="1"}} {publisher}\n'
        'dashboard_publisher_dropped_total{lane="2"} 0\n'
        f"dashboard_consumer_dropped_total {consumer}\n"
        "dashboard_consumer_gaps_total 3\n"
    )


class FakeDocker:
    def __init__(self, **over: Any) -> None:
        self.answers: dict[str, str | None] = {
            "ps api": "api1\n",
            "ps timescale": "ts1\n",
            "ps redis": "rd1\n",
            "metrics": _metrics(0),
            "psql": "96 0 0\n",
            "logs": '{"status": 200, "level": "info"}\n',
            "inspect": "2026-11-01T08:00:00.123456789Z\n",
        }
        self.answers.update(over)
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str]) -> str | None:
        argv = list(argv)
        self.calls.append(argv)
        if argv[0] == "ps":
            return self.answers[f"ps {argv[-1].rsplit('=', 1)[1]}"]
        if argv[0] == "exec":
            return self.answers["metrics" if argv[2] == "python" else "psql"]
        return self.answers[argv[0]]


def _collect(tmp_path: Path, docker: FakeDocker, previous: Any = None, gap_ms: float = 38) -> Any:
    db = _bot_db(tmp_path / "bot.db", gap_ms)
    return soak.collect(db, "k8s0abc", previous, run=docker, now=lambda: NOW)


def test_a_quiet_day_passes(tmp_path: Path) -> None:
    record = _collect(tmp_path, FakeDocker())
    assert record["failed"] == []
    assert record["bets"] == 50 and record["p50_ms"] == 38.0 and record["bps_ok"]
    assert record["reconcile_runs"] == 96 and record["api_errors"] == 0
    assert record["counters"]["dashboard_publisher_dropped_total"] == 0


def test_a_slower_bot_fails_the_bps_gate(tmp_path: Path) -> None:
    record = _collect(tmp_path, FakeDocker(), gap_ms=40)
    assert not record["bps_ok"]
    assert any(f.startswith("median gap 40.0 ms") for f in record["failed"])


def test_drops_count_since_the_previous_day_and_a_restart_resets_the_base(
    tmp_path: Path,
) -> None:
    before = {"counters": {"dashboard_publisher_dropped_total": 10.0}}
    same = _collect(tmp_path, FakeDocker(metrics=_metrics(10)), previous=before)
    assert same["publisher_dropped"] == 0 and same["failed"] == []
    grew = _collect(tmp_path, FakeDocker(metrics=_metrics(14)), previous=before)
    assert grew["publisher_dropped"] == 4
    assert "events dropped without a Redis restart" in grew["failed"]
    restarted = _collect(tmp_path, FakeDocker(metrics=_metrics(3)), previous=before)
    assert restarted["publisher_dropped"] == 3


def test_drops_on_a_day_redis_restarted_are_expected(tmp_path: Path) -> None:
    record = _collect(
        tmp_path,
        FakeDocker(metrics=_metrics(5, consumer=2), inspect="2026-11-21T04:00:00.5Z\n"),
    )
    assert record["redis_restarted"] and record["consumer_dropped"] == 2
    assert record["failed"] == []


def test_persisting_mismatches_and_api_errors_fail(tmp_path: Path) -> None:
    logs = "\n".join(
        [
            '{"status": 200, "level": "info"}',
            '{"status": 502, "level": "info"}',
            '{"event": "boom", "level": "error"}',
            "Traceback (most recent call last):",
            "not json",
        ]
    )
    record = _collect(tmp_path, FakeDocker(psql="96 4 1\n", logs=logs))
    assert record["mismatched"] == 4 and record["api_errors"] == 3
    assert record["failed"] == ["1 persisting mismatches", "3 api errors"]


def test_a_stack_that_is_down_fails_without_reading_further(tmp_path: Path) -> None:
    docker = FakeDocker(**{"ps timescale": ""})
    record = _collect(tmp_path, docker)
    assert record["stack_up"] is False and record["failed"] == ["stack down: timescale"]
    assert all(call[0] == "ps" for call in docker.calls)


def test_no_reconcile_runs_or_unreadable_metrics_fail(tmp_path: Path) -> None:
    record = _collect(tmp_path, FakeDocker(psql=None, metrics=None))
    assert record["failed"] == ["metrics unreadable", "no reconcile runs"]


def test_an_idle_bot_is_not_a_failure(tmp_path: Path) -> None:
    record = soak.collect(
        tmp_path / "missing.db", "k8s0abc", None, run=FakeDocker(), now=lambda: NOW
    )
    assert record["bets"] == 0 and record["bps"] is None and record["failed"] == []


def test_the_table_has_a_row_per_day_and_a_verdict(tmp_path: Path) -> None:
    good = _collect(tmp_path, FakeDocker())
    down = _collect(tmp_path, FakeDocker(**{"ps api": ""}))
    table = soak.render([good, down])
    rows = [line for line in table.splitlines() if line.startswith("| ")]
    assert len(rows) == 3
    assert rows[1].startswith("| 1 | 2026-11-21 | 50 |") and rows[1].endswith("| PASS |")
    assert "FAIL: stack down: api" in rows[2]
    assert table.endswith("Verdict: FAIL on day 2.")
    assert soak.render([good] * 3).endswith("Verdict: 3 of 7 days so far, all PASS.")
    assert soak.render([good] * 7).endswith("Verdict: 7 days, all PASS.")


def test_run_appends_one_record_and_exits_1_on_a_failed_day(
    tmp_path: Path, monkeypatch: Any
) -> None:
    results = tmp_path / "out" / "soak.jsonl"
    monkeypatch.setattr(soak, "docker", FakeDocker(**{"ps redis": ""}))
    code = soak.main(
        [
            "run",
            "--db",
            str(tmp_path / "none.db"),
            "--results",
            str(results),
            "--project",
            "k8s0abc",
        ]
    )
    assert code == 1
    assert [json.loads(line)["failed"] for line in results.read_text().splitlines()] == [
        ["stack down: redis"]
    ]


def _soak_sh(env: dict[str, str], verb: str) -> subprocess.CompletedProcess[str]:
    command = ["bash", str(TOOLS / "soak.sh"), verb]
    return subprocess.run(  # noqa: S603  # nosec B603: our script under test, no shell
        command, capture_output=True, text=True, env=env, timeout=30, check=False
    )


def test_install_writes_a_quiet_nightly_timer(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (
        ("id", 'echo "${STUB_UID:-0}"'),
        ("systemctl", 'echo "$*" >> "$HOME/calls"'),
    ):
        (bin_dir / name).write_text(f"#!/bin/sh\n{body}\n")
        (bin_dir / name).chmod(0o755)
    env = {
        "HOME": str(tmp_path),
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "SOAK_UNIT_DIR": str(tmp_path / "units"),
    }
    done = _soak_sh(env, "install")
    assert done.returncode == 0, done.stderr
    service = (tmp_path / "units/casino-soak.service").read_text()
    timer = (tmp_path / "units/casino-soak.timer").read_text()
    assert f"ExecStart={TOOLS}/soak.sh run" in service and "IOSchedulingClass=idle" in service
    assert "OnCalendar=*-*-* 23:50" in timer and "Persistent=true" in timer
    assert "enable --now casino-soak.timer" in (tmp_path / "calls").read_text()
    unconfigured = _soak_sh(env, "run")
    assert unconfigured.returncode == 2 and "Dashboard not installed" in unconfigured.stderr


def test_run_reads_the_stack_by_its_compose_project(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python3").write_text('#!/bin/sh\necho "$*" >> "$HOME/calls"\n')
    (bin_dir / "python3").chmod(0o755)
    conf = tmp_path / ".config/casino-dashboard"
    conf.mkdir(parents=True)
    (conf / "dash.env").write_text("DASH_PROJECT=hrrxmud5u4rwmjyilbqwryib\n")
    env = {"HOME": str(tmp_path), "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    done = _soak_sh(env, "run")
    assert done.returncode == 0, done.stderr
    assert (tmp_path / "calls").read_text().split()[-2:] == ["--project", "wolfbet-dashboard"]
