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
