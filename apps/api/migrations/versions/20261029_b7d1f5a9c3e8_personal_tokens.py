"""personal API tokens (G-13)

A person's own API token (`act_…`): it acts as them in one organisation, with at most the role chosen when it was
made. Only its SHA-256 is stored. Row-level security like the other organisation tables (see `db/rls.py`).

Revision ID: b7d1f5a9c3e8
Revises: a9d3e7b1f5c2
Create Date: 2026-10-29 18:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7d1f5a9c3e8"
down_revision: str | None = "f2b6d0a4c8e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY = "agentcanvas_tenant"
# the tables this migration adds to the row-level security of a3c7e9b1d5f2 (same shape as its TABLES)
TABLES: dict[str, tuple[str, str | None]] = {"personal_token": ("org_id", None)}


def _condition(table: str) -> str:
    column, parent = TABLES[table]
    own = (
        f"{column} = current_setting('matura.tenant_id', true)"
        if parent is None
        else f'EXISTS (SELECT 1 FROM {parent} p WHERE p.id = "{table}".{column})'
    )
    return f"current_setting('agentcanvas.all_tenants', true) = 'on' OR {own}"


def upgrade() -> None:
    op.create_table(
        "personal_token",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organization.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(), sa.ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("prefix", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.CheckConstraint("role IN ('admin','builder','reviewer','viewer')", name="personal_token_role"),
    )
    op.create_index("personal_token_owner_idx", "personal_token", ["org_id", "user_id"])
    if op.get_bind().dialect.name == "postgresql":
        for table in TABLES:
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
            op.execute(f'CREATE POLICY {POLICY} ON "{table}" USING ({_condition(table)})')


def downgrade() -> None:
    op.drop_table("personal_token")
