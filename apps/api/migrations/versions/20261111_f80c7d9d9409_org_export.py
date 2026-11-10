"""organisation exports (E-7)

An admin asks for an export of the organisation; a background job on any replica builds it (`lease_until`: the
replica building it now) as a zip of NDJSON files and the organisation's files, kept in parts of a few megabytes in
`org_export_part` so every replica can serve it without shared disk. It is downloaded once, through a short-lived
link whose SHA-256 is kept (`link_hash`), and deleted when it expires. Both tables have the row-level security
policy of the other tenant tables (see `agentcanvas/db/rls.py`).

Revision ID: f80c7d9d9409
Revises: 4b9526b0547e
Create Date: 2026-11-11 11:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f80c7d9d9409"
down_revision: str | None = "4b9526b0547e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY = "agentcanvas_tenant"
# the tables this migration adds to the row-level security of a3c7e9b1d5f2 (same shape as its TABLES)
TABLES: dict[str, tuple[str, str | None]] = {
    "org_export": ("org_id", None),
    "org_export_part": ("export_id", "org_export"),
}


def _condition(table: str) -> str:
    column, parent = TABLES[table]
    if parent is None:
        own = f"{column} = current_setting('matura.tenant_id', true)"
    else:
        own = f'EXISTS (SELECT 1 FROM {parent} p WHERE p.id = "{table}".{column})'
    return f"current_setting('agentcanvas.all_tenants', true) = 'on' OR {own}"


def upgrade() -> None:
    at = sa.DateTime(timezone=True)
    op.create_table(
        "org_export",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organization.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("requested_by", sa.Text(), nullable=True),
        sa.Column("lease_until", at, nullable=True),
        sa.Column("started_at", at, nullable=True),
        sa.Column("finished_at", at, nullable=True),
        sa.Column("expires_at", at, nullable=True),
        sa.Column("size", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("link_hash", sa.Text(), nullable=True, unique=True),
        sa.Column("link_expires_at", at, nullable=True),
        sa.Column("downloaded_at", at, nullable=True),
        sa.Column("created_at", at, server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued','building','ready','downloaded','expired','failed')", name="org_export_status"
        ),
    )
    op.create_index("org_export_org_idx", "org_export", ["org_id", "created_at"])
    op.create_index("org_export_status_idx", "org_export", ["status"])
    op.create_table(
        "org_export_part",
        sa.Column("export_id", sa.String(), sa.ForeignKey("org_export.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("seq", sa.Integer(), primary_key=True),
        sa.Column("data", sa.LargeBinary(), nullable=False),
    )
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY {POLICY} ON "{table}" USING ({_condition(table)})')


def downgrade() -> None:
    op.drop_table("org_export_part")
    op.drop_index("org_export_status_idx", table_name="org_export")
    op.drop_index("org_export_org_idx", table_name="org_export")
    op.drop_table("org_export")
