"""run priority and the run queue (2026-11-10)

With a concurrent-run limit (AGENTCANVAS_MAX_CONCURRENT_RUNS, or the organisation's own), a run beyond it waits in
the organisation's queue, highest priority first, then oldest. `queued` marks a run still waiting (it is "running" as
far as its thread and the kill switch are concerned); `priority` is 1 high, 0 normal, -1 low.

Revision ID: b8e2f6a0c4d7
Revises: a7d1e5b9c3f6
Create Date: 2026-11-10 09:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8e2f6a0c4d7"
down_revision: str | None = "a7d1e5b9c3f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("run") as batch:
        batch.add_column(sa.Column("priority", sa.SmallInteger(), server_default=sa.text("0"), nullable=False))
        batch.add_column(sa.Column("queued", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.create_index("run_status_queued_idx", "run", ["status", "queued"])


def downgrade() -> None:
    op.drop_index("run_status_queued_idx", table_name="run")
    with op.batch_alter_table("run") as batch:
        batch.drop_column("queued")
        batch.drop_column("priority")
