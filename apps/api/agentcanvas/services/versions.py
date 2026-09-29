"""Workflow versions (M6-T1): immutable snapshots of the IR, layout and workspace commit, with semver and diffs."""

from __future__ import annotations

from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.db.models import WorkflowRow, WorkflowVersion
from agentcanvas.db.repositories import WorkflowRepo
from agentcanvas.errors import Conflict, NotFound
from agentcanvas.ids import new_id
from agentcanvas.ir.diff import WorkflowDiff, diff_workflows
from agentcanvas.ir.models import Workflow

Bump = Literal["major", "minor", "patch"]
FIRST = "1.0.0"


def next_semver(latest: str | None, bump: Bump) -> str:
    if latest is None:
        return FIRST
    major, minor, patch = (int(x) for x in latest.split("."))
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


class VersionService:
    def __init__(self, session: AsyncSession):
        self.s = session
        self.workflows = WorkflowRepo(session)

    async def list(self, wf_id: str) -> list[WorkflowVersion]:
        await self.workflows.require(wf_id)
        q = select(WorkflowVersion).where(WorkflowVersion.workflow_id == wf_id).order_by(WorkflowVersion.id.desc())
        return list((await self.s.scalars(q)).all())

    async def latest(self, wf_id: str) -> WorkflowVersion | None:
        q = (
            select(WorkflowVersion)
            .where(WorkflowVersion.workflow_id == wf_id)
            .order_by(WorkflowVersion.id.desc())
            .limit(1)
        )
        return await self.s.scalar(q)

    async def get(self, wf_id: str, ref: str) -> WorkflowVersion:
        """A version by id or semver (`1.2.0`, `v1.2.0`)."""
        q = select(WorkflowVersion).where(WorkflowVersion.workflow_id == wf_id)
        if ref.startswith("ver_"):
            q = q.where(WorkflowVersion.id == ref)
        else:
            q = q.where(WorkflowVersion.semver == ref.removeprefix("v"))
        row = await self.s.scalar(q)
        if row is None:
            raise NotFound(f"version '{ref}' of workflow '{wf_id}' not found")
        return row

    async def create(
        self,
        wf_id: str,
        *,
        message: str = "",
        bump: Bump = "minor",
        workspace_commit: str | None = None,
        created_by: str | None = None,
        reuse_unchanged: bool = False,
    ) -> tuple[WorkflowVersion, bool]:
        """Snapshot the draft. Returns (version, created); an unchanged draft is refused (or reused when asked)."""
        row = await self.workflows.require(wf_id)
        latest = await self.latest(wf_id)
        if latest is not None and latest.ir_hash == row.ir_hash and latest.workspace_commit == workspace_commit:
            if reuse_unchanged:
                return latest, False
            raise Conflict(f"nothing changed since v{latest.semver}", version_id=latest.id, semver=latest.semver)
        version = WorkflowVersion(
            id=new_id("ver"),
            workflow_id=wf_id,
            semver=next_semver(latest.semver if latest else None, bump),
            ir=row.draft_ir,
            layout=row.layout or {},
            ir_hash=row.ir_hash,
            workspace_commit=workspace_commit,
            message=message,
            created_by=created_by,
        )
        self.s.add(version)
        await self.s.commit()
        await self.s.refresh(version)
        return version, True

    async def workflow_at(self, wf_id: str, ref: str) -> Workflow:
        """The IR of a version, or of the draft for `ref == "draft"`."""
        if ref == "draft":
            row: WorkflowRow = await self.workflows.require(wf_id)
            return Workflow.model_validate(row.draft_ir)
        return Workflow.model_validate((await self.get(wf_id, ref)).ir)

    async def diff(self, wf_id: str, a: str, b: str) -> WorkflowDiff:
        return diff_workflows(await self.workflow_at(wf_id, a), await self.workflow_at(wf_id, b))
