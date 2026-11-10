"""Approve and Deny links in approval notifications (2026-11-13)

`org_notifier.actions` is an organisation's opt-in: its approval e-mails then carry signed Approve and Deny links for
each person, and its Slack and webhook messages links that open the item in the Inbox. Off for every existing
organisation (a constant default: Postgres adds it without rewriting the table).

Revision ID: 89af7965f705
Revises: 98d069f94d68
Create Date: 2026-11-13 09:30:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "89af7965f705"
down_revision: str | None = "98d069f94d68"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("org_notifier") as batch:
        batch.add_column(sa.Column("actions", sa.Boolean(), server_default=sa.text("false"), nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("org_notifier") as batch:
        batch.drop_column("actions")
