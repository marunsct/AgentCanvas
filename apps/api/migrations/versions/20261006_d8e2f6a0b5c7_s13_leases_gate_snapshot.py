"""experiment owners and release-gate snapshots (S13 item 7)

Revision ID: d8e2f6a0b5c7
Revises: c7d1e5f9a4b6
Create Date: 2026-10-06 00:00:12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "d8e2f6a0b5c7"
down_revision: str | None = "c7d1e5f9a4b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("experiment", sa.Column("owner", sa.Text(), nullable=True))
    op.add_column("release_gate", sa.Column("dataset_version", sa.Integer(), nullable=True))
    op.add_column("release_gate", sa.Column("evaluators", JSON, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("release_gate") as batch:
        batch.drop_column("evaluators")
        batch.drop_column("dataset_version")
    with op.batch_alter_table("experiment") as batch:
        batch.drop_column("owner")
