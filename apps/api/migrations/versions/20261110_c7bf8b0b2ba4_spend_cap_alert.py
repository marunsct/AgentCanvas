"""spending caps per organisation and project: their cost alerts (2026-11-10)

An organisation (Settings > Spending, or `limits` of `PUT /v1/org/settings`) and each of its projects may cap what
they spend a day and a month. The caps themselves live in the organisation's and the project's `settings`; this table
keeps each alert raised when spending reaches a share of a cap, once per scope (`''` for the organisation, else the
project's id), period and share. On Postgres it gets the tenant policy like every organisation-owned table (see
`agentcanvas/db/rls.py`; the condition is copied here so later changes to that module do not rewrite this migration).

Revision ID: c7bf8b0b2ba4
Revises: 1ec6eb9f62ed
Create Date: 2026-11-10 20:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7bf8b0b2ba4"
down_revision: str | None = "1ec6eb9f62ed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY = "agentcanvas_tenant"
TABLE = "spend_cap_alert"
TABLES: dict[str, tuple[str, str | None]] = {TABLE: ("org_id", None)}  # copied, as in a3c7e9b1d5f2


def _condition(table: str) -> str:
    column, _ = TABLES[table]
    own = f"{column} = current_setting('matura.tenant_id', true)"
    return f"current_setting('agentcanvas.all_tenants', true) = 'on' OR {own}"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organization.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("project_id", sa.String(), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=True),
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
        sa.UniqueConstraint("org_id", "scope", "period", "period_start", "share", name="spend_cap_alert_once"),
    )
    op.create_index("spend_cap_alert_org_idx", TABLE, ["org_id", "created_at"])
    op.create_index("ix_spend_cap_alert_project_id", TABLE, ["project_id"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f'ALTER TABLE "{TABLE}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{TABLE}" FORCE ROW LEVEL SECURITY')
        op.execute(f'DROP POLICY IF EXISTS {POLICY} ON "{TABLE}"')
        op.execute(f'CREATE POLICY {POLICY} ON "{TABLE}" USING ({_condition(TABLE)})')


def downgrade() -> None:
    op.drop_table(TABLE)
