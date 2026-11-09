"""a deployment run's result, kept with the run (2026-11-11)

`output` is the run's answer and `interrupts` the pauses it ended with, written when the run ends. A caller of the
deployment API, A2A or MCP reads them later, from any server, once the run's events have left the memory of the
server that ran it. Both are new nullable columns: adding them rewrites nothing on Postgres.

Revision ID: cdeb54ba6bf8
Revises: 1ec6eb9f62ed
Create Date: 2026-11-11 09:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "cdeb54ba6bf8"
down_revision: str | None = "1ec6eb9f62ed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("run") as batch:
        batch.add_column(sa.Column("output", JSON, nullable=True))
        batch.add_column(sa.Column("interrupts", JSON, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("run") as batch:
        batch.drop_column("interrupts")
        batch.drop_column("output")
