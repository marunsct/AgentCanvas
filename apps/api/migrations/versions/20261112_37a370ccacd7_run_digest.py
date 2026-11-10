"""weekly run digests (2026-11-12)

`run_digest` keeps one digest per project and UTC week (failure groups, spend, latency, the most flagged runs), made
by whichever server inserts it first: the unique key is the claim, so several servers make it once. It goes with its
project. On Postgres it gets the tenant policy like every organisation-owned table (see `agentcanvas/db/rls.py`; the
condition is copied here so later changes to that module do not rewrite this migration). A new, empty table: nothing
existing is rewritten or locked for long.

Revision ID: 37a370ccacd7
Revises: 0fe17eb4eb92
Create Date: 2026-11-12 09:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "37a370ccacd7"
down_revision: str | None = "0fe17eb4eb92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(JSONB(), "postgresql")
POLICY = "agentcanvas_tenant"
TABLE = "run_digest"
TABLES: dict[str, tuple[str, str | None]] = {TABLE: ("project_id", "project")}  # copied, as in a3c7e9b1d5f2


def _condition(table: str) -> str:
    column, parent = TABLES[table]
    own = f'EXISTS (SELECT 1 FROM {parent} p WHERE p.id = "{table}".{column})'
    return f"current_setting('agentcanvas.all_tenants', true) = 'on' OR {own}"


def upgrade() -> None:
    now = sa.text("CURRENT_TIMESTAMP")
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("week_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("summary", JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.UniqueConstraint("project_id", "week_start", name="run_digest_once"),
    )
    op.create_index("run_digest_week_idx", TABLE, ["week_start"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f'ALTER TABLE "{TABLE}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{TABLE}" FORCE ROW LEVEL SECURITY')
        op.execute(f'DROP POLICY IF EXISTS {POLICY} ON "{TABLE}"')
        op.execute(f'CREATE POLICY {POLICY} ON "{TABLE}" USING ({_condition(TABLE)})')


def downgrade() -> None:
    op.drop_table(TABLE)
