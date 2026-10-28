"""Copilot threads and a turn lease (2026-10-28)

A person kept one Copilot conversation per workflow, and "New chat" deleted it. Conversations now have a title and
an `archived_at`: "New chat" archives the open one, the panel lists them, and one can be reopened. One open
conversation per (workflow, person) is a partial unique index instead of a unique constraint.

`busy_until` is a lease on the conversation while a turn answers: the API took turns one at a time with a set in
its own memory, which two replicas did not share.

Revision ID: f7b3d9e1a5c2
Revises: e5a9c3f7b1d4
Create Date: 2026-10-28 23:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f7b3d9e1a5c2"
down_revision: str | None = "e5a9c3f7b1d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "copilot_conversation"
OPEN = sa.text("archived_at IS NULL")
# SQLite's unique constraint has no name: batch mode names it by this convention to drop it
NAMES = {"uq": "uq_%(table_name)s_%(column_0_N_name)s"}


def _unique_name() -> str:
    return f"{TABLE}_workflow_id_user_id_key" if op.get_bind().dialect.name == "postgresql" else (
        f"uq_{TABLE}_workflow_id_user_id"
    )


def upgrade() -> None:
    with op.batch_alter_table(TABLE, naming_convention=NAMES) as batch:
        batch.add_column(sa.Column("title", sa.Text(), nullable=True))
        batch.add_column(sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("busy_until", sa.DateTime(timezone=True), nullable=True))
        batch.drop_constraint(_unique_name(), type_="unique")
    op.create_index(
        "copilot_conversation_open_once", TABLE, ["workflow_id", "user_id"], unique=True,
        postgresql_where=OPEN, sqlite_where=OPEN,
    )  # fmt: skip
    op.create_index("copilot_conversation_by_person", TABLE, ["workflow_id", "user_id", "updated_at"])


def downgrade() -> None:
    op.drop_index("copilot_conversation_by_person", TABLE)
    op.drop_index("copilot_conversation_open_once", TABLE)
    op.execute(sa.text(f"DELETE FROM {TABLE} WHERE archived_at IS NOT NULL"))
    with op.batch_alter_table(TABLE, naming_convention=NAMES) as batch:
        batch.create_unique_constraint(_unique_name(), ["workflow_id", "user_id"])
        batch.drop_column("busy_until")
        batch.drop_column("archived_at")
        batch.drop_column("title")
