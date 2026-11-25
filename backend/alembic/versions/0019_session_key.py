"""Each copied session keeps the vault key it bet with, so statistics can be read per account.

`sessions.key_ref` is the bot's key id (`default` is the main key). Sessions the bot recorded
before it kept the key stay NULL and are read as the main key.

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("key_ref", sa.Text(), nullable=True))
    op.create_index("ix_sessions_service_epoch_key", "sessions", ["service", "epoch_id", "key_ref"])


def downgrade() -> None:
    op.drop_index("ix_sessions_service_epoch_key", table_name="sessions")
    op.drop_column("sessions", "key_ref")
