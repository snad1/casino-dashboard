"""The analytics report rebuilt from history equals `wolfbet.py --analytics --json`.

A fixture database is built with WolfBet's own schema, the real CLI reads it (and must
leave it unchanged), the tailer copies it into the test Timescale, and every section of
the report must match to 1e-12. The charts are checked against the same fixture.
"""

import hashlib
import json
import os
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg  # type: ignore[import-untyped]  # ships no type hints
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.wolfbet.analytics import (
    AnalyticsReport,
    SessionWindow,
    history_report,
    session_stats,
)
from app.api.analytics import BINS, chart_data
from app.history.epochs import epoch_view
from app.ingest.tailer import LIVE_DB
from tests.test_reconcile import copy_all
from tests.wolfbet_db import WORKSPACE, BotDb, needs_wolfbet

BOT = WORKSPACE / "wolfbet" / "wolfbet.py"
BOT_PYTHON = WORKSPACE / ".venv-test" / "bin" / "python"
UTC_ZONE = ZoneInfo("UTC")

pytestmark = [
    needs_wolfbet,
    pytest.mark.skipif(
        not BOT_PYTHON.is_file(), reason="needs the workspace .venv-test that runs wolfbet"
    ),
]

# lane, started, ended, game, strategy, bets, wins, losses, profit, wagered
SESSIONS: list[tuple[Any, ...]] = [
    (1, "2026-01-05T10:00:00", "2026-01-05T10:30:00", "dice", "Mart", 100, 49, 51, 1.2345e-4, 0.01),
    (2, "2026-01-05T11:00:00", "2026-01-05T12:15:30.5", "limbo", "Flat", 200, 120, 80, -3e-4, 0.02),
    (3, "2026-01-06T09:30:00", "2026-01-06T09:31:00", "dice", "Flat", 50, 25, 25, 0.0000071, 0.005),
    (1, "2026-01-06T23:59:59", "2026-01-07T00:10:00", "limbo", "Mart", 10, 3, 7, -0.00001, 0.001),
    (2, "2026-01-07T08:00:00", "2026-01-07T08:05:00", None, None, 7, 4, 3, 0.0, 0.0007),
    (2, "2026-01-07T09:00:00", "2026-01-07T09:01:00", "dice", "D'Al", 3, 1, 2, 0.1 + 0.2, 0.3),
]  # fmt: skip
BETS_PER_SESSION = 4


def build(wolfbet: ModuleType, path: Path, sessions: list[tuple[Any, ...]]) -> None:
    bot = BotDb(wolfbet, path)
    for lane, started, ended, game, strategy, bets, wins, losses, profit, wagered in sessions:
        session = bot.session(lane=lane, started=started)
        bot.conn.execute(
            "UPDATE sessions SET ended_at = ?, game = ?, strategy = ?, total_bets = ?, wins = ?,"
            " losses = ?, profit = ?, wagered = ? WHERE id = ?",
            (ended, game, strategy, bets, wins, losses, profit, wagered, session),
        )
        bot.conn.commit()
        bot.bets(session, BETS_PER_SESSION, datetime.fromisoformat(started))
    bot.close()


def digest(directory: Path) -> dict[str, str]:
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(directory.glob(f"{LIVE_DB}*"))
    }


def remove_db(directory: Path) -> None:
    """What `wolfctl reset` leaves behind: no live database, so the next one is a new epoch."""
    for path in directory.glob(f"{LIVE_DB}*"):
        path.unlink()


def run_bot(directory: Path, *window: str, command: str = "--analytics") -> dict[str, Any]:
    """The real CLI, sandboxed: every path it could write points into `directory`."""
    home = directory / "home"
    home.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("WOLFBET_", "VIRTUAL_ENV"))}
    env.update(
        APP_ENV="testing",
        HOME=str(home),
        TZ="UTC",
        WOLFBET_DB_PATH=str(directory / LIVE_DB),
        WOLFBET_CONFIG_PATH=str(home / "config.json"),
        WOLFBET_STATE_PATH=str(home / "live.json"),
        WOLFBET_PID_PATH=str(home / "bot.pid"),
        WOLFBET_RUN_PATH=str(home / "run.json"),
        WOLFBET_PRESET_PATH=str(home / "presets.json"),
        WOLFBET_LOG_DIR=str(home / "logs"),
    )
    before = digest(directory)
    result = subprocess.run(  # noqa: S603  # nosec B603: fixed interpreter and script, no shell
        [str(BOT_PYTHON), str(BOT), command, "--json", *window],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert digest(directory) == before, "the analytics command changed the bot's database"
    doc: dict[str, Any] = json.loads(result.stdout)
    return doc


def assert_same(ours: Any, theirs: Any, where: str = "report") -> None:
    if isinstance(theirs, dict):
        assert isinstance(ours, dict) and ours.keys() == theirs.keys(), where
        for key in theirs:
            assert_same(ours[key], theirs[key], f"{where}.{key}")
    elif isinstance(theirs, list):
        assert isinstance(ours, list) and len(ours) == len(theirs), where
        for i, (a, b) in enumerate(zip(ours, theirs, strict=True)):
            assert_same(a, b, f"{where}[{i}]")
    elif isinstance(theirs, float | int) and not isinstance(theirs, bool):
        assert ours == pytest.approx(theirs, rel=1e-12, abs=1e-12), where
    else:
        assert ours == theirs, where


async def window(
    db: AsyncSession,
    service: str,
    since: str | None = None,
    until: str | None = None,
    lane: int | None = None,
    key: str | None = None,
) -> SessionWindow:
    """A window over the current epoch, as the api builds one without `epoch`."""
    view = await epoch_view(db, service)
    return SessionWindow(service, since, until, lane, UTC_ZONE, view.ids, key)


def flags(since: str | None, until: str | None, lane: int | None) -> list[str]:
    out: list[str] = []
    for flag, value in (("--since", since), ("--until", until), ("--lane", lane)):
        if value is not None:
            out += [flag, str(value)]
    return out


WINDOWS = [
    (None, None, None),
    ("2026-01-05", "2026-01-06", None),
    ("2026-01-05T10:30:00", None, 2),
    (None, "2026-01-06T23:59:59", 1),
    ("2026-02-01", None, None),
]


@pytest.mark.parametrize(("since", "until", "lane"), WINDOWS)
async def test_history_report_equals_the_bots_json(
    wolfbet: ModuleType,
    tmp_path: Path,
    pg: asyncpg.Connection,
    service: str,
    db_session: AsyncSession,
    since: str | None,
    until: str | None,
    lane: int | None,
) -> None:
    build(wolfbet, tmp_path / LIVE_DB, SESSIONS)
    bot = run_bot(tmp_path, *flags(since, until, lane))
    AnalyticsReport.model_validate(bot)
    await copy_all(pg, service, tmp_path)

    ours = await history_report(db_session, await window(db_session, service, since, until, lane))
    assert_same(ours.model_dump(), bot)


async def test_an_older_epoch_does_not_count_once_the_bot_was_reset(
    wolfbet: ModuleType,
    tmp_path: Path,
    pg: asyncpg.Connection,
    service: str,
    db_session: AsyncSession,
) -> None:
    build(wolfbet, tmp_path / LIVE_DB, SESSIONS)
    await copy_all(pg, service, tmp_path)
    remove_db(tmp_path)
    build(wolfbet, tmp_path / LIVE_DB, SESSIONS[3:])
    bot = run_bot(tmp_path)
    await copy_all(pg, service, tmp_path)
    epochs = await pg.fetchval("SELECT count(*) FROM db_epochs WHERE service = $1", service)
    assert epochs == 2

    ours = await history_report(db_session, await window(db_session, service))
    assert_same(ours.model_dump(), bot)
    assert ours.lifetime.sessions_total == 3


async def test_charts_cover_the_same_sessions_and_bets(
    wolfbet: ModuleType,
    tmp_path: Path,
    pg: asyncpg.Connection,
    service: str,
    db_session: AsyncSession,
) -> None:
    build(wolfbet, tmp_path / LIVE_DB, SESSIONS)
    await copy_all(pg, service, tmp_path)

    charts = await chart_data(
        db_session, await window(db_session, service), await epoch_view(db_session, service)
    )
    assert len(charts.profit_bins) == BINS
    assert sum(b.sessions for b in charts.profit_bins) == len(SESSIONS)
    assert charts.profit_bins[0].lo == pytest.approx(-0.0003)
    assert charts.profit_bins[-1].hi == pytest.approx(0.3)
    assert sum(b.sessions for b in charts.duration_bins) == len(SESSIONS)
    assert charts.duration_bins[0].lo_sec == 60
    assert sum(h.bets for h in charts.by_hour) == len(SESSIONS) * BETS_PER_SESSION
    assert {h.hour for h in charts.by_hour} == {0, 8, 9, 10, 11, 23}
    assert [d.day for d in charts.by_day] == ["2026-01-05", "2026-01-06", "2026-01-07"]

    lane_two = await chart_data(
        db_session, await window(db_session, service, lane=2), await epoch_view(db_session, service)
    )
    assert sum(b.sessions for b in lane_two.profit_bins) == 3
    assert sum(h.bets for h in lane_two.by_hour) == 3 * BETS_PER_SESSION


async def test_a_key_counts_only_its_own_sessions_and_unrecorded_ones_count_as_main(
    wolfbet: ModuleType,
    tmp_path: Path,
    pg: asyncpg.Connection,
    service: str,
    db_session: AsyncSession,
) -> None:
    live = tmp_path / LIVE_DB
    build(wolfbet, live, SESSIONS)
    conn = sqlite3.connect(live)
    conn.execute("UPDATE sessions SET key_ref = 'lakai' WHERE lane = 2")
    conn.execute("UPDATE sessions SET key_ref = 'default' WHERE lane = 1")
    conn.commit()
    conn.close()
    await copy_all(pg, service, tmp_path)
    copied = await pg.fetch(
        "SELECT lane, key_ref FROM sessions WHERE service = $1 ORDER BY src_id", service
    )
    assert [(r["lane"], r["key_ref"]) for r in copied][:3] == [
        (1, "default"),
        (2, "lakai"),
        (3, None),
    ]

    view = await epoch_view(db_session, service)
    lakai = await window(db_session, service, key="lakai")
    report = await history_report(db_session, lakai)
    assert report.lifetime.sessions_total == 3
    charts = await chart_data(db_session, lakai, view)
    assert sum(h.bets for h in charts.by_hour) == 3 * BETS_PER_SESSION
    assert sum(b.sessions for b in charts.profit_bins) == 3

    main = await history_report(db_session, await window(db_session, service, key="default"))
    assert main.lifetime.sessions_total == 3
    stats = await session_stats(db_session, await window(db_session, service, key="lakai"))
    assert stats.max_bets == 200


STATS_KEYS = (
    "best_win_streak", "best_loss_streak", "max_bets", "avg_bets", "max_wins", "avg_wins",
    "max_losses", "avg_losses", "peak_balance", "avg_bps", "avg_bpm", "peak_bps", "peak_bpm",
)  # fmt: skip


@pytest.mark.parametrize(("since", "until", "lane"), WINDOWS[:4])
async def test_session_stats_equal_the_bots_stats_totals(
    wolfbet: ModuleType,
    tmp_path: Path,
    pg: asyncpg.Connection,
    service: str,
    db_session: AsyncSession,
    since: str | None,
    until: str | None,
    lane: int | None,
) -> None:
    path = tmp_path / LIVE_DB
    build(wolfbet, path, SESSIONS)
    bot_db = BotDb(wolfbet, path)
    bot_db.conn.execute(
        "UPDATE sessions SET max_win_streak = wins % 7, max_loss_streak = losses % 5,"
        " highest_balance = 1.0 + id / 8.0, bets_per_second = id * 1.5,"
        " bets_per_minute = id * 90.0, peak_bps = id * 2.0, peak_bpm = id * 100.0"
    )
    bot_db.conn.commit()
    bot_db.close()
    totals = run_bot(tmp_path, *flags(since, until, lane), command="--stats")["totals"]
    await copy_all(pg, service, tmp_path)

    ours = await session_stats(db_session, await window(db_session, service, since, until, lane))
    assert_same(ours.model_dump(), {key: totals[key] for key in STATS_KEYS}, "stats")
