"""datasets and their versions (S12 FR-EVAL-02)

Revision ID: e3f7a1b5c0d2
Revises: d2e6f0a4b9c1
Create Date: 2026-10-06 00:00:07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "e3f7a1b5c0d2"
down_revision: str | None = "d2e6f0a4b9c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "dataset",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("workflow_id", sa.String(), sa.ForeignKey("workflow.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("created_by", sa.String(), sa.ForeignKey("app_user.id"), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
    )
    op.create_index("ix_dataset_workflow_id", "dataset", ["workflow_id"])
    op.create_table(
        "dataset_version",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("dataset_id", sa.String(), sa.ForeignKey("dataset.id", ondelete="CASCADE"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("message", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("examples", JSON, nullable=False),
        sa.Column("created_by", sa.String(), sa.ForeignKey("app_user.id"), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.UniqueConstraint("dataset_id", "number", name="dataset_version_number"),
    )
    op.create_index("ix_dataset_version_dataset_id", "dataset_version", ["dataset_id"])


def downgrade() -> None:
    op.drop_table("dataset_version")
    op.drop_table("dataset")
