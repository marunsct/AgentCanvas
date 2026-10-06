"""online evaluators, run scores and drift alerts (S13 FR-EVAL-06)

Revision ID: e9f3a7b1c6d8
Revises: d8e2f6a0b5c7
Create Date: 2026-10-06 00:00:13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "e9f3a7b1c6d8"
down_revision: str | None = "d8e2f6a0b5c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(JSONB(), "postgresql")
NOW = sa.text("CURRENT_TIMESTAMP")


def upgrade() -> None:
    op.add_column("run", sa.Column("labels", JSON, nullable=True))
    op.create_table(
        "online_evaluator",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("deployment_id", sa.String(), sa.ForeignKey("deployment.id", ondelete="CASCADE"), nullable=False),
        sa.Column("evaluator_id", sa.String(), sa.ForeignKey("evaluator.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sampling", sa.Float(), nullable=False),
        sa.Column("filter", JSON, nullable=False),
        sa.Column("alert_below", sa.Float(), nullable=True),
        sa.Column("alert_drop", sa.Float(), nullable=True),
        sa.Column("window_minutes", sa.Integer(), server_default=sa.text("60"), nullable=False),
        sa.Column("min_samples", sa.Integer(), server_default=sa.text("5"), nullable=False),
        sa.Column("created_by", sa.String(), sa.ForeignKey("app_user.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.UniqueConstraint("deployment_id", "evaluator_id", name="online_evaluator_once"),
    )
    op.create_index("ix_online_evaluator_deployment_id", "online_evaluator", ["deployment_id"])
    op.create_table(
        "run_score",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("run_id", sa.String(), sa.ForeignKey("run.id", ondelete="CASCADE"), nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("workflow_id", sa.String(), sa.ForeignKey("workflow.id", ondelete="CASCADE"), nullable=False),
        sa.Column("deployment_id", sa.String(), sa.ForeignKey("deployment.id", ondelete="CASCADE"), nullable=True),
        sa.Column("evaluator", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("judge_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("judge_cost_usd", sa.Float(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
    )
    op.create_index("ix_run_score_run_id", "run_score", ["run_id"])
    op.create_index("ix_run_score_workflow_id", "run_score", ["workflow_id"])
    op.create_index("run_score_deployment_idx", "run_score", ["deployment_id", "evaluator", "created_at"])
    op.create_table(
        "eval_alert",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("workflow_id", sa.String(), sa.ForeignKey("workflow.id", ondelete="CASCADE"), nullable=False),
        sa.Column("deployment_id", sa.String(), sa.ForeignKey("deployment.id", ondelete="CASCADE"), nullable=False),
        sa.Column("evaluator", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("baseline", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_eval_alert_workflow_id", "eval_alert", ["workflow_id"])
    op.create_index("eval_alert_open_idx", "eval_alert", ["deployment_id", "evaluator", "kind", "resolved_at"])


def downgrade() -> None:
    op.drop_table("eval_alert")
    op.drop_table("run_score")
    op.drop_table("online_evaluator")
    with op.batch_alter_table("run") as batch:
        batch.drop_column("labels")
