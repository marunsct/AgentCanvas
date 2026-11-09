"""knowledge base web re-crawl (G-6)

`knowledge_base.recrawl` holds the re-crawl settings and the last result (JSON), `crawled_at` the time a crawl last
started, which the crawl job moves forward in one conditional UPDATE so that replicas crawl a base once per interval.

Revision ID: f3c7d1e5a9b4
Revises: e1b5c9d3f7a2
Create Date: 2026-10-29 19:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f3c7d1e5a9b4"
down_revision: str | None = "e1b5c9d3f7a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("knowledge_base") as batch:
        batch.add_column(sa.Column("recrawl", JSON, nullable=True))
        batch.add_column(sa.Column("crawled_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("knowledge_base") as batch:
        batch.drop_column("crawled_at")
        batch.drop_column("recrawl")
