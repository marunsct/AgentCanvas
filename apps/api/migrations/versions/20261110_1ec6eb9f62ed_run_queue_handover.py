"""the run queue through a hand-over (2026-11-10)

`pending` keeps a waiting run's start arguments: no checkpoint holds its input before it takes a slot, so a server
that stops hands it over with them. `queue_key` keeps a handed-over run's place in the queue (the id of the run it
continues). `event_depth` keeps how many events led to an event handler's run, which a hand-over continues at the
same depth. `handover_attempts` counts the hand-overs that failed for a passing reason; it gives up after a few.

The queue's index covers only runs in flight (`WHERE status = 'running'`), the only rows the run queue and the
heartbeat read, instead of every run ever made; on Postgres it is built concurrently, after the columns are
committed, so the run table stays readable and writable while it builds.

Revision ID: 1ec6eb9f62ed
Revises: fe5b2feda682
Create Date: 2026-11-10 18:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "1ec6eb9f62ed"
down_revision: str | None = "fe5b2feda682"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("run") as batch:
        batch.add_column(sa.Column("pending", JSON, nullable=True))
        batch.add_column(sa.Column("queue_key", sa.Text(), nullable=True))
        batch.add_column(sa.Column("event_depth", sa.SmallInteger(), server_default=sa.text("0"), nullable=False))
        batch.add_column(sa.Column("handover_attempts", sa.SmallInteger(), server_default=sa.text("0"), nullable=False))
    live = sa.text("status = 'running'")
    if op.get_bind().dialect.name != "postgresql":
        op.drop_index("run_status_queued_idx", table_name="run")
        op.create_index("run_live_idx", "run", ["queued", "priority"], sqlite_where=live)
        return
    with op.get_context().autocommit_block():  # CREATE INDEX CONCURRENTLY cannot run in a transaction
        # a build cut short leaves an invalid index behind: it is built again, never kept
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS run_live_idx")
        op.create_index("run_live_idx", "run", ["queued", "priority"], postgresql_where=live,
                        postgresql_concurrently=True)  # fmt: skip
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS run_status_queued_idx")


def downgrade() -> None:
    op.drop_index("run_live_idx", table_name="run")
    op.create_index("run_status_queued_idx", "run", ["status", "queued"])
    with op.batch_alter_table("run") as batch:
        batch.drop_column("handover_attempts")
        batch.drop_column("event_depth")
        batch.drop_column("queue_key")
        batch.drop_column("pending")
