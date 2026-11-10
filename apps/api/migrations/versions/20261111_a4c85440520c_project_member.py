"""project members: who works in a project, and with what role (2026-11-11)

Roles were the organisation's only (`membership.role`), and every member saw and changed every project. A
`project_member` row binds one person or one SCIM group to a project with a role. With the organisation's projects
private (`organization.settings.private_projects`, off by default), only organisation admins and the project's
members see it; otherwise a binding can only raise someone's role in that project. Nothing is written here, so an
existing installation behaves as before. On Postgres the table gets the tenant policy like every organisation-owned
table (see `agentcanvas/db/rls.py`; the condition is copied here so later changes to that module do not rewrite this
migration).

Revision ID: a4c85440520c
Revises: a7872ec7fc84
Create Date: 2026-11-11 09:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a4c85440520c"
down_revision: str | None = "a7872ec7fc84"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY = "agentcanvas_tenant"
TABLE = "project_member"
TABLES: dict[str, tuple[str, str | None]] = {TABLE: ("project_id", "project")}  # copied, as in a3c7e9b1d5f2


def _condition(table: str) -> str:
    column, parent = TABLES[table]
    own = f'EXISTS (SELECT 1 FROM {parent} p WHERE p.id = "{table}".{column})'
    return f"current_setting('agentcanvas.all_tenants', true) = 'on' OR {own}"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(), sa.ForeignKey("app_user.id", ondelete="CASCADE"), nullable=True),
        sa.Column("group_id", sa.String(), sa.ForeignKey("scim_group.id", ondelete="CASCADE"), nullable=True),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.CheckConstraint("role IN ('admin','builder','reviewer','viewer')", name="project_member_role"),
        sa.CheckConstraint("(user_id IS NULL) <> (group_id IS NULL)", name="project_member_principal"),
        sa.UniqueConstraint("project_id", "user_id", name="project_member_user"),
        sa.UniqueConstraint("project_id", "group_id", name="project_member_group"),
    )
    op.create_index("project_member_user_idx", TABLE, ["user_id"])
    op.create_index("project_member_group_idx", TABLE, ["group_id"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f'ALTER TABLE "{TABLE}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{TABLE}" FORCE ROW LEVEL SECURITY')
        op.execute(f'DROP POLICY IF EXISTS {POLICY} ON "{TABLE}"')
        op.execute(f'CREATE POLICY {POLICY} ON "{TABLE}" USING ({_condition(TABLE)})')


def downgrade() -> None:
    op.drop_table(TABLE)
