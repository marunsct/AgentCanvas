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
