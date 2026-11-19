"""History tables: a copy of each bot's SQLite `sessions` and `bets`, keyed by database epoch.

SQLite ids restart when the bot's database is archived and recreated, so every copied row
carries the `epoch_id` of the database file it came from. `src_id` is the SQLite row id.
`bets` is a Timescale hypertable on `ts`; its primary key must include `ts`.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Double,
    ForeignKey,
    Identity,
    Index,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class DbEpoch(Base):
    """One SQLite database file lifetime, told apart by inode and first session start."""

    __tablename__ = "db_epochs"
    __table_args__ = (UniqueConstraint("service", "inode", "first_started_at"),)

    epoch_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    service: Mapped[str] = mapped_column(Text)
    source_path: Mapped[str] = mapped_column(Text)
    inode: Mapped[int] = mapped_column(BigInteger)
    first_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class HistorySession(Base):
    """A bot session with its running totals, as last read from SQLite."""

    __tablename__ = "sessions"

    service: Mapped[str] = mapped_column(Text, primary_key=True)
    epoch_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("db_epochs.epoch_id"), primary_key=True
    )
    src_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    lane: Mapped[int] = mapped_column(BigInteger)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    currency: Mapped[str | None] = mapped_column(Text)
    game: Mapped[str | None] = mapped_column(Text)
    strategy: Mapped[str | None] = mapped_column(Text)
    base_bet: Mapped[float | None] = mapped_column(Double)
    multiplier: Mapped[float | None] = mapped_column(Double)
    total_bets: Mapped[int | None] = mapped_column(BigInteger)
    wins: Mapped[int | None] = mapped_column(BigInteger)
    losses: Mapped[int | None] = mapped_column(BigInteger)
    profit: Mapped[float | None] = mapped_column(Double)
    wagered: Mapped[float | None] = mapped_column(Double)
    start_balance: Mapped[float | None] = mapped_column(Double)
    end_balance: Mapped[float | None] = mapped_column(Double)
    max_win_streak: Mapped[int | None] = mapped_column(BigInteger)
    max_loss_streak: Mapped[int | None] = mapped_column(BigInteger)
    highest_balance: Mapped[float | None] = mapped_column(Double)
    lowest_balance: Mapped[float | None] = mapped_column(Double)
    highest_win: Mapped[float | None] = mapped_column(Double)
    biggest_loss: Mapped[float | None] = mapped_column(Double)
    bets_per_minute: Mapped[float | None] = mapped_column(Double)
    bets_per_second: Mapped[float | None] = mapped_column(Double)
    peak_bps: Mapped[float | None] = mapped_column(Double)
    low_bps: Mapped[float | None] = mapped_column(Double)
    peak_bpm: Mapped[float | None] = mapped_column(Double)
    low_bpm: Mapped[float | None] = mapped_column(Double)


class Bet(Base):
    """One bet. `lane` is copied from its session at ingest, since SQLite bets have no lane
    and continuous aggregates cannot join."""

    __tablename__ = "bets"
    __table_args__ = (Index("ix_bets_session", "service", "epoch_id", "session_src_id", "ts"),)

    service: Mapped[str] = mapped_column(Text, primary_key=True)
    epoch_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    src_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    session_src_id: Mapped[int | None] = mapped_column(BigInteger)
    lane: Mapped[int] = mapped_column(BigInteger)
    amount: Mapped[float | None] = mapped_column(Double)
    multiplier: Mapped[float | None] = mapped_column(Double)
    rule: Mapped[str | None] = mapped_column(Text)
    bet_value: Mapped[float | None] = mapped_column(Double)
    result_value: Mapped[float | None] = mapped_column(Double)
    state: Mapped[str | None] = mapped_column(Text)
    profit: Mapped[float | None] = mapped_column(Double)
    balance_after: Mapped[float | None] = mapped_column(Double)
