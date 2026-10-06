"""request counts for the public API rate limits (S12)

Revision ID: d2e6f0a4b9c1
Revises: c1d5e9f3a8b0
Create Date: 2026-10-06 00:00:06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d2e6f0a4b9c1"
down_revision: str | None = "c1d5e9f3a8b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rate_window",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("slot", sa.Integer(), primary_key=True),
        sa.Column("count", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("rate_window")
