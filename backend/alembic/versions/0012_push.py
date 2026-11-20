"""Web Push: each user's browser subscriptions, one delivery per device, and private mode.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("p256dh", sa.Text(), nullable=False),
        sa.Column("auth", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_push_subscriptions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_push_subscriptions")),
        sa.UniqueConstraint("endpoint", name=op.f("uq_push_subscriptions_endpoint")),
    )
    op.create_index(
        op.f("ix_push_subscriptions_user_id"), "push_subscriptions", ["user_id"], unique=False
    )
    op.add_column("alert_outbox", sa.Column("subscription_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        op.f("fk_alert_outbox_subscription_id_push_subscriptions"),
        "alert_outbox",
        "push_subscriptions",
        ["subscription_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        op.f("ix_alert_outbox_subscription_id"), "alert_outbox", ["subscription_id"], unique=False
    )
    op.add_column(
        "user_channels",
        sa.Column("push_private", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )


def downgrade() -> None:
    op.execute("ALTER TABLE user_channels DROP COLUMN IF EXISTS push_private")
    op.execute("DROP INDEX IF EXISTS ix_alert_outbox_subscription_id")
    op.execute("ALTER TABLE alert_outbox DROP COLUMN IF EXISTS subscription_id")
    op.execute("DROP TABLE IF EXISTS push_subscriptions")
