"""Safe redeploy (FR-X-10).

**Drain.** On shutdown (SIGTERM) an instance stops taking runs (`/readyz` turns 503, new runs get 503 with
`Retry-After`), gives running runs `AGENTCANVAS_DRAIN_GRACE_S` to finish, then stops the rest at their last
checkpoint: the run ends `interrupted` with code `redeploy` and `Run.handover` set.

**Hand-over.** Every instance runs `HandoverJob`: it claims a handed-over run (one conditional UPDATE, so exactly
one instance wins) and continues its thread from the checkpoint, on the version the run started on.

**New versions.** A conversation of a deployment continues on the deployment's current version only when that
version can resume it (`resume_compatible`: the same state channels and pause points); otherwise it finishes on
the version it started on, and new conversations get the new version.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.db.models import Run, WorkflowVersion
from agentcanvas.errors import Conflict, Unavailable
from agentcanvas.ir.contract import contract
from agentcanvas.ir.models import Workflow
from agentcanvas.log import get_logger

log = get_logger(__name__)

BATCH = 100
# (session, thread_id, version_id of the handed-over run) -> (workflow, Runner.start kwargs)
Target = Callable[[AsyncSession, str, str | None], Awaitable[tuple[Workflow, dict[str, Any]]]]


def resume_compatible(old: Workflow, new: Workflow) -> list[str]:
    """Why `new` cannot continue a conversation `old` started; empty when it can. A checkpoint holds the state
    channels and, when paused, the step it waits on, so both must mean the same in the new version."""
    reasons: list[str] = []
    for c in old.state.channels:
        n = new.state.channel(c.name)
        if n is None:
            reasons.append(f"state field '{c.name}' was removed")
        elif n.type != c.type or n.reducer != c.reducer:
            reasons.append(f"state field '{c.name}' changed type or reducer")
    before, after = contract(old).pauses, contract(new).pauses
    for step, kind in before.items():
        if after.get(step) != kind:
            reasons.append(f"pause point '{step}' ({kind}) was removed or changed")
    return reasons


async def thread_version(session: AsyncSession, thread_id: str, current: WorkflowVersion) -> WorkflowVersion:
    """The version a deployment's conversation continues on: the deployment's `current` one, unless the
    conversation started on another version that `current` cannot resume (then that one)."""
    pinned_id = await session.scalar(
        select(Run.workflow_version_id)
        .where(Run.thread_id == thread_id, Run.workflow_version_id.is_not(None))
        .order_by(Run.started_at.desc())
        .limit(1)
    )
    if pinned_id is None or pinned_id == current.id:
        return current
    pinned = await session.get(WorkflowVersion, pinned_id)
    if pinned is None:
        return current
    reasons = resume_compatible(Workflow.model_validate(pinned.ir), Workflow.model_validate(current.ir))
    if not reasons:
        return current
    log.info("redeploy.thread_pinned", thread_id=thread_id, version=pinned.semver, reasons=reasons)
    return pinned


class HandoverJob:
    """Continues the runs a draining instance handed over (every instance runs it; claims are exclusive)."""

    def __init__(self, db: Any, runner: Any, target: Target, interval_s: float = 10.0):
        self.db, self.runner, self.target, self.interval_s = db, runner, target, interval_s
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="handover")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("handover.tick_failed")
            await asyncio.sleep(self.interval_s)

    async def _mark(self, run_id: str, value: bool) -> bool:
        async with self.db.sessionmaker() as s:
            res = await s.execute(
                update(Run).where(Run.id == run_id, Run.handover.is_(not value)).values(handover=value)
            )
            await s.commit()
        return res.rowcount == 1  # type: ignore[attr-defined]

    async def tick(self) -> list[str]:
        """Continue the handed-over runs' threads; returns the ids of the runs started."""
        started: list[str] = []
        async with self.db.sessionmaker() as s:
            rows = (
                await s.execute(
                    select(Run.id, Run.thread_id, Run.workflow_version_id).where(Run.handover.is_(True)).limit(BATCH)
                )
            ).all()
        for run_id, thread_id, version_id in rows:
            if self.runner.draining or not await self._mark(run_id, False):  # another instance claimed it
                continue
            try:
                async with self.db.sessionmaker() as s:
                    wf, kw = await self.target(s, thread_id, version_id)
                h = await self.runner.start(wf, thread_id=thread_id, input=None, origin="handover", **kw)
            except Conflict:  # someone already continued the conversation
                continue
            except Unavailable:  # this instance began draining meanwhile: leave it to the next one
                await self._mark(run_id, True)
                continue
            except Exception:
                log.exception("handover.failed", run_id=run_id, thread_id=thread_id)
                continue
            log.info("handover.resumed", run_id=run_id, thread_id=thread_id, new_run_id=h.run_id)
            started.append(h.run_id)
        return started
