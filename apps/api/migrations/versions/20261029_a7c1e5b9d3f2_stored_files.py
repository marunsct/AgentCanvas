"""files people attach to runs (2026-10-29, G-5)

Uploaded in the Run tab, through a share link, or sent inline to the API: runs carry a reference, the bytes stay
here. The table belongs to a project, so it gets the tenant policy of every project table (TS26).

Revision ID: a7c1e5b9d3f2
Revises: a9d3e7b1f5c2
Create Date: 2026-10-29 18:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c1e5b9d3f2"
down_revision: str | None = "a9d3e7b1f5c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY = "agentcanvas_tenant"
# table -> (column, parent table), as in migration a3c7e9b1d5f2 and db/rls.py
TABLES: dict[str, tuple[str, str | None]] = {"stored_file": ("project_id", "project")}


def _condition(table: str) -> str:
    column, parent = TABLES[table]
    return (
        f"current_setting('agentcanvas.all_tenants', true) = 'on' OR "
        f'EXISTS (SELECT 1 FROM {parent} p WHERE p.id = "{table}".{column})'
    )


def upgrade() -> None:
    now = sa.text("CURRENT_TIMESTAMP")
    op.create_table(
        "stored_file",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("source", sa.Text(), server_default=sa.text("'upload'"), nullable=False),
        sa.Column("deployment_id", sa.String(), sa.ForeignKey("deployment.id", ondelete="CASCADE"), nullable=True),
        sa.Column("visitor_hash", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
    )
    op.create_index("stored_file_project_idx", "stored_file", ["project_id", "last_used_at"])
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(f'DROP POLICY IF EXISTS {POLICY} ON "{table}"')
        op.execute(f'CREATE POLICY {POLICY} ON "{table}" USING ({_condition(table)})')


def downgrade() -> None:
    op.drop_index("stored_file_project_idx", table_name="stored_file")
    op.drop_table("stored_file")  # its policy goes with it
