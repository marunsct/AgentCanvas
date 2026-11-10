"""Inbox items the conversation moved past are "withdrawn" (2026-11-13)

An Inbox item still pending when LangGraph has moved past its question (the conversation's state was edited, it went
back to an earlier point, or a new message started it over) could never be answered: every answer was refused as an
unknown interrupt and the item went back to wait. Such an item is now marked `withdrawn`. Only the check constraint
changes: no column, no row rewritten.

On Postgres the constraint is replaced NOT VALID and validated afterwards in its own transaction (a validation does
not block writes), so the Inbox stays readable and writable during a rolling upgrade. SQLite rebuilds the table.

Revision ID: 72239139b385
Revises: a4c85440520c
Create Date: 2026-11-13 09:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "72239139b385"
down_revision: str | None = "a4c85440520c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUSES = "status IN ('pending','decided','expired','cancelled','withdrawn')"
OLD_STATUSES = "status IN ('pending','decided','expired','cancelled')"


def _replace(rule: str) -> None:
    if op.get_bind().dialect.name != "postgresql":
        with op.batch_alter_table("interrupt_task", recreate="always") as batch:
            batch.drop_constraint("interrupt_status", type_="check")
            batch.create_check_constraint("interrupt_status", rule)
        return
    op.execute(
        "ALTER TABLE interrupt_task DROP CONSTRAINT IF EXISTS interrupt_status, "
        f"ADD CONSTRAINT interrupt_status CHECK ({rule}) NOT VALID"
    )
    with op.get_context().autocommit_block():  # after the new constraint committed: validating does not block writes
        op.execute("ALTER TABLE interrupt_task VALIDATE CONSTRAINT interrupt_status")


def upgrade() -> None:
    _replace(STATUSES)


def downgrade() -> None:
    # the older code does not know the status: a question nobody can answer any more reads as expired there
    op.execute("UPDATE interrupt_task SET status = 'expired' WHERE status = 'withdrawn'")
    _replace(OLD_STATUSES)
