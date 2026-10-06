"""experiment.simulation: simulated-user conversations (S13 FR-EVAL-07)

Revision ID: a1b5c9d3e8f0
Revises: f0a4b8c2d7e9
Create Date: 2026-10-06 00:00:15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "a1b5c9d3e8f0"
down_revision: str | None = "f0a4b8c2d7e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("experiment", sa.Column("simulation", JSON, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("experiment") as batch:
        batch.drop_column("simulation")
