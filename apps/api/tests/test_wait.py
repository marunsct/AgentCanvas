"""F-10: the durable Wait block (`core.wait`): for a while, until a time, or until an event, resumed by the timer job
on one replica, never by a person."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from sqlalchemy import select, update
from sqlalchemy.orm import undefer
from test_runs_v1 import events, linear, make

from agentcanvas import waits
from agentcanvas.adapters.langchain.codegen import generate
from agentcanvas.adapters.langchain.interpreter import build_graph
from agentcanvas.adapters.langchain.nodes import SKIP_WAITS, Resources
from agentcanvas.db.models import InterruptTask, Run, Thread
from agentcanvas.ir.models import Workflow
from agentcanvas.services.timers import TimerJob
from agentcanvas.validation import validate


# ----------------------------------------------------------------------------------------- durations ---
@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("3d", 3 * 86400), ("2h 30m", 9000), ("1h30m", 5400), ("90 minutes", 5400), ("1 week", 604800),
        ("2 hours and 5 minutes", 7500), ("PT45M", 2700), ("P3D", 3 * 86400), ("P1DT2H", 93600), ("p1w", 604800),
        ("45", 45), (12, 12), ("0", 0), ("1.5h", 5400),
    ],
)  # fmt: skip
def test_durations(text, seconds):
    assert waits.parse_duration(text) == timedelta(seconds=seconds)


@pytest.mark.parametrize("text", ["", "soon", "3 fortnights", "P1Y", "P1M", "PT", "P1DT", "-5", "3d later", True])
def test_what_is_not_a_duration(text):
    with pytest.raises(waits.WaitError):
        waits.parse_duration(text)


def test_times_are_in_the_steps_time_zone_unless_they_say():
    assert waits.parse_time("2026-12-01T09:00:00Z") == datetime(2026, 12, 1, 9, tzinfo=UTC)
    assert waits.parse_time("2026-12-01T09:00+01:00") == datetime(2026, 12, 1, 8, tzinfo=UTC)
    assert waits.parse_time("2026-12-01T09:00", "Europe/Berlin") == datetime(2026, 12, 1, 8, tzinfo=UTC)
    assert waits.parse_time("2026-12-01 09:00") == datetime(2026, 12, 1, 9, tzinfo=UTC)
    assert waits.parse_time("2026-12-01") == datetime(2026, 12, 1, tzinfo=UTC)
    for bad in ("tomorrow", "2026-13-01", ""):
        with pytest.raises(waits.WaitError):
            waits.parse_time(bad)
    with pytest.raises(waits.WaitError, match="time zone"):
        waits.parse_time("2026-12-01T09:00", "Mars/Olympus")


def test_a_wait_lasts_up_to_a_year():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert waits.due_at("for", now=now, duration="366d") == now + timedelta(days=366)
    with pytest.raises(waits.WaitError, match="up to 366 days"):
        waits.due_at("for", now=now, duration="367d")
    with pytest.raises(waits.WaitError, match="up to 366 days"):
        waits.due_at("until", now=now, until="2030-01-01")
    assert waits.due_at("event", now=now, timeout="2h") == now + timedelta(hours=2)


def test_event_names():
    assert waits.check_event("invoice.paid") == "invoice.paid"
    for bad in ("Invoice", "paid", "run.completed", "interrupt.raised", ""):
        with pytest.raises(waits.WaitError):
            waits.check_event(bad)


# ---------------------------------------------------------------------------------------- validation ---
def wait_ir(config: dict, *, framework: str = "langchain", after: str = "done") -> dict:
    ir = linear(
        {"id": "n_wait", "type": "core.wait", "name": "wait", "config": config},
        {"id": "n_set", "type": "core.set", "name": "after", "config": {"values": {"out": after}}},
        channels=("out", "woke"),
    )
    return {**ir, "ir_version": "1.1", "id": "wf_wait", "framework": framework}


def diags(config: dict, **kw) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for d in validate(Workflow.model_validate(wait_ir(config, **kw))):
        if d.code not in ("P003",):
            out.setdefault(d.code, []).append(f"{d.path}: {d.message}")
    return out


def test_the_validator_checks_what_each_way_of_waiting_needs():
    assert diags({"duration": "3d"}) == {}
    assert diags({"mode": "for", "duration": "{{ state.out }}"}) == {}
    assert diags({"mode": "until", "until": "2026-12-24T09:00", "timezone": "Europe/Berlin"}) == {}
    assert diags({"mode": "event", "event": "invoice.paid", "event_key": "{{ state.out }}", "timeout": "3d"}) == {}
    assert "config.duration" in diags({})["E097"][0]
    assert "is not a duration" in diags({"duration": "soon"})["E097"][0]
    assert "up to 366 days" in diags({"duration": "2 years"} | {"duration": "800d"})["E097"][0]
    assert "template error" in diags({"duration": "{{ state.out "})["E097"][0]
    assert "config.until" in diags({"mode": "until"})["E097"][0]
    assert "time zone" in diags({"mode": "until", "until": "2026-12-24", "timezone": "Nowhere/Land"})["E097"][0]
    missing = diags({"mode": "event"})["E097"]
    assert any("config.event" in m for m in missing) and any("time limit" in m for m in missing)
    assert "about runs themselves" in diags({"mode": "event", "event": "run.completed", "timeout": "1h"})["E097"][0]
    assert "config.event_key" in diags({"mode": "event", "event": "invoice.paid", "timeout": "1h"})["W096"][0]


def test_other_frameworks_refuse_it_and_say_which_can():
    wf = Workflow.model_validate(wait_ir({"duration": "1h"}, framework="openai_agents"))
    found = [d for d in validate(wf) if d.code == "P001"]
    assert found and found[0].node_id == "n_wait" and "LangGraph" in (found[0].hint or "")


# ------------------------------------------------------------------------------- interpreter and code ---
def compiled(wf: Workflow, tmp_path, checkpointer):
    path = tmp_path / "wait_graph.py"
    path.write_text(generate(wf))
    spec = importlib.util.spec_from_file_location("wait_graph", path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["wait_graph"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module.build_graph(Resources(), checkpointer=checkpointer)


@pytest.mark.parametrize("mode", ["interpreter", "generated"])
async def test_a_wait_pauses_with_its_due_time_and_continues_with_what_woke_it(tmp_path, mode):
    wf = Workflow.model_validate(wait_ir({"duration": "2h", "output": "woke"}, after="{{ state.woke.timed_out }}"))
    graph = (build_graph(wf, Resources(), checkpointer=InMemorySaver()) if mode == "interpreter"
             else compiled(wf, tmp_path, InMemorySaver()))  # fmt: skip
    cfg = {"configurable": {"thread_id": "t1"}}
    before = datetime.now(UTC)
    paused = await graph.ainvoke({}, cfg)
    asked = paused["__interrupt__"][0]
    assert asked.value["kind"] == "timer" and asked.value["canvas_node_id"] == "n_wait"
    due = datetime.fromisoformat(asked.value["due_at"])
    assert before + timedelta(hours=2) <= due <= datetime.now(UTC) + timedelta(hours=2)
    value = waits.woken(at=due)
    done = await graph.ainvoke(Command(resume={asked.id: value}), cfg)
    assert done == {"out": "False", "woke": value}


async def test_an_event_wait_names_its_event_and_key():
    wf = Workflow.model_validate(
        wait_ir({"mode": "event", "event": "invoice.paid", "event_key": "inv-{{ state.out }}", "timeout": "1d"})
    )
    graph = build_graph(wf, Resources(), checkpointer=InMemorySaver())
    paused = await graph.ainvoke({"out": "42"}, {"configurable": {"thread_id": "t2"}})
    assert {k: paused["__interrupt__"][0].value[k] for k in ("mode", "event", "key")} == {
        "mode": "event", "event": "invoice.paid", "key": "inv-42"
    }  # fmt: skip


async def test_a_time_already_past_and_test_runs_do_not_wait():
    past = wait_ir({"mode": "until", "until": "2020-01-01T00:00Z", "output": "woke"})
    graph = build_graph(Workflow.model_validate(past), Resources(), checkpointer=InMemorySaver())
    done = await graph.ainvoke({}, {"configurable": {"thread_id": "t3"}})
    assert done["out"] == "done" and done["woke"]["timed_out"] is False
    long = wait_ir({"mode": "event", "event": "a.b", "timeout": "3d", "output": "woke"})
    graph = build_graph(Workflow.model_validate(long), Resources(), checkpointer=InMemorySaver())
    done = await graph.ainvoke({}, {"configurable": {"thread_id": "t4", SKIP_WAITS: True}})
    assert done["out"] == "done" and done["woke"] | {"woke_at": None} == {
        "timed_out": True, "event": None, "woke_at": None, "skipped": True
    }  # fmt: skip


# --------------------------------------------------------------------------------------- the server ---
def job(app) -> TimerJob:
    return app.state.timers


async def paused(app, api_client, config: dict, *, thread_id: str = "th_wait") -> tuple[str, InterruptTask]:
    wf = await make(api_client, wait_ir(config | {"output": "woke"}, after="{{ state.woke.timed_out }}"))
    r = await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={"thread_id": thread_id})
    fin = events(r.text)[-1]
    assert fin["status"] == "interrupted" and fin["interrupts"][0]["kind"] == "timer", fin
    async with app.state.db.sessionmaker() as s:
        item = await s.scalar(select(InterruptTask).where(InterruptTask.thread_id == thread_id))
    assert item is not None
    return wf, item


@dataclass
class Finished:
    status: str
    origin: str | None
    error: str | None
    output: dict


async def finished(app, thread_id: str) -> Finished:
    """The conversation's last run once it is over, with the output its RUN_FINISHED event carried."""
    for _ in range(200):
        async with app.state.db.sessionmaker() as s:
            q = select(Run).options(undefer(Run.events)).where(Run.thread_id == thread_id)
            run = await s.scalar(q.order_by(Run.id.desc()).limit(1))
        if run is not None and run.status != "running" and thread_id not in app.state.runner.active_by_thread:
            fin = next((e for e in reversed(run.events or []) if e.get("type") == "RUN_FINISHED"), {})
            return Finished(run.status, run.origin, run.error, fin.get("output") or {})
        await asyncio.sleep(0.05)
    raise AssertionError("the run did not finish")


async def test_a_waiting_run_holds_nothing_and_continues_when_due_on_one_replica(app, api_client):
    _, item = await paused(app, api_client, {"duration": "1h"})
    assert item.kind == "timer" and item.status == "pending" and item.noticed == "timer"
    assert item.due_at is not None and item.wait_event is None and item.approvers is None
    assert "th_wait" not in app.state.runner.active_by_thread  # no thread, task or connection held while it waits
    assert await job(app).tick(datetime.now(UTC)) == []  # not due yet
    later = item.due_at + timedelta(seconds=1)
    twin = TimerJob(app.state.db, app.state.runner, job(app).target)  # another replica ticking at the same moment
    started = await asyncio.gather(job(app).tick(later), twin.tick(later))
    assert sum(len(s) for s in started) == 1  # claimed by one of them only
    run = await finished(app, "th_wait")
    assert (
        run.status == "success" and run.origin == "timer" and run.output == {"out": "False", "woke": run.output["woke"]}
    )
    async with app.state.db.sessionmaker() as s:
        done = await s.get(InterruptTask, item.id)
    assert done is not None and done.status == "decided" and done.decided_by is None
    assert await job(app).tick(later + timedelta(minutes=10)) == []  # once


async def test_nobody_can_answer_a_wait_step(app, api_client):
    _, item = await paused(app, api_client, {"duration": "1h"})
    for path, body in [
        ("/v1/threads/th_wait/resume", {"value": {"timed_out": False}}),
        ("/v1/threads/th_wait/resume", {"value": "go", "interrupt_id": item.id}),
        (f"/v1/inbox/{item.id}/decision", {"value": "go"}),
    ]:
        r = await api_client.post(path, json=body)
        assert r.status_code == 409 and "Wait step" in r.json()["detail"], (path, r.text)
    async with app.state.db.sessionmaker() as s:
        assert (await s.get(InterruptTask, item.id)).status == "pending"  # type: ignore[union-attr]


async def test_an_event_with_its_key_ends_the_wait_and_another_project_never_does(app, api_client):
    config = {"mode": "event", "event": "invoice.paid", "event_key": "inv-1", "timeout": "3d"}
    _, item = await paused(app, api_client, config)
    assert (item.wait_event, item.wait_key) == ("invoice.paid", "inv-1")
    other = (await api_client.post("/v1/projects", json={"name": "Other"})).json()["id"]
    sent = [
        (other, {"type": "invoice.paid", "data": {"key": "inv-1"}}),  # another project: not its run
        ("prj_default", {"type": "invoice.paid", "data": {"key": "inv-2"}}),  # another key
        ("prj_default", {"type": "invoice.sent", "data": {"key": "inv-1"}}),  # another type
    ]
    for project, body in sent:
        r = await api_client.post(f"/v1/projects/{project}/events", json=body)
        assert r.status_code == 200 and r.json()["woke"] == [], r.text
    async with app.state.db.sessionmaker() as s:
        assert (await s.get(InterruptTask, item.id)).status == "pending"  # type: ignore[union-attr]
    r = await api_client.post("/v1/projects/prj_default/events",
                              json={"type": "invoice.paid", "data": {"key": "inv-1", "amount": 12}})  # fmt: skip
    assert r.status_code == 200 and len(r.json()["woke"]) == 1 and r.json()["status"] == "handled"
    run = await finished(app, "th_wait")
    woke = run.output["woke"]
    assert run.status == "success" and woke["timed_out"] is False
    assert woke["event"]["type"] == "invoice.paid" and woke["event"]["data"] == {"key": "inv-1", "amount": 12}
    assert await job(app).tick(datetime.now(UTC) + timedelta(days=4)) == []  # its time limit no longer applies


async def test_an_event_wait_times_out(app, api_client):
    _, item = await paused(
        app, api_client, {"mode": "event", "event": "invoice.paid", "event_key": "x", "timeout": "1h"}
    )
    assert len(await job(app).tick(item.due_at + timedelta(seconds=1))) == 1  # type: ignore[operator]
    run = await finished(app, "th_wait")
    assert run.status == "success" and run.output["out"] == "True" and run.output["woke"]["event"] is None


async def test_a_new_message_ends_the_wait(app, api_client):
    wf, item = await paused(app, api_client, {"duration": "1h"})
    r = await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={"thread_id": "th_wait"})
    assert events(r.text)[-1]["status"] == "interrupted"  # it waits again, at a new Wait step
    async with app.state.db.sessionmaker() as s:
        items = (await s.scalars(select(InterruptTask).where(InterruptTask.thread_id == "th_wait"))).all()
    assert {i.id: i.status for i in items}[item.id] == "cancelled"  # the old wait never wakes the run that moved on
    assert sorted(i.status for i in items) == ["cancelled", "pending"]


async def test_a_wait_a_finished_run_left_behind_never_wakes_the_conversation(app, api_client):
    _, item = await paused(app, api_client, {"duration": "1h"})
    async with app.state.db.sessionmaker() as s:  # as if a parallel branch's Wait had passed its time meanwhile
        stale = InterruptTask(
            id="intr_stale", thread_id="th_wait", run_id=item.run_id, canvas_node_id="n_other", kind="timer",
            payload={"kind": "timer"}, due_at=item.due_at, noticed="timer",
        )  # fmt: skip
        s.add(stale)
        await s.commit()
    assert len(await job(app).tick(item.due_at + timedelta(seconds=1))) == 1  # type: ignore[operator]
    assert (await finished(app, "th_wait")).status == "success"
    async with app.state.db.sessionmaker() as s:
        assert (await s.get(InterruptTask, "intr_stale")).status == "cancelled"  # type: ignore[union-attr]
    assert await job(app).tick(item.due_at + timedelta(minutes=5)) == []  # type: ignore[operator]


async def test_a_wait_whose_workflow_is_gone_ends_and_a_busy_one_waits(app, api_client):
    _, item = await paused(app, api_client, {"duration": "1h"})
    later = item.due_at + timedelta(seconds=1)  # type: ignore[operator]
    app.state.runner.active_by_thread["th_wait"] = "run_other"  # busy here: not even claimed
    try:
        assert await job(app).tick(later) == []
    finally:
        del app.state.runner.active_by_thread["th_wait"]
    async with app.state.db.sessionmaker() as s:
        assert (await s.get(InterruptTask, item.id)).claimed_until is None  # type: ignore[union-attr]

    async def gone(s, thread_id, version_id):
        from agentcanvas.errors import NotFound

        raise NotFound("the deployment of this conversation no longer exists")

    assert await TimerJob(app.state.db, app.state.runner, gone).tick(later) == []
    async with app.state.db.sessionmaker() as s:
        assert (await s.get(InterruptTask, item.id)).status == "cancelled"  # type: ignore[union-attr]
        run = await s.scalar(select(Run).where(Run.thread_id == "th_wait"))
        thread = await s.get(Thread, "th_wait")
    assert run.status == "cancelled" and "no longer exists" in (run.error or "")  # type: ignore[union-attr]
    assert thread.status == "idle"  # type: ignore[union-attr]


async def test_a_failed_start_is_tried_again_later(app, api_client):
    _, item = await paused(app, api_client, {"duration": "1h"})
    later = item.due_at + timedelta(seconds=1)  # type: ignore[operator]

    async def broken(s, thread_id, version_id):
        raise RuntimeError("the database went away")

    assert await TimerJob(app.state.db, app.state.runner, broken).tick(later) == []
    assert await job(app).tick(later) == []  # held for a while
    async with app.state.db.sessionmaker() as s:
        await s.execute(update(InterruptTask).where(InterruptTask.id == item.id).values(claimed_until=None))
        await s.commit()
    assert len(await job(app).tick(later)) == 1


async def test_an_event_a_run_emits_ends_a_wait_in_a_project_without_handlers(app, api_client):
    await paused(app, api_client, {"mode": "event", "event": "invoice.paid", "event_key": "INV-7", "timeout": "1d"})
    src = "from agentcanvas.sdk import emit\n\n\ndef run(state):\n"
    src += "    emit('invoice.paid', {'key': 'INV-7'})\n    return {}\n"
    await api_client.put("/v1/projects/prj_default/files/scripts/pay.py", json={"content": src})
    pay = {"id": "n_pay", "type": "core.script", "name": "pay", "config": {"import_path": "scripts.pay:run"}}
    payer = await make(api_client, linear(pay))
    await api_client.post(f"/v1/workflows/{payer}/runs/stream", json={"thread_id": "th_payer"})
    for h in list(app.state.runner.handles.values()):  # its after-hooks run in the run's task
        if h.task is not None:
            await asyncio.wait_for(h.task, 10)
    run = await finished(app, "th_wait")
    assert run.status == "success" and run.origin == "event" and run.output["woke"]["event"]["data"] == {"key": "INV-7"}
