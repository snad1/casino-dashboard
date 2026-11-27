"""Which bot database a history view reads: the live one, or an archived one picked on purpose.

`wolfctl reset` archives the bot's live database, and the tailer then records that epoch under
its archive file name. The current epoch is the service's newest one if it still carries the
live file's name, so right after a reset there is none and every view starts empty. Older
epochs stay stored and are read only when asked for by id.

The rollups are kept per service and lane, not per epoch. Epochs never overlap in time (a reset
stops the bot first), so a rollup read is bounded to the epoch's whole buckets: the bucket a
reset happened in is left out on both sides rather than mixing two databases.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import registry
from app.history.models import DbEpoch
from app.ingest.tailer import LIVE_DB

EPOCH_NOT_FOUND = "That reset was not found."


@dataclass(frozen=True)
class EpochView:
    ids: list[int]
    first_started_at: datetime | None = None
    next_started_at: datetime | None = None
    has_previous: bool = False

    def bounds(self, step: timedelta) -> tuple[datetime | None, datetime | None]:
        """The epoch as whole `step` buckets: [start, end), either side open when unbounded."""
        start = self.first_started_at
        if start is not None and self.has_previous:
            start = _bucket(start, step, up=True)
        end = _bucket(self.next_started_at, step, up=False) if self.next_started_at else None
        return start, end


def _bucket(moment: datetime, step: timedelta, *, up: bool) -> datetime:
    seconds = step.total_seconds()
    whole, rest = divmod(moment.timestamp(), seconds)
    return datetime.fromtimestamp((whole + (1 if up and rest else 0)) * seconds, UTC)


def is_live(service: str, source_path: str) -> bool:
    """Whether the epoch was read from the service's live file (WolfBet's name for a service
    the registry does not know)."""
    entry = registry.KNOWN.get(service)
    return PurePosixPath(source_path).name == (entry.files.live_db if entry else LIVE_DB)


async def epoch_view(db: AsyncSession, service: str, epoch: int | None = None) -> EpochView:
    """The current epoch (none right after a reset), or the bot epoch `epoch`, else 404."""
    rows = (
        await db.execute(
            select(DbEpoch.epoch_id, DbEpoch.source_path, DbEpoch.first_started_at)
            .where(DbEpoch.service == service, DbEpoch.tg_uid.is_(None))
            .order_by(DbEpoch.epoch_id)
        )
    ).all()
    if epoch is None:
        if not rows or not is_live(service, rows[-1].source_path):
            return EpochView(ids=[])
        index = len(rows) - 1
    else:
        index = next((i for i, row in enumerate(rows) if row.epoch_id == epoch), -1)
        if index < 0:
            raise HTTPException(status.HTTP_404_NOT_FOUND, EPOCH_NOT_FOUND)
    chosen = rows[index]
    later = rows[index + 1] if index + 1 < len(rows) else None
    return EpochView(
        ids=[chosen.epoch_id],
        first_started_at=chosen.first_started_at,
        next_started_at=later.first_started_at if later else None,
        has_previous=index > 0,
    )
