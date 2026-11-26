"""Reconciliation finds real gaps, tells them apart from bot-side purges, and flags lag."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import asyncpg  # type: ignore[import-untyped]  # ships no type hints
import pytest

from app.ingest.reconcile import BOT_CONFIG, Reconciler, read_purge_days
from app.ingest.tailer import LIVE_DB, Tailer
from tests.wolfbet_db import BotDb, needs_wolfbet

pytestmark = needs_wolfbet

T0 = datetime(2026, 1, 5, 10, 0)


async def copy_all(pg: asyncpg.Connection, service: str, directory: Path) -> None:
    tailer = Tailer(pg, service, directory, "UTC")
    await tailer.prepare()
    for _ in range(1000):
        if not await tailer.step():
            return
    raise AssertionError("tailer never caught up")


@pytest.fixture
def reconcile(pg: asyncpg.Connection, service: str, tmp_path: Path) -> Any:
    reconciler = Reconciler(pg, service, tmp_path, "UTC")

    async def run() -> dict[str, Any]:
        row = await reconciler.run_once()
        assert row is not None
        return row

    run.reconciler = reconciler  # type: ignore[attr-defined]
    return run


async def drop_bet(pg: asyncpg.Connection, service: str, src_id: int) -> None:
    await pg.execute("DELETE FROM bets WHERE service = $1 AND src_id = $2", service, src_id)


def details(row: dict[str, Any]) -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(row["details"])
    return parsed


async def test_clean_copy_has_no_mismatch(
    wolfbet: ModuleType, tmp_path: Path, pg: asyncpg.Connection, service: str, reconcile: Any
) -> None:
    bot = BotDb(wolfbet, tmp_path / LIVE_DB)
    bot.bets(bot.session(lane=1), 30, T0)
    bot.bets(bot.session(lane=2), 20, T0)
    await copy_all(pg, service, tmp_path)
    row = await reconcile()
    assert (row["kind"], row["lo_id"], row["hi_id"]) == ("full", 0, 50)
    assert (row["sessions_checked"], row["mismatched"], row["purged_upstream"]) == (2, 0, 0)
    assert row["lag_alert"] is False
    stored = await pg.fetchval("SELECT count(*) FROM reconcile_runs WHERE service = $1", service)
    assert stored == 1
    bot.close()


async def test_missing_bet_is_a_mismatch_that_persists_until_fixed(
    wolfbet: ModuleType, tmp_path: Path, pg: asyncpg.Connection, service: str, reconcile: Any
) -> None:
    bot = BotDb(wolfbet, tmp_path / LIVE_DB)
    session = bot.session()
    bot.bets(session, 10, T0)
    await copy_all(pg, service, tmp_path)
    dropped = await pg.fetchrow("SELECT * FROM bets WHERE service = $1 AND src_id = 4", service)
    await drop_bet(pg, service, 4)

    first = await reconcile()
    assert (first["mismatched"], first["persisting"]) == (1, 0)
    [entry] = details(first)["sessions"]
    assert entry["session"] == session
    assert entry["status"] == "mismatch"
    assert (entry["sqlite"]["count"], entry["timescale"]["count"]) == (10, 9)

    second = await reconcile()
    assert second["kind"] == "quick"
    assert second["lo_id"] == 0
    assert (second["mismatched"], second["persisting"]) == (1, 1)

    columns = ", ".join(dropped.keys())
    marks = ", ".join(f"${i}" for i in range(1, len(dropped) + 1))
    await pg.execute(f"INSERT INTO bets ({columns}) VALUES ({marks})", *dropped.values())  # noqa: S608  # nosec B608: column names from the row
    fixed = await reconcile()
    assert (fixed["mismatched"], fixed["persisting"]) == (0, 0)
    bot.close()


async def test_bot_side_purge_is_not_a_mismatch(
    wolfbet: ModuleType, tmp_path: Path, pg: asyncpg.Connection, service: str, reconcile: Any
) -> None:
    bot = BotDb(wolfbet, tmp_path / LIVE_DB)
    old, live = bot.session(), bot.session()
    bot.bets(old, 10, T0)
    bot.bets(live, 10, T0 + timedelta(hours=1))
    await copy_all(pg, service, tmp_path)
    bot.conn.execute("DELETE FROM bets WHERE session_id = ?", (old,))
    bot.conn.execute("DELETE FROM bets WHERE session_id = ? AND id <= 13", (live,))
    bot.conn.commit()
    row = await reconcile()
    assert (row["mismatched"], row["purged_upstream"]) == (0, 2)
    assert {e["status"] for e in details(row)["sessions"]} == {"purged_upstream"}
    bot.close()


async def test_gap_in_the_unpurged_tail_is_still_a_mismatch(
    wolfbet: ModuleType, tmp_path: Path, pg: asyncpg.Connection, service: str, reconcile: Any
) -> None:
    bot = BotDb(wolfbet, tmp_path / LIVE_DB)
    bot.bets(bot.session(), 20, T0)
    await copy_all(pg, service, tmp_path)
    bot.conn.execute("DELETE FROM bets WHERE id <= 5")
    bot.conn.commit()
    await drop_bet(pg, service, 15)
    row = await reconcile()
    assert (row["mismatched"], row["purged_upstream"]) == (1, 0)
    bot.close()


async def test_quick_run_starts_where_the_last_clean_run_ended(
    wolfbet: ModuleType, tmp_path: Path, pg: asyncpg.Connection, service: str, reconcile: Any
) -> None:
    bot = BotDb(wolfbet, tmp_path / LIVE_DB)
    session = bot.session()
    bot.bets(session, 10, T0)
    await copy_all(pg, service, tmp_path)
    await reconcile()
    bot.bets(session, 5, T0 + timedelta(minutes=5))
    await copy_all(pg, service, tmp_path)
    row = await reconcile()
    assert (row["kind"], row["lo_id"], row["hi_id"]) == ("quick", 10, 15)
    assert row["mismatched"] == 0
    bot.close()


async def test_float_sums_compare_exactly(
    wolfbet: ModuleType, tmp_path: Path, pg: asyncpg.Connection, service: str, reconcile: Any
) -> None:
    bot = BotDb(wolfbet, tmp_path / LIVE_DB)
    session = bot.session()
    for profit in (0.1, 0.2, 1e-9, -0.30000000000000004, 123456.789):
        bot.bets(session, 1, T0, profit=profit)
    await copy_all(pg, service, tmp_path)
    row = await reconcile()
    assert row["mismatched"] == 0
    bot.close()


async def test_lag_over_half_the_purge_window_is_flagged(
    wolfbet: ModuleType, tmp_path: Path, pg: asyncpg.Connection, service: str, reconcile: Any
) -> None:
    (tmp_path / BOT_CONFIG).write_text(json.dumps({"purge_days": 1, "api_key": "not-logged"}))
    bot = BotDb(wolfbet, tmp_path / LIVE_DB)
    session = bot.session()
    bot.bets(session, 5, T0)
    await copy_all(pg, service, tmp_path)
    bot.bets(session, 3, T0 + timedelta(hours=13))
    row = await reconcile()
    assert row["lag_rows"] == 3
    assert row["lag_seconds"] == pytest.approx(13 * 3600 - 2)
    assert row["lag_alert"] is True
    assert row["mismatched"] == 0
    bot.close()


async def test_missing_bot_config_assumes_three_days(
    wolfbet: ModuleType, tmp_path: Path, pg: asyncpg.Connection, service: str, reconcile: Any
) -> None:
    bot = BotDb(wolfbet, tmp_path / LIVE_DB)
    session = bot.session()
    bot.bets(session, 5, T0)
    await copy_all(pg, service, tmp_path)
    bot.bets(session, 1, T0 + timedelta(hours=13))
    row = await reconcile()
    assert row["lag_alert"] is False
    bot.close()


async def test_every_sqlite_read_is_short(
    wolfbet: ModuleType, tmp_path: Path, pg: asyncpg.Connection, service: str, reconcile: Any
) -> None:
    bot = BotDb(wolfbet, tmp_path / LIVE_DB)
    bot.bets(bot.session(), 120_000, T0)
    await copy_all(pg, service, tmp_path)
    row = await reconcile()
    assert (row["hi_id"], row["mismatched"]) == (120_000, 0)
    assert reconcile.reconciler.max_read_seconds < 0.2
    bot.close()


def test_purge_days_come_from_the_services_own_config(tmp_path: Path) -> None:
    (tmp_path / BOT_CONFIG).write_text(json.dumps({"purge_days": 3}))
    (tmp_path / ".demo_autobot.json").write_text(json.dumps({"purge_days": 9, "api_key": "x" * 40}))
    assert read_purge_days(tmp_path, ".demo_autobot.json") == 9
    assert read_purge_days(tmp_path) == 3
