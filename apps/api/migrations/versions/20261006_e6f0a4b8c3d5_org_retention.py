"""organisation retention for conversations (S11 M8-T4)

Revision ID: e6f0a4b8c3d5
Revises: d5e9f3a7b2c4
Create Date: 2026-10-06 00:00:01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e6f0a4b8c3d5"
down_revision: str | None = "d5e9f3a7b2c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("organization") as batch:
        batch.add_column(sa.Column("retention_days", sa.Integer(), nullable=True))
    with op.batch_alter_table("thread") as batch:  # the retention job's scan
        batch.create_index("thread_updated_idx", ["updated_at"])


def downgrade() -> None:
    with op.batch_alter_table("thread") as batch:
        batch.drop_index("thread_updated_idx")
    with op.batch_alter_table("organization") as batch:
        batch.drop_column("retention_days")
