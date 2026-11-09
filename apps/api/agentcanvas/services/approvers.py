"""Who may decide an Inbox item (E-6): the `approvers` a step names (Ask for approval, Ask a person, and the tool
approvals of AI Agent and Deep Agent blocks).

A step that names nobody keeps the old behaviour: anyone who may answer the Inbox decides (the reviewer role and
above, minus the person who started the run when the organisation forbids approving your own runs), and on a
published workflow so may its caller (the deployment key, an MCP or A2A client, a share visitor's question, an event
handler). A step that names roles, SCIM groups or people sends the decision to them only, and the caller may answer
only when the step also allows `caller`. `not_initiator` keeps the person who started the run from deciding it.

The step's setting is copied onto the Inbox item when the run pauses (`InterruptTask.approvers`), so a later edit of
the workflow does not change who decides a pause that is already waiting. An escalated item (`assignee_role`) may also
be decided by members with that role.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.db.models import AppUser, InterruptTask, ScimGroup, ScimGroupMember
from agentcanvas.errors import Forbidden
from agentcanvas.ir.models import Workflow

ROLES = ("reviewer", "builder", "admin")


class Approvers(BaseModel):
    """A step's `approvers` setting (the node manifests hold its JSON Schema)."""

    model_config = ConfigDict(extra="ignore")
    roles: list[str] = Field(default_factory=list)
    groups: list[str] = Field(default_factory=list)
    people: list[str] = Field(default_factory=list)
    caller: bool = False
    not_initiator: bool = False

    @classmethod
    def of(cls, stored: Any) -> Approvers:
        """The setting as stored on an Inbox item; anything unreadable counts as naming nobody."""
        try:
            return cls.model_validate(stored) if isinstance(stored, dict) else cls()
        except ValueError:
            return cls()

    @property
    def named(self) -> bool:
        """Names who may decide (rather than leaving it to anyone who answers the Inbox)."""
        return bool(self.roles or self.groups or self.people)

    @property
    def routed(self) -> bool:
        """Changes who may decide at all: then the caller of a published workflow answers only if allowed."""
        return self.named or self.not_initiator

    def summary(self, assignee_role: str | None = None) -> str:
        """Who may decide, in words: for an error message, a notification or the Inbox."""
        roles: list[str] = list(dict.fromkeys([*self.roles, *([assignee_role] if assignee_role else [])]))
        parts = [f"{', '.join(roles)} role{'s' if len(roles) > 1 else ''}" if roles else ""]
        parts += [f"group {', '.join(self.groups)}" if self.groups else "", ", ".join(self.people)]
        return "; ".join(p for p in parts if p) or "anyone who answers the Inbox"


def node_approvers(wf: Workflow) -> dict[str, dict[str, Any]]:
    """Each step's `approvers` (canvas node id -> setting), inside embedded subgraphs too: an interrupt names the
    step that asked, also when it sits in a subgraph."""
    out: dict[str, dict[str, Any]] = {}

    def walk(nodes: Iterable[Any]) -> None:
        for n in nodes:
            node_id, ntype, config = (
                (n.get("id"), n.get("type"), n.get("config") or {})
                if isinstance(n, dict)
                else (n.id, n.type, n.config or {})
            )
            if isinstance(config.get("approvers"), dict) and config["approvers"]:
                out[str(node_id)] = config["approvers"]
            inner = config.get("workflow") if ntype == "core.subgraph" else None
            if isinstance(inner, dict):
                walk(inner.get("nodes") or [])

    walk(wf.nodes)
    return out


@dataclass(frozen=True)
class Decider:
    """Who is answering: a member of the organisation (a user id and their role there), or, with no user, the caller
    of a published workflow."""

    user_id: str | None = None
    role: str | None = None
    org_id: str | None = None


CALLER = Decider()


@dataclass
class Member:
    """What a member's right to decide depends on, looked up once for any number of items."""

    user_id: str
    role: str
    email: str = ""
    groups: set[str] = field(default_factory=set)


async def member(session: AsyncSession, who: Decider) -> Member:
    assert who.user_id is not None and who.role is not None
    email = await session.scalar(select(AppUser.email).where(AppUser.id == who.user_id))
    groups = await session.scalars(
        select(ScimGroup.display_name)
        .join(ScimGroupMember, ScimGroupMember.group_id == ScimGroup.id)
        .where(ScimGroupMember.user_id == who.user_id, ScimGroup.org_id == who.org_id)
    )
    return Member(who.user_id, who.role, (email or "").strip().lower(), {g.casefold() for g in groups.all()})


def refusal(policy: Approvers, assignee_role: str | None, m: Member, started_by: str | None) -> str | None:
    """Why `m` may not decide an item with this setting, or None when they may."""
    if policy.not_initiator and started_by == m.user_id:
        return "this step may not be decided by the person who started the run: ask another reviewer"
    if not policy.named:
        return None
    if m.role in policy.roles or (assignee_role and m.role == assignee_role):
        return None
    if m.email and m.email in {p.strip().lower() for p in policy.people}:
        return None
    if m.groups & {g.casefold() for g in policy.groups}:
        return None
    return f"this step may be decided only by: {policy.summary(assignee_role)}"


def caller_refusal(policy: Approvers) -> str | None:
    if policy.routed and not policy.caller:
        return (
            "this step is decided by the people its workflow names, in the AgentCanvas Inbox; the caller of the "
            "published workflow cannot answer it"
        )
    return None


async def check(
    session: AsyncSession, tasks: Iterable[InterruptTask], who: Decider, started_by: str | None = None
) -> None:
    """Refuse (403) unless `who` may decide every one of `tasks`. `started_by`: who started the conversation, for
    items that do not record who asked."""
    tasks = list(tasks)
    m: Member | None = None
    for task in tasks:
        policy = Approvers.of(task.approvers)
        if who.user_id is None:
            why = caller_refusal(policy)
        else:
            if m is None:
                m = await member(session, who)
            why = refusal(policy, task.assignee_role, m, task.requested_by or started_by)
        if why:
            raise Forbidden(why)


async def check_pending(
    session: AsyncSession, thread_id: str, interrupt_id: str | None, who: Decider, started_by: str | None = None
) -> None:
    """`check` for what a resume of the conversation answers: the named item, else every waiting one."""
    q = select(InterruptTask).where(InterruptTask.thread_id == thread_id, InterruptTask.status == "pending")
    if interrupt_id:
        q = q.where(InterruptTask.id == interrupt_id)
    await check(session, (await session.scalars(q)).all(), who, started_by)
