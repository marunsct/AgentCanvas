"""what a deployment's app connectors changed since publishing (2026-11-10)

Publishing pins each app connector's tools. When a deployment's run finds that the server added or changed tools
since, the deployment keeps what was left out, per connector, so the editor can show it; publishing again clears it.
The deployment table is small: a plain nullable column.

Revision ID: a01bee975c80
Revises: 1ec6eb9f62ed
Create Date: 2026-11-10 18:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "a01bee975c80"
down_revision: str | None = "1ec6eb9f62ed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("deployment") as batch:
        batch.add_column(sa.Column("tool_drift", JSON, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("deployment") as batch:
        batch.drop_column("tool_drift")
