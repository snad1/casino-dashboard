"""Queued mail. The api writes rows in the same transaction as the action that needs them."""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Identity, Index, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import text

from app.db.models import Base

PENDING, SENT, DEAD = "pending", "sent", "dead"


class OutboxMessage(Base):
    """Bodies are cleared once a message is sent or dead, since they carry one-time links."""

    __tablename__ = "mail_outbox"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'sent', 'dead')", name="status"),
        Index(
            "ix_mail_outbox_due",
            "next_attempt_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    to_email: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text)
    text_body: Mapped[str | None] = mapped_column(Text)
    html_body: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
