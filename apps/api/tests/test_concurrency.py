"""Races: many runs at once, two starts on one thread, two decisions on one approval.

Each must end in exactly one outcome the user can understand, with the database consistent afterwards. They run on
Postgres in CI (TEST_DATABASE_URL), where the claims are real row updates and not SQLite's single writer.
"""

from __future__ import annotations

import asyncio

from test_frameworks import greeter
from test_publish import make_greeter
from test_runs_v1 import events, triage_api  # noqa: F401 - `triage_api` is a fixture
from test_versions import new_triage

from agentcanvas.errors import Conflict
from agentcanvas.ir.models import Workflow


async def test_many_runs_at_once_all_finish_and_none_leaks_a_thread(app, api_client):
    wf = Workflow.model_validate((await greeter(api_client))["ir"])
    runner = app.state.runner
    handles = await asyncio.gather(*(runner.start(wf, input={"who": f"user {i}"}) for i in range(25)))
    await asyncio.wait_for(asyncio.gather(*(h.done.wait() for h in handles)), 60)
    assert {h.status for h in handles} == {"success"}
    assert len({h.thread_id for h in handles}) == 25
    assert runner.active_by_thread == {}
    for h in handles:
        assert (await api_client.get(f"/v1/threads/{h.thread_id}")).json()["status"] == "idle"


async def test_two_starts_on_one_thread_exactly_one_wins(app, api_client):
    wf = Workflow.model_validate((await greeter(api_client))["ir"])
    runner = app.state.runner
    outcomes = await asyncio.gather(
        *(runner.start(wf, thread_id="th_race", input={"who": f"racer {i}"}) for i in range(6)), return_exceptions=True
    )
    winners = [o for o in outcomes if not isinstance(o, BaseException)]
    losers = [o for o in outcomes if isinstance(o, BaseException)]
    assert len(winners) == 1 and all(isinstance(o, Conflict) for o in losers), outcomes
    await asyncio.wait_for(winners[0].done.wait(), 30)
    runs = (await api_client.get("/v1/threads/th_race/runs")).json()
    assert len(runs) == 1 and runs[0]["status"] == "success"
    assert (await api_client.get("/v1/threads/th_race")).json()["status"] == "idle"


async def test_two_decisions_on_one_approval_one_applies_and_the_other_is_told(triage_api):  # noqa: F811
    api = triage_api
    r = await api.post(
        f"/v1/workflows/{api.wf_id}/runs/stream", json={"input": {"ticket": "Invoice?"}, "thread_id": "th_low"}
    )
    assert events(r.text)[-1]["outcome"] == "interrupt"
    item = (await api.get("/v1/inbox", params={"workflow_id": api.wf_id})).json()[0]["id"]
    approve = {"value": {"decisions": [{"type": "approve"}]}}
    first, second = await asyncio.gather(
        api.post(f"/v1/inbox/{item}/decision", params={"stream": "true"}, json=approve),
        api.post(f"/v1/inbox/{item}/decision", params={"stream": "true"}, json=approve),
    )
    assert sorted([first.status_code, second.status_code]) == [200, 409], (first.text[:200], second.text[:200])
    thread = (await api.get("/v1/threads/th_low")).json()
    assert thread["status"] == "idle"
    runs = (await api.get("/v1/threads/th_low/runs")).json()
    assert [x["status"] for x in runs] == ["interrupted", "success"]  # applied once, not twice


class _Hangs:
    """The workflow's adapter, but a run that starts a step and then waits until it is cancelled."""

    id = "langchain"

    def __init__(self, inner):
        self.inner, self.manifest = inner, inner.manifest

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def execute(self, spec, resources):
        yield {"type": "STEP_STARTED", "stepName": "x", "canvas_node_id": "n1", "task_id": "t"}
        await asyncio.sleep(3600)


async def test_a_second_cancel_while_a_run_saves_itself_leaves_its_thread_free(app, api_client, monkeypatch):
    wf = Workflow.model_validate((await greeter(api_client))["ir"])
    runner = app.state.runner
    monkeypatch.setitem(runner.adapters, "langchain", _Hangs(runner.adapters["langchain"]))
    save = runner._finish

    async def slow_save(h, *args):
        await asyncio.sleep(0.3)  # a slow database: the second click lands while the run saves its outcome
        await save(h, *args)

    monkeypatch.setattr(runner, "_finish", slow_save)
    h = await runner.start(wf, input={"who": "x"}, thread_id="th_twice")
    await asyncio.sleep(0.1)
    assert await runner.cancel(h.run_id)
    await asyncio.sleep(0.1)
    assert not await runner.cancel(h.run_id)  # already stopping: nothing to cancel
    await asyncio.wait_for(h.done.wait(), 5)
    assert (await api_client.get("/v1/threads/th_twice")).json()["status"] == "idle"
    assert h.status == "cancelled"


async def test_two_snapshots_of_one_draft_make_one_version_and_the_other_is_told(api_client):
    wf = await new_triage(api_client)
    base = f"/v1/workflows/{wf['id']}/versions"
    first, second = await asyncio.gather(api_client.post(base, json={}), api_client.post(base, json={}))
    assert sorted([first.status_code, second.status_code]) == [201, 409], (first.text[:200], second.text[:200])
    assert [v["semver"] for v in (await api_client.get(base)).json()] == ["1.0.0"]


async def test_two_publishes_of_one_draft_pin_the_same_version(api_client):
    wf = await make_greeter(api_client)
    first, second = await asyncio.gather(
        api_client.post(f"/v1/workflows/{wf}/publish", json={}), api_client.post(f"/v1/workflows/{wf}/publish", json={})
    )
    assert first.status_code == second.status_code == 200, (first.text[:200], second.text[:200])
    assert len({r.json()["deployment"]["version_id"] for r in (first, second)}) == 1
    assert [v["semver"] for v in (await api_client.get(f"/v1/workflows/{wf}/versions")).json()] == ["1.0.0"]


async def test_a_new_thread_another_server_created_a_moment_ago_is_a_409(app, api_client, monkeypatch):
    """R-6: two replicas starting the same new caller-named thread: the second insert failed with IntegrityError, a
    500. It is the same 409 as two starts on one server."""
    from sqlalchemy.ext.asyncio import AsyncSession

    from agentcanvas.db.models import Thread

    wf = Workflow.model_validate((await greeter(api_client))["ir"])
    real_get = AsyncSession.get

    async def get(self, entity, ident, **kw):
        found = await real_get(self, entity, ident, **kw)
        if entity is Thread and ident == "th_race" and found is None:  # the other server wins in between
            async with app.state.db.sessionmaker() as other:
                other.add(Thread(id="th_race", workflow_id=wf.id, status="running"))
                await other.commit()
        return found

    monkeypatch.setattr(AsyncSession, "get", get)
    try:
        await app.state.runner.start(wf, input={"who": "x"}, thread_id="th_race")
        raise AssertionError("expected a conflict")
    except Conflict:
        pass
    assert "th_race" not in app.state.runner.active_by_thread


async def test_an_error_before_the_adapter_runs_does_not_show_a_secret(app, api_client, monkeypatch):
    """R-7: errors raised outside the adapter's own handling (building the graph, an MCP server, a model client)
    reached the run's error event and the log with any key in their text."""
    from agentcanvas.runtime import masking

    wf = Workflow.model_validate((await greeter(api_client))["ir"])
    runner = app.state.runner
    real = runner.adapters["langchain"]

    class Breaks(_Hangs):
        async def execute(self, spec, resources):
            raise RuntimeError("401 from https://llm.example.com with key sk-live-0123456789")
            yield  # pragma: no cover

    monkeypatch.setitem(runner.adapters, "langchain", Breaks(real))
    monkeypatch.setattr(masking, "provider", lambda: ["sk-live-0123456789"])
    h = await runner.start(wf, input={"who": "x"})
    await asyncio.wait_for(h.done.wait(), 10)
    [err] = [e for e in h.events if e["type"] == "RUN_ERROR"]
    assert "sk-live-0123456789" not in err["message"] and "401 from" in err["message"]


def test_running_runs_do_not_push_finished_ones_out(app):
    """R-8: eviction counted running runs against the finished ones it may keep, so with many runs going a run that
    had just finished was forgotten at once and a client attaching a moment later got 404."""
    from agentcanvas.runtime.runner import RunHandle

    runner = app.state.runner
    monkey_keep, runner._keep = runner._keep, 2
    try:
        running = [RunHandle(run_id=f"run_r{i}", thread_id=f"th_r{i}", workflow_id="wf") for i in range(3)]
        finished = [RunHandle(run_id=f"run_f{i}", thread_id=f"th_f{i}", workflow_id="wf") for i in range(2)]
        for h in finished:
            h.done.set()
        for h in [*running, *finished]:
            runner.handles[h.run_id] = h
        runner._evict()
        assert all(h.run_id in runner.handles for h in finished)
    finally:
        runner._keep = monkey_keep
        for rid in [k for k in runner.handles if k.startswith(("run_r", "run_f"))]:
            runner.handles.pop(rid)


async def test_a_long_runs_oldest_text_is_trimmed_but_its_numbers_stay(app, monkeypatch):
    from agentcanvas.runtime import runner as runner_module
    from agentcanvas.runtime.runner import RunHandle

    monkeypatch.setattr(runner_module, "RUNNING_BYTES", 20_000)
    h = RunHandle(run_id="run_long", thread_id="th_long", workflow_id="wf")
    for _ in range(400):
        await app.state.runner._emit(h, {"type": "TEXT_MESSAGE_CONTENT", "delta": "x" * 100})
    untrimmed = 400 * len('{"type": "TEXT_MESSAGE_CONTENT", "delta": "' + "x" * 100 + '", "seq": 400}')
    assert h.size < untrimmed * 0.7 and [e["seq"] for e in h.events] == list(range(1, 401))
    assert h.events[0]["delta"] == "" and all(e["delta"] == "x" * 100 for e in h.events[-100:])  # newest whole
