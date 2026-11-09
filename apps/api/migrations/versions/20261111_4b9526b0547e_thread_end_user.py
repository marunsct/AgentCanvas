"""a deployment conversation's end user (E-7)

The deployment API's caller names its end user with `user_id`; the conversation keeps it (`api:<deployment>:<user
id>`, the id its runs and memories use) so an erasure or export request for that person finds every conversation,
also those of workflows without a context field for the user. The index is built concurrently on Postgres, after
the column is committed, so the thread table stays readable and writable while it builds.

Revision ID: 4b9526b0547e
Revises: 86a3d3a11976
Create Date: 2026-11-11 10:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4b9526b0547e"
down_revision: str | None = "86a3d3a11976"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HAS_END_USER = sa.text("end_user IS NOT NULL")


def upgrade() -> None:
    with op.batch_alter_table("thread") as batch:
        batch.add_column(sa.Column("end_user", sa.Text(), nullable=True))
    if op.get_bind().dialect.name != "postgresql":
        op.create_index("thread_end_user_idx", "thread", ["end_user"], sqlite_where=HAS_END_USER)
        return
    with op.get_context().autocommit_block():  # CREATE INDEX CONCURRENTLY cannot run in a transaction
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS thread_end_user_idx")  # an invalid one from a cut-short build
        op.create_index("thread_end_user_idx", "thread", ["end_user"], postgresql_where=HAS_END_USER,
                        postgresql_concurrently=True)  # fmt: skip


def downgrade() -> None:
    op.drop_index("thread_end_user_idx", table_name="thread")
    with op.batch_alter_table("thread") as batch:
        batch.drop_column("end_user")
