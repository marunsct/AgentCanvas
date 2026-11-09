"""Checkpointer + store bindings for the runner (spec doc 08 §2; MVP: memory, SQLite, Postgres).

Long-term memory has a semantic index when the server names a memory embedding model (`MemoryEmbedding`, F-7): the
store embeds what it saves and `Recall` / `search_memory` rank by meaning with `top_k`, instead of scanning up to 1000
items and ranking them in Python. The vector column has one size, so the model is the server's, not each project's.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from agentcanvas.log import get_logger

log = get_logger(__name__)


@dataclass
class Persistence:
    checkpointer: Any
    store: Any
    kind: str
    # while memories saved before the index existed (or under another model) are being embedded
    reindexing: asyncio.Task[None] | None = None


@dataclass
class MemoryEmbedding:
    """The model behind long-term memory's semantic index: "builtin" (offline, 512 dimensions) or
    "<provider>:<model>" (keys from the provider's usual environment variable). `dims` 0: known models and the
    built-in embedder are looked up, any other is measured by embedding a word once at startup."""

    model: str
    dims: int = 0
    base_url: str | None = None
    embed: Callable[[Sequence[str]], Awaitable[list[list[float]]]] | None = None  # replaces the model (tests)


KNOWN_DIMS = {
    "openai:text-embedding-3-small": 1536, "openai:text-embedding-3-large": 3072, "mistralai:mistral-embed": 1024,
    "google_genai:models/gemini-embedding-001": 3072, "ollama:nomic-embed-text": 768,
}  # fmt: skip
MEMORY_MARKER = (("org:_system", "migrations"), "memory_index")  # {"model", "dims"} the items were last embedded with


async def memory_index(memory: MemoryEmbedding | None) -> dict[str, Any] | None:
    """The store's `index` argument for `memory`, or None for a store without semantic search."""
    if memory is None or not memory.model:
        return None
    from agentcanvas.adapters.langchain.nodes import knowledge_embedder

    spec: dict[str, Any] = {"model": memory.model}
    if memory.base_url:
        spec["base_url"] = memory.base_url
    try:
        embed = memory.embed or knowledge_embedder(spec, {})
    except Exception as exc:  # noqa: BLE001 - unknown provider or a missing package: memory stays keyword-ranked
        log.warning("memory.index_disabled", model=memory.model, error=str(exc))
        return None
    dims = memory.dims
    if not dims and memory.model == "builtin":
        from agentcanvas import knowledge_search as ks

        dims = ks.DIM
    dims = dims or KNOWN_DIMS.get(memory.model, 0)
    if not dims:
        try:
            dims = len((await embed(["dimension"]))[0])
        except Exception as exc:  # noqa: BLE001 - no key, no server: memory stays keyword-ranked
            log.warning("memory.index_disabled", model=memory.model, error=str(exc))
            return None
    return {"dims": dims, "embed": embed, "fields": ["$"]}


async def reindex_memory(store: Any, memory: MemoryEmbedding, dims: int, force: bool = False) -> int:
    """Embed the items saved before the index existed or under another model: each is saved again as it is (the
    store embeds on `put`; it refreshes the item's `updated_at`, which the memory retention counts from). A marker
    records the model, so this runs again only when the model changes (or `force`: the vectors table is new). Returns
    the number of items saved again."""
    state = {"model": memory.model, "dims": dims}
    marker = await store.aget(*MEMORY_MARKER)
    if marker is not None and marker.value == state and not force:
        return 0
    done, offset = 0, 0
    while page := await store.alist_namespaces(limit=500, offset=offset):
        offset += len(page)
        for ns in page:
            if ns and str(ns[0]) == "org:_system":
                continue
            seen = 0
            while batch := await store.asearch(ns, limit=200, offset=seen):  # matches deeper namespaces too
                seen += len(batch)
                for item in batch:
                    if tuple(item.namespace) == tuple(ns):
                        await store.aput(item.namespace, item.key, item.value)
                        done += 1
    await store.aput(*MEMORY_MARKER, state, index=False)
    log.info("memory.reindexed", model=memory.model, items=done)
    return done


def _background(
    store: Any, memory: MemoryEmbedding | None, index: dict[str, Any] | None, force: bool = False
) -> asyncio.Task[None] | None:
    if memory is None or index is None:
        return None

    async def run() -> None:
        try:
            await reindex_memory(store, memory, index["dims"], force)
        except Exception as exc:  # noqa: BLE001 - memories stay searchable by keywords until the next start
            log.warning("memory.reindex_failed", error=str(exc))

    return asyncio.create_task(run(), name="memory-reindex")


async def _stop(task: asyncio.Task[None] | None) -> None:
    if task is not None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def _vector_dims(conn: Any) -> int | None:
    """The size of an existing Postgres `store_vectors.embedding` column (None: no such table yet)."""
    sql = "SELECT atttypmod FROM pg_attribute WHERE attrelid = to_regclass('store_vectors') AND attname = 'embedding'"
    row = await (await conn.execute(sql)).fetchone()
    return int(row["atttypmod"]) if row and row["atttypmod"] > 0 else None


def _sqlite_path(url: str) -> str:
    """sqlite+aiosqlite:///./agentcanvas.db -> ./agentcanvas.checkpoints.db (separate file avoids lock contention)."""
    raw = url.split(":///", 1)[1]
    if raw in ("", ":memory:"):
        return ":memory:"
    p = Path(raw)
    return str(p.with_name(p.stem + ".checkpoints" + (p.suffix or ".db")))


SETUP_LOCK = 0x6163_7365_7475_70  # Postgres advisory lock id held while migrating the checkpoint/store tables
POOL_SIZE = int(os.getenv("AGENTCANVAS_PERSISTENCE_POOL_SIZE", "20"))  # checkpoint/store connections per process


def checkpoint_serde() -> Any:
    """AES-encrypted checkpoints when `LANGGRAPH_AES_KEY` (16, 24 or 32 bytes) is set (S14); None = plain JSON.
    Checkpoints written before the key was set stay readable."""
    if not os.getenv("LANGGRAPH_AES_KEY"):
        return None
    from langgraph.checkpoint.serde.encrypted import EncryptedSerializer

    return EncryptedSerializer.from_pycryptodome_aes()


async def _try_lock(conn: Any) -> bool:
    row = await (await conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (SETUP_LOCK,))).fetchone()
    return bool(row["ok"])


@asynccontextmanager
async def open_persistence(
    database_url: str | None, memory: MemoryEmbedding | None = None
) -> AsyncIterator[Persistence]:
    index = await memory_index(memory)
    if not database_url or database_url == "memory":
        store = InMemoryStore(index=index)  # type: ignore[arg-type]
        p = Persistence(InMemorySaver(), store, "memory", _background(store, memory, index))
        try:
            yield p
        finally:
            await _stop(p.reindexing)
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
            if index:  # pgvector: an HNSW index over cosine distance (halfvec beyond the 2000 dimensions of vector)
                vector_type = "halfvec" if index["dims"] > 2000 else "vector"
                index = {
                    **index, "distance_type": "cosine",
                    "ann_index_config": {"kind": "hnsw", "vector_type": vector_type},
                }  # fmt: skip
            st = AsyncPostgresStore(conn=pool, index=index)  # type: ignore[arg-type]
            # replicas starting together would run the migrations at once and deadlock on CREATE INDEX
            # CONCURRENTLY: one instance migrates at a time, the others then find nothing to do. The others poll
            # with a try-lock: a session blocked in pg_advisory_lock is a running statement, which CREATE INDEX
            # CONCURRENTLY would wait for.
            have: int | None = None
            async with pool.connection() as lock:
                while not await _try_lock(lock):
                    await asyncio.sleep(0.2)
                try:
                    await saver.setup()
                    have = await _vector_dims(lock) if index else None
                    if index and have not in (None, index["dims"]):
                        log.error(
                            "memory.index_disabled", reason=f"the store's vectors have {have} dimensions, not "
                            f"{index['dims']}: drop the store_vectors table to change the memory embedding model",
                        )  # fmt: skip
                        index = None
                        st = AsyncPostgresStore(conn=pool)  # type: ignore[arg-type]
                    try:
                        await st.setup()
                    except Exception as exc:  # most likely pgvector is not installed
                        if not index:
                            raise
                        log.error("memory.index_disabled", reason=f"the vector tables cannot be created: {exc}")
                        index = None
                        st = AsyncPostgresStore(conn=pool)  # type: ignore[arg-type]
                        await st.setup()
                finally:
                    await lock.execute("SELECT pg_advisory_unlock(%s)", (SETUP_LOCK,))
            p = Persistence(saver, st, "postgres", _background(st, memory, index, force=have is None))
            try:
                yield p
            finally:
                await _stop(p.reindexing)
    elif database_url.startswith("sqlite"):
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from langgraph.store.sqlite.aio import AsyncSqliteStore

        path = _sqlite_path(database_url)
        async with (
            AsyncSqliteSaver.from_conn_string(path) as saver,
            AsyncSqliteStore.from_conn_string(path, index=index) as st,  # type: ignore[arg-type]
        ):
            if (serde := checkpoint_serde()) is not None:
                saver.serde = serde
            await saver.setup()
            await st.setup()
            p = Persistence(saver, st, "sqlite", _background(st, memory, index))
            try:
                yield p
            finally:
                await _stop(p.reindexing)
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
