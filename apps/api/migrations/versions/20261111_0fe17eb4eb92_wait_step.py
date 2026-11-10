"""the Wait step: Inbox items that wait for a time or an event (2026-11-11)

A Wait step pauses its run as an Inbox item of kind `timer`: `due_at` is when it is due (an event wait's timeout),
`wait_event` and `wait_key` the event that wakes it sooner, and `claimed_until` the lease of the server starting the
run that continues it. A Stop or the kill switch marks such an item `cancelled`. The columns are nullable without a
default, so no row is rewritten.

On Postgres the two check constraints are replaced NOT VALID and validated afterwards (a validation does not block
writes), and the timer job's indexes, which cover only waiting items, are built concurrently once the columns are
committed, so the Inbox stays readable and writable during a rolling upgrade. SQLite rebuilds the table.

Revision ID: 0fe17eb4eb92
Revises: d32911f9085f
Create Date: 2026-11-11 09:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0fe17eb4eb92"
down_revision: str | None = "d32911f9085f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = "kind IN ('tool_approval','approval','input','timer')"
STATUSES = "status IN ('pending','decided','expired','cancelled')"
OLD_KINDS = "kind IN ('tool_approval','approval','input')"
OLD_STATUSES = "status IN ('pending','decided','expired')"
COLUMNS = ("due_at", "wait_event", "wait_key", "claimed_until")
INDEXES = {  # name -> (column, which rows)
    "interrupt_due_idx": ("due_at", "status = 'pending' AND due_at IS NOT NULL"),
    "interrupt_event_idx": ("wait_event", "status = 'pending' AND wait_event IS NOT NULL"),
}


def _columns() -> list[sa.Column]:
    return [
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("wait_event", sa.Text(), nullable=True),
        sa.Column("wait_key", sa.Text(), nullable=True),
        sa.Column("claimed_until", sa.DateTime(timezone=True), nullable=True),
    ]


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        with op.batch_alter_table("interrupt_task", recreate="always") as batch:
            for column in _columns():
                batch.add_column(column)
            batch.drop_constraint("interrupt_kind", type_="check")
            batch.drop_constraint("interrupt_status", type_="check")
            batch.create_check_constraint("interrupt_kind", KINDS)
            batch.create_check_constraint("interrupt_status", STATUSES)
        for name, (column, where) in INDEXES.items():
            op.create_index(name, "interrupt_task", [column], sqlite_where=sa.text(where))
        return
    for column in _columns():
        op.add_column("interrupt_task", column)
    for name, rule in (("interrupt_kind", KINDS), ("interrupt_status", STATUSES)):
        replace = f"DROP CONSTRAINT IF EXISTS {name}, ADD CONSTRAINT {name} CHECK ({rule}) NOT VALID"
        op.execute(f"ALTER TABLE interrupt_task {replace}")
    with op.get_context().autocommit_block():  # VALIDATE and CREATE INDEX CONCURRENTLY: after the columns committed
        op.execute("ALTER TABLE interrupt_task VALIDATE CONSTRAINT interrupt_kind")
        op.execute("ALTER TABLE interrupt_task VALIDATE CONSTRAINT interrupt_status")
        for name, (column, where) in INDEXES.items():
            # a build cut short leaves an invalid index behind: it is built again, never kept
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
            op.create_index(name, "interrupt_task", [column], postgresql_where=sa.text(where),
                            postgresql_concurrently=True)  # fmt: skip


def downgrade() -> None:
    # the older code knows neither kind nor status: what waits on a timer, or was cancelled, goes
    op.execute("DELETE FROM interrupt_task WHERE kind = 'timer'")
    op.execute("UPDATE interrupt_task SET status = 'expired' WHERE status = 'cancelled'")
    for name in INDEXES:
        op.drop_index(name, table_name="interrupt_task")
    if op.get_bind().dialect.name != "postgresql":
        with op.batch_alter_table("interrupt_task", recreate="always") as batch:
            batch.drop_constraint("interrupt_kind", type_="check")
            batch.drop_constraint("interrupt_status", type_="check")
            batch.create_check_constraint("interrupt_kind", OLD_KINDS)
            batch.create_check_constraint("interrupt_status", OLD_STATUSES)
            for column in reversed(COLUMNS):
                batch.drop_column(column)
        return
    for name, rule in (("interrupt_kind", OLD_KINDS), ("interrupt_status", OLD_STATUSES)):
        op.execute(f"ALTER TABLE interrupt_task DROP CONSTRAINT IF EXISTS {name}, ADD CONSTRAINT {name} CHECK ({rule})")
    for column in reversed(COLUMNS):
        op.drop_column("interrupt_task", column)
