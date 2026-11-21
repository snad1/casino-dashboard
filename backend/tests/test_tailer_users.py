"""The Telegram bot's per-user databases are found, copied once each and tagged with their user."""

import os
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import asyncpg  # type: ignore[import-untyped]  # ships no type hints
import pytest

from app.ingest import tailer as tailer_module
from app.ingest.tailer import USER_DB, UserTailers, user_dirs
from tests.wolfbet_db import BotDb, needs_wolfbet

pytestmark = needs_wolfbet

T0 = datetime(2026, 1, 5, 10, 0)


def _user(wolfbet: ModuleType, data: Path, uid: int | str, bets: int) -> BotDb:
    folder = data / str(uid)
    folder.mkdir(parents=True)
    bot = BotDb(wolfbet, folder / USER_DB)
    bot.bets(bot.session(), bets, T0)
    return bot


async def drain(tailers: UserTailers) -> None:
    for _ in range(1000):
        if not await tailers.step():
            return
    raise AssertionError("ingest never caught up")


async def per_user(pg: asyncpg.Connection, service: str) -> dict[int, int]:
    rows = await pg.fetch(
        "SELECT e.tg_uid, count(*) FROM bets b JOIN db_epochs e USING (epoch_id)"
        " WHERE b.service = $1 GROUP BY e.tg_uid",
        service,
    )
    return {row[0]: row[1] for row in rows}


async def test_two_users_land_under_their_own_epochs(
    wolfbet: ModuleType, tmp_path: Path, pg: asyncpg.Connection, service: str
) -> None:
    alice, bob = _user(wolfbet, tmp_path, 42, 30), _user(wolfbet, tmp_path, 43, 20)
    await drain(UserTailers(pg, service, tmp_path, "UTC"))
    await drain(UserTailers(pg, service, tmp_path, "UTC"))
    assert await per_user(pg, service) == {42: 30, 43: 20}
    sessions = await pg.fetch(
        "SELECT e.tg_uid, s.src_id FROM sessions s JOIN db_epochs e USING (epoch_id)"
        " WHERE s.service = $1 ORDER BY e.tg_uid",
        service,
    )
    assert [tuple(row) for row in sessions] == [(42, 1), (43, 1)]
    alice.close()
    bob.close()


async def test_a_new_user_is_found_on_the_next_scan(
    wolfbet: ModuleType, tmp_path: Path, pg: asyncpg.Connection, service: str
) -> None:
    alice = _user(wolfbet, tmp_path, 42, 5)
    tailers = UserTailers(pg, service, tmp_path, "UTC", discover_seconds=3600)
    await drain(tailers)
    bob = _user(wolfbet, tmp_path, 44, 7)
    await drain(tailers)
    assert await per_user(pg, service) == {42: 5}
    tailers.discover_seconds = 0.0
    await drain(tailers)
    assert await per_user(pg, service) == {42: 5, 44: 7}
    alice.close()
    bob.close()


def test_only_numeric_real_directories_with_a_real_database_count(
    wolfbet: ModuleType, tmp_path: Path
) -> None:
    data = tmp_path / "tg"
    _user(wolfbet, data, 42, 1).close()
    _user(wolfbet, data, "notes", 1).close()
    _user(wolfbet, data, "042", 1).close()
    elsewhere = tmp_path / "elsewhere"
    _user(wolfbet, elsewhere, 7, 1).close()
    (data / "43").symlink_to(elsewhere / "7")
    (data / "44").mkdir()
    (data / "44" / USER_DB).symlink_to(elsewhere / "7" / USER_DB)
    (data / "45").mkdir()
    (data / "update.log").write_text("x")
    assert user_dirs(data) == [(42, data / "42")]
    assert user_dirs(tmp_path / "missing") == []


async def test_an_idle_users_closed_database_on_a_read_only_mount_is_read(
    wolfbet: ModuleType, tmp_path: Path, pg: asyncpg.Connection, service: str
) -> None:
    _user(wolfbet, tmp_path, 42, 12).close()
    folder = tmp_path / "42"
    assert not (folder / f"{USER_DB}-wal").exists()
    before = sorted(os.listdir(folder))
    folder.chmod(0o555)
    try:
        await drain(UserTailers(pg, service, tmp_path, "UTC"))
    finally:
        folder.chmod(0o755)
    assert await per_user(pg, service) == {42: 12}
    assert sorted(os.listdir(folder)) == before


async def test_a_read_that_raced_a_write_is_thrown_away_and_retried(
    wolfbet: ModuleType,
    tmp_path: Path,
    pg: asyncpg.Connection,
    service: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _user(wolfbet, tmp_path, 42, 9).close()
    folder = tmp_path / "42"
    real_stamp = tailer_module._stamp
    calls = {"n": 0}

    def stamp(path: Path) -> Any:
        calls["n"] += 1
        size, mtime, wal = real_stamp(path)
        return (size, mtime + 1, wal) if calls["n"] == 2 else (size, mtime, wal)

    monkeypatch.setattr(tailer_module, "_stamp", stamp)
    folder.chmod(0o555)
    try:
        tailers = UserTailers(pg, service, tmp_path, "UTC")
        await tailers.step()
        assert await per_user(pg, service) == {}
        await drain(tailers)
    finally:
        folder.chmod(0o755)
    assert await per_user(pg, service) == {42: 9}
