"""deleting an organisation, with a grace period (E-7)

An admin's request suspends the organisation at once (`suspended_at`: every sign-in, key, link and run is refused)
and sets when it is deleted (`delete_after`). Until then an admin can take the request back. After it, a background
job on any replica deletes everything the organisation holds; `purge_lease_until` keeps two replicas from doing
it at once. The row stays as a tombstone (`deleted_at`) with the audit entries of the deletion.

Revision ID: 86a3d3a11976
Revises: a01bee975c80
Create Date: 2026-11-11 09:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "86a3d3a11976"
down_revision: str | None = "a01bee975c80"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("organization") as batch:
        batch.add_column(sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("delete_after", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("deletion_requested_by", sa.Text(), nullable=True))
        batch.add_column(sa.Column("purge_lease_until", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("organization") as batch:
        batch.drop_column("deleted_at")
        batch.drop_column("purge_lease_until")
        batch.drop_column("deletion_requested_by")
        batch.drop_column("delete_after")
        batch.drop_column("suspended_at")
