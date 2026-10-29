"""per-user OAuth connections to app connectors (G-7)

A person's (or the organisation's service) sign-in to an MCP server, the sign-ins in progress, and AgentCanvas's
registration at each authorization server. Tokens are stored encrypted. Row-level security on the three new tables like
the other organisation tables (see `db/rls.py`).

Revision ID: e5a9c1b7d3f2
Revises: d2f6b0c4e8a1
Create Date: 2026-10-29 22:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5a9c1b7d3f2"
down_revision: str | None = "d2f6b0c4e8a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY = "agentcanvas_tenant"
TABLES: dict[str, tuple[str, str | None]] = {
    "oauth_connection": ("org_id", None),
    "oauth_flow": ("org_id", None),
    "oauth_client": ("org_id", None),
}


def _condition(table: str) -> str:
    column, parent = TABLES[table]
    own = (
        f"{column} = current_setting('matura.tenant_id', true)"
        if parent is None
        else f'EXISTS (SELECT 1 FROM {parent} p WHERE p.id = "{table}".{column})'
    )
    return f"current_setting('agentcanvas.all_tenants', true) = 'on' OR {own}"


def _org() -> sa.Column:
    return sa.Column("org_id", sa.String(), sa.ForeignKey("organization.id", ondelete="CASCADE"), nullable=False)


def _stamp(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "oauth_connection",
        sa.Column("id", sa.String(), primary_key=True),
        _org(),
        sa.Column("owner", sa.Text(), nullable=False),
        sa.Column("resource", sa.Text(), nullable=False),
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("token_endpoint", sa.Text(), nullable=False),
        sa.Column("revocation_endpoint", sa.Text(), nullable=True),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("client_secret_enc", sa.LargeBinary(), nullable=True),
        sa.Column("access_token_enc", sa.LargeBinary(), nullable=False),
        sa.Column("refresh_token_enc", sa.LargeBinary(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scope", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        _stamp("created_at"),
        _stamp("updated_at"),
        sa.UniqueConstraint("org_id", "owner", "resource"),
        sa.CheckConstraint("status IN ('active','reconnect')", name="oauth_connection_status"),
    )
    op.create_index("ix_oauth_connection_org_id", "oauth_connection", ["org_id"])
    op.create_table(
        "oauth_flow",
        sa.Column("state_hash", sa.String(), primary_key=True),
        _org(),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("owner", sa.Text(), nullable=False),
        sa.Column("resource", sa.Text(), nullable=False),
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("token_endpoint", sa.Text(), nullable=False),
        sa.Column("revocation_endpoint", sa.Text(), nullable=True),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("client_secret_enc", sa.LargeBinary(), nullable=True),
        sa.Column("iss_in_response", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("verifier_enc", sa.LargeBinary(), nullable=False),
        sa.Column("return_to", sa.Text(), server_default=sa.text("'/'"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_oauth_flow_org_id", "oauth_flow", ["org_id"])
    op.create_table(
        "oauth_client",
        sa.Column("org_id", sa.String(), sa.ForeignKey("organization.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("issuer", sa.Text(), primary_key=True),
        sa.Column("redirect_uri", sa.Text(), primary_key=True),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("client_secret_enc", sa.LargeBinary(), nullable=True),
        _stamp("created_at"),
    )
    if op.get_bind().dialect.name == "postgresql":
        for table in TABLES:
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
            op.execute(f'CREATE POLICY {POLICY} ON "{table}" USING ({_condition(table)})')


def downgrade() -> None:
    op.drop_table("oauth_client")
    op.drop_index("ix_oauth_flow_org_id", table_name="oauth_flow")
    op.drop_table("oauth_flow")
    op.drop_index("ix_oauth_connection_org_id", table_name="oauth_connection")
    op.drop_table("oauth_connection")
