"""a dataset version's example count and splits, kept beside its examples (2026-11-10)

Every edit writes a version holding the whole example set (up to 10,000 examples). The dataset list and the version
history loaded every version's examples only to count them. The count and the examples per split are now written
with the version; this fills them in for the versions written before.

Revision ID: 2f5f9b223d7f
Revises: c4d13ac418cd
Create Date: 2026-11-10 14:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "2f5f9b223d7f"
down_revision: str | None = "c4d13ac418cd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POSTGRES = """
UPDATE dataset_version SET
  example_count = jsonb_array_length(examples),
  splits = (SELECT coalesce(jsonb_object_agg(k, n), '{}'::jsonb)
            FROM (SELECT coalesce(e ->> 'split', '') AS k, count(*) AS n
                  FROM jsonb_array_elements(examples) AS e GROUP BY 1) AS s)
WHERE jsonb_typeof(examples) = 'array'
"""
SQLITE = """
UPDATE dataset_version SET
  example_count = json_array_length(examples),
  splits = (SELECT coalesce(json_group_object(k, n), '{}')
            FROM (SELECT coalesce(json_extract(e.value, '$.split'), '') AS k, count(*) AS n
                  FROM json_each(dataset_version.examples) AS e GROUP BY 1))
WHERE json_type(examples) = 'array'
"""


def upgrade() -> None:
    with op.batch_alter_table("dataset_version") as batch:
        batch.add_column(sa.Column("example_count", sa.Integer(), server_default=sa.text("0"), nullable=False))
        batch.add_column(
            sa.Column(
                "splits",
                sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
                server_default=sa.text("'{}'"),
                nullable=False,
            )
        )
    op.execute(POSTGRES if op.get_bind().dialect.name == "postgresql" else SQLITE)


def downgrade() -> None:
    with op.batch_alter_table("dataset_version") as batch:
        batch.drop_column("splits")
        batch.drop_column("example_count")
