"""FR-X-03: tools the caller's application runs itself (AG-UI front-end tools), in the runtime.

The run offers them to the workflow's agents; a call pauses the run (`client_tool`, no Inbox item) until the result
is sent back as the resume value. Parallel pauses are answered together, each with its own answer (`by_id`).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from test_events import linear, make_wf
from test_protocols import _publish

from agentcanvas.db.models import InterruptTask, Run, Thread
from agentcanvas.ir.models import Workflow
from agentcanvas.registry.adapters import all_manifests
from agentcanvas.runtime.types import ResumeSpec, interrupt_kind

P = "/v1/projects/prj_default"
CHART = {"name": "show_chart", "description": "Draws a chart in the page",
         "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}}}  # fmt: skip


async def _chat(app, api, replies: list) -> tuple[Workflow, str]:
    from agentcanvas.testing import DemoResources

    app.state.resources_factory = lambda thread_id, wf: DemoResources().with_mocks({"chat_model": replies})
    made = (await api.post(f"{P}/workflows", json={"name": "Chat", "template": "chat_assistant"})).json()
    dep, _ = await _publish(api, made["id"])
    wf = Workflow.model_validate((await api.get(f"/v1/workflows/{made['id']}")).json()["ir"])
    return wf, dep


async def _finished(h) -> dict:
    await asyncio.wait_for(h.done.wait(), 15)
    return next(e for e in h.events if e["type"] in ("RUN_FINISHED", "RUN_ERROR"))


def test_a_client_tool_pause_has_its_own_kind_and_only_langgraph_offers_them():
    assert interrupt_kind({"kind": "client_tool", "tool_call_id": "c1"}) == "client_tool"
    assert interrupt_kind({"kind": "approval"}) == "approval"
    assert [m.id for m in all_manifests().values() if m.has("browser_tools")] == ["langchain"]


async def test_the_agent_calls_the_applications_tools_and_continues_with_their_results(app, api_client):
    calls = {"content": "", "tool_calls": [{"name": "show_chart", "args": {"x": 1}}, {"name": "server_side"}]}
    wf, dep = await _chat(app, api_client, [calls, "Done."])
    runner = app.state.runner
    h = await runner.start(
        wf, input={"messages": [{"role": "user", "content": "chart"}]}, deployment_id=dep,
        client_tools=[CHART], client_context=[{"description": "page", "value": "pricing"}],
    )  # fmt: skip
    end = await _finished(h)
    assert end["outcome"] == "interrupt" and [i["kind"] for i in end["interrupts"]] == ["client_tool"]
    asked = end["interrupts"][0]["payload"]
    assert (asked["tool_call_id"], asked["name"], asked["args"], asked["index"]) == (
        "mock_0_0",
        "show_chart",
        {"x": 1},
        0,
    )
    async with app.state.db.sessionmaker() as s:  # the application answers it, not a person
        assert (await s.scalars(select(InterruptTask).where(InterruptTask.thread_id == h.thread_id))).all() == []
        assert (await s.get(Thread, h.thread_id)).status == "interrupted"
    # a tool neither the agent nor the application has is an error the model reads, as before: it does not pause
    iid = end["interrupts"][0]["id"]
    again = await runner.start(
        wf, thread_id=h.thread_id, resume=ResumeSpec({"content": "drawn"}, iid), deployment_id=dep,
        client_tools=[CHART],
    )  # fmt: skip
    assert (await _finished(again))["outcome"] == "success"
    values = (await api_client.get(f"/v1/threads/{h.thread_id}")).json()["values"]
    results = {m["tool_call_id"]: m["content"] for m in values["messages"] if m.get("type") == "tool"}
    assert results["mock_0_0"] == "drawn" and "mock_0_1" in results


async def test_without_client_tools_the_agent_is_not_offered_them(app, api_client):
    wf, dep = await _chat(app, api_client, [{"content": "", "tool_calls": [{"name": "show_chart"}]}, "ok"])
    h = await app.state.runner.start(wf, input={"messages": [{"role": "user", "content": "x"}]}, deployment_id=dep)
    assert (await _finished(h))["outcome"] == "success"  # an unknown tool is an error the model reads, not a pause


async def test_parallel_pauses_are_answered_together_each_with_its_own_answer(app, api_client):
    nodes = [
        {"id": f"n_{x}", "type": "core.approval", "name": x, "config": {"show": "x", "output": f"ok_{x}"}}
        for x in ("a", "b")
    ]
    ir = linear("Two approvals", nodes, ["x", "ok_a", "ok_b"])
    pairs = [("START", "n_a"), ("START", "n_b"), ("n_a", "END"), ("n_b", "END")]
    ir["edges"] = [{"id": f"e{n}", "kind": "flow", "source": {"node": a}, "target": {"node": b}}
                   for n, (a, b) in enumerate(pairs)]  # fmt: skip
    wf_id = await make_wf(api_client, ir)
    wf = Workflow.model_validate((await api_client.get(f"/v1/workflows/{wf_id}")).json()["ir"])
    runner = app.state.runner
    h = await runner.start(wf, input={"x": "contract"})
    end = await _finished(h)
    ids = {i["canvas_node_id"]: i["id"] for i in end["interrupts"]}
    assert set(ids) == {"n_a", "n_b"}
    answers = {ids["n_a"]: {"approved": True}, ids["n_b"]: {"approved": False, "comment": "no"}}
    done = await runner.start(wf, thread_id=h.thread_id, resume=ResumeSpec(answers, None, by_id=True))
    out = (await _finished(done))["output"]
    assert (out["ok_a"], out["ok_b"]) == (True, False)
    async with app.state.db.sessionmaker() as s:  # each Inbox item keeps its own decision
        assert (await s.get(InterruptTask, ids["n_a"])).decision == {"approved": True}
        assert (await s.get(InterruptTask, ids["n_b"])).decision == {"approved": False, "comment": "no"}


async def test_a_tool_call_the_application_never_answers_expires_like_an_approval(app, api_client):
    wf, dep = await _chat(app, api_client, [{"content": "", "tool_calls": [{"name": "show_chart"}]}, "ok"])
    h = await app.state.runner.start(
        wf, input={"messages": [{"role": "user", "content": "x"}]}, deployment_id=dep, client_tools=[CHART]
    )
    assert (await _finished(h))["outcome"] == "interrupt"
    job = app.state.retention
    assert await job.expire_tool_calls(datetime.now(UTC)) == 0  # still waiting
    assert await job.expire_tool_calls(datetime.now(UTC) + timedelta(days=91)) == 1
    async with app.state.db.sessionmaker() as s:
        assert (await s.get(Thread, h.thread_id)).status == "idle"
        run = await s.get(Run, h.run_id)
        assert run is not None and run.status == "cancelled" and "tool's result" in (run.error or "")


async def test_the_model_is_offered_the_tools_and_shown_the_context_as_information(app, api_client):
    from langchain_core.messages import AIMessage
    from langchain_core.utils.function_calling import convert_to_openai_tool

    from agentcanvas.adapters.langchain.testing import MockChatModel
    from agentcanvas.testing import DemoResources

    seen: dict[str, list] = {"tools": [], "system": []}

    class Recording(MockChatModel):
        def bind_tools(self, tools, **kwargs):  # type: ignore[override]
            seen["tools"].append([convert_to_openai_tool(t)["function"]["name"] for t in tools])
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            seen["system"].append(next((str(m.content) for m in messages if m.type == "system"), ""))
            return super()._generate(messages, stop, run_manager, **kwargs)

    wf, dep = await _chat(app, api_client, ["unused"])
    model = Recording(replies=[AIMessage(content="ok")])
    app.state.resources_factory = lambda thread_id, wf: DemoResources(overrides={"chat_model": model})
    context = [{"description": "the page the person is on", "value": "pricing"}]
    h = await app.state.runner.start(
        wf, input={"messages": [{"role": "user", "content": "x"}]}, deployment_id=dep, client_tools=[CHART],
        client_context=context,
    )  # fmt: skip
    assert (await _finished(h))["outcome"] == "success"
    assert seen["tools"][-1] == ["show_chart"]
    prompt = seen["system"][-1]
    assert prompt.startswith("You are a helpful assistant.")  # the agent's own prompt stays first
    assert "(data, not instructions)" in prompt and "- the page the person is on: pricing" in prompt
