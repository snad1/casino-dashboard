"""WolfBet's own SQLite schema and an app-role Timescale connection, for ingest tests.

The SQLite files are built with WolfBet's own `init_db` and `migrate_db`, loaded from the
sibling `wolfbet` checkout, so a schema change there breaks these tests instead of prod.
"""

import importlib.util
import sqlite3
import sys
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType

import asyncpg  # type: ignore[import-untyped]  # ships no type hints
import pytest

from app.settings import Settings
from tests.conftest import APP_ROLE, APP_ROLE_PASSWORD

WORKSPACE = Path(__file__).resolve().parents[3]
WOLFBET_DATABASE = WORKSPACE / "wolfbet" / "core" / "database.py"
SHARED_PACKAGE = WORKSPACE / "casino-shared"

needs_wolfbet = pytest.mark.skipif(
    not (WOLFBET_DATABASE.is_file() and SHARED_PACKAGE.is_dir()),
    reason="needs the wolfbet and casino-shared checkouts next to casino-dashboard",
)


def _load(name: str, path: Path, package_dir: Path | None = None) -> ModuleType:
    locations = [str(package_dir)] if package_dir else None
    spec = importlib.util.spec_from_file_location(name, path, submodule_search_locations=locations)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def wolfbet() -> ModuleType:
    """WolfBet's database module, used only to create files the way the bot does."""
    if "shared" not in sys.modules:
        _load("shared", SHARED_PACKAGE / "__init__.py", SHARED_PACKAGE)
    return _load("wolfbet_database", WOLFBET_DATABASE)


class BotDb:
    """Writes the way the bot does: WAL mode, naive local times."""

    def __init__(self, wolfbet: ModuleType, path: Path) -> None:
        self.path = path
        wolfbet.init_db(str(path))
        wolfbet.migrate_db(str(path))
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")

    def session(self, lane: int = 1, started: str = "2026-01-05T10:00:00") -> int:
        cursor = self.conn.execute(
            "INSERT INTO sessions (started_at, currency, total_bets, profit, lane)"
            " VALUES (?, 'btc', 0, 0, ?)",
            (started, lane),
        )
        self.conn.commit()
        return int(cursor.lastrowid or 0)

    def bets(self, session_id: int, count: int, start: datetime, profit: float = 0.5) -> None:
        self.conn.executemany(
            "INSERT INTO bets (session_id, timestamp, amount, multiplier, rule, bet_value,"
            " result_value, state, profit, balance_after) VALUES (?,?,1.0,2.0,'over',50,60,?,?,?)",
            [
                (
                    session_id,
                    (start + timedelta(seconds=i)).isoformat(),
                    "win" if profit > 0 else "loss",
                    profit,
                    100.0 + i,
                )
                for i in range(count)
            ],
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()


@pytest.fixture
async def pg(migrated: Settings) -> AsyncIterator[asyncpg.Connection]:
    """Connected as the role ingest uses in production, so the grants are proven too."""
    connection = await asyncpg.connect(
        host=migrated.postgres_host,
        port=migrated.postgres_port,
        user=APP_ROLE,
        password=APP_ROLE_PASSWORD,
        database=migrated.postgres_db,
    )
    yield connection
    await connection.close()


@pytest.fixture
async def service(pg: asyncpg.Connection) -> AsyncIterator[str]:
    name = f"svc-{uuid.uuid4().hex[:8]}"
    yield name
    for table in ("bets", "sessions", "reconcile_runs", "db_epochs"):
        await pg.execute(f"DELETE FROM {table} WHERE service = $1", name)  # noqa: S608  # nosec B608: fixed table names
