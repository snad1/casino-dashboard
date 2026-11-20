"""Alert tables: the rules, each rule's saved state, what fired, and one delivery per channel.

A rule with no lane applies to every lane on its own; state for a service-level rule (day
profit, ingest health) is kept under lane 0. The engine writes one delivery per channel with
no recipient; the mailer fans each out into one delivery per user who should get it.
"""

from datetime import datetime, time
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    Identity,
    Index,
    Integer,
    LargeBinary,
    Text,
    Time,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import text

from app.db.models import Base

CHANNELS = ("email", "telegram", "push")
SERVICE_LANE = 0


class AlertRule(Base):
    __tablename__ = "alert_rules"
    __table_args__ = (
        CheckConstraint("cooldown_s >= 0 AND cooldown_s <= 86400", name="cooldown_s"),
        CheckConstraint("lane IS NULL OR lane >= 1", name="lane"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    service: Mapped[str] = mapped_column(Text)
    lane: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    cooldown_s: Mapped[int] = mapped_column(Integer, server_default=text("600"))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AlertRuleState(Base):
    __tablename__ = "alert_rule_state"

    rule_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("alert_rules.id", ondelete="CASCADE"), primary_key=True
    )
    lane: Mapped[int] = mapped_column(Integer, primary_key=True)
    state: Mapped[dict[str, Any]] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AlertEvent(Base):
    __tablename__ = "alert_events"
    __table_args__ = (Index("ix_alert_events_service_fired_at", "service", "fired_at"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    rule_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("alert_rules.id", ondelete="SET NULL"), index=True
    )
    service: Mapped[str] = mapped_column(Text)
    lane: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(Text)
    value: Mapped[float | None] = mapped_column(Double)
    message: Mapped[str] = mapped_column(Text)
    fired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AlertDelivery(Base):
    __tablename__ = "alert_outbox"
    __table_args__ = (
        CheckConstraint("channel IN ('email', 'telegram', 'push')", name="channel"),
        CheckConstraint("status IN ('pending', 'sent', 'dead')", name="status"),
        Index(
            "ix_alert_outbox_due",
            "next_attempt_at",
            postgresql_where=text("status = 'pending'"),
        ),
        Index("ix_alert_outbox_user_sent", "user_id", "channel", "sent_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    event_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("alert_events.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE")
    )
    channel: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserChannels(Base):
    """Where a user wants alerts, and when not. Quiet hours are host-local times; a window
    that ends before it starts runs over midnight. A Telegram link code is stored hashed."""

    __tablename__ = "user_channels"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    email_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    telegram_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    telegram_link_hash: Mapped[bytes | None] = mapped_column(LargeBinary, unique=True)
    telegram_link_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quiet_start: Mapped[time | None] = mapped_column(Time)
    quiet_end: Mapped[time | None] = mapped_column(Time)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
