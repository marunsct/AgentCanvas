"""S24 Knowledge Studio: knowledge bases, ingestion, chunking, hybrid search with citations, the Search knowledge
block on the server and in exports, and retrieval scores."""

from __future__ import annotations

import io
import json
import zipfile

import httpx
import pytest

from agentcanvas import knowledge_search as ks
from agentcanvas.evaluation import Observation, Spec, score
from agentcanvas.services import knowledge as kn

P = "/v1/projects/prj_default"


# ------------------------------------------------------------------ search core ----
def test_chunking_strategies():
    text = "# Leave\n\nYou get 25 days.\n\nCarry over 5.\n\n## Sick leave\n\nCall in before 10am. Bring a note."
    md = ks.chunk_text(text, strategy="markdown", size=200)
    assert [c["heading"] for c in md] == ["Leave", "Sick leave"]
    assert md[0]["text"] == "You get 25 days.\nCarry over 5."
    paras = ks.chunk_text(text, strategy="paragraphs", size=100)
    assert all(len(c["text"]) <= 100 for c in paras) and paras[0]["heading"] is None
    sentences = ks.chunk_text("One. Two! Three? " * 40, strategy="sentences", size=100)
    assert len(sentences) > 3 and all(len(c["text"]) <= 100 for c in sentences)
    fixed = ks.chunk_text("x" * 1000, strategy="fixed", size=400, overlap=100, page=3)
    assert [len(c["text"]) for c in fixed] == [400, 400, 400, 100] and fixed[0]["page"] == 3
    assert ks.chunk_text("word " * 500, strategy="paragraphs", size=300)  # a long paragraph is split at words


def test_markdown_headings_are_parsed_in_linear_time():
    """A heading line with a long run of spaces made the old pattern backtrack cubically (seconds per line)."""
    import time

    started = time.perf_counter()
    assert ks.chunk_text("# a" + " " * 40_000 + "x\nbody", strategy="markdown")[0]["text"] == "body"
    ks.chunk_text("## a" + " #" * 20_000 + " " * 20_000 + "\n# b" + "\t" * 40_000 + "y", strategy="markdown")
    assert time.perf_counter() - started < 1.0
    text = "# Title ##\nbody\n   ## C# tips #\nmore\n#not a heading\n    # indented code"
    md = ks.chunk_text(text, strategy="markdown", size=200)
    assert [(c["heading"], c["text"]) for c in md] == [
        ("Title", "body"),
        ("C# tips", "more\n#not a heading\n    # indented code"),
    ]


def test_word_zip_bombs_are_refused_before_parsing(monkeypatch):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", b"\0" * (2 * 1024 * 1024))
    monkeypatch.setattr(kn, "MAX_DOCX_UNZIPPED", 1024 * 1024)
    with pytest.raises(Exception, match="unpacks to more than 1 MB"):
        kn.extract("bomb.docx", buf.getvalue())


def test_builtin_embedder_matches_word_forms_and_typos():
    a, b, c = ks.builtin_embed(["reimbursement of expenses", "expense reimbursed", "parental leave for fathers"])
    assert len(a) == ks.DIM and abs(sum(x * x for x in a) - 1) < 1e-3
    assert ks.cosine(a, b) > ks.cosine(a, c) + 0.2
    assert ks.builtin_embed(["vacaton"]) and ks.cosine(*ks.builtin_embed(["vacaton days", "vacation days"])) > 0.6
    assert ks.builtin_embed(["same"]) == ks.builtin_embed(["same"])  # deterministic: exports search the same way


def _index() -> ks.Index:
    docs = {
        "vacation": "Everyone gets 25 vacation days a year; up to 5 carry over.",
        "laptops": "New hires get a laptop on day one. Report a lost laptop to IT.",
        "pension": "The company adds 5% of salary to your pension plan.",
    }
    chunks = [
        {"id": k, "doc_id": k, "title": k.title(), "source": f"{k}.md", "text": t, "page": None, "heading": None}
        for k, t in docs.items()
    ]
    for c, v in zip(chunks, ks.builtin_embed([ks.embed_text(c) for c in chunks]), strict=True):
        c["vector"] = v
    return ks.Index(chunks)


def test_search_modes_scores_and_citations():
    index = _index()
    for mode in ks.MODES:
        found = ks.search(index, "how many vacation days", mode=mode, top_k=2)
        assert found[0]["id"] == "vacation", mode
        assert [r["cite"] for r in found] == list(range(1, len(found) + 1))
    assert ks.search(index, "zzz qqq", mode="keyword") == []  # nothing matches: nothing returned
    assert ks.search(index, "laptop", mode="keyword", min_score=99) == []
    assert ks.sources(ks.search(index, "pension", top_k=1)) == [
        {"cite": 1, "title": "Pension", "source": "pension.md", "page": None}
    ]
    with pytest.raises(ValueError, match="unknown search mode"):
        ks.search(index, "x", mode="fuzzy")


def test_scores_are_on_one_scale_in_every_mode():
    """E-3: hybrid scores were reciprocal-rank sums (at most 0.033), so a minimum score of 0.1 found nothing; BM25
    scores had no upper bound."""
    index = _index()
    for mode in ks.MODES:
        found = ks.search(index, "lost laptop", mode=mode)
        assert found and all(0 < r["score"] <= 1 for r in found), (mode, found)
        assert ks.search(index, "lost laptop", mode=mode, min_score=0.1)[0]["id"] == "laptops", mode
    assert ks.search(index, "lost laptop", mode="keyword")[0]["score"] == 1.0  # the best match
    assert ks.search(index, "lost laptop", mode="hybrid")[0]["score"] == 1.0  # first in both rankings


def test_vectors_of_another_model_are_not_compared():
    """E-2: a 1536-d query zipped with 512-d chunk vectors gave noise; such a search is by keywords instead."""
    index = _index()
    other = [0.03] * 1536
    for mode in ("semantic", "hybrid"):
        assert ks.search(index, "lost laptop", mode=mode, query_vector=other) == ks.search(
            index, "lost laptop", mode="keyword"
        )
    with pytest.raises(ValueError):
        ks.cosine([1.0, 0.0], [1.0, 0.0, 0.0])


async def test_a_stale_knowledge_base_is_searched_by_keywords_until_it_is_reindexed(app):
    """E-2: after a change of embedding model the old vectors were compared with a query embedded by the new model,
    and the export shipped them under the new model's name."""
    calls: list[list[str]] = []

    async def provider(texts):
        calls.append(list(texts))
        return [[float(len(t) % 7), 1.0, 0.5] for t in texts]

    def factory(spec, secrets):
        return kn.builtin_embed if (spec.get("model") or ks.BUILTIN) == ks.BUILTIN else provider

    async with app.state.db.sessionmaker() as s:
        svc = kn.KnowledgeService(s, embedder=factory)
        kb = await svc.create("prj_default", "stale-test")
        for title, text in (("Leave", "Everyone gets 25 vacation days."), ("IT", "Report a lost laptop to IT.")):
            await svc.add(kb.id, title=title, text=text)
        kb = await svc.update(kb.id, embedding=kn.Embedding(model="fake:embed-3"))
        assert kb.stale
        for mode in ks.MODES:
            found = await svc.search(kb, "lost laptop", mode=mode)
            assert [r["title"] for r in found] == ["IT"]
            assert found == await svc.search(kb, "lost laptop", mode="keyword")
        assert calls == []  # the query is not embedded with a model the vectors did not come from
        assert '"vector"' not in await svc.export(kb)
        kb = await svc.reindex(kb.id)
        assert not kb.stale and '"vector"' in await svc.export(kb)
        calls.clear()
        await svc.search(kb, "lost laptop", mode="semantic")
        assert calls == [["lost laptop"]]


def test_retrieval_metrics():
    m = ks.retrieval_metrics(["a", "x", "b", "y"], ["a", "b"], k=3)
    assert m == {"recall": 1.0, "precision": 2 / 3, "hit": 1.0, "mrr": 1.0}
    assert ks.retrieval_metrics(["x", "a"], ["a"], k=2)["mrr"] == 0.5
    assert ks.retrieval_metrics([], ["a"], k=3) == {"recall": 0.0, "precision": 0.0, "hit": 0.0, "mrr": 0.0}


def test_jsonl_round_trip_recomputes_builtin_vectors():
    index = _index()
    text = ks.dump_jsonl("hr", {"model": "builtin"}, index.chunks)
    assert '"vector"' not in text  # recomputed on load: smaller exports
    header, chunks = ks.load_jsonl(text)
    assert header["knowledge_base"] == "hr" and chunks[0]["vector"] == index.chunks[0]["vector"]
    provider = ks.dump_jsonl("hr", {"model": "openai:text-embedding-3-small"}, index.chunks)
    assert '"vector"' in provider
    with pytest.raises(ValueError, match="not a knowledge base file"):
        ks.load_jsonl('{"id": 1}\n')


# --------------------------------------------------------------------- extraction ----
def _pdf(pages: list[str]) -> bytes:
    """A minimal PDF with one line of text per page."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", ""]
    kids = []
    for i, text in enumerate(pages):
        page_no, content_no = 4 + 2 * i, 5 + 2 * i
        kids.append(f"{page_no} 0 R")
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET"
        objs += [
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> "
            f"/Contents {content_no} 0 R >>",
            f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        ]
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>"
    objs.insert(2, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out, offsets = b"%PDF-1.4\n", []
    for n, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def _docx() -> bytes:
    import docx

    d = docx.Document()
    d.add_heading("Travel policy", level=1)
    d.add_paragraph("Book flights through the travel portal at least two weeks ahead.")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def test_extract_files():
    title, text = kn.extract("guide.pdf", _pdf(["Pension plan basics", "Second page about laptops"]))
    assert title == "guide" and text.split("\f") == ["Pension plan basics", "Second page about laptops"]
    title, text = kn.extract("travel.docx", _docx())
    assert "# Travel policy" in text and "travel portal" in text
    html = "<html><head><title>Help</title><script>evil()</script></head>"
    html += "<body><h2>Refunds</h2><p>Within 30 days.</p></body></html>"
    title, text = kn.extract("help.html", html.encode())
    assert title == "Help" and "evil" not in text and "## Refunds" in text and "Within 30 days." in text
    assert kn.extract("notes.md", b"# Notes\n\nhello") == ("Notes", "# Notes\n\nhello")
    with pytest.raises(Exception, match="unsupported file type"):
        kn.extract("tool.exe", b"MZ")
    with pytest.raises(Exception, match="binary"):
        kn.extract("data.txt", b"\x00\x01\x02")
    with pytest.raises(Exception, match="could not be read"):
        kn.extract("broken.pdf", b"not a pdf")


# ---------------------------------------------------------------------------- API ----
async def new_kb(api, name: str = "docs", **body) -> dict:
    r = await api.post(f"{P}/knowledge", json={"name": name, **body})
    assert r.status_code == 201, r.text
    return r.json()


async def test_concurrent_changes_each_bump_the_version(app, api_client):
    """Two changes made from stale copies both count: `version` is bumped in SQL, so no search cache keeps an index
    one of them made stale."""
    kb = await new_kb(api_client)
    async with app.state.db.sessionmaker() as s1, app.state.db.sessionmaker() as s2:
        a, b = kn.KnowledgeService(s1), kn.KnowledgeService(s2)
        start = (await a.get(kb["id"])).version
        assert (await b.get(kb["id"])).version == start  # both hold the same version
        await a.update(kb["id"], description="one")
        after = await b.update(kb["id"], description="two")
        assert after.version == start + 2
    async with app.state.db.sessionmaker() as s:
        assert (await kn.KnowledgeService(s).get(kb["id"])).version == start + 2


async def test_knowledge_base_lifecycle(api_client):
    kb = await new_kb(api_client, description="Team docs")
    assert kb["chunking"] == {"strategy": "markdown", "size": 800, "overlap": 100}
    assert kb["embedding"] == {"model": "builtin"} and kb["documents"] == 0 and not kb["stale"]
    assert (await api_client.post(f"{P}/knowledge", json={"name": "docs"})).status_code == 409
    assert (await api_client.post(f"{P}/knowledge", json={"name": "Bad Name"})).status_code == 422
    r = await api_client.post(f"{P}/knowledge", json={"name": "other", "embedding": {"model": "nope:model"}})
    assert r.status_code == 422 and "cannot be used" in r.json()["detail"]

    k = f"/v1/knowledge/{kb['id']}"
    files = [
        ("files", ("leave.md", b"# Leave\n\nEveryone gets 25 vacation days a year.", "text/markdown")),
        (
            "files",
            ("guide.pdf", _pdf(["Pension plan basics", "Laptops are replaced every three years"]), "application/pdf"),
        ),
        ("files", ("travel.docx", _docx(), "application/octet-stream")),
    ]
    r = await api_client.post(f"{k}/documents", files=files)
    assert r.status_code == 201, r.text
    docs = r.json()
    assert [d["name"] for d in docs] == ["leave.md", "guide.pdf", "travel.docx"] and all(d["chunks"] for d in docs)
    r = await api_client.post(f"{k}/documents/text", json={"title": "Canteen", "text": "Lunch is served from noon."})
    assert r.status_code == 201 and r.json()["source"] == "text"
    again = await api_client.post(f"{k}/documents/text", json={"title": "Copy", "text": "Lunch is served from noon."})
    assert again.status_code == 409  # the same text twice
    bad = await api_client.post(f"{k}/documents", files=[("files", ("x.exe", b"MZ", "application/octet-stream"))])
    assert bad.status_code == 422

    chunks = (await api_client.get(f"{k}/documents/{docs[1]['id']}/chunks")).json()
    assert [c["page"] for c in chunks] == [1, 2]
    r = await api_client.post(f"{k}/search", json={"query": "when are laptops replaced", "top_k": 2})
    assert r.status_code == 200, r.text
    top = r.json()["results"][0]
    assert top["source"] == "guide.pdf" and top["page"] == 2 and top["cite"] == 1 and top["chunk_id"]
    kw = (await api_client.post(f"{k}/search", json={"query": "vacation", "mode": "keyword"})).json()["results"]
    assert kw[0]["title"] == "Leave"

    patched = (await api_client.patch(k, json={"chunking": {"strategy": "fixed", "size": 100, "overlap": 20}})).json()
    assert patched["stale"] and patched["version"] > kb["version"]
    reindexed = (await api_client.post(f"{k}/reindex")).json()
    assert not reindexed["stale"] and reindexed["chunks"] >= patched["chunks"]
    assert reindexed["documents"] == 4

    assert (await api_client.delete(f"{k}/documents/{docs[0]['id']}")).status_code == 204
    assert len((await api_client.get(f"{k}/documents")).json()) == 3
    listed = (await api_client.get(f"{P}/knowledge")).json()
    assert [x["name"] for x in listed] == ["docs"] and listed[0]["documents"] == 3
    assert (await api_client.delete(k)).status_code == 204
    assert (await api_client.get(k)).status_code == 404


async def test_web_pages_are_fetched_safely(app, api_client):
    kb = await new_kb(api_client, "web")
    k = f"/v1/knowledge/{kb['id']}"
    # without a test transport the real resolver runs: internal addresses are refused before any request
    for url in ("http://127.0.0.1:8000/admin", "http://169.254.169.254/latest/meta-data", "file:///etc/passwd"):
        r = await api_client.post(f"{k}/documents/url", json={"url": url})
        assert r.status_code == 422, (url, r.text)

    def serve(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "/faq"})
        if request.url.path == "/faq":
            body = "<html><title>FAQ</title><body><p>Shipping takes three days.</p></body></html>"
            return httpx.Response(200, text=body, headers={"content-type": "text/html; charset=utf-8"})
        if request.url.path == "/image.png":
            return httpx.Response(200, content=b"\x89PNG", headers={"content-type": "image/png"})
        return httpx.Response(404)

    app.state.knowledge_transport = httpx.MockTransport(serve)
    r = await api_client.post(f"{k}/documents/url", json={"url": "https://shop.example/old"})
    assert r.status_code == 201, r.text
    assert r.json()["url"] == "https://shop.example/faq" and r.json()["name"] == "FAQ"
    assert (
        await api_client.post(f"{k}/documents/url", json={"url": "https://shop.example/image.png"})
    ).status_code == 422
    assert (await api_client.post(f"{k}/documents/url", json={"url": "https://shop.example/gone"})).status_code == 422


async def test_viewers_search_but_do_not_change(app, api_client):
    from agentcanvas.api.deps import CurrentUser, current_user

    kb = await new_kb(api_client, "ro")
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="usr_dev", org_id="org_default", role="viewer", memberships={"org_default": "viewer"}
    )
    try:
        assert (await api_client.post(f"/v1/knowledge/{kb['id']}/search", json={"query": "x"})).status_code == 200
        r = await api_client.post(f"/v1/knowledge/{kb['id']}/documents/text", json={"title": "t", "text": "x"})
        assert r.status_code == 403
    finally:
        app.dependency_overrides.pop(current_user)


# ---------------------------------------------------- the block, exports, retrieval scores ----
def search_flow(wf_id: str, mode: str = "hybrid", kb: str = "handbook") -> dict:
    return {
        "ir_version": "1.1", "id": wf_id, "name": "Search",
        "state": {"channels": [{"name": "question", "io": "in"},
                               {"name": "passages", "io": "out", "type": {"type": "array"}},
                               {"name": "sources", "io": "out", "type": {"type": "array"}}]},
        "nodes": [{"id": "n_s", "type": "knowledge.search", "name": "search",
                   "config": {"knowledge_base": kb, "query": "{{ state.question }}", "mode": mode, "top_k": 3,
                              "output": "passages", "sources_output": "sources"}}],
        "edges": [{"id": "e1", "kind": "flow", "source": {"node": "START"}, "target": {"node": "n_s"}},
                  {"id": "e2", "kind": "flow", "source": {"node": "n_s"}, "target": {"node": "END"}}],
    }  # fmt: skip


async def run(api, wf: str, input: dict) -> dict:
    text = (await api.post(f"/v1/workflows/{wf}/runs/stream", json={"input": input})).text
    events = [json.loads(ln[6:]) for ln in text.splitlines() if ln.startswith("data: ")]
    assert events[-1]["type"] == "RUN_FINISHED", events[-1]
    return events[-1]["output"]


async def make_search_flow(api, mode: str = "hybrid", kb: str = "handbook") -> str:
    wf = (await api.post(f"{P}/workflows", json={"name": "Search"})).json()
    r = await api.put(
        f"/v1/workflows/{wf['id']}", json={"ir": search_flow(wf["id"], mode, kb)}, headers={"If-Match": "1"}
    )
    assert r.status_code == 200, r.text
    assert not [d for d in r.json()["diagnostics"] if d["severity"] == "error"], r.json()["diagnostics"]
    return wf["id"]


async def test_search_block_runs_on_the_server_and_ships_in_exports(api_client, tmp_path, monkeypatch):
    sample = await api_client.post(f"{P}/knowledge", json={"sample": "handbook"})
    assert sample.status_code == 201, sample.text
    assert sample.json()["documents"] == 22
    wf = await make_search_flow(api_client)
    out = await run(api_client, wf, {"question": "How many vacation days?"})
    assert out["passages"][0]["source"] == "vacation-policy.md" and out["passages"][0]["cite"] == 1
    assert out["sources"][0] == {"cite": 1, "title": "Vacation policy", "source": "vacation-policy.md", "page": None}

    missing = await make_search_flow(api_client, kb="nothing-here")
    r = await api_client.post(f"/v1/workflows/{missing}/runs/stream", json={"input": {"question": "x"}})
    assert "knowledge base 'nothing-here' not found" in r.text

    export = await api_client.get(f"/v1/workflows/{wf}/export.zip")
    assert "handbook" not in export.headers["content-disposition"]  # named after the workflow, not a knowledge base
    assert export.headers["content-disposition"].endswith('.zip"')
    data = export.content
    z = zipfile.ZipFile(io.BytesIO(data))
    shipped = [n for n in z.namelist() if n.endswith("/src/knowledge/handbook.jsonl")]
    assert shipped and any(n.endswith("src/agentcanvas/knowledge_search.py") for n in z.namelist())
    (tmp_path / "handbook.jsonl").write_bytes(z.read(shipped[0]))
    code = next(n for n in z.namelist() if n.endswith("/graph.py"))
    lacking = zipfile.ZipFile(io.BytesIO((await api_client.get(f"/v1/workflows/{missing}/export.zip")).content))
    assert not [n for n in lacking.namelist() if "/knowledge/" in n]  # still exports; its search says what is missing
    assert "ac.knowledge_search_node(" in z.read(code).decode()

    # the exported runtime searches the shipped file the same way the server does
    from agentcanvas.adapters.langchain import nodes as ac

    monkeypatch.setenv("AGENTCANVAS_KNOWLEDGE_DIR", str(tmp_path))
    node = ac.knowledge_search_node(knowledge_base="handbook", query="{{ state.question }}", output="p", top_k=3)
    found = (await node({"question": "How many vacation days?"}))["p"]
    assert [p["text"] for p in found] == [p["text"] for p in out["passages"]]


def test_retrieval_evaluator():
    passages = [{"id": "c1", "source": "vacation-policy.md", "title": "Vacation policy", "text": "25 days a year"},
                {"id": "c2", "source": "https://x.example/sick", "title": "Sick leave", "text": "call in"}]  # fmt: skip
    obs = Observation(output={"passages": passages}, reference={"relevant": ["vacation-policy"]})

    async def run(config: dict, ref: dict | None = None):
        o = obs.model_copy(update={"reference": ref}) if ref is not None else obs
        return await score(Spec(name="r", kind="retrieval", config=config), o)

    import asyncio

    s = asyncio.run(run({}))
    assert s.score == 1.0 and s.passed and "mrr@5 1.00" in s.comment
    assert asyncio.run(run({"metric": "precision", "k": 2})).score == 0.5
    assert asyncio.run(run({"match_by": "title"}, {"relevant": ["Sick leave"]})).score == 1.0
    assert asyncio.run(run({"match_by": "source"}, {"relevant": ["https://x.example/sick"]})).score == 1.0
    assert asyncio.run(run({"match_by": "text", "metric": "mrr"}, {"relevant": ["call in"]})).score == 0.5
    assert asyncio.run(run({"match_by": "id"}, {"relevant": "c2"})).score == 1.0
    none = asyncio.run(run({}, {}))
    assert none.score is None and "reference.relevant" in none.comment
    assert asyncio.run(run({"channel": "other"})).score == 0.0


def _measure(index: ks.Index, cases: list[dict], mode: str, k: int = 3) -> dict[str, float]:
    totals = {"recall": 0.0, "mrr": 0.0}
    for case in cases:
        qv = ks.builtin_embed([case["input"]["question"]])[0]
        found = ks.search(index, case["input"]["question"], mode=mode, top_k=k, query_vector=qv)
        m = ks.retrieval_metrics([r["source"].removesuffix(".md") for r in found], case["reference"]["relevant"], k)
        totals = {n: totals[n] + m[n] for n in totals}
    return {n: round(v / len(cases), 3) for n, v in totals.items()}


async def test_hybrid_search_beats_the_keyword_baseline_on_the_sample_eval_set(app, api_client):
    """The S24 exit: the RAG template's hybrid search finds the right handbook page more often than keyword
    search alone (recall@3 and MRR over 50 questions with typos and paraphrases)."""
    kb = (await api_client.post(f"{P}/knowledge", json={"sample": "handbook"})).json()
    cases = kn.sample_eval()
    assert len(cases) == 50
    async with app.state.db.sessionmaker() as s:
        svc = kn.KnowledgeService(s)
        index = await svc.index(await svc.get(kb["id"]))
    hybrid, keyword = _measure(index, cases, "hybrid"), _measure(index, cases, "keyword")
    assert hybrid["recall"] > keyword["recall"] and hybrid["mrr"] >= keyword["mrr"], (hybrid, keyword)
    assert hybrid["recall"] >= 0.9


async def test_an_experiment_scores_hybrid_against_keyword(app, api_client):
    """The same comparison as a user runs it: the sample eval set as a dataset, a retrieval evaluator, and an
    experiment with the keyword variant as the baseline target."""
    from test_experiments import finished

    assert (await api_client.post(f"{P}/knowledge", json={"sample": "handbook"})).status_code == 201
    wf = await make_search_flow(api_client)
    ds = (await api_client.post(f"/v1/workflows/{wf}/datasets", json={"name": "Handbook questions"})).json()
    examples = [{"input": c["input"], "reference": c["reference"], "metadata": c["metadata"]} for c in kn.sample_eval()]
    assert (await api_client.post(f"/v1/datasets/{ds['id']}/examples", json={"examples": examples})).status_code == 200
    body = {"name": "found it", "kind": "retrieval", "config": {"k": 3, "metric": "recall"}}
    assert (await api_client.post(f"/v1/workflows/{wf}/evaluators", json=body)).status_code == 201
    r = await api_client.post(
        f"/v1/workflows/{wf}/experiments",
        json={"dataset_id": ds["id"], "targets": [
            {"overrides": {"search": {"mode": "keyword"}}, "label": "keyword"}, {"label": "hybrid"}]},
    )  # fmt: skip
    assert r.status_code == 202, r.text
    exp = await finished(app, api_client, r.json()["id"])
    assert exp["status"] == "done", exp
    keyword, hybrid = ({e["evaluator"]: e["mean"] for e in t["evaluators"]}["found it"] for t in exp["summary"])
    assert hybrid > keyword, (hybrid, keyword)
    assert json.dumps(exp["summary"])  # serialisable for the comparison view


async def test_the_export_plan_names_the_knowledge_bases_the_workflow_searches_and_whether_the_project_has_them(
    api_client,
):
    """An export of a workflow that searches a base the project lacks leaves it out; the plan says so beforehand."""
    wf = (
        await api_client.post("/v1/projects/prj_default/workflows", json={"name": "QA", "template": "knowledge_qa"})
    ).json()
    plan = (await api_client.get(f"/v1/workflows/{wf['id']}/export/plan")).json()["prerequisites"]
    assert plan["knowledge"] == [{"name": "handbook", "found": False}]
    made = await api_client.post("/v1/projects/prj_default/knowledge", json={"name": "handbook", "sample": "handbook"})
    assert made.status_code in (200, 201), made.text
    plan = (await api_client.get(f"/v1/workflows/{wf['id']}/export/plan")).json()["prerequisites"]
    assert plan["knowledge"] == [{"name": "handbook", "found": True}]
    other = (await api_client.post("/v1/projects/prj_default/workflows", json={"name": "Plain"})).json()
    assert "knowledge" not in (await api_client.get(f"/v1/workflows/{other['id']}/export/plan")).json()["prerequisites"]
