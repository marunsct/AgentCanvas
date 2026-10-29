"""G-6: on Postgres with pgvector the knowledge chunks are searched in the database (an HNSW index per embedding
dimension, full-text search for keywords) with the in-process engine's scores and citations; SQLite keeps the
in-process engine. Most tests need Postgres (TEST_DATABASE_URL)."""

from __future__ import annotations

import array
import asyncio
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from agentcanvas import knowledge_search as ks
from agentcanvas.db.session import async_url, run_alembic
from agentcanvas.services import knowledge as kn

URL = os.getenv("TEST_DATABASE_URL") or ""
postgres = pytest.mark.skipif(not URL.startswith("postgres"), reason="pgvector needs Postgres")
P = "/v1/projects/prj_default"
BEFORE = "d8f2b6c0e4a9"  # the revision before the pgvector columns


def _measure(found_by_case: list[tuple[list[dict], list[str]]], k: int = 3) -> dict[str, float]:
    totals = {"recall": 0.0, "mrr": 0.0}
    for found, relevant in found_by_case:
        m = ks.retrieval_metrics([r["source"].removesuffix(".md") for r in found], relevant, k)
        totals = {n: totals[n] + m[n] for n in totals}
    return {n: round(v / len(found_by_case), 3) for n, v in totals.items()}


async def test_the_cap_is_per_engine(app, monkeypatch):
    """The in-process engine holds every chunk in memory and keeps its cap; Postgres searches in place and holds
    far more."""
    monkeypatch.setattr(kn, "MAX_CHUNKS", 2)
    async with app.state.db.sessionmaker() as s:
        svc = kn.KnowledgeService(s)
        in_database = await svc.pgvector() is not None
        assert in_database == URL.startswith("postgres")  # the test database has pgvector, as the image does
        assert await svc.max_chunks() == (kn.MAX_CHUNKS_PG if in_database else 2)
        kb = await svc.create("prj_default", "cap", chunking=kn.Chunking(strategy="paragraphs", size=100))
        three = "\n\n".join(f"Paragraph {n} is about topic {n}, with enough words to fill a chunk." * 2 for n in "abc")
        if in_database:
            assert (await svc.add(kb.id, title="Three", text=three)).chunks >= 3
        else:
            with pytest.raises(kn.InvalidInput, match="at most 2 chunks"):
                await svc.add(kb.id, title="Three", text=three)


@postgres
async def test_postgres_searches_the_sample_in_the_database(app, api_client, monkeypatch):
    """The 50 handbook questions through the database engine: no chunk is loaded into the process, the scores are
    0-1 with citations 1..n, and hybrid still finds the right page in the top 3 for nine questions in ten."""

    def never(*_a, **_k):
        raise AssertionError("the chunks were loaded into the process")

    kb = (await api_client.post(f"{P}/knowledge", json={"sample": "handbook"})).json()
    monkeypatch.setattr(kn.ks, "Index", never)
    cases = kn.sample_eval()
    results: dict[str, list[tuple[list[dict], list[str]]]] = {"hybrid": [], "keyword": [], "semantic": []}
    async with app.state.db.sessionmaker() as s:
        svc = kn.KnowledgeService(s)
        base = await svc.get(kb["id"])
        for case in cases:
            for mode, found in results.items():
                hits = await svc.search(base, case["input"]["question"], mode=mode, top_k=3)
                assert [h["cite"] for h in hits] == list(range(1, len(hits) + 1))
                assert all(0 < h["score"] <= 1 for h in hits)
                assert [h["score"] for h in hits] == sorted((h["score"] for h in hits), reverse=True)
                assert all(h["title"] and h["source"].endswith(".md") and h["text"] for h in hits)
                found.append((hits, case["reference"]["relevant"]))
        keyword_top = [hits[0]["score"] for hits, _ in results["keyword"] if hits]
        assert keyword_top and set(keyword_top) == {1.0}  # relative to the best match
        hybrid, keyword = _measure(results["hybrid"]), _measure(results["keyword"])
        assert hybrid["recall"] >= 0.9 and hybrid["recall"] > keyword["recall"], (hybrid, keyword)
        assert hybrid["mrr"] >= keyword["mrr"], (hybrid, keyword)
        assert await svc.search(base, "the of and", mode="keyword") == []  # stop words only
        laptop = await svc.search(base, "lost laptop", mode="keyword", min_score=0.99)
        assert len(laptop) == 1 and laptop[0]["score"] == 1.0


@postgres
async def test_each_common_dimension_is_searched_through_its_ann_index(app):
    """The nearest-neighbour query uses the expression and predicate of its dimension's partial HNSW index."""
    async with app.state.db.sessionmaker() as s:
        for dims, kind in kn.ANN_INDEXES.items():
            await s.execute(text("SET LOCAL enable_seqscan = off"))
            await s.execute(text("SET LOCAL enable_sort = off"))
            plan = "\n".join(
                r[0]
                for r in await s.execute(
                    text("EXPLAIN " + kn.nearest_sql(dims)), {"kb": "kb_x", "q": kn.literal([0.1] * dims), "k": 5}
                )
            )
            assert f"knowledge_chunk_ann_{dims}" in plan, plan
            assert kind in plan
            await s.rollback()
        assert "CAST(:q AS vector)" in kn.nearest_sql(4096)  # beyond the indexed sizes: searched exactly


@postgres
async def test_a_provider_model_of_another_dimension_is_searched_by_meaning(app):
    """Knowledge bases with different embedding sizes share the table; each is compared only with its own."""

    async def provider(texts):
        return [[1.0 if "laptop" in t.lower() else 0.1, 0.5, 0.2] for t in texts]

    def factory(spec, secrets):
        return kn.builtin_embed if (spec.get("model") or ks.BUILTIN) == ks.BUILTIN else provider

    async with app.state.db.sessionmaker() as s:
        svc = kn.KnowledgeService(s, embedder=factory)
        small = await svc.create("prj_default", "three-d", embedding=kn.Embedding(model="fake:tiny"))
        builtin = await svc.create("prj_default", "builtin")
        for kb in (small, builtin):
            await svc.add(kb.id, title="IT", text="Report a lost laptop to IT.")
            await svc.add(kb.id, title="Leave", text="Everyone gets 25 vacation days.")
        found = await svc.search(await svc.get(small.id), "my notebook computer is gone: laptop", mode="semantic")
        assert found[0]["title"] == "IT" and found[0]["score"] > 0.9
        found = await svc.search(await svc.get(builtin.id), "vacation", mode="semantic")
        assert found[0]["title"] == "Leave"
        assert {c["id"] for c in (await svc.index(await svc.get(small.id))).chunks} == {
            r["id"] for r in await svc.search(await svc.get(small.id), "laptop vacation", mode="semantic", top_k=10)
        }


@postgres
async def test_the_migration_moves_existing_chunks_into_pgvector_and_back():
    admin = create_async_engine(async_url(URL), isolation_level="AUTOCOMMIT")
    name = f"kn_{uuid.uuid4().hex[:10]}"
    async with admin.connect() as c:
        await c.execute(text(f"CREATE DATABASE {name}"))
    fresh = async_url(URL).rsplit("/", 1)[0] + f"/{name}"
    engine = create_async_engine(fresh)
    try:
        await asyncio.to_thread(run_alembic, fresh, "upgrade", BEFORE)
        vector = ks.builtin_embed(["Report a lost laptop to IT."])[0]
        async with engine.begin() as c:
            await c.execute(text("INSERT INTO organization (id, name) VALUES ('org_m', 'M')"))
            await c.execute(text("INSERT INTO project (id, org_id, name) VALUES ('prj_m', 'org_m', 'M')"))
            await c.execute(
                text(
                    "INSERT INTO knowledge_base (id, project_id, name, embedding, chunking) VALUES ('kb_m', 'prj_m', "
                    """'m', '{"model": "builtin"}', '{"strategy": "markdown", "size": 800, "overlap": 100}')"""
                )
            )
            await c.execute(
                text(
                    "INSERT INTO knowledge_document (id, kb_id, name, source, content, chunks) VALUES "
                    "('kdoc_m', 'kb_m', 'it-policy.md', 'upload', '\n# Lost devices\n\nReport a lost laptop.', 1)"
                )
            )
            await c.execute(
                text(
                    "INSERT INTO knowledge_chunk (id, kb_id, doc_id, ordinal, text, vector) VALUES "
                    "('kdoc_m_0', 'kb_m', 'kdoc_m', 0, 'Report a lost laptop.', :v)"
                ),
                {"v": array.array("f", vector).tobytes()},
            )
        await asyncio.to_thread(run_alembic, fresh, "upgrade", "head")
        async with engine.connect() as c:
            row = (
                await c.execute(
                    text(
                        "SELECT dims, CAST(embedding AS text) AS e, CAST(tsv AS text) AS tsv, vector "
                        "FROM knowledge_chunk"
                    )
                )
            ).one()
            assert row.dims == 512 and row.vector is None
            assert [round(v, 5) for v in kn.json.loads(row.e)] == [round(v, 5) for v in vector]
            assert "'devices'" in row.tsv and "'laptop'" in row.tsv  # the title is searched too
        await asyncio.to_thread(run_alembic, fresh, "downgrade", BEFORE)
        async with engine.connect() as c:
            back = (await c.execute(text("SELECT vector FROM knowledge_chunk"))).scalar()
            assert kn.unpack(back) == pytest.approx(vector, abs=1e-6)
    finally:
        await engine.dispose()
        async with admin.connect() as c:
            await c.execute(text(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))
        await admin.dispose()
