"""a person's preferences (2026-10-29)

Theme and the editor's Simple or Full mode were kept in one browser only; they now follow the person.

Revision ID: b2d6f0a4c8e3
Revises: a8c4e2f6b0d3
Create Date: 2026-10-29 10:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b2d6f0a4c8e3"
down_revision: str | None = "a8c4e2f6b0d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("app_user") as batch:
        batch.add_column(sa.Column("preferences", JSON, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("app_user") as batch:
        batch.drop_column("preferences")
