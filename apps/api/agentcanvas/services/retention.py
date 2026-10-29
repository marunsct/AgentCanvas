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
from typing import Any, cast

from prometheus_client import Counter
from sqlalchemy import delete, select

from agentcanvas.adapters.spi import MemoryAbility, supports
from agentcanvas.db.models import AuditLog, Organization, Project, Thread, WorkflowRow
from agentcanvas.db.repositories import delete_thread_rows
from agentcanvas.log import get_logger
from agentcanvas.services import org_settings
from agentcanvas.settings import Settings

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
        server: Settings | None = None,
    ):
        self.db, self.runner, self.default_days, self.interval_s = db, runner, default_days, interval_s
        self.audit_days, self.memory_days = audit_days, memory_days
        self.server = server or Settings(audit_retention_days=audit_days, memory_retention_days=memory_days)
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
                await self.runner.forget_thread(thread_id)  # checkpoints first: rows are the index
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
            where = AuditLog.org_id == org_id if org_id else AuditLog.org_id.is_(None)
            async with self.db.sessionmaker() as s:
                res = await s.execute(delete(AuditLog).where(where, AuditLog.created_at < now - timedelta(days=days)))
                await s.commit()
            gone = res.rowcount or 0  # type: ignore[attr-defined]
            if gone:
                log.info("retention.audit_deleted", org_id=org_id, rows=gone, days=days)
            n += gone
        return n

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
