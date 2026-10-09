"""S16: agents from other frameworks, remote A2A agents and MCP agents as canvas steps, in both execution modes."""

from __future__ import annotations

import json
import sys

import httpx
import pytest
from fake_frameworks import TARGETS, WORKSPACE_AGENTS, install
from fastmcp import FastMCP
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from test_catalog import MESSAGES, cfg, ch, codes, compiled, errors, workflow
from test_protocols import _publish
from test_publish import bearer
from test_versions import HIGH_CASE, new_triage

from agentcanvas.adapters.langchain import interop
from agentcanvas.adapters.langchain.interpreter import abuild_graph, build_graph
from agentcanvas.adapters.langchain.nodes import Resources
from agentcanvas.adapters.langchain.packager import dependencies
from agentcanvas.validation import validate


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """A workspace with agents from every framework (stand-ins), importable from both modes."""
    install(monkeypatch)
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "__init__.py").write_text("")
    (tmp_path / "tools" / "fw.py").write_text(WORKSPACE_AGENTS)
    monkeypatch.syspath_prepend(str(tmp_path))  # generated code imports `tools.fw` as an installed package
    for name in [m for m in sys.modules if m == "tools" or m.startswith("tools.")]:
        monkeypatch.delitem(sys.modules, name)
    return tmp_path


def agent_wf(config: dict, channels: list | None = None):
    return workflow(
        channels=channels or [MESSAGES],
        nodes=[("helper", "agent.framework", {"output": "messages", **config})],
        flow=[("START", "helper"), ("helper", "END")],
    )


async def run_modes(wf, ws, run):
    """`run(graph)` against the interpreted and the generated graph."""
    assert not errors(wf), validate(wf)
    mod = compiled(wf, ws)
    a = await run(build_graph(wf, Resources(workspace=ws), checkpointer=InMemorySaver()))
    b = await run(mod.build_graph(Resources(), checkpointer=InMemorySaver()))
    return a, b


@pytest.mark.parametrize("framework", sorted(TARGETS))
async def test_an_agent_from_each_framework_runs_as_a_step(ws, framework):
    wf = agent_wf({"framework": framework, "import_path": TARGETS[framework]})

    async def run(graph):
        out = await graph.ainvoke({"messages": [("user", "where is my order?")]}, cfg())
        return out["messages"][-1].content, out["messages"][-1].name

    a, b = await run_modes(wf, ws, run)
    assert a == b == (f"{framework}: where is my order?", "helper")


async def test_prompt_template_and_output_field(ws):
    wf = agent_wf(
        {"framework": "crewai", "import_path": "tools.fw:crew", "prompt": "Topic: {{ state.topic }}",
         "output": "reply"},
        channels=[ch("topic", io="in"), ch("reply", io="out")],
    )  # fmt: skip

    async def run(graph):
        return (await graph.ainvoke({"topic": "tides"}, cfg()))["reply"]

    assert await run_modes(wf, ws, run) == ("crewai: Topic: tides", "crewai: Topic: tides")


async def test_a_factory_builds_a_fresh_agent_per_run(ws):
    wf = agent_wf({"framework": "pydantic_ai", "import_path": "tools.fw:fresh_agent"})
    graph = build_graph(wf, Resources(workspace=ws), checkpointer=InMemorySaver())
    for thread in ("a", "b"):
        out = await graph.ainvoke({"messages": [("user", "hi")]}, cfg(thread))
        assert out["messages"][-1].content == "pydantic_ai: hi"
    from agentcanvas.workspace.loader import load_attr

    assert len(load_attr("tools.fw:BUILT", ws)) == 2


async def test_a_python_function_may_update_several_fields(ws):
    wf = agent_wf(
        {"framework": "callable", "import_path": "tools.fw:shout", "prompt": "{{ state.text }}", "output": "reply"},
        channels=[ch("text", io="in"), ch("reply", io="out"), ch("loud", "boolean", io="out")],
    )

    async def run(graph):
        return await graph.ainvoke({"text": "hello"}, cfg())

    a, b = await run_modes(wf, ws, run)
    assert a == b == {"reply": "HELLO", "loud": True}


async def test_approval_before_the_agent_and_its_trace(ws):
    wf = agent_wf({"framework": "agno", "import_path": "tools.fw:agno_agent", "approve": True})

    async def run(graph):
        first = await graph.ainvoke({"messages": [("user", "refund order 7")]}, cfg())
        (pause,) = first["__interrupt__"]
        assert pause.value["kind"] == "approval" and pause.value["value"]["input"] == "refund order 7"
        events = [c async for _, c in graph.astream(Command(resume={"approved": True}), cfg(), stream_mode=["custom"])]
        state = await graph.aget_state(cfg())
        return state.values["messages"][-1].content, [e["ac_agent"] for e in events if "ac_agent" in e]

    a, b = await run_modes(wf, ws, run)
    assert a == b
    reply, trace = a
    assert reply == "agno: refund order 7"
    assert trace == [{"node": "helper", "framework": "agno", "input": "refund order 7", "output": reply}]


async def test_a_rejected_agent_does_not_run(ws):
    wf = agent_wf({"framework": "agno", "import_path": "tools.fw:agno_agent", "approve": True})
    graph = build_graph(wf, Resources(workspace=ws), checkpointer=InMemorySaver())
    await graph.ainvoke({"messages": [("user", "delete everything")]}, cfg())
    out = await graph.ainvoke(Command(resume={"approved": "no"}), cfg())
    assert [m.type for m in out["messages"]] == ["human"]


async def test_a_missing_framework_says_what_to_install(tmp_path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "x.py").write_text("agent = object()\n")
    wf = agent_wf({"framework": "google_adk", "import_path": "tools.x:agent"})
    graph = build_graph(wf, Resources(workspace=tmp_path), checkpointer=InMemorySaver())
    with pytest.raises(interop.AgentError, match=r"Google ADK is not installed here: add `google-adk==2\.11\.0`"):
        await graph.ainvoke({"messages": [("user", "hi")]}, cfg())


def test_rules_exports_and_hosted_edition(ws):
    wf = agent_wf({"framework": "crewai", "import_path": "tools.fw:crew", "approve": True,
                   "side_effects": ["external_write"]})  # fmt: skip
    assert "E070" not in codes(wf)  # the approval comes before the agent acts
    assert "E041" in {d.code for d in validate(wf, edition="hosted")}  # runs workspace code in the server process
    assert "crewai==1.15.25" in dependencies(wf)
    bad = agent_wf({"framework": "crewai", "import_path": "os:system"})
    assert "E080" in errors(bad)
    assert errors(agent_wf({"framework": "langflow", "import_path": "tools.fw:crew"}))


# ---------------------------------------------------------------------------------------------- A2A ----
def a2a_server(log: list[dict]) -> httpx.MockTransport:
    """A remote agent that asks one question, then answers."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"name": "Billing", "url": "https://agents.example.com/a2a"})
        msg = json.loads(request.content)["params"]["message"]
        log.append(msg)
        text = msg["parts"][0]["text"]
        if "taskId" not in msg:
            status = {"state": "input-required",
                      "message": {"role": "agent", "parts": [{"kind": "text", "text": "Which account?"}]}}  # fmt: skip
            result = {"kind": "task", "id": "task1", "contextId": "ctx1", "status": status}
        else:
            result = {"kind": "task", "id": "task1", "contextId": "ctx1", "status": {"state": "completed"},
                      "artifacts": [{"parts": [{"kind": "text", "text": f"Refunded on account {text}"}]}]}  # fmt: skip
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})

    return httpx.MockTransport(handle)


async def test_a_remote_a2a_agent_asks_and_answers(ws):
    wf = workflow(
        channels=[MESSAGES],
        nodes=[("billing", "agent.a2a", {"url": "https://agents.example.com/.well-known/agent-card.json",
                                         "output": "messages", "headers": {"Authorization": {"$secret": "KEY"}}})],
        flow=[("START", "billing"), ("billing", "END")],
    )  # fmt: skip

    async def run_with(graph, log):
        first = await graph.ainvoke({"messages": [("user", "refund me")]}, cfg())
        (pause,) = first["__interrupt__"]
        assert pause.value == {"kind": "input", "canvas_node_id": "billing", "prompt": "Which account?"}
        out = await graph.ainvoke(Command(resume="ACME-9"), cfg())
        return out["messages"][-1].content, [(m["parts"][0]["text"], m.get("taskId")) for m in log]

    results = []
    for build in (
        lambda res: build_graph(wf, res, checkpointer=InMemorySaver()),
        lambda res: compiled(wf, ws).build_graph(res, checkpointer=InMemorySaver()),
    ):
        log: list[dict] = []
        res = Resources(secrets={"KEY": "Bearer s3cret"}, http_transport=a2a_server(log))
        results.append(await run_with(build(res), log))
    assert results[0] == results[1]
    reply, sent = results[0]
    assert reply == "Refunded on account ACME-9"
    assert sent == [("refund me", None), ("ACME-9", "task1")]  # the first message is not sent again on resume


async def test_a2a_stops_instead_of_resending_after_a_restart():
    wf = workflow(
        channels=[MESSAGES],
        nodes=[("billing", "agent.a2a", {"url": "https://agents.example.com/a2a", "output": "messages"})],
        flow=[("START", "billing"), ("billing", "END")],
    )
    log: list[dict] = []
    graph = build_graph(wf, Resources(http_transport=a2a_server(log)), checkpointer=InMemorySaver())
    await graph.ainvoke({"messages": [("user", "refund me")]}, cfg())
    interop._SENT.clear()  # what a restart loses
    with pytest.raises(Exception, match="restarted while the remote agent 'billing' was waiting"):
        await graph.ainvoke(Command(resume="ACME-9"), cfg())
    assert [m["parts"][0]["text"] for m in log] == ["refund me"]  # nothing was sent twice


async def test_a2a_connects_to_the_address_it_checked(monkeypatch):
    from agentcanvas.adapters.langchain import nodes

    async def public(host, port):
        assert (host, port) == ("agents.example.com", 443)
        return "93.184.216.34"

    monkeypatch.setattr(nodes, "_public_address", public)
    t = await interop._pinned("https://agents.example.com/a2a?x=1", {"Authorization": "k"}, private_ok=False)
    assert t.url == "https://93.184.216.34:443/a2a?x=1"  # a second DNS answer cannot redirect the request
    assert t.headers == {"Authorization": "k", "Host": "agents.example.com"}
    assert t.extensions == {"sni_hostname": "agents.example.com"}
    assert (await interop._pinned("http://10.0.0.5/a2a", {}, private_ok=True)).url == "http://10.0.0.5/a2a"


async def test_a_field_called_input_does_not_clash_with_the_agent_input(ws):
    for framework, target in (("llamaindex", TARGETS["llamaindex"]), ("crewai", TARGETS["crewai"])):
        wf = agent_wf({"framework": framework, "import_path": target, "prompt": "{{ state.question }}"},
                      channels=[MESSAGES, ch("question"), ch("input")])  # fmt: skip
        out = await build_graph(wf, Resources(workspace=ws), checkpointer=InMemorySaver()).ainvoke(
            {"question": "q1", "input": "something else"}, cfg()
        )
        assert out["messages"][-1].content == f"{framework}: q1"


async def test_an_a2a_step_calls_a_published_agentcanvas_workflow(app, api_client):
    """End to end: one workflow asks another (published, with an approval inside) over A2A."""
    from agentcanvas.testing import DemoResources

    app.state.resources_factory = lambda thread_id, wf: DemoResources().with_mocks(HIGH_CASE["mocks"])
    dep, key = await _publish(api_client, (await new_triage(api_client))["id"])
    card = f"http://test/v1/deployments/{dep}/a2a/.well-known/agent-card.json"
    wf = workflow(
        channels=[ch("ticket", io="in"), ch("result", io="out")],
        nodes=[("triage", "agent.a2a", {"url": card, "prompt": "{{ state.ticket }}", "output": "result",
                                        "headers": bearer(key)})],
        flow=[("START", "triage"), ("triage", "END")],
    )  # fmt: skip
    graph = build_graph(wf, Resources(http_transport=httpx.ASGITransport(app=app)), checkpointer=InMemorySaver())
    first = await graph.ainvoke({"ticket": "the site is down"}, cfg())
    (pause,) = first["__interrupt__"]
    assert pause.value["kind"] == "input" and pause.value["prompt"]
    out = await graph.ainvoke(Command(resume="yes"), cfg())
    assert json.loads(out["result"])["approved"] is True


async def test_a2a_refuses_private_addresses_unless_allowed():
    wf = workflow(
        channels=[MESSAGES],
        nodes=[("inner", "agent.a2a", {"url": "http://127.0.0.1:9/a2a", "output": "messages"})],
        flow=[("START", "inner"), ("inner", "END")],
    )
    graph = build_graph(wf, Resources(), checkpointer=InMemorySaver())
    with pytest.raises(Exception, match="private or internal address"):
        await graph.ainvoke({"messages": [("user", "hi")]}, cfg())


# ---------------------------------------------------------------------------------------------- MCP ----
def research_server() -> FastMCP:
    srv = FastMCP("research")

    @srv.tool
    def research(question: str, depth: str = "short") -> str:
        """A research agent published as a tool."""
        return f"{depth} answer to: {question}"

    return srv


async def test_an_mcp_agent_is_called_once_as_a_step(ws):
    wf = workflow(
        channels=[ch("question", io="in"), ch("answer", io="out")],
        resources=[("research_srv", "mcp.server", {"url": "https://mcp.example.com/mcp"})],
        nodes=[("ask", "agent.mcp", {"tool": "research", "output": "answer",
                                     "arguments": {"question": "{{ state.question }}", "depth": "deep"}})],
        flow=[("START", "ask"), ("ask", "END")],
        wiring=[("research_srv", "ask", "tools")],
    )  # fmt: skip
    assert not errors(wf), validate(wf)
    mod = compiled(wf, ws)
    outs = []
    for build in (abuild_graph, None):
        res = Resources(overrides={"research_srv": research_server()})
        saver = InMemorySaver()
        graph = await (build(wf, res, checkpointer=saver) if build else mod.abuild_graph(res, checkpointer=saver))
        outs.append((await graph.ainvoke({"question": "why is the sky blue?"}, cfg()))["answer"])
    assert outs[0] == outs[1] == "deep answer to: why is the sky blue?"


async def test_an_unknown_mcp_tool_names_the_tools_there():
    wf = workflow(
        channels=[ch("answer", io="out")],
        resources=[("research_srv", "mcp.server", {"url": "https://mcp.example.com/mcp"})],
        nodes=[("ask", "agent.mcp", {"tool": "reserch", "output": "answer"})],
        flow=[("START", "ask"), ("ask", "END")],
        wiring=[("research_srv", "ask", "tools")],
    )
    graph = await abuild_graph(wf, Resources(overrides={"research_srv": research_server()}))
    with pytest.raises(interop.AgentError, match="has no tool 'reserch' \\(it has: research\\)"):
        await graph.ainvoke({}, cfg())
