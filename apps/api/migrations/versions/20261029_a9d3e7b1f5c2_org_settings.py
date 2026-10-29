"""an organisation's own values for server settings (2026-10-29)

The server settings an organisation's admin may override: local MCP servers, the rate limits and two retentions
(`services/org_settings.py`). Null: none, the server's values apply. Also joins the two heads the branch had
(the project's Git link and the answer cache).

Revision ID: a9d3e7b1f5c2
Revises: e1a5c9d3f7b2, f6b0d4a8c2e5
Create Date: 2026-10-29 21:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a9d3e7b1f5c2"
down_revision: str | Sequence[str] | None = ("e1a5c9d3f7b2", "f6b0d4a8c2e5")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("organization") as batch:
        batch.add_column(sa.Column("settings", JSON, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("organization") as batch:
        batch.drop_column("settings")
