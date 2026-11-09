"""why a run was asked to stop (2026-11-10)

Stop, Delete conversation and the kill switch mark a running run in the database (`stop_requested`): the instance
running it stops it at its next heartbeat, wherever the request landed. `stop_reason` keeps why, so the run ends
with the person's reason ("cancelled by user") rather than the kill switch's.

Revision ID: 726e0996692d
Revises: b8e2f6a0c4d7
Create Date: 2026-11-10 12:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "726e0996692d"
down_revision: str | None = "b8e2f6a0c4d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("run") as batch:
        batch.add_column(sa.Column("stop_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("run") as batch:
        batch.drop_column("stop_reason")
