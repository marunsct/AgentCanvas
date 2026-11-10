"""completion callbacks of deployment runs (2026-11-11)

A deployment API caller may ask to be called back when its run ends (`callback_url`): `run_callback` keeps each
such callback until it is delivered (or given up on), so any server can send it and try again after a failure.
`deployment.callback_secret` signs them, encrypted with the organisation's data key; it is empty until a builder
creates one. A new table and a nullable column: nothing existing is rewritten.

Revision ID: 77fe2c6e6cec
Revises: cdeb54ba6bf8
Create Date: 2026-11-11 10:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "77fe2c6e6cec"
down_revision: str | None = "cdeb54ba6bf8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


POLICY = "agentcanvas_tenant"
TABLE = "run_callback"
TABLES: dict[str, tuple[str, str | None]] = {TABLE: ("run_id", "run")}  # copied, as in a3c7e9b1d5f2


def _condition(table: str) -> str:
    column, parent = TABLES[table]
    own = f'EXISTS (SELECT 1 FROM {parent} p WHERE p.id = "{table}".{column})'
    return f"current_setting('agentcanvas.all_tenants', true) = 'on' OR {own}"


def upgrade() -> None:
    NOW = sa.text("CURRENT_TIMESTAMP")
    with op.batch_alter_table("deployment") as batch:
        batch.add_column(sa.Column("callback_secret", sa.Text(), nullable=True))
    op.create_table(
        "run_callback",
        sa.Column("run_id", sa.String(), sa.ForeignKey("run.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("deployment_id", sa.String(), sa.ForeignKey("deployment.id", ondelete="CASCADE"), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("attempts", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("last_status", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.CheckConstraint("status IN ('pending','delivered','failed')", name="run_callback_status"),
    )
    due = sa.text("status = 'pending'")
    op.create_index("run_callback_due_idx", "run_callback", ["next_at"], postgresql_where=due, sqlite_where=due)
    op.create_index("run_callback_deployment_idx", "run_callback", ["deployment_id"])
    if op.get_bind().dialect.name == "postgresql":  # a run's callback belongs to the run's organisation
        op.execute(f'ALTER TABLE "{TABLE}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{TABLE}" FORCE ROW LEVEL SECURITY')
        op.execute(f'DROP POLICY IF EXISTS {POLICY} ON "{TABLE}"')
        op.execute(f'CREATE POLICY {POLICY} ON "{TABLE}" USING ({_condition(TABLE)})')


def downgrade() -> None:
    op.drop_index("run_callback_deployment_idx", table_name="run_callback")
    op.drop_index("run_callback_due_idx", table_name="run_callback")
    op.drop_table("run_callback")
    with op.batch_alter_table("deployment") as batch:
        batch.drop_column("callback_secret")
