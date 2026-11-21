"""The tailer copies WolfBet's own SQLite files into Timescale: once, read-only, in short reads."""

import asyncio
import hashlib
import os
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import asyncpg  # type: ignore[import-untyped]  # ships no type hints
import pytest

from app.ingest import tailer as tailer_module
from app.ingest.tailer import ARCHIVE_GLOB, LIVE_DB, Tailer
from app.settings import Settings
from tests.wolfbet_db import BotDb, needs_wolfbet

pytestmark = needs_wolfbet


@pytest.fixture
def make_tailer(pg: asyncpg.Connection, service: str, tmp_path: Path) -> Callable[..., Any]:
    async def make(zone: str = "UTC", directory: Path = tmp_path) -> Tailer:
        tailer = Tailer(pg, service, directory, zone)
        await tailer.prepare()
        return tailer

    return make


async def drain(tailer: Tailer) -> None:
    for _ in range(1000):
        if not await tailer.step():
            return
    raise AssertionError("tailer never caught up")


async def count(pg: asyncpg.Connection, service: str, where: str = "true") -> int:
    value: int = await pg.fetchval(
        f"SELECT count(*) FROM bets WHERE service = $1 AND {where}",  # noqa: S608  # nosec B608: test-only fixed filters
        service,
    )
    return value


T0 = datetime(2026, 1, 5, 10, 0)


async def test_copies_every_bet_and_session_once(
    wolfbet: ModuleType, tmp_path: Path, pg: asyncpg.Connection, service: str, make_tailer: Any
) -> None:
    bot = BotDb(wolfbet, tmp_path / LIVE_DB)
    one, two = bot.session(lane=1), bot.session(lane=2)
    bot.bets(one, 30, T0)
    bot.bets(two, 20, T0)
    await drain(await make_tailer())
    await drain(await make_tailer())
    assert await count(pg, service) == 50
    assert await count(pg, service, "lane = 2") == 20
    lanes = await pg.fetch(
        "SELECT src_id, lane FROM sessions WHERE service = $1 ORDER BY src_id", service
    )
    assert [tuple(r) for r in lanes] == [(one, 1), (two, 2)]
    assert await pg.fetchval("SELECT count(*) FROM db_epochs WHERE service = $1", service) == 1
    bot.close()


async def test_reset_starts_a_new_epoch_and_finishes_the_archive(
    wolfbet: ModuleType, tmp_path: Path, pg: asyncpg.Connection, service: str, make_tailer: Any
) -> None:
    live = tmp_path / LIVE_DB
    bot = BotDb(wolfbet, live)
    first = bot.session(started="2026-01-04T09:00:00")
    bot.bets(first, 10, T0)
    tailer = await make_tailer()
    await drain(tailer)
    bot.bets(first, 5, T0 + timedelta(minutes=5))
    bot.close()
    live.rename(tmp_path / ARCHIVE_GLOB.replace("*", "20260105-120000"))
    fresh = BotDb(wolfbet, live)
    fresh.bets(fresh.session(started="2026-01-06T09:00:00"), 7, T0 + timedelta(days=1))
    await drain(tailer)
    epochs = await pg.fetch(
        "SELECT e.epoch_id, count(b.src_id) AS n, max(b.src_id) AS top FROM db_epochs e"
        " JOIN bets b USING (service, epoch_id) WHERE e.service = $1"
        " GROUP BY e.epoch_id ORDER BY e.epoch_id",
        service,
    )
    assert [(r["n"], r["top"]) for r in epochs] == [(15, 15), (7, 7)]
    fresh.close()


@pytest.mark.parametrize("fresh_db", [False, True], ids=["no-live-file", "empty-live-file"])
async def test_a_reset_is_seen_before_the_bot_starts_its_next_session(
    wolfbet: ModuleType,
    tmp_path: Path,
    pg: asyncpg.Connection,
    service: str,
    make_tailer: Any,
    fresh_db: bool,
) -> None:
    live = tmp_path / LIVE_DB
    bot = BotDb(wolfbet, live)
    bot.bets(bot.session(started="2026-01-04T09:00:00"), 10, T0)
    tailer = await make_tailer()
    await drain(tailer)
    bot.close()
    archive = tmp_path / ARCHIVE_GLOB.replace("*", "20260105-120000")
    live.rename(archive)
    fresh = BotDb(wolfbet, live) if fresh_db else None
    await drain(tailer)
    paths = await pg.fetch("SELECT source_path FROM db_epochs WHERE service = $1", service)
    assert [r["source_path"] for r in paths] == [str(archive)]
    assert await count(pg, service) == 10
    if fresh is not None:
        fresh.close()


async def test_purged_rows_do_not_break_the_watermark(
    wolfbet: ModuleType, tmp_path: Path, pg: asyncpg.Connection, service: str, make_tailer: Any
) -> None:
    bot = BotDb(wolfbet, tmp_path / LIVE_DB)
    session = bot.session()
    bot.bets(session, 40, T0)
    tailer = await make_tailer()
    await drain(tailer)
    bot.conn.execute("DELETE FROM bets WHERE id <= 30")
    bot.conn.commit()
    bot.bets(session, 5, T0 + timedelta(hours=1))
    await drain(tailer)
    assert await count(pg, service) == 45
    bot.close()


async def test_naive_times_are_read_in_the_host_zone(
    wolfbet: ModuleType, tmp_path: Path, pg: asyncpg.Connection, service: str, make_tailer: Any
) -> None:
    bot = BotDb(wolfbet, tmp_path / LIVE_DB)
    bot.bets(bot.session(started="2026-01-05T12:30:00"), 1, datetime(2026, 1, 5, 13, 0))
    await drain(await make_tailer("Europe/Berlin"))
    ts = await pg.fetchval("SELECT ts FROM bets WHERE service = $1", service)
    started = await pg.fetchval("SELECT started_at FROM sessions WHERE service = $1", service)
    assert ts == datetime(2026, 1, 5, 12, 0, tzinfo=UTC)
    assert started == datetime(2026, 1, 5, 11, 30, tzinfo=UTC)
    bot.close()


async def test_every_sqlite_read_is_short(
    wolfbet: ModuleType, tmp_path: Path, pg: asyncpg.Connection, service: str, make_tailer: Any
) -> None:
    bot = BotDb(wolfbet, tmp_path / LIVE_DB)
    bot.bets(bot.session(), 20_000, T0)
    tailer = await make_tailer()
    await drain(tailer)
    assert await count(pg, service) == 20_000
    assert tailer.max_read_seconds < 0.2
    assert tailer.lag.rows == 0
    bot.close()


async def test_wal_stays_bounded_while_the_bot_writes(
    wolfbet: ModuleType, tmp_path: Path, pg: asyncpg.Connection, service: str, make_tailer: Any
) -> None:
    bot = BotDb(wolfbet, tmp_path / LIVE_DB)
    session = bot.session()
    bot.bets(session, 1, T0)
    wal = Path(f"{bot.path}-wal")
    largest = 0
    rounds = 50

    def write() -> None:
        nonlocal largest
        for i in range(rounds):
            bot.bets(session, 50, T0 + timedelta(minutes=i + 1))
            largest = max(largest, wal.stat().st_size if wal.exists() else 0)
            time.sleep(0.1)

    tailer = await make_tailer()
    stop = asyncio.Event()
    running = asyncio.create_task(tailer.run(stop))
    writer = threading.Thread(target=write)
    writer.start()
    await asyncio.to_thread(writer.join)
    await asyncio.sleep(1)
    stop.set()
    await running
    await drain(tailer)
    assert await count(pg, service) == 1 + rounds * 50
    assert largest < 8 * 1024 * 1024
    assert tailer.max_read_seconds < 0.2
    bot.close()


async def test_changed_session_totals_are_synced(
    wolfbet: ModuleType,
    tmp_path: Path,
    pg: asyncpg.Connection,
    service: str,
    make_tailer: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = BotDb(wolfbet, tmp_path / LIVE_DB)
    session = bot.session()
    tailer = await make_tailer()
    await drain(tailer)
    bot.conn.execute(
        "UPDATE sessions SET total_bets = 99, profit = 1.5, ended_at = '2026-01-05T11:00:00'"
        " WHERE id = ?",
        (session,),
    )
    bot.conn.commit()
    monkeypatch.setattr(tailer_module, "SESSION_SYNC_SECONDS", 0.0)
    await drain(tailer)
    row = await pg.fetchrow(
        "SELECT total_bets, profit, ended_at FROM sessions WHERE service = $1", service
    )
    assert tuple(row) == (99, 1.5, datetime(2026, 1, 5, 11, 0, tzinfo=UTC))
    bot.close()


def _fingerprint(path: Path) -> tuple[str, int]:
    return hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns


async def test_closed_archive_on_read_only_directory_is_copied_untouched(
    wolfbet: ModuleType, tmp_path: Path, pg: asyncpg.Connection, service: str, make_tailer: Any
) -> None:
    data = tmp_path / "botdata"
    data.mkdir()
    archive = data / ARCHIVE_GLOB.replace("*", "20260101-000000")
    bot = BotDb(wolfbet, archive)
    bot.bets(bot.session(), 12, T0)
    bot.close()
    before = _fingerprint(archive)
    assert not Path(f"{archive}-wal").exists()  # noqa: ASYNC240  # setup check, before the tailer runs
    data.chmod(0o555)
    try:
        await drain(await make_tailer(directory=data))
    finally:
        data.chmod(0o755)
    assert await count(pg, service) == 12
    assert _fingerprint(archive) == before
    assert sorted(os.listdir(data)) == [archive.name]


async def test_old_backfill_reaches_the_minute_rollup(
    wolfbet: ModuleType,
    tmp_path: Path,
    pg: asyncpg.Connection,
    service: str,
    make_tailer: Any,
    migrated: Settings,
) -> None:
    bot = BotDb(wolfbet, tmp_path / LIVE_DB)
    old = datetime.now() - timedelta(days=3)
    bot.bets(bot.session(started=old.isoformat()), 3, old)
    await drain(await make_tailer())
    owner = await asyncpg.connect(
        host=migrated.postgres_host,
        port=migrated.postgres_port,
        user=migrated.postgres_user,
        password=migrated.postgres_password.get_secret_value(),
        database=migrated.postgres_db,
    )
    try:
        job = await owner.fetchval(
            "SELECT job_id FROM timescaledb_information.jobs WHERE hypertable_name = 'bets_1m'"
        )
        await owner.execute(f"CALL run_job({int(job)})")
        schema, table = await owner.fetchrow(
            "SELECT materialization_hypertable_schema, materialization_hypertable_name"
            " FROM timescaledb_information.continuous_aggregates WHERE view_name = 'bets_1m'"
        )
        refreshed = await owner.fetchval(
            f'SELECT sum(bets) FROM "{schema}"."{table}" WHERE service = $1',  # noqa: S608  # nosec B608: names from the catalog
            service,
        )
    finally:
        await owner.close()
    assert refreshed == 3
    bot.close()
