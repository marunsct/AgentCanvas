"""The kill switch (F-5): stop every run in flight in an organisation, or of one deployment or workflow.

Runs live on the instance that started them, so stopping is a mark in the database (`Run.stop_requested`): this
instance stops its marked runs at once; every other instance stops its own at its next heartbeat (the hand-over
interval, 10 s by default). A marked run whose instance is gone is cancelled by the hand-over sweep instead of
continuing elsewhere, and a run already waiting to be handed over is cancelled here. New runs are not refused: to
keep a deployment from taking more, stop it on its page as well.

A run paused at a Wait step (F-10) runs nowhere either: its wait is cancelled here, and the run with it, so no timer
or event continues it later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.db.models import InterruptTask, Project, Run, Thread, WorkflowRow
from agentcanvas.runtime.runner import STOPPED_BY_ADMIN
from agentcanvas.services.timers import end_waits

MAX_RUNS = 10_000  # runs marked by one press; a press again marks the rest


@dataclass
class Stopped:
    running: list[str]  # marked: their instances stop them (this one at once)
    handed_over: list[str]  # were waiting for another instance to continue them: cancelled now
    waiting: list[str] = field(default_factory=list)  # were paused at a Wait step: cancelled now


async def stop_runs(
    session: AsyncSession,
    runner: Any,
    org_id: str,
    *,
    deployment_id: str | None = None,
    workflow_id: str | None = None,
) -> Stopped:
    """Mark the organisation's runs in flight (narrowed to a deployment or a workflow) to stop."""
    q = (
        select(Run.id, Run.thread_id, Run.status)
        .join(Thread, Thread.id == Run.thread_id)
        .join(WorkflowRow, WorkflowRow.id == Thread.workflow_id)
        .join(Project, Project.id == WorkflowRow.project_id)
        .where(Project.org_id == org_id, or_(Run.status == "running", Run.handover.is_(True)))
        .limit(MAX_RUNS)
    )
    if deployment_id is not None:
        q = q.where(Thread.deployment_id == deployment_id)
    if workflow_id is not None:
        q = q.where(Thread.workflow_id == workflow_id)
    rows = (await session.execute(q)).all()
    running = [r for r, _, status in rows if status == "running"]
    waiting = [(r, t) for r, t, status in rows if status != "running"]
    if running:
        await session.execute(
            update(Run).where(Run.id.in_(running)).values(stop_requested=True, stop_reason=STOPPED_BY_ADMIN)
        )
    handed: list[str] = []
    for run_id, thread_id in waiting:  # not running anywhere: nothing else would stop it
        res = await session.execute(
            update(Run)
            .where(Run.id == run_id, Run.handover.is_(True), Run.status != "running")
            .values(status="cancelled", error=STOPPED_BY_ADMIN, handover=False, stop_requested=True,
                    stop_reason=STOPPED_BY_ADMIN, ended_at=datetime.now(UTC))
        )  # fmt: skip
        if res.rowcount == 1:  # type: ignore[attr-defined]
            await session.execute(update(Thread).where(Thread.id == thread_id).values(status="idle"))
            handed.append(run_id)
    waits = (
        select(InterruptTask.id)
        .join(Thread, Thread.id == InterruptTask.thread_id)
        .join(WorkflowRow, WorkflowRow.id == Thread.workflow_id)
        .join(Project, Project.id == WorkflowRow.project_id)
        .where(Project.org_id == org_id, InterruptTask.status == "pending", InterruptTask.kind == "timer")
        .limit(MAX_RUNS)
    )
    if deployment_id is not None:
        waits = waits.where(Thread.deployment_id == deployment_id)
    if workflow_id is not None:
        waits = waits.where(Thread.workflow_id == workflow_id)
    waited = await end_waits(session, list((await session.scalars(waits)).all()), STOPPED_BY_ADMIN)
    await session.commit()
    for run_id in running:  # the ones running here stop now; the others at their instance's next heartbeat
        await runner.cancel(run_id, STOPPED_BY_ADMIN)
    return Stopped(running, handed, waited)
