"""Where paused runs of frameworks without their own checkpoints are kept (2026-10-18 review, H7).

CrewAI, Google ADK, the flow-engine frameworks (Pydantic AI, Agno, Agent Framework, Strands, LlamaIndex), Mastra and
the OpenAI Agents SDK describe a paused run as JSON. It used to live in a dict of one API process, so a resume that
reached another replica, or came after a restart, found "nothing to resume on this conversation". The server installs
`DatabasePaused` at startup (`thread.paused_state`); without a server (the conformance kit, unit tests) a process
dict is used, as before.

The replies a remote A2A agent gave a LangGraph step that paused for its question live here too (`DatabaseReplies`,
table `a2a_reply`, G-9): they used to be kept in one process, so the step's continuation on another replica or after
a restart had to stop rather than send the first message again.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

MEMORY_LIMIT = 10_000  # conversations kept by the in-process fallback


class PausedStore(Protocol):
    async def get(self, thread_id: str) -> str | None: ...
    async def put(self, thread_id: str, state: str) -> None: ...
    async def drop(self, thread_id: str) -> None: ...


class MemoryPaused:
    """One process's paused runs (no server): bounded, oldest dropped first."""

    def __init__(self) -> None:
        self._runs: dict[str, str] = {}

    async def get(self, thread_id: str) -> str | None:
        return self._runs.get(thread_id)

    async def put(self, thread_id: str, state: str) -> None:
        self._runs.pop(thread_id, None)
        self._runs[thread_id] = state
        while len(self._runs) > MEMORY_LIMIT:
            self._runs.pop(next(iter(self._runs)))

    async def drop(self, thread_id: str) -> None:
        self._runs.pop(thread_id, None)


class DatabasePaused:
    """Paused runs on their conversation's row, so every replica and a restarted server can continue them. A
    conversation with no row (a run started outside the runner) falls back to this process's memory."""

    def __init__(self, db: Any) -> None:
        self.db = db
        self._memory = MemoryPaused()

    async def get(self, thread_id: str) -> str | None:
        from agentcanvas.db.models import Thread

        async with self.db.sessionmaker() as s:
            found = await s.scalar(select(Thread.paused_state).where(Thread.id == thread_id))
        return found if found is not None else await self._memory.get(thread_id)

    async def put(self, thread_id: str, state: str) -> None:
        from agentcanvas.db.models import Thread

        async with self.db.sessionmaker() as s:
            res = await s.execute(update(Thread).where(Thread.id == thread_id).values(paused_state=state))
            await s.commit()
        if res.rowcount == 0:  # type: ignore[attr-defined]
            await self._memory.put(thread_id, state)

    async def drop(self, thread_id: str) -> None:
        from agentcanvas.db.models import Thread

        async with self.db.sessionmaker() as s:
            await s.execute(update(Thread).where(Thread.id == thread_id).values(paused_state=None))
            await s.commit()
        await self._memory.drop(thread_id)


class DatabaseReplies:
    """Remote A2A replies per step (`interop.REPLIES`) in the database, on every replica. A step of a conversation
    with no row (a graph run outside the runner) falls back to this process's memory."""

    def __init__(self, db: Any) -> None:
        from agentcanvas.adapters.langchain.interop import MemoryReplies

        self.db = db
        self._memory = MemoryReplies()

    async def get(self, thread_id: str, step: str) -> list[dict[str, Any]]:
        from agentcanvas.db.models import A2AReply

        async with self.db.sessionmaker() as s:
            row = await s.get(A2AReply, (thread_id, step))
        return list(row.replies) if row is not None else await self._memory.get(thread_id, step)

    async def add(self, thread_id: str, step: str, reply: dict[str, Any]) -> None:
        from agentcanvas.db.models import A2AReply, Thread

        async with self.db.sessionmaker() as s:  # a step runs once at a time: no other writer of its row
            if await s.scalar(select(Thread.id).where(Thread.id == thread_id)) is None:
                await self._memory.add(thread_id, step, reply)
                return
            row = await s.get(A2AReply, (thread_id, step))
            if row is None:
                s.add(A2AReply(thread_id=thread_id, step=step, replies=[reply]))
            else:
                row.replies = [*row.replies, reply]
            try:
                await s.commit()
            except IntegrityError:  # the conversation was deleted meanwhile
                await s.rollback()

    async def drop(self, thread_id: str, step: str) -> None:
        from agentcanvas.db.models import A2AReply

        async with self.db.sessionmaker() as s:
            await s.execute(delete(A2AReply).where(A2AReply.thread_id == thread_id, A2AReply.step == step))
            await s.commit()
        await self._memory.drop(thread_id, step)


STORE: PausedStore = MemoryPaused()


def install(store: PausedStore) -> None:
    """Called by the server at startup (and to put the in-process store back at shutdown)."""
    global STORE  # noqa: PLW0603 - one store per process, chosen by the server
    STORE = store


def store() -> PausedStore:
    return STORE


async def save(thread_id: str, value: Any) -> None:
    """Keep a paused run (a framework's JSON text, or a JSON-able value) for its conversation."""
    await STORE.put(thread_id, json.dumps(value))


async def load(thread_id: str) -> Any:
    """The paused run saved for a conversation, as it was given to `save`; None when there is none."""
    found = await STORE.get(thread_id)
    return None if found is None else json.loads(found)
