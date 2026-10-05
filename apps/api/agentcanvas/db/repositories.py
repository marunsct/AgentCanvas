"""Repositories: the only code that issues SQL. No business logic here (build-guide/05 §1)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.db.models import (
    AppUser,
    Deployment,
    InterruptTask,
    Membership,
    Organization,
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

    async def provision(self, subject: str, email: str, name: str | None) -> AppUser:
        """The user of an OIDC subject, created on first sign-in; email and name follow the latest token."""
        user = await self.by_subject(subject)
        if user is None:
            user = AppUser(id=new_id("usr"), oidc_subject=subject, email=email, display_name=name)
            self.s.add(user)
            try:
                await self.s.commit()
            except IntegrityError:  # a parallel first request created it
                await self.s.rollback()
                user = await self.by_subject(subject)
                assert user is not None
        elif (email and user.email != email) or (name and user.display_name != name):
            user.email, user.display_name = email or user.email, name or user.display_name
            await self.s.commit()
        return user

    async def memberships(self, user_id: str) -> dict[str, str]:
        rows = await self.s.execute(select(Membership.org_id, Membership.role).where(Membership.user_id == user_id))
        return {org: role for org, role in rows.all()}

    async def bootstrap_admin(self, user_id: str, org_id: str) -> dict[str, str]:
        if await self.s.get(Organization, org_id) is None:
            self.s.add(Organization(id=org_id, name="Default organisation"))
            await self.s.flush()
        self.s.add(Membership(org_id=org_id, user_id=user_id, role="admin"))
        try:
            await self.s.commit()
        except IntegrityError:
            await self.s.rollback()
        return await self.memberships(user_id)


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
