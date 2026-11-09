"""a run keeps its events, compact (2026-10-29, G-10)

The trajectory view of a conversation (the main agent, its helpers, steps and framework parts) reads them once the
run's events have left the server's memory.

Revision ID: e1a5c9f3b7d2
Revises: e5a9c1b7d3f2
Create Date: 2026-10-29 18:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e1a5c9f3b7d2"
down_revision: str | None = "e5a9c1b7d3f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("run") as batch:
        batch.add_column(sa.Column("events", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("run") as batch:
        batch.drop_column("events")
