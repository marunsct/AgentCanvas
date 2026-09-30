"""saved test cases (FR-EVAL-01, M6-T9)

Revision ID: 5c1f0a7e2b44
Revises: 18197a219394
Create Date: 2026-09-29 22:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "5c1f0a7e2b44"
down_revision: str | None = "18197a219394"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "test_case",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workflow_id", sa.String(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("input", JSON, server_default=sa.text("'{}'"), nullable=False),
        sa.Column("context", JSON, nullable=True),
        sa.Column("mocks", JSON, server_default=sa.text("'{}'"), nullable=False),
        sa.Column("pins", JSON, server_default=sa.text("'{}'"), nullable=False),
        sa.Column("expect", JSON, server_default=sa.text("'{}'"), nullable=False),
        sa.Column("last_result", JSON, nullable=True),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflow.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_test_case_workflow_id", "test_case", ["workflow_id"])


def downgrade() -> None:
    op.drop_index("ix_test_case_workflow_id", table_name="test_case")
    op.drop_table("test_case")
