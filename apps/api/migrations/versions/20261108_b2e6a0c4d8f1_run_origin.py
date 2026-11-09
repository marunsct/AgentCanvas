"""where a run came from (I-15)

`run.origin` keeps the runner's origin ("user", "test" for experiment and test-case runs, …), so the run search's
re-index can leave test runs out as the incremental index does. Runs from before stay NULL and count as people's.

Revision ID: b2e6a0c4d8f1
Revises: a9d3f7b1c5e2
Create Date: 2026-11-08 10:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2e6a0c4d8f1"
down_revision: str | None = "a9d3f7b1c5e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("run", sa.Column("origin", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("run", "origin")
