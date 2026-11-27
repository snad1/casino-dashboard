"""The epoch probe runs on every poll, so it must be an index lookup, not a scan."""

import sqlite3
from pathlib import Path

from app.ingest.tailer import _probe_epoch


def _db(path: Path, starts: list[str]) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT)"
    )
    connection.executemany("INSERT INTO sessions (started_at) VALUES (?)", [(s,) for s in starts])
    connection.commit()
    return connection


def test_the_epoch_is_the_first_sessions_start(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    connection = _db(path, ["2026-11-25T19:08:18", "2026-11-25T19:09:00"])
    assert _probe_epoch(connection, path) == (path.stat().st_ino, "2026-11-25T19:08:18")


def test_a_later_row_with_an_earlier_clock_does_not_move_the_epoch(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    connection = _db(path, ["2026-11-25T19:08:18", "2026-11-25T19:09:00"])
    connection.execute("INSERT INTO sessions (started_at) VALUES ('2026-11-25T18:00:00')")
    connection.commit()
    assert _probe_epoch(connection, path) == (path.stat().st_ino, "2026-11-25T19:08:18")


def test_an_empty_file_has_no_epoch_yet(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    assert _probe_epoch(_db(path, []), path) is None


def test_the_probe_reads_by_rowid_instead_of_scanning(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    connection = _db(path, ["2026-11-25T19:08:18"])
    plan = " ".join(
        row[-1]
        for row in connection.execute(
            "EXPLAIN QUERY PLAN SELECT started_at FROM sessions ORDER BY id LIMIT 1"
        )
    )
    assert "TEMP B-TREE" not in plan
