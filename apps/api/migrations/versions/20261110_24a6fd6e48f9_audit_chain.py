"""the audit log's hash chain (2026-11-10)

Each organisation's audit entries are linked: `prev_hash` is the previous entry's `row_hash`, and `row_hash` the
SHA-256 of the entry's canonical form with it (`agentcanvas/services/audit_chain.py`), so a changed or deleted entry
shows when the chain is verified. Entries written before this migration keep no hash. `audit_anchor` records where
retention left a chain (the last entry it deleted). The index on (org_id, id) finds an organisation's latest entry
for every write, and walks its chain in order; on Postgres it is built concurrently, after the columns are committed,
so the audit log stays writable while it builds. `audit_anchor` gets the tenant policy like every organisation-owned
table (see `agentcanvas/db/rls.py`; the condition is copied here).

Revision ID: 24a6fd6e48f9
Revises: 7d5c15cdde99
Create Date: 2026-11-10 21:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "24a6fd6e48f9"
down_revision: str | None = "7d5c15cdde99"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY = "agentcanvas_tenant"
TABLE = "audit_anchor"
TABLES: dict[str, tuple[str, str | None]] = {TABLE: ("org_id", None)}  # copied, as in a3c7e9b1d5f2
BIG = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def _condition(table: str) -> str:
    column, _ = TABLES[table]
    own = f"{column} = current_setting('matura.tenant_id', true)"
    return f"current_setting('agentcanvas.all_tenants', true) = 'on' OR {own}"


def upgrade() -> None:
    with op.batch_alter_table("audit_log") as batch:  # nullable columns: no rewrite of the table on Postgres
        batch.add_column(sa.Column("prev_hash", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("row_hash", sa.String(length=64), nullable=True))
    op.create_table(
        TABLE,
        sa.Column("id", BIG, autoincrement=True, primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column("through_id", sa.BigInteger(), nullable=False),
        sa.Column("last_hash", sa.String(length=64), nullable=True),
        sa.Column("rows", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
    )
    op.create_index("audit_anchor_org_idx", TABLE, ["org_id", "id"])
    if op.get_bind().dialect.name != "postgresql":
        op.create_index("audit_org_id_idx", "audit_log", ["org_id", "id"], postgresql_concurrently=True)  # not here
        return
    op.execute(f'ALTER TABLE "{TABLE}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{TABLE}" FORCE ROW LEVEL SECURITY')
    op.execute(f'DROP POLICY IF EXISTS {POLICY} ON "{TABLE}"')
    op.execute(f'CREATE POLICY {POLICY} ON "{TABLE}" USING ({_condition(TABLE)})')
    with op.get_context().autocommit_block():  # CREATE INDEX CONCURRENTLY cannot run in a transaction
        # a build cut short leaves an invalid index behind: it is built again, never kept
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS audit_org_id_idx")
        op.create_index("audit_org_id_idx", "audit_log", ["org_id", "id"], postgresql_concurrently=True)


def downgrade() -> None:
    op.drop_index("audit_org_id_idx", table_name="audit_log")
    op.drop_table(TABLE)
    with op.batch_alter_table("audit_log") as batch:
        batch.drop_column("row_hash")
        batch.drop_column("prev_hash")
