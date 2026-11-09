"""the run queue through a hand-over (2026-11-10)

`pending` keeps a waiting run's start arguments: no checkpoint holds its input before it takes a slot, so a server
that stops hands it over with them.

Revision ID: 1ec6eb9f62ed
Revises: b8e2f6a0c4d7
Create Date: 2026-11-10 18:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "1ec6eb9f62ed"
down_revision: str | None = "b8e2f6a0c4d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("run") as batch:
        batch.add_column(sa.Column("pending", JSON, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("run") as batch:
        batch.drop_column("pending")
