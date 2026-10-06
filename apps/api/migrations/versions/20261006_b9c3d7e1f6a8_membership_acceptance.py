"""invitations need acceptance across organisations; the last organisation used is the default (S11)

Revision ID: b9c3d7e1f6a8
Revises: a8b2c6d0e5f7
Create Date: 2026-10-06 00:00:04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b9c3d7e1f6a8"
down_revision: str | None = "a8b2c6d0e5f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("membership") as batch:
        batch.add_column(sa.Column("accepted", sa.Boolean(), server_default=sa.text("true"), nullable=False))
        batch.add_column(sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("membership") as batch:
        batch.drop_column("last_used_at")
        batch.drop_column("accepted")
