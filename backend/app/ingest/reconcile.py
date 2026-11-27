"""Check that Timescale holds exactly the bets the bot's SQLite has, session by session.

Each run compares `count(*)`, `sum(profit)` and `sum(amount)` per session for bet ids in
`(lo, hi]` of the live epoch, where `hi` is the highest id already copied, so bets still on
their way are never reported missing. A quick run starts where the previous run stopped
(and rechecks what it flagged); a full run, once a day, starts at 0.

Both sides sum exact decimals: SQLite floats are taken at their shortest repr, Postgres
floats through `::text`, which prints the same shortest form. The bot purges its oldest bets
first, so when SQLite holds fewer rows than Timescale and the remaining tail matches, the
session is reported as purged upstream, not as a mismatch.

Ingest lagging more than half the bot's purge window means unread bets may be deleted
before they are copied; that sets `lag_alert`.
"""

import asyncio
import json
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg  # type: ignore[import-untyped]  # ships no type hints

from app import registry
from app.ingest.tailer import LIVE_DB, Source, aware, read_once

BOT_CONFIG = registry.KNOWN["wolfbet"].files.config
DEFAULT_PURGE_DAYS = 3
FULL_EVERY = timedelta(hours=24)
CHUNK_IDS = 50_000
TOLERANCE = Decimal("1e-12")
MAX_DETAILS = 100

OK, MISMATCH, PURGED = "ok", "mismatch", "purged_upstream"

logger = logging.getLogger("ingest.reconcile")


def _decimal(value: Any) -> Decimal:
    return Decimal(0) if value is None else Decimal(repr(value))


@dataclass
class Totals:
    count: int = 0
    profit: Decimal = field(default_factory=Decimal)
    amount: Decimal = field(default_factory=Decimal)
    min_id: int | None = None

    def add(self, bet_id: int, profit: Any, amount: Any) -> None:
        self.count += 1
        self.profit += _decimal(profit)
        self.amount += _decimal(amount)
        self.min_id = bet_id if self.min_id is None else min(self.min_id, bet_id)

    def matches(self, other: "Totals") -> bool:
        return (
            self.count == other.count
            and abs(self.profit - other.profit) <= TOLERANCE
            and abs(self.amount - other.amount) <= TOLERANCE
        )

    def as_json(self) -> dict[str, Any]:
        return {"count": self.count, "profit": str(self.profit), "amount": str(self.amount)}


def read_purge_days(data_dir: Path, config_name: str = BOT_CONFIG) -> int:
    """The bot's bet retention in days. The config also holds the API key, so only this one
    key is read and nothing from the file is ever logged."""
    try:
        config = json.loads((data_dir / config_name).read_text())
        days = config.get("purge_days", DEFAULT_PURGE_DAYS)
        if isinstance(days, int) and not isinstance(days, bool) and 1 <= days <= 30:
            return days
    except (OSError, ValueError, AttributeError):
        pass
    logger.warning("bot purge_days unreadable; assuming %s days", DEFAULT_PURGE_DAYS)
    return DEFAULT_PURGE_DAYS


class Reconciler:
    """Compares one service's live epoch between SQLite and Timescale, one run at a time."""

    def __init__(
        self,
        db: asyncpg.Connection,
        service: str,
        data_dir: Path,
        host_tz: str,
        live_name: str = LIVE_DB,
        config_name: str = BOT_CONFIG,
    ) -> None:
        self.db = db
        self.service = service
        self.data_dir = data_dir
        self.zone = ZoneInfo(host_tz)
        self.config_name = config_name
        # A stopped bot's closed file is read immutably, guarded as the tailer reads it.
        self.source = Source(data_dir / live_name, archived=False, closed_ok=True)
        self.max_read_seconds = 0.0

    async def run_once(self) -> dict[str, Any] | None:
        """Run one check and store it. Returns the stored row, or None before the first epoch."""
        started = datetime.now(UTC)
        epoch_id = await self.db.fetchval(
            "SELECT epoch_id FROM db_epochs WHERE service = $1 AND source_path = $2"
            " ORDER BY epoch_id DESC LIMIT 1",
            self.service,
            str(self.source.path),
        )
        if epoch_id is None:
            return None
        hi, copied_ts = await self.db.fetchrow(
            "SELECT coalesce(max(src_id), 0), max(ts) FROM bets"
            " WHERE service = $1 AND epoch_id = $2",
            self.service,
            epoch_id,
        )
        kind, lo, previously_flagged = await self._range(epoch_id, started)
        sqlite_min, sqlite_max, newest_ts = await self._read(_bounds)
        local = await self._sqlite_totals(max(lo, (sqlite_min or 1) - 1), hi)
        remote = await self._timescale_totals(epoch_id, lo, hi)

        verdicts: dict[Any, tuple[str, Totals, Totals]] = {}
        for session in local.keys() | remote.keys():
            mine, theirs = local.get(session, Totals()), remote.get(session, Totals())
            verdicts[session] = (
                await self._verdict(epoch_id, session, hi, mine, theirs),
                mine,
                theirs,
            )
        flagged = {s: v for s, v in verdicts.items() if v[0] != OK}
        mismatched = {s for s, v in flagged.items() if v[0] == MISMATCH}

        lag_rows = max((sqlite_max or 0) - hi, 0)
        lag_seconds = 0.0
        if lag_rows and newest_ts and copied_ts:
            lag_seconds = max((aware(newest_ts, self.zone) - copied_ts).total_seconds(), 0.0)
        purge_days = await asyncio.to_thread(read_purge_days, self.data_dir, self.config_name)

        first_ids = [
            i
            for s, (_, mine, theirs) in flagged.items()
            if s in mismatched
            for i in (mine.min_id, theirs.min_id)
            if i is not None
        ]
        details = {
            "recheck_from": min(first_ids) - 1 if first_ids else None,
            "sessions": [
                {
                    "session": session,
                    "status": status,
                    "sqlite": mine.as_json(),
                    "timescale": theirs.as_json(),
                }
                for session, (status, mine, theirs) in sorted(
                    flagged.items(), key=lambda item: (item[0] is None, item[0] or 0)
                )[:MAX_DETAILS]
            ],
            "mismatched_sessions": sorted(s for s in mismatched if s is not None),
        }
        row = {
            "service": self.service,
            "epoch_id": epoch_id,
            "kind": kind,
            "lo_id": lo,
            "hi_id": hi,
            "started_at": started,
            "finished_at": datetime.now(UTC),
            "sessions_checked": len(verdicts),
            "mismatched": len(mismatched),
            "purged_upstream": len(flagged) - len(mismatched),
            "persisting": len(mismatched & previously_flagged),
            "lag_rows": lag_rows,
            "lag_seconds": lag_seconds,
            "lag_alert": lag_seconds > purge_days * 86400 / 2,
            "details": json.dumps(details),
        }
        columns = ", ".join(row)
        marks = ", ".join(f"${i}" for i in range(1, len(row) + 1))
        await self.db.execute(
            f"INSERT INTO reconcile_runs ({columns}) VALUES ({marks})",  # noqa: S608  # nosec B608: fixed column names
            *row.values(),
        )
        logger.info(
            "%s reconcile of ids %s..%s: %s sessions, %s mismatched, %s persisting,"
            " %s purged upstream, lag %s rows / %.0f s%s",
            kind,
            lo,
            hi,
            row["sessions_checked"],
            row["mismatched"],
            row["persisting"],
            row["purged_upstream"],
            lag_rows,
            lag_seconds,
            " (over half the purge window)" if row["lag_alert"] else "",
        )
        return row

    async def _range(self, epoch_id: int, now: datetime) -> tuple[str, int, set[int]]:
        """Full from 0 once a day, otherwise from the previous run's end or its first gap."""
        previous = await self.db.fetchrow(
            "SELECT hi_id, details FROM reconcile_runs WHERE service = $1 AND epoch_id = $2"
            " ORDER BY id DESC LIMIT 1",
            self.service,
            epoch_id,
        )
        last_full = await self.db.fetchval(
            "SELECT max(started_at) FROM reconcile_runs"
            " WHERE service = $1 AND epoch_id = $2 AND kind = 'full'",
            self.service,
            epoch_id,
        )
        flagged: set[int] = set()
        recheck_from = None
        if previous is not None:
            details = json.loads(previous["details"])
            flagged = set(details["mismatched_sessions"])
            recheck_from = details["recheck_from"]
        if previous is None or last_full is None or now - last_full >= FULL_EVERY:
            return "full", 0, flagged
        lo = int(previous["hi_id"])
        if recheck_from is not None:
            lo = min(lo, int(recheck_from))
        return "quick", lo, flagged

    async def _sqlite_totals(self, lo: int, hi: int) -> dict[Any, Totals]:
        totals: dict[Any, Totals] = {}
        start = lo
        while start < hi:
            end = min(start + CHUNK_IDS, hi)
            rows = await self._read(partial(_bets_between, lo=start, hi=end))
            for bet_id, session, profit, amount in rows:
                totals.setdefault(session, Totals()).add(bet_id, profit, amount)
            start = end
        return totals

    async def _timescale_totals(self, epoch_id: int, lo: int, hi: int) -> dict[Any, Totals]:
        rows = await self.db.fetch(
            "SELECT session_src_id, count(*), sum(profit::text::numeric),"
            " sum(amount::text::numeric), min(src_id) FROM bets"
            " WHERE service = $1 AND epoch_id = $2 AND src_id > $3 AND src_id <= $4"
            " GROUP BY session_src_id",
            self.service,
            epoch_id,
            lo,
            hi,
        )
        return {r[0]: Totals(r[1], r[2] or Decimal(0), r[3] or Decimal(0), r[4]) for r in rows}

    async def _verdict(
        self, epoch_id: int, session: Any, hi: int, mine: Totals, theirs: Totals
    ) -> str:
        if mine.matches(theirs):
            return OK
        if mine.count >= theirs.count:
            return MISMATCH
        if mine.count == 0:
            return PURGED
        tail = await self.db.fetchrow(
            "SELECT count(*), sum(profit::text::numeric), sum(amount::text::numeric) FROM bets"
            " WHERE service = $1 AND epoch_id = $2 AND session_src_id IS NOT DISTINCT FROM $3"
            " AND src_id >= $4 AND src_id <= $5",
            self.service,
            epoch_id,
            session,
            mine.min_id,
            hi,
        )
        kept = Totals(tail[0], tail[1] or Decimal(0), tail[2] or Decimal(0))
        return PURGED if mine.matches(kept) else MISMATCH

    async def _read[T](self, query: Callable[[sqlite3.Connection], T]) -> T:
        result, seconds = await asyncio.to_thread(read_once, self.source, query)
        self.max_read_seconds = max(self.max_read_seconds, seconds)
        return result


def _bounds(connection: sqlite3.Connection) -> tuple[int | None, int | None, str | None]:
    low, high = connection.execute("SELECT min(id), max(id) FROM bets").fetchone()
    newest = connection.execute("SELECT timestamp FROM bets ORDER BY id DESC LIMIT 1").fetchone()
    return low, high, newest[0] if newest else None


def _bets_between(connection: sqlite3.Connection, lo: int, hi: int) -> list[tuple[Any, ...]]:
    return connection.execute(
        "SELECT id, session_id, profit, amount FROM bets WHERE id > ? AND id <= ? ORDER BY id",
        (lo, hi),
    ).fetchall()
