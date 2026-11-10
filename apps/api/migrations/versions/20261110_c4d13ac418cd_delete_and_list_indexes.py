"""indexes for deletes, the Conversations list and the spending report (2026-11-10)

Postgres does not index foreign keys: every row a cascade or SET NULL touched (retention deleting conversations and
runs, deleting a workflow, a version or a deployment) scanned the child table. The Conversations list sorted every
matching conversation on each page, and the spending report scanned every run.

On Postgres the indexes are built CONCURRENTLY, outside the migration's transaction, so a large `run` or `thread`
table keeps taking writes while they build (`env.py` holds its migration lock across that). An index a failed build
left invalid is dropped and built again.

Revision ID: c4d13ac418cd
Revises: 51daef33f300
Create Date: 2026-11-10 13:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4d13ac418cd"
down_revision: str | None = "51daef33f300"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEXES: list[tuple[str, str, list[str]]] = [
    ("thread_created_idx", "thread", ["created_at", "id"]),
    ("thread_workflow_created_idx", "thread", ["workflow_id", "created_at", "id"]),
    ("thread_deployment_idx", "thread", ["deployment_id"]),
    ("run_started_idx", "run", ["started_at"]),
    ("run_version_idx", "run", ["workflow_version_id"]),
    ("interrupt_thread_idx", "interrupt_task", ["thread_id"]),
    ("interrupt_run_idx", "interrupt_task", ["run_id"]),
    ("annotation_item_run_idx", "annotation_item", ["run_id"]),
    ("deployment_version_idx", "deployment", ["version_id"]),
    ("trigger_workflow_idx", "trigger", ["workflow_id"]),
    ("stored_file_deployment_idx", "stored_file", ["deployment_id"]),
]
VALID = sa.text("SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :n")


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        for name, table, columns in INDEXES:
            op.create_index(name, table, columns)
        return
    with op.get_context().autocommit_block():
        # a concurrent build waits for the transactions already running: no lock timeout cuts that wait short
        before = conn.exec_driver_sql("SHOW lock_timeout").scalar()
        conn.exec_driver_sql("SET lock_timeout = 0")
        for name, table, columns in INDEXES:
            valid = conn.execute(VALID, {"n": name}).scalar()
            if valid:
                continue
            if valid is not None:  # left invalid by a build that failed
                op.drop_index(name, table_name=table, postgresql_concurrently=True)
            op.create_index(name, table, columns, postgresql_concurrently=True)
        conn.exec_driver_sql(f"SET lock_timeout = '{before}'")


def downgrade() -> None:
    for name, table, _ in reversed(INDEXES):
        op.drop_index(name, table_name=table)
