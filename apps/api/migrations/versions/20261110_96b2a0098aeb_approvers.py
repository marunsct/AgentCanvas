"""who may decide an Inbox item (2026-11-10)

`approvers` keeps a step's "Who may decide" setting (roles, SCIM groups, people, whether the caller of a published
workflow may answer, not the person who started the run) on the Inbox item when the run pauses, so a later edit of
the workflow does not change who decides a pause that already waits. NULL: the step names nobody, as before. Adding a
nullable column without a default changes no existing row and does not rewrite the table.

Revision ID: 96b2a0098aeb
Revises: 1ec6eb9f62ed
Create Date: 2026-11-10 20:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "96b2a0098aeb"
down_revision: str | None = "1ec6eb9f62ed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("interrupt_task") as batch:
        batch.add_column(sa.Column("approvers", JSON, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("interrupt_task") as batch:
        batch.drop_column("approvers")
