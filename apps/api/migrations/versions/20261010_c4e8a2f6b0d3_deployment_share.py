"""share links and chat widgets per deployment (S23)

`deployment_share`: a deployment's public share link (a capability token), whether it is on, and how its chat page
looks. `share_thread`: the conversations visitors of a link started, with the hash of the visitor's secret.

Revision ID: c4e8a2f6b0d3
Revises: b3c7d1e5f9a2
Create Date: 2026-10-10 12:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c4e8a2f6b0d3"
down_revision: str | None = "b3c7d1e5f9a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "deployment_share",
        sa.Column("deployment_id", sa.String(), sa.ForeignKey("deployment.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("token", sa.Text(), nullable=False, unique=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("title", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("greeting", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("suggestions", JSON, server_default=sa.text("'[]'"), nullable=False),
        sa.Column("input_field", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
    )
    op.create_table(
        "share_thread",
        sa.Column("thread_id", sa.String(), sa.ForeignKey("thread.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("deployment_id", sa.String(), sa.ForeignKey("deployment.id", ondelete="CASCADE"), nullable=False),
        sa.Column("visitor_hash", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
    )
    op.create_index("ix_share_thread_deployment_id", "share_thread", ["deployment_id"])


def downgrade() -> None:
    op.drop_index("ix_share_thread_deployment_id", table_name="share_thread")
    op.drop_table("share_thread")
    op.drop_table("deployment_share")
