"""Data-subject rights (E-7, GDPR Art. 15, 17 and 20): erase or export one person's data in an organisation.

Two kinds of people leave data behind. A **member** signs in: their account (`app_user`), API tokens, app-connector
sign-ins, Copilot chats, long-term memories (namespaces that carry their user id) and the conversations they
started. An **end user** of a deployment never signs in: the caller of the deployment API names them with
`user_id`, and their conversations, memories (namespace label `api:<deployment>:<user_id>`) and uploaded files are
theirs.

Erasing keeps ids, never content: audit entries and decisions keep the member's id, which no longer leads to a name
or an email once the account is pseudonymised. A member who still belongs to another organisation keeps their
account; only what they left in this organisation goes.
"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.adapters.spi import MemoryAbility, supports
from agentcanvas.db.models import (
    AuditLog,
    CopilotConversation,
    Membership,
    OAuthConnection,
    OAuthFlow,
    PersonalToken,
    Presence,
    Project,
    ScimGroup,
    ScimGroupMember,
    StoredFile,
    Thread,
    WorkflowRow,
)
from agentcanvas.db.repositories import MemberRepo, delete_thread_rows
from agentcanvas.errors import Conflict, NotFound
from agentcanvas.services.offboarding import erase_profile

NAMESPACES = 100_000  # memory namespaces looked through per erasure


def org_projects(org_id: str) -> Any:
    return select(Project.id).where(Project.org_id == org_id)


def org_workflows(org_id: str) -> Any:
    return select(WorkflowRow.id).where(WorkflowRow.project_id.in_(org_projects(org_id)))


async def memory_namespaces(runner: Any, org_id: str, label: str) -> list[tuple[str, ...]]:
    """The organisation's memory namespaces that carry `label` (a user id, or an end user's `api:` id)."""
    if not supports(runner.adapter, "memory_store"):
        return []
    memory = cast(MemoryAbility, runner.adapter)
    found = await memory.memory_namespaces((f"org:{org_id}",), limit=NAMESPACES)
    return [tuple(ns) for ns in found if label in tuple(ns)[1:]]


async def erase_memories(runner: Any, org_id: str, label: str) -> int:
    memory = cast(MemoryAbility, runner.adapter)
    return sum([await memory.memory_erase(ns) for ns in await memory_namespaces(runner, org_id, label)])


async def delete_conversations(session: AsyncSession, runner: Any, thread_ids: list[str]) -> int:
    """Delete conversations: their saved state first (the rows are its only index), then their rows. Commits; the
    caller must not hold a transaction open (runs on them are stopped first)."""
    await session.commit()
    for i in range(0, len(thread_ids), 200):
        page = thread_ids[i : i + 200]
        await runner.forget_threads(page)
        for thread_id in page:
            await delete_thread_rows(session, thread_id)
        await session.commit()
    return len(thread_ids)


async def is_or_was_member(session: AsyncSession, org_id: str, user_id: str) -> bool:
    """A member of the organisation, or someone who left it (a member of no organisation now) and acted in it."""
    if await session.get(Membership, (org_id, user_id)) is not None:
        return True
    if await session.scalar(select(func.count()).select_from(Membership).where(Membership.user_id == user_id)):
        return False  # belongs elsewhere: not this organisation's to touch
    acted = await session.scalar(
        select(AuditLog.id).where(AuditLog.org_id == org_id, AuditLog.actor_id == user_id).limit(1)
    )
    started = await session.scalar(
        select(Thread.id).where(Thread.created_by == user_id, Thread.workflow_id.in_(org_workflows(org_id))).limit(1)
    )
    return acted is not None or started is not None


async def erase_member(
    session: AsyncSession, runner: Any, org_id: str, user_id: str, *, conversations: bool
) -> dict[str, Any]:
    """Remove a member with what they left in the organisation; pseudonymise their account when they belong to no
    other one. `conversations`: also delete the conversations they started (and the files they attached).
    Returns counts. Commits."""
    if not await is_or_was_member(session, org_id, user_id):
        raise NotFound(f"user '{user_id}' is not a member")
    counts: dict[str, Any] = {}
    for name, table, where in (
        ("tokens", PersonalToken, (PersonalToken.org_id == org_id, PersonalToken.user_id == user_id)),
        ("connections", OAuthConnection, (OAuthConnection.org_id == org_id, OAuthConnection.owner == user_id)),
    ):  # counted now: removing the membership deletes them
        counts[name] = await session.scalar(select(func.count()).select_from(table).where(*where)) or 0
    if await session.get(Membership, (org_id, user_id)) is not None:
        await MemberRepo(session).remove(org_id, user_id)  # first: refused (409) when they are the last admin
    workflows = org_workflows(org_id)
    if conversations:
        ids = list(
            (
                await session.scalars(
                    select(Thread.id).where(Thread.created_by == user_id, Thread.workflow_id.in_(workflows))
                )
            ).all()
        )
        counts["conversations"] = await delete_conversations(session, runner, ids)
        res = await session.execute(
            delete(StoredFile).where(StoredFile.created_by == user_id, StoredFile.project_id.in_(org_projects(org_id)))
        )
        counts["files"] = res.rowcount or 0  # type: ignore[attr-defined]
    await session.commit()  # no write held while the run store (maybe the same SQLite file) is written
    counts["memories"] = await erase_memories(runner, org_id, user_id)
    for name, table, where in (
        ("copilot_conversations", CopilotConversation,
         (CopilotConversation.user_id == user_id, CopilotConversation.workflow_id.in_(workflows))),
        ("", PersonalToken, (PersonalToken.org_id == org_id, PersonalToken.user_id == user_id)),  # a former member's
        ("", OAuthConnection, (OAuthConnection.org_id == org_id, OAuthConnection.owner == user_id)),
        ("", OAuthFlow, (OAuthFlow.org_id == org_id, OAuthFlow.user_id == user_id)),
        ("", Presence, (Presence.user_id == user_id, Presence.workflow_id.in_(workflows))),
        ("", ScimGroupMember, (ScimGroupMember.user_id == user_id,
                               ScimGroupMember.group_id.in_(select(ScimGroup.id).where(ScimGroup.org_id == org_id)))),
    ):  # fmt: skip
        res = await session.execute(delete(table).where(*where))
        if name:
            counts[name] = res.rowcount or 0  # type: ignore[attr-defined]
    elsewhere = await session.scalar(select(func.count()).select_from(Membership).where(Membership.user_id == user_id))
    counts["account_erased"] = not elsewhere and await erase_profile(session, user_id)
    await session.commit()
    return counts


def refuse_self(caller_id: str, user_id: str) -> None:
    if caller_id == user_id:
        raise Conflict("you cannot erase yourself: ask another admin")
