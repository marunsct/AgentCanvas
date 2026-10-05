"""cron triggers claim each minute once across replicas (S11)

Revision ID: d5e9f3a7b2c4
Revises: c4d8e2f6a1b3
Create Date: 2026-10-06 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5e9f3a7b2c4"
down_revision: str | None = "c4d8e2f6a1b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("trigger") as batch:
        batch.add_column(sa.Column("last_fired", sa.String(length=16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("trigger") as batch:
        batch.drop_column("last_fired")
