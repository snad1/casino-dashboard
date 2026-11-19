"""Copy a WolfBet bot's SQLite history into Timescale, without ever slowing the bot down.

The bot's database is only ever opened read-only, and every read is one short transaction
(a batch of at most 5 000 bets), so the bot's WAL checkpoints are never held back.

A database file's lifetime is an epoch: its inode plus its first session's start time.
`wolfctl reset` moves the file to `.wolfbet_autobot-<timestamp>.db` (same inode, so the same
epoch) and starts a fresh one whose ids restart at 1 (a new epoch). The watermark is the
highest bet id already in Timescale for an epoch; SQLite ids never repeat within a file,
so purged bet rows below it change nothing. Rows are inserted with `ON CONFLICT DO NOTHING`,
so a re-read after a crash or restart never duplicates anything.

The bot writes naive local times; they are read in the host's zone (`HOST_TZ`).
"""

import asyncio
import logging
import os
import sqlite3
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg  # type: ignore[import-untyped]  # ships no type hints

LIVE_DB = ".wolfbet_autobot.db"
ARCHIVE_GLOB = ".wolfbet_autobot-*.db"
BATCH_SIZE = 5000
SESSION_SYNC_SECONDS = 5.0
LAG_LOG_SECONDS = 30.0
BUSY_TIMEOUT_MS = 2000

BET_COLUMNS = (
    "service",
    "epoch_id",
    "src_id",
    "ts",
    "session_src_id",
    "lane",
    "amount",
    "multiplier",
    "rule",
    "bet_value",
    "result_value",
    "state",
    "profit",
    "balance_after",
)
SESSION_TEXT = ("currency", "game", "strategy")
SESSION_INT = ("total_bets", "wins", "losses", "max_win_streak", "max_loss_streak")
SESSION_REAL = (
    "base_bet",
    "multiplier",
    "profit",
    "wagered",
    "start_balance",
    "end_balance",
    "highest_balance",
    "lowest_balance",
    "highest_win",
    "biggest_loss",
    "bets_per_minute",
    "bets_per_second",
    "peak_bps",
    "low_bps",
    "peak_bpm",
    "low_bpm",
)
SESSION_COLUMNS = ("lane", "started_at", "ended_at", *SESSION_TEXT, *SESSION_INT, *SESSION_REAL)

logger = logging.getLogger("ingest")


class SourceUnavailable(Exception):
    """The database cannot be read right now; the next poll tries again."""


@dataclass(frozen=True)
class Epoch:
    inode: int
    first_started_at: datetime


@dataclass
class Lag:
    """How far Timescale is behind the live database."""

    rows: int = 0
    seconds: float = 0.0


@dataclass
class Source:
    """One database file and how far it has been copied."""

    path: Path
    archived: bool
    epoch: Epoch | None = None
    epoch_id: int = 0
    watermark: int = 0
    max_session_id: int = 0
    open_sessions: set[int] = field(default_factory=set)
    last_session_sync: float = 0.0


def _float(value: Any) -> float | None:
    return None if value is None else float(value)


def _int(value: Any) -> int | None:
    return None if value is None else int(value)


class Tailer:
    """Copies the live database and every archive of one service, one batch per call."""

    def __init__(self, db: asyncpg.Connection, service: str, data_dir: Path, host_tz: str) -> None:
        self.db = db
        self.service = service
        self.data_dir = data_dir
        self.zone = ZoneInfo(host_tz)
        self.live: Source | None = None
        self.archives: list[Source] = []
        self.lag = Lag()
        self.max_read_seconds = 0.0
        self._unavailable_logged = False

    async def prepare(self) -> None:
        """Session setup for this connection: history may lag a crash by a moment, never the
        bot, and the temp table feeds COPY."""
        await self.db.execute("SET synchronous_commit = off")
        await self.db.execute(
            "CREATE TEMP TABLE IF NOT EXISTS bets_in (LIKE bets) ON COMMIT DELETE ROWS"
        )
        await self._rescan_archives()

    async def step(self) -> bool:
        """Copy one batch from the live database, or else from the oldest archive.

        Returns True while there is more to copy, False when everything is caught up.
        """
        busy = False
        live = await self._live_source()
        if live is not None:
            busy = await self._copy_bets(live)
            if time.monotonic() - live.last_session_sync >= SESSION_SYNC_SECONDS:
                await self._sync_sessions(live)
        if not busy and self.archives:
            archive = self.archives[0]
            if not await self._copy_bets(archive):
                await self._sync_sessions(archive)
                self.archives.pop(0)
            busy = True
        return busy

    async def run(self, stop: asyncio.Event, idle_seconds: float = 0.25) -> None:
        """Step until stop is set, sleeping briefly whenever everything is caught up."""
        logged = time.monotonic()
        while not stop.is_set():
            if time.monotonic() - logged >= LAG_LOG_SECONDS:
                logger.info("lag: %s rows, %.1f s", self.lag.rows, self.lag.seconds)
                logged = time.monotonic()
            if not await self.step():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=idle_seconds)
                except TimeoutError:
                    pass

    async def _live_source(self) -> Source | None:
        path = self.data_dir / LIVE_DB
        try:
            epoch = await self._probe(Source(path, archived=False))
        except SourceUnavailable as error:
            if not self._unavailable_logged:
                logger.info("live database not readable yet: %s", error)
                self._unavailable_logged = True
            return None
        self._unavailable_logged = False
        if epoch is None:
            return None
        if self.live is None or self.live.epoch != epoch:
            self.live = await self._start(Source(path, archived=False), epoch)
            await self._rescan_archives()
        return self.live

    async def _rescan_archives(self) -> None:
        pending = {a.path for a in self.archives}
        for path in sorted(self.data_dir.glob(ARCHIVE_GLOB)):
            if path in pending:
                continue
            source = Source(path, archived=True)
            try:
                epoch = await self._probe(source)
            except SourceUnavailable as error:
                logger.warning("skipping archive %s: %s", path.name, error)
                continue
            if epoch is not None:
                self.archives.append(await self._start(source, epoch))

    async def _probe(self, source: Source) -> Epoch | None:
        found = await self._read(source, lambda c: _probe_epoch(c, source.path))
        if found is None:
            return None
        inode, first_started_at = found
        return Epoch(inode, self._aware(first_started_at))

    async def _start(self, source: Source, epoch: Epoch) -> Source:
        source.epoch = epoch
        source.epoch_id = await self.db.fetchval(
            "INSERT INTO db_epochs (service, source_path, inode, first_started_at)"
            " VALUES ($1, $2, $3, $4)"
            " ON CONFLICT (service, inode, first_started_at)"
            " DO UPDATE SET source_path = EXCLUDED.source_path RETURNING epoch_id",
            self.service,
            str(source.path),
            epoch.inode,
            epoch.first_started_at,
        )
        source.watermark = await self.db.fetchval(
            "SELECT coalesce(max(src_id), 0) FROM bets WHERE service = $1 AND epoch_id = $2",
            self.service,
            source.epoch_id,
        )
        logger.info(
            "copying %s as epoch %s from bet id %s",
            source.path.name,
            source.epoch_id,
            source.watermark,
        )
        return source

    async def _copy_bets(self, source: Source) -> bool:
        rows, newest_id, newest_ts = await self._read(
            source, lambda c: _read_bets(c, source.watermark)
        )
        if rows:
            records = [self._bet_record(source, row) for row in rows]
            async with self.db.transaction():
                await self.db.copy_records_to_table("bets_in", records=records, columns=BET_COLUMNS)
                columns = ", ".join(BET_COLUMNS)
                await self.db.execute(
                    f"INSERT INTO bets ({columns}) SELECT {columns} FROM bets_in"  # noqa: S608  # nosec B608: fixed column names
                    " ON CONFLICT DO NOTHING"
                )
            source.watermark = rows[-1][0]
        if not source.archived:
            behind = max((newest_id or 0) - source.watermark, 0)
            seconds = 0.0
            if behind and rows and newest_ts:
                gap = self._aware(newest_ts) - self._aware(rows[-1][1])
                seconds = max(gap.total_seconds(), 0.0)
            self.lag = Lag(rows=behind, seconds=seconds)
        return len(rows) == BATCH_SIZE

    def _bet_record(self, source: Source, row: Sequence[Any]) -> tuple[Any, ...]:
        src_id, ts, session_id, lane, amount, multiplier, rule, bet_value, result, state = row[:10]
        profit, balance = row[10:12]
        return (
            self.service,
            source.epoch_id,
            src_id,
            self._time(ts),
            _int(session_id),
            int(lane),
            _float(amount),
            _float(multiplier),
            rule,
            _float(bet_value),
            _float(result),
            state,
            _float(profit),
            _float(balance),
        )

    async def _sync_sessions(self, source: Source) -> None:
        """Upsert new sessions plus every session that was or is still open."""
        while True:
            rows = await self._read(
                source,
                lambda c: _read_sessions(c, source.max_session_id, source.open_sessions),
            )
            if rows:
                await self._upsert_sessions(source, rows)
                source.max_session_id = max(source.max_session_id, max(r["id"] for r in rows))
                source.open_sessions = {r["id"] for r in rows if r["ended_at"] is None}
            if len(rows) < BATCH_SIZE:
                break
        source.last_session_sync = time.monotonic()

    async def _upsert_sessions(self, source: Source, rows: list[dict[str, Any]]) -> None:
        columns = ("service", "epoch_id", "src_id", *SESSION_COLUMNS)
        values = ", ".join(f"${i}" for i in range(1, len(columns) + 1))
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in SESSION_COLUMNS)
        statement = (
            f"INSERT INTO sessions ({', '.join(columns)}) VALUES ({values})"  # noqa: S608  # nosec B608: fixed column names
            f" ON CONFLICT (service, epoch_id, src_id) DO UPDATE SET {updates}"
        )
        await self.db.executemany(statement, [self._session_record(source, row) for row in rows])

    def _session_record(self, source: Source, row: dict[str, Any]) -> tuple[Any, ...]:
        return (
            self.service,
            source.epoch_id,
            row["id"],
            int(row["lane"] or 1),
            self._time(row["started_at"]),
            self._time(row["ended_at"]),
            *(row[c] for c in SESSION_TEXT),
            *(_int(row[c]) for c in SESSION_INT),
            *(_float(row[c]) for c in SESSION_REAL),
        )

    def _aware(self, value: str) -> datetime:
        return aware(value, self.zone)

    def _time(self, value: str | None) -> datetime | None:
        return aware(value, self.zone) if value else None

    async def _read[T](self, source: Source, query: Callable[[sqlite3.Connection], T]) -> T:
        result, seconds = await asyncio.to_thread(read_once, source, query)
        self.max_read_seconds = max(self.max_read_seconds, seconds)
        return result


def aware(value: str, zone: ZoneInfo) -> datetime:
    """A time the bot wrote: naive values are local time in `zone`."""
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=zone)


def _connect(source: Source) -> sqlite3.Connection:
    """Open read-only. A closed archive on a read-only mount has no `-shm` and cannot get
    one, so it is opened as immutable; it never changes again. The live file without a
    `-wal` means the bot is stopped: there is nothing new to read, so wait for it."""
    path = source.path
    if not path.exists():
        raise SourceUnavailable(f"{path.name} does not exist")
    wal_present = path.with_name(path.name + "-wal").exists()
    dir_writable = os.access(path.parent, os.W_OK)
    if wal_present or dir_writable:
        uri = f"{path.as_uri()}?mode=ro"
    elif source.archived:
        uri = f"{path.as_uri()}?mode=ro&immutable=1"
    else:
        raise SourceUnavailable(f"{path.name} is closed and its directory is read-only")
    try:
        connection = sqlite3.connect(
            uri, uri=True, isolation_level=None, timeout=BUSY_TIMEOUT_MS / 1000
        )
    except sqlite3.Error as error:
        raise SourceUnavailable(str(error)) from None
    connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    return connection


def read_once[T](source: Source, query: Callable[[sqlite3.Connection], T]) -> tuple[T, float]:
    """Run one query in one short read transaction; returns its result and duration."""
    connection = _connect(source)
    try:
        started = time.perf_counter()
        connection.execute("BEGIN")
        try:
            result = query(connection)
        finally:
            connection.execute("COMMIT")
        return result, time.perf_counter() - started
    except sqlite3.Error as error:
        raise SourceUnavailable(str(error)) from None
    finally:
        connection.close()


def _probe_epoch(connection: sqlite3.Connection, path: Path) -> tuple[int, str] | None:
    first = connection.execute("SELECT min(started_at) FROM sessions").fetchone()[0]
    if not first:
        return None
    return path.stat().st_ino, first


def _read_bets(
    connection: sqlite3.Connection, after_id: int
) -> tuple[list[tuple[Any, ...]], int | None, str | None]:
    rows = connection.execute(
        "SELECT b.id, b.timestamp, b.session_id, coalesce(s.lane, 1), b.amount, b.multiplier,"
        " b.rule, b.bet_value, b.result_value, b.state, b.profit, b.balance_after"
        " FROM bets b LEFT JOIN sessions s ON s.id = b.session_id"
        " WHERE b.id > ? ORDER BY b.id LIMIT ?",
        (after_id, BATCH_SIZE),
    ).fetchall()
    newest = connection.execute(
        "SELECT id, timestamp FROM bets ORDER BY id DESC LIMIT 1"
    ).fetchone()
    newest_id, newest_ts = newest if newest else (None, None)
    return rows, newest_id, newest_ts


def _read_sessions(
    connection: sqlite3.Connection, after_id: int, still_open: set[int]
) -> list[dict[str, Any]]:
    """Archives made before a column existed lack it; it reads as NULL."""
    present = {row[1] for row in connection.execute("PRAGMA table_info(sessions)")}
    selected = ", ".join(c if c in present else f"NULL AS {c}" for c in SESSION_COLUMNS)
    if "lane" not in present:
        selected = selected.replace("NULL AS lane", "1 AS lane")
    marks = ", ".join("?" * len(still_open))
    reopen = f" OR id IN ({marks})" if still_open else ""
    cursor = connection.execute(
        f"SELECT id, {selected} FROM sessions"  # noqa: S608  # nosec B608: fixed column names
        f" WHERE id > ? OR ended_at IS NULL{reopen} ORDER BY id LIMIT ?",
        (after_id, *sorted(still_open), BATCH_SIZE),
    )
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
