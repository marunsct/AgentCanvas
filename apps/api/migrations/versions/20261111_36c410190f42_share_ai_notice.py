"""the AI notice of a share link's chat page (2026-11-11)

The chat page and the chat widget always tell visitors they are chatting with an AI (EU AI Act Art. 50(1)).
`ai_notice` is a link's own wording of that notice; empty uses the default wording.

Revision ID: 36c410190f42
Revises: 1ec6eb9f62ed
Create Date: 2026-11-11 09:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "36c410190f42"
down_revision: str | None = "1ec6eb9f62ed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("deployment_share") as batch:
        batch.add_column(sa.Column("ai_notice", sa.Text(), server_default=sa.text("''"), nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("deployment_share") as batch:
        batch.drop_column("ai_notice")
