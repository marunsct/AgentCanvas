"""release gates (S12 FR-EVAL-05)

Revision ID: b6c0d4e8f3a5
Revises: a5b9c3d7e2f4
Create Date: 2026-10-06 00:00:10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "b6c0d4e8f3a5"
down_revision: str | None = "a5b9c3d7e2f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "release_gate",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("workflow_id", sa.String(), sa.ForeignKey("workflow.id", ondelete="CASCADE"), nullable=False),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("dataset_id", sa.String(), sa.ForeignKey("dataset.id", ondelete="CASCADE"), nullable=False),
        sa.Column("min_scores", JSON, nullable=False),
        sa.Column("max_regression", sa.Float(), nullable=True),
        sa.Column("updated_by", sa.String(), sa.ForeignKey("app_user.id"), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.UniqueConstraint("workflow_id", "environment", name="release_gate_environment"),
    )
    op.create_index("ix_release_gate_workflow_id", "release_gate", ["workflow_id"])


def downgrade() -> None:
    op.drop_table("release_gate")
