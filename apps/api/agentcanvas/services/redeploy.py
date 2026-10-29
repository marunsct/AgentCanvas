"""Safe redeploy (FR-X-10).

**Drain.** On shutdown (SIGTERM) an instance stops taking runs (`/readyz` turns 503, new runs get 503 with
`Retry-After`), gives running runs `AGENTCANVAS_DRAIN_GRACE_S` to finish, then stops the rest at their last
checkpoint: the run ends `interrupted` with code `redeploy` and `Run.handover` set.

**Hand-over.** Every instance runs `HandoverJob`: it claims a handed-over run (one conditional UPDATE, so exactly
one instance wins) and continues its thread from the checkpoint, on the version the run started on.

**Lost instances.** An instance killed without draining (OOM, node failure) cannot hand its runs over. Every
instance refreshes `Run.heartbeat_at` of its running runs on each tick; a run still `running` whose heartbeat is
older than `AGENTCANVAS_ORPHAN_AFTER_S` lost its instance, so the sweep marks it `interrupted` with `handover` set
and the hand-over continues it from its last checkpoint like a drained one.

**New versions.** A conversation of a deployment continues on the deployment's current version only when that
version can resume it (`resume_compatible`: the same state channels and pause points); otherwise it finishes on
the version it started on, and new conversations get the new version.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.adapters.spi import supports
from agentcanvas.db.models import Run, Thread, WorkflowVersion
from agentcanvas.errors import Conflict, Unavailable
from agentcanvas.ir.contract import contract
from agentcanvas.ir.models import Workflow
from agentcanvas.log import get_logger

log = get_logger(__name__)

BATCH = 100
ORPHANED = "its server stopped unexpectedly; it continues automatically"
NO_STATE = "its server stopped, and this framework keeps no saved state to continue from; start the run again"
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
        .order_by(Run.id.desc())  # ids are time-ordered; `started_at` has second resolution on SQLite (R-11)
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

    def __init__(self, db: Any, runner: Any, target: Target, interval_s: float = 10.0, orphan_after_s: float = 60.0):
        self.db, self.runner, self.target, self.interval_s = db, runner, target, interval_s
        self.orphan_after_s = orphan_after_s
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

    async def _fail(self, run_id: str, thread_id: str) -> None:
        async with self.db.sessionmaker() as s:
            await s.execute(update(Run).where(Run.id == run_id).values(status="error", error=NO_STATE))
            await s.execute(update(Thread).where(Thread.id == thread_id).values(status="error"))
            await s.commit()
        log.warning("handover.cannot_continue", run_id=run_id, thread_id=thread_id)

    async def sweep(self, now: datetime | None = None) -> list[str]:
        """Hand over the runs whose instance stopped without draining; returns their ids."""
        cutoff = (now or datetime.now(UTC)) - timedelta(seconds=self.orphan_after_s)
        stale = (Run.status == "running", func.coalesce(Run.heartbeat_at, Run.started_at) < cutoff)
        mine = set(self.runner.active())
        swept: list[str] = []
        async with self.db.sessionmaker() as s:
            rows = (await s.execute(select(Run.id, Run.thread_id).where(*stale).limit(BATCH))).all()
            for run_id, thread_id in rows:
                if run_id in mine:  # alive here; its heartbeat is just late
                    continue
                res = await s.execute(
                    update(Run)
                    .where(Run.id == run_id, *stale)  # exactly one instance sweeps it
                    .values(status="interrupted", error=ORPHANED, ended_at=datetime.now(UTC), handover=True)
                )
                if res.rowcount == 1:  # type: ignore[attr-defined]
                    await s.execute(update(Thread).where(Thread.id == thread_id).values(status="interrupted"))
                    swept.append(run_id)
            await s.commit()
        if swept:
            log.warning("handover.orphaned_runs", runs=swept)
        return swept

    async def tick(self) -> list[str]:
        """Keep this instance's runs alive, sweep orphaned runs and continue the handed-over runs' threads;
        returns the ids of the runs started."""
        await self.runner.heartbeat()
        if not self.runner.draining:
            await self.sweep()
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
                if not supports(self.runner.adapter_for(wf), "thread_state"):
                    # no saved state to continue from: starting again would rerun the flow with no input and
                    # repeat its side effects, so the run ends with an error the person can act on
                    await self._fail(run_id, thread_id)
                    continue
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
