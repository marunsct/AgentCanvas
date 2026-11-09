"""S28 mixed-flow parts: agents of frameworks that clash with LangGraph run in their own environment, behind a typed
contract; the host keeps tools, approvals and the model.

The stand-in agent (tests/test_designs.py) runs as a part with this test's own Python; the real CrewAI part runs when
`make crewai` has set up its environment.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Any, TypedDict

import pytest
from langchain_core.messages import AIMessage
from langgraph.graph.message import add_messages

from agentcanvas import designs
from agentcanvas.adapters import create_adapter
from agentcanvas.adapters.langchain import interop
from agentcanvas.adapters.langchain.parts import PartError, PartModule, check_answer
from agentcanvas.adapters.langchain.testing import MockChatModel
from agentcanvas.ids import new_id
from agentcanvas.ir.models import Workflow
from agentcanvas.runtime.types import ResumeSpec, RunSpec

STAND_IN = '''
import json

import httpx


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
                content = msg["content"]
                return json.loads(content) if content.startswith("{") else content
            messages.append(msg)
            for call in msg["tool_calls"]:
                fn = agent["tools"][call["function"]["name"]]
                out = await fn(**json.loads(call["function"]["arguments"]))
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": out})
'''
CALL = AIMessage(content="", tool_calls=[{"name": "lookup_order", "args": {"order_id": "A1"}, "id": "c1"}])


class State(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    structured_response: Any


TYPED = {"type": "object", "required": ["eta"], "properties": {"eta": {"type": "string"}}}


async def run_part(replies: list[AIMessage], *, approve: bool = False, contract: dict | None = None,
                   ran: list[str] | None = None):  # fmt: skip
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command

    from agentcanvas.adapters.langchain.nodes import as_tool

    async def lookup_order(order_id: str) -> str:
        """Look up an order."""
        if ran is not None:
            ran.append(order_id)
        return f"order {order_id} ships tomorrow"

    node = interop.designed_agent_node(
        node_id="n_a", name="helper", framework="stand_in", label="Stand-in", api="openai", requires="no_such_module",
        module=PartModule(sys.executable, source=STAND_IN), model=MockChatModel(replies=replies),
        tools=[as_tool(lookup_order)], approve_tools=["lookup_order"] if approve else None,
        contract=interop.part_contract(contract),
    )  # fmt: skip
    g = StateGraph(State)
    g.add_node("helper", node)
    g.add_edge(START, "helper")
    g.add_edge("helper", END)
    graph = g.compile(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": new_id("th")}}
    out = await graph.ainvoke({"messages": [{"role": "user", "content": "Where is A1?"}]}, cfg)
    return graph, cfg, out, Command


async def test_a_part_runs_in_its_own_process_with_the_hosts_tools():
    ran: list[str] = []
    _, _, out, _ = await run_part([CALL, AIMessage(content="It ships tomorrow")], ran=ran)
    assert out["messages"][-1].content == "It ships tomorrow" and ran == ["A1"]  # the tool ran here, in the host


async def test_a_part_asks_before_an_approved_tool():
    graph, cfg, out, Command = await run_part([CALL, AIMessage(content="It ships tomorrow")], approve=True)
    assert out["__interrupt__"][0].value["kind"] == "approval"
    out = await graph.ainvoke(Command(resume={"approved": True}), cfg)
    assert out["messages"][-1].content == "It ships tomorrow"


async def test_a_typed_answer_crosses_back_checked():
    _, _, out, _ = await run_part([AIMessage(content='{"eta": "tomorrow"}')], contract=TYPED)
    assert out["structured_response"] == {"eta": "tomorrow"}


async def test_an_answer_outside_the_contract_stops_the_step():
    with pytest.raises(Exception, match="outside its contract: the answer: 'eta' is a required property"):
        await run_part([AIMessage(content='{"when": "tomorrow"}')], contract=TYPED)
    with pytest.raises(Exception, match="plain text, not the structured output"):
        await run_part([AIMessage(content="tomorrow")], contract=TYPED)


async def test_a_broken_part_says_why():
    part = PartModule(sys.executable, source="raise SystemExit('no agent here')")
    with pytest.raises(PartError, match=r"SystemExit: no agent here"):
        await part.ask(part.build([]), "hi")


def test_contracts():
    assert check_answer(None, {"a": 1}) == '{"a": 1}'  # a text contract takes anything as text
    assert check_answer(TYPED, '{"eta": "now"}') == {"eta": "now"}
    with pytest.raises(ValueError, match="eta: 1 is not of type 'string'"):
        check_answer(TYPED, {"eta": 1})
    design = designs.AgentDesign(name="a", workflow="w", instructions="", model=None, output_schema=TYPED)
    assert designs.contract(design) == interop.part_contract(TYPED)  # the server and an export agree
    assert designs.contract(designs.AgentDesign(name="a", workflow="w", instructions="", model=None)) == (
        interop.part_contract(None)
    )


def test_frameworks_that_clash_with_langgraph_get_their_own_environment():
    own = {f.id for f in designs.AGENT_FRAMEWORKS.values() if f.own_env}
    assert own == {"crewai", "google_adk", "agno", "strands", "llamaindex"}  # scripts/check_part_envs.py proves it


# ------------------------------------------------------------------ a real CrewAI part on the server ----
needs_crewai = pytest.mark.skipif(designs.part_python("crewai") is None, reason="no CrewAI here (make crewai)")


def mixed_wf(typed: bool = False) -> Workflow:
    config: dict[str, Any] = {"system_prompt": "You write the reply."}
    channels: list[dict[str, Any]] = [{"name": "messages", "reducer": "add_messages"}]
    if typed:
        config["response_format"] = TYPED
        channels.append({"name": "structured_response"})
    return Workflow.model_validate({
        "id": "wf_mixed", "name": "Mixed", "framework": "langchain", "state": {"channels": channels},
        "resources": [{"id": "r_m", "type": "model.chat", "name": "model", "config": {"model": "openai:gpt-5.5"}}],
        "nodes": [{"id": "n_w", "type": "core.agent", "name": "writer", "engine": "crewai", "config": config}],
        "edges": [
            {"id": "e1", "kind": "flow", "source": {"node": "START"}, "target": {"node": "n_w"}},
            {"id": "e2", "kind": "flow", "source": {"node": "n_w"}, "target": {"node": "END"}},
            {"id": "w1", "kind": "wiring", "source": {"node": "r_m"}, "target": {"node": "n_w", "port": "model"}},
        ],
    })  # fmt: skip


async def _run(wf: Workflow, mocks: dict) -> list[dict]:
    adapter = create_adapter("langchain")
    spec = RunSpec(workflow=wf, thread_id=new_id("th"), run_id=new_id("run"),
                   input={"messages": [{"role": "user", "content": "Say hi"}]}, mocks=mocks)  # fmt: skip
    async with adapter.persistence(None):
        return [ev async for ev in adapter.execute(spec, adapter.default_resources())]


@needs_crewai
async def test_a_crewai_agent_runs_in_a_langgraph_flow_on_this_server(monkeypatch):
    monkeypatch.setattr(designs, "importable", lambda fw: False)
    events = await _run(mixed_wf(), {"model": ["Hi there!"]})
    assert events[-1]["outcome"] == "success", events[-1]
    said = [m["content"] for m in events[-1]["output"]["messages"] if m.get("type") == "ai"]
    assert said == ["Hi there!"]


@needs_crewai
async def test_a_crewai_part_keeps_its_contract():
    events = await _run(mixed_wf(typed=True), {"model": ['{"eta": "soon"}']})
    assert events[-1]["outcome"] == "success", events[-1]
    assert events[-1]["output"]["structured_response"] == {"eta": "soon"}


_ = ResumeSpec  # resumes are covered by the stand-in tests above


# ------------------------------------------------------------------ the export ----
def test_a_mixed_export_installs_each_framework_where_it_fits():
    import io
    import json
    import zipfile

    from agentcanvas.adapters import get_adapter
    from agentcanvas.registry import default_registry

    doc = json.loads((Path(__file__).parents[1] / "agentcanvas/registry/templates/mixed_team.ir.json")
                     .read_text())  # fmt: skip
    doc["nodes"].append({"id": "n_ts", "type": "agent.a2a", "name": "checker", "config": {
        "url": "https://agents.example.com/.well-known/agent-card.json", "output": "messages"}})  # fmt: skip
    doc["edges"] = [e for e in doc["edges"] if e["id"] != "e2"] + [
        {"id": "e3", "kind": "flow", "source": {"node": "n_write"}, "target": {"node": "n_ts"}},
        {"id": "e4", "kind": "flow", "source": {"node": "n_ts"}, "target": {"node": "END"}},
    ]
    wf = Workflow.model_validate(doc)
    _, data = get_adapter("langchain").package(wf, default_registry(), target="local", version="1.0.0")
    z = zipfile.ZipFile(io.BytesIO(data))
    files = {n.split("/", 1)[1]: n for n in z.namelist()}

    def read(rel: str) -> str:
        return z.read(files[rel]).decode()

    pyproject = read("pyproject.toml")
    assert "openai-agents==0.24.0" in pyproject and "crewai" not in pyproject  # the host has what fits
    assert read("parts/writer/requirements.txt") == "crewai==1.15.39\n"
    assert read("parts/writer/worker.py").startswith('"""A part\'s worker (S28)')
    assert "src/mixed_framework_team/agents/researcher.py" in files and "parts/researcher/agent.py" not in files
    contracts = json.loads(read("contracts.json"))
    runs = {p["name"]: (p["framework"], p["runs"]) for p in contracts["parts"]}
    assert runs == {"researcher": ("openai_agents", "host"), "writer": ("crewai", "own environment")}
    writer = next(p for p in contracts["parts"] if p["name"] == "writer")
    assert writer["contract"]["output"]["required"] == ["title", "summary"]
    assert json.loads(read("parts/writer/contract.json"))["output"] == writer["contract"]["output"]
    text = {"type": "string"}
    assert contracts["remote"] == [{"name": "checker", "node_id": "n_ts", "protocol": "a2a",
                                    "url": "https://agents.example.com/.well-known/agent-card.json",
                                    "contract": {"input": text, "output": text}}]  # fmt: skip
    assert "PARTS := writer" in read("Makefile") and "parts/$$p/requirements.txt" in read("Makefile")
    assert "COPY parts ./parts" in read("Dockerfile") and "for p in writer;" in read("Dockerfile")
    assert "parts/*/.venv/" in read(".dockerignore")
    kit = json.loads(read("src/mixed_framework_team/kit.json"))
    assert kit["parts"] == [{"name": "writer", "framework": "crewai", "label": "CrewAI"}]
    assert {
        "host": "agents.example.com",
        "port": 443,
        "kind": "agent",
        "why": "the remote agent (A2A) 'checker'",
    } in kit["hosts"]  # make doctor checks it answers
    assert "## A mixed flow" in read("README.md") and "tests/test_parts.py" in files
    compile(read("tests/test_parts.py"), "test_parts.py", "exec")
