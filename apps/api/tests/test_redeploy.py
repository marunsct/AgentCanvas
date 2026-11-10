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
    assert h.status == "success" and h.handed_over and h.origin == "user" and h.thread_id == thread_id
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
    assert h.status == "success" and h.handed_over and h.thread_id == thread_id

    # an instance keeps its own runs alive, so a slow tick never sweeps them
    r = await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"seconds": 2}, "stream": False})
    for _ in range(100):  # the run row turns running once its task starts (slow on a busy CI runner)
        if (await run_row(app, r.json()["run_id"])).status == "running" and r.json()["run_id"] in job.runner.active():
            break
        await asyncio.sleep(0.05)
    await job.runner.heartbeat()
    assert (await run_row(app, r.json()["run_id"])).heartbeat_at is not None
    assert await job.sweep(datetime.now(UTC) + timedelta(minutes=5)) == ["run_alive"]  # but not the local run
    assert (await app.state.runner.wait(r.json()["run_id"])).status == "success"


async def test_a_stalled_instance_stops_a_run_another_instance_took_over(app, api_client):
    """The sweep is a lease: once another instance swept a run, its old instance stops it at the next heartbeat
    and leaves the run row to the new owner (S13)."""
    from sqlalchemy import update

    wf = await make(api_client, linear(script("n_slow", "examples.scripts:slow"), channels=("out", "seconds")))
    r = await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"seconds": 2}, "stream": False})
    run_id = r.json()["run_id"]
    await asyncio.sleep(0.1)
    async with app.state.db.sessionmaker() as s:  # what another instance's sweep writes while this one stalls
        await s.execute(update(Run).where(Run.id == run_id).values(status="interrupted", error="swept", handover=True))
        await s.commit()
    runner = app.state.runner
    assert await runner.heartbeat() == [run_id]
    h = await runner.wait(run_id)
    assert h.status == "interrupted" and h.events[-1]["code"] == "superseded"
    row = await run_row(app, run_id)
    assert row.status == "interrupted" and row.error == "swept" and row.handover is True  # untouched
    assert await runner.heartbeat() == []


async def test_the_pinned_version_is_the_latest_run_even_within_one_second(app, api_client):
    """R-11: the version a conversation was on was read by `started_at`, which SQLite keeps to the second, so two
    runs in one second could pin the older one. Run ids are time-ordered and decide now."""
    from sqlalchemy import inspect as sa_inspect

    from agentcanvas.db.models import WorkflowVersion
    from agentcanvas.ids import new_id
    from agentcanvas.services.redeploy import thread_version
    from agentcanvas.testing import DemoResources

    app.state.resources_factory = lambda thread_id, wf: DemoResources().with_mocks(HIGH_CASE["mocks"])
    wf_id = (await new_triage(api_client))["id"]
    pub = (await api_client.post(f"/v1/workflows/{wf_id}/publish", json={})).json()
    dep, key, v1 = pub["deployment"]["id"], pub["api_key"], pub["deployment"]["version_id"]
    res = (await api_client.post(f"/v1/deployments/{dep}/runs/wait", json={"input": {"ticket": "down"}},
                                 headers=bearer(key))).json()  # fmt: skip
    cur = (await api_client.get(f"/v1/workflows/{wf_id}")).json()
    next(n for n in cur["ir"]["nodes"] if n["id"] == "n_ok")["name"] = "lead_approval"  # v2: incompatible
    await api_client.put(f"/v1/workflows/{wf_id}", json={"ir": cur["ir"]}, headers={"If-Match": str(cur["version"])})
    v2 = (await api_client.post(f"/v1/workflows/{wf_id}/publish", json={})).json()["deployment"]["version_id"]
    async with app.state.db.sessionmaker() as s:
        first = await s.get(Run, res["run_id"])
        assert first is not None and first.workflow_version_id == v1
        cols = {c.key: getattr(first, c.key) for c in sa_inspect(Run).mapper.column_attrs if not c.deferred}
        s.add(Run(**{**cols, "id": new_id("run"), "workflow_version_id": v2}))  # same second, a later id
        await s.commit()
        current = await s.get(WorkflowVersion, v2)
        assert current is not None
        assert (await thread_version(s, res["thread_id"], current)).id == v2  # the latest run's version


async def _lost(app, api_client, **row) -> str:
    """A thread whose last run lost its instance (killed, not drained); returns the thread's id."""
    from datetime import UTC, datetime, timedelta

    from agentcanvas.db.models import Thread

    wf = await make(api_client, linear(script("n_slow", "examples.scripts:slow"), channels=("out", "seconds")))
    first = await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"seconds": 0}, "stream": False})
    thread_id = first.json()["thread_id"]
    await app.state.runner.wait(first.json()["run_id"])
    long_ago = datetime.now(UTC) - timedelta(minutes=5)
    async with app.state.db.sessionmaker() as s:
        s.add(Run(id="run_lost", thread_id=thread_id, ir_hash="x", status="running", started_at=long_ago, **row))
        thread = await s.get(Thread, thread_id)
        assert thread is not None
        thread.status = "running"
        await s.commit()
    return thread_id


async def test_a_lost_test_run_is_not_continued(app, api_client):
    """Experiments run their examples again themselves, and a test case's caller is gone: a test run whose server
    died is stopped, not continued as a person's run (it fired event handlers and spent the model calls twice)."""
    thread_id = await _lost(app, api_client, origin="test")
    assert await app.state.handover.tick() == []
    lost = await run_row(app, "run_lost")
    assert lost.status == "cancelled" and lost.handover is False and "not continued" in (lost.error or "")
    assert (await api_client.get(f"/v1/threads/{thread_id}")).json()["status"] == "idle"


async def test_a_continued_run_keeps_its_origin_and_event_depth(app, api_client):
    """An event handler's run continued on another server is still an event handler's run: its lifecycle events stay
    undelivered (the loop guard) and the events it emits keep their depth."""
    thread_id = await _lost(app, api_client, origin="event", event_depth=2)
    [resumed] = await app.state.handover.tick()
    h = await app.state.runner.wait(resumed)
    assert h.origin == "event" and h.event_depth == 2 and h.handed_over and h.thread_id == thread_id
    assert (await run_row(app, resumed)).origin == "event"


async def test_a_hand_over_that_fails_is_tried_again(app, api_client):
    """A passing failure (the database did not answer) leaves the run to the next tick, not 'interrupted' for good
    with a message saying it continues."""
    thread_id = await _lost(app, api_client)
    job, target, calls = app.state.handover, app.state.handover.target, []

    async def flaky(s, thread_id, version_id):
        calls.append(thread_id)
        if len(calls) == 1:
            raise ConnectionError("database restarting")
        return await target(s, thread_id, version_id)

    job.target = flaky
    assert await job.tick() == []
    lost = await run_row(app, "run_lost")
    assert lost.handover is True and lost.handover_attempts == 1
    [resumed] = await job.tick()
    assert (await app.state.runner.wait(resumed)).status == "success"
    assert (await api_client.get(f"/v1/threads/{thread_id}")).json()["status"] == "idle"


async def test_a_hand_over_that_cannot_work_ends_the_run_with_the_reason(app, api_client):
    from agentcanvas.errors import WorkflowHasErrors

    thread_id = await _lost(app, api_client)
    job = app.state.handover

    async def broken(s, thread_id, version_id):
        raise WorkflowHasErrors("workflow has 1 error(s); fix them before running")

    job.target = broken
    assert await job.tick() == []
    lost = await run_row(app, "run_lost")
    assert lost.status == "error" and lost.handover is False
    assert lost.error == (
        "its server stopped and it could not continue: workflow has 1 error(s); fix them before running; "
        "start the run again"
    )
    assert (await api_client.get(f"/v1/threads/{thread_id}")).json()["status"] == "error"


async def test_a_hand_over_that_keeps_failing_ends_after_a_few_tries(app, api_client):
    from agentcanvas.services.redeploy import HANDOVER_ATTEMPTS

    await _lost(app, api_client)
    job = app.state.handover

    async def down(s, thread_id, version_id):
        raise ConnectionError("database restarting")

    job.target = down
    for _ in range(HANDOVER_ATTEMPTS):
        assert await job.tick() == []
    lost = await run_row(app, "run_lost")
    assert lost.status == "error" and lost.handover is False
    assert "ConnectionError (see the server log)" in (lost.error or "")
    assert await job.tick() == []


async def test_runs_keep_their_heartbeat_while_the_server_drains(settings, monkeypatch):
    """The hand-over job is what refreshes this server's runs' heartbeats: it stops after the drain, not before, or
    another server would take a run still finishing in the grace period for orphaned and run it a second time."""
    from agentcanvas.api.main import create_app
    from agentcanvas.runtime.runner import Runner
    from agentcanvas.services.redeploy import HandoverJob

    order: list[str] = []
    drain, stop = Runner.drain, HandoverJob.stop

    async def drained(self, grace_s):
        order.append("drain")
        return await drain(self, grace_s)

    async def stopped(self):
        order.append("handover.stop")
        await stop(self)

    monkeypatch.setattr(Runner, "drain", drained)
    monkeypatch.setattr(HandoverJob, "stop", stopped)
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        pass
    assert order == ["drain", "handover.stop"]
