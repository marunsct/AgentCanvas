"""heartbeat of running runs, so runs of an instance that died are handed over (S12)

Revision ID: c1d5e9f3a8b0
Revises: b9c3d7e1f6a8
Create Date: 2026-10-06 00:00:05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c1d5e9f3a8b0"
down_revision: str | None = "b9c3d7e1f6a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("run") as batch:
        batch.add_column(sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("run") as batch:
        batch.drop_column("heartbeat_at")
