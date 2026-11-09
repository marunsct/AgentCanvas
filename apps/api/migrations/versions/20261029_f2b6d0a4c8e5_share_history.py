"""a share link visitor's conversation is kept for a reload (2026-10-29, G-15)

The transcript is what the visitor saw (their messages and the answers), kept on the conversation's share_thread row;
`closed` marks one the visitor started over from with "New conversation".

Revision ID: f2b6d0a4c8e5
Revises: a7c1e5b9d3f2
Create Date: 2026-10-29 20:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f2b6d0a4c8e5"
down_revision: str | None = "a7c1e5b9d3f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("share_thread") as batch:
        batch.add_column(sa.Column("transcript", JSON, nullable=False, server_default=sa.text("'[]'")))
        batch.add_column(sa.Column("closed", sa.Boolean(), nullable=False, server_default=sa.text("false")))


def downgrade() -> None:
    with op.batch_alter_table("share_thread") as batch:
        batch.drop_column("closed")
        batch.drop_column("transcript")
