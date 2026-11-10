"""Weekly run digests (F-11) on the Runs page: what a project's runs did last week, and this week so far."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.deps import get_session
from agentcanvas.db.models import RunDigest
from agentcanvas.services import digest

router = APIRouter(tags=["runs"])


class FailureGroupOut(BaseModel):
    reason: str = Field(description="the error, with what differs between runs (numbers, quoted values) taken out")
    runs: int
    workflows: list[str] = Field(description="the workflows it happened in (at most 5)")
    example: dict[str, str] = Field(description="the newest such run: run_id, thread_id, workflow_id")


class WorkflowLine(BaseModel):
    id: str
    name: str
    runs: int
    failed: int
    cost_usd: float


class FlaggedRun(BaseModel):
    run_id: str
    thread_id: str
    workflow_id: str
    workflow_name: str
    flags: int = Field(description="failing scores (evaluators, thumbs-down) and review queue items")
    why: list[str]


class DigestSummary(BaseModel):
    runs: int
    failed: int
    failure_rate: float | None
    cost_usd: float
    tokens: int
    latency_p50_ms: int | None
    latency_p95_ms: int | None
    failure_groups: list[FailureGroupOut]
    workflows: list[WorkflowLine] = Field(description="the busiest workflows (at most 5)")
    flagged: list[FlaggedRun] = Field(description="the runs flagged most (at most 5)")


class RunDigestOut(BaseModel):
    week_start: datetime = Field(description="Monday 00:00 UTC")
    week_end: datetime
    current: bool = Field(description="this week so far, computed now and not kept")
    created_at: datetime | None = None
    summary: DigestSummary


def _out(start: datetime, summary: dict[str, Any], created: datetime | None, current: bool) -> RunDigestOut:
    return RunDigestOut(
        week_start=start, week_end=start + digest.WEEK, current=current, created_at=created,
        summary=DigestSummary.model_validate(summary),
    )  # fmt: skip


@router.get("/projects/{project_id}/run-digests", response_model=list[RunDigestOut])
async def list_digests(
    project_id: str, limit: int = Query(8, ge=1, le=52), session: AsyncSession = Depends(get_session)
) -> list[RunDigestOut]:
    """The project's weekly run digests, newest first. One is made each Monday for the week before, when the
    project had runs in it."""
    q = select(RunDigest).where(RunDigest.project_id == project_id).order_by(RunDigest.week_start.desc()).limit(limit)
    return [_out(d.week_start, d.summary, d.created_at, False) for d in (await session.scalars(q)).all()]


@router.get("/projects/{project_id}/run-digests/current", response_model=RunDigestOut)
async def current_digest(
    project_id: str, request: Request, session: AsyncSession = Depends(get_session)
) -> RunDigestOut:
    """This week so far (since Monday 00:00 UTC), computed now and not kept."""
    now = datetime.now(UTC)
    start = digest.week_of(now)
    secrets = request.app.state.projects.secrets.get(project_id, {})
    return _out(start, await digest.compute(session, project_id, start, now, secrets), None, True)
