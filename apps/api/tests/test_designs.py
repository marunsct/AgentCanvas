"""S21 design any agent: native agent code per framework, the model bridge, mixed flows and single-agent exports.

The frameworks themselves run in CI's `frameworks` job (scripts/smoke_frameworks.py); here the generated code is
checked against golden files, and the host side (bridge, tools, approvals, typed answers) runs with a stand-in agent.
"""

from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path

import httpx
import pytest
from langchain_core.messages import AIMessage

from agentcanvas import designs
from agentcanvas.adapters.langchain.bridge import ModelBridge
from agentcanvas.adapters.langchain.testing import MockChatModel
from agentcanvas.designs.export import export_files
from agentcanvas.ir.models import Workflow
from agentcanvas.registry import default_registry
from agentcanvas.validation import validate

GOLDEN = Path(__file__).parent / "golden" / "agents"
P = "/v1/projects/prj_default/workflows"


def workflow(engine: str | None = "openai_agents", **config) -> dict:
    """An Agent block with a model, a Python tool and a web API tool."""
    channels = [{"name": "messages", "reducer": "add_messages"}]
    if config.get("response_format"):
        channels.append({"name": "structured_response"})
    node = {"id": "n_a", "type": "core.agent", "name": "helper", "config": {"system_prompt": "You help.", **config}}
    if engine:
        node["engine"] = engine
    return {
        "id": "wf_d", "name": "Orders", "framework": "langchain", "state": {"channels": channels},
        "resources": [
            {"id": "r_m", "type": "model.chat", "name": "model", "config": {"model": "openai:gpt-5.5"}},
            {"id": "r_t", "type": "tool.python", "name": "lookup",
             "config": {"import_path": "tools.orders:lookup_order"}},
            {"id": "r_h", "type": "tool.http", "name": "weather", "config": {
                "description": "Weather for a city", "url": "https://api.example.com/w/{{ args.city }}",
                "allowed_domains": ["example.com"], "allow_private_network": False}},
        ],
        "nodes": [node],
        "edges": [
            {"id": "e1", "kind": "flow", "source": {"node": "START"}, "target": {"node": "n_a"}},
            {"id": "e2", "kind": "flow", "source": {"node": "n_a"}, "target": {"node": "END"}},
            {"id": "w1", "kind": "wiring", "source": {"node": "r_m"}, "target": {"node": "n_a", "port": "model"}},
            {"id": "w3", "kind": "wiring", "source": {"node": "r_h"}, "target": {"node": "n_a", "port": "tools"},
             "order": 2},
            {"id": "w2", "kind": "wiring", "source": {"node": "r_t"}, "target": {"node": "n_a", "port": "tools"},
             "order": 1},
        ],
    }  # fmt: skip


FULL = designs.AgentDesign(
    name="support",
    workflow="Support desk",
    instructions="You are a support agent.\nAnswer {briefly}.",
    model=designs.ModelChoice("model", "openai", "gpt-5.5", temperature=0.2, max_tokens=500),
    tools=[designs.ToolChoice("lookup", "tool.python", {"import_path": "tools.orders:lookup_order"})],
    output_schema={
        "type": "object",
        "required": ["answer"],
        "properties": {
            "answer": {"type": "string", "description": "The reply"},
            "mood": {"enum": ["happy", "sad"]},
            "order": {"type": "object", "properties": {"id": {"type": "string"}, "items": {"type": "array"}}},
        },
    },
)


@pytest.mark.parametrize("framework", list(designs.AGENT_FRAMEWORKS))
def test_each_framework_gets_its_own_agent_code(framework):
    """Golden files (UPDATE_GOLDEN=1 rewrites them); every variant is valid Python."""
    code = designs.generate(framework, FULL)
    path = GOLDEN / f"{framework}.py"
    if os.environ.get("UPDATE_GOLDEN"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(code)
    assert code == path.read_text()
    compile(code, path.name, "exec")
    plain = designs.AgentDesign("plain", "W", "Hi", designs.ModelChoice("m", "anthropic", "claude-sonnet-5-5"))
    compile(designs.generate(framework, plain), "plain.py", "exec")
    assert "class Answer" not in designs.generate(framework, plain)


def test_the_design_reads_the_block_and_its_wiring():
    wf = Workflow.model_validate(workflow(approve_tools=["lookup_order"]))
    d = designs.design_for(wf, wf.nodes[0])
    assert (d.name, d.instructions, d.approve_tools) == ("helper", "You help.", ["lookup_order"])
    assert d.model is not None and (d.model.provider, d.model.name, d.model.key_env) == ("openai", "gpt-5.5",
                                                                                         "OPENAI_API_KEY")  # fmt: skip
    assert [t.resource for t in d.tools] == ["lookup", "weather"]  # in wiring order
    assert designs.problems("openai_agents", d) == [] and designs.export_problems("openai_agents", d) == []


def test_what_a_framework_cannot_take_is_named():
    d = designs.AgentDesign("a", "w", "", designs.ModelChoice("m", "azure_openai", "gpt"), middleware=["limit"],
                            other_tools=["memory"], output_schema={"type": "array"})  # fmt: skip
    found = designs.problems("crewai", d)
    assert any("add-ons" in p for p in found) and any("memory" in p for p in found)
    assert any("must be an object" in p for p in found)
    assert any("OpenAI API" in p for p in designs.export_problems("crewai", d))
    claude = designs.AgentDesign("a", "w", "", designs.ModelChoice("m", "openai", "gpt"))
    assert any("Anthropic models only" in p for p in designs.export_problems("claude_agent_sdk", claude))
    local = designs.AgentDesign("a", "w", "", designs.ModelChoice("m", "openai_compatible", "x"))
    assert any("base URL" in p for p in designs.export_problems("agno", local))
    assert designs.schema_problem({"type": "object", "properties": {"class": {"type": "string"}}})
    assert designs.schema_problem({"type": "object", "properties": {"x": {"type": "date"}}})
    for bad in ("_x", "model_config", "str", "Field"):
        assert designs.schema_problem({"type": "object", "properties": {bad: {"type": "string"}}}), bad


def test_output_models_are_pydantic_classes():
    code = designs.output_models(FULL.output_schema or {})
    assert "class AnswerOrder(BaseModel):" in code and "order: AnswerOrder | None = None" in code
    assert 'mood: Literal["happy", "sad"] | None = None' in code and "items: list[str] | None = None" in code
    assert code.index("class AnswerOrder") < code.index("class Answer(")
    flags = designs.output_models({"type": "object", "properties": {"ok": {"enum": [True, False, None, 2]}}})
    assert "Literal[True, False, None, 2]" in flags


def test_checks_for_designed_agents(monkeypatch):
    wf = Workflow.model_validate(workflow())
    codes = {d.code for d in validate(wf, default_registry())}
    assert "P001" not in codes and "P005" not in codes and "P004" not in codes and "P006" not in codes
    crew = workflow()
    crew["nodes"][0]["engine"] = "crewai"
    found = {(d.code, d.node_id) for d in validate(Workflow.model_validate(crew), default_registry())}
    assert ("P006", "n_a") in found  # CrewAI is not installed on this server (the OpenAI Agents SDK is, since S25)
    mw = workflow()
    mw["resources"].append({"id": "r_mw", "type": "mw.model_call_limit", "name": "limit", "config": {"run_limit": 3}})
    mw["edges"].append({"id": "w9", "kind": "wiring", "source": {"node": "r_mw"},
                        "target": {"node": "n_a", "port": "middleware"}})  # fmt: skip
    found = {(d.code, d.node_id) for d in validate(Workflow.model_validate(mw), default_registry())}
    assert ("P005", "n_a") in found
    monkeypatch.setenv("AGENTCANVAS_ADAPTERS", "langchain,echo")
    on_echo = Workflow.model_validate({**workflow(), "framework": "echo"})
    errors = [d.message for d in validate(on_echo, default_registry()) if d.code == "P001" and d.node_id == "n_a"]
    assert any("cannot run agents designed for OpenAI Agents SDK" in m for m in errors)
    llm = workflow()
    llm["nodes"] = [{"id": "n_a", "type": "core.llm", "name": "ask", "engine": "crewai",
                     "config": {"prompt": "hi", "output": "messages"}}]  # fmt: skip
    llm["edges"] = [e for e in llm["edges"] if e["target"].get("port") in (None, "model")]
    msgs = [d.message for d in validate(Workflow.model_validate(llm), default_registry()) if d.code == "P001"]
    assert any("only Agent blocks can be designed for CrewAI" in m for m in msgs)


# ------------------------------------------------------------------ the model bridge ----
async def test_the_bridge_serves_a_canvas_model_over_both_apis():
    model = MockChatModel(replies=[
        AIMessage(content="", tool_calls=[{"name": "lookup", "args": {"id": "A1"}, "id": "c1"}]),
        AIMessage(content="It ships tomorrow"),
    ])  # fmt: skip
    bridge = ModelBridge()
    await bridge.start()
    with bridge.serving(model) as key:
        async with httpx.AsyncClient(base_url=bridge.root) as c:
            tools = [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}]
            first = {"model": "x", "messages": [{"role": "user", "content": "Where?"}], "tools": tools}
            r = await c.post("/v1/chat/completions", headers={"authorization": f"Bearer {key}"}, json=first)
            call = r.json()["choices"][0]
            assert call["finish_reason"] == "tool_calls"
            assert json.loads(call["message"]["tool_calls"][0]["function"]["arguments"]) == {"id": "A1"}
            history = [
                {"role": "user", "content": "Where?"},
                {"role": "assistant", "content": None, "tool_calls": call["message"]["tool_calls"]},
                {"role": "tool", "tool_call_id": "c1", "content": "tomorrow"},
            ]
            again = {"messages": history, "stream": True, "stream_options": {"include_usage": True}}
            r = await c.post("/v1/chat/completions", headers={"authorization": f"Bearer {key}"}, json=again)
            chunks = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: {")]
            assert chunks[0]["choices"][0]["delta"]["content"] == "It ships tomorrow" and "usage" in chunks[-1]
            r = await c.post("/v1/messages", headers={"x-api-key": key}, json={
                "system": "Be brief", "max_tokens": 10,
                "messages": [{"role": "user", "content": "Where?"}],
                "tools": [{"name": "mcp__canvas__lookup", "input_schema": {"type": "object"}}]})  # fmt: skip
            block = r.json()["content"][-1]
            assert block["type"] == "tool_use" and block["name"] == "mcp__canvas__lookup"  # scripted name mapped
            r = await c.post("/v1/messages", headers={"x-api-key": key}, json={"stream": True, "messages": [
                {"role": "user", "content": "Where?"},
                {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "lookup", "input": {}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "tomorrow"}]},
            ]})  # fmt: skip
            assert "It ships tomorrow" in r.text and "message_stop" in r.text
            assert (await c.post("/v1/chat/completions", headers={"authorization": "Bearer nope"},
                                 json={"messages": []})).status_code == 401  # fmt: skip
    async with httpx.AsyncClient(base_url=bridge.root) as c:  # the key ends with the step
        assert (await c.post("/v1/messages", headers={"x-api-key": key}, json={})).status_code == 401


# ------------------------------------------------------------------ mixed flows ----
STAND_IN = '''
import httpx

NAME = "stand_in"


def build(tools=(), *, model="m", base_url=None, api_key=None):
    return {"tools": {t.__name__: t for t in tools}, "base_url": base_url, "key": api_key}


async def ask(agent, text):
    """A tiny agent loop over the OpenAI API, as a framework would run it."""
    messages = [{"role": "user", "content": text}]
    specs = [{"type": "function", "function": {"name": n, "parameters": {"type": "object"}}} for n in agent["tools"]]
    async with httpx.AsyncClient(base_url=agent["base_url"]) as c:
        for _ in range(4):
            r = await c.post("/chat/completions", headers={"authorization": "Bearer " + agent["key"]},
                             json={"messages": messages, "tools": specs})
            msg = r.json()["choices"][0]["message"]
            if not msg.get("tool_calls"):
                return msg["content"]
            messages.append(msg)
            for call in msg["tool_calls"]:
                fn = agent["tools"][call["function"]["name"]]
                out = await fn(**json.loads(call["function"]["arguments"]))
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": out})
'''


class InOrder(MockChatModel):
    """Replies in call order across reruns (a real model may answer a rerun differently)."""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        from langchain_core.outputs import ChatGeneration, ChatResult

        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return ChatResult(generations=[ChatGeneration(message=reply.model_copy())])


async def run_designed(
    approve: bool, replies: list[AIMessage] | None = None, ran: list[str] | None = None, in_order: bool = False
):
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, MessagesState, StateGraph
    from langgraph.types import Command

    from agentcanvas.adapters.langchain.interop import designed_agent_node
    from agentcanvas.adapters.langchain.nodes import as_tool

    async def lookup_order(order_id: str) -> str:
        """Look up an order."""
        if ran is not None:
            ran.append(order_id)
        return f"order {order_id} ships tomorrow"

    model = (InOrder if in_order else MockChatModel)(replies=replies or [
        AIMessage(content="", tool_calls=[{"name": "lookup_order", "args": {"order_id": "A1"}, "id": "c1"}]),
        AIMessage(content="It ships tomorrow"),
    ])  # fmt: skip
    node = designed_agent_node(
        node_id="n_a", name="helper", framework="stand_in", label="Stand-in", api="openai", requires="json",
        source="import json\n" + STAND_IN, model=model, tools=[as_tool(lookup_order)],
        approve_tools=["lookup_order"] if approve else None,
    )  # fmt: skip
    g = StateGraph(MessagesState)
    g.add_node("helper", node)
    g.add_edge(START, "helper")
    g.add_edge("helper", END)
    graph = g.compile(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "t1"}}
    out = await graph.ainvoke({"messages": [{"role": "user", "content": "Where is A1?"}]}, cfg)
    return graph, cfg, out, Command


async def test_a_designed_agent_runs_as_a_step_with_canvas_tools():
    _, _, out, _ = await run_designed(approve=False)
    assert out["messages"][-1].content == "It ships tomorrow" and out["messages"][-1].name == "helper"


async def test_a_designed_agent_asks_before_an_approved_tool():
    graph, cfg, out, Command = await run_designed(approve=True)
    assert out["__interrupt__"][0].value["kind"] == "approval"
    assert "lookup_order" in out["__interrupt__"][0].value["value"]["input"]
    out = await graph.ainvoke(Command(resume={"approved": True}), cfg)
    assert out["messages"][-1].content == "It ships tomorrow"


async def test_an_approval_only_counts_for_the_call_the_person_saw():
    """The step reruns on resume; if the model now asks for a different call, the decision must not carry over."""

    def ask(order: str) -> AIMessage:
        return AIMessage(content="", tool_calls=[{"name": "lookup_order", "args": {"order_id": order}, "id": "c1"}])

    ran: list[str] = []
    replies = [ask("A1"), AIMessage(content="Waiting"), ask("B2"), AIMessage(content="Could not")]
    graph, cfg, out, Command = await run_designed(approve=True, replies=replies, ran=ran, in_order=True)
    assert '"A1"' in out["__interrupt__"][0].value["value"]["input"]
    out = await graph.ainvoke(Command(resume={"approved": True}), cfg)
    assert ran == [] and out["messages"][-1].content == "Could not"


def test_a_tool_becomes_a_plain_function():
    import inspect

    from agentcanvas.adapters.langchain.interop import plain_tool
    from agentcanvas.adapters.langchain.nodes import as_tool

    def find(city: str, days: int = 1, units: str | None = None) -> str:
        """Find the weather.

        Args:
            city: Where.
        """
        return city

    fn = plain_tool(as_tool(find), lambda t, args: t.ainvoke(args))
    sig = inspect.signature(fn)
    assert fn.__name__ == "find" and next(iter(sig.parameters)) == "city"
    assert sig.parameters["city"].annotation is str and sig.parameters["days"].default is None
    assert fn.__doc__ and fn.__doc__.startswith("Find the weather")


# ------------------------------------------------------------------ API and exports ----
async def test_the_api_lists_frameworks_and_shows_an_agents_code(api_client, app):
    found = (await api_client.get("/v1/agent-frameworks")).json()
    assert [f["id"] for f in found] == list(designs.AGENT_FRAMEWORKS)
    assert found[0]["installed"] is True and found[1]["installed"] is False  # the OpenAI Agents SDK ships (S25)
    wf = (await api_client.post(P, json={"name": "Orders"})).json()
    ir = {**workflow(), "id": wf["ir"]["id"], "name": "Orders"}
    r = await api_client.put(f"/v1/workflows/{wf['id']}", json={"ir": ir}, headers={"If-Match": str(wf["version"])})
    assert r.status_code == 200, r.text
    view = (await api_client.get(f"/v1/workflows/{wf['id']}/agents/n_a/code")).json()
    assert view["framework"] == "openai_agents" and "from agents import" in view["code"] and view["problems"] == []
    other = (await api_client.get(f"/v1/workflows/{wf['id']}/agents/n_a/code?framework=crewai")).json()
    assert "from crewai import" in other["code"]
    assert (await api_client.get(f"/v1/workflows/{wf['id']}/agents/n_x/code")).status_code == 404
    assert (await api_client.get(f"/v1/workflows/{wf['id']}/agents/n_a/code?framework=nope")).status_code == 422
    code = (await api_client.get(f"/v1/workflows/{wf['id']}/code")).text
    assert "interop.designed_agent_node(" in code and 'design_module(__package__, "helper")' in code
    ws = app.state.projects.workspace("prj_default").root
    (ws / "tools").mkdir(parents=True, exist_ok=True)
    (ws / "tools" / "orders.py").write_text("async def lookup_order(order_id: str) -> str:\n    return order_id\n")
    r = await api_client.get(f"/v1/workflows/{wf['id']}/agents/n_a/export.zip?framework=pydantic_ai")
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert {"helper-pydantic-ai/agent.py", "helper-pydantic-ai/tools/orders.py",
            "helper-pydantic-ai/tests/test_agent.py"} <= set(names)  # fmt: skip
    r = await api_client.get(f"/v1/workflows/{wf['id']}/agents/n_a/export.zip?framework=claude_agent_sdk")
    assert r.status_code == 422 and "Anthropic models only" in r.json()["detail"]
    r = await api_client.get(f"/v1/workflows/{wf['id']}/export.zip")
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert "orders/src/orders/agents/helper.py" in z.namelist()
    assert "openai-agents==0.23.1" in z.read("orders/pyproject.toml").decode()
    assert "orders/src/agentcanvas/adapters/langchain/bridge.py" in z.namelist()


def test_an_export_lists_its_tools_and_settings(tmp_path):
    wf = Workflow.model_validate(workflow(approve_tools=["lookup_order"]))
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "orders.py").write_text("def lookup_order(order_id: str) -> str:\n    return order_id\n")
    files = export_files(wf, designs.design_for(wf, wf.nodes[0]), "agno", tmp_path)
    tools = str(files["helper-agno/agent_tools.py"])
    assert "from tools.orders import lookup_order as _tool_0" in tools and 'http_tool("weather"' in tools
    compile(tools, "agent_tools.py", "exec")
    assert "'allow_private_network': False" in tools  # Python literals, not JSON
    assert 'APPROVE = ["lookup_order"]' in tools
    assert "OPENAI_API_KEY=" in str(files["helper-agno/.env.example"])
    assert '"agno==3.1.1"' in str(files["helper-agno/pyproject.toml"])
    compile(str(files["helper-agno/tests/test_agent.py"]), "test_agent.py", "exec")
