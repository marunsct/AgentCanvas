"""F-5: the kill switch stops an organisation's runs in flight, on this instance at once and on others at their next
heartbeat, never hands a stopped run over, and is audited with its reason."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import update
from test_redeploy import run_row
from test_runs_v1 import linear, make, script

from agentcanvas.db.models import Run, Thread
from agentcanvas.runtime.runner import STOPPED_BY_ADMIN

SLOW = linear(script("n_slow", "examples.scripts:slow"), channels=("out", "seconds"))


async def _start_slow(app, api_client, wf: str) -> str:
    r = await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"seconds": 30}, "stream": False})
    run_id = r.json()["run_id"]
    for _ in range(200):  # its row turns running once its task starts
        if (await run_row(app, run_id)).status == "running" and run_id in app.state.runner.active():
            return run_id
        await asyncio.sleep(0.05)
    raise AssertionError("the run did not start")


async def test_stop_all_runs_stops_the_runs_here_and_is_audited(app, api_client):
    wf = await make(api_client, SLOW)
    run_id = await _start_slow(app, api_client, wf)
    res = await api_client.post("/v1/org/runs/stop", json={"reason": "runaway agent spending"})
    assert res.status_code == 200 and res.json() == {"stopping": [run_id], "cancelled": []}
    h = await asyncio.wait_for(app.state.runner.wait(run_id), 10)
    assert h.status == "cancelled"
    row = await run_row(app, run_id)
    assert row.status == "cancelled" and row.error == STOPPED_BY_ADMIN and row.stop_requested is True
    [entry] = (await api_client.get("/v1/org/audit", params={"action": "stop_all_runs"})).json()
    assert entry["detail"]["reason"] == "runaway agent spending" and entry["detail"]["stopping"] == 1
    assert entry["target"] == "org_id=org_default"
    again = (await api_client.post("/v1/org/runs/stop", json={"reason": "again"})).json()
    assert again == {"stopping": [], "cancelled": []}  # nothing left in flight


async def test_a_run_on_another_instance_stops_at_its_heartbeat(app, api_client):
    """The press marks the run; the instance running it (here: this one, as if pressed elsewhere) stops it at its
    next heartbeat, through the normal cancel path."""
    wf = await make(api_client, SLOW)
    run_id = await _start_slow(app, api_client, wf)
    async with app.state.db.sessionmaker() as s:  # pressed on another instance: only the mark reaches the database
        await s.execute(update(Run).where(Run.id == run_id).values(stop_requested=True))
        await s.commit()
    assert (await run_row(app, run_id)).status == "running"
    await app.state.runner.heartbeat()
    h = await asyncio.wait_for(app.state.runner.wait(run_id), 10)
    assert h.status == "cancelled" and (await run_row(app, run_id)).error == STOPPED_BY_ADMIN


async def test_a_stopped_run_is_never_handed_over(app, api_client):
    """A marked run whose instance died is cancelled by the sweep (it used to be continued elsewhere), and a run
    already waiting for hand-over is cancelled by the press itself."""
    wf = await make(api_client, SLOW)
    first = await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"seconds": 0}, "stream": False})
    thread_id = first.json()["thread_id"]
    await app.state.runner.wait(first.json()["run_id"])
    long_ago = datetime.now(UTC) - timedelta(minutes=5)
    async with app.state.db.sessionmaker() as s:
        s.add(Run(id="run_dead", thread_id=thread_id, ir_hash="x", status="running", started_at=long_ago,
                  stop_requested=True))  # fmt: skip
        s.add(Run(id="run_waiting", thread_id=thread_id, ir_hash="x", status="interrupted", started_at=long_ago,
                  handover=True))  # fmt: skip
        thread = await s.get(Thread, thread_id)
        assert thread is not None
        thread.status = "interrupted"
        await s.commit()
    res = (await api_client.post("/v1/org/runs/stop", json={"reason": "incident 42"})).json()
    assert res == {"stopping": ["run_dead"], "cancelled": ["run_waiting"]}
    waiting = await run_row(app, "run_waiting")
    assert waiting.status == "cancelled" and waiting.handover is False
    assert await app.state.handover.sweep() == ["run_dead"]
    dead = await run_row(app, "run_dead")
    assert dead.status == "cancelled" and dead.handover is False and dead.error == STOPPED_BY_ADMIN
    assert await app.state.handover.tick() == []  # nothing is continued
    async with app.state.db.sessionmaker() as s:
        thread = await s.get(Thread, thread_id)
        assert thread is not None and thread.status == "idle"


async def test_stop_runs_of_one_workflow_leaves_the_others(app, api_client):
    one, other = await make(api_client, SLOW), await make(api_client, SLOW)
    a, b = await _start_slow(app, api_client, one), await _start_slow(app, api_client, other)
    res = (await api_client.post("/v1/org/runs/stop", json={"reason": "bad release", "workflow_id": one})).json()
    assert res["stopping"] == [a]
    assert (await asyncio.wait_for(app.state.runner.wait(a), 10)).status == "cancelled"
    assert (await run_row(app, b)).status == "running"
    deployment_scope = await api_client.post("/v1/org/runs/stop", json={"reason": "x", "deployment_id": "dep_x"})
    assert deployment_scope.json()["stopping"] == []  # b is a draft run, not one of that deployment
    await app.state.runner.cancel(b)
    await app.state.runner.wait(b)


async def test_stop_all_runs_needs_an_admin_and_a_reason(api_client):
    from agentcanvas.api.deps import required_role

    assert required_role("POST", "/v1/org/runs/stop") == "admin"
    assert (await api_client.post("/v1/org/runs/stop", json={})).status_code == 422
    assert (await api_client.post("/v1/org/runs/stop", json={"reason": "  "})).status_code == 422
