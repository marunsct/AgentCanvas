"""Repositories: the only code that issues SQL. No business logic here (build-guide/05 §1)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.db.models import AppUser, Membership, Organization, Project, WorkflowRow
from agentcanvas.errors import Conflict, NotFound

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


class ProjectRepo:
    def __init__(self, s: AsyncSession):
        self.s = s

    async def list(self, org_id: str) -> list[Project]:
        return list((await self.s.scalars(select(Project).where(Project.org_id == org_id).order_by(Project.id))).all())

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
