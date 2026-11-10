"""the audit outbox: entries still to be delivered to a SIEM (2026-11-10)

Audit forwarding posted each security event once, in the background: a failed delivery, or a server stopping with
deliveries in flight, lost it. `audit_outbox` holds one row per entry and destination, written in the same
transaction as the audit row; a delivery worker on every API server claims due rows (skipping rows another server
holds), retries failures with a growing delay, and marks a row `dead` after the last attempt. Delivered rows are
deleted. On Postgres it gets the tenant policy like every organisation-owned table (see `agentcanvas/db/rls.py`; the
condition is copied here so later changes to that module do not rewrite this migration).

Revision ID: 7d5c15cdde99
Revises: f80c7d9d9409
Create Date: 2026-11-10 20:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7d5c15cdde99"
down_revision: str | None = "f80c7d9d9409"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY = "agentcanvas_tenant"
TABLE = "audit_outbox"
TABLES: dict[str, tuple[str, str | None]] = {TABLE: ("org_id", None)}  # copied, as in a3c7e9b1d5f2


def _condition(table: str) -> str:
    column, _ = TABLES[table]
    own = f"{column} = current_setting('matura.tenant_id', true)"
    return f"current_setting('agentcanvas.all_tenants', true) = 'on' OR {own}"


BIG = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", BIG, autoincrement=True, primary_key=True),
        sa.Column("audit_id", BIG, sa.ForeignKey("audit_log.id", ondelete="CASCADE"), nullable=False),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organization.id"), nullable=True),
        sa.Column("destination", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.CheckConstraint("state IN ('pending','dead')", name="audit_outbox_state"),
    )
    op.create_index("audit_outbox_due_idx", TABLE, ["state", "next_attempt_at"])
    op.create_index("audit_outbox_audit_idx", TABLE, ["audit_id"])  # deleting audit rows deletes theirs
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f'ALTER TABLE "{TABLE}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{TABLE}" FORCE ROW LEVEL SECURITY')
        op.execute(f'DROP POLICY IF EXISTS {POLICY} ON "{TABLE}"')
        op.execute(f'CREATE POLICY {POLICY} ON "{TABLE}" USING ({_condition(TABLE)})')


def downgrade() -> None:
    op.drop_table(TABLE)
