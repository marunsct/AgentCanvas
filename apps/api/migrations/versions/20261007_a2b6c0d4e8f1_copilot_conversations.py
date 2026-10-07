"""Copilot conversations (S14)

Revision ID: a2b6c0d4e8f1
Revises: d4e8f2a6b1c3
Create Date: 2026-10-07 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a2b6c0d4e8f1"
down_revision: str | None = "d4e8f2a6b1c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "copilot_conversation",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("workflow_id", sa.String(), sa.ForeignKey("workflow.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("messages", JSON, server_default=sa.text("'[]'"), nullable=False),
        sa.Column("brief", JSON, nullable=True),
        sa.Column("proposals", JSON, server_default=sa.text("'[]'"), nullable=False),
        sa.Column("usage", JSON, server_default=sa.text("'{}'"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.UniqueConstraint("workflow_id", "user_id"),
    )


def downgrade() -> None:
    op.drop_table("copilot_conversation")
