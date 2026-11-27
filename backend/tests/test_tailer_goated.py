"""goated's bot database tails into history through its registry entry's bet columns: the
target multiplier lands in `multiplier`, the session keeps its game, and nothing is invented
for the dice rule or bet value goated does not keep."""

import sqlite3
from pathlib import Path

import asyncpg  # type: ignore[import-untyped]  # ships no type hints

from app import registry
from app.ingest.tailer import Tailer

# goated.py's own tables, as `init_db` creates them (the columns the tailer reads).
_GOATED_SCHEMA = """
CREATE TABLE sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, ended_at TEXT, currency TEXT,
    game TEXT, strategy TEXT, base_bet REAL, multiplier REAL, total_bets INTEGER DEFAULT 0,
    wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0, profit REAL DEFAULT 0,
    wagered REAL DEFAULT 0, start_balance REAL, end_balance REAL, lane INTEGER DEFAULT 1
);
CREATE TABLE bets (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER, timestamp TEXT, game TEXT,
    amount REAL, multiplier_target REAL, result_value REAL, result_display TEXT, state TEXT,
    profit REAL, balance_after REAL
);
"""


def _goated_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(_GOATED_SCHEMA)
    conn.execute(
        "INSERT INTO sessions (started_at, currency, game, strategy, total_bets, lane)"
        " VALUES ('2026-01-05T10:00:00', 'usdt', 'limbo', 'Flat Bet', 2, 2)"
    )
    conn.executemany(
        "INSERT INTO bets (session_id, timestamp, game, amount, multiplier_target, result_value,"
        " result_display, state, profit, balance_after) VALUES (1, ?, 'limbo', 0.01, 2.5, ?, ?,"
        " ?, ?, ?)",
        [
            ("2026-01-05T10:00:01", 3.1, "3.10x", "win", 0.015, 10.015),
            ("2026-01-05T10:00:02", 1.2, "1.20x", "loss", -0.01, 10.005),
        ],
    )
    conn.commit()
    conn.close()


async def test_goated_bets_land_with_their_target_and_their_sessions_game(
    tmp_path: Path, pg: asyncpg.Connection, service: str
) -> None:
    entry = registry.KNOWN["goated"]
    _goated_db(tmp_path / entry.files.live_db)
    tailer = Tailer(
        pg,
        service,
        tmp_path,
        "UTC",
        live_name=entry.files.live_db,
        archive_glob=entry.files.archive_glob,
        bet_columns=entry.bets,
    )
    await tailer.prepare()
    for _ in range(100):
        if not await tailer.step():
            break
    bets = await pg.fetch(
        "SELECT lane, multiplier, rule, bet_value, result_value, state FROM bets"
        " WHERE service = $1 ORDER BY src_id",
        service,
    )
    assert [tuple(row) for row in bets] == [
        (2, 2.5, None, None, 3.1, "win"),
        (2, 2.5, None, None, 1.2, "loss"),
    ]
    game = await pg.fetchval("SELECT game FROM sessions WHERE service = $1", service)
    assert game == "limbo"


def test_wolfbet_keeps_its_own_bet_columns() -> None:
    assert registry.KNOWN["wolfbet"].bets == registry.WOLFBET_BETS
    assert registry.KNOWN["goated"].bets == registry.BetColumns(
        "b.multiplier_target", "NULL", "NULL"
    )
