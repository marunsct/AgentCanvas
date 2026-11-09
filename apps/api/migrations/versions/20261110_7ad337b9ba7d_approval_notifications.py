"""approval notifications (2026-11-10)

`org_notifier` keeps where an organisation's approval notifications go: e-mail on or off and its fixed addresses, a
webhook's address, and, encrypted with the organisation's data key, the Slack address (it holds Slack's token) and
the webhook's signing secret. `approval_notice` is the outbox: one row per Inbox item, event and channel, claimed by
one server at a time and retried until sent. `interrupt_task.noticed` records which announcement of an item a server
claimed, so each is sent once whatever the number of servers. Both new tables get the tenant policy on Postgres (see
`agentcanvas/db/rls.py`; the condition is copied here so later changes to that module do not rewrite this
migration). The new column is nullable without a default: no existing row is rewritten.

Revision ID: 7ad337b9ba7d
Revises: 96b2a0098aeb
Create Date: 2026-11-10 21:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "7ad337b9ba7d"
down_revision: str | None = "96b2a0098aeb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(JSONB(), "postgresql")
POLICY = "agentcanvas_tenant"
# both carry org_id themselves (copied, as in a3c7e9b1d5f2)
TABLES: dict[str, tuple[str, str | None]] = {"org_notifier": ("org_id", None), "approval_notice": ("org_id", None)}


def _condition(table: str) -> str:
    column, _ = TABLES[table]
    own = f"{column} = current_setting('matura.tenant_id', true)"
    return f"current_setting('agentcanvas.all_tenants', true) = 'on' OR {own}"


def upgrade() -> None:
    now = sa.text("CURRENT_TIMESTAMP")
    op.create_table(
        "org_notifier",
        sa.Column("org_id", sa.String(), sa.ForeignKey("organization.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("email", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("email_to", JSON, server_default=sa.text("'[]'"), nullable=False),
        sa.Column("webhook_url", sa.Text(), nullable=True),
        sa.Column("slack_url", sa.LargeBinary(), nullable=True),
        sa.Column("webhook_secret", sa.LargeBinary(), nullable=True),
        sa.Column("updated_by", sa.String(), sa.ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
    )
    op.create_table(
        "approval_notice",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organization.id", ondelete="CASCADE"), nullable=False),
        sa.Column("interrupt_id", sa.String(), sa.ForeignKey("interrupt_task.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event", sa.Text(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('pending','sent','failed','dropped')", name="approval_notice_status"),
    )
    op.create_index("approval_notice_due_idx", "approval_notice", ["status", "next_at"])
    op.create_index("approval_notice_interrupt_idx", "approval_notice", ["interrupt_id"])
    with op.batch_alter_table("interrupt_task") as batch:
        batch.add_column(sa.Column("noticed", sa.Text(), nullable=True))
    if op.get_bind().dialect.name == "postgresql":
        for table in TABLES:
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
            op.execute(f'DROP POLICY IF EXISTS {POLICY} ON "{table}"')
            op.execute(f'CREATE POLICY {POLICY} ON "{table}" USING ({_condition(table)})')


def downgrade() -> None:
    with op.batch_alter_table("interrupt_task") as batch:
        batch.drop_column("noticed")
    op.drop_table("approval_notice")
    op.drop_table("org_notifier")
