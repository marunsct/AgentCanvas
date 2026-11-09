"""what the Copilot and AI judges spent, for spending caps and the spending report (2026-11-10)

Their model calls are priced (each Copilot answer keeps its cost on its message, each judge's cost is on its score),
but nowhere that adds up cheaply per period. `model_spend` keeps what they spent per project, UTC hour and kind
("copilot", "judge"), so an organisation's or project's spending caps and the spending report count it with the runs;
it goes with its project. On Postgres it gets the tenant policy like every organisation-owned table (see
`agentcanvas/db/rls.py`; the condition is copied here so later changes to that module do not rewrite this migration).

Revision ID: 29547c665705
Revises: c7bf8b0b2ba4
Create Date: 2026-11-10 20:30:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "29547c665705"
down_revision: str | None = "c7bf8b0b2ba4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY = "agentcanvas_tenant"
TABLE = "model_spend"
TABLES: dict[str, tuple[str, str | None]] = {TABLE: ("project_id", "project")}  # copied, as in a3c7e9b1d5f2


def _condition(table: str) -> str:
    column, parent = TABLES[table]
    own = f'EXISTS (SELECT 1 FROM {parent} p WHERE p.id = "{table}".{column})'
    return f"current_setting('agentcanvas.all_tenants', true) = 'on' OR {own}"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("project_id", sa.String(), sa.ForeignKey("project.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("hour", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("kind", sa.String(), primary_key=True),
        sa.Column("calls", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("tokens_in", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("tokens_out", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("cost_usd", sa.Numeric(14, 6), server_default=sa.text("0"), nullable=False),
    )
    op.create_index("model_spend_hour_idx", TABLE, ["hour"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f'ALTER TABLE "{TABLE}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{TABLE}" FORCE ROW LEVEL SECURITY')
        op.execute(f'DROP POLICY IF EXISTS {POLICY} ON "{TABLE}"')
        op.execute(f'CREATE POLICY {POLICY} ON "{TABLE}" USING ({_condition(TABLE)})')


def downgrade() -> None:
    op.drop_table(TABLE)
