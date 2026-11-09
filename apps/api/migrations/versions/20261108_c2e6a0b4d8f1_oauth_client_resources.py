"""the MCP servers an admin's client credentials are for (2026-11-08, CP-3)

`oauth_client.resources`: the canonical addresses of the MCP servers entered client credentials sign in to; null for a
dynamic registration AgentCanvas made itself. Rows written before this column could be either, so each is kept to the
servers its organisation already connected to through that sign-in server: nothing that works stops, and no new server
borrows the credentials until an admin enters them with its address (or removes them, so AgentCanvas registers again).

Revision ID: c2e6a0b4d8f1
Revises: b2e6a0c4d8f1
Create Date: 2026-11-08 09:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "c2e6a0b4d8f1"
down_revision: str | None = "b2e6a0c4d8f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("oauth_client", sa.Column("resources", JSON, nullable=True))
    bind = op.get_bind()
    clients = sa.table(
        "oauth_client",
        sa.column("org_id"),
        sa.column("issuer"),
        sa.column("redirect_uri"),
        sa.column("resources", JSON),
    )
    used = bind.execute(sa.text("SELECT DISTINCT org_id, issuer, resource FROM oauth_connection")).all()
    by_server: dict[tuple[str, str], set[str]] = {}
    for org_id, issuer, resource in used:
        by_server.setdefault((org_id, (issuer or "").rstrip("/")), set()).add(resource)
    for org_id, issuer, redirect_uri in bind.execute(
        sa.select(clients.c.org_id, clients.c.issuer, clients.c.redirect_uri)
    ).all():
        resources = sorted(by_server.get((org_id, issuer.rstrip("/")), set()))
        bind.execute(
            clients.update()
            .where(clients.c.org_id == org_id, clients.c.issuer == issuer, clients.c.redirect_uri == redirect_uri)
            .values(resources=resources)
        )


def downgrade() -> None:
    op.drop_column("oauth_client", "resources")
