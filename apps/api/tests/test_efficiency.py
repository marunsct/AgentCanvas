"""Efficiency (HARDENING F-3, F-4): list pages cost a fixed number of queries, however many rows they summarise."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import event

P = "/v1/projects/prj_default"


@contextmanager
def statements(app) -> Iterator[list[str]]:
    """The SQL statements the app runs inside the block."""
    seen: list[str] = []

    def record(_conn, _cursor, statement, *_args) -> None:
        seen.append(statement)

    engine = app.state.db.engine.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", record)


def touching(seen: list[str], table: str) -> list[str]:
    return [s for s in seen if f"FROM {table}" in s or f"JOIN {table}" in s]


# ------------------------------------------------------------------------------------------------- F-3 ----
async def test_the_knowledge_list_counts_every_base_in_one_query(app, api_client):
    for name in ("one", "two", "three"):
        kb = (await api_client.post(f"{P}/knowledge", json={"name": name})).json()
        text = {"title": name, "text": f"The {name} handbook says hello."}
        assert (await api_client.post(f"/v1/knowledge/{kb['id']}/documents/text", json=text)).status_code == 201
    with statements(app) as seen:
        listed = (await api_client.get(f"{P}/knowledge")).json()
    assert [(k["name"], k["documents"]) for k in listed] == [("one", 1), ("three", 1), ("two", 1)]
    assert all(k["chunks"] >= 1 for k in listed)
    assert len(touching(seen, "knowledge_document")) == 1, touching(seen, "knowledge_document")


async def test_a_snapshot_reads_only_the_copilot_proposals_decided_since_the_version_before(app, api_client):
    from datetime import UTC, datetime, timedelta

    from test_publish import make_greeter, set_greeting

    from agentcanvas.db.models import CopilotConversation

    wf = await make_greeter(api_client)
    long_ago = datetime.now(UTC) - timedelta(days=30)
    accepted = {"id": "prp_old", "status": "accepted", "summary": "Old", "commands": [{}], "decided_by": "usr_dev"}
    async with app.state.db.sessionmaker() as s:
        s.add(CopilotConversation(
            id="cc_old", workflow_id=wf, user_id="usr_dev", messages=[{"role": "user", "content": "x" * 50_000}],
            proposals=[{**accepted, "decided_at": long_ago.isoformat()}], archived_at=long_ago, updated_at=long_ago,
        ))  # fmt: skip
        await s.commit()
    first = (await api_client.post(f"/v1/workflows/{wf}/versions", json={})).json()
    assert [c["summary"] for c in first["copilot_changes"]] == ["Old"]  # the first version credits everything
    await set_greeting(api_client, wf, "Hey")
    with statements(app) as seen:
        second = (await api_client.post(f"/v1/workflows/{wf}/versions", json={})).json()
    assert second["copilot_changes"] == []
    [read] = touching(seen, "copilot_conversation")
    assert "copilot_conversation.messages" not in read and "copilot_conversation.updated_at >=" in read, read


async def test_deployment_scores_are_bucketed_by_the_database(app, api_client):
    """The summary used to pull up to 100 000 score rows and group them in Python."""
    from datetime import UTC, datetime, timedelta

    from test_publish import bearer, make_greeter

    from agentcanvas.db.models import RunScore
    from agentcanvas.ids import new_id

    wf = await make_greeter(api_client)
    pub = (await api_client.post(f"/v1/workflows/{wf}/publish", json={})).json()
    dep = pub["deployment"]["id"]
    ran = await api_client.post(
        f"/v1/deployments/{dep}/runs/wait", json={"input": {"who": "A"}}, headers=bearer(pub["api_key"])
    )
    run, thread = ran.json()["run_id"], ran.json()["thread_id"]
    hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=3)
    async with app.state.db.sessionmaker() as s:
        rows = [(hour + timedelta(minutes=i % 50), 1.0 if i % 4 else 0.0, bool(i % 4), 0.001) for i in range(200)]
        rows += [(hour + timedelta(hours=1, minutes=5), None, False, 0.0)]  # scored nothing: counted, not averaged
        for at, value, passed, cost in rows:
            s.add(RunScore(
                id=new_id("rsc"), run_id=run, thread_id=thread, workflow_id=wf, deployment_id=dep,
                evaluator="greets", source="online", score=value, passed=passed, judge_cost_usd=cost, created_at=at,
            ))  # fmt: skip
        await s.commit()
    with statements(app) as seen:
        hourly = (await api_client.get(f"/v1/deployments/{dep}/scores")).json()
    got = [(b["start"], b["runs"], b["scored"], b["mean"], b["pass_rate"], b["judge_cost_usd"]) for b in hourly]
    first, second = hour.isoformat().replace("+00:00", "Z"), (hour + timedelta(hours=1)).isoformat()
    assert got == [(first, 200, 200, 0.75, 0.75, 0.2), (second.replace("+00:00", "Z"), 1, 0, None, 0.0, 0.0)], got
    [read] = touching(seen, "run_score")
    assert "GROUP BY" in read, read
    [daily] = (await api_client.get(f"/v1/deployments/{dep}/scores", params={"bucket": "day"})).json()
    assert daily["start"].endswith("T00:00:00Z") and daily["runs"] == 201 and daily["mean"] == 0.75


async def test_the_experiment_list_summarises_every_experiment_in_one_light_query_a_page_at_a_time(app, api_client):
    """The list (polled every second while one runs) read each experiment's results, outputs and all, one query
    per experiment."""
    from test_experiments import finished, setup

    wf, ds = await setup(api_client)
    ids = []
    for _ in range(3):
        r = await api_client.post(f"/v1/workflows/{wf}/experiments", json={"dataset_id": ds["id"], "targets": [{}]})
        assert r.status_code == 202, r.text
        ids.append(r.json()["id"])
        await finished(app, api_client, ids[-1])
    with statements(app) as seen:
        listed = (await api_client.get(f"/v1/workflows/{wf}/experiments")).json()
    assert [e["id"] for e in listed] == ids[::-1]
    assert all(e["summary"][0]["completed"] == 3 and e["summary"][0]["pass_rate"] is not None for e in listed)
    [read] = touching(seen, "experiment_result")
    assert "experiment_result.output" not in read and "experiment_result.trajectory" not in read, read
    page = (await api_client.get(f"/v1/workflows/{wf}/experiments", params={"limit": 2})).json()
    assert [e["id"] for e in page] == ids[:0:-1]
    rest = (await api_client.get(f"/v1/workflows/{wf}/experiments", params={"before": page[-1]["id"]})).json()
    assert [e["id"] for e in rest] == ids[:1]
    one = (await api_client.get(f"/v1/experiments/{ids[0]}")).json()
    assert one["summary"] == listed[-1]["summary"]


async def test_experiment_results_come_a_page_at_a_time(app, api_client):
    from test_experiments import finished, setup

    wf, ds = await setup(api_client)
    exp = (
        await api_client.post(f"/v1/workflows/{wf}/experiments", json={"dataset_id": ds["id"], "targets": [{}, {}]})
    ).json()
    await finished(app, api_client, exp["id"])
    url = f"/v1/experiments/{exp['id']}/results"
    everything = (await api_client.get(url)).json()
    assert len(everything) == 6
    first = (await api_client.get(url, params={"limit": 4})).json()
    second = (await api_client.get(url, params={"limit": 4, "offset": 4})).json()
    assert [r["id"] for r in first + second] == [r["id"] for r in everything] and len(first) == 4
    assert (await api_client.get(url, params={"limit": 100_000})).status_code == 422


async def test_the_contract_reads_every_deployment_with_its_version_in_one_query(app, api_client):
    from test_publish import make_greeter, set_greeting

    wf = await make_greeter(api_client)
    for env, greeting in (("prod", "Hi"), ("qa", "Hey"), ("staging", "Yo")):
        await set_greeting(api_client, wf, greeting)  # a version of its own each
        assert (await api_client.post(f"/v1/workflows/{wf}/publish", json={"environment": env})).status_code == 200
    with statements(app) as seen:
        got = (await api_client.get(f"/v1/workflows/{wf}/contract")).json()
    http = [s["detail"] for s in got["surfaces"] if s["kind"] == "http"]
    assert [d.split(":")[0] for d in http] == ["prod v1.0.0", "qa v1.1.0", "staging v1.2.0"]
    assert len(touching(seen, "workflow_version")) == 1, touching(seen, "workflow_version")


# ------------------------------------------------------------------------------------------------- F-4 ----
NODE = """from agentcanvas.sdk import canvas_node


@canvas_node(name="eff.shout", label="Shout", writes=["out"])
def shout(state: dict, cfg: dict) -> dict:
    return {"out": str(state.get("out", "")).upper()}
"""


async def test_the_workspace_is_walked_once_for_many_reads_and_again_after_a_change(app, api_client, monkeypatch):
    """Every workflow read and validation walked the whole workspace (rglob and stat) to see whether code changed."""
    from pathlib import Path

    from test_publish import make_greeter

    walks = []
    rglob = Path.rglob

    def counted(self, pattern, *args, **kwargs):
        walks.append(self)
        return rglob(self, pattern, *args, **kwargs)

    wf = await make_greeter(api_client)
    monkeypatch.setattr(Path, "rglob", counted)
    for _ in range(5):
        assert (await api_client.get(f"/v1/workflows/{wf}")).status_code == 200
        assert (await api_client.post(f"/v1/workflows/{wf}/validate")).status_code == 200
    assert len(walks) <= 5, f"{len(walks)} folder walks for 10 reads"  # one revision walks the 5 code folders
    before = len(walks)
    saved = await api_client.put(f"{P}/files/nodes/shout.py", json={"content": NODE})
    assert saved.status_code == 200, saved.text
    types = {t["type"] for t in (await api_client.get("/v1/node-types", params={"project_id": "prj_default"})).json()}
    assert "eff.shout" in types and len(walks) > before  # a save is seen at once


async def test_deleting_a_project_removes_its_workspace_off_the_event_loop(app, api_client, monkeypatch):
    import shutil
    import threading

    pid = (await api_client.post("/v1/projects", json={"name": "Doomed"})).json()["id"]
    root = app.state.projects.workspace(pid).root
    where = []
    rmtree = shutil.rmtree

    def watched(path, *args, **kwargs):
        where.append(threading.current_thread() is threading.main_thread())
        return rmtree(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", watched)
    assert (await api_client.delete(f"/v1/projects/{pid}")).status_code == 204
    assert where == [False] and not root.exists()
