"""a reminder before an approval expires or escalates (2026-11-13)

`interrupt_task.reminded_at` is when the notifier claimed an Inbox item's reminder: set once, with a conditional
update, so one server sends it whatever the number of servers, and cleared when the item escalates (its new deadline
gets a reminder of its own). Nullable without a default, so no row is rewritten and items waiting today are reminded
like new ones.

Revision ID: 98d069f94d68
Revises: a4c85440520c
Create Date: 2026-11-13 09:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "98d069f94d68"
down_revision: str | None = "a4c85440520c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("interrupt_task") as batch:
        batch.add_column(sa.Column("reminded_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("interrupt_task") as batch:
        batch.drop_column("reminded_at")
