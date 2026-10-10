"""one deployment per (workflow, environment); project secrets_version (S22 review)

Two first publishes of one environment at the same moment could each create a deployment. Existing duplicates are
merged into the oldest one (what referred to the others now refers to it) before the constraint is added.
`project.secrets_version` grows with every secret change, so each server instance can tell its decrypted copy of a
project's secrets is stale.

Revision ID: b3c7d1e5f9a2
Revises: a2b6c0d4e8f1
Create Date: 2026-10-10 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3c7d1e5f9a2"
down_revision: str | None = "a2b6c0d4e8f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REFERRERS = ("thread", "trigger", "run_score", "eval_alert")


def _merge_duplicates() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, workflow_id, environment FROM deployment ORDER BY created_at, id")
    ).all()  # fmt: skip
    keep: dict[tuple[str, str], str] = {}
    for dep_id, wf_id, environment in rows:
        kept = keep.setdefault((wf_id, environment), dep_id)
        if kept == dep_id:
            continue
        ids = {"dup": dep_id, "kept": kept}
        for table in REFERRERS:
            conn.execute(sa.text(f'UPDATE "{table}" SET deployment_id = :kept WHERE deployment_id = :dup'), ids)
        conn.execute(
            sa.text(
                "UPDATE online_evaluator SET deployment_id = :kept WHERE deployment_id = :dup AND evaluator_id NOT IN "
                "(SELECT evaluator_id FROM online_evaluator WHERE deployment_id = :kept)"
            ),
            ids,
        )
        conn.execute(sa.text("DELETE FROM online_evaluator WHERE deployment_id = :dup"), ids)
        conn.execute(sa.text("DELETE FROM deployment WHERE id = :dup"), ids)


def upgrade() -> None:
    _merge_duplicates()
    with op.batch_alter_table("deployment") as batch:
        batch.create_unique_constraint("deployment_once", ["workflow_id", "environment"])
    with op.batch_alter_table("project") as batch:
        batch.add_column(sa.Column("secrets_version", sa.Integer(), server_default=sa.text("0"), nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("project") as batch:
        batch.drop_column("secrets_version")
    with op.batch_alter_table("deployment") as batch:
        batch.drop_constraint("deployment_once", type_="unique")
