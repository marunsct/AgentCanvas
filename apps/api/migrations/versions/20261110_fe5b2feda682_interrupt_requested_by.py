"""who asked for the action an Inbox item waits on (2026-11-10)

`interrupt_task.requested_by` is the person whose run raised the item (a resumed run keeps the original requester);
with "approving your own runs" off, that person cannot decide it.

Revision ID: fe5b2feda682
Revises: b8e2f6a0c4d7
Create Date: 2026-11-10 12:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "fe5b2feda682"
down_revision: str | None = "b8e2f6a0c4d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("interrupt_task") as batch:
        batch.add_column(sa.Column("requested_by", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("interrupt_task") as batch:
        batch.drop_column("requested_by")
