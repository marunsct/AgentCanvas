"""The run queue (F-5): how many runs an organisation has in flight at once, and which waiting run goes next.

With no limit (the default: `AGENTCANVAS_MAX_CONCURRENT_RUNS=0` and nothing set by the organisation) every run starts
at once, as before. With a limit, a run beyond it waits: its row is marked `queued` and it takes a slot when one is
free and no waiting run of the organisation comes before it, highest priority first (1 high, 0 normal, -1 low),
then oldest. The queue lives in the database, so every server sees the same one.

Each server makes one admission pass per organisation at a time for all its waiting runs (not one per waiting run):
it reads the busy count and the queue's order once, and gives the free slots at the head of the queue to its own
runs there; the runs of another server at the head keep their slots until that server's pass takes them. On
Postgres the pass holds a transaction-scoped advisory lock per organisation, so two servers cannot both take the
last slot, and no row is locked (an organisation row lock would hold up every insert that references it). A pass
runs when a run of the server ends and otherwise every `poll_s`, jittered so servers do not go in step.

A waiting run is `running` for everything else (its thread is taken, the kill switch and a person's stop reach it),
and its server's heartbeat keeps it fresh: one whose server died stops counting, ahead or in flight, after
`orphan_after_s`, and the hand-over sweep deals with it as with any orphaned run. A run that waits longer than
`AGENTCANVAS_MAX_QUEUE_WAIT_S` gives up with a clear error (`QueueTimeout`).
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import and_, func, or_, select, update

from agentcanvas.db.models import Project, Run, Thread, WorkflowRow
from agentcanvas.log import get_logger

log = get_logger(__name__)

Priority = Literal["high", "normal", "low"]
PRIORITIES: dict[str, int] = {"high": 1, "normal": 0, "low": -1}
NAMES: dict[int, Priority] = {1: "high", 0: "normal", -1: "low"}
LimitOf = Callable[[str | None], Awaitable[int]]  # project id -> the organisation's limit (0: none)
ADMISSION_LOCK = 0x6163_7175  # Postgres advisory lock class ("acqu"); the second key is the organisation's hash


def priority_of(given: str | None, origin: str) -> int:
    """A run's priority: the one asked for, else low for experiments and test cases (they can wait), else normal."""
    if given is not None:
        return PRIORITIES[given]
    return PRIORITIES["low"] if origin == "test" else PRIORITIES["normal"]


class QueueTimeout(Exception):
    """A run waited longer than the server allows for a slot."""


@dataclass
class _Waiter:
    run_id: str
    project_id: str | None
    on_queued: Callable[[dict[str, Any]], Awaitable[None]]
    admitted: asyncio.Future[None] = field(default_factory=lambda: asyncio.get_running_loop().create_future())
    shown: int | None = None  # the place it was last told


class Admission:
    def __init__(
        self,
        db: Any,
        limit_of: LimitOf,
        *,
        poll_s: float = 1.0,
        stale_after_s: float = 60.0,
        max_wait_s: float = 0.0,
    ):
        self.db, self.limit_of, self.poll_s, self.stale_after_s = db, limit_of, poll_s, stale_after_s
        self.max_wait_s = max_wait_s  # 0: a run waits as long as it takes
        # this server is draining: a waiting run takes no slot here (it would be cut off at the end of the grace
        # period); the drain hands it over with its start arguments instead
        self.closed = False
        self._waiting: dict[str, dict[str, _Waiter]] = {}  # organisation -> run id -> this server's waiting run
        self._passes: dict[str, asyncio.Task[None]] = {}  # organisation -> the task making its admission passes
        self._wake: dict[str, asyncio.Event] = {}
        self._locks: dict[str, asyncio.Lock] = {}  # one pass per organisation at a time in this process

    def freed(self) -> None:
        """A run of this server ended: every organisation's waiting runs look again at once."""
        for wake in self._wake.values():
            wake.set()

    def close(self) -> None:
        self.closed = True

    def waiting(self) -> int:
        """How many runs wait for a slot on this server."""
        return sum(len(w) for w in self._waiting.values())

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
        pending: dict[str, Any] | None = None,
    ) -> None:
        """Return when the run may go: at once without a limit, else once it has a slot. `on_queued` is told the
        run's place in the queue when it has to wait, and again when its place changes. `pending`: what the run
        was started with, kept on its row while it waits (a hand-over starts it again with it). Raises
        QueueTimeout after `max_wait_s` without a slot."""
        if org_id is None:
            return
        if await self.limit_of(project_id) <= 0:
            return
        async with self.db.sessionmaker() as s:
            await s.execute(update(Run).where(Run.id == run_id).values(queued=True, pending=pending))
            await s.commit()
        w = _Waiter(run_id, project_id, on_queued)
        self._waiting.setdefault(org_id, {})[run_id] = w
        self._serve(org_id)
        try:
            async with asyncio.timeout(self.max_wait_s or None):
                await asyncio.shield(w.admitted)  # a stop cancels the wait, never the pass's future
        except TimeoutError:
            await self._leave(run_id)
            raise QueueTimeout(
                f"waited {self.max_wait_s:g} s for a free run slot (the organisation's runs-at-once limit) and gave "
                "up; try again later"
            ) from None
        finally:
            self._waiting.get(org_id, {}).pop(run_id, None)

    async def _leave(self, run_id: str) -> None:
        async with self.db.sessionmaker() as s:
            await s.execute(update(Run).where(Run.id == run_id).values(queued=False, pending=None))
            await s.commit()

    def _serve(self, org_id: str) -> None:
        """Make sure a task makes the organisation's admission passes, and that it makes one now."""
        self._wake.setdefault(org_id, asyncio.Event()).set()
        if org_id not in self._passes:
            self._passes[org_id] = asyncio.create_task(self._loop(org_id), name=f"admission:{org_id}")

    async def _loop(self, org_id: str) -> None:
        wake = self._wake[org_id]
        try:
            while self._waiting.get(org_id):
                wake.clear()
                if not self.closed:
                    try:
                        await self.admit(org_id)
                    except Exception:
                        log.exception("admission.pass_failed", org_id=org_id)
                try:  # jittered: servers that freed slots together do not keep asking together
                    await asyncio.wait_for(wake.wait(), self.poll_s * random.uniform(0.75, 1.25))
                except TimeoutError:
                    pass
        finally:
            self._passes.pop(org_id, None)  # no await since the last check: a new waiter starts a new task

    async def lock(self, s: Any, org_id: str) -> None:
        """Postgres: one server at a time gives out the organisation's slots (until the transaction ends)."""
        if s.bind.dialect.name == "postgresql":
            await s.execute(select(func.pg_advisory_xact_lock(ADMISSION_LOCK, func.hashtext(org_id))))

    async def admit(self, org_id: str) -> None:
        """One admission pass for this server's waiting runs of the organisation."""
        lock = self._locks.setdefault(org_id, asyncio.Lock())
        async with lock:
            mine = {r: w for r, w in self._waiting.get(org_id, {}).items() if not w.admitted.done()}
            if not mine:
                return
            limit = await self.limit_of(next(iter(mine.values())).project_id) or 0
            places: dict[str, int] = {}
            async with self.db.sessionmaker() as s:
                if limit <= 0:  # the limit was lifted while they waited: all go
                    go = list(mine)
                else:
                    await self.lock(s, org_id)
                    in_org = self._in_org(org_id)
                    busy = await s.scalar(
                        select(func.count()).select_from(in_org.where(Run.queued.is_(False), self._alive()).subquery())
                    )
                    # this server's own runs count however late their heartbeat is: they are waiting right here
                    queue = list(
                        (
                            await s.scalars(
                                in_org.where(Run.queued.is_(True), or_(self._alive(), Run.id.in_(list(mine)))).order_by(
                                    Run.priority.desc(), Run.id
                                )
                            )
                        ).all()
                    )
                    free = limit - (busy or 0)
                    places = {run_id: i + 1 for i, run_id in enumerate(queue) if run_id in mine}
                    # the free slots go to the head of the queue; a run of this server there takes its slot, and
                    # one that is no longer waiting (stopped, swept) has nothing to wait for
                    go = [r for r in mine if r not in places or places[r] <= free]
                if go:
                    await s.execute(
                        update(Run).where(Run.id.in_(go), Run.queued.is_(True)).values(queued=False, pending=None)
                    )
                await s.commit()
        for run_id in go:
            mine[run_id].admitted.set_result(None)
        for run_id, place in places.items():
            w = mine[run_id]
            if run_id in go or w.shown == place:
                continue
            w.shown = place
            try:
                await w.on_queued({"position": place, "limit": limit})
            except Exception:
                log.exception("admission.notify_failed", run_id=run_id)
