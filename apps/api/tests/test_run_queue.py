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


class _Counting:
    """The database, counting the sessions opened through it."""

    def __init__(self, db):
        self.db, self.opened = db, 0

    def sessionmaker(self):
        self.opened += 1
        return self.db.sessionmaker()


async def test_waiting_runs_share_one_admission_pass(app, api_client, limited):
    """However many runs wait on a server, it asks the database for the organisation's queue once per round, not
    once per waiting run (each used to poll every second, holding a lock on the organisation's row)."""
    runner = app.state.runner
    first = await _start_slow(app, api_client, limited)
    waiting = [await _start(api_client, limited, 30) for _ in range(8)]
    for run_id in waiting:
        assert await _queued(app, run_id)
    counting = _Counting(app.state.db)
    runner.admission.db = counting
    await asyncio.sleep(0.5)  # about 10 rounds of 50 ms
    for _ in range(5):
        runner.admission.freed()
        await asyncio.sleep(0.01)
    assert counting.opened <= 20, counting.opened  # 8 waiters polling alone: about 80, plus 40 for the wake-ups
    runner.admission.db = app.state.db
    for run_id in [first, *waiting]:
        await runner.cancel(run_id)


async def test_a_run_waits_no_longer_than_the_server_allows(app, api_client, limited):
    runner = app.state.runner
    runner.admission.max_wait_s = 0.3
    first = await _start_slow(app, api_client, limited)
    second = await _start(api_client, limited, 0)
    h = await asyncio.wait_for(runner.wait(second), 10)
    assert h.status == "error" and h.events[-1]["code"] == "queue_timeout"
    assert "waited 0.3 s for a free run slot" in h.events[-1]["message"]
    row = await run_row(app, second)
    assert row.status == "error" and row.queued is False and row.pending is None
    assert (await api_client.get(f"/v1/threads/{h.thread_id}")).json()["status"] == "error"
    await runner.cancel(first)


async def test_passes_at_once_never_take_more_slots_than_the_limit(app, api_client, limited):
    """SQLite has no row lock: passes that ran at once (every waiting run woke together when a run ended) could each
    see the free slot and all take it."""
    from sqlalchemy import func, select

    runner = app.state.runner
    first = await _start_slow(app, api_client, limited)
    runner.admission.close()  # its own passes stay out of the way
    waiting = [await _start(api_client, limited, 30) for _ in range(4)]
    for _ in range(100):
        if (await run_row(app, waiting[-1])).queued:
            break
        await asyncio.sleep(0.02)
    await runner.cancel(first)
    await asyncio.wait_for(runner.wait(first), 10)
    await asyncio.gather(*(runner.admission.admit("org_default") for _ in range(4)))
    async with app.state.db.sessionmaker() as s:
        took = await s.scalar(
            select(func.count()).where(Run.id.in_(waiting), Run.status == "running", Run.queued.is_(False))
        )
    assert took == 1
    for run_id in waiting:
        await runner.cancel(run_id)


async def test_a_pass_locks_no_row_of_the_organisation(app, api_client):
    """Postgres: the pass serialises servers with an advisory lock; a row lock on the organisation would hold up
    every insert that references it (audit log, projects, tokens) while runs wait."""
    if not app.state.db.is_postgres:
        pytest.skip("row locks need Postgres")
    from sqlalchemy import text

    runner = app.state.runner
    async with app.state.db.sessionmaker() as held:
        await runner.admission.lock(held, "org_default")
        async with app.state.db.sessionmaker() as other:
            await other.execute(text("SET LOCAL lock_timeout = '200ms'"))
            await other.execute(text("SELECT id FROM organization WHERE id = 'org_default' FOR KEY SHARE"))
            taken = await other.scalar(
                text("SELECT pg_try_advisory_xact_lock(:a, hashtext('org_default'))"), {"a": 0x6163_7175}
            )
            assert taken is False  # another server's pass waits for this one
            await other.rollback()
        await held.rollback()


async def test_a_handed_over_run_keeps_its_place_in_the_queue(app, api_client, limited):
    """A run that a stopping server handed over waits ahead of the runs that came after it, as it did before."""
    runner = app.state.runner
    first = await _start_slow(app, api_client, limited)
    await runner.drain(0.05)
    runner.draining, runner.admission.closed = False, False  # the next server
    holder = await _start_slow(app, api_client, limited)
    later = await _start(api_client, limited, 30)
    assert await _queued(app, later) == {"position": 1, "limit": 1}
    [resumed] = await app.state.handover.tick()
    assert await _queued(app, resumed) == {"position": 1, "limit": 1}  # ahead of the later run
    for _ in range(100):
        if (await _queued(app, later))["position"] == 2:
            break
        await asyncio.sleep(0.02)
    assert (await _queued(app, later))["position"] == 2
    assert (await run_row(app, resumed)).queue_key == first
    for run_id in (holder, later, resumed):
        await runner.cancel(run_id)


async def test_a_test_case_counts_its_time_limit_from_its_slot(app, api_client, limited, monkeypatch):
    """Test runs wait in the run queue behind people's runs: the wait does not count against the case's time limit
    (every case used to fail 'timed out' without having run)."""
    from agentcanvas.db.models import TestCase
    from agentcanvas.ir.models import Workflow
    from agentcanvas.services import testcases

    monkeypatch.setattr(testcases, "CASE_TIMEOUT_S", 0.5)
    runner = app.state.runner
    blocker = await _start_slow(app, api_client, limited)
    wf = Workflow.model_validate((await api_client.get(f"/v1/workflows/{limited}")).json()["ir"])
    case = TestCase(id="tc_queued", workflow_id=limited, name="quick", input={"seconds": 0.01}, expect={})
    task = asyncio.create_task(testcases.run_case(runner, wf, case, {}, None))
    await asyncio.sleep(1)
    assert not task.done()
    await runner.cancel(blocker)
    res = await asyncio.wait_for(task, 10)
    assert res.passed, res.failures
    assert res.duration_ms is not None and res.duration_ms < 500  # the run's own time, not the wait


async def test_experiment_examples_count_their_time_limit_from_their_slot(app, api_client, limited, monkeypatch):
    from test_experiments import finished, setup

    from agentcanvas.services import experiments

    monkeypatch.setattr(experiments, "EXAMPLE_TIMEOUT_S", 0.5)
    runner = app.state.runner
    blocker = await _start_slow(app, api_client, limited)
    wf, ds = await setup(api_client)
    r = await api_client.post(f"/v1/workflows/{wf}/experiments", json={"dataset_id": ds["id"], "targets": [{}]})
    assert r.status_code == 202, r.text
    await asyncio.sleep(1.2)
    await runner.cancel(blocker)
    exp = await finished(app, api_client, r.json()["id"])
    results = (await api_client.get(f"/v1/experiments/{exp['id']}/results")).json()
    assert [x["run_status"] for x in results] == ["success"] * 3, [x["error"] for x in results]
    assert all(x["latency_ms"] < 500 for x in results)


async def test_a_copilot_test_run_says_when_it_could_not_get_a_slot(app, api_client, limited, monkeypatch):
    """A person waits for the Copilot's test run: it queues as a person's run (not behind them), and a run that never
    got a slot is reported as that, not as a run that took too long."""
    from agentcanvas.copilot import runs
    from agentcanvas.ir.models import Workflow

    monkeypatch.setattr(runs, "RUN_TIMEOUT_S", 0.3)
    blocker = await _start_slow(app, api_client, limited)
    wf = Workflow.model_validate((await api_client.get(f"/v1/workflows/{limited}")).json()["ir"])
    access = runs.RunAccess(app.state.runner, app.state.db.sessionmaker, wf, lambda _wf, ctx: ctx)
    out = await access.test(wf, {"seconds": 0.01})
    assert out["ok"] is False and "waited 0.3 s for a free run slot" in out["error"]
    [run] = [h for h in app.state.runner.handles.values() if h.origin == "test"]
    assert run.priority == 0 and (await asyncio.wait_for(app.state.runner.wait(run.run_id), 10)).status == "cancelled"
    await app.state.runner.cancel(blocker)


async def test_publishing_runs_its_test_cases_as_a_persons_runs(app, api_client, limited):
    """Someone waits to publish: the checklist's test cases queue like their own runs, not behind every person's."""
    runner = app.state.runner
    case = {"name": "quick", "input": {"seconds": 0.01}}
    assert (await api_client.post(f"/v1/workflows/{limited}/test-cases", json=case)).status_code == 201
    blocker = await _start_slow(app, api_client, limited)
    publish = asyncio.create_task(api_client.post(f"/v1/workflows/{limited}/publish", json={}))
    for _ in range(200):
        tests = [h for h in runner.handles.values() if h.origin == "test"]
        if tests:
            break
        await asyncio.sleep(0.02)
    assert [h.priority for h in tests] == [0]
    await runner.cancel(blocker)
    r = await asyncio.wait_for(publish, 20)
    assert r.status_code in (200, 201), r.text
