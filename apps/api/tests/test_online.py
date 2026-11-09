"""Online evaluators (S13 FR-EVAL-06): a sample of a deployment's runs is scored after the response, drift alerts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from test_evaluators import new
from test_org_scope import act_as_b, org_b  # noqa: F401 - fixture
from test_publish import bearer, make_greeter, set_greeting

from agentcanvas import observability
from agentcanvas.api.deps import current_user
from agentcanvas.services.online import judge_cost, matches, sampled

GREETS = {"channel": "out", "pattern": r"^Hello \w+$"}


async def deployed(api) -> tuple[str, str, str, dict]:
    wf = await make_greeter(api)
    ev = await new(api, wf, "greets", "regex", GREETS)
    pub = (await api.post(f"/v1/workflows/{wf}/publish", json={})).json()
    return wf, pub["deployment"]["id"], pub["api_key"], ev


async def call(app, api, dep: str, key: str, who: str = "Ada", **metadata: str) -> dict:
    body = {"input": {"who": who}, "metadata": metadata}
    r = await api.post(f"/v1/deployments/{dep}/runs/wait", json=body, headers=bearer(key))
    assert r.status_code == 200, r.text
    await app.state.online.drain()
    return r.json()


async def test_runs_are_scored_after_the_response(app, api_client):
    wf, dep, key, ev = await deployed(api_client)
    url = f"/v1/deployments/{dep}/online-evaluators"
    body = {"evaluator_id": ev["id"], "filter": {"status": ["success"], "metadata": {"tier": "gold"}}}
    r = await api_client.post(url, json=body)
    assert r.status_code == 201, r.text
    online = r.json()
    assert online["evaluator"] == "greets" and online["sampling"] == 1.0
    assert (await api_client.post(url, json=body)).status_code == 409

    gold = await call(app, api_client, dep, key, tier="gold")
    await call(app, api_client, dep, key)  # not labelled gold: left alone
    [scored] = (await api_client.get(f"/v1/deployments/{dep}/scored-runs")).json()
    assert scored["run_id"] == gold["run_id"] and scored["status"] == "success"
    [s] = scored["scores"]
    assert (s["evaluator"], s["source"], s["score"], s["passed"]) == ("greets", "online", 1.0, True)
    assert s["judge_cost_usd"] == 0.0  # a regex costs nothing; a judge's cost is kept apart from the run's
    assert (await api_client.get(f"/v1/threads/{gold['thread_id']}/scores")).json() == [s]

    [bucket] = (await api_client.get(f"/v1/deployments/{dep}/scores", params={"bucket": "day"})).json()
    assert (bucket["evaluator"], bucket["runs"], bucket["mean"], bucket["pass_rate"]) == ("greets", 1, 1.0, 1.0)

    patched = await api_client.patch(f"/v1/online-evaluators/{online['id']}", json={"sampling": 0, "filter": None})
    assert patched.status_code == 200 and patched.json()["filter"] == {"status": None, "metadata": {}}
    await call(app, api_client, dep, key, tier="gold")  # sampled out
    assert len((await api_client.get(f"/v1/deployments/{dep}/scored-runs")).json()) == 1

    assert (await api_client.delete(f"/v1/online-evaluators/{online['id']}")).status_code == 204
    assert (await api_client.get(url)).json() == []
    entry = (await api_client.get("/v1/org/audit", params={"limit": 1})).json()[0]
    assert entry["target"] == f"oev_id={online['id']}"
    _ = wf


async def test_drift_opens_and_resolves_an_alert(app, api_client):
    wf, dep, key, ev = await deployed(api_client)
    body = {"evaluator_id": ev["id"], "alert_below": 0.5, "min_samples": 2}
    oev = (await api_client.post(f"/v1/deployments/{dep}/online-evaluators", json=body)).json()
    for who in ("Ada", "Bob"):
        await call(app, api_client, dep, key, who)
    assert (await api_client.get(f"/v1/workflows/{wf}/alerts")).json() == []

    await set_greeting(api_client, wf, "Hey")  # a regression goes live
    assert (await api_client.post(f"/v1/workflows/{wf}/publish", json={})).status_code == 200
    for who in ("Cy", "Di", "Ed"):
        await call(app, api_client, dep, key, who)
    [alert] = (await api_client.get(f"/v1/workflows/{wf}/alerts")).json()
    assert alert["kind"] == "below" and alert["deployment_id"] == dep and alert["value"] == 0.4
    assert "greets: mean 0.40" in alert["message"]

    assert await app.state.online.report() == {(dep, "greets", "below")}
    gauge = observability.EVAL_ALERT_OPEN.labels(deployment=dep, evaluator="greets", kind="below")
    assert gauge._value.get() == 1

    await set_greeting(api_client, wf, "Hello")
    assert (await api_client.post(f"/v1/workflows/{wf}/publish", json={})).status_code == 200
    for who in ("Fa", "Gu"):
        await call(app, api_client, dep, key, who)  # 4 of 7 pass: 0.57
    assert (await api_client.get(f"/v1/workflows/{wf}/alerts")).json() == []
    [resolved] = (await api_client.get(f"/v1/workflows/{wf}/alerts", params={"state": "all"})).json()
    assert resolved["resolved_at"] is not None
    await app.state.online.report()
    assert gauge._value.get() == 0

    await set_greeting(api_client, wf, "Hey")  # it regresses again, then nobody watches it any more
    assert (await api_client.post(f"/v1/workflows/{wf}/publish", json={})).status_code == 200
    for who in ("Ha", "Io", "Ju", "Ka", "Lu", "Mo"):
        await call(app, api_client, dep, key, who)
    assert len((await api_client.get(f"/v1/workflows/{wf}/alerts")).json()) == 1
    assert (await api_client.delete(f"/v1/online-evaluators/{oev['id']}")).status_code == 204
    assert (await api_client.get(f"/v1/workflows/{wf}/alerts")).json() == []  # resolved with its evaluator
    assert await app.state.online.report() == set()


async def test_an_alert_resolves_once_traffic_stops(app, api_client):
    """E-11: with too few scores in the window the check returned before resolving, and only a scored run checked,
    so an alert stayed open for good once traffic stopped."""
    from sqlalchemy import update

    from agentcanvas.db.models import RunScore

    wf, dep, key, ev = await deployed(api_client)
    body = {"evaluator_id": ev["id"], "alert_below": 0.5, "min_samples": 2}
    assert (await api_client.post(f"/v1/deployments/{dep}/online-evaluators", json=body)).status_code == 201
    await set_greeting(api_client, wf, "Hey")
    assert (await api_client.post(f"/v1/workflows/{wf}/publish", json={})).status_code == 200
    for who in ("Ada", "Bob"):
        await call(app, api_client, dep, key, who)
    assert len((await api_client.get(f"/v1/workflows/{wf}/alerts")).json()) == 1
    async with app.state.db.sessionmaker() as s:  # two hours pass without a run
        await s.execute(update(RunScore).values(created_at=datetime.now(UTC) - timedelta(hours=2)))
        await s.commit()
    assert await app.state.online.report() == set()
    assert (await api_client.get(f"/v1/workflows/{wf}/alerts")).json() == []


async def test_an_alert_stays_open_while_runs_go_unscored(app, api_client):
    """I-2: the drift check counted scored runs only, so when the evaluator stopped scoring (a judge failing on
    every run) the window looked empty and the open alert was resolved."""
    from sqlalchemy import select, update

    from agentcanvas.db.models import OnlineEvaluator, RunScore

    wf, dep, key, ev = await deployed(api_client)
    body = {"evaluator_id": ev["id"], "alert_below": 0.5, "min_samples": 2}
    assert (await api_client.post(f"/v1/deployments/{dep}/online-evaluators", json=body)).status_code == 201
    await set_greeting(api_client, wf, "Hey")
    assert (await api_client.post(f"/v1/workflows/{wf}/publish", json={})).status_code == 200
    for who in ("Ada", "Bob"):
        await call(app, api_client, dep, key, who)
    assert len((await api_client.get(f"/v1/workflows/{wf}/alerts")).json()) == 1
    async with app.state.db.sessionmaker() as s:
        await s.execute(update(RunScore).values(score=None, passed=False))  # the evaluator now fails on every run
        online = await s.scalar(select(OnlineEvaluator).where(OnlineEvaluator.deployment_id == dep))
        assert online is not None
        await app.state.online.check_drift(s, online, "greets", wf)
        await s.commit()
    assert len((await api_client.get(f"/v1/workflows/{wf}/alerts")).json()) == 1


async def test_one_open_alert_per_rule(app, api_client):
    """Several scoring tasks (or pods) seeing the same rule break at once open one alert between them."""
    import asyncio

    from sqlalchemy import select

    from agentcanvas.db.models import OnlineEvaluator

    wf, dep, key, ev = await deployed(api_client)
    url = f"/v1/deployments/{dep}/online-evaluators"
    oev = (await api_client.post(url, json={"evaluator_id": ev["id"], "min_samples": 1})).json()
    await set_greeting(api_client, wf, "Hey")
    assert (await api_client.post(f"/v1/workflows/{wf}/publish", json={})).status_code == 200
    await call(app, api_client, dep, key)  # scores 0, no rule yet
    await api_client.patch(f"/v1/online-evaluators/{oev['id']}", json={"alert_below": 0.5})
    db, scorer = app.state.db, app.state.online
    async with db.sessionmaker() as s:
        online = await s.scalar(select(OnlineEvaluator).where(OnlineEvaluator.deployment_id == dep))
    assert online is not None

    async def check() -> None:
        async with db.sessionmaker() as s:
            await scorer.check_drift(s, online, "greets", wf)
            await s.commit()

    await asyncio.gather(*(check() for _ in range(4)))
    assert len((await api_client.get(f"/v1/workflows/{wf}/alerts")).json()) == 1


async def test_online_evaluators_are_org_scoped(app, api_client, org_b):  # noqa: F811
    _, dep, _, ev = await deployed(api_client)
    oev = (await api_client.post(f"/v1/deployments/{dep}/online-evaluators", json={"evaluator_id": ev["id"]})).json()
    other = await make_greeter(api_client)
    foreign = await new(api_client, other, "x", "regex", GREETS)  # another workflow's evaluator
    r = await api_client.post(f"/v1/deployments/{dep}/online-evaluators", json={"evaluator_id": foreign["id"]})
    assert r.status_code == 404
    act_as_b(app)
    try:
        assert (await api_client.get(f"/v1/deployments/{dep}/online-evaluators")).status_code == 404
        assert (await api_client.patch(f"/v1/online-evaluators/{oev['id']}", json={"sampling": 0})).status_code == 404
        assert (await api_client.get(f"/v1/deployments/{dep}/scores")).status_code == 404
    finally:
        app.dependency_overrides.pop(current_user)


def test_sampling_filters_and_costs(monkeypatch):
    assert sampled("run_1", "oev_1", 1.0) and not sampled("run_1", "oev_1", 0.0)
    hits = sum(sampled(f"run_{i}", "oev_1", 0.25) for i in range(4000))
    assert 900 < hits < 1100 and sampled("run_7", "oev_1", 0.5) == sampled("run_7", "oev_1", 0.5)
    assert matches({"status": ["error"]}, "error", {}) and not matches({"status": ["error"]}, "success", {})
    assert matches({"metadata": {"tier": "gold"}}, "success", {"tier": "gold", "x": "y"})
    assert not matches({"metadata": {"tier": "gold"}}, "success", {})

    from agentcanvas.services import online

    monkeypatch.setattr(online, "price", lambda m, i, o: None if m.startswith("nobody") else (i + 5 * o) / 1e6)
    tokens, cost = judge_cost({"anthropic:claude-haiku-4-5": {"input_tokens": 1000, "output_tokens": 100}})
    assert (tokens, cost) == (1100, 0.0015)
    assert judge_cost({"nobody:nothing": {"input_tokens": 5}}) == (5, 0.0)
    # E-11: a judge without a price counted as free with nothing saying so
    assert online.unpriced({"nobody:nothing": {"input_tokens": 5}, "anthropic:claude-haiku-4-5": {"input_tokens": 1}})
    assert online.unpriced({"nobody:nothing": {"input_tokens": 5}}) == ["nobody:nothing"]
    assert online.unpriced({"nobody:nothing": {}}) == []  # never called

    # the hourly and daily buckets: test_efficiency.py, through the endpoint (the database groups them)
