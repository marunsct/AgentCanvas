"""F-7: long-term memory has a semantic index when the server names a memory embedding model: Recall and
`search_memory` rank by meaning with `top_k` instead of scanning 1000 items and ranking them by shared words. Without
one nothing changes (tests/test_memory.py)."""

from __future__ import annotations

import os
import uuid
from collections.abc import Sequence

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from test_catalog import cfg
from test_memory import SPACE, recall_wf, remember_wf

from agentcanvas.adapters.langchain import nodes as ac
from agentcanvas.adapters.langchain.interpreter import build_graph
from agentcanvas.adapters.langchain.nodes import Resources
from agentcanvas.adapters.langchain.persistence import MemoryEmbedding, open_persistence, reindex_memory
from agentcanvas.db.session import async_url
from agentcanvas.settings import Settings

TOPICS = [("tea", "coffee", "drink", "beverage"), ("jazz", "piano", "music", "song"), ("flight", "train", "travel")]
URL = os.getenv("TEST_DATABASE_URL") or ""


async def embed(texts: Sequence[str]) -> list[list[float]]:
    """A tiny embedding model that knows synonyms: one dimension per topic, plus one for anything else."""
    out = []
    for t in texts:
        low = t.lower()
        v = [float(sum(w in low for w in topic)) for topic in TOPICS]
        out.append([*v, 0.0 if any(v) else 1.0])
    return out


MODEL = MemoryEmbedding("fake:topics", dims=4, embed=embed)
FACTS = {
    "about:drinks": "likes green tea",
    "about:music": "plays jazz piano",
    "about:trips": "prefers the train",
    **{f"filler:{n}": f"note number {n} about nothing in particular" for n in range(40)},
}


class Spy:
    """A store that notes the `limit` of each search it gets."""

    def __init__(self, store):
        self.store, self.seen = store, []

    def __getattr__(self, name):
        return getattr(self.store, name)

    async def asearch(self, *a, **kw):
        self.seen.append(kw.get("limit", 10))
        return await self.store.asearch(*a, **kw)


async def test_recall_ranks_by_meaning_and_asks_the_store_for_top_k_only():
    async with open_persistence("memory", MODEL) as p:
        store = p.store
        assert store.index_config and store.index_config["dims"] == 4
        for k, v in FACTS.items():
            await store.aput(("prefs", "user", "u1"), k, {"value": v})
        spy = Spy(store)
        found = await ac.recall(spy, ("prefs", "user", "u1"), "which beverage do they enjoy", 2)
        assert found[0].key == "about:drinks"  # no word in common: the keyword fallback cannot find it
        assert spy.seen == [2]  # top_k, not a scan of 1000 items
        assert [i.key for i in await ac.recall(store, ("prefs", "user", "u1"), "", 3)] == [  # empty: the recent ones
            i.key for i in await store.asearch(("prefs", "user", "u1"), limit=3)
        ]


async def test_without_an_index_recall_still_scans_and_ranks_by_words():
    store = InMemoryStore()
    for k, v in FACTS.items():
        await store.aput(("prefs", "user", "u1"), k, {"value": v})
    spy = Spy(store)
    assert await ac.recall(spy, ("prefs", "user", "u1"), "which beverage do they enjoy", 2) == []
    found = await ac.recall(spy, ("prefs", "user", "u1"), "green tea", 2)
    assert [i.key for i in found] == ["about:drinks"] and spy.seen == [1000, 1000]


async def test_the_recall_block_and_the_agent_tool_use_the_index():
    async with open_persistence("memory", MODEL) as p:
        save, look = (
            build_graph(wf, Resources(), checkpointer=InMemorySaver(), store=p.store)
            for wf in (remember_wf(), recall_wf())
        )
        for topic, fact in (
            ("drinks", "likes green tea"),
            ("music", "plays jazz piano"),
            ("trips", "prefers the train"),
        ):
            await save.ainvoke({"topic": topic, "fact": fact}, cfg(topic), context={"user_id": "u1"})
        spy = Spy(p.store)
        look = build_graph(recall_wf(), Resources(), checkpointer=InMemorySaver(), store=spy)
        out = await look.ainvoke({"question": "which beverage do they enjoy"}, cfg("q"), context={"user_id": "u1"})
        assert [m["key"] for m in out["recalled"]][:1] == ["about:drinks"] and spy.seen == [3]  # the block's top_k
        tools = {
            t.name: t for t in ac.memory_tools(space=SPACE["space"], scope="user", workflow_id="w", user_key="user_id")
        }
        assert set(tools) == {"search_memory", "save_memory"}


@pytest.fixture
def indexed_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"memory_embedding": "builtin"})


async def test_the_server_setting_gives_the_store_an_index(indexed_settings):
    from agentcanvas.api.main import create_app

    app = create_app(indexed_settings)
    async with app.router.lifespan_context(app):
        store = app.state.runner.adapter.store
        assert store.index_config and store.index_config["dims"] == 512  # the built-in embedder's size
        org = ("org:org_default", "prefs", "user", "usr_dev")
        await store.aput(org, "about:drinks", {"value": "likes green tea"})
        await store.aput(org, "about:music", {"value": "plays jazz piano"})
        import httpx

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get(
                "/v1/memory/items", params={"namespace": "prefs/user/usr_dev", "q": "green teas", "limit": 1}
            )
        assert [i["key"] for i in r.json()] == ["about:drinks"]  # word forms, by the built-in embedder's vectors


async def test_memories_saved_before_the_index_are_embedded_once(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'm.db'}"
    async with open_persistence(url) as p:  # no index yet
        for k, v in FACTS.items():
            await p.store.aput(("prefs", "user", "u1"), k, {"value": v})
    async with open_persistence(url, MODEL) as p:
        assert p.reindexing is not None
        await p.reindexing
        found = await ac.recall(p.store, ("prefs", "user", "u1"), "which beverage do they enjoy", 1)
        assert [i.key for i in found] == ["about:drinks"]
        assert await reindex_memory(p.store, MODEL, 4) == 0  # the marker records the model
        marker = await p.store.aget(("org:_system", "migrations"), "memory_index")
        assert marker is not None and marker.value == {"model": "fake:topics", "dims": 4}
        assert (await ac.recall(p.store, ("org:_system", "migrations"), "memory_index", 5)) == []  # not embedded itself
    async with open_persistence(url, MODEL) as p:  # restarted: nothing to do
        assert p.reindexing is not None and (await p.reindexing) is None
        assert await reindex_memory(p.store, MODEL, 4) == 0


async def test_an_unknown_model_without_a_key_leaves_memory_on_keywords(tmp_path):
    async with open_persistence("memory", MemoryEmbedding("nosuchprovider:model")) as p:
        assert p.store.index_config is None and p.reindexing is None


@pytest.mark.skipif(not URL.startswith("postgres"), reason="pgvector needs Postgres")
async def test_postgres_memory_index_uses_pgvector_and_survives_a_change_of_size():
    admin = create_async_engine(async_url(URL), isolation_level="AUTOCOMMIT")
    name = f"mem_{uuid.uuid4().hex[:10]}"
    async with admin.connect() as c:
        await c.execute(text(f"CREATE DATABASE {name}"))
    fresh = URL.rsplit("/", 1)[0] + f"/{name}"
    try:
        async with open_persistence(fresh, MODEL) as p:
            for k, v in FACTS.items():
                await p.store.aput(("prefs", "user", "u1"), k, {"value": v})
            found = await ac.recall(p.store, ("prefs", "user", "u1"), "which beverage do they enjoy", 2)
            assert found[0].key == "about:drinks" and found[0].score and found[0].score > 0.5
        engine = create_async_engine(async_url(fresh))
        async with engine.connect() as c:
            indexes = (
                await c.execute(text("SELECT indexdef FROM pg_indexes WHERE tablename = 'store_vectors'"))
            ).scalars()
            assert any("hnsw" in d and "vector_cosine_ops" in d for d in indexes)
        await engine.dispose()
        # a model of another size cannot use the existing column: the server starts, with memory on keywords
        async with open_persistence(fresh, MemoryEmbedding("builtin")) as p:
            assert p.store.index_config is None
            assert [i.key for i in await ac.recall(p.store, ("prefs", "user", "u1"), "green tea", 1)] == [
                "about:drinks"
            ]
        # dropping the vector tables starts the index again: the memories are embedded again whatever the marker says
        engine = create_async_engine(async_url(fresh))
        async with engine.begin() as c:
            await c.execute(text("DROP TABLE store_vectors, vector_migrations"))
        await engine.dispose()
        async with open_persistence(fresh, MODEL) as p:
            assert p.reindexing is not None
            await p.reindexing
            found = await ac.recall(p.store, ("prefs", "user", "u1"), "which beverage do they enjoy", 1)
            assert [i.key for i in found] == ["about:drinks"]
    finally:
        async with admin.connect() as c:
            await c.execute(text(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))
        await admin.dispose()
