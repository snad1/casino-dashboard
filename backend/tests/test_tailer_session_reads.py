"""After the first full sync, session reads are rowid lookups that return what the scan would."""

import random
import sqlite3

import pytest

from app.ingest.tailer import _read_sessions


def _db(rows: int, lanes: bool, seed: int) -> sqlite3.Connection:
    rng = random.Random(seed)  # noqa: S311  # nosec B311: test data, not crypto
    connection = sqlite3.connect(":memory:")
    lane_column = ", lane INTEGER" if lanes else ""
    connection.execute(
        f"CREATE TABLE sessions (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        f" started_at TEXT, ended_at TEXT{lane_column})"
    )
    for i in range(rows):
        ended = None if rng.random() < 0.05 else f"2026-11-27T10:{i % 60:02d}:00"
        if lanes:
            connection.execute(
                "INSERT INTO sessions (started_at, ended_at, lane) VALUES (?, ?, ?)",
                ("2026-11-27T10:00:00", ended, rng.choice([1, 2, 3, None])),
            )
        else:
            connection.execute(
                "INSERT INTO sessions (started_at, ended_at) VALUES (?, ?)",
                ("2026-11-27T10:00:00", ended),
            )
    return connection


def _open(rows: list[dict[str, object]]) -> set[int]:
    return {int(r["id"]) for r in rows if r["ended_at"] is None or r["lane_newest"]}  # type: ignore[call-overload]


@pytest.mark.parametrize("lanes", [True, False])
@pytest.mark.parametrize("seed", range(5))
def test_a_primed_read_returns_what_the_full_scan_returns(lanes: bool, seed: int) -> None:
    connection = _db(300, lanes, seed)
    first = _read_sessions(connection, 0, set())
    still_open = _open(first)
    for after_id in (0, 1, 50, 150, 280, 299, 300):
        full = _read_sessions(connection, after_id, still_open)
        primed = _read_sessions(connection, after_id, still_open, primed=True)
        assert primed == full, after_id


def test_a_primed_read_with_nothing_open_reads_only_new_rows() -> None:
    connection = _db(20, True, 1)
    rows = _read_sessions(connection, 18, set(), primed=True)
    assert [r["id"] for r in rows] == [19, 20]


def test_a_primed_read_never_scans_the_sessions_table() -> None:
    connection = _db(50, True, 2)
    seen: list[str] = []
    connection.set_trace_callback(seen.append)
    _read_sessions(connection, 40, {3, 7}, primed=True)
    query = next(q for q in seen if q.lstrip().upper().startswith("SELECT *"))
    plan = [row[-1] for row in connection.execute("EXPLAIN QUERY PLAN " + query)]
    assert not any(step.startswith("SCAN") for step in plan), plan
