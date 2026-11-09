"""several API keys per deployment, with names, expiry and last use (E-8)

A deployment had one key (`deployment.api_key_hash`), and rotating it cut every caller off at once. `deployment_key`
holds any number of keys per deployment, each with a name, an optional expiry, when it was last used and when it was
revoked, so a key is replaced without downtime. The existing key is copied in as "default" (revoked already for an
unpublished deployment, whose key was meant to be dead). The old columns stay, unused by this release, so a server
still on the previous one keeps accepting the key while an upgrade rolls through; a later migration drops them.
On Postgres the table gets the tenant policy like every organisation-owned table (see `agentcanvas/db/rls.py`; the
condition is copied here so later changes to that module do not rewrite this migration).

Revision ID: 2201e65b5be8
Revises: 1ec6eb9f62ed
Create Date: 2026-11-10 20:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2201e65b5be8"
down_revision: str | None = "1ec6eb9f62ed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY = "agentcanvas_tenant"
TABLE = "deployment_key"
TABLES: dict[str, tuple[str, str | None]] = {TABLE: ("deployment_id", "deployment")}  # copied, as in a3c7e9b1d5f2


def _condition(table: str) -> str:
    column, parent = TABLES[table]
    own = f'EXISTS (SELECT 1 FROM {parent} p WHERE p.id = "{table}".{column})'
    return f"current_setting('agentcanvas.all_tenants', true) = 'on' OR {own}"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("deployment_id", sa.String(), sa.ForeignKey("deployment.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("key_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("prefix", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
    )
    op.create_index("deployment_key_deployment_idx", TABLE, ["deployment_id"])
    # the key's id reuses the deployment's ULID ("dep_X" -> "dky_X"): unique, and no Python per row
    op.execute(
        f"INSERT INTO {TABLE} (id, deployment_id, name, key_hash, prefix, created_at, revoked_at)"
        " SELECT 'dky_' || substr(id, 5), id, 'default', api_key_hash, COALESCE(key_prefix, ''), created_at,"
        " CASE WHEN active THEN NULL ELSE CURRENT_TIMESTAMP END FROM deployment WHERE api_key_hash IS NOT NULL"
    )
    with op.batch_alter_table("deployment") as batch:
        batch.alter_column("api_key_hash", existing_type=sa.Text(), nullable=True)
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f'ALTER TABLE "{TABLE}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{TABLE}" FORCE ROW LEVEL SECURITY')
        op.execute(f'DROP POLICY IF EXISTS {POLICY} ON "{TABLE}"')
        op.execute(f'CREATE POLICY {POLICY} ON "{TABLE}" USING ({_condition(TABLE)})')


def downgrade() -> None:
    # the previous release knows one key per deployment: the newest one still working, else none that works
    op.execute(
        "UPDATE deployment SET api_key_hash = k.key_hash, key_prefix = k.prefix FROM ("
        " SELECT DISTINCT ON (deployment_id) deployment_id, key_hash, prefix FROM deployment_key"
        " WHERE revoked_at IS NULL ORDER BY deployment_id, created_at DESC) k WHERE k.deployment_id = deployment.id"
        if op.get_bind().dialect.name == "postgresql"
        else "UPDATE deployment SET"
        " api_key_hash = (SELECT key_hash FROM deployment_key k WHERE k.deployment_id = deployment.id"
        "  AND k.revoked_at IS NULL ORDER BY k.created_at DESC LIMIT 1),"
        " key_prefix = (SELECT prefix FROM deployment_key k WHERE k.deployment_id = deployment.id"
        "  AND k.revoked_at IS NULL ORDER BY k.created_at DESC LIMIT 1)"
    )
    op.execute("UPDATE deployment SET api_key_hash = 'revoked' WHERE api_key_hash IS NULL")  # matches no key
    with op.batch_alter_table("deployment") as batch:
        batch.alter_column("api_key_hash", existing_type=sa.Text(), nullable=False)
    op.drop_table(TABLE)
