"""Withdrawn Inbox items: a question the conversation moved past (its state was edited, it went back to an earlier
point, a new message started it over) can never be answered. Its Inbox item is withdrawn rather than left waiting,
where every answer used to be refused as an unknown interrupt and the item went back to wait."""

from __future__ import annotations

from datetime import UTC, datetime

from test_retention import age
from test_strict_resume import SIGNERS, events, make, parallel, paused, waiting

from agentcanvas.db.models import InterruptTask, Thread
from agentcanvas.ir.models import Workflow
from agentcanvas.services.approvers import WITHDRAWN


async def statuses(app, thread: str) -> dict[str, str]:
    async with app.state.db.sessionmaker() as s:
        rows = (await s.execute(InterruptTask.__table__.select().where(InterruptTask.thread_id == thread))).all()
        return {r.id: r.status for r in rows}


async def thread_status(app, thread: str) -> str:
    async with app.state.db.sessionmaker() as s:
        return (await s.get(Thread, thread)).status  # type: ignore[union-attr]


async def withdrawn(api, thread: str) -> set[str]:
    items = (await api.get("/v1/inbox", params={"status": "withdrawn"})).json()
    return {i["id"] for i in items if i["thread_id"] == thread}


def one_approval() -> dict:
    """A single approval after START."""
    ir = parallel()
    ir["nodes"] = ir["nodes"][:1]
    ir["edges"] = [e for e in ir["edges"] if e["id"] in ("in_n_a", "out_n_a")]
    ir["state"]["channels"] = [{"name": "who"}, {"name": "ok_a"}]
    return ir


async def test_a_state_edit_withdraws_the_questions_it_moved_past(app, api_client):
    api = api_client
    wf = await make(api, parallel())
    asked = await paused(api, wf, "th_edit")
    assert await thread_status(app, "th_edit") == "interrupted"

    r = await api.post("/v1/threads/th_edit/state", json={"values": {"who": "y"}, "as_node": "n_a"})
    assert r.status_code == 200, r.text
    assert sorted(r.json()["withdrawn"]) == sorted(asked.values())
    assert await waiting(api, "th_edit") == set()  # out of the pending list (and its counts)
    assert await withdrawn(api, "th_edit") == set(asked.values())
    assert await thread_status(app, "th_edit") == "idle"  # nothing waits for a person: retention may take it

    refused = await api.post(f"/v1/inbox/{asked['n_a']}/decision", json={"value": True})
    assert refused.status_code == 409 and refused.json()["detail"] == WITHDRAWN
    unnamed = await api.post("/v1/threads/th_edit/resume", json={"value": True})  # not given to the next question
    assert unnamed.status_code == 409 and "withdrawn" in unnamed.json()["detail"], unnamed.text
    named = await api.post("/v1/threads/th_edit/resume", json={"value": True, "interrupt_id": asked["n_b"]})
    assert named.status_code == 409 and named.json()["detail"] == WITHDRAWN

    audit = (await api.get("/v1/org/audit", params={"action": "edit_state"})).json()
    rows = audit["items"] if isinstance(audit, dict) else audit
    assert any(sorted((row.get("detail") or {}).get("withdrawn") or []) == sorted(asked.values()) for row in rows)


async def test_an_answer_the_engine_refuses_withdraws_its_item(app, api_client):
    """The backstop: the state changed without the Inbox hearing of it (here: written straight through the
    framework). The answer is refused by LangGraph, and the item is withdrawn instead of waiting again."""
    api = api_client
    wf_id = await make(api, parallel())
    asked = await paused(api, wf_id, "th_backstop")
    runner = app.state.runner
    wf = Workflow.model_validate((await api.get(f"/v1/workflows/{wf_id}")).json()["ir"])
    await runner.adapter_for(wf).update_state(wf, "th_backstop", runner.resources("th_backstop", wf), {"who": "z"},
                                              "n_a", None)  # fmt: skip
    assert await waiting(api, "th_backstop") == set(SIGNERS)  # the Inbox does not know yet

    r = await api.post(f"/v1/inbox/{asked['n_a']}/decision", params={"stream": "true"}, json={"value": True})
    last = events(r.text)[-1]
    assert last["type"] == "RUN_ERROR" and last["code"] == "unknown_interrupt", last
    assert "withdrawn" in last["message"] and last["interrupt_ids"] == [asked["n_a"]]
    assert await statuses(app, "th_backstop") == {asked["n_a"]: "withdrawn", asked["n_b"]: "pending"}
    assert await thread_status(app, "th_backstop") == "interrupted"  # the other one still waits

    r = await api.post(f"/v1/inbox/{asked['n_b']}/decision", params={"stream": "true"}, json={"value": True})
    assert events(r.text)[-1]["code"] == "unknown_interrupt"
    assert set((await statuses(app, "th_backstop")).values()) == {"withdrawn"}
    assert await thread_status(app, "th_backstop") == "idle"
    async with app.state.db.sessionmaker() as s:
        row = await s.get(InterruptTask, asked["n_b"])
        assert row is not None and row.decision is None and row.decided_by is None and row.decided_at is not None


async def test_a_new_message_withdraws_the_question_it_started_over_from(app, api_client):
    api = api_client
    wf = await make(api, one_approval())
    first = await api.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"who": "x"}, "thread_id": "th_new"})
    (old,) = [i["id"] for i in events(first.text)[-1]["interrupts"]]
    again = await api.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"who": "y"}, "thread_id": "th_new"})
    fin = events(again.text)[-1]
    assert fin["status"] == "interrupted", fin
    (new,) = [i["id"] for i in fin["interrupts"]]
    assert new != old
    assert await statuses(app, "th_new") == {old: "withdrawn", new: "pending"}
    r = await api.post(f"/v1/inbox/{new}/decision", params={"stream": "true"}, json={"value": True})
    assert events(r.text)[-1]["status"] == "success"


async def test_going_back_to_an_earlier_point_withdraws_the_question_left_behind(app, api_client):
    api = api_client
    wf = await make(api, one_approval())
    first = await api.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"who": "x"}, "thread_id": "th_fork"})
    (old,) = [i["id"] for i in events(first.text)[-1]["interrupts"]]
    history = (await api.get("/v1/threads/th_fork/history")).json()
    start = next(c for c in history if c["source"] == "input")  # before the run began: it asks again from there
    r = await api.post("/v1/threads/th_fork/fork", json={"checkpoint_id": start["checkpoint_id"]})
    fin = events(r.text)[-1]
    assert fin["status"] == "interrupted", fin
    (new,) = [i["id"] for i in fin["interrupts"]]
    assert new != old and await statuses(app, "th_fork") == {old: "withdrawn", new: "pending"}


async def test_a_withdrawn_question_does_not_keep_its_conversation(app, api_client):
    """Retention keeps a conversation that waits for a person; one whose questions were withdrawn waits for nobody."""
    api = api_client
    wf = await make(api, parallel())
    await paused(api, wf, "th_kept")
    await api.post("/v1/threads/th_kept/state", json={"values": {"who": "y"}, "as_node": "n_a"})
    await age(app, "th_kept", 400)
    assert "th_kept" in await app.state.retention.tick(datetime.now(UTC))
