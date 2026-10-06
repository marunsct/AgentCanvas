"""experiments remember the workspace head they ran with (S12 review)

Revision ID: c7d1e5f9a4b6
Revises: b6c0d4e8f3a5
Create Date: 2026-10-06 00:00:11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7d1e5f9a4b6"
down_revision: str | None = "b6c0d4e8f3a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("experiment", sa.Column("workspace_commit", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("experiment") as batch:
        batch.drop_column("workspace_commit")
