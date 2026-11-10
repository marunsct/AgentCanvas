"""Wait steps (F-10): the job that continues runs paused at a `core.wait` block when they are due, and the events
that end one sooner.

A Wait step pauses its run as an Inbox item of kind `timer` with `due_at` (an event wait's time limit), and holds no
thread, server or connection while it waits. Every API replica runs this job beside the cron scheduler; each tick
reads the items that are due and claims each one by moving its `claimed_until` lease forward in one conditional
UPDATE, so only the replica whose update matched starts the run that continues it (with `ResumeSpec(wake=True)`,
the only answer the runner accepts for a Wait step). A start that fails is tried again when the lease runs out: a
conversation busy with another run soon, a workflow with errors or an organisation at its spending cap later
(`RETRY_S`); one whose workflow or deployment is gone ends, and its run is cancelled.

An event delivered to the project (`EventService.dispatch`) ends the event waits for its type in that project whose
key, if they have one, equals the event data's `key`; their runs continue with the event. The run continues as the
person whose run paused while they are still a member of its organisation, else as nobody (a deployment's runs use
its service connection anyway).

A wait that cannot continue ends with `end_waits`: its items are marked `cancelled` and the run is cancelled.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas import observability, waits
from agentcanvas.db.models import InterruptTask, Membership, Run, Thread, WorkflowRow
from agentcanvas.db.repositories import ScopeRepo
from agentcanvas.errors import Conflict, NotFound
from agentcanvas.ir.models import Workflow
from agentcanvas.log import get_logger
from agentcanvas.runtime.types import ResumeSpec

log = get_logger(__name__)
BATCH = 100  # due items a tick looks at; the rest wait for the next tick
EVENT_WAKES = 200  # waits one event ends at most (the rest wait for the next such event)
LEASE_S = 120  # how long a server holds an item while it starts its run
BUSY_S = 15  # the conversation runs something else right now: try again soon
RETRY_S = 300  # a start refused for a reason that takes a person to fix (errors, a spending cap): try again later
Target = Callable[[AsyncSession, str, str | None], Awaitable[tuple[Workflow, dict[str, Any]]]]


@dataclass(frozen=True)
class Waiting:
    id: str
    thread_id: str
    run_id: str
    requested_by: str | None
    wait_event: str | None


def _columns() -> tuple[Any, ...]:
    return (
        InterruptTask.id,
        InterruptTask.thread_id,
        InterruptTask.run_id,
        InterruptTask.requested_by,
        InterruptTask.wait_event,
    )


async def end_waits(s: AsyncSession, item_ids: list[str], error: str, now: datetime | None = None) -> list[str]:
    """Cancel these waiting Wait steps, and their runs when nothing else of theirs still waits; returns the run ids
    cancelled. The caller commits."""
    now = now or datetime.now(UTC)
    if not item_ids:
        return []
    rows = (
        await s.execute(
            select(InterruptTask.id, InterruptTask.thread_id, InterruptTask.run_id).where(
                InterruptTask.id.in_(item_ids), InterruptTask.status == "pending", InterruptTask.kind == "timer"
            )
        )
    ).all()
    if not rows:
        return []
    await s.execute(
        update(InterruptTask)  # a wake that won the race keeps its item
        .where(InterruptTask.id.in_([r.id for r in rows]), InterruptTask.status == "pending")
        .values(status="cancelled", decided_at=now)
    )
    cancelled: list[str] = []
    for thread_id, run_ids in _by_thread(rows).items():
        waiting = await s.scalar(
            select(InterruptTask.id)
            .where(InterruptTask.thread_id == thread_id, InterruptTask.status == "pending")
            .limit(1)
        )
        if waiting:  # an approval of the same run still waits for a person: the run goes on waiting for it
            continue
        res = await s.execute(
            update(Run)
            .where(Run.id.in_(run_ids), Run.status == "interrupted")
            .values(status="cancelled", error=error, ended_at=now)
        )
        if res.rowcount:  # type: ignore[attr-defined]
            cancelled.extend(sorted(run_ids))
        waits_here = (Thread.id == thread_id, Thread.status == "interrupted")
        await s.execute(update(Thread).where(*waits_here).values(status="idle"))
    return cancelled


def _by_thread(rows: Any) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for r in rows:
        out.setdefault(r.thread_id, set()).add(r.run_id)
    return out


class TimerJob:
    """Continues the runs whose Wait step is due, on one replica each (see the module docstring)."""

    def __init__(self, db: Any, runner: Any, target: Target, tick_s: float = 5.0):
        self.db, self.runner, self.target, self.tick_s = db, runner, target, tick_s
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        observability.job_started("timers")
        self._task = asyncio.create_task(self._loop(), name="timers")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _loop(self) -> None:
        while True:
            try:
                with observability.job_round("timers"):
                    await self.tick(datetime.now(UTC))
            except Exception:
                log.exception("timers.tick_failed")
            await asyncio.sleep(self.tick_s)

    async def tick(self, now: datetime) -> list[str]:
        """Continue the runs whose Wait step is due at `now`; returns the run ids started."""
        if getattr(self.runner, "draining", False):
            return []  # a restarting server starts nothing: another one takes them
        free = or_(InterruptTask.claimed_until.is_(None), InterruptTask.claimed_until < now)
        async with self.db.sessionmaker() as s:
            q = (
                select(*_columns())
                .where(InterruptTask.status == "pending", InterruptTask.due_at.is_not(None))
                .where(InterruptTask.due_at <= now, InterruptTask.kind == "timer", free)
                .order_by(InterruptTask.due_at, InterruptTask.id)
                .limit(BATCH)
            )
            rows = [Waiting(*r) for r in (await s.execute(q)).all()]
        started: list[str] = []
        for row in rows:
            if row.thread_id in self.runner.active_by_thread:
                continue  # the conversation runs here right now: the next tick
            if not await self._claim(row.id, now, LEASE_S):
                continue  # another replica has it
            value = waits.woken(timed_out=row.wait_event is not None, at=now)
            run_id = await self._wake(row, value, now)
            if run_id is not None:
                started.append(run_id)
        return started

    async def _claim(self, item_id: str, now: datetime, seconds: float) -> bool:
        """Hold a due item for `seconds`: true for exactly one server however many tick at once."""
        async with self.db.sessionmaker() as s:
            res = await s.execute(
                update(InterruptTask)
                .where(InterruptTask.id == item_id, InterruptTask.status == "pending")
                .where(or_(InterruptTask.claimed_until.is_(None), InterruptTask.claimed_until < now))
                .values(claimed_until=now + timedelta(seconds=seconds))
            )
            await s.commit()
        return res.rowcount == 1  # type: ignore[attr-defined]

    async def _hold(self, item_id: str, until: datetime) -> None:
        async with self.db.sessionmaker() as s:
            pending = (InterruptTask.id == item_id, InterruptTask.status == "pending")
            await s.execute(update(InterruptTask).where(*pending).values(claimed_until=until))
            await s.commit()

    async def _wake(
        self,
        row: Waiting,
        value: dict[str, Any],
        now: datetime,
        *,
        origin: str = "timer",
        user_id: str | None = None,
        depth: int = 0,
    ) -> str | None:
        """Start the run that continues `row` with `value`; None when it did not start (see the module docstring)."""
        try:
            async with self.db.sessionmaker() as s:
                version_id = await s.scalar(select(Run.workflow_version_id).where(Run.id == row.run_id))
                wf, target = await self.target(s, row.thread_id, version_id)
                if origin == "timer":
                    user_id = await _member(s, row.requested_by, wf.id)
            h = await self.runner.start(
                wf, thread_id=row.thread_id, resume=ResumeSpec(value, row.id, wake=True), user_id=user_id,
                origin=origin, event_depth=depth, **target,
            )  # fmt: skip
        except NotFound as exc:  # the workflow, the conversation or its deployment is gone: nothing can continue it
            await self._give_up(row, f"the run could not continue after its wait: {exc.detail}", now)
            return None
        except Conflict as exc:  # busy with another run, or no longer waiting (an event or a Stop came first)
            await self._hold(row.id, now + timedelta(seconds=BUSY_S))
            log.info("timers.not_now", item=row.id, thread=row.thread_id, reason=exc.detail)
            return None
        except Exception as exc:  # noqa: BLE001 - errors in the workflow, a spending cap, the database: later
            await self._hold_quietly(row.id, now + timedelta(seconds=RETRY_S))
            log.warning("timers.wake_failed", item=row.id, thread=row.thread_id, error=str(exc)[:300])
            return None
        log.info("timers.woke", item=row.id, run=h.run_id, origin=origin)
        return h.run_id

    async def _hold_quietly(self, item_id: str, until: datetime) -> None:
        try:
            await self._hold(item_id, until)
        except Exception:  # the lease it has runs out anyway
            log.warning("timers.hold_failed", item=item_id, exc_info=True)

    async def _give_up(self, row: Waiting, error: str, now: datetime) -> None:
        async with self.db.sessionmaker() as s:
            await end_waits(s, [row.id], error, now)
            await s.commit()
        log.warning("timers.gave_up", item=row.id, thread=row.thread_id, error=error)

    # ------------------------------------------------------------------------------------------- events ---
    async def wake_event(
        self, project_id: str, event: dict[str, Any], *, user_id: str | None = None, depth: int = 0
    ) -> list[str]:
        """End the project's event waits that `event` ({"id", "type", "data"}) matches; returns the run ids started.
        Only waits in this project: another project's runs never hear its events."""
        key = (event.get("data") or {}).get("key")
        key = None if key is None else str(key)
        now = datetime.now(UTC)
        async with self.db.sessionmaker() as s:
            q = (
                select(*_columns())
                .join(Thread, Thread.id == InterruptTask.thread_id)
                .join(WorkflowRow, WorkflowRow.id == Thread.workflow_id)
                .where(WorkflowRow.project_id == project_id, InterruptTask.status == "pending")
                .where(InterruptTask.wait_event == str(event.get("type")), InterruptTask.kind == "timer")
                .where(or_(InterruptTask.wait_key.is_(None), InterruptTask.wait_key == key))
                .order_by(InterruptTask.created_at, InterruptTask.id)
                .limit(EVENT_WAKES)
            )
            rows = [Waiting(*r) for r in (await s.execute(q)).all()]
        shown = {k: event.get(k) for k in ("id", "type", "data")}
        started: list[str] = []
        for row in rows:
            value = waits.woken(event=shown, at=now)
            run_id = await self._wake(row, value, now, origin="event", user_id=user_id, depth=depth)
            if run_id is not None:
                started.append(run_id)
        return started


async def _member(s: AsyncSession, user_id: str | None, workflow_id: str) -> str | None:
    """`user_id` while they are an active member of the workflow's organisation, else None."""
    if not user_id:
        return None
    org_id = await ScopeRepo(s).org_of("workflow", workflow_id)
    active = await s.scalar(
        select(Membership.user_id).where(
            Membership.org_id == org_id,
            Membership.user_id == user_id,
            Membership.accepted.is_(True),
            Membership.suspended.is_(False),
        )
    )
    return user_id if active else None
