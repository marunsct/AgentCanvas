"""Semantic search over runs (G-10): finished runs are embedded with the project's model and found by meaning."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from test_efficiency import statements
from test_org_scope import act_as_b, org_b  # noqa: F401 - fixture
from test_simulation import make_chat

from agentcanvas.db.models import Run, RunSearch, Thread
from agentcanvas.services import run_search
from agentcanvas.testing import DemoResources

P = "/v1/projects/prj_default"
ANSWERS = {
    "th_refund": "We will send the money back to your card within five days.",
    "th_hotel": "Your room in Rome is booked for two nights.",
    "th_reset": "Use the link we emailed to choose a new password.",
}
ASKS = {
    "th_refund": "I want my refund for the broken kettle",
    "th_hotel": "Please book a hotel in Rome",
    "th_reset": "I forgot my password",
}


async def converse(app, api, wf: str) -> None:
    app.state.resources_factory = lambda thread_id, w: DemoResources().with_mocks({"model": [ANSWERS[thread_id]]})
    for thread_id, ask in ASKS.items():
        body = {"input": {"messages": [{"role": "user", "content": ask}]}, "thread_id": thread_id}
        r = await api.post(f"/v1/workflows/{wf}/runs/stream", json=body)
        assert r.status_code == 200 and "RUN_FINISHED" in r.text, r.text
    await app.state.run_search.drain()


async def search(api, q: str, **params) -> dict:
    r = await api.get(f"{P}/runs/search", params={"q": q, **params})
    assert r.status_code == 200, r.text
    return r.json()


async def test_finished_runs_are_found_by_what_was_said(app, api_client):
    wf = await make_chat(api_client)
    await converse(app, api_client, wf)

    out = await search(api_client, "kettle refund")
    assert out["model"] == "builtin" and out["indexed"] == 3 and out["stale"] == 0
    assert out["hits"][0]["thread_id"] == "th_refund"
    top = out["hits"][0]
    assert top["workflow_id"] == wf and top["workflow_name"] == "Chat" and top["run_id"] and top["started_at"]
    assert top["snippet"].startswith("User: I want my refund") and "money back" in top["snippet"]  # input and answer
    assert (await search(api_client, "password", workflow_id=wf))["hits"][0]["thread_id"] == "th_reset"
    assert (await search(api_client, "nothing like this zzz", mode="keyword"))["hits"] == []
    other = await api_client.get(f"{P}/runs/search", params={"q": "x", "workflow_id": "wf_nope"})
    assert other.status_code == 404


async def test_the_projects_embedding_model_ranks_by_meaning(app, api_client):
    """With a provider model words need not match: "money back" finds the refund."""
    topics = {"refund": ("refund", "money back"), "hotel": ("hotel", "room"), "password": ("password", "log in")}

    def factory(spec, secrets):
        async def embed(texts):
            return [[float(any(w in t.lower() for w in words)) for words in topics.values()] for t in texts]

        assert spec == {"model": "openai:text-embedding-3-small"}
        return embed

    app.state.run_search.embedder = factory
    await api_client.put(f"{P}/model-defaults", json={"embedding": "openai:text-embedding-3-small"})
    wf = await make_chat(api_client)
    await converse(app, api_client, wf)
    out = await search(api_client, "give me my money back", mode="semantic")
    assert out["model"] == "openai:text-embedding-3-small" and out["indexed"] == 3
    assert [h["thread_id"] for h in out["hits"]] == ["th_refund"]  # the other two are no match at all


async def test_runs_of_another_model_are_left_out_until_reindexed(app, api_client):
    wf = await make_chat(api_client)
    await converse(app, api_client, wf)
    async with app.state.db.sessionmaker() as s:
        await s.execute(update(RunSearch).values(model="openai:old-model"))
        await s.commit()
    out = await search(api_client, "refund")
    assert out["hits"] == [] and out["indexed"] == 0 and out["stale"] == 3  # vectors of another model: noise

    done = await api_client.post(f"{P}/runs/search/index")
    assert done.status_code == 200 and done.json() == {"indexed": 3, "skipped": 0}
    out = await search(api_client, "refund")
    assert out["hits"][0]["thread_id"] == "th_refund" and out["stale"] == 0
    assert (await api_client.post(f"{P}/runs/search/index")).json() == {"indexed": 0, "skipped": 0}  # nothing left


async def test_runs_from_before_the_index_are_indexed_from_their_recording(app, api_client):
    wf = await make_chat(api_client)
    await converse(app, api_client, wf)
    async with app.state.db.sessionmaker() as s:
        await s.execute(RunSearch.__table__.delete())
        await s.commit()
    assert (await search(api_client, "refund"))["stale"] == 3
    assert (await api_client.post(f"{P}/runs/search/index")).json()["indexed"] == 3
    assert (await search(api_client, "kettle"))["hits"][0]["thread_id"] == "th_refund"


async def test_a_reindex_leaves_test_runs_out_and_stale_counts_only_what_it_can_add(app, api_client, monkeypatch):
    """I-15: the run did not keep its origin, so a reindex indexed experiment and test runs the incremental index
    leaves out; they, and runs past the newest CANDIDATES, counted as stale for good."""
    wf = await make_chat(api_client)
    await converse(app, api_client, wf)
    async with app.state.db.sessionmaker() as s:
        assert set((await s.scalars(select(Run.origin))).all()) == {"user"}  # the runner keeps it
        await s.execute(update(Run).where(Run.thread_id == "th_hotel").values(origin="test"))
        await s.execute(RunSearch.__table__.delete())
        await s.commit()
    assert (await search(api_client, "refund"))["stale"] == 2
    assert (await api_client.post(f"{P}/runs/search/index")).json() == {"indexed": 2, "skipped": 0}
    assert (await search(api_client, "hotel Rome", mode="keyword"))["hits"] == []  # the test run is not found
    monkeypatch.setattr(run_search, "CANDIDATES", 1)
    out = await search(api_client, "refund")
    assert out["indexed"] == 1 and out["stale"] == 0  # all are in the index; only the newest one was ranked


async def test_the_index_follows_retention_and_deleting_a_conversation(app, api_client):
    wf = await make_chat(api_client)
    await converse(app, api_client, wf)
    async with app.state.db.sessionmaker() as s:
        old = datetime.now(UTC) - timedelta(days=91)
        await s.execute(update(Thread).where(Thread.id == "th_hotel").values(updated_at=old))
        await s.commit()
    assert await app.state.retention.tick(datetime.now(UTC)) == ["th_hotel"]
    assert (await api_client.delete("/v1/threads/th_reset")).status_code == 204
    async with app.state.db.sessionmaker() as s:
        assert await s.scalar(select(func.count()).select_from(RunSearch)) == 1
        assert await s.scalar(select(func.count()).select_from(Run)) == 1
    out = await search(api_client, "hotel in Rome")
    assert {h["thread_id"] for h in out["hits"]} <= {"th_refund"}  # the hotel and password conversations are gone


async def test_search_reads_only_the_names_of_the_workflows_it_found(app, api_client):
    """Not every workflow of the installation, on every search."""
    wf = await make_chat(api_client)
    await converse(app, api_client, wf)
    await api_client.post(f"{P}/workflows", json={"name": "Unrelated"})
    with statements(app) as seen:
        out = await search(api_client, "kettle refund")
    assert out["hits"][0]["workflow_name"] == "Chat"
    names = [q for q in seen if "workflow.name" in q and "FROM workflow" in q]
    assert names and all("WHERE" in q for q in names), names


async def test_search_stays_in_the_organisation(app, api_client, org_b):  # noqa: F811
    wf = await make_chat(api_client)
    await converse(app, api_client, wf)
    act_as_b(app)
    try:
        assert (await api_client.get(f"{P}/runs/search", params={"q": "refund"})).status_code == 404
        assert (await api_client.post(f"{P}/runs/search/index")).status_code == 404
        mine = await api_client.get("/v1/projects/prj_b/runs/search", params={"q": "refund"})
        assert mine.status_code == 200 and mine.json()["hits"] == [] and mine.json()["indexed"] == 0
    finally:
        app.dependency_overrides.clear()


def test_the_text_is_what_was_asked_and_answered_with_secrets_masked():
    text = run_search.text_of(
        {"messages": [{"role": "user", "content": "my key is sk-live-123456"}], "n": 3},
        {"messages": [{"type": "ai", "content": [{"type": "text", "text": "noted"}]}], "ok": True},
        {"KEY": "sk-live-123456"},
    )
    assert text == "User: my key is ***\nAnswer: noted"
    assert run_search.text_of({}, {}) == ""
    assert len(run_search.text_of("x" * 10_000, "")) == run_search.MAX_TEXT
