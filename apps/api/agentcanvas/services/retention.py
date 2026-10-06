"""Conversation retention (M8-T4).

A conversation (thread) is deleted, with its checkpoints, runs and decisions, once it has been inactive for
longer than its organisation keeps conversations: `Organization.retention_days`, or `AGENTCANVAS_RETENTION_DAYS`
(default 90) when the organisation sets none; 0 keeps them forever. Conversations that are running or waiting
for a person (interrupted) are kept whatever their age.

The same job deletes audit-log rows older than `AGENTCANVAS_AUDIT_RETENTION_DAYS` (default 400) and, when
`AGENTCANVAS_MEMORY_RETENTION_DAYS` is set (off by default), long-term memories not updated for that long (S12).

Every API replica runs the job; deleting is idempotent, so replicas that overlap only repeat work.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from prometheus_client import Counter
from sqlalchemy import delete, select

from agentcanvas.db.models import AuditLog, Organization, Project, Thread, WorkflowRow
from agentcanvas.db.repositories import delete_thread_rows
from agentcanvas.log import get_logger

log = get_logger(__name__)

DELETED = Counter("agentcanvas_threads_expired_total", "Conversations deleted by the retention job")
BATCH = 500


class RetentionJob:
    def __init__(
        self,
        db: Any,
        runner: Any,
        default_days: int,
        interval_s: float = 3600.0,
        audit_days: int = 0,
        memory_days: int = 0,
    ):
        self.db, self.runner, self.default_days, self.interval_s = db, runner, default_days, interval_s
        self.audit_days, self.memory_days = audit_days, memory_days
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="retention")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick(datetime.now(UTC))
            except Exception:
                log.exception("retention.tick_failed")
            await asyncio.sleep(self.interval_s)

    async def tick(self, now: datetime) -> list[str]:
        """Delete the conversations that expired by `now` (at most BATCH per organisation); returns their ids."""
        deleted: list[str] = []
        async with self.db.sessionmaker() as s:
            orgs = (await s.execute(select(Organization.id, Organization.retention_days))).all()
        for org_id, own in orgs:
            days = self.default_days if own is None else own
            if days <= 0:
                continue
            async with self.db.sessionmaker() as s:
                expired = (
                    await s.scalars(
                        select(Thread.id)
                        .join(WorkflowRow, WorkflowRow.id == Thread.workflow_id)
                        .join(Project, Project.id == WorkflowRow.project_id)
                        .where(
                            Project.org_id == org_id,
                            Thread.updated_at < now - timedelta(days=days),
                            Thread.status.not_in(("running", "interrupted")),
                        )
                        .limit(BATCH)
                    )
                ).all()
            gone = 0
            for thread_id in expired:
                if thread_id in self.runner.active_by_thread:  # started since the scan
                    continue
                await self.runner.adapter.delete_thread(thread_id)  # checkpoints first: rows are the index
                async with self.db.sessionmaker() as s:
                    await delete_thread_rows(s, thread_id)
                    await s.commit()
                deleted.append(thread_id)
                gone += 1
            if gone:
                log.info("retention.deleted", org_id=org_id, threads=gone, days=days)
        DELETED.inc(len(deleted))
        await self.expire_audit(now)
        await self.expire_memory(now, [org_id for org_id, _ in orgs])
        return deleted

    async def expire_audit(self, now: datetime) -> int:
        if self.audit_days <= 0:
            return 0
        async with self.db.sessionmaker() as s:
            res = await s.execute(delete(AuditLog).where(AuditLog.created_at < now - timedelta(days=self.audit_days)))
            await s.commit()
        n = res.rowcount or 0  # type: ignore[attr-defined]
        if n:
            log.info("retention.audit_deleted", rows=n, days=self.audit_days)
        return n

    async def expire_memory(self, now: datetime, org_ids: list[str]) -> int:
        if self.memory_days <= 0:
            return 0
        before, n = now - timedelta(days=self.memory_days), 0
        for org_id in org_ids:
            n += await self.runner.adapter.memory_expire((f"org:{org_id}",), before)
        if n:
            log.info("retention.memory_deleted", items=n, days=self.memory_days)
        return n
