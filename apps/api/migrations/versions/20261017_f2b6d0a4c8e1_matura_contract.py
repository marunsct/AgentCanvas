"""Matura contract tables (TS9a)

The `matura_*` tables every app integrated with Matura keeps (contract `matura.contract.v1`): tenants, users,
memberships, role grants and the app's roles, kept current by webhooks and the change feed. They are created on
every install, so switching `AGENTCANVAS_AUTH_MODE` to `matura` needs no migration of its own; they stay empty
otherwise.

Revision ID: f2b6d0a4c8e1
Revises: e7a1c5f9b3d6
Create Date: 2026-10-17 10:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from agentcanvas.vendor.matura import CONTRACT_VERSION, ddl

revision: str = "f2b6d0a4c8e1"
down_revision: str | None = "e7a1c5f9b3d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = [
    "matura_contract", "matura_inbox", "matura_sync_cursor", "matura_app_role", "matura_role_grant",
    "matura_membership", "matura_user", "matura_workspace", "matura_tenant",
]  # fmt: skip


def upgrade() -> None:
    bind = op.get_bind()
    for stmt in ddl(bind.dialect.name):
        op.execute(stmt)
    op.execute(f"INSERT INTO matura_contract (version, applied_at) VALUES ('{CONTRACT_VERSION}', CURRENT_TIMESTAMP)")


def downgrade() -> None:
    for table in TABLES:
        op.drop_table(table)
