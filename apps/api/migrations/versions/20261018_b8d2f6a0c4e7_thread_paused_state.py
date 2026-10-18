"""paused state of runs on frameworks without their own checkpoints (2026-10-18 review, H7)

CrewAI, Google ADK, the flow-engine frameworks, Mastra and the OpenAI Agents SDK keep a paused run as JSON. It lived
in one API process's memory, so a resume that reached another replica, or came after a restart, found nothing.
`thread.paused_state` keeps it with the conversation.

Revision ID: b8d2f6a0c4e7
Revises: a3c7e9b1d5f2
Create Date: 2026-10-18 21:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8d2f6a0c4e7"
down_revision: str | None = "a3c7e9b1d5f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("thread", sa.Column("paused_state", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("thread", "paused_state")
