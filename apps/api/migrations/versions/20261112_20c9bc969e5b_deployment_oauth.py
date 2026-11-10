"""OAuth sign-in for a deployment's MCP and A2A endpoints (2026-11-12)

A builder may let people sign in to a deployment's MCP server and A2A agent with an access token from the
organisation's identity provider, beside its keys. The settings (issuer, audience, scopes) live on the deployment;
NULL keeps today's behaviour (keys only). The deployment table is small: a plain nullable column.

Revision ID: 20c9bc969e5b
Revises: d32911f9085f
Create Date: 2026-11-12 09:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "20c9bc969e5b"
down_revision: str | None = "d32911f9085f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("deployment") as batch:
        batch.add_column(sa.Column("oauth", JSON, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("deployment") as batch:
        batch.drop_column("oauth")
