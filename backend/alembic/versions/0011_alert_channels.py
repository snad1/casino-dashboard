"""Alert channels: a recipient and send time on each delivery, and each user's channel choices.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("alert_outbox", sa.Column("user_id", sa.BigInteger(), nullable=True))
    op.add_column("alert_outbox", sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        op.f("fk_alert_outbox_user_id_users"),
        "alert_outbox",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_alert_outbox_user_sent", "alert_outbox", ["user_id", "channel", "sent_at"], unique=False
    )
    op.create_table(
        "user_channels",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("email_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "telegram_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("telegram_link_hash", sa.LargeBinary(), nullable=True),
        sa.Column("telegram_link_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("quiet_start", sa.Time(), nullable=True),
        sa.Column("quiet_end", sa.Time(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_channels_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_user_channels")),
        sa.UniqueConstraint("telegram_chat_id", name=op.f("uq_user_channels_telegram_chat_id")),
        sa.UniqueConstraint("telegram_link_hash", name=op.f("uq_user_channels_telegram_link_hash")),
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_channels")
    op.execute("DROP INDEX IF EXISTS ix_alert_outbox_user_sent")
    op.execute("ALTER TABLE alert_outbox DROP COLUMN IF EXISTS sent_at")
    op.execute("ALTER TABLE alert_outbox DROP COLUMN IF EXISTS user_id")
