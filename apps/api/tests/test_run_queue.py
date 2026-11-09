"""F-5: the run queue. Past the organisation's concurrent-run limit a run waits, highest priority then oldest first;
without a limit (the default) nothing waits."""

from __future__ import annotations

import asyncio

import pytest
from test_kill_switch import SLOW, _start_slow
from test_redeploy import run_row
from test_runs_v1 import make

from agentcanvas.db.models import Run
from agentcanvas.runtime.admission import priority_of


@pytest.fixture
async def limited(app, api_client):
    """An organisation that runs one run at a time; the queue looks again every 50 ms."""
    r = await api_client.put("/v1/org/settings", json={"limits": {"max_concurrent_runs": 1}})
    assert r.status_code == 200, r.text
    assert r.json()["limits"]["effective"]["max_concurrent_runs"] == 1
    app.state.runner.admission.poll_s = 0.05
    return await make(api_client, SLOW)


async def _start(api_client, wf: str, seconds: float, priority: str | None = None) -> str:
    body = {"input": {"seconds": seconds}, "stream": False} | ({"priority": priority} if priority else {})
    r = await api_client.post(f"/v1/workflows/{wf}/runs/stream", json=body)
    assert r.status_code in (200, 202), r.text
    return r.json()["run_id"]


async def _queued(app, run_id: str) -> dict | None:
    for _ in range(200):
        h = app.state.runner.handles.get(run_id)
        found = [e["value"] for e in (h.events if h else []) if e.get("name") == "ac.queued"]
        if found:
            return found[-1]
        await asyncio.sleep(0.02)
    return None


async def test_without_a_limit_nothing_waits(app, api_client):
    wf = await make(api_client, SLOW)
    first = await _start_slow(app, api_client, wf)
    second = await _start(api_client, wf, 0)
    h = await asyncio.wait_for(app.state.runner.wait(second), 10)
    assert h.status == "success" and not [e for e in h.events if e.get("name") == "ac.queued"]
    await app.state.runner.cancel(first)


async def test_past_the_limit_a_run_waits_for_a_slot(app, api_client, limited):
    first = await _start_slow(app, api_client, limited)
    second = await _start(api_client, limited, 0)
    assert await _queued(app, second) == {"position": 1, "limit": 1}
    assert (await run_row(app, second)).queued is True
    assert not app.state.runner.handles[second].done.is_set()
    await app.state.runner.cancel(first)  # a slot frees: the waiting run goes
    h = await asyncio.wait_for(app.state.runner.wait(second), 10)
    assert h.status == "success" and (await run_row(app, second)).queued is False
    started = [e["type"] for e in h.events if e["type"] in ("RUN_STARTED", "STEP_STARTED")]
    assert started[0] == "RUN_STARTED" and "STEP_STARTED" in started  # it runs only once admitted


async def test_higher_priority_goes_first_then_the_oldest(app, api_client, limited):
    first = await _start_slow(app, api_client, limited)
    low = await _start(api_client, limited, 30, "low")
    assert await _queued(app, low) == {"position": 1, "limit": 1}
    normal = await _start(api_client, limited, 30)
    high = await _start(api_client, limited, 30, "high")
    assert (await _queued(app, high))["position"] == 1  # ahead of both
    assert (await run_row(app, high)).priority == 1 and (await run_row(app, low)).priority == -1
    await app.state.runner.cancel(first)
    for _ in range(200):
        if not (await run_row(app, high)).queued:
            break
        await asyncio.sleep(0.05)
    assert not (await run_row(app, high)).queued
    assert (await run_row(app, normal)).queued and (await run_row(app, low)).queued
    await app.state.runner.cancel(high)
    for _ in range(200):
        if not (await run_row(app, normal)).queued:
            break
        await asyncio.sleep(0.05)
    assert not (await run_row(app, normal)).queued and (await run_row(app, low)).queued
    for run_id in (normal, low):
        await app.state.runner.cancel(run_id)


async def test_a_waiting_run_can_be_stopped(app, api_client, limited):
    first = await _start_slow(app, api_client, limited)
    second = await _start(api_client, limited, 0)
    assert await _queued(app, second)
    stopped = (await api_client.post("/v1/org/runs/stop", json={"reason": "queue jam"})).json()
    assert set(stopped["stopping"]) == {first, second}  # a waiting run is in flight for the kill switch
    for run_id in (first, second):
        assert (await asyncio.wait_for(app.state.runner.wait(run_id), 10)).status == "cancelled"


async def test_waiting_runs_are_counted_in_a_gauge(app, api_client, limited):
    """`agentcanvas_runs_queued`: a queue that only grows (a stuck slot, a limit set too low) shows on a dashboard."""
    from agentcanvas.observability import RUNS_QUEUED

    before = RUNS_QUEUED._value.get()
    first = await _start_slow(app, api_client, limited)
    stopped, admitted = await _start(api_client, limited, 30), await _start(api_client, limited, 0)
    assert await _queued(app, stopped) and await _queued(app, admitted)
    assert RUNS_QUEUED._value.get() == before + 2
    await app.state.runner.cancel(stopped)  # stopped while it waited
    assert (await asyncio.wait_for(app.state.runner.wait(stopped), 10)).status == "cancelled"
    assert RUNS_QUEUED._value.get() == before + 1
    await app.state.runner.cancel(first)  # a slot frees: the other one takes it
    assert (await asyncio.wait_for(app.state.runner.wait(admitted), 10)).status == "success"
    assert RUNS_QUEUED._value.get() == before


async def test_a_dead_servers_runs_stop_counting(app, api_client, limited):
    """A run in flight on a server that died (no heartbeat for orphan_after_s) no longer holds a slot."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    first = await _start_slow(app, api_client, limited)
    long_ago = datetime.now(UTC) - timedelta(minutes=5)
    async with app.state.db.sessionmaker() as s:
        await s.execute(update(Run).where(Run.id == first).values(heartbeat_at=long_ago, started_at=long_ago))
        await s.commit()
    second = await _start(api_client, limited, 0)
    assert (await asyncio.wait_for(app.state.runner.wait(second), 10)).status == "success"
    await app.state.runner.cancel(first)


async def test_an_organisation_limit_stays_under_the_servers(app, api_client):
    app.state.settings.max_concurrent_runs = 5
    try:
        r = await api_client.put("/v1/org/settings", json={"limits": {"max_concurrent_runs": 9}})
        assert r.status_code == 422 and "5 runs at once" in r.json()["detail"]
        ok = await api_client.put("/v1/org/settings", json={"limits": {"max_concurrent_runs": 2}})
        assert ok.json()["limits"]["effective"]["max_concurrent_runs"] == 2
    finally:
        app.state.settings.max_concurrent_runs = 0


def test_test_runs_wait_behind_people():
    assert priority_of(None, "test") == -1 and priority_of(None, "user") == 0 and priority_of(None, "trigger") == 0
    assert priority_of("high", "test") == 1
