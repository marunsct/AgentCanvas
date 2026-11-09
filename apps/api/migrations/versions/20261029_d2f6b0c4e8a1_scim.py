"""SCIM 2.0 provisioning (G-13)

An organisation's SCIM bearer token (stored hashed), suspended memberships (`active: false`), the identity
provider's id for a person, and the groups it provisions with their members. Row-level security on the two new
tables like the other organisation tables (see `db/rls.py`).

Revision ID: d2f6b0c4e8a1
Revises: c9e3a7b1d5f2
Create Date: 2026-10-29 20:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d2f6b0c4e8a1"
down_revision: str | None = "c9e3a7b1d5f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY = "agentcanvas_tenant"
TABLES: dict[str, tuple[str, str | None]] = {
    "scim_group": ("org_id", None),
    "scim_group_member": ("group_id", "scim_group"),
}


def _condition(table: str) -> str:
    column, parent = TABLES[table]
    own = (
        f"{column} = current_setting('matura.tenant_id', true)"
        if parent is None
        else f'EXISTS (SELECT 1 FROM {parent} p WHERE p.id = "{table}".{column})'
    )
    return f"current_setting('agentcanvas.all_tenants', true) = 'on' OR {own}"


def upgrade() -> None:
    with op.batch_alter_table("organization") as batch:
        batch.add_column(sa.Column("scim_token_hash", sa.Text(), nullable=True))
        batch.add_column(sa.Column("scim_token_prefix", sa.Text(), nullable=True))
        batch.add_column(sa.Column("scim_token_created_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_unique_constraint("organization_scim_token_hash_key", ["scim_token_hash"])
    with op.batch_alter_table("membership") as batch:
        batch.add_column(sa.Column("suspended", sa.Boolean(), server_default=sa.text("false"), nullable=False))
        batch.add_column(sa.Column("scim_external_id", sa.Text(), nullable=True))
    op.create_table(
        "scim_group",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organization.id", ondelete="CASCADE"), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.UniqueConstraint("org_id", "display_name"),
    )
    op.create_index("ix_scim_group_org_id", "scim_group", ["org_id"])
    op.create_table(
        "scim_group_member",
        sa.Column("group_id", sa.String(), sa.ForeignKey("scim_group.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("app_user.id", ondelete="CASCADE"), primary_key=True),
    )
    if op.get_bind().dialect.name == "postgresql":
        for table in TABLES:
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
            op.execute(f'CREATE POLICY {POLICY} ON "{table}" USING ({_condition(table)})')


def downgrade() -> None:
    op.drop_table("scim_group_member")
    op.drop_index("ix_scim_group_org_id", table_name="scim_group")
    op.drop_table("scim_group")
    with op.batch_alter_table("membership") as batch:
        batch.drop_column("scim_external_id")
        batch.drop_column("suspended")
    with op.batch_alter_table("organization") as batch:
        batch.drop_constraint("organization_scim_token_hash_key", type_="unique")
        batch.drop_column("scim_token_created_at")
        batch.drop_column("scim_token_prefix")
        batch.drop_column("scim_token_hash")
