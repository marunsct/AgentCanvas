"""Safe redeploy (FR-X-10).

**Drain.** On shutdown (SIGTERM) an instance stops taking runs (`/readyz` turns 503, new runs get 503 with
`Retry-After`), gives running runs `AGENTCANVAS_DRAIN_GRACE_S` to finish, then stops the rest at their last
checkpoint: the run ends `interrupted` with code `redeploy` and `Run.handover` set.

**Hand-over.** Every instance runs `HandoverJob`: it claims a handed-over run (one conditional UPDATE, so exactly
one instance wins) and continues its thread from the checkpoint, on the version the run started on. A run that
was still waiting in the run queue has no checkpoint yet: it starts again with what it was sent (`Run.pending`).

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

from agentcanvas import observability
from agentcanvas.adapters.spi import supports
from agentcanvas.db.models import InterruptTask, Run, Thread, WorkflowVersion
from agentcanvas.errors import (
    Conflict,
    DomainError,
    Forbidden,
    InvalidInput,
    NotFound,
    PreconditionRequired,
    Unauthorized,
    Unavailable,
    WorkflowHasErrors,
)
from agentcanvas.ir.contract import contract
from agentcanvas.ir.models import Workflow
from agentcanvas.log import get_logger
from agentcanvas.runtime.admission import NAMES
from agentcanvas.runtime.runner import STOPPED_BY_ADMIN, TEST_NOT_CONTINUED
from agentcanvas.runtime.types import ResumeSpec

log = get_logger(__name__)

BATCH = 100
ORPHANED = "its server stopped unexpectedly; it continues automatically"
NO_STATE = "its server stopped, and this framework keeps no saved state to continue from; start the run again"
NO_INPUT = "its server stopped while it waited for a run slot, and its input was not kept; send it again"
HANDOVER_ATTEMPTS = 5  # a hand-over that fails for a passing reason is tried on this many ticks, then the run ends
# failures that trying again cannot fix: the workflow, deployment or thread is gone or broken, or a rule refuses it
LASTING = (NotFound, WorkflowHasErrors, InvalidInput, Forbidden, Unauthorized, PreconditionRequired)
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
        observability.job_started("handover")
        self._task = asyncio.create_task(self._loop(), name="handover")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _loop(self) -> None:
        while True:
            try:
                with observability.job_round("handover"):
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

    async def _fail(self, run_id: str, thread_id: str, error: str = NO_STATE, status: str = "error") -> None:
        async with self.db.sessionmaker() as s:
            await s.execute(update(Run).where(Run.id == run_id).values(status=status, error=error))
            thread_status = "idle" if status == "cancelled" else "error"
            await s.execute(update(Thread).where(Thread.id == thread_id).values(status=thread_status))
            await s.commit()
        log.warning("handover.cannot_continue", run_id=run_id, thread_id=thread_id)

    async def _failed(self, run_id: str, thread_id: str, exc: Exception) -> None:
        """Continuing the run failed: a passing failure leaves it to a later tick (a few times), any other ends it
        with the reason, so it never says it continues automatically when it will not."""
        if not isinstance(exc, LASTING):
            async with self.db.sessionmaker() as s:
                res = await s.execute(
                    update(Run)
                    .where(Run.id == run_id, Run.handover_attempts < HANDOVER_ATTEMPTS - 1)
                    .values(handover=True, handover_attempts=Run.handover_attempts + 1)
                )
                await s.commit()
            if res.rowcount == 1:  # type: ignore[attr-defined]
                return
        # a database or network error's text can hold the run's data: it stays in the server log
        reason = str(exc) if isinstance(exc, DomainError) else f"{type(exc).__name__} (see the server log)"
        await self._fail(
            run_id, thread_id, f"its server stopped and it could not continue: {reason}; start the run again"
        )

    async def _again(self, pending: dict[str, Any], thread_id: str) -> dict[str, Any]:
        """Runner.start arguments that start a waiting run again as it was asked. The Inbox items it answered go
        back to waiting for that answer (a server that died did not put them back), so it can answer them again."""
        resume = pending.get("resume")
        if resume is not None and pending.get("decided"):
            async with self.db.sessionmaker() as s:
                await s.execute(
                    update(InterruptTask)
                    .where(InterruptTask.id.in_(pending["decided"]), InterruptTask.thread_id == thread_id)
                    .where(InterruptTask.status == "decided")
                    .values(status="pending", decision=None, decided_by=None, decided_at=None)
                )
                await s.commit()
        keep = ("input", "mode", "breakpoints_before", "breakpoints_after", "durability", "checkpoint_id", "pins",
                "mocks", "dry_run", "user_id", "service_connection")  # fmt: skip
        out = {k: pending[k] for k in keep if k in pending}
        out["resume"] = ResumeSpec(value=resume["value"], interrupt_id=resume.get("interrupt_id")) if resume else None
        out["project_files"] = True  # its files were checked when it was first started
        return out

    async def sweep(self, now: datetime | None = None) -> list[str]:
        """Hand over the runs whose instance stopped without draining; returns their ids."""
        cutoff = (now or datetime.now(UTC)) - timedelta(seconds=self.orphan_after_s)
        stale = (Run.status == "running", func.coalesce(Run.heartbeat_at, Run.started_at) < cutoff)
        mine = set(self.runner.active())
        swept: list[str] = []
        async with self.db.sessionmaker() as s:
            q = (
                select(Run.id, Run.thread_id, Run.stop_requested, Run.stop_reason, Run.origin)
                .where(*stale)
                .limit(BATCH)
            )
            rows = (await s.execute(q)).all()
            for run_id, thread_id, stop, reason, origin in rows:
                if run_id in mine:  # alive here; its heartbeat is just late
                    continue
                # a run someone asked to stop (Stop, the F-5 kill switch) is cancelled, not continued elsewhere
                if stop:
                    values = {"status": "cancelled", "error": reason or STOPPED_BY_ADMIN, "handover": False}
                elif origin == "test":  # its experiment runs the example again, and a test case's caller is gone
                    values = {"status": "cancelled", "error": TEST_NOT_CONTINUED, "handover": False}
                else:
                    values = {"status": "interrupted", "error": ORPHANED, "handover": True}
                res = await s.execute(
                    update(Run)
                    .where(Run.id == run_id, *stale)  # exactly one instance sweeps it
                    .values(ended_at=datetime.now(UTC), **values)
                )
                if res.rowcount == 1:  # type: ignore[attr-defined]
                    thread_status = "interrupted" if values["handover"] else "idle"
                    await s.execute(update(Thread).where(Thread.id == thread_id).values(status=thread_status))
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
                    select(
                        Run.id,
                        Run.thread_id,
                        Run.workflow_version_id,
                        Run.priority,
                        Run.queued,
                        Run.pending,
                        func.coalesce(Run.queue_key, Run.id),
                        Run.origin,
                        Run.event_depth,
                    )
                    .where(Run.handover.is_(True), Run.stop_requested.is_(False))
                    .limit(BATCH)
                )
            ).all()
        for run_id, thread_id, version_id, priority, queued, pending, queue_key, origin, depth in rows:
            if self.runner.draining or not await self._mark(run_id, False):  # another instance claimed it
                continue
            if origin == "test":  # handed over by a server that still did that: see `sweep`
                await self._fail(run_id, thread_id, TEST_NOT_CONTINUED, "cancelled")
                continue
            try:
                async with self.db.sessionmaker() as s:
                    wf, kw = await self.target(s, thread_id, version_id)
                if queued:  # it never took a slot: no checkpoint holds its input, its row does
                    if pending is None:  # handed over by a server that did not keep it
                        await self._fail(run_id, thread_id, NO_INPUT)
                        continue
                    kw |= await self._again(pending, thread_id)
                elif not supports(self.runner.adapter_for(wf), "thread_state"):
                    # no saved state to continue from: starting again would rerun the flow with no input and
                    # repeat its side effects, so the run ends with an error the person can act on
                    await self._fail(run_id, thread_id)
                    continue
                else:
                    kw["input"] = None  # it continues from its last checkpoint
                h = await self.runner.start(  # in the queue, it keeps the place its run had
                    wf, thread_id=thread_id, origin=origin or "user", priority=NAMES.get(priority),
                    queue_key=queue_key, event_depth=depth, handed_over=True, **kw,
                )  # fmt: skip
            except Conflict:  # someone already continued the conversation
                continue
            except Unavailable:  # this instance began draining meanwhile: leave it to the next one
                await self._mark(run_id, True)
                continue
            except Exception as exc:
                log.exception("handover.failed", run_id=run_id, thread_id=thread_id)
                await self._failed(run_id, thread_id, exc)
                continue
            log.info("handover.resumed", run_id=run_id, thread_id=thread_id, new_run_id=h.run_id)
            started.append(h.run_id)
        return started
