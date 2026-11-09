"""escalating an approval nobody decides (2026-11-10)

A step may escalate an Inbox item to a role when nobody decides it: after its own number of hours, else when the
approval would expire. `escalate_at` keeps when the step's own time runs out (NULL: at the expiry, or never) and
`escalated_at` when it was escalated (the item's expiry then counts from there). Both are nullable without a default,
so no row is rewritten. The notifier's index covers only items that have an escalation time; on Postgres it is built
concurrently, after the columns are committed, so the Inbox stays readable and writable while it builds.

Revision ID: 7549afff4827
Revises: 7ad337b9ba7d
Create Date: 2026-11-10 22:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7549afff4827"
down_revision: str | None = "7ad337b9ba7d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("interrupt_task") as batch:
        batch.add_column(sa.Column("escalate_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True))
    has_time = sa.text("escalate_at IS NOT NULL")
    if op.get_bind().dialect.name != "postgresql":
        op.create_index("interrupt_escalate_idx", "interrupt_task", ["escalate_at"], sqlite_where=has_time)
        return
    with op.get_context().autocommit_block():  # CREATE INDEX CONCURRENTLY cannot run in a transaction
        # a build cut short leaves an invalid index behind: it is built again, never kept
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS interrupt_escalate_idx")
        op.create_index("interrupt_escalate_idx", "interrupt_task", ["escalate_at"], postgresql_where=has_time,
                        postgresql_concurrently=True)  # fmt: skip


def downgrade() -> None:
    op.drop_index("interrupt_escalate_idx", table_name="interrupt_task")
    with op.batch_alter_table("interrupt_task") as batch:
        batch.drop_column("escalated_at")
        batch.drop_column("escalate_at")
