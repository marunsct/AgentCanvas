"""dataset.langsmith: the LangSmith dataset a dataset is synced with (S13)

Revision ID: b2c6d0e4f9a1
Revises: a1b5c9d3e8f0
Create Date: 2026-10-06 00:00:16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "b2c6d0e4f9a1"
down_revision: str | None = "a1b5c9d3e8f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("dataset", sa.Column("langsmith", JSON, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("dataset") as batch:
        batch.drop_column("langsmith")
