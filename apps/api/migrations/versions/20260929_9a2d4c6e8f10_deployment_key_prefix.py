"""deployment key prefix (M6-T4)

Revision ID: 9a2d4c6e8f10
Revises: 5c1f0a7e2b44
Create Date: 2026-09-29 23:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9a2d4c6e8f10"
down_revision: str | None = "5c1f0a7e2b44"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("deployment") as batch:
        batch.add_column(sa.Column("key_prefix", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("deployment") as batch:
        batch.drop_column("key_prefix")
