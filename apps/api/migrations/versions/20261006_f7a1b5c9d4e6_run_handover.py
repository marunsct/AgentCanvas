"""runs handed over by a draining server (S11 FR-X-10)

Revision ID: f7a1b5c9d4e6
Revises: e6f0a4b8c3d5
Create Date: 2026-10-06 00:00:02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f7a1b5c9d4e6"
down_revision: str | None = "e6f0a4b8c3d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("run") as batch:
        batch.add_column(sa.Column("handover", sa.Boolean(), server_default=sa.text("false"), nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("run") as batch:
        batch.drop_column("handover")
