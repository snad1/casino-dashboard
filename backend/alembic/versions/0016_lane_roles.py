"""A role may apply to one lane of a service.

`user_roles.lane` narrows a service role to one lane; the permission checks count such a
row only for a check that names that lane. The unique key gains the lane, so a user may
hold a service role and lane roles side by side.

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LANE_CHECK = "lane IS NULL OR (service_id IS NOT NULL AND lane BETWEEN 1 AND 99)"


def _unique(columns: list[str]) -> None:
    op.drop_constraint(op.f("uq_user_roles_user_id"), "user_roles", type_="unique")
    op.create_unique_constraint(
        op.f("uq_user_roles_user_id"), "user_roles", columns, postgresql_nulls_not_distinct=True
    )


def upgrade() -> None:
    op.add_column("user_roles", sa.Column("lane", sa.SmallInteger(), nullable=True))
    op.create_check_constraint("lane", "user_roles", LANE_CHECK)
    _unique(["user_id", "service_id", "lane"])


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM user_roles WHERE lane IS NOT NULL) THEN"
        " RAISE EXCEPTION 'Lane roles exist; remove them first'; END IF; END $$"
    )
    _unique(["user_id", "service_id"])
    op.drop_constraint(op.f("ck_user_roles_lane"), "user_roles", type_="check")
    op.drop_column("user_roles", "lane")
