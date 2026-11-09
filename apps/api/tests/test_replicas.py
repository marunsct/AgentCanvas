"""Several API replicas on one database: Stop and Delete conversation reach a run wherever it runs.

A run lives on the instance that started it, and requests are spread over the replicas with no affinity, so a Stop
or a delete often lands on another one. The run row is the source of truth: the request marks it, and the instance
running the run stops it at its next heartbeat.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import delete, select
from test_kill_switch import SLOW, _start_slow
from test_redeploy import run_row
from test_runs_v1 import make

from agentcanvas.api.main import create_app
from agentcanvas.db.models import Run, Thread
from agentcanvas.runtime.runner import CANCELLED_BY_USER


@asynccontextmanager
async def replica(settings) -> AsyncIterator[tuple[object, httpx.AsyncClient]]:
    """A second API instance on the same database (another pod behind the load balancer)."""
    other = create_app(settings)
    async with other.router.lifespan_context(other):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=other), base_url="http://test") as client:
            yield other, client


async def test_stop_pressed_on_another_replica_stops_the_run(app, api_client, settings):
    wf = await make(api_client, SLOW)
    run_id = await _start_slow(app, api_client, wf)
    async with replica(settings) as (_, b):
        res = await b.post(f"/v1/runs/{run_id}/cancel")
        assert res.status_code == 202 and res.json() == {"cancelled": True, "pending": True}
        row = await run_row(app, run_id)
        assert row.status == "running" and row.stop_requested is True and row.stop_reason == CANCELLED_BY_USER
        await app.state.runner.heartbeat()  # the instance running it stops it at its next heartbeat
        h = await asyncio.wait_for(app.state.runner.wait(run_id), 10)
        assert h.status == "cancelled"
        row = await run_row(app, run_id)
        assert row.status == "cancelled" and row.error == CANCELLED_BY_USER  # not "stopped by an admin"
        again = await b.post(f"/v1/runs/{run_id}/cancel")  # over: nothing to stop
        assert again.json() == {"cancelled": False, "pending": False}


async def test_stop_on_the_replica_running_it_is_immediate(app, api_client):
    wf = await make(api_client, SLOW)
    run_id = await _start_slow(app, api_client, wf)
    res = await api_client.post(f"/v1/runs/{run_id}/cancel")
    assert res.json() == {"cancelled": True, "pending": False}
    assert (await asyncio.wait_for(app.state.runner.wait(run_id), 10)).status == "cancelled"
    assert (await run_row(app, run_id)).error == CANCELLED_BY_USER


async def test_a_stopped_run_whose_server_died_keeps_the_persons_reason(app, api_client):
    """The hand-over sweep cancels a marked run whose instance is gone, with the reason it was stopped for."""
    wf = await make(api_client, SLOW)
    first = await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"seconds": 0}, "stream": False})
    await app.state.runner.wait(first.json()["run_id"])
    long_ago = datetime.now(UTC) - timedelta(minutes=5)
    async with app.state.db.sessionmaker() as s:
        s.add(Run(id="run_dead", thread_id=first.json()["thread_id"], ir_hash="x", status="running",
                  started_at=long_ago, stop_requested=True, stop_reason=CANCELLED_BY_USER))  # fmt: skip
        await s.commit()
    assert await app.state.handover.sweep() == ["run_dead"]
    assert (await run_row(app, "run_dead")).error == CANCELLED_BY_USER


async def test_stop_pressed_while_a_redeploy_hands_the_run_over_cancels_it(app, api_client):
    """Paused by a redeploy, the run waits for another instance to continue it: Stop ends it there instead."""
    wf = await make(api_client, SLOW)
    run_id = await _start_slow(app, api_client, wf)
    thread_id = (await run_row(app, run_id)).thread_id
    assert await app.state.runner.drain(0) == [run_id]
    assert (await run_row(app, run_id)).handover is True
    res = await api_client.post(f"/v1/runs/{run_id}/cancel")
    assert res.json() == {"cancelled": True, "pending": False}
    row = await run_row(app, run_id)
    assert (row.status, row.error, row.handover) == ("cancelled", CANCELLED_BY_USER, False)
    async with app.state.db.sessionmaker() as s:
        assert (await s.get(Thread, thread_id)).status == "idle"  # type: ignore[union-attr]


async def test_deleting_a_conversation_on_another_replica_stops_its_run_first(app, api_client, settings):
    wf = await make(api_client, SLOW)
    run_id = await _start_slow(app, api_client, wf)
    thread_id = (await run_row(app, run_id)).thread_id
    async with replica(settings) as (_, b):
        deleting = asyncio.create_task(b.delete(f"/v1/threads/{thread_id}"))
        await asyncio.sleep(0.5)
        assert not deleting.done()  # it waits for the run to stop: the run would write to deleted rows
        assert (await run_row(app, run_id)).stop_requested is True
        await app.state.runner.heartbeat()
        res = await asyncio.wait_for(deleting, 10)
        assert res.status_code == 204, res.text
        assert app.state.runner.handles[run_id].status == "cancelled"
    async with app.state.db.sessionmaker() as s:
        assert await s.get(Thread, thread_id) is None
        assert (await s.scalars(select(Run.id).where(Run.thread_id == thread_id))).all() == []


async def test_deleting_a_conversation_whose_run_does_not_stop_is_refused(app, api_client, settings, monkeypatch):
    """The run's instance did not stop it in time (stalled, or gone and not swept yet): 409, nothing deleted."""
    from agentcanvas.api.v1 import runs as runs_module

    monkeypatch.setattr(runs_module, "STOP_MARGIN_S", 0.2)
    wf = await make(api_client, SLOW)
    run_id = await _start_slow(app, api_client, wf)
    thread_id = (await run_row(app, run_id)).thread_id
    async with replica(settings.model_copy(update={"handover_interval_s": 0.1})) as (_, b):
        res = await b.delete(f"/v1/threads/{thread_id}")
        assert res.status_code == 409 and "stopping" in res.json()["detail"]
    assert (await run_row(app, run_id)).status == "running"  # still there: delete again once it has stopped
    await app.state.runner.cancel(run_id)
    await app.state.runner.wait(run_id)


async def test_a_run_whose_conversation_was_deleted_elsewhere_stops(app, api_client, monkeypatch):
    """Rows deleted under a running run (another replica, the retention job): its heartbeat stops it, and the state
    its last checkpoint wrote is deleted too."""
    forgotten: list[str] = []
    forget = app.state.runner.forget_thread

    async def recording(thread_id: str) -> None:
        forgotten.append(thread_id)
        await forget(thread_id)

    monkeypatch.setattr(app.state.runner, "forget_thread", recording)
    wf = await make(api_client, SLOW)
    run_id = await _start_slow(app, api_client, wf)
    thread_id = (await run_row(app, run_id)).thread_id
    async with app.state.db.sessionmaker() as s:
        await s.execute(delete(Run).where(Run.thread_id == thread_id))
        await s.execute(delete(Thread).where(Thread.id == thread_id))
        await s.commit()
    assert await app.state.runner.heartbeat() == [run_id]
    h = await asyncio.wait_for(app.state.runner.wait(run_id), 10)
    assert h.status == "cancelled"
    assert forgotten == [thread_id]


async def test_following_a_run_on_another_replica_says_where_to_look(app, api_client, settings):
    """A run's live events are kept by the replica running it; another replica points to the conversation."""
    wf = await make(api_client, SLOW)
    run_id = await _start_slow(app, api_client, wf)
    thread_id = (await run_row(app, run_id)).thread_id
    async with replica(settings) as (_, b):
        res = await b.get(f"/v1/runs/{run_id}/events")
        assert res.status_code == 404
        assert "another server" in res.json()["detail"] and f"/v1/threads/{thread_id}" in res.json()["detail"]
        assert (await b.get("/v1/runs/run_none/events")).status_code == 404
    await app.state.runner.cancel(run_id)
    await app.state.runner.wait(run_id)
