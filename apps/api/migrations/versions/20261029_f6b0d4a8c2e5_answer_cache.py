"""the model gateway's answer cache in the database (G-8)

One cache per project that every API replica shares (it was a dict of one process). Rows carry the row-level security
policy of the other project-owned tables.

Revision ID: f6b0d4a8c2e5
Revises: e4a8c2f6b0d1
Create Date: 2026-10-29 19:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f6b0d4a8c2e5"
down_revision: str | None = "e4a8c2f6b0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY = "agentcanvas_tenant"
TABLES: dict[str, tuple[str, str | None]] = {"answer_cache": ("project_id", "project")}  # copied, as in a3c7e9b1d5f2


def _condition(table: str) -> str:
    column, parent = TABLES[table]
    own = f'EXISTS (SELECT 1 FROM {parent} p WHERE p.id = "{table}".{column})'
    return f"current_setting('agentcanvas.all_tenants', true) = 'on' OR {own}"


def upgrade() -> None:
    op.create_table(
        "answer_cache",
        sa.Column("project_id", sa.String(), sa.ForeignKey("project.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("answer", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"),
                  nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )  # fmt: skip
    op.create_index("answer_cache_expires_idx", "answer_cache", ["project_id", "expires_at"])
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY {POLICY} ON "{table}" USING ({_condition(table)})')


def downgrade() -> None:
    op.drop_index("answer_cache_expires_idx", table_name="answer_cache")
    op.drop_table("answer_cache")
