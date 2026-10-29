"""The CrewAI adapter (S27): native CrewAI flows, agents with tools and typed answers, crews and tasks, exports.

Runs need a Python environment with CrewAI (`make crewai`); without one these tests skip, except those that only
generate code or check what a server without CrewAI says.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from agentcanvas.adapters import create_adapter
from agentcanvas.adapters import crewai as crewai_adapter
from agentcanvas.adapters.crewai import codegen
from agentcanvas.ids import new_id
from agentcanvas.ir.models import Workflow
from agentcanvas.registry.adapters import all_manifests
from agentcanvas.runtime import paused as paused_runs
from agentcanvas.runtime.types import ResumeSpec, RunSpec

TEMPLATES = Path(__file__).parents[1] / "agentcanvas" / "registry" / "templates"
needs_crewai = pytest.mark.skipif(crewai_adapter.crewai_python() is None, reason="no CrewAI here (make crewai)")
MODEL = {"id": "r_model", "type": "model.chat", "name": "model", "config": {"model": "openai:gpt-5.5"}}


def _flow(src: str, dst: str) -> dict[str, Any]:
    return {"id": f"e_{src}_{dst}", "kind": "flow", "source": {"node": src}, "target": {"node": dst}}


def _wire(res: str, node: str, port: str = "model") -> dict[str, Any]:
    return {"id": f"w_{res}_{node}_{port}", "kind": "wiring", "source": {"node": res},
            "target": {"node": node, "port": port}}  # fmt: skip


def _agent_wf(**config: Any) -> Workflow:
    limit = config.pop("run_limit", None)
    resources = [
        MODEL,
        {"id": "r_send", "type": "tool.python", "name": "send", "config": {"import_path": "examples.tools:send_reply"}},
    ]
    edges = [_flow("START", "n_agent"), _flow("n_agent", "END"), _wire("r_model", "n_agent"),
             _wire("r_send", "n_agent", "tools")]  # fmt: skip
    if limit:
        resources.append({"id": "r_limit", "type": "mw.model_call_limit", "name": "limit",
                          "config": {"run_limit": limit}})  # fmt: skip
        edges.append(_wire("r_limit", "n_agent", "middleware"))
    channels = [{"name": "messages", "type": {"type": "array"}, "reducer": "add_messages"}]
    if config.get("response_format"):
        channels.append({"name": "structured_response", "type": {"type": "object"}})
    return Workflow.model_validate(
        {
            "id": "wf_crew", "name": "Support", "framework": "crewai",
            "state": {"channels": channels},
            "resources": resources,
            "nodes": [{"id": "n_agent", "type": "core.agent", "name": "support",
                       "config": {"system_prompt": "You help customers.", **config}}],
            "edges": edges,
        }
    )  # fmt: skip


def _crew_wf() -> Workflow:
    """A crew researches, a task block with a guardrail answers (each with its own model block, so the scripted
    replies of one do not depend on the other)."""
    second = {"id": "r_model2", "type": "model.chat", "name": "checker", "config": {"model": "openai:gpt-5.5"}}
    return Workflow.model_validate(
        {
            "id": "wf_team", "name": "Bee team", "framework": "crewai",
            "state": {"channels": [{"name": "topic", "default": ""}, {"name": "report", "default": ""},
                                   {"name": "reply", "default": ""}]},
            "resources": [MODEL, second],
            "nodes": [
                {"id": "n_crew", "type": "crewai.crew", "name": "research", "config": {
                    "agents": [
                        {"name": "researcher", "role": "Researcher", "goal": "Find facts about {{ state.topic }}"},
                        {"name": "writer", "role": "Writer", "goal": "Write a short report",
                         "backstory": "You write for busy people."},
                    ],
                    "tasks": [
                        {"description": "List facts about {{ state.topic }}", "expected_output": "Three facts",
                         "agent": "researcher"},
                        {"description": "Write the report", "expected_output": "One paragraph", "agent": "writer"},
                    ],
                    "process": "sequential", "output": "report"}},
                {"id": "n_task", "type": "crewai.task", "name": "answer", "config": {
                    "role": "Support", "goal": "Help", "description": "Reply about {{ state.report }}",
                    "guardrail": "Be polite", "guardrail_max_retries": 1, "output": "reply"}},
            ],
            "edges": [_flow("START", "n_crew"), _flow("n_crew", "n_task"), _flow("n_task", "END"),
                      _wire("r_model", "n_crew"), _wire("r_model2", "n_task")],
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
        last = events[-1]
        if last.get("outcome") != "interrupt" or not answers:
            return runs
        spec = RunSpec(workflow=wf, thread_id=thread, run_id=new_id("run"), resume=ResumeSpec(value=answers.pop(0)),
                       mocks=mocks)  # fmt: skip


def _said(events: list[dict]) -> list[str]:
    return [m["content"] for m in events[-1]["output"]["messages"] if m.get("type") == "ai"]


def _tool_io(runs: list[list[dict]]) -> list[dict]:
    return [ev["value"] for evs in runs for ev in evs if ev.get("name") == "ac.tool_io"]


@pytest.fixture
async def adapter():
    a = create_adapter("crewai")
    async with a.persistence(None):
        yield a


ASK = {"role": "user", "content": "Tell the customer hi"}
CALL = {"content": "", "tool_calls": [{"name": "send_reply", "args": {"text": "hi"}}]}


# ------------------------------------------------------------------ runs ----
@needs_crewai
async def test_agent_calls_a_canvas_tool(adapter):
    runs = await _run(adapter, _agent_wf(), {"messages": [ASK]}, {"model": [CALL, "Done: I said hi."]})
    assert runs[0][-1]["outcome"] == "success", runs[0][-1]
    assert _said(runs[0]) == ["Done: I said hi."]
    assert [(io["tool"], io["args"], io["output"]) for io in _tool_io(runs)] == [
        ("send_reply", {"text": "hi"}, "sent: hi")
    ]
    replies = [ev["value"] for ev in runs[0] if ev.get("name") == "ac.model_reply"]
    assert [r["canvas_node_id"] for r in replies] == ["n_agent", "n_agent"]  # both model calls are recorded


@needs_crewai
async def test_structured_answer_fills_structured_response(adapter):
    schema = {"type": "object",
              "properties": {"category": {"enum": ["billing", "other"]}, "urgent": {"type": "boolean"}},
              "required": ["category", "urgent"]}  # fmt: skip
    wf = _agent_wf(response_format=schema)
    reply = json.dumps({"category": "billing", "urgent": True})
    runs = await _run(adapter, wf, {"messages": [ASK]}, {"model": [reply]})
    out = runs[0][-1]["output"]
    assert out["structured_response"] == {"category": "billing", "urgent": True}, runs[0][-1]
    assert json.loads(out["messages"][-1]["content"]) == {"category": "billing", "urgent": True}


@needs_crewai
async def test_a_crew_and_a_task_with_a_guardrail_run_as_steps(adapter):
    verdict = json.dumps({"valid": True, "feedback": None})  # the task's agent and its guardrail check share a model
    runs = await _run(adapter, _crew_wf(), {"topic": "bees"},
                      {"model": ["Bees pollinate flowers."], "checker": [verdict]})  # fmt: skip
    last = runs[0][-1]
    assert last["outcome"] == "success", last
    assert last["output"]["report"] == "Bees pollinate flowers."
    assert last["output"]["reply"] == verdict
    replies = [ev["value"] for ev in runs[0] if ev.get("name") == "ac.model_reply"]
    assert [r["canvas_node_id"] for r in replies].count("n_crew") == 2  # one model call per task of the crew
    assert [r["canvas_node_id"] for r in replies].count("n_task") == 2  # the answer, then the guardrail's check


@needs_crewai
async def test_a_guardrail_that_keeps_failing_fails_the_step(adapter):
    runs = await _run(adapter, _crew_wf(), {"topic": "bees"}, {"model": ["Facts."], "checker": ["Not JSON."]})
    last = runs[0][-1]
    assert last["type"] == "RUN_ERROR" and "guardrail" in last["message"], last
    failed = [ev for ev in runs[0] if ev["type"] == "STEP_FINISHED" and ev["status"] == "error"]
    assert [ev["canvas_node_id"] for ev in failed] == ["n_task"]


@needs_crewai
async def test_a_loop_past_the_limit_stops_the_run(adapter):
    wf = Workflow.model_validate({
        "id": "wf_spin", "name": "Spin", "framework": "crewai", "settings": {"recursion_limit": 4},
        "state": {"channels": [{"name": "n", "default": 0}]},
        "nodes": [{"id": "n_a", "type": "core.set", "name": "a", "config": {"values": {"n": 1}}},
                  {"id": "n_r", "type": "core.router", "name": "again", "config": {"cases": [], "default": "loop"}}],
        "edges": [_flow("START", "n_a"), _flow("n_a", "n_r"),
                  {"id": "e_back", "kind": "flow", "source": {"node": "n_r", "port": "route:loop"},
                   "target": {"node": "n_a"}}],
    })  # fmt: skip
    events = (await _run(adapter, wf, {}, {}))[0]
    assert events[-1]["type"] == "RUN_ERROR" and events[-1]["code"] == "recursion_limit", events[-1]
    assert sum(1 for ev in events if ev["type"] == "STEP_FINISHED" and ev["canvas_node_id"] == "n_a") == 4


@needs_crewai
async def test_a_pause_in_a_loop_continues_in_the_same_round(adapter):
    """Two rounds of a question in a loop: each answer reaches the round that asked, earlier rounds are replayed."""
    wf = Workflow.model_validate({
        "id": "wf_rounds", "name": "Rounds", "framework": "crewai",
        "state": {"channels": [{"name": "answers", "type": {"type": "array"}, "reducer": "append", "default": []}]},
        "nodes": [
            {"id": "n_ask", "type": "core.human_input", "name": "ask",
             "config": {"prompt": "Next word?", "output": "answers"}},
            {"id": "n_more", "type": "core.router", "name": "more",
             "config": {"cases": [{"when": "size(state.answers) < 2", "route": "again"}], "default": "done"}},
        ],
        "edges": [_flow("START", "n_ask"), _flow("n_ask", "n_more"),
                  {"id": "e_back", "kind": "flow", "source": {"node": "n_more", "port": "route:again"},
                   "target": {"node": "n_ask"}},
                  {"id": "e_end", "kind": "flow", "source": {"node": "n_more", "port": "route:done"},
                   "target": {"node": "END"}}],
    })  # fmt: skip
    runs = await _run(adapter, wf, {}, {}, ["one", "two"])
    assert [r[-1]["outcome"] for r in runs] == ["interrupt", "interrupt", "success"]
    assert runs[-1][-1]["output"]["answers"] == ["one", "two"]
    asked = [ev["canvas_node_id"] for evs in runs for ev in evs if ev["type"] == "STEP_STARTED"]
    assert asked == ["n_ask", "n_ask", "n_more", "n_ask", "n_ask", "n_more"]  # nothing that finished runs again


@needs_crewai
async def test_unsupported_blocks_end_the_run_with_a_plain_error(adapter):
    wf = json.loads((TEMPLATES / "research_brief.ir.json").read_text())
    events = [ev async for ev in adapter.execute(
        RunSpec(workflow=Workflow.model_validate({**wf, "framework": "crewai"}), thread_id="t", run_id="r"),
        adapter.default_resources(),
    )]  # fmt: skip
    assert events[-1]["type"] == "RUN_ERROR"
    assert "CrewAI cannot run 'agent.deep' blocks" in events[-1]["message"]


async def test_without_crewai_a_run_says_how_to_install_it(adapter, monkeypatch):
    monkeypatch.setattr(crewai_adapter, "crewai_python", lambda: None)
    events = [ev async for ev in adapter.execute(
        RunSpec(workflow=_agent_wf(), thread_id="t", run_id="r"), adapter.default_resources()
    )]  # fmt: skip
    assert events[-1]["type"] == "RUN_ERROR" and "make crewai" in events[-1]["message"]


async def test_a_paused_run_is_kept_until_its_continuation_ends(adapter, monkeypatch):
    """A continuation that fails (the worker died, the run errored) keeps the paused run, to be continued again; a new
    pause replaces it and the end of the run forgets it."""

    class Worker:
        def __init__(self) -> None:
            self.events: list[dict[str, Any]] = []
            self.sent: list[Any] = []

        async def run(self, run_id: str, request: dict[str, Any], handle: Any):
            self.sent.append(request["resume"]["paused"])
            for ev in self.events:
                yield dict(ev)

    worker = Worker()
    monkeypatch.setattr(adapter, "python", lambda: "python")
    monkeypatch.setattr(adapter, "worker", lambda python: worker)
    monkeypatch.setattr(paused_runs, "STORE", paused_runs.MemoryPaused())
    await paused_runs.save("t", '{"saved": 1}')

    async def resume() -> dict[str, Any]:
        spec = RunSpec(workflow=_agent_wf(), thread_id="t", run_id=new_id("run"), resume=ResumeSpec(value="yes"),
                       mocks={"model": ["hi"]})  # fmt: skip
        return [ev async for ev in adapter.execute(spec, adapter.default_resources())][-1]

    worker.events = [{"type": "RUN_ERROR", "code": "error", "message": "the CrewAI worker stopped"}]
    assert (await resume())["type"] == "RUN_ERROR"
    assert await paused_runs.load("t") == '{"saved": 1}'
    worker.events = [{"type": "RUN_FINISHED", "outcome": "interrupt", "paused": '{"saved": 2}'}]
    assert (await resume())["outcome"] == "interrupt"
    assert await paused_runs.load("t") == '{"saved": 2}'
    worker.events = [{"type": "RUN_FINISHED", "outcome": "success"}]
    assert (await resume())["outcome"] == "success"
    assert await paused_runs.load("t") is None
    assert worker.sent == ['{"saved": 1}', '{"saved": 1}', '{"saved": 2}']
    assert (await resume())["message"] == "nothing to resume on this conversation"


async def test_a_worker_that_cannot_take_a_run_ends_it_with_run_error():
    worker = crewai_adapter.CrewWorker.__new__(crewai_adapter.CrewWorker)
    worker._runs = {}

    def send(msg: dict[str, Any]) -> None:
        raise BrokenPipeError(32, "Broken pipe")

    worker.send = send  # type: ignore[method-assign]
    events = [ev async for ev in worker.run("r", {}, lambda op, args: None)]
    assert len(events) == 1 and events[0]["type"] == "RUN_ERROR", events
    assert "the CrewAI worker stopped" in events[0]["message"]
    assert worker._runs == {}


def test_asking_before_a_tool_call_is_refused_plainly():
    wf = _agent_wf(approve_tools=["send_reply"])
    with pytest.raises(Exception, match="cannot ask a person before a tool call"):
        create_adapter("crewai").generate(wf)


# ------------------------------------------------------------------ code ----
def test_generated_flow_is_native_crewai_code():
    source = codegen.generate(_crew_wf())
    ast.parse(source)
    assert "class BeeTeamFlow(Flow[dict]):" in source
    assert "from crewai import Agent, Crew, Process, Task" in source
    assert "@listen(research)" in source and "process=Process.sequential" in source
    assert 'guardrail="Be polite"' in source and "guardrail_max_retries=1" in source
    assert 'goal=rt.render("Find facts about {{ state.topic }}", state)' in source
    for n in _crew_wf().nodes:
        assert f"@canvas-node {n.id} " in source


def test_paths_joins_and_loops_become_crewai_listeners():
    from agentcanvas.conformance import CASES

    def source(case_id: str) -> str:
        case = next(c for c in CASES if c.id == case_id)
        return codegen.generate(Workflow.model_validate({**case.workflow, "framework": "crewai"}))

    assert "@listen(and_(left, right))" in source("flow/parallel")
    loop = source("flow/loop")
    assert '@listen(or_(begin, "more:again"))' in loop and "@router(add)" in loop
    assert '@listen("route:high")' in source("flow/paths-high")


def test_step_names_that_clash_with_flow_methods_get_a_suffix():
    wf = Workflow.model_validate({
        "id": "wf_clash", "name": "Clash", "framework": "crewai",
        "state": {"channels": [{"name": "x", "default": ""}]},
        "nodes": [{"id": "n_1", "type": "core.set", "name": "kickoff", "config": {"values": {"x": "1"}}},
                  {"id": "n_2", "type": "core.set", "name": "state", "config": {"values": {"x": "2"}}}],
        "edges": [_flow("START", "n_1"), _flow("n_1", "n_2"), _flow("n_2", "END")],
    })  # fmt: skip
    source = codegen.generate(wf)
    assert "async def kickoff_step(" in source and "async def state_step(" in source
    assert "@listen(kickoff_step)" in source


TEMPLATE_SUPPORT = sorted(
    p.name.removesuffix(".ir.json")
    for p in TEMPLATES.glob("*.ir.json")
    if all(
        all_manifests()["crewai"].support(el["type"]) != "unsupported"
        for el in [*json.loads(p.read_text())["nodes"], *json.loads(p.read_text()).get("resources", [])]
    )
    and not any(n.get("engine") for n in json.loads(p.read_text())["nodes"])  # mixed flows run on LangGraph (S28)
    and not any(n.get("config", {}).get("approve_tools") for n in json.loads(p.read_text())["nodes"])
)


@pytest.mark.parametrize("name", TEMPLATE_SUPPORT)
def test_supported_templates_generate_valid_python(name):
    wf = Workflow.model_validate({**json.loads((TEMPLATES / f"{name}.ir.json").read_text()), "framework": "crewai"})
    compile(codegen.generate(wf), name, "exec")


def test_most_templates_run_on_crewai():
    assert len(TEMPLATE_SUPPORT) >= 18, TEMPLATE_SUPPORT


def test_crewai_blocks_belong_to_crewai_flows():
    from agentcanvas.registry import default_registry
    from agentcanvas.validation import validate

    data = _crew_wf().model_dump(mode="json")
    assert not [d for d in validate(Workflow.model_validate(data), default_registry()) if d.severity == "error"]
    data["framework"] = "langchain"
    found = {(d.code, d.node_id) for d in validate(Workflow.model_validate(data), default_registry())}
    assert {("P001", "n_crew"), ("P001", "n_task")} <= found


@needs_crewai
@pytest.mark.parametrize("name", TEMPLATE_SUPPORT)
async def test_supported_templates_run_on_crewai(app, api_client, name):
    """Every template CrewAI can run does, end to end, with the demo model (like `test_templates` on LangGraph)."""
    from test_templates import demo_resources, template_input

    demo = demo_resources(app)
    app.state.resources_factory = lambda thread_id, wf: demo
    P = "/v1/projects/prj_default"
    if name == "knowledge_qa":
        assert (await api_client.post(f"{P}/knowledge", json={"sample": "handbook"})).status_code == 201
        demo.knowledge = app.state.projects.knowledge.for_project("prj_default")
    wf = (await api_client.post(f"{P}/workflows", json={"name": name, "template": name})).json()
    ir = {**wf["ir"], "framework": "crewai"}
    r = await api_client.put(f"/v1/workflows/{wf['id']}", json={"ir": ir}, headers={"If-Match": str(wf["version"])})
    assert r.status_code == 200 and not [d for d in r.json()["diagnostics"] if d["severity"] == "error"], r.text
    text = (await api_client.post(f"/v1/workflows/{wf['id']}/runs/stream", json={"input": template_input(name)})).text
    last = [json.loads(ln[6:]) for ln in text.splitlines() if ln.startswith("data: ")][-1]
    assert last["type"] == "RUN_FINISHED", last


# ------------------------------------------------------------------ export ----
def _export_wf() -> Workflow:
    data = _agent_wf().model_dump(mode="json")
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


def test_export_is_a_crewai_project(tmp_path):
    import subprocess
    import zipfile

    ws = tmp_path / "ws"
    (ws / "tools").mkdir(parents=True)
    (ws / "tools" / "orders.py").write_text(
        'def lookup_order(order_id: str) -> str:\n    """Look up an order."""\n'
        '    return f"order {order_id}: shipped"\n'
    )
    cases = [{"name": "drafts and asks", "input": {"messages": [ASK]}, "mocks": {"model": ["It shipped."]},
              "expect": {"status": "interrupted", "channels": {"draft": "It shipped."}}}]  # fmt: skip
    name, data = create_adapter("crewai").package(_export_wf(), workspace=ws, test_cases=cases, version="1.2.0")
    assert name == "support-crewai-1.2.0.zip"
    root = tmp_path / "out"
    zipfile.ZipFile(__import__("io").BytesIO(data)).extractall(root)
    project = root / "support-crewai"
    names = {p.relative_to(project).as_posix() for p in project.rglob("*") if p.is_file()}
    assert {"src/support/flow.py", "src/support/host.py", "src/tools/orders.py", "README.md", ".env.example",
            "src/agentcanvas/adapters/crewai/runtime.py", "tests/test_flow.py"} <= names  # fmt: skip
    assert not any("langchain" in n for n in names)
    assert "crewai==1.15.25" in (project / "pyproject.toml").read_text()
    assert (project / ".env.example").read_text() == "OPENAI_API_KEY=\n"
    for py in project.rglob("*.py"):
        compile(py.read_text(), str(py), "exec")
    python = crewai_adapter.crewai_python()
    if python is None:
        return
    # the command-line runner pauses, saves the run and continues it, with CrewAI
    run = [python, "-c", "import sys; sys.path.insert(0, 'src'); from support.__main__ import main; main(sys.argv[1])"]
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "RUNS_DIR": str(tmp_path / "runs")}
    mocks = {"model": ["It shipped."]}
    script = json.dumps({"input": {"messages": [ASK]}, "thread_id": "t9", "mocks": mocks})
    first = subprocess.run([*run, script], cwd=project, env=env, capture_output=True, text=True, timeout=120,
                           check=False)  # fmt: skip
    last = json.loads(first.stdout.splitlines()[-1])
    assert last["outcome"] == "interrupt" and last["interrupts"][0]["kind"] == "approval", first.stdout + first.stderr
    assert last["output"]["draft"] == "It shipped."
    assert (tmp_path / "runs" / "t9.json").is_file()
    script = json.dumps({"thread_id": "t9", "resume": {"approved": True}, "mocks": mocks})
    second = subprocess.run([*run, script], cwd=project, env=env, capture_output=True, text=True, timeout=120,
                            check=False)  # fmt: skip
    last = json.loads(second.stdout.splitlines()[-1])
    assert last["outcome"] == "success" and last["output"]["ok"] is True, second.stdout + second.stderr
    assert not (tmp_path / "runs" / "t9.json").exists()
