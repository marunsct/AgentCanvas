"""organisation policies (FR-X-19)

Revision ID: c4d8e2f6a1b3
Revises: b7e1f3a9c2d5
Create Date: 2026-10-05 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c4d8e2f6a1b3"
down_revision: str | None = "b7e1f3a9c2d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "org_policy",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("org_id", sa.String(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("match", sa.Text(), server_default=sa.text("'true'"), nullable=False),
        sa.Column("require", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), server_default=sa.text("'error'"), nullable=False),
        sa.Column("environments", JSON, server_default=sa.text("'[]'"), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.CheckConstraint("scope IN ('node','resource','workflow')", name="org_policy_scope"),
        sa.CheckConstraint("severity IN ('error','warning')", name="org_policy_severity"),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "name"),
    )


def downgrade() -> None:
    op.drop_table("org_policy")
