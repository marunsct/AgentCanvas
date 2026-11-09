"""OIDC groups claim to roles (G-13)

An organisation may map the groups its identity provider puts in the sign-in token to roles
(`organization.sso_groups`); NULL keeps roles managed by hand.

Revision ID: c9e3a7b1d5f2
Revises: b7d1f5a9c3e8
Create Date: 2026-10-29 19:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c9e3a7b1d5f2"
down_revision: str | None = "b7d1f5a9c3e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("organization") as batch:
        batch.add_column(sa.Column("sso_groups", JSON, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("organization") as batch:
        batch.drop_column("sso_groups")
