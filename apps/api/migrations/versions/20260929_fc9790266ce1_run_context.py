"""run context

Revision ID: fc9790266ce1
Revises: e3d37ae82457
Create Date: 2026-09-29 18:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "fc9790266ce1"
down_revision: str | None = "e3d37ae82457"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("run") as batch:
        json = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
        batch.add_column(sa.Column("context", json, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("run") as batch:
        batch.drop_column("context")
