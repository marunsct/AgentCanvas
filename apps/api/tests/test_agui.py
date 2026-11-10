"""F-8: a deployment speaks AG-UI 1.0 (`POST /v1/deployments/{id}/ag-ui`), checked with the `ag-ui-protocol` package.

Every streamed event is parsed with the package's own models (its validators) and the stream is checked with the
ordering rules of the reference client's verifier (`@ag-ui/client` verify.ts), ported below.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from ag_ui.core import PROTOCOL_VERSION, Event, Interrupt
from ag_ui.encoder import EventEncoder
from pydantic import TypeAdapter
from sqlalchemy import select
from test_events import linear, make_wf
from test_protocols import _publish
from test_publish import bearer, make_greeter
from test_versions import HIGH_CASE, edit_node, new_triage

from agentcanvas.db.models import AuditLog, InterruptTask
from agentcanvas.runtime.agui import Translator, encode

EVENT = TypeAdapter(Event)
P = "/v1/projects/prj_default"


def parse(text: str) -> list[Any]:
    """The SSE body as AG-UI events, each validated by the package's models (keepalive comments skipped)."""
    out = []
    for frame in text.split("\n\n"):
        data = [line[6:] for line in frame.splitlines() if line.startswith("data: ")]
        if data:
            out.append(EVENT.validate_json("\n".join(data)))
    return out


def verify(events: list[Any]) -> None:
    """The reference client's sequence rules (verify.ts) for the events this server sends."""
    assert events and events[0].type == "RUN_STARTED", "first event must be RUN_STARTED"
    texts, tools, steps, spans, thoughts = set(), set(), Counter(), set(), set()
    ended = False
    for ev in events[1:]:
        t = ev.type
        assert not ended, f"{t} after the run ended"
        if t == "TEXT_MESSAGE_START":
            assert ev.message_id not in texts, "text message already in progress"
            texts.add(ev.message_id)
        elif t in ("TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END"):
            assert ev.message_id in texts, f"no active text message {ev.message_id}"
            if t == "TEXT_MESSAGE_END":
                texts.discard(ev.message_id)
        elif t == "TOOL_CALL_START":
            assert ev.tool_call_id not in tools, "tool call already in progress"
            tools.add(ev.tool_call_id)
        elif t in ("TOOL_CALL_ARGS", "TOOL_CALL_END"):
            assert ev.tool_call_id in tools, f"no active tool call {ev.tool_call_id}"
            if t == "TOOL_CALL_END":
                tools.discard(ev.tool_call_id)
        elif t == "STEP_STARTED":
            assert steps[ev.step_name] == 0, f"step {ev.step_name} is already active"
            steps[ev.step_name] += 1
        elif t == "STEP_FINISHED":
            assert steps[ev.step_name] == 1, f"step {ev.step_name} was not started"
            steps[ev.step_name] -= 1
        elif t == "REASONING_START":
            spans.add(ev.message_id)
        elif t == "REASONING_END":
            assert ev.message_id in spans
            spans.discard(ev.message_id)
        elif t == "REASONING_MESSAGE_START":
            thoughts.add(ev.message_id)
        elif t in ("REASONING_MESSAGE_CONTENT", "REASONING_MESSAGE_END"):
            assert ev.message_id in thoughts
            if t == "REASONING_MESSAGE_END":
                thoughts.discard(ev.message_id)
        elif t == "RUN_FINISHED":
            assert not texts and not tools and not +steps and not spans and not thoughts, "RUN_FINISHED with open items"
            ended = True
        elif t == "RUN_ERROR":
            ended = True
        elif t == "RUN_STARTED":
            raise AssertionError("RUN_STARTED while a run is active")
    assert ended, "the stream must end with RUN_FINISHED or RUN_ERROR"


def types(events: list[Any]) -> list[str]:
    return [e.type for e in events]


def text_of(events: list[Any]) -> str:
    return "".join(e.delta for e in events if e.type == "TEXT_MESSAGE_CONTENT")


def run_input(thread: str, *messages: dict, **extra: Any) -> dict:
    return {"threadId": thread, "runId": f"r_{thread}_{len(messages)}", "messages": list(messages), **extra}


def user(text: str, id_: str = "u1") -> dict:
    return {"id": id_, "role": "user", "content": text}


async def chat_deployment(app, api, replies: list[Any]) -> tuple[str, str, str]:
    from agentcanvas.testing import DemoResources

    app.state.resources_factory = lambda thread_id, wf: DemoResources().with_mocks({"chat_model": replies})
    wf = (await api.post(f"{P}/workflows", json={"name": "Chat", "template": "chat_assistant"})).json()
    dep, key = await _publish(api, wf["id"])
    return wf["id"], dep, key


async def ag_ui(api, dep: str, key: str, body: dict) -> list[Any]:
    r = await api.post(f"/v1/deployments/{dep}/ag-ui", json=body, headers=bearer(key))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream") and r.headers["x-ai-generated"] == "true"
    events = parse(r.text)
    verify(events)
    return events


async def test_a_chat_turn_streams_ag_ui_events_and_the_thread_continues(app, api_client):
    _, dep, key = await chat_deployment(app, api_client, ["Hello there", "Again, hello"])
    first = await ag_ui(api_client, dep, key, run_input("th-agui-1", user("hi")))
    started, finished = first[0], first[-1]
    assert started.thread_id == "th-agui-1" and started.run_id == "r_th-agui-1_1"
    assert started.protocol_version == PROTOCOL_VERSION == "1.0"
    assert started.metadata["agentcanvas"]["run_id"].startswith("run_")
    assert ["STEP_STARTED", "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END", "STEP_FINISHED"] == [
        t for t in types(first) if t.startswith(("STEP", "TEXT"))
    ]
    assert text_of(first) == "Hello there"
    assert finished.type == "RUN_FINISHED" and finished.outcome.type == "success"
    assert finished.metadata["agentcanvas"]["generated_by"]["kind"] == "ai"
    assert "messages" not in json.dumps([e.model_dump() for e in first if e.type == "STATE_SNAPSHOT"])

    history = [user("hi"), {"id": "a1", "role": "assistant", "content": "Hello there"}, user("again", "u2")]
    second = await ag_ui(api_client, dep, key, run_input("th-agui-1", *history))
    assert text_of(second) == "Again, hello"  # the model's second turn: the conversation went on
    thread = (await api_client.get("/v1/threads/th-agui-1")).json()
    said = [m["content"] for m in thread["values"]["messages"] if m.get("type") == "human"]
    assert said == ["hi", "again"]  # the history was not sent again


async def test_tools_the_application_runs_end_the_run_and_their_result_continues_it(app, api_client):
    """FR-X-03: a front-end tool is offered to the agent; its call ends the run with `pendingToolCallIds`, and the
    tool message with the result continues it. Two calls at once are answered together."""
    calls = {"content": "Let me show you.", "tool_calls": [{"name": "show_chart", "args": {"x": 1}},
                                                          {"name": "show_chart", "args": {"x": 2}}]}  # fmt: skip
    _, dep, key = await chat_deployment(app, api_client, [calls, "There you go."])
    tool = {"name": "show_chart", "description": "Draws a chart in the page",
            "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}}}  # fmt: skip
    first = await ag_ui(api_client, dep, key, run_input("th-tools", user("chart"), tools=[tool]))
    finished = first[-1]
    assert finished.outcome.type == "success" and finished.outcome.pending_tool_call_ids == ["mock_0_0", "mock_0_1"]
    starts = [e for e in first if e.type == "TOOL_CALL_START"]
    message = next(e for e in first if e.type == "TEXT_MESSAGE_START").message_id
    assert {(s.tool_call_id, s.tool_call_name, s.parent_message_id) for s in starts} == {
        ("mock_0_0", "show_chart", message),
        ("mock_0_1", "show_chart", message),
    }
    args = {e.tool_call_id: json.loads(e.delta) for e in first if e.type == "TOOL_CALL_ARGS"}
    assert args == {"mock_0_0": {"x": 1}, "mock_0_1": {"x": 2}}
    async with app.state.db.sessionmaker() as s:  # the application answers it, not a person: no Inbox item
        assert (await s.scalars(select(InterruptTask).where(InterruptTask.thread_id == "th-tools"))).all() == []

    asked = [{"id": c, "type": "function", "function": {"name": "show_chart", "arguments": "{}"}} for c in args]
    assistant = {"id": "a1", "role": "assistant", "content": "Let me show you.", "toolCalls": asked}
    one = {"id": "t1", "role": "tool", "toolCallId": "mock_0_0", "content": "drawn 1"}
    partial = run_input("th-tools", user("chart"), assistant, one, tools=[tool])
    r = await api_client.post(f"/v1/deployments/{dep}/ag-ui", json=partial, headers=bearer(key))
    assert r.status_code == 422 and "mock_0_1" in r.json()["detail"]  # every call must be answered

    two = {"id": "t2", "role": "tool", "toolCallId": "mock_0_1", "content": "drawn 2"}
    second = await ag_ui(api_client, dep, key, run_input("th-tools", user("chart"), assistant, one, two))
    assert text_of(second) == "There you go." and second[-1].outcome.pending_tool_call_ids is None
    thread = (await api_client.get("/v1/threads/th-tools")).json()
    results = {m["tool_call_id"]: m["content"] for m in thread["values"]["messages"] if m.get("type") == "tool"}
    assert results == {"mock_0_0": "drawn 1", "mock_0_1": "drawn 2"}


async def test_a_pause_ends_with_the_interrupt_outcome_and_resume_entries_answer_it(app, api_client):
    from agentcanvas.testing import DemoResources

    app.state.resources_factory = lambda thread_id, wf: DemoResources().with_mocks(HIGH_CASE["mocks"])
    wf = await new_triage(api_client)
    dep, key = await _publish(api_client, wf["id"])
    paused = await ag_ui(api_client, dep, key, run_input("th-triage", user("Site down")))
    outcome = paused[-1].outcome
    assert outcome.type == "interrupt" and len(outcome.interrupts) == 1
    asked: Interrupt = outcome.interrupts[0]
    assert asked.response_schema is not None and asked.metadata is not None
    assert asked.reason == "approval" and asked.response_schema["required"] == ["approve"]
    assert asked.metadata["agentcanvas"]["decided_in_agentcanvas"] is False
    snapshot = next(e for e in reversed(paused) if e.type == "STATE_SNAPSHOT").snapshot
    assert snapshot["summary"] == "ESCALATED: Site down" and snapshot["ticket"] == "Site down"
    assert any(e.type == "STATE_DELTA" and e.delta[0].path == "/summary" for e in paused)

    url = f"/v1/deployments/{dep}/ag-ui"
    again = await api_client.post(url, json=run_input("th-triage", user("more")), headers=bearer(key))
    assert again.status_code == 422 and asked.id in again.json()["detail"]  # not covered: refused before running
    bad = {"interruptId": asked.id, "status": "resolved", "payload": {"approve": "yes please"}}
    r = await api_client.post(url, json=run_input("th-triage", resume=[bad]), headers=bearer(key))
    assert r.status_code == 422 and "responseSchema" in r.json()["detail"]
    async with app.state.db.sessionmaker() as s:
        before = len((await s.scalars(select(AuditLog).where(AuditLog.action == "api_ag_ui"))).all())
    assert before == 0  # plain runs are not audited

    entry = {"interruptId": asked.id, "status": "resolved", "payload": {"approve": True}}
    unknown = {"interruptId": "nope", "status": "resolved"}
    done = await ag_ui(api_client, dep, key, run_input("th-triage", resume=[entry, unknown]))
    assert done[-1].outcome.type == "success" and done[-1].result["approved"] is True
    warned = [e.value["message"] for e in done if e.type == "CUSTOM" and e.name == "ac.warning"]
    assert any("'nope' ignored" in w for w in warned)
    async with app.state.db.sessionmaker() as s:  # the decision is audited, and kept on the Inbox item
        rows = (await s.scalars(select(AuditLog).where(AuditLog.action == "api_ag_ui"))).all()
        assert len(rows) == 1 and rows[0].detail["interrupt_ids"] == [asked.id]
        assert (await s.get(InterruptTask, asked.id)).decision == {"approved": True}


async def test_a_step_that_names_its_deciders_is_not_answered_over_ag_ui(app, api_client):
    from agentcanvas.testing import DemoResources

    app.state.resources_factory = lambda thread_id, wf: DemoResources().with_mocks(HIGH_CASE["mocks"])
    wf = await new_triage(api_client)
    await edit_node(api_client, wf["id"], "n_ok", approvers={"roles": ["reviewer"]})
    dep, key = await _publish(api_client, wf["id"])
    paused = await ag_ui(api_client, dep, key, run_input("th-routed", user("Site down")))
    asked = paused[-1].outcome.interrupts[0]
    assert asked.metadata["agentcanvas"]["decided_in_agentcanvas"] is True and "Inbox" in asked.message
    entry = {"interruptId": asked.id, "status": "resolved", "payload": {"approve": True}}
    r = await api_client.post(f"/v1/deployments/{dep}/ag-ui", json=run_input("th-routed", resume=[entry]),
                              headers=bearer(key))  # fmt: skip
    assert r.status_code == 403 and "Inbox" in r.json()["detail"]


async def test_parallel_pauses_are_answered_together_each_with_its_own_answer(app, api_client):
    nodes = [
        {"id": f"n_{x}", "type": "core.approval", "name": x, "config": {"show": "x", "output": f"ok_{x}"}}
        for x in ("a", "b")
    ]
    ir = linear("Two approvals", nodes, ["x", "ok_a", "ok_b"])
    ir["edges"] = [
        {"id": f"e_{n}_{d}", "kind": "flow", "source": {"node": s}, "target": {"node": d if s == "START" else "END"}}
        for n, (s, d) in enumerate([("START", "n_a"), ("START", "n_b"), ("n_a", "END"), ("n_b", "END")])
    ]
    wf = await make_wf(api_client, ir)
    dep, key = await _publish(api_client, wf)
    paused = await ag_ui(api_client, dep, key, run_input("th-two", state={"x": "contract"}))
    asked = paused[-1].outcome.interrupts
    assert len(asked) == 2
    by_node = {i.metadata["agentcanvas"]["canvas_node_id"]: i.id for i in asked}
    entries = [{"interruptId": by_node["n_a"], "status": "resolved", "payload": True},
               {"interruptId": by_node["n_b"], "status": "cancelled"}]  # fmt: skip
    done = await ag_ui(api_client, dep, key, run_input("th-two", resume=entries))
    assert done[-1].outcome.type == "success"
    assert (done[-1].result["ok_a"], done[-1].result["ok_b"]) == (True, False)
    async with app.state.db.sessionmaker() as s:
        assert (await s.get(InterruptTask, by_node["n_a"])).decision["approved"] is True
        assert (await s.get(InterruptTask, by_node["n_b"])).decision["approved"] is False


async def test_the_endpoint_refuses_what_it_should(app, api_client):
    dep, key = await _publish(api_client, await make_greeter(api_client))
    other, other_key = await _publish(api_client, await make_greeter(api_client, "Hi"))
    url = f"/v1/deployments/{dep}/ag-ui"
    body = run_input("th-mine", state={"who": "Ada"})
    assert (await api_client.post(url, json=body)).status_code == 401
    assert (await api_client.post(url, json=body, headers=bearer(other_key))).status_code == 401  # another's key
    done = await ag_ui(api_client, dep, key, body)
    assert done[-1].result["out"] == "Hello Ada" and text_of(done) == "Hello Ada"  # the answer as a message
    taken = await api_client.post(f"/v1/deployments/{other}/ag-ui", json=body, headers=bearer(other_key))
    assert taken.status_code == 409 and dep not in taken.text and "Greeter" not in taken.text
    for wrong, why in [
        ({**body, "threadId": "a/b"}, "threadId"),
        ({**body, "tools": [{"name": "no spaces", "description": ""}]}, "tool name"),
        ({**body, "tools": [{"name": "t", "description": "", "parameters": {"type": "string"}}]}, "type object"),
        ({**body, "threadId": "th-empty", "state": None}, "user message"),
        ({**body, "forwardedProps": {"user_id": 7}}, "user_id"),
    ]:
        r = await api_client.post(url, json=wrong, headers=bearer(key))
        assert r.status_code == 422 and why in r.text, (why, r.text)
    tools = [{"name": "t", "description": "a tool"}]
    plain = await ag_ui(api_client, dep, key, run_input("th-mine-2", state={"who": "Lin"}, tools=tools))
    assert plain[-1].outcome.type == "success"  # a workflow without agents simply does not use them


# ------------------------------------------------------------------------------------------ translator ----
def _translator(**kw: Any) -> Translator:
    defaults: dict[str, Any] = {
        "thread_id": "t", "run_id": "r", "agentcanvas_run_id": "run_1", "shown": {"n_ag"}, "fields": {"q", "a"},
        "reducers": set(), "snapshot": {"q": "hi"}, "answer": lambda out: "", "ask": lambda i: None,
    }  # fmt: skip
    return Translator(**{**defaults, **kw})


def _run(tr: Translator, events: list[dict]) -> list[Any]:
    """Translate, then encode with the package's encoder and parse back: what a client reads."""
    out = []
    for ev in events:
        for e in tr.feed(ev):
            assert encode(e) == EventEncoder().encode(e)
            out.extend(parse(encode(e)))
    verify(out)
    return out


def test_streamed_text_thinking_tool_calls_and_state_translate():
    events = [
        {"type": "RUN_STARTED", "runId": "run_1"},
        {"type": "STEP_STARTED", "stepName": "agent", "canvas_node_id": "n_ag"},
        {"type": "CUSTOM", "name": "ac.reasoning", "canvas_node_id": "n_ag", "value": {"delta": "Let me think"}},
        {"type": "TEXT_MESSAGE_CONTENT", "canvas_node_id": "n_ag", "delta": "Look"},
        {"type": "TEXT_MESSAGE_CONTENT", "canvas_node_id": "n_ag", "delta": "ing it up"},
        {"type": "TEXT_MESSAGE_CONTENT", "canvas_node_id": "n_draft", "delta": "a hidden draft"},
        {
            "type": "CUSTOM",
            "name": "ac.usage",
            "value": {"model": "anthropic:claude-x", "input_tokens": 10, "output_tokens": 4, "cache_read_tokens": 6},
        },
        {"type": "CUSTOM", "name": "ac.model_reply", "value": {"canvas_node_id": "n_ag", "reply": "Looking it up"}},
        {
            "type": "CUSTOM",
            "name": "ac.tool_io",
            "value": {"canvas_node_id": "n_ag", "tool": "search", "args": {"q": "x"}, "output": {"hits": 2}},
        },
        {"type": "CUSTOM", "name": "ac.model_reply", "value": {"canvas_node_id": "n_ag", "reply": "Found two"}},
        {"type": "CUSTOM", "name": "ac.agent", "value": {"answer": "internal"}},
        {"type": "CUSTOM", "name": "ac.warning", "value": {"message": "careful"}},
        {"type": "STATE_DELTA", "canvas_node_id": "n_ag", "delta": {"a": "Found two", "scratch": "x"}},
        {"type": "STEP_FINISHED", "stepName": "agent", "canvas_node_id": "n_ag"},
        {"type": "RUN_FINISHED", "outcome": "success", "interrupts": [], "output": {"a": "Found two"}},
    ]
    out = _run(_translator(), events)
    kinds = types(out)
    assert kinds[:2] == ["RUN_STARTED", "STATE_SNAPSHOT"] and out[1].snapshot == {"q": "hi"}
    assert kinds.index("REASONING_START") < kinds.index("REASONING_MESSAGE_CONTENT") < kinds.index("REASONING_END")
    assert kinds.index("REASONING_END") < kinds.index("TEXT_MESSAGE_START")  # thinking closes when text begins
    messages = [e for e in out if e.type == "TEXT_MESSAGE_START"]
    assert len(messages) == 2  # the streamed reply and the one that did not stream: never the hidden step's draft
    assert text_of(out) == "Looking it upFound two"
    start = next(e for e in out if e.type == "TOOL_CALL_START")
    assert start.tool_call_name == "search" and start.parent_message_id == messages[0].message_id
    result = next(e for e in out if e.type == "TOOL_CALL_RESULT")
    assert json.loads(result.content) == {"hits": 2} and result.tool_call_id == start.tool_call_id
    delta = next(e for e in out if e.type == "STATE_DELTA")
    assert [(op.op, op.path, op.value) for op in delta.delta] == [("add", "/a", "Found two")]  # working fields stay
    assert [e.name for e in out if e.type == "CUSTOM"] == ["ac.warning"]
    usage = out[-1].usage[0]
    assert (usage.provider, usage.model, usage.input_tokens, usage.cached_input_tokens, usage.total_tokens) == (
        "anthropic",
        "claude-x",
        10,
        6,
        14,
    )
    assert out[-2].type == "STATE_SNAPSHOT" and out[-2].snapshot == {"q": "hi", "a": "Found two"}


def test_a_run_that_fails_ends_with_run_error_and_a_breakpoint_is_not_a_success():
    failed = _run(_translator(), [{"type": "RUN_ERROR", "code": "budget_exceeded", "message": "over budget"}])
    assert types(failed) == ["RUN_STARTED", "STATE_SNAPSHOT", "RUN_ERROR"] and failed[-1].code == "budget_exceeded"
    stopped = _run(_translator(snapshot=None), [{"type": "RUN_FINISHED", "outcome": "interrupt", "interrupts": []}])
    assert stopped[-1].type == "RUN_ERROR" and stopped[-1].code == "breakpoint"


async def test_a_tool_call_the_application_never_answers_expires_like_an_approval(app, api_client):
    from datetime import UTC, datetime, timedelta

    from agentcanvas.db.models import Run, Thread

    calls = {"content": "", "tool_calls": [{"name": "pick_date", "args": {}}]}
    _, dep, key = await chat_deployment(app, api_client, [calls, "Thanks."])
    tool = {"name": "pick_date", "description": "Asks the person for a date in the page"}
    await ag_ui(api_client, dep, key, run_input("th-gone", user("book it"), tools=[tool]))
    later = datetime.now(UTC) + timedelta(days=91)
    job = app.state.retention
    assert await job.expire_tool_calls(datetime.now(UTC)) == 0  # still waiting
    assert await job.expire_tool_calls(later) == 1
    async with app.state.db.sessionmaker() as s:
        assert (await s.get(Thread, "th-gone")).status == "idle"
        run = await s.scalar(select(Run).where(Run.thread_id == "th-gone"))
        assert run.status == "cancelled" and "tool's result" in run.error
    history = [user("book it"), {"id": "a1", "role": "assistant", "content": ""}, user("never mind", "u2")]
    again = await ag_ui(api_client, dep, key, run_input("th-gone", *history))  # a new turn, not a refusal
    assert again[-1].type == "RUN_FINISHED" and again[-1].outcome.type == "success"
