"""sites allowed to frame a share link's chat page (2026-10-18 review)

The chat page was framable by any site. `deployment_share.frame_origins` lists the origins a builder allows; an empty
list keeps the old behaviour (any site) so embedded widgets keep working until their owner narrows it.

Revision ID: c9e1a7d3f5b2
Revises: b8d2f6a0c4e7
Create Date: 2026-10-19 16:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "c9e1a7d3f5b2"
down_revision: str | None = "b8d2f6a0c4e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("deployment_share", sa.Column("frame_origins", JSONB, server_default=sa.text("'[]'"), nullable=False))


def downgrade() -> None:
    op.drop_column("deployment_share", "frame_origins")
