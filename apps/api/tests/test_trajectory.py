"""The trajectory of a conversation (G-10): kept run events, helper agents told apart, the tree and its endpoint."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from langchain_core.messages import AIMessageChunk
from test_runs_v1 import triage_api  # noqa: F401 - fixture

from agentcanvas.adapters.langchain.events import UsageTracker, translate
from agentcanvas.ir.models import Workflow
from agentcanvas.runtime import trajectory as kept
from agentcanvas.services.trajectory import build_run

MS = 1_000_000


def test_kept_events_join_text_and_leave_state_out():
    events = [
        {"type": "RUN_STARTED", "mode": "normal", "adapter": "langchain"},
        {"type": "STEP_STARTED", "canvas_node_id": "n_a", "task_id": "t1", "stepName": "a"},
        {"type": "TEXT_MESSAGE_CONTENT", "canvas_node_id": "n_a", "delta": "Hel"},
        {"type": "TEXT_MESSAGE_CONTENT", "canvas_node_id": "n_a", "delta": "lo"},
        {"type": "TEXT_MESSAGE_CONTENT", "canvas_node_id": "n_a", "delta": "!", "agent": "researcher"},
        {"type": "STATE_DELTA", "canvas_node_id": "n_a", "delta": {"x": "y" * 50_000}},
        {"type": "CUSTOM", "name": "ac.tool_io", "value": {"tool": "t", "args": {}, "output": "o" * 20_000}},
        {"type": "STEP_FINISHED", "canvas_node_id": "n_a", "task_id": "t1", "status": "ok", "duration_ms": 5},
        {"type": "RUN_FINISHED", "status": "success", "duration_ms": 9, "output": {"out": "Hello"}},
    ]
    out = kept.compact(events, [0, 1 * MS, 2 * MS, 3 * MS, 4 * MS, 5 * MS, 6 * MS, 7 * MS], 10 * MS)
    assert [e["type"] for e in out] == [
        "RUN_STARTED", "STEP_STARTED", "TEXT", "TEXT", "ac.tool_io", "STEP_FINISHED", "RUN_FINISHED",
    ]  # fmt: skip
    assert out[2]["text"] == "Hello" and out[3] == {"type": "TEXT", "t": 4, "node": "n_a", "text": "!",
                                                    "agent": "researcher"}  # fmt: skip
    assert len(out[4]["value"]["output"]) == kept.MAX_TEXT + 1  # cut, with an ellipsis
    assert out[-1]["t"] == 10 and out[-1]["output"] == {"out": "Hello"}  # not published yet when the run is saved


def test_a_very_long_run_keeps_its_first_events_and_its_end():
    events = [{"type": "CUSTOM", "name": "ac.usage", "value": {}} for _ in range(kept.MAX_EVENTS + 50)]
    out = kept.compact([*events, {"type": "RUN_ERROR", "code": "x", "message": "boom"}], [], 0)
    assert len(out) == kept.MAX_EVENTS and out[-2]["type"] == "ac.truncated" and out[-1]["type"] == "RUN_ERROR"


def test_events_name_the_helper_agent_that_produced_them():
    meta = {"lc_agent_name": "researcher", "langgraph_node": "model"}
    part = {"type": "messages", "ns": ("planner:1", "tools:2"), "data": (AIMessageChunk(content="found it"), meta)}
    [ev] = translate(part, {"planner": "n_plan"})
    assert ev == {"type": "TEXT_MESSAGE_CONTENT", "canvas_node_id": "n_plan", "delta": "found it",
                  "agent": "researcher"}  # fmt: skip
    top = {"type": "messages", "ns": (), "data": (AIMessageChunk(content="hi"), {"langgraph_node": "planner"})}
    assert "agent" not in next(iter(translate(top, {"planner": "n_plan"})))

    tracker = UsageTracker({"planner": "n_plan"})
    run_id = uuid.uuid4()
    md = {"checkpoint_ns": "planner:1|tools:2", "ac_resource": "model", "lc_agent_name": "researcher"}
    tracker.on_chat_model_start({}, [], run_id=run_id, metadata=md)
    msg = SimpleNamespace(content="done", tool_calls=[], usage_metadata=None)
    tracker.on_llm_end(SimpleNamespace(generations=[[SimpleNamespace(message=msg)]], llm_output={}), run_id=run_id)
    assert tracker.drain_replies() == [{"resource": "model", "canvas_node_id": "n_plan", "reply": "done",
                                        "agent": "researcher"}]  # fmt: skip
    tool = uuid.uuid4()
    tracker.on_tool_start({"name": "search"}, "q", run_id=tool, metadata={"ac_resource": "web", "lc_agent_name": "r"},
                          inputs={"q": "x"})  # fmt: skip
    tracker.on_tool_end("result", run_id=tool)
    assert tracker.drain_tool_io() == [
        {"resource": "web", "tool": "search", "args": {"q": "x"}, "agent": "r", "output": "result"}
    ]  # no step known: kept without one
    tool = uuid.uuid4()
    md = {"ac_resource": "web", "lc_agent_name": "r", "checkpoint_ns": "planner:1|tools:2"}
    tracker.on_tool_start({"name": "search"}, "q", run_id=tool, metadata=md, inputs={"q": "x"})
    tracker.on_tool_end("result", run_id=tool)
    assert tracker.drain_tool_io() == [{"resource": "web", "canvas_node_id": "n_plan", "tool": "search",
                                        "args": {"q": "x"}, "agent": "r", "output": "result"}]  # fmt: skip


def _wf() -> Workflow:
    inner = {"id": "sub", "name": "Sub", "nodes": [{"id": "n_in", "type": "core.set", "name": "inner"}], "edges": []}
    return Workflow.model_validate({
        "id": "wf", "name": "Team",
        "nodes": [
            {"id": "n_plan", "type": "agent.deep", "name": "planner", "label": "Planner"},
            {"id": "n_sub", "type": "core.subgraph", "name": "checks", "config": {"workflow": inner}},
            {"id": "n_end", "type": "core.set", "name": "wrap_up"},
        ],
        "metadata": {"groups": [{"id": "g1", "label": "Finishing", "color": "#000", "members": ["n_sub", "n_end"]}]},
    })  # fmt: skip


def test_the_tree_nests_helpers_subflows_and_frames():
    events = [
        {"type": "RUN_STARTED", "t": 0},
        {"type": "STEP_STARTED", "t": 1, "node": "n_plan", "task": "a"},
        {"type": "ac.model_reply", "t": 2, "node": "n_plan", "value": {"resource": "model", "agent": "planner",
         "reply": {"content": "", "tool_calls": [{"name": "task", "args": {"subagent_type": "researcher"}}]}}},
        {"type": "ac.model_reply", "t": 3, "node": "n_plan",
         "value": {"resource": "model", "agent": "researcher", "reply": {"content": "", "tool_calls": [
             {"name": "web_search", "args": {"q": "x"}}]}}},
        {"type": "ac.tool_io", "t": 4, "node": None,  # no step named: the one running
         "value": {"tool": "web_search", "resource": "web", "agent": "researcher", "args": {"q": "x"}, "output": "y"}},
        {"type": "ac.usage", "t": 5, "node": None, "value": {"canvas_node_id": "n_plan", "input_tokens": 10,
                                                              "output_tokens": 5}},
        {"type": "STEP_FINISHED", "t": 6, "node": "n_plan", "task": "a", "status": "ok", "duration_ms": 5},
        {"type": "ac.model_reply", "t": 6, "node": "n_plan", "value": {"resource": "model", "reply": "All done."}},
        {"type": "TEXT", "t": 6, "node": "n_plan", "text": "All done."},
        {"type": "STEP_STARTED", "t": 7, "node": "n_sub", "task": "b"},
        {"type": "STEP_STARTED", "t": 8, "node": "n_in", "task": "c"},
        {"type": "STEP_FINISHED", "t": 9, "node": "n_in", "task": "c", "status": "ok"},
        {"type": "STEP_FINISHED", "t": 10, "node": "n_sub", "task": "b", "status": "ok"},
        {"type": "STEP_STARTED", "t": 11, "node": "n_end", "task": "d"},
        {"type": "STEP_FINISHED", "t": 12, "node": "n_end", "task": "d", "status": "error", "error": "bad"},
        {"type": "ac.interrupt", "t": 12, "node": None, "value": {"id": "i1", "kind": "approval", "payload": {}}},
        {"type": "RUN_FINISHED", "t": 13, "status": "interrupted", "duration_ms": 13, "output": {"x": 1}},
    ]  # fmt: skip
    run = build_run("run_1", events, _wf(), status="running")
    assert run.status == "interrupted" and run.duration_ms == 13 and run.tokens == 15
    plan, finishing, waiting = run.children
    assert (plan.kind, plan.label, plan.status, plan.duration_ms, plan.tokens) == ("step", "Planner", "ok", 5, 15)
    delegate, helper, answer = plan.children  # the streamed text is the same as the model's reply: said once
    assert delegate.label == "model: hands a task to researcher"
    assert helper.kind == "agent" and helper.label == "Helper agent researcher"
    assert [c.label for c in helper.children] == ["model: asks for web_search", "Tool web_search"]
    assert answer.label == "model: All done." and answer.start_ms == 6  # drained after its step ended
    assert finishing.kind == "group" and finishing.label == "Finishing" and finishing.status == "error"
    checks, wrap_up = finishing.children
    assert [c.label for c in checks.children] == ["Inner"] and wrap_up.detail == {"error": "bad"}
    assert finishing.start_ms == 7 and finishing.duration_ms == 5
    assert waiting.kind == "interrupt" and waiting.label == "Waits for a person (approval)"
    assert len({n.id for n in _all(run)}) == len(list(_all(run)))


def _all(n):
    yield n
    for c in n.children:
        yield from _all(c)


def test_a_run_from_before_trajectories_says_so():
    run = build_run("run_old", None, None, status="success")
    assert [c.kind for c in run.children] == ["note"]


async def test_the_endpoint_shows_a_finished_conversation(app, triage_api):  # noqa: F811
    api, wf = triage_api, triage_api.wf_id
    r = await api.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"ticket": "Invoice?"}, "thread_id": "th_low"})
    assert r.status_code == 200, r.text
    [item] = (await api.get("/v1/inbox", params={"workflow_id": wf})).json()  # the reply tool asks first
    r = await api.post(f"/v1/inbox/{item['id']}/decision", params={"stream": "true"},
                       json={"value": {"decisions": [{"type": "approve"}]}})  # fmt: skip
    assert r.status_code == 200, r.text
    app.state.runner.handles.clear()  # the events have left memory: the tree comes from the database
    r = await api.get("/v1/threads/th_low/trajectory")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["thread_id"] == "th_low" and body["workflow_id"] == wf and len(body["runs"]) == 2
    first, second = body["runs"]
    assert first["label"] == "Run 1" and first["status"] == "interrupted" and first["started_at"]
    assert [c["label"] for c in first["children"] if c["kind"] == "step"] == ["Classify ticket", "Route urgency",
                                                                              "Reply drafter"]  # fmt: skip
    assert first["children"][-1]["kind"] == "interrupt" and first["children"][-1]["canvas_node_id"] == "n_ag"
    assert second["status"] == "success" and second["detail"]["output"]
    drafter = next(c for c in second["children"] if c["kind"] == "step")
    assert drafter["canvas_node_id"] == "n_ag" and drafter["status"] == "ok" and drafter["duration_ms"] is not None
    tools = [c for c in drafter["children"] if c["kind"] == "tool"]
    assert tools and tools[0]["label"] == "Tool send_reply" and tools[0]["detail"]["args"]
    assert (await api.get("/v1/threads/th_nope/trajectory")).status_code == 404
