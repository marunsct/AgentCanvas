"""organisation data keys for envelope encryption of secrets (S11)

Revision ID: a8b2c6d0e5f7
Revises: f7a1b5c9d4e6
Create Date: 2026-10-06 00:00:03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a8b2c6d0e5f7"
down_revision: str | None = "f7a1b5c9d4e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "org_data_key",
        sa.Column("org_id", sa.String(), sa.ForeignKey("organization.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("master_key_id", sa.Text(), nullable=False),
        sa.Column("wrapped_key", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
    )
    with op.batch_alter_table("secret") as batch:
        batch.add_column(sa.Column("org_key", sa.Boolean(), server_default=sa.text("false"), nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("secret") as batch:
        batch.drop_column("org_key")
    op.drop_table("org_data_key")
