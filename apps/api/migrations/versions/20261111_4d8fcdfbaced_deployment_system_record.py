"""a deployment's AI-system record: purpose, owner, risk class, data categories (2026-11-11)

What the person responsible writes down about a published workflow for an AI inventory (ISO/IEC 42001, NIST AI
600-1, the AI Act's deployer duties): its purpose, its owner, its risk class and why, the kinds of data it handles,
whether its end users are the public, and when it is next reviewed. Everything else the record shows is read from
what the server already keeps. The deployment table is small: a plain nullable column.

Revision ID: 4d8fcdfbaced
Revises: d32911f9085f
Create Date: 2026-11-11 09:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "4d8fcdfbaced"
down_revision: str | None = "d32911f9085f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("deployment") as batch:
        batch.add_column(sa.Column("system_record", JSON, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("deployment") as batch:
        batch.drop_column("system_record")
