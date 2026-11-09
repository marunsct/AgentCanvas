"""a run an admin asked to stop (2026-11-09)

The kill switch (F-5) marks the organisation's running runs; the instance that runs each one stops it at its next
heartbeat, and the hand-over sweep cancels a marked run instead of continuing it elsewhere.

Revision ID: f4a8c2e6b0d3
Revises: c2e6a0b4d8f1
Create Date: 2026-11-09 12:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f4a8c2e6b0d3"
down_revision: str | None = "c2e6a0b4d8f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("run") as batch:
        batch.add_column(sa.Column("stop_requested", sa.Boolean(), server_default=sa.text("false"), nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("run") as batch:
        batch.drop_column("stop_requested")
