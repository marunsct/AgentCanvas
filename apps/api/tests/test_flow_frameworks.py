"""The Pydantic AI and Agno (S32), Microsoft Agent Framework (S33-S34), Strands and LlamaIndex (S35) adapters: flows
run by the flow engine with the frameworks' own agents, tools that wait for a person, typed answers, model call
limits, every template they can run, and exports.

Runs need the framework's Python environment (`make framework FW=pydantic_ai`, `make framework FW=agno`); without
one these tests skip, except those that only generate code or check what a server without it says.
"""

from __future__ import annotations

import ast
import io
import json
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import pytest

from agentcanvas import designs
from agentcanvas.adapters import create_adapter, flowpackager
from agentcanvas.adapters.agno import codegen as agno_codegen
from agentcanvas.adapters.llamaindex import codegen as llama_codegen
from agentcanvas.adapters.microsoft_agent_framework import codegen as maf_codegen
from agentcanvas.adapters.pydantic_ai import codegen as pai_codegen
from agentcanvas.adapters.strands import codegen as strands_codegen
from agentcanvas.ids import new_id
from agentcanvas.ir.models import Workflow
from agentcanvas.registry.adapters import all_manifests
from agentcanvas.runtime.types import ResumeSpec, RunSpec

TEMPLATES = Path(__file__).parents[1] / "agentcanvas" / "registry" / "templates"
FRAMEWORKS = ["pydantic_ai", "agno", "microsoft_agent_framework", "strands", "llamaindex"]
CODEGEN = {"pydantic_ai": pai_codegen, "agno": agno_codegen, "microsoft_agent_framework": maf_codegen,
           "strands": strands_codegen, "llamaindex": llama_codegen}  # fmt: skip
PINS = {
    "pydantic_ai": "pydantic-ai-slim[openai]==2.66.0",
    "agno": "agno==3.1.12",
    "microsoft_agent_framework": "agent-framework-core==1.25.0",
    "strands": "strands-agents[openai]==1.64.0",
    "llamaindex": "llama-index-core==0.14.25",
}
MODEL = {"id": "r_model", "type": "model.chat", "name": "model", "config": {"model": "openai:gpt-5.5"}}
ASK = {"role": "user", "content": "Tell the customer hi"}
CALL = {"content": "", "tool_calls": [{"name": "send_reply", "args": {"text": "hi"}}]}


def installed(framework: str) -> Any:
    return pytest.mark.skipif(designs.part_python(framework) is None, reason=f"no {framework} here (make framework)")


def _flow(src: str, dst: str, port: str | None = None) -> dict[str, Any]:
    source: dict[str, Any] = {"node": src} | ({"port": port} if port else {})
    return {"id": f"e_{src}_{dst}_{port or ''}", "kind": "flow", "source": source, "target": {"node": dst}}


def _wire(res: str, node: str, port: str = "model") -> dict[str, Any]:
    return {"id": f"w_{res}_{node}_{port}", "kind": "wiring", "source": {"node": res},
            "target": {"node": node, "port": port}}  # fmt: skip


def _agent_wf(framework: str, **config: Any) -> Workflow:
    limit = config.pop("run_limit", None)
    fail = config.pop("fail_at_limit", False)
    resources = [
        MODEL,
        {"id": "r_send", "type": "tool.python", "name": "send", "config": {"import_path": "examples.tools:send_reply"}},
    ]
    edges = [_flow("START", "n_agent"), _flow("n_agent", "END"), _wire("r_model", "n_agent"),
             _wire("r_send", "n_agent", "tools")]  # fmt: skip
    if limit:
        mw = {"run_limit": limit} | ({"exit_behavior": "error"} if fail else {})
        resources.append({"id": "r_limit", "type": "mw.model_call_limit", "name": "limit", "config": mw})
        edges.append(_wire("r_limit", "n_agent", "middleware"))
    channels = [{"name": "messages", "type": {"type": "array"}, "reducer": "add_messages"}]
    if config.get("response_format"):
        channels.append({"name": "structured_response", "type": {"type": "object"}})
    return Workflow.model_validate(
        {
            "id": "wf_flow", "name": "Support", "framework": framework,
            "state": {"channels": channels},
            "resources": resources,
            "nodes": [{"id": "n_agent", "type": "core.agent", "name": "support",
                       "config": {"system_prompt": "You help {customers}.", **config}}],
            "edges": edges,
        }
    )  # fmt: skip


async def _run(adapter, wf: Workflow, input: dict, mocks: dict, answers: list[Any] = ()) -> list[list[dict]]:
    thread = new_id("th")
    spec = RunSpec(workflow=wf, thread_id=thread, run_id=new_id("run"), input=input, mocks=mocks)
    answers, runs = list(answers), []
    resources = adapter.default_resources()
    while True:
        events = [ev async for ev in adapter.execute(spec, resources)]
        runs.append(events)
        if events[-1].get("outcome") != "interrupt" or not answers:
            return runs
        spec = RunSpec(workflow=wf, thread_id=thread, run_id=new_id("run"), resume=ResumeSpec(value=answers.pop(0)),
                       mocks=mocks)  # fmt: skip


def _said(events: list[dict]) -> list[str]:
    return [m["content"] for m in events[-1]["output"]["messages"] if m.get("type") == "ai"]


def _tool_io(runs: list[list[dict]]) -> list[dict]:
    return [ev["value"] for evs in runs for ev in evs if ev.get("name") == "ac.tool_io"]


@pytest.fixture(params=[pytest.param(f, marks=installed(f)) for f in FRAMEWORKS])
async def adapter(request):
    a = create_adapter(request.param)
    async with a.persistence(None):
        yield a


# ------------------------------------------------------------------ runs ----
async def test_agent_calls_a_canvas_tool(adapter):
    runs = await _run(adapter, _agent_wf(adapter.id), {"messages": [ASK]}, {"model": [CALL, "Done: I said hi."]})
    assert runs[0][-1]["outcome"] == "success", runs[0][-1]
    assert _said(runs[0]) == ["Done: I said hi."]
    assert [(io["tool"], io["args"], io["output"]) for io in _tool_io(runs)] == [
        ("send_reply", {"text": "hi"}, "sent: hi")
    ]
    replies = [ev["value"] for ev in runs[0] if ev.get("name") == "ac.model_reply"]
    assert [r["canvas_node_id"] for r in replies] == ["n_agent", "n_agent"]  # both model calls are recorded


async def test_structured_answer_fills_structured_response(adapter):
    schema = {"type": "object",
              "properties": {"category": {"enum": ["billing", "other"]}, "urgent": {"type": "boolean"}},
              "required": ["category", "urgent"]}  # fmt: skip
    reply = json.dumps({"category": "billing", "urgent": True})
    runs = await _run(adapter, _agent_wf(adapter.id, response_format=schema), {"messages": [ASK]}, {"model": [reply]})
    out = runs[0][-1]["output"]
    assert out["structured_response"] == {"category": "billing", "urgent": True}, runs[0][-1]
    assert json.loads(out["messages"][-1]["content"]) == {"category": "billing", "urgent": True}


@pytest.mark.parametrize(
    ("answer", "sent"),
    [
        ({"approved": True}, "sent: hi"),
        ({"decisions": [{"type": "edit", "edited_action": {"name": "send_reply", "args": {"text": "hello"}}}]},
         "sent: hello"),
        ({"decisions": [{"type": "reject", "message": "Not now."}]}, None),
    ],
    ids=["approve", "edit", "reject"],
)  # fmt: skip
async def test_a_tool_call_waits_for_a_person(adapter, answer, sent):
    wf = _agent_wf(adapter.id, approve_tools=["send_reply"])
    runs = await _run(adapter, wf, {"messages": [ASK]}, {"model": [CALL, "Done."]}, [answer])
    first = runs[0][-1]
    assert first["outcome"] == "interrupt", first
    asked = first["interrupts"][0]
    assert asked["kind"] == "tool_approval" and asked["canvas_node_id"] == "n_agent"
    assert asked["payload"]["action_requests"][0] == {"name": "send_reply", "args": {"text": "hi"},
                                                      "description": "support wants to call send_reply"}  # fmt: skip
    assert _tool_io(runs[:1]) == []  # nothing ran before the person answered
    assert runs[1][-1]["outcome"] == "success", runs[1][-1]
    assert _said(runs[1]) == ["Done."]
    assert [io["output"] for io in _tool_io(runs)] == ([sent] if sent else [])


APPROVING = ["microsoft_agent_framework", "strands", "llamaindex"]  # their own way of pausing for a person
TWO = {"content": "", "tool_calls": [{"name": "send_reply", "args": {"text": "one"}},
                                     {"name": "send_reply", "args": {"text": "two"}}]}  # fmt: skip
SAME = {"content": "", "tool_calls": [{"name": "send_reply", "args": {"text": "hi"}},
                                      {"name": "send_reply", "args": {"text": "hi"}}]}  # fmt: skip
EDIT = {"type": "edit", "edited_action": {"name": "send_reply", "args": {"text": "EDITED"}}}


@pytest.fixture(params=[pytest.param(f, marks=installed(f)) for f in APPROVING])
async def approving(request):
    """An adapter whose agents pause for a person natively (the model bridge streams each tool call of a reply in a
    chunk of its own, as OpenAI does: LlamaIndex reads one tool call per chunk)."""
    a = create_adapter(request.param)
    async with a.persistence(None):
        yield a


@pytest.mark.parametrize(
    ("reply", "decisions", "sent"),
    [
        (TWO, [{"type": "approve"}, EDIT], ["sent: EDITED", "sent: one"]),
        (TWO, [EDIT, {"type": "approve"}], ["sent: EDITED", "sent: two"]),
        (TWO, [{"type": "reject", "message": "no"}, {"type": "approve"}], ["sent: two"]),
        (SAME, [{"type": "approve"}, {"type": "reject"}], ["sent: hi"]),
        (SAME, [{"type": "approve"}, EDIT], ["sent: EDITED", "sent: hi"]),
    ],
    ids=["edit-second", "edit-first", "reject-first", "same-args-reject", "same-args-edit"],
)
async def test_each_call_of_a_turn_gets_its_own_decision(approving, reply, decisions, sent):
    """All the calls of a model turn that wait are asked about at once, and each decision applies to its own call
    (also when a tool is called twice, or with the same arguments)."""
    wf = _agent_wf(approving.id, approve_tools=["send_reply"])
    answer = {"decisions": decisions}
    runs = await _run(approving, wf, {"messages": [ASK]}, {"model": [reply, "Done."]}, [answer, answer])
    asked = runs[0][-1]["interrupts"][0]["payload"]["action_requests"]
    assert [r["args"] for r in asked] == [c["args"] for c in reply["tool_calls"]]
    assert len(runs) == 2 and runs[1][-1]["outcome"] == "success", runs[-1][-1]
    assert sorted(io["output"] for io in _tool_io(runs)) == sent


async def test_a_rejected_call_tells_the_model_why(approving, monkeypatch):
    from agentcanvas.adapters.langchain.testing import MockChatModel

    seen: list[str] = []
    generate = MockChatModel._generate

    def recording(self, messages, *args, **kwargs):
        seen.extend(str(m.content) for m in messages if m.type == "tool")
        return generate(self, messages, *args, **kwargs)

    monkeypatch.setattr(MockChatModel, "_generate", recording)
    wf = _agent_wf(approving.id, approve_tools=["send_reply"])
    answer = {"decisions": [{"type": "reject", "message": "Not now."}]}
    runs = await _run(approving, wf, {"messages": [ASK]}, {"model": [CALL, "Done."]}, [answer])
    assert runs[-1][-1]["outcome"] == "success", runs[-1][-1]
    assert _tool_io(runs) == []
    assert any("Rejected by a person: Not now." in s for s in seen), seen


async def test_a_model_call_limit_holds_across_approvals(approving):
    wf = _agent_wf(approving.id, approve_tools=["send_reply"], run_limit=2)
    runs = await _run(approving, wf, {"messages": [ASK]}, {"model": [CALL]}, [{"approved": True}] * 5)
    assert len(runs) <= 3 and runs[-1][-1]["outcome"] == "success", [r[-1] for r in runs]
    assert _said(runs[-1]) == ["Stopped: support reached its limit of 2 model calls before finishing."]
    assert sum(1 for r in runs for ev in r if ev.get("name") == "ac.model_reply") == 2


async def test_a_model_call_limit_stops_the_agent(adapter):
    events = (await _run(adapter, _agent_wf(adapter.id, run_limit=2), {"messages": [ASK]}, {"model": [CALL]}))[0]
    assert events[-1]["outcome"] == "success", events[-1]  # the last reply repeats: a tool loop, cut short
    assert _said(events) == ["Stopped: support reached its limit of 2 model calls before finishing."]
    assert len([ev for ev in events if ev.get("name") == "ac.model_reply"]) == 2


async def test_a_model_call_limit_can_fail_the_run(adapter):
    wf = _agent_wf(adapter.id, run_limit=2, fail_at_limit=True)
    events = (await _run(adapter, wf, {"messages": [ASK]}, {"model": [CALL]}))[0]
    assert events[-1]["type"] == "RUN_ERROR" and events[-1]["code"] == "model_call_limit", events[-1]


async def test_a_loop_past_the_limit_stops_the_run(adapter):
    wf = Workflow.model_validate({
        "id": "wf_spin", "name": "Spin", "framework": adapter.id, "settings": {"recursion_limit": 4},
        "state": {"channels": [{"name": "n", "default": 0}]},
        "nodes": [{"id": "n_a", "type": "core.set", "name": "a", "config": {"values": {"n": 1}}},
                  {"id": "n_r", "type": "core.router", "name": "again", "config": {"cases": [], "default": "loop"}}],
        "edges": [_flow("START", "n_a"), _flow("n_a", "n_r"), _flow("n_r", "n_a", "route:loop")],
    })  # fmt: skip
    events = (await _run(adapter, wf, {}, {}))[0]
    assert events[-1]["type"] == "RUN_ERROR" and events[-1]["code"] == "recursion_limit", events[-1]


async def test_unsupported_blocks_end_the_run_with_a_plain_error(adapter):
    wf = json.loads((TEMPLATES / "research_brief.ir.json").read_text())
    events = [ev async for ev in adapter.execute(
        RunSpec(workflow=Workflow.model_validate({**wf, "framework": adapter.id}), thread_id="t", run_id="r"),
        adapter.default_resources(),
    )]  # fmt: skip
    assert events[-1]["type"] == "RUN_ERROR"
    assert f"{adapter.manifest.label} cannot run 'agent.deep' blocks" in events[-1]["message"]


@pytest.mark.parametrize("framework", FRAMEWORKS)
async def test_without_the_framework_a_run_says_how_to_install_it(framework, monkeypatch):
    monkeypatch.setattr(designs, "part_python", lambda fw: None)
    a = create_adapter(framework)
    events = [ev async for ev in a.execute(
        RunSpec(workflow=_agent_wf(framework), thread_id="t", run_id="r"), a.default_resources()
    )]  # fmt: skip
    assert events[-1]["type"] == "RUN_ERROR" and f"make framework FW={framework}" in events[-1]["message"]


# ------------------------------------------------------------------ code ----
def test_generated_pydantic_ai_flow_is_native_code():
    schema = {"type": "object", "properties": {"category": {"type": "string"}}, "required": ["category"]}
    source = pai_codegen.generate(_agent_wf("pydantic_ai", approve_tools=["send_reply"], run_limit=3,
                                            response_format=schema))  # fmt: skip
    ast.parse(source)
    assert "from pydantic_ai import Agent, NativeOutput" in source
    assert 'run.model("model"),' in source and "instructions=SUPPORT_INSTRUCTIONS," in source
    assert 'tools=run.tools(["send"], approve=["send_reply"])' in source
    assert "output_type=NativeOutput(SupportAnswer)" in source and "class SupportAnswer(BaseModel):" in source
    assert 'run.agent(support_agent(run), state.get("messages"), structured=True, max_calls=3)' in source
    assert "FLOW = rt.Flow(" in source and "@canvas-node n_agent " in source


def test_generated_agno_flow_is_native_code():
    source = agno_codegen.generate(_agent_wf("agno", approve_tools=["send_reply"], run_limit=3))
    ast.parse(source)
    assert "from agno.agent import Agent" in source
    assert 'name="support",' in source and 'model=run.model("model"),' in source
    assert 'tools=run.tools(["send"], approve=["send_reply"])' in source
    assert "resolve_in_context=False," in source
    assert 'run.agent(support_agent(run), state.get("messages"), max_calls=3)' in source
    assert "FLOW = rt.Flow(" in source and "@canvas-node n_agent " in source


def test_generated_agent_framework_flow_is_native_code():
    schema = {"type": "object", "properties": {"category": {"type": "string"}}, "required": ["category"]}
    source = maf_codegen.generate(_agent_wf("microsoft_agent_framework", approve_tools=["send_reply"], run_limit=3,
                                            response_format=schema))  # fmt: skip
    ast.parse(source)
    assert "from agent_framework import Agent" in source
    assert 'run.model("model"),' in source and "instructions=SUPPORT_INSTRUCTIONS," in source
    assert 'tools=run.tools(["send"], approve=["send_reply"])' in source
    assert 'default_options={"response_format": SupportAnswer}' in source
    assert "class SupportAnswer(BaseModel):" in source
    assert 'run.agent(support_agent(run), state.get("messages"), structured=True, max_calls=3)' in source
    assert "FLOW = rt.Flow(" in source and "@canvas-node n_agent " in source


async def test_agent_framework_asks_only_about_the_tools_that_wait():
    """The Agent Framework asks about every call of a model turn once one tool needs approval: the others go ahead
    without asking a person."""
    if designs.part_python("microsoft_agent_framework") is None:
        pytest.skip("no microsoft_agent_framework here (make framework)")
    data = _agent_wf("microsoft_agent_framework", approve_tools=["send_reply"]).model_dump(mode="json")
    data["resources"].append(
        {
            "id": "r_note",
            "type": "tool.python",
            "name": "note",
            "config": {"import_path": "examples.tools:lookup_order"},
        }
    )
    data["edges"].append(_wire("r_note", "n_agent", "tools"))
    wf = Workflow.model_validate(data)
    both = {"content": "", "tool_calls": [{"name": "lookup_order", "args": {"order_id": "A1"}},
                                          {"name": "send_reply", "args": {"text": "hi"}}]}  # fmt: skip
    a = create_adapter("microsoft_agent_framework")
    async with a.persistence(None):
        runs = await _run(a, wf, {"messages": [ASK]}, {"model": [both, "Done."]}, [{"approved": True}])
    asked = runs[0][-1]["interrupts"][0]["payload"]["action_requests"]
    assert [r["name"] for r in asked] == ["send_reply"]
    assert runs[1][-1]["outcome"] == "success", runs[1][-1]
    assert sorted(io["tool"] for io in _tool_io(runs)) == ["lookup_order", "send_reply"]


def test_generated_strands_flow_is_native_code():
    schema = {"type": "object", "properties": {"category": {"type": "string"}}, "required": ["category"]}
    source = strands_codegen.generate(_agent_wf("strands", approve_tools=["send_reply"], run_limit=3,
                                                response_format=schema))  # fmt: skip
    ast.parse(source)
    assert "from strands import Agent" in source
    assert 'model=run.model("model"),' in source and "system_prompt=SUPPORT_INSTRUCTIONS," in source
    assert 'tools=run.tools(["send"]),' in source and 'hooks=[rt.AskBefore(["send_reply"])],' in source
    assert "structured_output_model=SupportAnswer," in source and "callback_handler=None," in source
    assert 'run.agent(support_agent(run), state.get("messages"), structured=True, max_calls=3)' in source


def test_generated_llamaindex_flow_is_native_code():
    schema = {"type": "object", "properties": {"category": {"type": "string"}}, "required": ["category"]}
    source = llama_codegen.generate(_agent_wf("llamaindex", approve_tools=["send_reply"], run_limit=3,
                                              response_format=schema))  # fmt: skip
    ast.parse(source)
    assert "from llama_index.core.agent.workflow import FunctionAgent" in source
    assert "def support_agent(run: rt.Run) -> FunctionAgent:" in source
    assert 'llm=run.model("model"),' in source and "system_prompt=SUPPORT_INSTRUCTIONS," in source
    assert 'tools=run.tools(["send"], approve=["send_reply"])' in source and "output_cls=SupportAnswer," in source
    assert 'run.agent(support_agent(run), state.get("messages"), structured=True, max_calls=3)' in source


@pytest.mark.parametrize("framework", FRAMEWORKS)
def test_paths_joins_and_loops_become_the_flow(framework):
    from agentcanvas.conformance import CASES

    def source(case_id: str) -> str:
        case = next(c for c in CASES if c.id == case_id)
        return CODEGEN[framework].generate(Workflow.model_validate({**case.workflow, "framework": framework}))

    parallel = source("flow/parallel")
    assert 'start=["n_left", "n_right"],' in parallel and '"n_join": rt.Step("join", "core.join"),' in parallel
    assert '"n_more": {"again": ["n_add"]' in source("flow/loop")


@pytest.mark.parametrize("framework", FRAMEWORKS)
def test_step_names_stay_unique_and_valid(framework):
    wf = Workflow.model_validate({
        "id": "wf_clash", "name": "Clash", "framework": framework,
        "state": {"channels": [{"name": "x", "default": ""}]},
        "nodes": [{"id": "n_1", "type": "core.set", "name": "run", "config": {"values": {"x": "1"}}},
                  {"id": "n_2", "type": "core.set", "name": "list", "config": {"values": {"x": "2"}}},
                  {"id": "n_3", "type": "core.set", "name": "run", "config": {"values": {"x": "3"}}}],
        "edges": [_flow("START", "n_1"), _flow("n_1", "n_2"), _flow("n_2", "n_3"), _flow("n_3", "END")],
    })  # fmt: skip
    source = CODEGEN[framework].generate(wf)
    ast.parse(source)
    assert "async def run_step(" in source and "async def list_step(" in source and "async def run_step2(" in source


def _supported(framework: str) -> list[str]:
    out = []
    for p in sorted(TEMPLATES.glob("*.ir.json")):
        data = json.loads(p.read_text())
        if any(all_manifests()[framework].support(el["type"]) == "unsupported"
               for el in [*data["nodes"], *data.get("resources", [])]):  # fmt: skip
            continue
        if any(n.get("engine") for n in data["nodes"]):  # mixed flows run on LangGraph (S28)
            continue
        out.append(p.name.removesuffix(".ir.json"))
    return out


TEMPLATE_CASES = [(f, name) for f in FRAMEWORKS for name in _supported(f)]


@pytest.mark.parametrize(("framework", "name"), TEMPLATE_CASES)
def test_supported_templates_generate_valid_python(framework, name):
    wf = Workflow.model_validate({**json.loads((TEMPLATES / f"{name}.ir.json").read_text()), "framework": framework})
    compile(CODEGEN[framework].generate(wf), name, "exec")


@pytest.mark.parametrize("framework", FRAMEWORKS)
def test_most_templates_run_on_both(framework):
    assert len(_supported(framework)) >= 20, _supported(framework)


@pytest.mark.parametrize(
    ("framework", "name"), [pytest.param(f, n, marks=installed(f)) for f, n in TEMPLATE_CASES]
)  # fmt: skip
async def test_supported_templates_run(app, api_client, framework, name):
    """Every template each framework can run does, end to end, with the demo model (like `test_templates`)."""
    from test_templates import demo_resources, template_input

    demo = demo_resources(app)
    app.state.resources_factory = lambda thread_id, wf: demo
    P = "/v1/projects/prj_default"
    if name == "knowledge_qa":
        assert (await api_client.post(f"{P}/knowledge", json={"sample": "handbook"})).status_code == 201
        demo.knowledge = app.state.projects.knowledge.for_project("prj_default")
    wf = (await api_client.post(f"{P}/workflows", json={"name": name, "template": name})).json()
    ir = {**wf["ir"], "framework": framework}
    r = await api_client.put(f"/v1/workflows/{wf['id']}", json={"ir": ir}, headers={"If-Match": str(wf["version"])})
    assert r.status_code == 200 and not [d for d in r.json()["diagnostics"] if d["severity"] == "error"], r.text
    text = (await api_client.post(f"/v1/workflows/{wf['id']}/runs/stream", json={"input": template_input(name)})).text
    last = [json.loads(ln[6:]) for ln in text.splitlines() if ln.startswith("data: ")][-1]
    assert last["type"] == "RUN_FINISHED", last


# ------------------------------------------------------------------ export ----
def _export_wf(framework: str) -> Workflow:
    data = _agent_wf(framework, approve_tools=["lookup_order"]).model_dump(mode="json")
    data["resources"][1]["config"]["import_path"] = "tools.orders:lookup_order"
    data["resources"][1]["name"] = "lookup_order"
    data["state"]["channels"] += [{"name": "draft"}, {"name": "ok", "type": {"type": "boolean"}}]
    data["nodes"] += [
        {"id": "n_draft", "type": "core.llm", "name": "draft_reply",
         "config": {"prompt": "Reply to: hi", "output": "draft"}},
        {"id": "n_ok", "type": "core.approval", "name": "check", "config": {"show": "draft", "output": "ok"}},
    ]  # fmt: skip
    data["edges"] = [e for e in data["edges"] if not (e["kind"] == "flow" and e["source"]["node"] == "n_agent")]
    data["edges"] += [_flow("n_agent", "n_draft"), _flow("n_draft", "n_ok"), _flow("n_ok", "END"),
                      _wire("r_model", "n_draft")]  # fmt: skip
    return Workflow.model_validate(data)


@pytest.mark.parametrize(
    ("name", "pkg"),
    [("Support", "support"), ("Strands", "wf_strands"), ("Llama Index", "wf_llama_index"),
     ("Workflows", "wf_workflows"), ("Agent Framework", "wf_agent_framework"), ("OpenAI", "wf_openai")],
)  # fmt: skip
def test_an_export_package_does_not_hide_what_it_imports(name, pkg):
    """The flow's package sits on src/ next to the frameworks' imports, which the server may not have installed."""
    assert flowpackager.flow_package(name) == pkg
    assert flowpackager.flow_package("tools", {"tools"}) == "wf_tools"  # the workspace code bundled with it
    data = _agent_wf("strands").model_dump(mode="json") | {"name": name}
    _, zipped = create_adapter("strands").package(Workflow.model_validate(data))
    assert f"src/{pkg}/workflow.py" in {n.split("/", 1)[1] for n in zipfile.ZipFile(io.BytesIO(zipped)).namelist()}


@pytest.mark.parametrize("framework", FRAMEWORKS)
def test_export_is_a_project_of_its_own(framework, tmp_path):
    ws = tmp_path / "ws"
    (ws / "tools").mkdir(parents=True)
    (ws / "tools" / "orders.py").write_text(
        'def lookup_order(order_id: str) -> str:\n    """Look up an order."""\n'
        '    return f"order {order_id}: shipped"\n'
    )
    cases = [{"name": "drafts and asks", "input": {"messages": [ASK]}, "mocks": {"model": ["It shipped."]},
              "expect": {"status": "interrupted", "channels": {"draft": "It shipped."}}}]  # fmt: skip
    name, data = create_adapter(framework).package(_export_wf(framework), workspace=ws, test_cases=cases,
                                                   version="1.2.0")  # fmt: skip
    slug = framework.replace("_", "-")
    assert name == f"support-{slug}-1.2.0.zip"
    root = tmp_path / "out"
    zipfile.ZipFile(io.BytesIO(data)).extractall(root)
    project = root / f"support-{slug}"
    names = {p.relative_to(project).as_posix() for p in project.rglob("*") if p.is_file()}
    assert {"src/support/workflow.py", "src/support/host.py", "src/tools/orders.py", "README.md", ".env.example",
            "src/agentcanvas/adapters/supersteps.py", f"src/agentcanvas/adapters/{framework}/runtime.py",
            "tests/test_workflow.py", "tests/test_cases.json"} <= names  # fmt: skip
    assert not any("langchain" in n for n in names)
    pyproject = (project / "pyproject.toml").read_text()
    assert PINS[framework] in pyproject
    assert (project / ".env.example").read_text() == "OPENAI_API_KEY=\n"
    for py in project.rglob("*.py"):
        compile(py.read_text(), str(py), "exec")
    python = designs.part_python(framework)
    if python is None:
        return
    # the command-line runner pauses at the tool call and at the approval, saves the run and continues it
    run = [python, "-c", "import sys; sys.path.insert(0, 'src'); from support.__main__ import main; main(sys.argv[1])"]
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "RUNS_DIR": str(tmp_path / "runs")}
    mocks = {"model": [{"content": "", "tool_calls": [{"name": "lookup_order", "args": {"order_id": "A1"}}]},
                       "It shipped."]}  # fmt: skip

    def step(req: dict) -> dict:
        out = subprocess.run([*run, json.dumps({"thread_id": "t9", "mocks": mocks, **req})], cwd=project, env=env,
                             capture_output=True, text=True, timeout=120, check=False)  # fmt: skip
        assert out.stdout, out.stderr
        return json.loads(out.stdout.splitlines()[-1])

    last = step({"input": {"messages": [ASK]}})
    assert last.get("outcome") == "interrupt" and last["interrupts"][0]["kind"] == "tool_approval", last
    assert (tmp_path / "runs" / "t9.json").is_file()
    last = step({"resume": {"approved": True}})
    assert last.get("outcome") == "interrupt" and last["interrupts"][0]["kind"] == "approval", last
    assert last["output"]["draft"] == "It shipped."
    assert "order A1: shipped" not in json.dumps(last["output"])  # the tool's answer went to the agent, not a field
    last = step({"resume": {"approved": True}})
    assert last["outcome"] == "success" and last["output"]["ok"] is True, last
    assert not (tmp_path / "runs" / "t9.json").exists()
    # the project's own tests pass too (the saved case, with a scripted model)
    tests = subprocess.run([python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"], cwd=project, env=env,
                           capture_output=True, text=True, timeout=180, check=False)  # fmt: skip
    if "No module named pytest" not in tests.stderr:
        assert tests.returncode == 0, tests.stdout + tests.stderr


@installed("strands")
async def test_a_blank_ask_ai_prompt_names_the_step_on_strands():
    """Strands 1.63 refuses a blank prompt ("Input prompt is blank"); the run says which step and why instead."""
    wf = Workflow.model_validate({
        "id": "wf_ask", "name": "Ask", "framework": "strands",
        "state": {"channels": [{"name": "question"}, {"name": "answer"}]},
        "resources": [MODEL],
        "nodes": [{"id": "n_ask", "type": "core.llm", "name": "ask_it",
                   "config": {"prompt": "{{ state.question }}", "output": "answer"}}],
        "edges": [_flow("START", "n_ask"), _flow("n_ask", "END"), _wire("r_model", "n_ask")],
    })  # fmt: skip
    a = create_adapter("strands")
    async with a.persistence(None):
        [events] = await _run(a, wf, {"question": "   "}, {"model": ["never asked"]})
    end = events[-1]
    assert end["type"] == "RUN_ERROR" and "ask_it" in end["message"] and "prompt is empty" in end["message"], end
