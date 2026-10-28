"""an organisation's environments (2026-10-29)

The environments people publish to (dev, staging, prod...). Null: none chosen, any name may be published to and the
web app offers dev, staging and prod; a list limits publishing to its names.

Revision ID: a8c4e2f6b0d3
Revises: f7b3d9e1a5c2
Create Date: 2026-10-29 09:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a8c4e2f6b0d3"
down_revision: str | None = "f7b3d9e1a5c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("organization") as batch:
        batch.add_column(sa.Column("environments", JSON, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("organization") as batch:
        batch.drop_column("environments")
