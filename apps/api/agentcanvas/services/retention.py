"""Conversation retention (M8-T4).

A conversation (thread) is deleted, with its checkpoints, runs and decisions, once it has been inactive for
longer than its organisation keeps conversations: `Organization.retention_days`, or `AGENTCANVAS_RETENTION_DAYS`
(default 90) when the organisation sets none; 0 keeps them forever. Conversations that are running or waiting
for a person (interrupted) are kept whatever their age, but a decision nobody gives for
`AGENTCANVAS_APPROVAL_EXPIRY_DAYS` (default 90) expires: the Inbox item is marked expired, the run that asked is
cancelled, and the conversation becomes idle, so retention deletes it once it has been inactive long enough (so does a
conversation that waits for a tool the caller's application runs, AG-UI, whose result never came). An item
whose step names an escalation role is escalated instead (E-6, `notifier.escalate`): the run keeps waiting and the
expiry counts again from the escalation. A Wait step's item (F-10) is nobody's to decide: its expiry counts from when
it is due, so a run waiting for a date months away is kept until then, and one the timer job never managed to
continue goes the same way after the expiry.

The same job deletes audit-log rows older than `AGENTCANVAS_AUDIT_RETENTION_DAYS` (default 400) and, when
`AGENTCANVAS_MEMORY_RETENTION_DAYS` is set (off by default), long-term memories not updated for that long (S12).
Files people attached to runs (G-5) follow conversations: one no run has used for the organisation's conversation
retention is deleted. So does the event log, which holds what other systems sent (webhook bodies, customer records);
an event handler's dedup values go after `AGENTCANVAS_EVENT_DEDUP_DAYS` (30), never later than the event log.

Every API replica runs the job. Each conversation is claimed before it is deleted (its row locked, skipping one
another replica holds, on Postgres) and the age and status are checked again under that lock, so replicas share the
work and a conversation that came back to life since the scan is kept. A tick goes through every expired
conversation, a batch at a time, within a time budget; what is left waits for the next tick.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from prometheus_client import Counter
from sqlalchemy import delete, func, select, text, update

from agentcanvas import observability
from agentcanvas.adapters.spi import MemoryAbility, supports
from agentcanvas.db.models import (
    AuditAnchor,
    AuditLog,
    EventDedup,
    EventLog,
    InterruptTask,
    Organization,
    Project,
    Run,
    Thread,
    WorkflowRow,
)
from agentcanvas.db.repositories import delete_thread_rows
from agentcanvas.db.retry import retrying
from agentcanvas.errors import Unavailable
from agentcanvas.log import get_logger
from agentcanvas.runtime.types import CLIENT_TOOL
from agentcanvas.services import org_settings
from agentcanvas.services.approvers import Approvers
from agentcanvas.services.files import expire_files
from agentcanvas.services.notifier import escalate, smtp_ready
from agentcanvas.settings import Settings

log = get_logger(__name__)

DELETED = Counter("agentcanvas_threads_expired_total", "Conversations deleted by the retention job")
EVENTS_DELETED = Counter("agentcanvas_events_expired_total", "Event-log rows deleted by the retention job")
APPROVALS_EXPIRED = Counter("agentcanvas_approvals_expired_total", "Inbox items nobody decided in time")
BATCH = 500
BUDGET_S = 300.0  # the most a tick spends deleting conversations; the rest is the next tick's


def _expired(cutoff: datetime) -> tuple[Any, ...]:
    """A conversation that may be deleted: inactive since before `cutoff`, and not running or waiting for a person."""
    return (Thread.updated_at < cutoff, Thread.status.not_in(("running", "interrupted")))


class RetentionJob:
    def __init__(
        self,
        db: Any,
        runner: Any,
        default_days: int,
        interval_s: float = 3600.0,
        audit_days: int = 0,
        memory_days: int = 0,
        server: Settings | None = None,
    ):
        self.db, self.runner, self.default_days, self.interval_s = db, runner, default_days, interval_s
        self.audit_days, self.memory_days = audit_days, memory_days
        self.server = server or Settings(audit_retention_days=audit_days, memory_retention_days=memory_days)
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        observability.job_started("retention")
        self._task = asyncio.create_task(self._loop(), name="retention")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _loop(self) -> None:
        while True:
            try:
                with observability.job_round("retention"):
                    await self.tick(datetime.now(UTC))
            except Exception:
                log.exception("retention.tick_failed")
            await asyncio.sleep(self.interval_s)

    async def _expired(self, org_id: str, cutoff: datetime, after: str) -> list[str]:
        """The next batch of an organisation's expired conversations, by id after `after`."""
        async with self.db.sessionmaker() as s:
            return list(
                (
                    await s.scalars(
                        select(Thread.id)
                        .join(WorkflowRow, WorkflowRow.id == Thread.workflow_id)
                        .join(Project, Project.id == WorkflowRow.project_id)
                        .where(Project.org_id == org_id, Thread.id > after, *_expired(cutoff))
                        .order_by(Thread.id)
                        .limit(BATCH)
                    )
                ).all()
            )

    async def _delete(self, thread_id: str, cutoff: datetime) -> bool:
        """Delete one conversation if it is still expired, holding its row so no run starts on it meanwhile and no
        other replica deletes it too (one that holds it is skipped). False when it was kept."""
        if thread_id in self.runner.active_by_thread:  # started since the scan
            return False
        async with self.db.sessionmaker() as s:

            async def rows() -> bool:
                claimed = await s.scalar(
                    select(Thread.id).where(Thread.id == thread_id, *_expired(cutoff)).with_for_update(skip_locked=True)
                )
                if claimed is None:  # touched since the scan, gone, or another replica's
                    return False
                await self.runner.forget_thread(thread_id)  # checkpoints first: rows are the index
                await delete_thread_rows(s, thread_id)
                await s.commit()
                return True

            try:  # a deadlock's victim (something still writing under it) runs again, claim and all
                return await retrying(s, rows, what="retention delete")
            except Unavailable:  # it kept colliding: the next tick tries again
                log.warning("retention.delete_deferred", thread_id=thread_id)
                return False

    async def tick(self, now: datetime) -> list[str]:
        """Delete the conversations that expired by `now`; returns their ids."""
        await self.expire_approvals(now)
        await self.expire_tool_calls(now)
        deleted: list[str] = []
        until = time.monotonic() + BUDGET_S
        async with self.db.sessionmaker() as s:
            orgs = (await s.execute(select(Organization.id, Organization.retention_days))).all()
        for org_id, own in orgs:
            days = self.default_days if own is None else own
            if days <= 0:
                continue
            cutoff, after, gone = now - timedelta(days=days), "", 0
            while time.monotonic() < until:
                batch = await self._expired(org_id, cutoff, after)
                for thread_id in batch:
                    if time.monotonic() >= until:
                        break
                    if await self._delete(thread_id, cutoff):
                        deleted.append(thread_id)
                        gone += 1
                if len(batch) < BATCH:
                    break
                after = batch[-1]
            if gone:
                log.info("retention.deleted", org_id=org_id, threads=gone, days=days)
            async with self.db.sessionmaker() as s:  # G-5: files no run has used for as long go too
                files = await expire_files(s, org_id, now - timedelta(days=days))
                await s.commit()
            if files:
                log.info("retention.files_deleted", org_id=org_id, files=files, days=days)
        DELETED.inc(len(deleted))
        await self.expire_events(now, [(org_id, self.default_days if own is None else own) for org_id, own in orgs])
        await self.expire_audit(now)
        await self.expire_memory(now, [org_id for org_id, _ in orgs])
        return deleted

    async def expire_approvals(self, now: datetime) -> int:
        """Mark the Inbox items nobody decided within the approval expiry as expired. A conversation left with
        nothing to wait for is no longer waiting: the run that asked is cancelled and the conversation becomes idle,
        so it follows conversation retention from now on. Returns the items expired."""
        days = self.server.approval_expiry_days
        if days <= 0:
            return 0
        cutoff, until, n, escalated = now - timedelta(days=days), time.monotonic() + BUDGET_S, 0, 0
        error = f"no one answered within {days} days: the approval expired"
        stuck = f"the run could not continue within {days} days after its Wait step was due: the wait expired"
        why = f"nobody decided within {days} days"
        while time.monotonic() < until:
            busy = list(self.runner.active_by_thread)  # being answered here right now
            async with self.db.sessionmaker() as s:
                found = (
                    await s.execute(
                        select(
                            InterruptTask.id,
                            InterruptTask.thread_id,
                            InterruptTask.run_id,
                            InterruptTask.approvers,
                            InterruptTask.escalated_at,
                            InterruptTask.kind,
                        )
                        .where(
                            InterruptTask.status == "pending",
                            # an escalated item gets a full expiry from its escalation, a Wait step from its due time
                            func.coalesce(InterruptTask.escalated_at, InterruptTask.due_at, InterruptTask.created_at)
                            < cutoff,
                            InterruptTask.escalate_at.is_(None),  # one its step escalates first, at its own time
                            InterruptTask.thread_id.not_in(busy),
                        )
                        .order_by(InterruptTask.created_at, InterruptTask.id)
                        .limit(BATCH)
                    )
                ).all()
                # a step that escalates hands its item to a role instead of cancelling the run (once)
                late = [(r.id, Approvers.of(r.approvers).escalate_to, r.escalated_at) for r in found]
                handed = [(i, role, why) for i, role, at in late if role and at is None]
                escalated += await escalate(s, handed, now, smtp_ready(self.server))
                rows = [r for r in found if r.id not in {i for i, _, _ in handed}]
                res = await s.execute(
                    update(InterruptTask)  # a decision that won the race keeps its item
                    .where(InterruptTask.id.in_([r.id for r in rows]), InterruptTask.status == "pending")
                    .values(status="expired")
                )
                runs: dict[str, set[str]] = {}
                timers: dict[str, bool] = {}  # thread -> every item that expired there was a Wait step
                for r in rows:
                    runs.setdefault(r.thread_id, set()).add(r.run_id)
                    timers[r.thread_id] = timers.get(r.thread_id, True) and r.kind == "timer"
                for thread_id, run_ids in runs.items():
                    waiting = await s.scalar(
                        select(InterruptTask.id)
                        .where(InterruptTask.thread_id == thread_id, InterruptTask.status == "pending")
                        .limit(1)
                    )
                    handed = await s.scalar(  # a run a restart paused continues on its own: not abandoned
                        select(Run.id)
                        .where(Run.thread_id == thread_id, Run.status == "interrupted", Run.handover.is_(True))
                        .limit(1)
                    )
                    if waiting or handed:
                        continue
                    await s.execute(
                        update(Run)
                        .where(Run.id.in_(run_ids), Run.status == "interrupted")
                        .values(status="cancelled", error=stuck if timers[thread_id] else error, ended_at=now)
                    )
                    waits = (Thread.id == thread_id, Thread.status == "interrupted")
                    await s.execute(update(Thread).where(*waits).values(status="idle"))  # updated now: retention
                    # counts its inactivity from the expiry
                await s.commit()
            gone = res.rowcount or 0  # type: ignore[attr-defined]
            n += gone
            if gone:
                log.info("retention.approvals_expired", items=gone, days=days)
            if len(found) < BATCH:
                break
        APPROVALS_EXPIRED.inc(n)
        if escalated:
            log.info("retention.approvals_escalated", items=escalated, days=days)
        return n

    async def expire_tool_calls(self, now: datetime) -> int:
        """Conversations that wait only for the result of a tool the caller's application runs (AG-UI front-end
        tools have no Inbox item): one not answered within the approval expiry is no longer waiting, like an expired
        approval. Its run is cancelled and the conversation becomes idle. Returns the conversations released."""
        days = self.server.approval_expiry_days
        if days <= 0:
            return 0
        cutoff, until, n, after = now - timedelta(days=days), time.monotonic() + BUDGET_S, 0, ""
        error = f"no one answered within {days} days: the application never sent the tool's result"
        asked = select(InterruptTask.id).where(InterruptTask.thread_id == Thread.id, InterruptTask.status == "pending")
        while time.monotonic() < until:
            busy = list(self.runner.active_by_thread)
            async with self.db.sessionmaker() as s:
                waiting = (
                    Thread.status == "interrupted",
                    Thread.deployment_id.is_not(None),
                    Thread.updated_at < cutoff,
                )
                found = list(
                    (
                        await s.scalars(
                            select(Thread.id)
                            .where(*waiting, Thread.id > after, Thread.id.not_in(busy), ~asked.exists())
                            .order_by(Thread.id)
                            .limit(BATCH)
                        )
                    ).all()
                )
                for thread_id in found:
                    q = select(Run.id, Run.interrupts, Run.handover).where(Run.thread_id == thread_id)
                    last = (await s.execute(q.order_by(Run.started_at.desc(), Run.id.desc()).limit(1))).first()
                    kinds = {i.get("kind") for i in (last.interrupts or []) if isinstance(i, dict)} if last else set()
                    if last is None or last.handover or kinds != {CLIENT_TOOL}:  # waits for something else
                        continue
                    await s.execute(
                        update(Run)
                        .where(Run.id == last.id, Run.status == "interrupted")
                        .values(status="cancelled", error=error, ended_at=now)
                    )
                    released = await s.execute(
                        update(Thread).where(Thread.id == thread_id, *waiting).values(status="idle")
                    )
                    n += released.rowcount or 0  # type: ignore[attr-defined]
                await s.commit()
            if len(found) < BATCH:
                break
            after = found[-1]
        if n:
            log.info("retention.tool_calls_expired", threads=n, days=days)
        return n

    async def expire_events(self, now: datetime, orgs: list[tuple[str, int]]) -> int:
        """Each organisation's event log older than its conversation retention, and its handlers' dedup values older
        than the dedup window or that retention, whichever is shorter. Returns the event-log rows deleted."""
        until = time.monotonic() + BUDGET_S
        dedup_days = self.server.event_dedup_days
        n = 0
        for org_id, days in orgs:
            projects = select(Project.id).where(Project.org_id == org_id)
            if days > 0:
                gone = await self._delete_old(EventLog, projects, now - timedelta(days=days), until)
                if gone:
                    log.info("retention.events_deleted", org_id=org_id, rows=gone, days=days)
                n += gone
            if window := min((d for d in (days, dedup_days) if d > 0), default=0):
                await self._delete_old(EventDedup, projects, now - timedelta(days=window), until)
        EVENTS_DELETED.inc(n)
        return n

    async def _delete_old(self, table: Any, projects: Any, cutoff: datetime, until: float) -> int:
        """Delete a project-owned table's rows created before `cutoff`, a batch at a time, within the time budget."""
        gone = 0
        while time.monotonic() < until:
            batch = select(table.id).where(table.project_id.in_(projects), table.created_at < cutoff).limit(BATCH)
            async with self.db.sessionmaker() as s:
                res = await s.execute(delete(table).where(table.id.in_(batch)))
                await s.commit()
            rows = res.rowcount or 0  # type: ignore[attr-defined]
            gone += rows
            if rows < BATCH:
                break
        return gone

    async def _days(self, org_id: str | None, key: str) -> int:
        """The days for `key` that apply to an organisation: its own within the server's ceiling, else the server's."""
        server = self.server.model_copy(
            update={"audit_retention_days": self.audit_days, "memory_retention_days": self.memory_days}
        )  # the job's own numbers: they are what the server was started with
        if org_id is None:
            return getattr(server, key)
        async with self.db.sessionmaker() as s:
            org = await s.get(Organization, org_id)
        return org_settings.effective(server, org.settings if org else None)[key]

    async def expire_audit(self, now: datetime) -> int:
        """Each organisation's rows older than its audit retention (never shorter than the server's), and rows that
        belong to none by the server's."""
        async with self.db.sessionmaker() as s:
            org_ids = list((await s.scalars(select(Organization.id))).all())
        n = 0
        for org_id in [*org_ids, None]:
            days = await self._days(org_id, "audit_retention_days")
            if days <= 0:
                continue
            gone = await self._expire_audit(org_id, now - timedelta(days=days))
            if gone:
                log.info("retention.audit_deleted", org_id=org_id, rows=gone, days=days)
            n += gone
        return n

    async def _expire_audit(self, org_id: str | None, cutoff: datetime) -> int:
        """Delete an organisation's entries older than `cutoff`, from the start of its hash chain up to the first
        that is not, so the chain stays whole (services/audit_chain.py); where it now starts is kept in
        `audit_anchor`. Entries are written in time order; an older one after a kept one waits for it."""
        cutoff = min(cutoff, datetime.now(UTC) - timedelta(days=1))  # never the last day's: the function refuses it
        if self.db.is_postgres:  # the API's role may not delete audit rows: the owner's function does (app_role.py)
            async with self.db.sessionmaker() as s:
                gone = await s.scalar(text("SELECT audit_expire(:org, :cutoff)"), {"org": org_id, "cutoff": cutoff})
                await s.commit()
                return gone or 0
        async with self.db.sessionmaker() as s:  # SQLite: the same, here (migration d32911f9085f)
            if org_id is None:  # entries of no organisation (none are written any more) have no chain
                res = await s.execute(delete(AuditLog).where(AuditLog.org_id.is_(None), AuditLog.created_at < cutoff))
                await s.commit()
                return res.rowcount or 0  # type: ignore[attr-defined]
            kept = await s.scalar(
                select(AuditLog.id)
                .where(AuditLog.org_id == org_id, AuditLog.created_at >= cutoff)
                .order_by(AuditLog.id)
                .limit(1)
            )
            last = (
                await s.execute(
                    select(AuditLog.id, AuditLog.row_hash)
                    .where(AuditLog.org_id == org_id, AuditLog.created_at < cutoff)
                    .where(*([AuditLog.id < kept] if kept is not None else []))
                    .order_by(AuditLog.id.desc())
                    .limit(1)
                )
            ).first()
            if last is None:
                return 0
            res = await s.execute(delete(AuditLog).where(AuditLog.org_id == org_id, AuditLog.id <= last.id))
            gone = res.rowcount or 0  # type: ignore[attr-defined]
            if gone:  # another server may have deleted them a moment ago
                s.add(AuditAnchor(org_id=org_id, through_id=last.id, last_hash=last.row_hash, rows=gone))
            await s.commit()
            return gone

    async def expire_memory(self, now: datetime, org_ids: list[str]) -> int:
        if not supports(self.runner.adapter, "memory_store"):
            return 0
        memory = cast(MemoryAbility, self.runner.adapter)
        n = 0
        for org_id in org_ids:
            days = await self._days(org_id, "memory_retention_days")
            if days <= 0:
                continue
            gone = await memory.memory_expire((f"org:{org_id}",), now - timedelta(days=days))
            if gone:
                log.info("retention.memory_deleted", org_id=org_id, items=gone, days=days)
            n += gone
        return n
