"""model gateway and cost controls (S26)

`run.cache_hits` and `run.fallbacks` count what the model gateway did in a run; `cost_alert` keeps the alerts raised
when a workflow's spending reaches a share of its day or month budget.

Revision ID: e7a1c5f9b3d6
Revises: d5f9b3a7c1e4
Create Date: 2026-10-10 20:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7a1c5f9b3d6"
down_revision: str | None = "d5f9b3a7c1e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("run", sa.Column("cache_hits", sa.Integer(), server_default=sa.text("0"), nullable=False))
    op.add_column("run", sa.Column("fallbacks", sa.Integer(), server_default=sa.text("0"), nullable=False))
    op.create_table(
        "cost_alert",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("workflow_id", sa.String(), sa.ForeignKey("workflow.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=True),
        sa.Column("period", sa.Text(), nullable=False),
        sa.Column("period_start", sa.String(10), nullable=False),
        sa.Column("share", sa.Float(), nullable=False),
        sa.Column("spent_usd", sa.Float(), nullable=False),
        sa.Column("limit_usd", sa.Float(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.UniqueConstraint("workflow_id", "period", "period_start", "share", name="cost_alert_once"),
    )
    op.create_index("cost_alert_workflow_idx", "cost_alert", ["workflow_id", "created_at"])


def downgrade() -> None:
    op.drop_index("cost_alert_workflow_idx", table_name="cost_alert")
    op.drop_table("cost_alert")
    op.drop_column("run", "fallbacks")
    op.drop_column("run", "cache_hits")
