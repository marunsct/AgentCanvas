"""an index for an organisation's audit entries about one thing (2026-11-11)

A deployment's AI-system record lists who published, unpublished and described it, read from the audit log by
target. Without an index that read walked the organisation's whole log.

On Postgres the index is built CONCURRENTLY, outside the migration's transaction, so a large `audit_log` keeps
taking writes while it builds (`env.py` holds its migration lock across that). An index a failed build left invalid
is dropped and built again.

Revision ID: b700c6e125be
Revises: 4d8fcdfbaced
Create Date: 2026-11-11 09:10:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b700c6e125be"
down_revision: str | None = "4d8fcdfbaced"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAME, TABLE, COLUMNS = "audit_org_target_idx", "audit_log", ["org_id", "target", "id"]
VALID = sa.text("SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :n")


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        op.create_index(NAME, TABLE, COLUMNS)
        return
    with op.get_context().autocommit_block():
        # a concurrent build waits for the transactions already running: no lock timeout cuts that wait short
        before = conn.exec_driver_sql("SHOW lock_timeout").scalar()
        conn.exec_driver_sql("SET lock_timeout = 0")
        valid = conn.execute(VALID, {"n": NAME}).scalar()
        if not valid:
            if valid is not None:  # left invalid by a build that failed
                op.drop_index(NAME, table_name=TABLE, postgresql_concurrently=True)
            op.create_index(NAME, TABLE, COLUMNS, postgresql_concurrently=True)
        conn.exec_driver_sql(f"SET lock_timeout = '{before}'")


def downgrade() -> None:
    op.drop_index(NAME, table_name=TABLE)
