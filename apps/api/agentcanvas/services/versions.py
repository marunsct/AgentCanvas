"""Workflow versions (M6-T1): immutable snapshots of the IR, layout and workspace commit, with semver and diffs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.db.models import AppUser, CopilotConversation, WorkflowRow, WorkflowVersion
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
        """Snapshot the draft. Returns (version, created); an unchanged draft is refused (or reused when asked).
        Two snapshots at once both compute the next semver: the loser reads the winner's and tries once more."""
        for attempt in range(2):
            row = await self.workflows.require(wf_id)
            latest = await self.latest(wf_id)
            if latest is not None and latest.ir_hash == row.ir_hash and latest.workspace_commit == workspace_commit:
                if reuse_unchanged:
                    return latest, False
                raise Conflict(f"nothing changed since v{latest.semver}", version_id=latest.id, semver=latest.semver)
            now = datetime.now(UTC)  # to the microsecond (SQLite's own clock is to the second): changes credited once
            since = _utc(latest.created_at) if latest else None
            version = WorkflowVersion(
                id=new_id("ver"),
                workflow_id=wf_id,
                semver=next_semver(latest.semver if latest else None, bump),
                ir=row.draft_ir,
                layout=row.layout or {},
                ir_hash=row.ir_hash,
                workspace_commit=workspace_commit,
                message=message,
                copilot_changes=await self._copilot_changes(wf_id, since, now),
                created_by=created_by,
                created_at=now,
            )
            self.s.add(version)
            try:
                await self.s.commit()
            except IntegrityError:  # another snapshot took this semver first
                await self.s.rollback()
                if attempt:
                    raise Conflict("another version was saved at the same moment; try again") from None
                continue
            await self.s.refresh(version)
            return version, True
        raise AssertionError("unreachable")

    async def _copilot_changes(self, wf_id: str, since: datetime | None, until: datetime) -> list[dict[str, Any]]:
        """The Copilot proposals accepted after the version before (FR-COP-07), oldest first, with who accepted them;
        kept on the version, since the conversations that held them may be archived or expire. Only the
        conversations changed since then are read (deciding a proposal changes its conversation), and only their
        proposals, not their messages."""
        q = select(CopilotConversation.user_id, CopilotConversation.proposals).where(
            CopilotConversation.workflow_id == wf_id
        )
        if since is not None:  # with an hour's margin for the database's clock (`updated_at` is its time)
            q = q.where(CopilotConversation.updated_at >= since - timedelta(hours=1))
        found: list[dict[str, Any]] = []
        for user_id, proposals in (await self.s.execute(q)).all():
            for p in proposals or []:
                at = _parse(p.get("decided_at"))
                if p.get("status") != "accepted" or at is None or at > until or (since is not None and at <= since):
                    continue
                kept = p.get("accepted_commands")
                found.append({
                    "summary": p.get("summary") or "Changes to the workflow",
                    "accepted_by": p.get("decided_by") or user_id,
                    "accepted_at": p["decided_at"],
                    "changes": len(kept) if kept is not None else len(p.get("commands") or []),
                })  # fmt: skip
        if found:
            ids = {c["accepted_by"] for c in found}
            emails = dict((await self.s.execute(select(AppUser.id, AppUser.email).where(AppUser.id.in_(ids)))).all())
            for c in found:
                c["accepted_by_email"] = emails.get(c["accepted_by"])
        return sorted(found, key=lambda c: c["accepted_at"])

    async def workflow_at(self, wf_id: str, ref: str) -> Workflow:
        """The IR of a version, or of the draft for `ref == "draft"`."""
        if ref == "draft":
            row: WorkflowRow = await self.workflows.require(wf_id)
            return Workflow.model_validate(row.draft_ir)
        return Workflow.model_validate((await self.get(wf_id, ref)).ir)

    async def diff(self, wf_id: str, a: str, b: str) -> WorkflowDiff:
        return diff_workflows(await self.workflow_at(wf_id, a), await self.workflow_at(wf_id, b))


def _utc(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=UTC)  # SQLite gives back naive UTC


def _parse(text: Any) -> datetime | None:
    try:
        return _utc(datetime.fromisoformat(text)) if isinstance(text, str) else None
    except ValueError:
        return None
