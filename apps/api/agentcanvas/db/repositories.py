"""Repositories: the only code that issues SQL. No business logic here (build-guide/05 §1)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from agentcanvas.db.models import (
    AppUser,
    AuditLog,
    Deployment,
    InterruptTask,
    Membership,
    Organization,
    OrgPolicy,
    Project,
    Run,
    Thread,
    Trigger,
    WorkflowRow,
)
from agentcanvas.errors import Conflict, NotFound
from agentcanvas.ids import new_id

DEV_ORG, DEV_USER, DEV_PROJECT = "org_default", "usr_dev", "prj_default"


async def seed_dev(session: AsyncSession) -> None:
    """Create the default organisation, user and project used before authentication exists (M7)."""
    if await session.get(Organization, DEV_ORG) is None:
        session.add(Organization(id=DEV_ORG, name="Default organisation"))
        session.add(AppUser(id=DEV_USER, oidc_subject="dev", email="dev@localhost", display_name="Developer"))
        await session.flush()
        session.add(Membership(org_id=DEV_ORG, user_id=DEV_USER, role="admin"))
        session.add(Project(id=DEV_PROJECT, org_id=DEV_ORG, name="Default project"))
        await session.commit()


class UserRepo:
    def __init__(self, s: AsyncSession):
        self.s = s

    async def by_subject(self, subject: str) -> AppUser | None:
        return await self.s.scalar(select(AppUser).where(AppUser.oidc_subject == subject))

    async def provision(
        self, subject: str, email: str, name: str | None, email_verified: bool = False
    ) -> tuple[AppUser, bool]:
        """The user of an OIDC subject and whether this sign-in created it; email and name follow the latest token.

        Provider subjects are stored as `oidc:<sub>`, apart from the seeded `dev` user and `pending:<email>`
        invitations, so no token can take over either. A verified email claims the invitation an admin made for
        it (see `MemberRepo.set_role`): a first sign-in takes over the placeholder, a returning user (who signed
        in before being invited) gets its memberships moved over.
        """
        user = await self.by_subject(oidc_subject(subject))
        pending = await self.by_subject(pending_subject(email)) if email and email_verified else None
        created = False
        if user is None and pending is not None:
            pending.oidc_subject, user, created = oidc_subject(subject), pending, True
            await self.s.commit()
        elif user is not None and pending is not None:
            await self._merge_invitation(pending, user)
        if user is None:
            user = AppUser(id=new_id("usr"), oidc_subject=oidc_subject(subject), email=email, display_name=name)
            self.s.add(user)
            try:
                await self.s.commit()
                created = True
            except IntegrityError:  # a parallel first request created it
                await self.s.rollback()
                user = await self.by_subject(oidc_subject(subject))
                assert user is not None
        if (email and user.email != email) or (name and user.display_name != name):
            user.email, user.display_name = email or user.email, name or user.display_name
            await self.s.commit()
        return user, created

    async def _merge_invitation(self, pending: AppUser, user: AppUser) -> None:
        """Move an invitation's memberships to the user who claimed it (existing memberships win), then drop it.
        Two first requests in parallel may both try: the loser finds the work done and lets it be."""
        try:
            mine = await self.memberships(user.id, accepted_only=False)
            for m in (await self.s.scalars(select(Membership).where(Membership.user_id == pending.id))).all():
                if m.org_id not in mine:  # someone who already belongs somewhere accepts it first
                    self.s.add(Membership(org_id=m.org_id, user_id=user.id, role=m.role, accepted=not mine))
                await self.s.delete(m)
            await self.s.flush()
            await self.s.delete(pending)
            await self.s.commit()
        except (IntegrityError, StaleDataError):
            await self.s.rollback()

    async def memberships(self, user_id: str, accepted_only: bool = True) -> dict[str, str]:
        """org id -> role; the default organisation (the one used last) first."""
        q = select(Membership.org_id, Membership.role).where(Membership.user_id == user_id)
        if accepted_only:
            q = q.where(Membership.accepted.is_(True))
        q = q.order_by(Membership.last_used_at.desc().nulls_last(), Membership.org_id)
        return {org: role for org, role in (await self.s.execute(q)).all()}

    async def used(self, user_id: str, org_id: str) -> None:
        """Remember the organisation a user just chose: it is their default from now on."""
        await self.s.execute(
            update(Membership)
            .where(Membership.user_id == user_id, Membership.org_id == org_id)
            .values(last_used_at=datetime.now(UTC))
        )
        await self.s.commit()

    async def invitations(self, user_id: str) -> list[tuple[Organization, str]]:
        q = (
            select(Organization, Membership.role)
            .join(Membership, Membership.org_id == Organization.id)
            .where(Membership.user_id == user_id, Membership.accepted.is_(False))
            .order_by(Organization.name)
        )
        return [(o, role) for o, role in (await self.s.execute(q)).all()]

    async def answer(self, user_id: str, org_id: str, accept: bool) -> None:
        m = await self.s.get(Membership, (org_id, user_id))
        if m is None or m.accepted:
            raise NotFound(f"no invitation to organisation '{org_id}'")
        if accept:
            m.accepted, m.last_used_at = True, datetime.now(UTC)  # land in the organisation just joined
        else:
            await self.s.delete(m)
        await self.s.commit()

    async def bootstrap_admin(self, user_id: str, org_id: str) -> dict[str, str]:
        try:  # a parallel bootstrap may create the organisation or the membership first
            if await self.s.get(Organization, org_id) is None:
                self.s.add(Organization(id=org_id, name="Default organisation"))
                await self.s.flush()
            self.s.add(Membership(org_id=org_id, user_id=user_id, role="admin"))
            await self.s.commit()
        except IntegrityError:
            await self.s.rollback()
        return await self.memberships(user_id)


def oidc_subject(sub: str) -> str:
    """How an identity provider's subject is stored (apart from `dev` and `pending:` placeholders)."""
    return f"oidc:{sub}"


async def retire_dev_user(session: AsyncSession) -> None:
    """With OIDC on, the seeded development user keeps no memberships: nobody can sign in as it, and it must not
    count as an organisation's admin."""
    await session.execute(delete(Membership).where(Membership.user_id == DEV_USER))
    await session.commit()


def pending_subject(email: str) -> str:
    """The placeholder subject of an invited user who has not signed in yet."""
    return f"pending:{email.strip().lower()}"


class MemberRepo:
    """Members of an organisation and their roles (M7-T3)."""

    def __init__(self, s: AsyncSession):
        self.s = s

    async def list(self, org_id: str) -> list[tuple[AppUser, Membership]]:
        q = (
            select(AppUser, Membership)
            .join(Membership, Membership.user_id == AppUser.id)
            .where(Membership.org_id == org_id)
            .order_by(AppUser.email)
        )
        return [(u, m) for u, m in (await self.s.execute(q)).all()]

    async def set_role(self, org_id: str, email: str, role: str, *, retry: bool = True) -> tuple[AppUser, str]:
        """Add the person with this email to the organisation, or change their role. Someone who has never
        signed in gets a placeholder user that their first verified sign-in claims."""
        email = email.strip().lower()
        await self._lock(org_id)
        user = await self.s.scalar(select(AppUser).where(func.lower(AppUser.email) == email).order_by(AppUser.id))
        if user is None:
            user = AppUser(id=new_id("usr"), oidc_subject=pending_subject(email), email=email)
            self.s.add(user)
            try:
                await self.s.flush()
            except IntegrityError:  # another organisation invited the same new email at the same moment
                await self.s.rollback()
                if not retry:
                    raise
                return await self.set_role(org_id, email, role, retry=False)
        m = await self.s.get(Membership, (org_id, user.id))
        if m is None:
            elsewhere = await self.s.scalar(
                select(func.count())
                .select_from(Membership)
                .where(Membership.user_id == user.id, Membership.org_id != org_id, Membership.accepted.is_(True))
            )
            # someone who already belongs to another organisation decides whether to join (S11)
            self.s.add(Membership(org_id=org_id, user_id=user.id, role=role, accepted=not elsewhere))
        else:
            m.role = role
            await self._keep_an_admin(org_id)
        await self.s.commit()
        return user, role

    async def remove(self, org_id: str, user_id: str) -> None:
        await self._lock(org_id)
        m = await self.s.get(Membership, (org_id, user_id))
        if m is None:
            raise NotFound(f"user '{user_id}' is not a member")
        await self.s.delete(m)
        await self._keep_an_admin(org_id)
        await self.s.commit()

    async def _lock(self, org_id: str) -> None:
        """Serialise membership changes of one organisation until commit (row lock on Postgres; SQLite allows one
        writer at a time), so two admins demoting each other cannot both pass the last-admin check."""
        await self.s.execute(select(Organization.id).where(Organization.id == org_id).with_for_update())

    async def _keep_an_admin(self, org_id: str) -> None:
        """After the change, inside its transaction: someone who can sign in must still be admin, or nothing is
        committed. Checking after the write also holds on SQLite, where the row lock is a no-op: the write takes the
        database's write lock, so a parallel change is either seen here or waits for this one. Invitations nobody
        has claimed yet do not count: they may never sign in."""
        await self.s.flush()
        q = (
            select(func.count())
            .select_from(Membership)
            .join(AppUser, AppUser.id == Membership.user_id)
            .where(Membership.org_id == org_id, Membership.role == "admin", Membership.accepted.is_(True))
            .where(~AppUser.oidc_subject.startswith("pending:"))
        )
        if not await self.s.scalar(q):
            await self.s.rollback()
            raise Conflict("an organisation needs at least one admin who has signed in; make someone else admin first")


class AuditRepo:
    """The append-only audit log (M7-T4): rows are added, never changed or deleted by the application."""

    def __init__(self, s: AsyncSession):
        self.s = s

    async def add(self, row: AuditLog) -> None:
        self.s.add(row)
        await self.s.commit()

    async def list(
        self,
        org_id: str,
        *,
        action: str | None = None,
        actor_id: str | None = None,
        target: str | None = None,
        before: int | None = None,
        limit: int = 100,
    ) -> list[tuple[AuditLog, str | None]]:
        q = (
            select(AuditLog, AppUser.email)
            .outerjoin(AppUser, AppUser.id == AuditLog.actor_id)
            .where(AuditLog.org_id == org_id)
        )
        if action:
            q = q.where(AuditLog.action == action)
        if actor_id:
            q = q.where(AuditLog.actor_id == actor_id)
        if target:
            q = q.where(AuditLog.target.contains(target))
        if before:
            q = q.where(AuditLog.id < before)
        rows = await self.s.execute(q.order_by(AuditLog.id.desc()).limit(limit))
        return [(row, email) for row, email in rows.all()]


class ScopeRepo:
    """Which organisation owns a resource (M7-T2): the API checks every id a request names against the caller's."""

    def __init__(self, s: AsyncSession):
        self.s = s

    async def org_of(self, kind: str, rid: str) -> str | None:
        """The owning organisation of `kind` ('project', 'workflow', 'thread', 'run', 'deployment', 'trigger',
        'interrupt') with id `rid`, or None when it does not exist."""
        via_workflow = select(Project.org_id).join(WorkflowRow, WorkflowRow.project_id == Project.id)
        via_thread = via_workflow.join(Thread, Thread.workflow_id == WorkflowRow.id)
        q = {
            "project": select(Project.org_id).where(Project.id == rid),
            "workflow": via_workflow.where(WorkflowRow.id == rid),
            "thread": via_thread.where(Thread.id == rid),
            "run": via_thread.join(Run, Run.thread_id == Thread.id).where(Run.id == rid),
            "deployment": via_workflow.join(Deployment, Deployment.workflow_id == WorkflowRow.id).where(
                Deployment.id == rid
            ),
            "trigger": via_workflow.join(Trigger, Trigger.workflow_id == WorkflowRow.id).where(Trigger.id == rid),
            "interrupt": via_thread.join(InterruptTask, InterruptTask.thread_id == Thread.id).where(
                InterruptTask.id == rid
            ),
        }[kind]
        return await self.s.scalar(q)

    async def member_ids(self, org_id: str) -> set[str]:
        return set((await self.s.scalars(select(Membership.user_id).where(Membership.org_id == org_id))).all())


class ProjectRepo:
    def __init__(self, s: AsyncSession):
        self.s = s

    async def list(self, org_id: str) -> list[Project]:
        return list((await self.s.scalars(select(Project).where(Project.org_id == org_id).order_by(Project.id))).all())

    async def create(self, org_id: str, name: str) -> Project:
        p = Project(id=new_id("prj"), org_id=org_id, name=name)
        self.s.add(p)
        try:
            await self.s.commit()
        except IntegrityError:
            await self.s.rollback()
            raise Conflict(f"a project named '{name}' already exists") from None
        await self.s.refresh(p)
        return p

    async def require(self, project_id: str) -> Project:
        p = await self.s.get(Project, project_id)
        if p is None:
            raise NotFound(f"project '{project_id}' not found")
        return p


class PolicyRepo:
    """An organisation's workflow policies (FR-X-19)."""

    FIELDS = ("name", "description", "scope", "match", "require", "severity", "environments", "enabled")

    def __init__(self, s: AsyncSession):
        self.s = s

    async def list(self, org_id: str) -> list[OrgPolicy]:
        q = select(OrgPolicy).where(OrgPolicy.org_id == org_id).order_by(OrgPolicy.name)
        return list((await self.s.scalars(q)).all())

    async def of_project(self, project_id: str) -> list[OrgPolicy]:
        """The policies that govern a project's workflows: its organisation's."""
        q = select(OrgPolicy).join(Project, Project.org_id == OrgPolicy.org_id).where(Project.id == project_id)
        return list((await self.s.scalars(q.order_by(OrgPolicy.name))).all())

    async def require(self, org_id: str, policy_id: str) -> OrgPolicy:
        p = await self.s.get(OrgPolicy, policy_id)
        if p is None or p.org_id != org_id:
            raise NotFound(f"policy '{policy_id}' not found")
        return p

    async def save(
        self, org_id: str, data: dict[str, Any], policy_id: str | None = None, created_by: str | None = None
    ) -> OrgPolicy:
        """Create a policy, or replace `policy_id`'s fields (409 when another policy has the name)."""
        if policy_id is None:
            p = OrgPolicy(id=new_id("pol"), org_id=org_id, created_by=created_by)
            self.s.add(p)
        else:
            p = await self.require(org_id, policy_id)
        for k in self.FIELDS:
            setattr(p, k, data[k])
        try:
            await self.s.commit()
        except IntegrityError:
            await self.s.rollback()
            raise Conflict(f"a policy named '{data['name']}' already exists") from None
        await self.s.refresh(p)
        return p

    async def delete(self, org_id: str, policy_id: str) -> None:
        await self.s.delete(await self.require(org_id, policy_id))
        await self.s.commit()


class WorkflowRepo:
    def __init__(self, s: AsyncSession):
        self.s = s

    async def require(self, wf_id: str) -> WorkflowRow:
        row = await self.s.get(WorkflowRow, wf_id)
        if row is None:
            raise NotFound(f"workflow '{wf_id}' not found")
        return row

    async def list(self, project_id: str, limit: int, cursor: str | None) -> list[WorkflowRow]:
        q = select(WorkflowRow).where(WorkflowRow.project_id == project_id)
        if cursor:
            q = q.where(WorkflowRow.id < cursor)
        q = q.order_by(WorkflowRow.id.desc()).limit(limit)
        return list((await self.s.scalars(q)).all())

    async def create(self, row: WorkflowRow) -> WorkflowRow:
        self.s.add(row)
        await self.s.commit()
        await self.s.refresh(row)
        return row

    async def save_ir(self, wf_id: str, ir: dict[str, Any], ir_hash: str, name: str, expected_version: int) -> int:
        """Optimistic locking: only the writer that saw `expected_version` wins (HTTP 409 otherwise)."""
        result = await self.s.execute(
            update(WorkflowRow)
            .where(WorkflowRow.id == wf_id, WorkflowRow.version == expected_version)
            .values(draft_ir=ir, ir_hash=ir_hash, name=name, version=WorkflowRow.version + 1)
            .returning(WorkflowRow.version)
        )
        new_version = result.scalar_one_or_none()
        if new_version is None:
            await self.s.rollback()
            await self.require(wf_id)  # 404 if it vanished
            raise Conflict("workflow was modified by someone else; reload and retry", expected_version=expected_version)
        await self.s.commit()
        return new_version

    async def save_layout(self, wf_id: str, layout: dict[str, Any]) -> None:
        await self.s.execute(update(WorkflowRow).where(WorkflowRow.id == wf_id).values(layout=layout))
        await self.s.commit()

    async def delete(self, wf_id: str) -> None:
        await self.require(wf_id)
        await self.s.execute(delete(WorkflowRow).where(WorkflowRow.id == wf_id))
        await self.s.commit()


async def delete_thread_rows(session: AsyncSession, thread_id: str) -> None:
    """A conversation's control rows: its decisions, runs and the thread itself. Explicit, because SQLite (the
    local edition) does not enforce the ON DELETE CASCADE the schema declares."""
    await session.execute(delete(InterruptTask).where(InterruptTask.thread_id == thread_id))
    await session.execute(delete(Run).where(Run.thread_id == thread_id))
    await session.execute(delete(Thread).where(Thread.id == thread_id))
