"""experiments that run each example several times (2026-11-09)

F-6: `experiment.repetitions` (1 for existing experiments) and `experiment_result.repetition` (0), so pass@k, pass^k
and flaky examples can be reported; a result is unique per run of an example (the repetition joins the key).

Revision ID: a7d1e5b9c3f6
Revises: f4a8c2e6b0d3
Create Date: 2026-11-09 15:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7d1e5b9c3f6"
down_revision: str | None = "f4a8c2e6b0d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("experiment") as batch:
        batch.add_column(sa.Column("repetitions", sa.Integer(), server_default=sa.text("1"), nullable=False))
    with op.batch_alter_table("experiment_result") as batch:
        batch.add_column(sa.Column("repetition", sa.Integer(), server_default=sa.text("0"), nullable=False))
        batch.drop_constraint("experiment_result_example", type_="unique")  # one row per run, not per example
        batch.create_unique_constraint(
            "experiment_result_example", ["experiment_id", "target", "example_id", "repetition"]
        )


def downgrade() -> None:
    op.execute("DELETE FROM experiment_result WHERE repetition > 0")  # the old key holds one run per example
    with op.batch_alter_table("experiment_result") as batch:
        batch.drop_constraint("experiment_result_example", type_="unique")
        batch.create_unique_constraint("experiment_result_example", ["experiment_id", "target", "example_id"])
        batch.drop_column("repetition")
    with op.batch_alter_table("experiment") as batch:
        batch.drop_column("repetitions")
