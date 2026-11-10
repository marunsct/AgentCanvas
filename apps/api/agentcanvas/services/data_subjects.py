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

import json
import re
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import Text, delete, func, select
from sqlalchemy import cast as sa_cast
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.adapters.spi import MemoryAbility, supports
from agentcanvas.db.models import (
    AppUser,
    AuditLog,
    CopilotConversation,
    InterruptTask,
    Membership,
    OAuthConnection,
    OAuthFlow,
    PersonalToken,
    Presence,
    Project,
    ProjectMember,
    Run,
    ScimGroup,
    ScimGroupMember,
    StoredFile,
    Thread,
    WorkflowRow,
)
from agentcanvas.db.repositories import MemberRepo, ScopeRepo, delete_thread_rows
from agentcanvas.db.retry import retrying
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

        async def rows(page: list[str] = page) -> None:
            for thread_id in page:
                await delete_thread_rows(session, thread_id)
            await session.commit()

        await retrying(session, rows, what="conversation delete")  # runs that just ended may still write under them
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


# ---------------------------------------------------------------------------------------- end users ----
FILE_ID = re.compile(r"fil_[0-9A-Za-z]{10,40}")


def end_user_label(deployment_id: str, user_id: str) -> str:
    """The id a deployment's end user has in runs, memories and conversations."""
    return f"api:{deployment_id}:{user_id}"


def _is_end_user(label: Any, user_id: str, deployments: set[str] | None) -> bool:
    """`label` names `user_id` of one of `deployments` (None: of any). A user id may hold colons: compared whole."""
    if not isinstance(label, str):
        return False
    parts = label.split(":", 2)
    return (
        len(parts) == 3
        and parts[0] == "api"
        and parts[2] == user_id
        and (deployments is None or parts[1] in deployments)
    )


async def scope_deployment(session: AsyncSession, org_id: str, deployment_id: str | None) -> set[str] | None:
    """The deployments a request is about: one (404 unless it is the organisation's), or None for every one,
    including those deleted since, which conversations and memories still name."""
    if deployment_id is None:
        return None
    if await ScopeRepo(session).org_of("deployment", deployment_id) != org_id:
        raise NotFound(f"deployment '{deployment_id}' not found")
    return {deployment_id}


def _like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def end_user_threads(session: AsyncSession, org_id: str, user_id: str, deployments: set[str] | None) -> list[str]:
    """The conversations of a deployment end user in the organisation: those that name them, and older ones (from
    before conversations kept their end user) whose runs had them as the run's user."""
    workflows = org_workflows(org_id)
    named = select(Thread.id, Thread.end_user).where(
        Thread.workflow_id.in_(workflows), Thread.end_user.like(f"api:%:{_like(user_id)}", escape="\\")
    )
    found = {tid for tid, label in (await session.execute(named)).all() if _is_end_user(label, user_id, deployments)}
    # the run context's JSON text as each database writes it (SQLite escapes non-ASCII characters, Postgres does not)
    needle = json.dumps(f":{user_id}", ensure_ascii=session.get_bind().dialect.name != "postgresql")[1:]
    older = (
        select(Run.thread_id, Run.context)
        .join(Thread, Thread.id == Run.thread_id)
        .where(
            Thread.workflow_id.in_(workflows),
            Thread.end_user.is_(None),
            Thread.deployment_id.is_not(None),
            sa_cast(Run.context, Text).like(f"%{_like(needle)}%", escape="\\"),
        )
    )
    for tid, context in (await session.execute(older)).all():
        if isinstance(context, dict) and any(_is_end_user(v, user_id, deployments) for v in context.values()):
            found.add(tid)
    return sorted(found)


async def end_user_files(session: AsyncSession, org_id: str, thread_ids: list[str]) -> list[str]:
    """The files given to a deployment (not the project's own) that the end user's runs used: their ids appear in the
    runs' input."""
    ids: set[str] = set()
    for i in range(0, len(thread_ids), 500):
        rows = await session.execute(
            select(Run.recording, Run.pending).where(Run.thread_id.in_(thread_ids[i : i + 500]))
        )
        for recording, pending in rows.all():
            ids |= set(FILE_ID.findall(json.dumps([recording, pending], default=str)))
    if not ids:
        return []
    q = select(StoredFile.id).where(
        StoredFile.id.in_(ids), StoredFile.deployment_id.is_not(None), StoredFile.project_id.in_(org_projects(org_id))
    )
    return list((await session.scalars(q)).all())


async def end_user_namespaces(
    runner: Any, org_id: str, user_id: str, deployments: set[str] | None
) -> list[tuple[str, ...]]:
    if not supports(runner.adapter, "memory_store"):
        return []
    found = await cast(MemoryAbility, runner.adapter).memory_namespaces((f"org:{org_id}",), limit=NAMESPACES)
    return [tuple(ns) for ns in found if any(_is_end_user(lb, user_id, deployments) for lb in tuple(ns)[1:])]


async def erase_end_user(
    session: AsyncSession, runner: Any, org_id: str, user_id: str, deployment_id: str | None = None
) -> dict[str, int]:
    """Delete a deployment end user's conversations, long-term memories and the files their runs used: of one
    deployment, or of every deployment of the organisation. Returns counts. Commits."""
    deployments = await scope_deployment(session, org_id, deployment_id)
    threads = await end_user_threads(session, org_id, user_id, deployments)
    files = await end_user_files(session, org_id, threads)
    counts = {"conversations": await delete_conversations(session, runner, threads)}
    res = await session.execute(delete(StoredFile).where(StoredFile.id.in_(files)))
    counts["files"] = res.rowcount or 0  # type: ignore[attr-defined]
    await session.commit()
    namespaces = await end_user_namespaces(runner, org_id, user_id, deployments)
    counts["memories"] = sum([await cast(MemoryAbility, runner.adapter).memory_erase(ns) for ns in namespaces])
    return counts


# ------------------------------------------------------------------------------------------- export ----
def _at(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


async def conversations_of(session: AsyncSession, thread_ids: list[str]) -> list[dict[str, Any]]:
    """Conversations as a person may take them away (GDPR Art. 15 and 20): each run's input, answer and steps."""
    out: list[dict[str, Any]] = []
    for i in range(0, len(thread_ids), 200):
        page = thread_ids[i : i + 200]
        threads = (await session.scalars(select(Thread).where(Thread.id.in_(page)).order_by(Thread.created_at))).all()
        runs: dict[str, list[dict[str, Any]]] = {}
        for r in (await session.scalars(select(Run).where(Run.thread_id.in_(page)).order_by(Run.started_at))).all():
            recording = r.recording or {}
            runs.setdefault(r.thread_id, []).append({
                "id": r.id, "status": r.status, "started_at": _at(r.started_at), "ended_at": _at(r.ended_at),
                "input": recording.get("input"), "output": recording.get("output"), "error": r.error,
                "events": r.events or [],
            })  # fmt: skip
        out += [
            {"id": t.id, "workflow_id": t.workflow_id, "deployment_id": t.deployment_id, "title": t.title,
             "created_at": _at(t.created_at), "updated_at": _at(t.updated_at), "runs": runs.get(t.id, [])}
            for t in threads
        ]  # fmt: skip
    return out


async def memories_in(runner: Any, org_id: str, namespaces: list[tuple[str, ...]]) -> list[dict[str, Any]]:
    """The items of memory namespaces, shown without the organisation's root."""
    memory = cast(MemoryAbility, runner.adapter)
    items: list[dict[str, Any]] = []
    for ns in namespaces:
        for item in await memory.memory_items(ns, None, 10_000):
            if tuple(item["namespace"]) == ns:  # a search also returns deeper namespaces: each is listed itself
                items.append({**item, "namespace": list(ns[1:])})
    return items


def _files(rows: Any) -> list[dict[str, Any]]:
    return [
        {"id": f.id, "name": f.name, "media_type": f.media_type, "size": f.size, "created_at": _at(f.created_at)}
        for f in rows
    ]


async def export_member(
    session: AsyncSession, runner: Any, org_id: str, user_id: str, *, every_organisation: bool
) -> dict[str, Any]:
    """What the organisation holds about a member (GDPR Art. 15 and 20), as JSON. `every_organisation`: list all of
    their memberships (their own request); an admin's request shows only this organisation's."""
    if not await is_or_was_member(session, org_id, user_id):
        raise NotFound(f"user '{user_id}' is not a member")
    person = await session.get(AppUser, user_id)
    if person is None:
        raise NotFound(f"user '{user_id}' is not a member")
    workflows = org_workflows(org_id)
    memberships = select(Membership).where(Membership.user_id == user_id)
    if not every_organisation:
        memberships = memberships.where(Membership.org_id == org_id)
    threads = list(
        (
            await session.scalars(
                select(Thread.id).where(Thread.created_by == user_id, Thread.workflow_id.in_(workflows))
            )
        ).all()
    )
    tokens = (
        await session.scalars(
            select(PersonalToken).where(PersonalToken.org_id == org_id, PersonalToken.user_id == user_id)
        )
    ).all()
    connections = (
        await session.scalars(
            select(OAuthConnection).where(OAuthConnection.org_id == org_id, OAuthConnection.owner == user_id)
        )
    ).all()
    chats = (
        await session.scalars(
            select(CopilotConversation).where(
                CopilotConversation.user_id == user_id, CopilotConversation.workflow_id.in_(workflows)
            )
        )
    ).all()
    decisions = (
        await session.scalars(
            select(InterruptTask).where(
                InterruptTask.decided_by == user_id,
                InterruptTask.thread_id.in_(select(Thread.id).where(Thread.workflow_id.in_(workflows))),
            )
        )
    ).all()
    files = (
        await session.scalars(
            select(StoredFile).where(StoredFile.created_by == user_id, StoredFile.project_id.in_(org_projects(org_id)))
        )
    ).all()
    projects = (
        await session.execute(
            select(ProjectMember, Project.name)
            .join(Project, Project.id == ProjectMember.project_id)
            .where(ProjectMember.user_id == user_id, Project.org_id == org_id)
            .order_by(Project.name)
        )
    ).all()
    audit = (
        await session.scalars(
            select(AuditLog).where(AuditLog.org_id == org_id, AuditLog.actor_id == user_id).order_by(AuditLog.id)
        )
    ).all()
    return {
        "exported_at": _at(datetime.now(UTC)),
        "organisation": org_id,
        "person": {"id": person.id, "email": person.email, "display_name": person.display_name,
                   "preferences": person.preferences, "created_at": _at(person.created_at)},
        "memberships": [{"org_id": m.org_id, "role": m.role, "accepted": m.accepted, "deactivated": m.suspended,
                         "last_used_at": _at(m.last_used_at)} for m in (await session.scalars(memberships)).all()],
        # the projects they were added to by name (what a SCIM group gives them is the group's)
        "project_memberships": [{"project_id": b.project_id, "project": name, "role": b.role,
                                 "created_at": _at(b.created_at)} for b, name in projects],
        "api_tokens": [{"id": t.id, "name": t.name, "prefix": t.prefix, "role": t.role, "created_at": _at(t.created_at),
                        "expires_at": _at(t.expires_at), "last_used_at": _at(t.last_used_at),
                        "revoked_at": _at(t.revoked_at)} for t in tokens],
        "connections": [{"id": c.id, "resource": c.resource, "issuer": c.issuer, "scope": c.scope, "status": c.status,
                         "created_at": _at(c.created_at)} for c in connections],
        "copilot_conversations": [{"id": c.id, "workflow_id": c.workflow_id, "title": c.title, "messages": c.messages,
                                   "created_at": _at(c.created_at), "archived_at": _at(c.archived_at)} for c in chats],
        "conversations": await conversations_of(session, threads),
        "decisions": [{"id": d.id, "thread_id": d.thread_id, "kind": d.kind, "decision": d.decision,
                       "decided_at": _at(d.decided_at)} for d in decisions],
        "memories": await memories_in(runner, org_id, await memory_namespaces(runner, org_id, user_id)),
        "files": _files(files),
        "audit_log": [{"id": a.id, "created_at": _at(a.created_at), "action": a.action, "target": a.target,
                       "detail": a.detail, "ip": a.ip} for a in audit],
    }  # fmt: skip


async def export_end_user(
    session: AsyncSession, runner: Any, org_id: str, user_id: str, deployment_id: str | None = None
) -> dict[str, Any]:
    """What the organisation holds about a deployment's end user, as JSON (GDPR Art. 15 and 20)."""
    deployments = await scope_deployment(session, org_id, deployment_id)
    threads = await end_user_threads(session, org_id, user_id, deployments)
    files = await end_user_files(session, org_id, threads)
    rows = (await session.scalars(select(StoredFile).where(StoredFile.id.in_(files)))).all()
    namespaces = await end_user_namespaces(runner, org_id, user_id, deployments)
    return {
        "exported_at": _at(datetime.now(UTC)),
        "user_id": user_id,
        "deployment_id": deployment_id,
        "conversations": await conversations_of(session, threads),
        "memories": await memories_in(runner, org_id, namespaces),
        "files": _files(rows),
    }
