"""A viewer or operator role may be limited to the lanes of a vault key.

`user_roles.key_ref` names the key; the role then reaches the lanes that bet with that key
when a check runs, and the sessions that bet with it. `invites.key_refs` carries the keys
until the invite is accepted.

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KEY_CHECK = (
    "key_ref IS NULL OR (lane IS NULL AND service_id IS NOT NULL"
    " AND role IN ('viewer', 'operator'))"
)
KEYS_CHECK = (
    "cardinality(key_refs) = 0 OR (service_id IS NOT NULL AND role IN ('viewer', 'operator')"
    " AND cardinality(lanes) = 0 AND cardinality(key_refs) <= 20)"
)


def _unique(columns: list[str]) -> None:
    op.drop_constraint(op.f("uq_user_roles_user_id"), "user_roles", type_="unique")
    op.create_unique_constraint(
        op.f("uq_user_roles_user_id"), "user_roles", columns, postgresql_nulls_not_distinct=True
    )


def upgrade() -> None:
    op.add_column("user_roles", sa.Column("key_ref", sa.String(32), nullable=True))
    op.create_check_constraint("key", "user_roles", KEY_CHECK)
    _unique(["user_id", "service_id", "lane", "key_ref"])
    op.add_column(
        "invites",
        sa.Column(
            "key_refs",
            postgresql.ARRAY(sa.String(32)),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )
    op.create_check_constraint("keys", "invites", KEYS_CHECK)


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM user_roles WHERE key_ref IS NOT NULL)"
        " OR EXISTS (SELECT 1 FROM invites WHERE cardinality(key_refs) > 0) THEN"
        " RAISE EXCEPTION 'Key roles or key invites exist; remove them first'; END IF; END $$"
    )
    op.drop_constraint(op.f("ck_invites_keys"), "invites", type_="check")
    op.drop_column("invites", "key_refs")
    _unique(["user_id", "service_id", "lane"])
    op.drop_constraint(op.f("ck_user_roles_key"), "user_roles", type_="check")
    op.drop_column("user_roles", "key_ref")
