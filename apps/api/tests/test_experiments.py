"""Experiments (S12 FR-EVAL-04): versions and variants over a pinned dataset version, scored, in the background."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from test_datasets import new_dataset
from test_org_scope import act_as_b, org_b  # noqa: F401 - fixture
from test_publish import make_greeter, set_greeting

from agentcanvas.api.deps import current_user
from agentcanvas.services.experiments import ExperimentJob

EXAMPLES = [
    {"input": {"who": "Ada"}, "reference": {"out": "Hello Ada"}, "split": "test"},
    {"input": {"who": "Bob"}, "reference": {"out": "Hello Bob"}, "split": "test"},
    {"input": {"who": "Cy"}, "reference": {"out": "Hi Cy"}, "split": "train"},
]


async def setup(api) -> tuple[str, dict]:
    wf = await make_greeter(api)
    assert (await api.post(f"/v1/workflows/{wf}/publish", json={})).status_code in (200, 201)
    await set_greeting(api, wf, "Hey")  # the draft now differs from v1.0.0
    ds = await new_dataset(api, wf)
    r = await api.post(f"/v1/datasets/{ds['id']}/examples", json={"examples": EXAMPLES})
    assert r.status_code == 200, r.text
    for body in (
        {"name": "exact", "kind": "exact", "config": {}},
        {"name": "greets", "kind": "regex", "config": {"channel": "out", "pattern": "^H"}},
    ):
        assert (await api.post(f"/v1/workflows/{wf}/evaluators", json=body)).status_code == 201
    return wf, ds


async def finished(app, api, exp_id: str) -> dict:
    task = app.state.experiments.tasks.get(exp_id)
    if task is not None:
        await asyncio.wait_for(asyncio.shield(task), 30)
    return (await api.get(f"/v1/experiments/{exp_id}")).json()


async def test_experiment_compares_versions_and_variants(app, api_client):
    wf, ds = await setup(api_client)
    hi = {"greet": {"values": {"out": "Hi {{ state.who }}"}}}
    r = await api_client.post(
        f"/v1/workflows/{wf}/experiments",
        json={"dataset_id": ds["id"], "targets": [{"version": "1.0.0"}, {}, {"overrides": hi, "label": "hi"}]},
    )
    assert r.status_code == 202, r.text
    started = r.json()
    assert started["status"] == "running" and started["dataset_version"] == 2 and started["name"] == "Smoke v2"
    assert [t["label"] for t in started["targets"]] == ["v1.0.0", "draft", "hi"]
    assert started["evaluators"] == ["exact", "greets"]

    exp = await finished(app, api_client, started["id"])
    assert exp["status"] == "done" and exp["ended_at"]
    v1, draft, variant = exp["summary"]
    assert (v1["examples"], v1["completed"], v1["errors"]) == (3, 3, 0)
    assert v1["pass_rate"] == round(2 / 3, 4)  # "Hi Cy" is expected for the third example
    assert {e["evaluator"]: e["mean"] for e in v1["evaluators"]} == {"exact": round(2 / 3, 4), "greets": 1.0}
    assert draft["pass_rate"] == 0.0 and draft["evaluators"][1]["pass_rate"] == 1.0
    assert variant["pass_rate"] == round(1 / 3, 4)
    assert v1["latency_p50_ms"] is not None and v1["tokens"] == 0 and v1["cost_usd"] == 0.0

    results = (await api_client.get(f"/v1/experiments/{exp['id']}/results")).json()
    assert len(results) == 9 and [r["target"] for r in results[:3]] == [0, 1, 2]
    first = results[0]
    assert first["output"]["out"] == "Hello Ada" and first["run_status"] == "success" and first["thread_id"]
    assert [s["passed"] for s in first["scores"]] == [True, True]
    assert results[1]["output"]["out"] == "Hey Ada" and results[2]["output"]["out"] == "Hi Ada"
    assert (await api_client.get(f"/v1/threads/{first['thread_id']}")).status_code == 200  # open the run's timeline

    listed = (await api_client.get(f"/v1/workflows/{wf}/experiments")).json()
    assert [e["id"] for e in listed] == [exp["id"]]
    # a split, and only some evaluators
    evaluators = (await api_client.get(f"/v1/workflows/{wf}/evaluators")).json()
    r = await api_client.post(
        f"/v1/workflows/{wf}/experiments",
        json={"dataset_id": ds["id"], "split": "train", "evaluator_ids": [evaluators[0]["id"]], "concurrency": 1},
    )
    small = await finished(app, api_client, r.json()["id"])
    assert small["summary"][0]["examples"] == 1 and small["evaluators"] == ["exact"]
    assert (await api_client.delete(f"/v1/experiments/{small['id']}")).status_code == 204
    assert (await api_client.get(f"/v1/experiments/{small['id']}")).status_code == 404


async def test_bad_experiments_are_refused(api_client):
    wf, ds = await setup(api_client)
    other = await make_greeter(api_client)
    url = f"/v1/workflows/{wf}/experiments"
    for body, status, says in [
        ({"dataset_id": ds["id"], "targets": [{"overrides": {"nope": {"x": 1}}}]}, 422, "'nope' is not a resource"),
        ({"dataset_id": ds["id"], "evaluator_ids": ["evl_x"]}, 422, "not an evaluator"),
        ({"dataset_id": ds["id"], "split": "none"}, 422, "no examples in split 'none'"),
        ({"dataset_id": ds["id"], "targets": [{"version": "9.9.9"}]}, 404, "9.9.9"),
        ({"dataset_id": ds["id"], "dataset_version": 7}, 404, "no version 7"),
        ({"dataset_id": (await new_dataset(api_client, other))["id"]}, 404, "not found"),
        ({"dataset_id": ds["id"], "targets": []}, 422, ""),
        ({"dataset_id": ds["id"], "concurrency": 100}, 422, ""),
    ]:
        r = await api_client.post(url, json=body)
        assert r.status_code == status and says in r.text, f"{body}: {r.status_code} {r.text}"
    broken = {"greet": {"values": "not a mapping"}}
    r = await api_client.post(url, json={"dataset_id": ds["id"], "targets": [{"overrides": broken}]})
    assert r.status_code == 422 and "target 1" in r.json()["detail"], r.text


async def test_cancel(app, api_client):
    wf, ds = await setup(api_client)
    app.state.experiments.launch = lambda exp_id: None  # nothing runs: cancel it while it waits
    exp = (await api_client.post(f"/v1/workflows/{wf}/experiments", json={"dataset_id": ds["id"]})).json()
    assert (await api_client.delete(f"/v1/experiments/{exp['id']}")).status_code == 409
    cancelled = (await api_client.post(f"/v1/experiments/{exp['id']}/cancel")).json()
    assert cancelled["status"] == "cancelled" and cancelled["summary"][0]["completed"] == 0
    later = datetime.now(UTC) + timedelta(minutes=5)
    assert await ExperimentJob(app.state.db, app.state.runner).tick(later) == []  # never taken over


async def test_another_instance_takes_over_an_experiment(app, api_client):
    """An instance that stops (drained or lost) leaves its experiment; another one finishes it (FR-X-10)."""
    wf, ds = await setup(api_client)
    lost = app.state.experiments.launch
    app.state.experiments.launch = lambda exp_id: None  # the instance dies right after accepting the experiment
    exp = (await api_client.post(f"/v1/workflows/{wf}/experiments", json={"dataset_id": ds["id"]})).json()
    other = ExperimentJob(app.state.db, app.state.runner)
    assert await other.tick() == []  # its heartbeat is fresh
    taken = await other.tick(datetime.now(UTC) + timedelta(minutes=2))
    assert taken == [exp["id"]]
    assert await ExperimentJob(app.state.db, app.state.runner).tick(datetime.now(UTC) + timedelta(minutes=2)) == []
    await asyncio.wait_for(other.tasks[exp["id"]], 30)
    done = (await api_client.get(f"/v1/experiments/{exp['id']}")).json()
    assert done["status"] == "done" and done["summary"][0]["completed"] == 3
    app.state.experiments.launch = lost


async def test_an_instance_stops_an_experiment_another_one_took_over(app, api_client):
    """Experiments have one owner: an instance that stalled past the takeover stops its copy at the next tick."""
    from sqlalchemy import update

    from agentcanvas.db.models import Experiment

    wf, ds = await setup(api_client)
    job = app.state.experiments
    lost = job.launch
    job.launch = lambda exp_id: None
    exp = (await api_client.post(f"/v1/workflows/{wf}/experiments", json={"dataset_id": ds["id"]})).json()
    job.launch = lost
    stalled = ExperimentJob(app.state.db, app.state.runner)
    stalled.tasks[exp["id"]] = work = asyncio.create_task(asyncio.sleep(60))  # still busy with it
    async with app.state.db.sessionmaker() as s:  # meanwhile another instance took it over
        await s.execute(update(Experiment).where(Experiment.id == exp["id"]).values(owner="ins_other"))
        await s.commit()
    await stalled.tick()
    await asyncio.gather(work, return_exceptions=True)
    assert work.cancelled()
    async with app.state.db.sessionmaker() as s:
        row = await s.get(Experiment, exp["id"])
        assert row is not None and row.owner == "ins_other" and row.status == "running"
    await job.cancel(exp["id"])


async def test_experiments_stay_in_their_organisation(app, api_client, org_b):  # noqa: F811
    wf, ds = await setup(api_client)
    exp = (await api_client.post(f"/v1/workflows/{wf}/experiments", json={"dataset_id": ds["id"]})).json()
    await finished(app, api_client, exp["id"])
    act_as_b(app)
    try:
        for method, path in [
            ("GET", f"/v1/experiments/{exp['id']}"),
            ("GET", f"/v1/experiments/{exp['id']}/results"),
            ("POST", f"/v1/experiments/{exp['id']}/cancel"),
            ("DELETE", f"/v1/experiments/{exp['id']}"),
            ("GET", f"/v1/workflows/{wf}/experiments"),
        ]:
            assert (await api_client.request(method, path)).status_code == 404, path
    finally:
        app.dependency_overrides.pop(current_user)
