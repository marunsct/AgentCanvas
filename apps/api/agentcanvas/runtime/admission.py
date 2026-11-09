"""The run queue (F-5): how many runs an organisation has in flight at once, and which waiting run goes next.

With no limit (the default: `AGENTCANVAS_MAX_CONCURRENT_RUNS=0` and nothing set by the organisation) every run starts
at once, as before. With a limit, a run beyond it waits: its row is marked `queued` and it takes a slot when one is
free and no waiting run of the organisation comes before it, highest priority first (1 high, 0 normal, -1 low),
then oldest. The queue lives in the database, so every server sees the same one; on Postgres the organisation's row
is locked while a run takes a slot, so two servers cannot both take the last one.

A waiting run is `running` for everything else (its thread is taken, the kill switch and a person's stop reach it),
and its server's heartbeat keeps it fresh: one whose server died stops counting, ahead or in flight, after
`orphan_after_s`, and the hand-over sweep deals with it as with any orphaned run.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import and_, func, or_, select, update

from agentcanvas.db.models import Organization, Project, Run, Thread, WorkflowRow

Priority = Literal["high", "normal", "low"]
PRIORITIES: dict[str, int] = {"high": 1, "normal": 0, "low": -1}
NAMES: dict[int, Priority] = {1: "high", 0: "normal", -1: "low"}
LimitOf = Callable[[str | None], Awaitable[int]]  # project id -> the organisation's limit (0: none)


def priority_of(given: str | None, origin: str) -> int:
    """A run's priority: the one asked for, else low for experiments and test cases (they can wait), else normal."""
    if given is not None:
        return PRIORITIES[given]
    return PRIORITIES["low"] if origin == "test" else PRIORITIES["normal"]


class Admission:
    def __init__(self, db: Any, limit_of: LimitOf, *, poll_s: float = 1.0, stale_after_s: float = 60.0):
        self.db, self.limit_of, self.poll_s, self.stale_after_s = db, limit_of, poll_s, stale_after_s
        self._freed = asyncio.Event()  # a run of this server ended: waiting ones look again at once

    def freed(self) -> None:
        self._freed.set()

    def _in_org(self, org_id: str) -> Any:
        """Runs of the organisation `org_id` (through their thread's workflow's project)."""
        return (
            select(Run.id)
            .join(Thread, Thread.id == Run.thread_id)
            .join(WorkflowRow, WorkflowRow.id == Thread.workflow_id)
            .join(Project, Project.id == WorkflowRow.project_id)
            .where(Project.org_id == org_id, Run.status == "running")
        )

    def _alive(self) -> Any:
        since = datetime.now(UTC) - timedelta(seconds=self.stale_after_s)
        return or_(Run.heartbeat_at >= since, and_(Run.heartbeat_at.is_(None), Run.started_at >= since))

    async def wait(
        self,
        run_id: str,
        org_id: str | None,
        project_id: str | None,
        on_queued: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        """Return when the run may go: at once without a limit, else once it has a slot. `on_queued` is told the
        run's place in the queue when it has to wait, and again when its place changes."""
        if org_id is None:
            return
        limit = await self.limit_of(project_id)
        if limit <= 0:
            return
        shown: int | None = None
        async with self.db.sessionmaker() as s:
            await s.execute(update(Run).where(Run.id == run_id).values(queued=True))
            await s.commit()
        while True:
            place = await self._try(run_id, org_id, limit)
            if place is None:
                return
            if place != shown:
                shown = place
                await on_queued({"position": place, "limit": limit})
            self._freed.clear()
            try:
                await asyncio.wait_for(self._freed.wait(), self.poll_s)
            except TimeoutError:
                pass
            limit = await self.limit_of(project_id) or 0
            if limit <= 0:  # the limit was lifted while it waited
                async with self.db.sessionmaker() as s:
                    await s.execute(update(Run).where(Run.id == run_id).values(queued=False))
                    await s.commit()
                return

    async def _try(self, run_id: str, org_id: str, limit: int) -> int | None:
        """Take a slot (None) or say the run's place in the queue (1: next)."""
        async with self.db.sessionmaker() as s:
            if s.bind.dialect.name == "postgresql":  # one server at a time takes the organisation's slots
                await s.execute(select(Organization.id).where(Organization.id == org_id).with_for_update())
            in_org = self._in_org(org_id).where(self._alive())
            busy = await s.scalar(select(func.count()).select_from(in_org.where(Run.queued.is_(False)).subquery()))
            mine = await s.get(Run, run_id)
            if mine is None or not mine.queued:  # stopped, or no longer waiting: nothing to wait for
                await s.commit()
                return None
            ahead = await s.scalar(
                select(func.count()).select_from(
                    in_org.where(
                        Run.queued.is_(True),
                        Run.id != run_id,
                        or_(Run.priority > mine.priority, and_(Run.priority == mine.priority, Run.id < run_id)),
                    ).subquery()
                )
            )
            if (busy or 0) + (ahead or 0) < limit:  # the free slots go to the runs ahead first
                mine.queued = False
                await s.commit()
                return None
            await s.commit()
            return (ahead or 0) + 1
