"""Release gates (S12 FR-EVAL-05): publishing to a gated environment runs or reuses an evaluation first."""

from __future__ import annotations

from test_datasets import new_dataset
from test_publish import make_greeter, set_greeting

from agentcanvas.api.deps import CurrentUser, current_user

EXAMPLES = [{"input": {"who": w}, "reference": {"out": f"Hello {w}"}} for w in ("Ada", "Bob")]


async def gated(api, **gate) -> tuple[str, dict]:
    wf = await make_greeter(api)
    ds = await new_dataset(api, wf)
    assert (await api.post(f"/v1/datasets/{ds['id']}/examples", json={"examples": EXAMPLES})).status_code == 200
    body = {"name": "exact", "kind": "exact", "config": {}}
    assert (await api.post(f"/v1/workflows/{wf}/evaluators", json=body)).status_code == 201
    r = await api.put(f"/v1/workflows/{wf}/gates/prod", json={"dataset_id": ds["id"], **gate})
    assert r.status_code == 200, r.text
    return wf, ds


def check(body: dict, name: str = "evaluation") -> dict:
    checklist = body.get("checklist") or body.get("extensions", {}).get("checklist") or []
    return next(c for c in checklist if c["name"] == name)


async def test_a_gate_blocks_a_regression_and_reuses_evaluations(api_client):
    wf, _ = await gated(api_client, min_scores={"exact": 0.9}, max_regression=0.0)
    gates = (await api_client.get(f"/v1/workflows/{wf}/gates")).json()
    assert [(g["environment"], g["min_scores"], g["max_regression"]) for g in gates] == [("prod", {"exact": 0.9}, 0.0)]

    first = await api_client.post(f"/v1/workflows/{wf}/publish", json={})
    assert first.status_code == 200, first.text
    ev = check(first.json())
    assert ev["ok"] and ev["detail"] == "2 example(s) on dataset v2: every threshold met"
    assert ev["items"] == ["exact: mean 1.0 (needs 0.9)"]
    runs = (await api_client.get(f"/v1/workflows/{wf}/experiments")).json()
    assert [e["name"] for e in runs] == ["release gate: prod"]

    again = await api_client.post(f"/v1/workflows/{wf}/publish", json={})  # same workflow: the evaluation is reused
    assert again.status_code == 200 and check(again.json())["ok"]
    assert len((await api_client.get(f"/v1/workflows/{wf}/experiments")).json()) == 1

    await set_greeting(api_client, wf, "Hey")  # a regression
    refused = await api_client.post(f"/v1/workflows/{wf}/publish", json={})
    assert refused.status_code == 422, refused.text
    body = refused.json()
    assert "evaluation: 2 example(s) on dataset v2: 2 threshold(s) missed" in body["detail"]
    assert check(body)["items"] == [
        "exact: mean 0.0 (needs 0.9)",
        "pass rate 0% vs 100% for v1.0.0 (may drop 0%)",
    ]
    # the live version's evaluation was reused, only the candidate ran
    assert [len(e["targets"]) for e in (await api_client.get(f"/v1/workflows/{wf}/experiments")).json()] == [1, 1]

    over = await api_client.post(f"/v1/workflows/{wf}/publish", json={"override_reason": "hotfix, ticket 42"})
    assert over.status_code == 200, over.text
    assert check(over.json())["detail"].startswith("overridden by usr_dev: hotfix, ticket 42")
    [entry] = (await api_client.get("/v1/org/audit", params={"action": "publish", "limit": 1})).json()[:1]
    assert entry["detail"]["gate_override"] == "hotfix, ticket 42"
    assert entry["detail"]["gate_failures"][0] == "exact: mean 0.0 (needs 0.9)"

    staging = await api_client.post(f"/v1/workflows/{wf}/publish", json={"environment": "staging"})
    assert staging.status_code == 200 and all(c["name"] != "evaluation" for c in staging.json()["checklist"])
    assert (await api_client.delete(f"/v1/workflows/{wf}/gates/prod")).status_code == 204
    assert (await api_client.delete(f"/v1/workflows/{wf}/gates/prod")).status_code == 404


async def test_default_thresholds_come_from_the_evaluators(api_client):
    wf, _ = await gated(api_client)
    await set_greeting(api_client, wf, "Hey")
    refused = await api_client.post(f"/v1/workflows/{wf}/publish", json={})
    assert refused.status_code == 422 and check(refused.json())["items"] == ["exact: mean 0.0 (needs 1)"]


async def test_gates_are_checked_and_admin_only(app, api_client):
    wf, ds = await gated(api_client)
    other = await make_greeter(api_client)
    url = f"/v1/workflows/{wf}/gates/prod"
    assert (await api_client.put(url, json={"dataset_id": ds["id"], "min_scores": {"nope": 1}})).status_code == 422
    foreign = (await new_dataset(api_client, other))["id"]
    assert (await api_client.put(url, json={"dataset_id": foreign})).status_code == 404
    assert (await api_client.put(f"/v1/workflows/{wf}/gates/Prod!", json={"dataset_id": ds["id"]})).status_code == 422
    assert (await api_client.put(f"/v1/workflows/{other}/gates/prod", json={"dataset_id": foreign})).status_code == 422

    await set_greeting(api_client, wf, "Hey")
    app.dependency_overrides[current_user] = lambda: CurrentUser(id="usr_dev", org_id="org_default", role="builder")
    try:
        assert (await api_client.put(url, json={"dataset_id": ds["id"]})).status_code == 403
        assert (await api_client.delete(url)).status_code == 403
        r = await api_client.post(f"/v1/workflows/{wf}/publish", json={"override_reason": "trust me"})
        assert r.status_code == 403 and "admin" in r.json()["detail"]
        # nor can a builder lower the bar through what the gate measures with
        [ev] = (await api_client.get(f"/v1/workflows/{wf}/evaluators")).json()
        easy = {"examples": [{"input": {"who": "x"}}]}
        for r in (
            await api_client.delete(f"/v1/datasets/{ds['id']}"),
            await api_client.post(f"/v1/datasets/{ds['id']}/examples", json=easy),
            await api_client.patch(f"/v1/evaluators/{ev['id']}", json={"threshold": 0}),
            await api_client.delete(f"/v1/evaluators/{ev['id']}"),
        ):
            assert r.status_code == 403 and "release gate" in r.json()["detail"], r.text
        assert (await api_client.get(f"/v1/datasets/{ds['id']}")).status_code == 200  # reading stays open
        assert (await api_client.post(f"/v1/evaluators/{ev['id']}/try", json={})).status_code == 200
    finally:
        app.dependency_overrides.pop(current_user)
    assert (await api_client.patch(f"/v1/evaluators/{ev['id']}", json={"threshold": 0.5})).status_code == 200


async def test_examples_that_cannot_run_fail_the_gate(app, api_client, monkeypatch):
    wf, _ = await gated(api_client)
    runner = app.state.runner
    start = runner.start
    calls = 0

    async def flaky(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("no capacity")
        return await start(*args, **kwargs)

    monkeypatch.setattr(runner, "start", flaky)
    refused = await api_client.post(f"/v1/workflows/{wf}/publish", json={})
    assert refused.status_code == 422, refused.text
    assert check(refused.json())["items"][0] == "exact: mean 0.5 (needs 1)"  # the run that never started scores 0


async def test_a_draft_changed_during_the_evaluation_is_not_published(api_client, monkeypatch):
    from agentcanvas.services import gates

    wf, _ = await gated(api_client)
    evaluate = gates.evaluate

    async def meanwhile(*args, **kwargs):
        check = await evaluate(*args, **kwargs)
        await set_greeting(api_client, wf, "Hey")  # an autosave lands while the gate runs
        return check

    monkeypatch.setattr(gates, "evaluate", meanwhile)
    r = await api_client.post(f"/v1/workflows/{wf}/publish", json={})
    assert r.status_code == 409 and "changed while it was being evaluated" in r.json()["detail"], r.text
