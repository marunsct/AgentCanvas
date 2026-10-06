"""Safe redeploy (S11 FR-X-10): drain on shutdown, hand-over to another instance, resume on compatible versions."""

from __future__ import annotations

import asyncio

from sqlalchemy import select
from test_publish import bearer
from test_runs_v1 import linear, make, script
from test_versions import HIGH_CASE, new_triage

from agentcanvas.db.models import Run
from agentcanvas.ir.models import Workflow
from agentcanvas.services.redeploy import resume_compatible


async def run_row(app, run_id: str) -> Run:
    async with app.state.db.sessionmaker() as s:
        row = await s.get(Run, run_id)
        assert row is not None
        return row


async def test_draining_hands_running_runs_to_another_instance(app, api_client):
    wf = await make(api_client, linear(script("n_slow", "examples.scripts:slow"), channels=("out", "seconds")))
    r = await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"seconds": 0.5}, "stream": False})
    run_id, thread_id = r.json()["run_id"], r.json()["thread_id"]
    await asyncio.sleep(0.1)

    runner = app.state.runner
    assert await runner.drain(0.05) == [run_id]
    assert (await api_client.get("/readyz")).json() == {"status": "draining"}
    refused = await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"seconds": 0}})
    assert refused.status_code == 503 and refused.headers["retry-after"] == "5"
    paused = await run_row(app, run_id)
    assert paused.status == "interrupted" and paused.handover is True
    assert app.state.handover.runner.draining and await app.state.handover.tick() == []  # never to itself

    runner.draining = False  # the next instance
    [resumed] = await app.state.handover.tick()
    assert await app.state.handover.tick() == []  # claimed once
    h = await runner.wait(resumed)
    assert h.status == "success" and h.origin == "handover" and h.thread_id == thread_id
    assert (await run_row(app, run_id)).handover is False
    state = (await api_client.get(f"/v1/threads/{thread_id}")).json()
    assert state["status"] == "idle" and state["values"]["out"] == "slept"
    assert (await api_client.get("/readyz")).status_code == 200


async def test_a_new_version_continues_conversations_only_when_it_can_resume_them(app, api_client):
    from agentcanvas.testing import DemoResources

    app.state.resources_factory = lambda thread_id, wf: DemoResources().with_mocks(HIGH_CASE["mocks"])
    wf_id = (await new_triage(api_client))["id"]
    pub = (await api_client.post(f"/v1/workflows/{wf_id}/publish", json={})).json()
    dep, key, v1 = pub["deployment"]["id"], pub["api_key"], pub["deployment"]["version_id"]

    async def paused_conversation() -> str:
        res = await api_client.post(
            f"/v1/deployments/{dep}/runs/wait", json={"input": {"ticket": "down"}}, headers=bearer(key)
        )
        assert res.json()["status"] == "interrupted", res.text
        return res.json()["thread_id"]

    async def republish(change) -> str:
        cur = (await api_client.get(f"/v1/workflows/{wf_id}")).json()
        change(next(n for n in cur["ir"]["nodes"] if n["id"] == "n_ok"))
        put = await api_client.put(
            f"/v1/workflows/{wf_id}", json={"ir": cur["ir"]}, headers={"If-Match": str(cur["version"])}
        )
        assert put.status_code == 200, put.text
        return (await api_client.post(f"/v1/workflows/{wf_id}/publish", json={})).json()["deployment"]["version_id"]

    async def approve(thread_id: str) -> Run:
        done = await api_client.post(
            f"/v1/deployments/{dep}/threads/{thread_id}/resume", json={"value": {"approved": True}},
            headers=bearer(key),
        )  # fmt: skip
        assert done.json()["status"] == "success", done.text
        return await run_row(app, done.json()["run_id"])

    t1 = await paused_conversation()
    v2 = await republish(lambda n: n.update(label="Manager sign-off"))  # same pause point: compatible
    assert v2 != v1 and (await approve(t1)).workflow_version_id == v2

    t2 = await paused_conversation()
    v3 = await republish(lambda n: n.update(name="lead_approval"))  # the pause point is gone: incompatible
    assert (await approve(t2)).workflow_version_id == v2  # finished on the version it started on
    async with app.state.db.sessionmaker() as s:
        await_new = select(Run.workflow_version_id).where(Run.thread_id == await paused_conversation())
        assert (await s.scalars(await_new)).all() == [v3]  # new conversations get the new version


def test_resume_compatibility_reasons():
    import json
    from pathlib import Path

    old = Workflow.model_validate(json.loads(Path("examples/support_triage.ir.json").read_text()))
    assert resume_compatible(old, old) == []
    new = old.model_copy(deep=True)
    new.state.channels = [c for c in new.state.channels if c.name != "summary"]
    next(c for c in new.state.channels if c.name == "urgency").type = {"type": "integer"}
    next(n for n in new.nodes if n.id == "n_ok").disabled = True
    assert resume_compatible(old, new) == [
        "state field 'urgency' changed type or reducer",
        "state field 'summary' was removed",
        "pause point 'manager_approval' (approval) was removed or changed",
    ]
    added = old.model_copy(deep=True)
    added.state.channels.append(added.state.channels[0].model_copy(update={"name": "extra"}))
    assert resume_compatible(old, added) == []  # new fields and steps are fine


async def test_runs_of_an_instance_that_died_are_handed_over(app, api_client):
    from datetime import UTC, datetime, timedelta

    from agentcanvas.db.models import Thread

    wf = await make(api_client, linear(script("n_slow", "examples.scripts:slow"), channels=("out", "seconds")))
    first = await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"seconds": 0}, "stream": False})
    thread_id = first.json()["thread_id"]
    await app.state.runner.wait(first.json()["run_id"])
    long_ago = datetime.now(UTC) - timedelta(minutes=5)
    async with app.state.db.sessionmaker() as s:  # a run its instance never finished: killed, not drained
        s.add(Run(id="run_lost", thread_id=thread_id, ir_hash="x", status="running", started_at=long_ago))
        s.add(Run(id="run_alive", thread_id=thread_id, ir_hash="x", status="running", started_at=long_ago,
                  heartbeat_at=datetime.now(UTC)))  # fmt: skip  # another instance still runs it
        thread = await s.get(Thread, thread_id)
        assert thread is not None
        thread.status = "running"
        await s.commit()

    job = app.state.handover
    [resumed] = await job.tick()
    lost = await run_row(app, "run_lost")
    assert lost.status == "interrupted" and "stopped unexpectedly" in (lost.error or "") and lost.handover is False
    assert (await run_row(app, "run_alive")).status == "running"
    assert await job.sweep() == []  # swept once
    h = await app.state.runner.wait(resumed)
    assert h.status == "success" and h.origin == "handover" and h.thread_id == thread_id

    # an instance keeps its own runs alive, so a slow tick never sweeps them
    r = await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"seconds": 0.5}, "stream": False})
    await asyncio.sleep(0.1)
    await job.runner.heartbeat()
    assert (await run_row(app, r.json()["run_id"])).heartbeat_at is not None
    assert await job.sweep(datetime.now(UTC) + timedelta(minutes=5)) == ["run_alive"]  # but not the local run
    assert (await app.state.runner.wait(r.json()["run_id"])).status == "success"
