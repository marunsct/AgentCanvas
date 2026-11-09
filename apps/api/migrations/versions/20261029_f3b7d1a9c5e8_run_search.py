"""semantic search over runs (2026-10-29, G-10)

`run_search` keeps each finished run's input and answer with an embedding, for "find conversations where …". On
Postgres it gets the tenant policy like every organisation-owned table (see `agentcanvas/db/rls.py`; the condition
is copied here so later changes to that module do not rewrite this migration).

Revision ID: f3b7d1a9c5e8
Revises: e1a5c9f3b7d2
Create Date: 2026-10-29 19:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3b7d1a9c5e8"
down_revision: str | None = "e1a5c9f3b7d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY = "agentcanvas_tenant"
TABLE = "run_search"
TABLES: dict[str, tuple[str, str | None]] = {TABLE: ("workflow_id", "workflow")}  # copied, as in a3c7e9b1d5f2


def _condition(table: str) -> str:
    column, parent = TABLES[table]
    own = f'EXISTS (SELECT 1 FROM {parent} p WHERE p.id = "{table}".{column})'
    return f"current_setting('agentcanvas.all_tenants', true) = 'on' OR {own}"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("run_id", sa.String(), sa.ForeignKey("run.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("workflow_id", sa.String(), sa.ForeignKey("workflow.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.String(), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("vector", sa.LargeBinary(), nullable=True),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
    )
    op.create_index("ix_run_search_thread_id", TABLE, ["thread_id"])
    op.create_index("ix_run_search_workflow_id", TABLE, ["workflow_id"])
    op.create_index("run_search_project_idx", TABLE, ["project_id", "created_at"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f'ALTER TABLE "{TABLE}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{TABLE}" FORCE ROW LEVEL SECURITY')
        op.execute(f'DROP POLICY IF EXISTS {POLICY} ON "{TABLE}"')
        op.execute(f'CREATE POLICY {POLICY} ON "{TABLE}" USING ({_condition(TABLE)})')


def downgrade() -> None:
    op.drop_table(TABLE)
