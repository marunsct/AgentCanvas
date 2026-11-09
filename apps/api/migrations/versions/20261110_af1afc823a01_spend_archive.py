"""what deleted conversations spent, kept for budgets and the spending report (2026-11-10)

Day and month budgets and the spending report added up the runs that still exist. The retention job and deleting a
conversation delete its runs, so a short retention reset a month's budget mid-month, deleting the conversations that
used a budget reset it, and the report for an older month showed only what was not deleted yet. `spend_archive`
keeps what the deleted runs spent, per workflow, UTC hour, deployment and person; it goes with its workflow. On
Postgres it gets the tenant policy like every organisation-owned table (see `agentcanvas/db/rls.py`; the condition
is copied here so later changes to that module do not rewrite this migration).

Revision ID: af1afc823a01
Revises: 2f5f9b223d7f
Create Date: 2026-11-10 15:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "af1afc823a01"
down_revision: str | None = "2f5f9b223d7f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY = "agentcanvas_tenant"
TABLE = "spend_archive"
TABLES: dict[str, tuple[str, str | None]] = {TABLE: ("workflow_id", "workflow")}  # copied, as in a3c7e9b1d5f2


def _condition(table: str) -> str:
    column, parent = TABLES[table]
    own = f'EXISTS (SELECT 1 FROM {parent} p WHERE p.id = "{table}".{column})'
    return f"current_setting('agentcanvas.all_tenants', true) = 'on' OR {own}"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("workflow_id", sa.String(), sa.ForeignKey("workflow.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("hour", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("deployment_id", sa.String(), primary_key=True),
        sa.Column("person", sa.String(), primary_key=True),
        sa.Column("runs", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("tokens_in", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("tokens_out", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("cost_usd", sa.Numeric(14, 6), server_default=sa.text("0"), nullable=False),
        sa.Column("cache_hits", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("fallbacks", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.create_index("spend_archive_hour_idx", TABLE, ["hour"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f'ALTER TABLE "{TABLE}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{TABLE}" FORCE ROW LEVEL SECURITY')
        op.execute(f'DROP POLICY IF EXISTS {POLICY} ON "{TABLE}"')
        op.execute(f'CREATE POLICY {POLICY} ON "{TABLE}" USING ({_condition(TABLE)})')


def downgrade() -> None:
    op.drop_table(TABLE)
