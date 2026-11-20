"""Alerts: rules, their saved state, fired events and one delivery per channel per event.

Default rules are seeded for wolfbet, all disabled: the owner sets real thresholds and turns
them on. Placeholder thresholds are deliberately small so nothing fires by surprise once a
rule is switched on without being edited first.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULTS = [
    ("Session profit milestone", "profit", {"period": "session", "step": 0.001}, 0),
    ("Session loss", "loss", {"period": "session", "at": 0.001}, 600),
    ("Drawdown from peak", "drawdown", {"pct": 10.0}, 600),
    ("Bot down", "down", {"statuses": ["down"], "for_s": 60}, 600),
    ("Bot stopped", "stop_reason", {"reasons": [], "pattern": None}, 600),
]


def _now() -> sa.TextClause:
    return sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "alert_rules",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("service", sa.Text(), nullable=False),
        sa.Column("lane", sa.Integer(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "params",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("cooldown_s", sa.Integer(), server_default=sa.text("600"), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.CheckConstraint(
            "cooldown_s >= 0 AND cooldown_s <= 86400", name=op.f("ck_alert_rules_cooldown_s")
        ),
        sa.CheckConstraint("lane IS NULL OR lane >= 1", name=op.f("ck_alert_rules_lane")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert_rules")),
    )
    op.create_table(
        "alert_rule_state",
        sa.Column("rule_id", sa.BigInteger(), nullable=False),
        sa.Column("lane", sa.Integer(), nullable=False),
        sa.Column("state", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["alert_rules.id"],
            name=op.f("fk_alert_rule_state_rule_id_alert_rules"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("rule_id", "lane", name=op.f("pk_alert_rule_state")),
    )
    op.create_table(
        "alert_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("rule_id", sa.BigInteger(), nullable=True),
        sa.Column("service", sa.Text(), nullable=False),
        sa.Column("lane", sa.Integer(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("value", sa.Double(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("fired_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["alert_rules.id"],
            name=op.f("fk_alert_events_rule_id_alert_rules"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert_events")),
    )
    op.create_index(op.f("ix_alert_events_rule_id"), "alert_events", ["rule_id"], unique=False)
    op.create_index(
        "ix_alert_events_service_fired_at", "alert_events", ["service", "fired_at"], unique=False
    )
    op.create_table(
        "alert_outbox",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("event_id", sa.BigInteger(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "next_attempt_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.CheckConstraint(
            "channel IN ('email', 'telegram', 'push')", name=op.f("ck_alert_outbox_channel")
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'sent', 'dead')", name=op.f("ck_alert_outbox_status")
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["alert_events.id"],
            name=op.f("fk_alert_outbox_event_id_alert_events"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert_outbox")),
    )
    op.create_index(op.f("ix_alert_outbox_event_id"), "alert_outbox", ["event_id"], unique=False)
    op.create_index(
        "ix_alert_outbox_due",
        "alert_outbox",
        ["next_attempt_at"],
        unique=False,
        postgresql_where=sa.text("status = 'pending'"),
    )
    rules = sa.table(
        "alert_rules",
        sa.column("service", sa.Text()),
        sa.column("kind", sa.Text()),
        sa.column("name", sa.Text()),
        sa.column("params", postgresql.JSONB()),
        sa.column("cooldown_s", sa.Integer()),
        sa.column("enabled", sa.Boolean()),
    )
    op.bulk_insert(
        rules,
        [
            {
                "service": "wolfbet",
                "kind": kind,
                "name": name,
                "params": params,
                "cooldown_s": cooldown,
                "enabled": False,
            }
            for name, kind, params, cooldown in DEFAULTS
        ],
    )


def downgrade() -> None:
    for table in ("alert_outbox", "alert_events", "alert_rule_state", "alert_rules"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
