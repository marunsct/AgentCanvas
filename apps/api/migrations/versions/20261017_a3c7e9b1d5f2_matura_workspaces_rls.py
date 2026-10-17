"""Matura workspaces and row-level security (TS26)

A project may belong to a Matura workspace (`project.workspace_id`, NULL = none; Matura mode only). On Postgres,
every organisation-owned table gets the forced row-level security policy `agentcanvas_tenant` (see
`agentcanvas/db/rls.py`): rows are visible only for the organisation in `matura.tenant_id`, or to connections in
all-tenants mode, which is how the API connects unless a Matura request narrows its session. The table list is
copied here so later changes to the module do not rewrite this migration.

Revision ID: a3c7e9b1d5f2
Revises: f2b6d0a4c8e1
Create Date: 2026-10-17 16:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3c7e9b1d5f2"
down_revision: str | None = "f2b6d0a4c8e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY = "agentcanvas_tenant"
# table -> (column, parent table); parent None: the column is the organisation id
TABLES: dict[str, tuple[str, str | None]] = {
    "project": ("org_id", None), "org_policy": ("org_id", None), "org_data_key": ("org_id", None),
    "audit_log": ("org_id", None),
    "workflow": ("project_id", "project"), "secret": ("project_id", "project"),
    "connection": ("project_id", "project"), "knowledge_base": ("project_id", "project"),
    "event_log": ("project_id", "project"), "event_dedup": ("project_id", "project"),
    "workflow_version": ("workflow_id", "workflow"), "test_case": ("workflow_id", "workflow"),
    "deployment": ("workflow_id", "workflow"), "thread": ("workflow_id", "workflow"),
    "dataset": ("workflow_id", "workflow"), "evaluator": ("workflow_id", "workflow"),
    "experiment": ("workflow_id", "workflow"), "release_gate": ("workflow_id", "workflow"),
    "run_score": ("workflow_id", "workflow"), "eval_alert": ("workflow_id", "workflow"),
    "cost_alert": ("workflow_id", "workflow"), "annotation_queue": ("workflow_id", "workflow"),
    "trigger": ("workflow_id", "workflow"), "copilot_conversation": ("workflow_id", "workflow"),
    "dataset_version": ("dataset_id", "dataset"), "experiment_result": ("experiment_id", "experiment"),
    "knowledge_document": ("kb_id", "knowledge_base"), "knowledge_chunk": ("kb_id", "knowledge_base"),
    "deployment_share": ("deployment_id", "deployment"), "share_thread": ("deployment_id", "deployment"),
    "online_evaluator": ("deployment_id", "deployment"),
    "run": ("thread_id", "thread"), "interrupt_task": ("thread_id", "thread"),
    "annotation_item": ("queue_id", "annotation_queue"), "annotation": ("item_id", "annotation_item"),
}  # fmt: skip


def _condition(table: str) -> str:
    column, parent = TABLES[table]
    own = (
        f"{column} = current_setting('matura.tenant_id', true)"
        if parent is None
        else f'EXISTS (SELECT 1 FROM {parent} p WHERE p.id = "{table}".{column})'
    )
    return f"current_setting('agentcanvas.all_tenants', true) = 'on' OR {own}"


def upgrade() -> None:
    with op.batch_alter_table("project") as batch:
        batch.add_column(sa.Column("workspace_id", sa.String(), nullable=True))
    op.create_index("project_workspace_idx", "project", ["org_id", "workspace_id"])
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
            op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
    op.drop_index("project_workspace_idx", table_name="project")
    with op.batch_alter_table("project") as batch:
        batch.drop_column("workspace_id")
