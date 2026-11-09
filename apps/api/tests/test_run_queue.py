"""F-5: the run queue. Past the organisation's concurrent-run limit a run waits, highest priority then oldest first;
without a limit (the default) nothing waits."""

from __future__ import annotations

import asyncio

import pytest
from test_kill_switch import SLOW, _start_slow
from test_redeploy import run_row
from test_runs_v1 import linear, make, script

from agentcanvas.db.models import InterruptTask, Run
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


async def _on_thread(api_client, wf: str, thread_id: str | None, seconds: float) -> tuple[str, str]:
    body = {"input": {"seconds": seconds}, "stream": False} | ({"thread_id": thread_id} if thread_id else {})
    r = await api_client.post(f"/v1/workflows/{wf}/runs/stream", json=body)
    assert r.status_code in (200, 202), r.text
    return r.json()["run_id"], r.json()["thread_id"]


async def test_a_waiting_run_keeps_its_message_through_a_server_restart(app, api_client, limited):
    """A run still waiting for a slot never reached the workflow, so no checkpoint holds its input: the hand-over
    starts it again with the input it was sent, on a new conversation and on one that already has turns."""
    runner = app.state.runner
    done, earlier = await _on_thread(api_client, limited, None, 0.01)
    assert (await asyncio.wait_for(runner.wait(done), 10)).status == "success"
    blocker = await _start_slow(app, api_client, limited)
    fresh, fresh_thread = await _on_thread(api_client, limited, None, 0.01)
    follow, _ = await _on_thread(api_client, limited, earlier, 0.02)
    assert await _queued(app, fresh) and await _queued(app, follow)

    await runner.drain(0.05)
    for run_id in (fresh, follow):
        row = await run_row(app, run_id)
        assert row.status == "interrupted" and row.handover is True and row.queued is True
    runner.draining, runner.admission.closed = False, False  # the next server
    resumed = await app.state.handover.tick()
    assert len(resumed) == 3
    blocker_thread = (await run_row(app, blocker)).thread_id
    for run_id in resumed:  # the blocker continues its 30 s sleep: stop it so the others get the slot
        if runner.handles[run_id].thread_id == blocker_thread:
            await runner.cancel(run_id)
    by_thread = {runner.handles[r].thread_id: runner.handles[r] for r in resumed}
    for thread_id, seconds in ((fresh_thread, 0.01), (earlier, 0.02)):
        h = await asyncio.wait_for(runner.wait(by_thread[thread_id].run_id), 10)
        assert h.status == "success", h.events[-1]
        assert any(e["type"] == "STEP_STARTED" for e in h.events)  # it ran its own message
        state = (await api_client.get(f"/v1/threads/{thread_id}")).json()
        assert state["values"]["seconds"] == seconds


async def test_a_draining_server_starts_no_waiting_run(app, api_client, limited):
    """During the drain's grace period a freed slot is not given to a waiting run: the run would be cut off at the
    end of the grace period. It is handed over instead, and the drain does not wait for it."""
    runner = app.state.runner
    first, _ = await _on_thread(api_client, limited, None, 0.3)
    for _ in range(100):
        if first in runner.active():
            break
        await asyncio.sleep(0.02)
    second, _ = await _on_thread(api_client, limited, None, 0.01)
    assert await _queued(app, second)
    began = asyncio.get_running_loop().time()
    assert await runner.drain(5) == [second]
    assert asyncio.get_running_loop().time() - began < 3  # the waiting run does not hold the drain up
    assert runner.handles[first].status == "success"
    h = runner.handles[second]
    assert h.status == "interrupted" and not any(e["type"] == "STEP_STARTED" for e in h.events)
    assert (await run_row(app, second)).handover is True
    runner.draining, runner.admission.closed = False, False


async def test_a_waiting_answer_is_given_again_after_a_hand_over(app, api_client, limited):
    """An answer to a question that waited for a slot reaches the workflow on the next server, also when its server
    died without putting the Inbox item back."""
    from sqlalchemy import update

    runner = app.state.runner
    ask = {"id": "n_ask", "type": "core.human_input", "name": "ask", "config": {"prompt": "Go?", "output": "answer"}}
    wf = await make(api_client, linear(ask, script("n_s", "examples.scripts:slow"), channels=("out", "answer")))
    r = await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={"thread_id": "th_ask", "stream": False})
    paused = await asyncio.wait_for(runner.wait(r.json()["run_id"]), 10)
    [item] = next(e for e in paused.events if e["type"] == "RUN_FINISHED")["interrupts"]
    blocker = await _start_slow(app, api_client, limited)
    answer = {"value": "yes", "interrupt_id": item["id"], "stream": False}
    r = await api_client.post("/v1/threads/th_ask/resume", json=answer)
    assert await _queued(app, r.json()["run_id"])
    await runner.drain(0.05)
    async with app.state.db.sessionmaker() as s:  # as a server that died leaves it: answered
        await s.execute(update(InterruptTask).where(InterruptTask.thread_id == "th_ask").values(status="decided"))
        await s.commit()
    runner.draining, runner.admission.closed = False, False  # the next server
    resumed = await app.state.handover.tick()
    blocker_thread = (await run_row(app, blocker)).thread_id
    for run_id in resumed:
        if runner.handles[run_id].thread_id == blocker_thread:
            await runner.cancel(run_id)
    [answer] = [runner.handles[r] for r in resumed if runner.handles[r].thread_id == "th_ask"]
    h = await asyncio.wait_for(runner.wait(answer.run_id), 10)
    assert h.status == "success", h.events[-1]
    assert (await api_client.get("/v1/threads/th_ask")).json()["values"]["answer"] == "yes"
    async with app.state.db.sessionmaker() as s:
        assert (await s.get(InterruptTask, item["id"])).status == "decided"
