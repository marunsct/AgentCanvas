"""who accepted the Copilot's changes in a version (2026-10-29)

A version records the Copilot proposals accepted since the one before, and by whom (FR-COP-07): the conversation that
held them may be archived or expire, the version stays.

Revision ID: c6e0a4b8d2f7
Revises: b2d6f0a4c8e3
Create Date: 2026-10-29 14:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c6e0a4b8d2f7"
down_revision: str | None = "b2d6f0a4c8e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("workflow_version") as batch:
        batch.add_column(sa.Column("copilot_changes", JSON, nullable=False, server_default=sa.text("'[]'")))


def downgrade() -> None:
    with op.batch_alter_table("workflow_version") as batch:
        batch.drop_column("copilot_changes")
