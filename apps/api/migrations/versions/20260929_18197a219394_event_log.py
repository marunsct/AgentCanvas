"""event log and handler dedup (FR-X-22)

Revision ID: 18197a219394
Revises: fc9790266ce1
Create Date: 2026-09-29 20:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "18197a219394"
down_revision: str | None = "fc9790266ce1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "event_log",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), server_default=sa.text("'api'"), nullable=False),
        sa.Column("data", JSON, server_default=sa.text("'{}'"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("results", JSON, server_default=sa.text("'[]'"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("event_log_project_idx", "event_log", ["project_id", "created_at"])
    op.create_table(
        "event_dedup",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("handler", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "handler", "value"),
    )


def downgrade() -> None:
    op.drop_table("event_dedup")
    op.drop_index("event_log_project_idx", table_name="event_log")
    op.drop_table("event_log")
