"""An invite may name several lanes.

`invites.lanes` replaces `invites.lane`: a viewer or operator invite can now open a set of
lanes of the invite's service, one role row each once accepted.

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LANES_CHECK = (
    "cardinality(lanes) = 0 OR (service_id IS NOT NULL AND role IN ('viewer', 'operator')"
    " AND cardinality(lanes) <= 20 AND 1 <= ALL(lanes) AND 99 >= ALL(lanes))"
)
LANE_CHECK = (
    "lane IS NULL OR (service_id IS NOT NULL AND lane BETWEEN 1 AND 99"
    " AND role IN ('viewer', 'operator'))"
)


def upgrade() -> None:
    op.add_column(
        "invites",
        sa.Column(
            "lanes",
            postgresql.ARRAY(sa.SmallInteger()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )
    op.execute("UPDATE invites SET lanes = ARRAY[lane] WHERE lane IS NOT NULL")
    op.drop_constraint(op.f("ck_invites_lane"), "invites", type_="check")
    op.drop_column("invites", "lane")
    op.create_check_constraint("lanes", "invites", LANES_CHECK)


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM invites WHERE cardinality(lanes) > 1) THEN"
        " RAISE EXCEPTION 'Invites name several lanes; remove them first'; END IF; END $$"
    )
    op.add_column("invites", sa.Column("lane", sa.SmallInteger(), nullable=True))
    op.execute("UPDATE invites SET lane = lanes[1] WHERE cardinality(lanes) = 1")
    op.drop_constraint(op.f("ck_invites_lanes"), "invites", type_="check")
    op.drop_column("invites", "lanes")
    op.create_check_constraint("lane", "invites", LANE_CHECK)
