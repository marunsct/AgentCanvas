"""Online evaluators (S13 FR-EVAL-06): a sample of a deployment's runs is scored after the response, drift alerts."""

from __future__ import annotations

from datetime import UTC, datetime

from test_evaluators import new
from test_org_scope import act_as_b, org_b  # noqa: F401 - fixture
from test_publish import bearer, make_greeter, set_greeting

from agentcanvas import observability
from agentcanvas.api.deps import current_user
from agentcanvas.services.online import buckets, judge_cost, matches, sampled

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
    _ = oev


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


def test_sampling_filters_costs_and_buckets(monkeypatch):
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

    t = datetime(2026, 10, 6, 13, 30, tzinfo=UTC)
    rows = [(t, "a", 1.0, True, 0.01), (t.replace(hour=14), "a", 0.0, False, 0.0), (t, "a", None, False, 0.0)]
    hourly = buckets(rows, 1)
    assert [(b["start"].hour, b["runs"], b["scored"], b["mean"]) for b in hourly] == [(13, 2, 1, 1.0), (14, 1, 1, 0.0)]
    [daily] = buckets(rows, 24)
    assert daily["start"].hour == 0 and daily["runs"] == 3 and daily["mean"] == 0.5 and daily["pass_rate"] == 0.3333
