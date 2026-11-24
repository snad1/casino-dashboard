"""An invite may name one lane, and each user has a lane quota.

`invites.lane` invites a viewer or operator straight onto one lane of the invite's service.
`users.lane_quota` is how many lanes a lane manager may create; it was fixed at 1.

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INVITE_LANE_CHECK = (
    "lane IS NULL OR (service_id IS NOT NULL AND lane BETWEEN 1 AND 99"
    " AND role IN ('viewer', 'operator'))"
)
QUOTA_CHECK = "lane_quota BETWEEN 1 AND 99"


def upgrade() -> None:
    op.add_column("invites", sa.Column("lane", sa.SmallInteger(), nullable=True))
    op.create_check_constraint("lane", "invites", INVITE_LANE_CHECK)
    op.add_column(
        "users",
        sa.Column("lane_quota", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
    )
    op.create_check_constraint("lane_quota", "users", QUOTA_CHECK)


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM invites WHERE lane IS NOT NULL)"
        " OR EXISTS (SELECT 1 FROM users WHERE lane_quota <> 1) THEN"
        " RAISE EXCEPTION 'Lane invites or lane quotas exist; remove them first'; END IF; END $$"
    )
    op.drop_constraint(op.f("ck_users_lane_quota"), "users", type_="check")
    op.drop_column("users", "lane_quota")
    op.drop_constraint(op.f("ck_invites_lane"), "invites", type_="check")
    op.drop_column("invites", "lane")
