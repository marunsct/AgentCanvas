"""Row-level security on AgentCanvas's own tables (TS26, Matura contract §4 rule 4; doc 18 T5).

On Postgres every organisation-owned table has the policy `agentcanvas_tenant` (migration `a3c7e9b1d5f2`): a row is
visible, and may be written, only when it belongs to the organisation named by the setting `matura.tenant_id`. Tables
with an `org_id` compare it directly; the others reach it through their parent (a workflow through its project, a
run through its thread, and so on), so a child row can never be read or written in another organisation either.

Two settings decide what a connection sees:

| `agentcanvas.all_tenants` | `matura.tenant_id` | rows visible                                                   |
|---------------------------|--------------------|----------------------------------------------------------------|
| `on`                      | (any)              | every organisation's: background work, public deployment calls |
| not `on`                  | an organisation id | that organisation's only                                       |
| not `on`                  | unset or empty     | none (fails closed)                                            |

The API's own connections start with `agentcanvas.all_tenants=on` (a startup option, see `Database`), so jobs that
work across organisations (runs handed over, retention, triggers, the public run API) and the `dev` and `oidc` modes
behave as before. In `matura` mode a signed-in request narrows its database session to the token's organisation for
every transaction (`scope`), and so does every other session opened while serving it or by work it started
(`TenantSession`), so a query that forgets its `org_id` filter still returns nothing of another tenant.

Postgres superusers and roles with `BYPASSRLS` skip every policy: connect the API as an ordinary role (the
migration forces the policies on the tables' owner too). SQLite has no row-level security; there the API's own
filters are the only isolation, as before.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

POLICY = "agentcanvas_tenant"
ALL_TENANTS = "agentcanvas.all_tenants"
TENANT = "matura.tenant_id"

# table -> (column, parent table); parent None: the column is the organisation id itself
TENANT_TABLES: dict[str, tuple[str, str | None]] = {
    "project": ("org_id", None),
    "org_policy": ("org_id", None),
    "org_data_key": ("org_id", None),
    "audit_log": ("org_id", None),
    "workflow": ("project_id", "project"),
    "secret": ("project_id", "project"),
    "connection": ("project_id", "project"),
    "knowledge_base": ("project_id", "project"),
    "event_log": ("project_id", "project"),
    "event_dedup": ("project_id", "project"),
    "workflow_version": ("workflow_id", "workflow"),
    "test_case": ("workflow_id", "workflow"),
    "deployment": ("workflow_id", "workflow"),
    "thread": ("workflow_id", "workflow"),
    "dataset": ("workflow_id", "workflow"),
    "evaluator": ("workflow_id", "workflow"),
    "experiment": ("workflow_id", "workflow"),
    "release_gate": ("workflow_id", "workflow"),
    "run_score": ("workflow_id", "workflow"),
    "eval_alert": ("workflow_id", "workflow"),
    "cost_alert": ("workflow_id", "workflow"),
    "annotation_queue": ("workflow_id", "workflow"),
    "trigger": ("workflow_id", "workflow"),
    "copilot_conversation": ("workflow_id", "workflow"),
    "git_link": ("workflow_id", "workflow"),
    "dataset_version": ("dataset_id", "dataset"),
    "experiment_result": ("experiment_id", "experiment"),
    "knowledge_document": ("kb_id", "knowledge_base"),
    "knowledge_chunk": ("kb_id", "knowledge_base"),
    "deployment_share": ("deployment_id", "deployment"),
    "share_thread": ("deployment_id", "deployment"),
    "online_evaluator": ("deployment_id", "deployment"),
    "run": ("thread_id", "thread"),
    "interrupt_task": ("thread_id", "thread"),
    "annotation_item": ("queue_id", "annotation_queue"),
    "annotation": ("item_id", "annotation_item"),
}
# tables without a policy, and why
EXEMPT: dict[str, str] = {
    "organization": "the organisation list itself; the organisation picker reads every one the user belongs to",
    "app_user": "people, shared between organisations",
    "membership": "who belongs where; sign-in reads all of a user's memberships",
    "rate_window": "request counters keyed by deployment or webhook, holding no organisation data",
}


def policy_condition(table: str) -> str:
    column, parent = TENANT_TABLES[table]
    if parent is None:
        own = f"{column} = current_setting('{TENANT}', true)"
    else:  # the parent's own policy applies inside the subquery, so the check follows the chain up to the project
        own = f'EXISTS (SELECT 1 FROM {parent} p WHERE p.id = "{table}".{column})'
    return f"current_setting('{ALL_TENANTS}', true) = 'on' OR {own}"


def connect_options(url: str, connect_args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Connection arguments that start every API connection in all-tenants mode (Postgres only)."""
    args = dict(connect_args or {})
    if url.startswith("postgresql"):
        args["options"] = (args.get("options", "") + f" -c {ALL_TENANTS}=on").strip()
    return args


REQUEST_ORG: ContextVar[str | None] = ContextVar("agentcanvas_request_org", default=None)
_NARROW = text(f"SELECT set_config('{TENANT}', :org, true), set_config('{ALL_TENANTS}', 'off', true)")


class TenantSession(Session):
    """The API's sessions. One opened while a Matura request is being served (or by work it started) is narrowed
    to the request's organisation like the request's own session, not left in all-tenants mode."""


@event.listens_for(TenantSession, "after_begin")
def _narrow_request_sessions(session: Session, _transaction: Any, connection: Any) -> None:
    org = REQUEST_ORG.get()
    if org is None or "matura_org" in session.info or connection.dialect.name != "postgresql":
        return
    connection.execute(_NARROW, {"org": org})


async def scope(session: AsyncSession, org_id: str) -> None:
    """Limit `session` to one organisation: now, if a transaction is open, and at the start of every later one.

    `set_config(..., true)` lasts until the transaction ends, so a pooled connection goes back to all-tenants mode
    on its own. No-op outside Postgres.
    """
    if session.bind is None or session.bind.dialect.name != "postgresql":
        return
    REQUEST_ORG.set(org_id)  # other sessions this request opens are narrowed too (TenantSession)
    first = "matura_org" not in session.info
    session.info["matura_org"] = org_id
    sql = text(f"SELECT set_config('{TENANT}', :org, true), set_config('{ALL_TENANTS}', 'off', true)")
    if first:

        @event.listens_for(session.sync_session, "after_begin")
        def _narrow(_session: Any, _transaction: Any, connection: Any) -> None:
            connection.execute(sql, {"org": session.info["matura_org"]})

    if session.in_transaction():
        await session.execute(sql, {"org": org_id})


async def bypasses_policies(engine: Any) -> bool:
    """True when the API's database role skips row-level security (a superuser or a role with BYPASSRLS), as the
    official Postgres image's POSTGRES_USER does. Always False outside Postgres."""
    if engine.dialect.name != "postgresql":
        return False
    async with engine.connect() as conn:
        found = await conn.execute(text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"))
        return bool(found.scalar())
