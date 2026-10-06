"""experiments (S12 FR-EVAL-04)

Revision ID: a5b9c3d7e2f4
Revises: f4a8b2c6d1e3
Create Date: 2026-10-06 00:00:09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "a5b9c3d7e2f4"
down_revision: str | None = "f4a8b2c6d1e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(JSONB(), "postgresql")
NOW = sa.text("CURRENT_TIMESTAMP")


def upgrade() -> None:
    op.create_table(
        "experiment",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("workflow_id", sa.String(), sa.ForeignKey("workflow.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("dataset_id", sa.String(), sa.ForeignKey("dataset.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dataset_version", sa.Integer(), nullable=False),
        sa.Column("split", sa.Text(), nullable=True),
        sa.Column("targets", JSON, nullable=False),
        sa.Column("evaluators", JSON, nullable=False),
        sa.Column("concurrency", sa.Integer(), server_default=sa.text("4"), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'running'"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(), sa.ForeignKey("app_user.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('running','done','cancelled','error')", name="experiment_status"),
    )
    op.create_index("ix_experiment_workflow_id", "experiment", ["workflow_id"])
    op.create_index("experiment_status_idx", "experiment", ["status", "heartbeat_at"])
    op.create_table(
        "experiment_result",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("experiment_id", sa.String(), sa.ForeignKey("experiment.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target", sa.Integer(), nullable=False),
        sa.Column("example_id", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=True),
        sa.Column("thread_id", sa.Text(), nullable=True),
        sa.Column("run_status", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("output", JSON, nullable=True),
        sa.Column("trajectory", JSON, nullable=True),
        sa.Column("scores", JSON, nullable=True),
        sa.Column("tokens_in", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("tokens_out", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("cost_usd", sa.Float(), server_default=sa.text("0"), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.CheckConstraint("status IN ('pending','running','done')", name="experiment_result_status"),
        sa.UniqueConstraint("experiment_id", "target", "example_id", name="experiment_result_example"),
    )
    op.create_index("ix_experiment_result_experiment_id", "experiment_result", ["experiment_id"])


def downgrade() -> None:
    op.drop_table("experiment_result")
    op.drop_table("experiment")
