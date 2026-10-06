"""evaluators (S12 FR-EVAL-03)

Revision ID: f4a8b2c6d1e3
Revises: e3f7a1b5c0d2
Create Date: 2026-10-06 00:00:08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "f4a8b2c6d1e3"
down_revision: str | None = "e3f7a1b5c0d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "evaluator",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("workflow_id", sa.String(), sa.ForeignKey("workflow.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("config", JSON, nullable=False),
        sa.Column("threshold", sa.Float(), nullable=True),
        sa.Column("created_by", sa.String(), sa.ForeignKey("app_user.id"), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.UniqueConstraint("workflow_id", "name", name="evaluator_name"),
    )
    op.create_index("ix_evaluator_workflow_id", "evaluator", ["workflow_id"])


def downgrade() -> None:
    op.drop_table("evaluator")
