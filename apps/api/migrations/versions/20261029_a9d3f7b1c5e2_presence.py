"""presence in the editor (2026-10-29, G-4)

`presence` keeps, per open editor tab, who is on a workflow and which block they have selected, so people on
different API replicas see each other. On Postgres it gets the tenant policy like every organisation-owned table (see
`agentcanvas/db/rls.py`; the condition is copied here so later changes to that module do not rewrite this migration).

Revision ID: a9d3f7b1c5e2
Revises: f3b7d1a9c5e8
Create Date: 2026-10-29 20:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a9d3f7b1c5e2"
down_revision: str | None = "f3b7d1a9c5e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY = "agentcanvas_tenant"
TABLE = "presence"
TABLES: dict[str, tuple[str, str | None]] = {TABLE: ("workflow_id", "workflow")}  # copied, as in a3c7e9b1d5f2


def _condition(table: str) -> str:
    column, parent = TABLES[table]
    own = f'EXISTS (SELECT 1 FROM {parent} p WHERE p.id = "{table}".{column})'
    return f"current_setting('agentcanvas.all_tenants', true) = 'on' OR {own}"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("workflow_id", sa.String(), sa.ForeignKey("workflow.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("session_id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("selected", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column("seen_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    op.create_index("presence_seen_idx", TABLE, ["workflow_id", "seen_at"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f'ALTER TABLE "{TABLE}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{TABLE}" FORCE ROW LEVEL SECURITY')
        op.execute(f'DROP POLICY IF EXISTS {POLICY} ON "{TABLE}"')
        op.execute(f'CREATE POLICY {POLICY} ON "{TABLE}" USING ({_condition(TABLE)})')


def downgrade() -> None:
    op.drop_table(TABLE)
