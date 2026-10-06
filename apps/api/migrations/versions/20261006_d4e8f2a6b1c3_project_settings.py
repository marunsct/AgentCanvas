"""project settings: the default model chosen in first-run setup

Revision ID: d4e8f2a6b1c3
Revises: c3d7e1f5a0b2
Create Date: 2026-10-06 00:00:03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d4e8f2a6b1c3"
down_revision: str | None = "c3d7e1f5a0b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("project") as batch:
        batch.add_column(
            sa.Column(
                "settings",
                sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
                server_default=sa.text("'{}'"),
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("project") as batch:
        batch.drop_column("settings")
