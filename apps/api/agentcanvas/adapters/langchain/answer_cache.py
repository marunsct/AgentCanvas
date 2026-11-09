"""The model gateway's answer cache in the database (G-8): one cache per project that every replica shares.

`AnswerCache` (adapters/langchain/gateway.py) keeps answers in one process, so on several replicas an identical request
paid the provider once per replica and again after every restart. `DatabaseAnswerCache` stores each answer in
`answer_cache` (project, request key, the message as JSON, when it was stored and when it expires) and keeps the
in-process cache for the synchronous calls. Keyed as before (the gateway hashes models, settings, messages, tools and
options), capped at `CACHE_SIZE` answers per project, expired and oldest ones dropped first. A database that cannot be
reached means a miss, never a failed model call.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from langchain_core.messages import AIMessage
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from agentcanvas.adapters.langchain.gateway import CACHE_SIZE, AnswerCache
from agentcanvas.db.models import AnswerCacheRow
from agentcanvas.log import get_logger

log = get_logger(__name__)
PRUNE_EVERY = 25  # stores between two clean-ups of a project's cache


class DatabaseAnswerCache(AnswerCache):
    def __init__(self, db: Any, project_id: str, size: int = CACHE_SIZE):
        super().__init__(size)
        self.db = db
        self.project_id = project_id
        self._stores = 0

    async def aget(self, key: str, ttl: float) -> AIMessage | None:
        now = datetime.now(UTC)
        try:
            async with self.db.sessionmaker() as s:
                q = select(AnswerCacheRow.answer).where(
                    AnswerCacheRow.project_id == self.project_id,
                    AnswerCacheRow.key == key,
                    AnswerCacheRow.created_at >= now - timedelta(seconds=ttl),
                    AnswerCacheRow.expires_at > now,
                )
                found = await s.scalar(q)
            return AIMessage.model_validate(found) if found is not None else None
        except Exception:
            log.warning("answer_cache.get_failed", project_id=self.project_id, exc_info=True)
            return await super().aget(key, ttl)

    async def aput(self, key: str, answer: AIMessage, ttl: float) -> None:
        await super().aput(key, answer, ttl)
        now = datetime.now(UTC)
        try:
            async with self.db.sessionmaker() as s:
                mine = (AnswerCacheRow.project_id == self.project_id, AnswerCacheRow.key == key)
                await s.execute(delete(AnswerCacheRow).where(*mine))
                s.add(
                    AnswerCacheRow(
                        project_id=self.project_id, key=key, answer=answer.model_dump(mode="json"),
                        created_at=now, expires_at=now + timedelta(seconds=ttl),
                    )
                )  # fmt: skip
                try:
                    await s.commit()
                except IntegrityError:  # another replica stored the same answer a moment earlier
                    await s.rollback()
            self._stores += 1
            if self._stores % PRUNE_EVERY == 1:
                await self.prune(now)
        except Exception:
            log.warning("answer_cache.put_failed", project_id=self.project_id, exc_info=True)

    async def prune(self, now: datetime | None = None) -> None:
        """Drop the project's expired answers, then the oldest beyond the size cap."""
        now = now or datetime.now(UTC)
        mine = AnswerCacheRow.project_id == self.project_id
        async with self.db.sessionmaker() as s:
            await s.execute(delete(AnswerCacheRow).where(mine, AnswerCacheRow.expires_at <= now))
            newest = select(AnswerCacheRow.key).where(mine).order_by(AnswerCacheRow.created_at.desc()).limit(self.size)
            await s.execute(delete(AnswerCacheRow).where(mine, AnswerCacheRow.key.not_in(newest)))
            await s.commit()

    async def count(self) -> int:
        async with self.db.sessionmaker() as s:
            q = select(func.count()).select_from(AnswerCacheRow).where(AnswerCacheRow.project_id == self.project_id)
            return int(await s.scalar(q) or 0)
