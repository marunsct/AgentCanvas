"""remote A2A replies of paused steps in the database (G-9)

A LangGraph step that calls a remote A2A agent pauses when the agent asks a question; the replies it got so far were
kept in one API process, so the answer reaching another replica, or coming after a restart, could not be continued.
They are kept per (thread, step) in `a2a_reply`, with the row-level security policy of the other tenant tables
(through the thread, see `agentcanvas/db/rls.py`).

Revision ID: e4a8c2f6b0d1
Revises: f3c7d1e5a9b4
Create Date: 2026-10-29 18:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e4a8c2f6b0d1"
down_revision: str | None = "f3c7d1e5a9b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY = "agentcanvas_tenant"
TABLES: dict[str, tuple[str, str | None]] = {"a2a_reply": ("thread_id", "thread")}  # copied, as in a3c7e9b1d5f2


def _condition(table: str) -> str:
    column, parent = TABLES[table]
    own = f'EXISTS (SELECT 1 FROM {parent} p WHERE p.id = "{table}".{column})'
    return f"current_setting('agentcanvas.all_tenants', true) = 'on' OR {own}"


def upgrade() -> None:
    op.create_table(
        "a2a_reply",
        sa.Column("thread_id", sa.String(), sa.ForeignKey("thread.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("step", sa.Text(), primary_key=True),
        sa.Column("replies", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), server_default=sa.text("'[]'"),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"),
                  nullable=False),
    )  # fmt: skip
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY {POLICY} ON "{table}" USING ({_condition(table)})')


def downgrade() -> None:
    op.drop_table("a2a_reply")
