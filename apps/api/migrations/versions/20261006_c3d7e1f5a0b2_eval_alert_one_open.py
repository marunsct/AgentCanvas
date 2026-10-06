"""eval_alert: at most one open alert per deployment, evaluator and rule (S13 review)

Revision ID: c3d7e1f5a0b2
Revises: b2c6d0e4f9a1
Create Date: 2026-10-06 00:00:17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d7e1f5a0b2"
down_revision: str | None = "b2c6d0e4f9a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OPEN = sa.text("resolved_at IS NULL")


def upgrade() -> None:
    # resolve duplicates a race may have opened, keeping the oldest open alert of each rule
    op.execute(
        "UPDATE eval_alert SET resolved_at = created_at WHERE resolved_at IS NULL AND id NOT IN ("
        " SELECT MIN(id) FROM eval_alert WHERE resolved_at IS NULL GROUP BY deployment_id, evaluator, kind)"
    )
    op.create_index(
        "eval_alert_one_open", "eval_alert", ["deployment_id", "evaluator", "kind"], unique=True,
        sqlite_where=OPEN, postgresql_where=OPEN,
    )  # fmt: skip


def downgrade() -> None:
    op.drop_index("eval_alert_one_open", table_name="eval_alert")
