"""a workflow connected to a Git branch (G-14)

`git_link`: the repository, branch and folder a workflow is exported to as a pull request, the secret holding the
GitHub token, when to open one (`auto`), and what the last sync left (the hash of the IR on the branch, the pull
request, an error). Like the other tables below a workflow it has the row-level security policy
`agentcanvas_tenant` on Postgres (see `agentcanvas/db/rls.py`); the condition is copied here so later changes to the
module do not rewrite this migration.

Revision ID: e1a5c9d3f7b2
Revises: f3c7d1e5a9b4
Create Date: 2026-10-29 18:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e1a5c9d3f7b2"
down_revision: str | None = "f3c7d1e5a9b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY = "agentcanvas_tenant"
TABLES: dict[str, tuple[str, str]] = {"git_link": ("workflow_id", "workflow")}  # table -> (column, parent table)


def _condition(table: str) -> str:
    column, parent = TABLES[table]
    own = f'EXISTS (SELECT 1 FROM {parent} p WHERE p.id = "{table}".{column})'
    return f"current_setting('agentcanvas.all_tenants', true) = 'on' OR {own}"


def upgrade() -> None:
    op.create_table(
        "git_link",
        sa.Column("workflow_id", sa.String(), sa.ForeignKey("workflow.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("repo", sa.Text(), nullable=False),
        sa.Column("branch", sa.Text(), nullable=False),
        sa.Column("folder", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("token_secret", sa.Text(), server_default=sa.text("'GITHUB_TOKEN'"), nullable=False),
        sa.Column("target", sa.Text(), server_default=sa.text("'local'"), nullable=False),
        sa.Column("auto", sa.Text(), server_default=sa.text("'publish'"), nullable=False),
        sa.Column("repo_hash", sa.Text(), nullable=True),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column("pr_url", sa.Text(), nullable=True),
        sa.Column("last_version", sa.Text(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(), sa.ForeignKey("app_user.id"), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
    )
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(f'DROP POLICY IF EXISTS {POLICY} ON "{table}"')
        op.execute(f'CREATE POLICY {POLICY} ON "{table}" USING ({_condition(table)})')


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table in TABLES:
            op.execute(f'DROP POLICY IF EXISTS {POLICY} ON "{table}"')
    op.drop_table("git_link")
