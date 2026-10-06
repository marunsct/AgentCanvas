"""Checkpointer + store bindings for the runner (spec doc 08 §2; MVP: memory, SQLite, Postgres)."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore


@dataclass
class Persistence:
    checkpointer: Any
    store: Any
    kind: str


def _sqlite_path(url: str) -> str:
    """sqlite+aiosqlite:///./agentcanvas.db -> ./agentcanvas.checkpoints.db (separate file avoids lock contention)."""
    raw = url.split(":///", 1)[1]
    if raw in ("", ":memory:"):
        return ":memory:"
    p = Path(raw)
    return str(p.with_name(p.stem + ".checkpoints" + (p.suffix or ".db")))


POOL_SIZE = int(os.getenv("AGENTCANVAS_PERSISTENCE_POOL_SIZE", "20"))  # checkpoint/store connections per process


def checkpoint_serde() -> Any:
    """AES-encrypted checkpoints when `LANGGRAPH_AES_KEY` (16, 24 or 32 bytes) is set (S14); None = plain JSON.
    Checkpoints written before the key was set stay readable."""
    if not os.getenv("LANGGRAPH_AES_KEY"):
        return None
    from langgraph.checkpoint.serde.encrypted import EncryptedSerializer

    return EncryptedSerializer.from_pycryptodome_aes()


@asynccontextmanager
async def open_persistence(database_url: str | None) -> AsyncIterator[Persistence]:
    if not database_url or database_url == "memory":
        yield Persistence(InMemorySaver(), InMemoryStore(), "memory")
    elif database_url.startswith(("postgresql", "postgres")):
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from langgraph.store.postgres.aio import AsyncPostgresStore
        from psycopg.rows import dict_row
        from psycopg_pool import AsyncConnectionPool

        conn = "postgresql://" + database_url.split("://", 1)[1]
        # a pool, not one shared connection: concurrent runs would queue behind each other's checkpoint writes
        # (M8-T5). The same settings `from_conn_string` uses, which also keep transaction pooling (PgBouncer) safe.
        kwargs = {"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row}
        async with AsyncConnectionPool(conn, min_size=1, max_size=POOL_SIZE, kwargs=kwargs, open=False) as pool:
            saver = AsyncPostgresSaver(conn=pool, serde=checkpoint_serde())  # type: ignore[arg-type]
            st = AsyncPostgresStore(conn=pool)  # type: ignore[arg-type]
            await saver.setup()
            await st.setup()
            yield Persistence(saver, st, "postgres")
    elif database_url.startswith("sqlite"):
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from langgraph.store.sqlite.aio import AsyncSqliteStore

        path = _sqlite_path(database_url)
        async with AsyncSqliteSaver.from_conn_string(path) as saver, AsyncSqliteStore.from_conn_string(path) as st:
            if (serde := checkpoint_serde()) is not None:
                saver.serde = serde
            await saver.setup()
            await st.setup()
            yield Persistence(saver, st, "sqlite")
    else:
        raise ValueError(f"unsupported persistence URL '{database_url}'")


async def copy_thread(checkpointer: Any, src: str, dst: str) -> None:
    """Copy every checkpoint (all namespaces, ids preserved) and pending write of `src` into `dst`.

    `BaseCheckpointSaver.acopy_thread` exists but none of the memory/SQLite/Postgres savers implement it
    (langgraph-checkpoint 2026-09, FAQ P-14), so we replay the thread through the public saver contract.
    Limitation: threads that use DeltaChannel need their full parent chain, which this copy preserves.
    """
    try:
        await checkpointer.acopy_thread(src, dst)
        return
    except NotImplementedError:
        pass
    tuples = [t async for t in checkpointer.alist({"configurable": {"thread_id": src}})]
    for t in sorted(tuples, key=lambda t: t.config["configurable"]["checkpoint_id"]):
        conf = t.config["configurable"]
        ns = conf.get("checkpoint_ns", "")
        target: dict[str, Any] = {"thread_id": dst, "checkpoint_ns": ns}
        if t.parent_config:
            target["checkpoint_id"] = t.parent_config["configurable"]["checkpoint_id"]
        await checkpointer.aput(
            {"configurable": target}, t.checkpoint, t.metadata, dict(t.checkpoint.get("channel_versions", {}))
        )
        by_task: dict[str, list[tuple[str, Any]]] = {}
        for task_id, channel, value in t.pending_writes or []:
            by_task.setdefault(task_id, []).append((channel, value))
        for task_id, writes in by_task.items():
            cfg = {"configurable": {"thread_id": dst, "checkpoint_ns": ns, "checkpoint_id": conf["checkpoint_id"]}}
            await checkpointer.aput_writes(cfg, writes, task_id)
