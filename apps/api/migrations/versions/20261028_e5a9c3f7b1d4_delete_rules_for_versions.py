"""delete rules on the references to versions and deployments (2026-10-28)

Deleting a workflow that had been published and called failed on Postgres: its versions and threads go by cascade, but
the runs' `workflow_version_id`, the deployments' `version_id` and the threads' and triggers' `deployment_id` had no
delete rule, so the database refused the delete (found by a test that deletes a called workflow). They now say what
happens: a deployment goes with its version; a run, thread or trigger forgets a version or deployment that is gone.

SQLite (whose foreign keys the app now enforces) gets the same rules by rebuilding the four tables.

Revision ID: e5a9c3f7b1d4
Revises: c9e1a7d3f5b2
Create Date: 2026-10-28 21:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e5a9c3f7b1d4"
down_revision: str | None = "c9e1a7d3f5b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, column, referred table, ON DELETE)
RULES = [
    ("run", "workflow_version_id", "workflow_version", "SET NULL"),
    ("deployment", "version_id", "workflow_version", "CASCADE"),
    ("thread", "deployment_id", "deployment", "SET NULL"),
    ("trigger", "deployment_id", "deployment", "SET NULL"),
]
# SQLite's foreign keys have no names: batch mode names them by this convention to drop and recreate them
SQLITE_NAMES = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}


def _apply(on_delete: dict[tuple[str, str], str | None]) -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table, column, referred, _ in RULES:
            name = f"{table}_{column}_fkey"
            op.drop_constraint(name, table, type_="foreignkey")
            op.create_foreign_key(name, table, referred, [column], ["id"], ondelete=on_delete[(table, column)])
        return
    for table, column, referred, _ in RULES:
        name = f"fk_{table}_{column}_{referred}"
        with op.batch_alter_table(table, naming_convention=SQLITE_NAMES, recreate="always") as batch:
            batch.drop_constraint(name, type_="foreignkey")
            batch.create_foreign_key(name, referred, [column], ["id"], ondelete=on_delete[(table, column)])


def upgrade() -> None:
    _apply({(t, c): rule for t, c, _, rule in RULES})


def downgrade() -> None:
    _apply({(t, c): None for t, c, _, _ in RULES})
