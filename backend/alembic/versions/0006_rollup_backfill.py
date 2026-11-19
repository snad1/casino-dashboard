"""Rollup refresh policies cover all of history, so backfilled bets reach the rollups.

Ingest copies archived and catch-up bets that are older than the refresh windows set in
0005, and its database role may not refresh a rollup by hand (only the owner may). With no
start offset, each owner-run policy also materializes those older buckets. After the first
pass a run only redoes buckets whose bets changed, so the cost stays small.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICIES = (
    # name, start offset before this revision, end offset, schedule
    ("bets_1m", "2 hours", "1 minute", "1 minute"),
    ("bets_1h", "3 days", "1 hour", "30 minutes"),
    ("bets_1d", "30 days", "1 day", "6 hours"),
)


def _set_policies(open_start: bool) -> None:
    for name, start, end, every in POLICIES:
        start_offset = "NULL" if open_start else f"INTERVAL '{start}'"
        op.execute(f"SELECT remove_continuous_aggregate_policy('{name}')")
        op.execute(
            f"SELECT add_continuous_aggregate_policy('{name}',"
            f" start_offset => {start_offset}, end_offset => INTERVAL '{end}',"
            f" schedule_interval => INTERVAL '{every}')"
        )


def upgrade() -> None:
    _set_policies(open_start=True)


def downgrade() -> None:
    _set_policies(open_start=False)
