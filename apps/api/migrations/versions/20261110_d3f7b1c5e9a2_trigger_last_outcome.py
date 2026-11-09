"""a schedule's last outcome (2026-11-10)

A cron trigger keeps its last run, and when its run could not start (its deployment unpublished, a security gate,
the database), why and how many times in a row, so the trigger list shows a schedule that keeps failing.

Revision ID: d3f7b1c5e9a2
Revises: b8e2f6a0c4d7
Create Date: 2026-11-10 12:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d3f7b1c5e9a2"
down_revision: str | None = "b8e2f6a0c4d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("trigger") as batch:
        batch.add_column(sa.Column("last_run_id", sa.String(), nullable=True))
        batch.add_column(sa.Column("last_error", sa.Text(), nullable=True))
        batch.add_column(sa.Column("failures", sa.Integer(), server_default=sa.text("0"), nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("trigger") as batch:
        batch.drop_column("failures")
        batch.drop_column("last_error")
        batch.drop_column("last_run_id")
