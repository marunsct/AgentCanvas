"""a Copilot conversation's own model (2026-10-29)

The person can switch the model for one conversation, within the project's policies; null follows the project.

Revision ID: d8f2b6c0e4a9
Revises: c6e0a4b8d2f7
Create Date: 2026-10-29 16:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d8f2b6c0e4a9"
down_revision: str | None = "c6e0a4b8d2f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("copilot_conversation") as batch:
        batch.add_column(sa.Column("model", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("copilot_conversation") as batch:
        batch.drop_column("model")
