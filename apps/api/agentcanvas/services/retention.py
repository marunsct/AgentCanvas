"""Conversation retention (M8-T4).

A conversation (thread) is deleted, with its checkpoints, runs and decisions, once it has been inactive for
longer than its organisation keeps conversations: `Organization.retention_days`, or `AGENTCANVAS_RETENTION_DAYS`
(default 90) when the organisation sets none; 0 keeps them forever. Conversations that are running or waiting
for a person (interrupted) are kept whatever their age.

Every API replica runs the job; deleting is idempotent, so replicas that overlap only repeat work.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from prometheus_client import Counter
from sqlalchemy import select

from agentcanvas.db.models import Organization, Project, Thread, WorkflowRow
from agentcanvas.db.repositories import delete_thread_rows
from agentcanvas.log import get_logger

log = get_logger(__name__)

DELETED = Counter("agentcanvas_threads_expired_total", "Conversations deleted by the retention job")
BATCH = 500


class RetentionJob:
    def __init__(self, db: Any, runner: Any, default_days: int, interval_s: float = 3600.0):
        self.db, self.runner, self.default_days, self.interval_s = db, runner, default_days, interval_s
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
        return deleted
