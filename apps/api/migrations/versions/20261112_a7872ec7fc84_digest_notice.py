"""weekly run digests sent through the approval notification channels (2026-11-12)

`org_notifier.digest` is an organisation's opt-in: each project's weekly run digest also goes to the channels it set
for approval notifications (e-mail, Slack, its own webhook). It is off for every existing organisation (a constant
default: Postgres adds it without rewriting the table). `digest_notice` is the outbox: one row per digest and
channel, claimed by one server at a time and retried until sent. It gets the tenant policy on Postgres (see
`agentcanvas/db/rls.py`; the condition is copied here so later changes to that module do not rewrite this
migration).

Revision ID: a7872ec7fc84
Revises: 37a370ccacd7
Create Date: 2026-11-12 09:30:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7872ec7fc84"
down_revision: str | None = "37a370ccacd7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY = "agentcanvas_tenant"
TABLE = "digest_notice"
TABLES: dict[str, tuple[str, str | None]] = {TABLE: ("org_id", None)}  # copied, as in a3c7e9b1d5f2


def _condition(table: str) -> str:
    column, _ = TABLES[table]
    own = f"{column} = current_setting('matura.tenant_id', true)"
    return f"current_setting('agentcanvas.all_tenants', true) = 'on' OR {own}"


def upgrade() -> None:
    now = sa.text("CURRENT_TIMESTAMP")
    with op.batch_alter_table("org_notifier") as batch:
        batch.add_column(sa.Column("digest", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organization.id", ondelete="CASCADE"), nullable=False),
        sa.Column("digest_id", sa.String(), sa.ForeignKey("run_digest.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('pending','sent','failed','dropped')", name="digest_notice_status"),
    )
    op.create_index("digest_notice_due_idx", TABLE, ["status", "next_at"])
    op.create_index("digest_notice_digest_idx", TABLE, ["digest_id"])
    op.create_index("digest_notice_org_idx", TABLE, ["org_id"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f'ALTER TABLE "{TABLE}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{TABLE}" FORCE ROW LEVEL SECURITY')
        op.execute(f'DROP POLICY IF EXISTS {POLICY} ON "{TABLE}"')
        op.execute(f'CREATE POLICY {POLICY} ON "{TABLE}" USING ({_condition(TABLE)})')


def downgrade() -> None:
    op.drop_table(TABLE)
    with op.batch_alter_table("org_notifier") as batch:
        batch.drop_column("digest")
