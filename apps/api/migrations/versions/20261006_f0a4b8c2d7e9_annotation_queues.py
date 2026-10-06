"""annotation queues, their items and reviewers' annotations (S13)

Revision ID: f0a4b8c2d7e9
Revises: e9f3a7b1c6d8
Create Date: 2026-10-06 00:00:14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "f0a4b8c2d7e9"
down_revision: str | None = "e9f3a7b1c6d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(JSONB(), "postgresql")
NOW = sa.text("CURRENT_TIMESTAMP")


def upgrade() -> None:
    op.create_table(
        "annotation_queue",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("workflow_id", sa.String(), sa.ForeignKey("workflow.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("rubric", JSON, nullable=False),
        sa.Column("rules", JSON, nullable=False),
        sa.Column("reviewers_per_item", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_by", sa.String(), sa.ForeignKey("app_user.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.UniqueConstraint("workflow_id", "name", name="annotation_queue_name"),
    )
    op.create_index("ix_annotation_queue_workflow_id", "annotation_queue", ["workflow_id"])
    op.create_table(
        "annotation_item",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("queue_id", sa.String(), sa.ForeignKey("annotation_queue.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", sa.String(), sa.ForeignKey("run.id", ondelete="CASCADE"), nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("added_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.UniqueConstraint("queue_id", "run_id", name="annotation_item_once"),
    )
    op.create_index("annotation_item_queue_idx", "annotation_item", ["queue_id", "status", "created_at"])
    op.create_table(
        "annotation",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("item_id", sa.String(), sa.ForeignKey("annotation_item.id", ondelete="CASCADE"), nullable=False),
        sa.Column("reviewer", sa.Text(), nullable=False),
        sa.Column("scores", JSON, nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.UniqueConstraint("item_id", "reviewer", name="annotation_once"),
    )
    op.create_index("ix_annotation_item_id", "annotation", ["item_id"])


def downgrade() -> None:
    op.drop_table("annotation")
    op.drop_table("annotation_item")
    op.drop_table("annotation_queue")
