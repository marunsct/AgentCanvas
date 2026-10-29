"""The Google ADK adapter (S29-S30): native ADK 2 workflows, agents with tools, typed answers and tool approvals,
exports.

Runs need a Python environment with ADK (`make framework FW=google_adk`); without one these tests skip, except those
that only generate code or check what a server without ADK says.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from agentcanvas.adapters import create_adapter
from agentcanvas.adapters import google_adk as adk_adapter
from agentcanvas.adapters.google_adk import codegen
from agentcanvas.ids import new_id
from agentcanvas.ir.models import Workflow
from agentcanvas.registry.adapters import all_manifests
from agentcanvas.runtime.types import ResumeSpec, RunSpec

TEMPLATES = Path(__file__).parents[1] / "agentcanvas" / "registry" / "templates"
needs_adk = pytest.mark.skipif(adk_adapter.adk_python() is None, reason="no Google ADK here (make framework)")
MODEL = {"id": "r_model", "type": "model.chat", "name": "model", "config": {"model": "openai:gpt-5.5"}}


def _flow(src: str, dst: str, port: str | None = None) -> dict[str, Any]:
    source: dict[str, Any] = {"node": src} | ({"port": port} if port else {})
    return {"id": f"e_{src}_{dst}_{port or ''}", "kind": "flow", "source": source, "target": {"node": dst}}


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
            "id": "wf_adk", "name": "Support", "framework": "google_adk",
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
    a = create_adapter("google_adk")
    async with a.persistence(None):
        yield a


ASK = {"role": "user", "content": "Tell the customer hi"}
CALL = {"content": "", "tool_calls": [{"name": "send_reply", "args": {"text": "hi"}}]}


# ------------------------------------------------------------------ runs ----
@needs_adk
async def test_agent_calls_a_canvas_tool(adapter):
    runs = await _run(adapter, _agent_wf(), {"messages": [ASK]}, {"model": [CALL, "Done: I said hi."]})
    assert runs[0][-1]["outcome"] == "success", runs[0][-1]
    assert _said(runs[0]) == ["Done: I said hi."]
    assert [(io["tool"], io["args"], io["output"]) for io in _tool_io(runs)] == [
        ("send_reply", {"text": "hi"}, "sent: hi")
    ]
    replies = [ev["value"] for ev in runs[0] if ev.get("name") == "ac.model_reply"]
    assert [r["canvas_node_id"] for r in replies] == ["n_agent", "n_agent"]  # both model calls are recorded


@needs_adk
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


@needs_adk
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
@pytest.mark.parametrize("approve", ["send", "send_reply"], ids=["block", "tool"])
async def test_a_tool_call_waits_for_a_person(adapter, answer, sent, approve):
    """`approve_tools` names the tool block (as the canvas checks it), or the tool it gives."""
    wf = _agent_wf(approve_tools=[approve])
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


def _call(text: str) -> dict[str, Any]:
    return {"content": "", "tool_calls": [{"name": "send_reply", "args": {"text": text}}]}


@needs_adk
async def test_an_approved_call_runs_once_when_the_turn_runs_again(adapter):
    """Each answer runs the agent's turn again: a call approved before gives its saved result, it is not sent twice."""
    wf = _agent_wf(approve_tools=["send"])
    model = [_call("one"), _call("two"), "Done."]
    runs = await _run(adapter, wf, {"messages": [ASK]}, {"model": model}, [{"approved": True}, {"approved": True}])
    assert [r[-1]["outcome"] for r in runs] == ["interrupt", "interrupt", "success"], runs[-1][-1]
    asked = [r[-1]["interrupts"][0]["payload"]["action_requests"][0]["args"] for r in runs[:2]]
    assert asked == [{"text": "one"}, {"text": "two"}]
    assert [io["output"] for io in _tool_io(runs)] == ["sent: one", "sent: two"]
    assert _said(runs[-1]) == ["Done."]


@needs_adk
async def test_an_answer_only_counts_for_the_call_the_person_saw(adapter):
    """When the turn runs again and the agent now calls the tool with other arguments, the person is asked again."""
    wf, thread = _agent_wf(approve_tools=["send"]), new_id("th")
    resources = adapter.default_resources()

    async def go(mocks: dict, **how: Any) -> list[dict]:
        spec = RunSpec(workflow=wf, thread_id=thread, run_id=new_id("run"), mocks=mocks, **how)
        return [ev async for ev in adapter.execute(spec, resources)]

    first = await go({"model": [_call("hi"), "Done."]}, input={"messages": [ASK]})
    assert first[-1]["outcome"] == "interrupt", first[-1]
    yes = ResumeSpec(value={"approved": True})
    changed = await go({"model": [_call("bye"), "Done."]}, resume=yes)
    assert changed[-1]["outcome"] == "interrupt", changed[-1]
    assert changed[-1]["interrupts"][0]["payload"]["action_requests"][0]["args"] == {"text": "bye"}
    assert _tool_io([changed]) == []  # the "yes" was for "hi"
    last = await go({"model": [_call("bye"), "Done."]}, resume=yes)
    assert last[-1]["outcome"] == "success", last[-1]
    assert [io["output"] for io in _tool_io([first, changed, last])] == ["sent: bye"]


@needs_adk
async def test_a_failed_continuation_can_be_tried_again(adapter, monkeypatch):
    """A worker that dies while a paused run continues does not lose the run: it can be continued again."""
    from agentcanvas.adapters.google_adk import AdkWorker

    wf = _agent_wf(approve_tools=["send"])
    thread, mocks = new_id("th"), {"model": [CALL, "Done."]}
    resources = adapter.default_resources()
    first = [ev async for ev in adapter.execute(
        RunSpec(workflow=wf, thread_id=thread, run_id=new_id("run"), input={"messages": [ASK]}, mocks=mocks),
        resources,
    )]  # fmt: skip
    assert first[-1]["outcome"] == "interrupt", first[-1]

    def broken(python: str) -> AdkWorker:
        worker = AdkWorker.__new__(AdkWorker)
        worker._runs = {}

        def send(msg: dict) -> None:
            raise BrokenPipeError(32, "Broken pipe")

        worker.send = send  # type: ignore[method-assign]
        return worker

    resume = ResumeSpec(value={"approved": True})
    with monkeypatch.context() as m:
        m.setattr(adapter, "worker", broken)
        failed = [ev async for ev in adapter.execute(
            RunSpec(workflow=wf, thread_id=thread, run_id=new_id("run"), resume=resume, mocks=mocks), resources
        )]  # fmt: skip
    assert failed[-1]["type"] == "RUN_ERROR" and "worker stopped" in failed[-1]["message"], failed[-1]
    again = [ev async for ev in adapter.execute(
        RunSpec(workflow=wf, thread_id=thread, run_id=new_id("run"), resume=resume, mocks=mocks), resources
    )]  # fmt: skip
    assert again[-1]["outcome"] == "success", again[-1]
    assert [io["output"] for io in _tool_io([first, failed, again])] == ["sent: hi"]
    gone = [ev async for ev in adapter.execute(
        RunSpec(workflow=wf, thread_id=thread, run_id=new_id("run"), resume=resume, mocks=mocks), resources
    )]  # fmt: skip
    assert gone[-1]["type"] == "RUN_ERROR" and "nothing to resume" in gone[-1]["message"]


@needs_adk
async def test_a_model_call_limit_stops_the_agent(adapter):
    events = (await _run(adapter, _agent_wf(run_limit=2), {"messages": [ASK]}, {"model": [CALL]}))[0]
    assert events[-1]["type"] == "RUN_ERROR" and events[-1]["code"] == "model_call_limit", events[-1]


@needs_adk
async def test_a_loop_past_the_limit_stops_the_run(adapter):
    wf = Workflow.model_validate({
        "id": "wf_spin", "name": "Spin", "framework": "google_adk", "settings": {"recursion_limit": 4},
        "state": {"channels": [{"name": "n", "default": 0}]},
        "nodes": [{"id": "n_a", "type": "core.set", "name": "a", "config": {"values": {"n": 1}}},
                  {"id": "n_r", "type": "core.router", "name": "again", "config": {"cases": [], "default": "loop"}}],
        "edges": [_flow("START", "n_a"), _flow("n_a", "n_r"), _flow("n_r", "n_a", "route:loop")],
    })  # fmt: skip
    events = (await _run(adapter, wf, {}, {}))[0]
    assert events[-1]["type"] == "RUN_ERROR" and events[-1]["code"] == "recursion_limit", events[-1]
    assert sum(1 for ev in events if ev["type"] == "STEP_FINISHED" and ev["canvas_node_id"] == "n_a") == 4


@needs_adk
async def test_a_pause_in_a_loop_continues_in_the_same_round(adapter):
    """Two rounds of a question in a loop: each answer reaches the round that asked, earlier rounds are replayed."""
    wf = Workflow.model_validate({
        "id": "wf_rounds", "name": "Rounds", "framework": "google_adk",
        "state": {"channels": [{"name": "answers", "type": {"type": "array"}, "reducer": "append", "default": []}]},
        "nodes": [
            {"id": "n_ask", "type": "core.human_input", "name": "ask",
             "config": {"prompt": "Next word?", "output": "answers"}},
            {"id": "n_more", "type": "core.router", "name": "more",
             "config": {"cases": [{"when": "size(state.answers) < 2", "route": "again"}], "default": "done"}},
        ],
        "edges": [_flow("START", "n_ask"), _flow("n_ask", "n_more"), _flow("n_more", "n_ask", "route:again"),
                  _flow("n_more", "END", "route:done")],
    })  # fmt: skip
    runs = await _run(adapter, wf, {}, {}, ["one", "two"])
    assert [r[-1]["outcome"] for r in runs] == ["interrupt", "interrupt", "success"]
    assert runs[-1][-1]["output"]["answers"] == ["one", "two"]
    asked = [ev["canvas_node_id"] for evs in runs for ev in evs if ev["type"] == "STEP_STARTED"]
    assert asked == ["n_ask", "n_ask", "n_more", "n_ask", "n_ask", "n_more"]  # nothing that finished runs again


@needs_adk
async def test_unsupported_blocks_end_the_run_with_a_plain_error(adapter):
    wf = json.loads((TEMPLATES / "research_brief.ir.json").read_text())
    events = [ev async for ev in adapter.execute(
        RunSpec(workflow=Workflow.model_validate({**wf, "framework": "google_adk"}), thread_id="t", run_id="r"),
        adapter.default_resources(),
    )]  # fmt: skip
    assert events[-1]["type"] == "RUN_ERROR"
    assert "Google ADK cannot run 'agent.deep' blocks" in events[-1]["message"]


async def test_without_adk_a_run_says_how_to_install_it(adapter, monkeypatch):
    monkeypatch.setattr(adk_adapter, "adk_python", lambda: None)
    events = [ev async for ev in adapter.execute(
        RunSpec(workflow=_agent_wf(), thread_id="t", run_id="r"), adapter.default_resources()
    )]  # fmt: skip
    assert events[-1]["type"] == "RUN_ERROR" and "make framework FW=google_adk" in events[-1]["message"]


# ------------------------------------------------------------------ code ----
def test_generated_workflow_is_native_adk_code():
    source = codegen.generate(_agent_wf(approve_tools=["send_reply"], run_limit=3))
    ast.parse(source)
    assert "from google.adk import Agent, Workflow" in source
    assert 'tools=run.tools(["send"], approve=["send_reply"])' in source
    assert "instruction=rt.literal(SUPPORT_INSTRUCTIONS)" in source  # `{customers}` is not ADK state templating
    assert "before_model_callback=run.call_limit(3)" in source
    assert '("START", support),' in source and "@canvas-node n_agent " in source


def test_paths_joins_and_loops_become_adk_edges():
    from agentcanvas.conformance import CASES

    def source(case_id: str) -> str:
        case = next(c for c in CASES if c.id == case_id)
        return codegen.generate(Workflow.model_validate({**case.workflow, "framework": "google_adk"}))

    parallel = source("flow/parallel")
    assert "from google.adk.workflow import JoinNode" in parallel
    assert 'join_wait = JoinNode(name="join_wait")' in parallel
    assert '("START", (left, right)),' in parallel and "(left, join_wait)," in parallel
    assert "(join_wait, join)," in parallel
    assert '(more, {"again": add}),' in source("flow/loop")
    assert '{"high": ' in source("flow/paths-high")


def test_step_names_stay_unique_and_valid():
    wf = Workflow.model_validate({
        "id": "wf_clash", "name": "Clash", "framework": "google_adk",
        "state": {"channels": [{"name": "x", "default": ""}]},
        "nodes": [{"id": "n_1", "type": "core.set", "name": "build", "config": {"values": {"x": "1"}}},
                  {"id": "n_2", "type": "core.set", "name": "list", "config": {"values": {"x": "2"}}},
                  {"id": "n_3", "type": "core.set", "name": "build", "config": {"values": {"x": "3"}}}],
        "edges": [_flow("START", "n_1"), _flow("n_1", "n_2"), _flow("n_2", "n_3"), _flow("n_3", "END")],
    })  # fmt: skip
    source = codegen.generate(wf)
    ast.parse(source)
    assert "async def build_step(" in source and "async def list_step(" in source
    assert "async def build_step2(" in source and "def build() -> Workflow:" in source


TEMPLATE_SUPPORT = sorted(
    p.name.removesuffix(".ir.json")
    for p in TEMPLATES.glob("*.ir.json")
    if all(
        all_manifests()["google_adk"].support(el["type"]) != "unsupported"
        for el in [*json.loads(p.read_text())["nodes"], *json.loads(p.read_text()).get("resources", [])]
    )
    and not any(n.get("engine") for n in json.loads(p.read_text())["nodes"])  # mixed flows run on LangGraph (S28)
)


@pytest.mark.parametrize("name", TEMPLATE_SUPPORT)
def test_supported_templates_generate_valid_python(name):
    wf = Workflow.model_validate({**json.loads((TEMPLATES / f"{name}.ir.json").read_text()), "framework": "google_adk"})
    compile(codegen.generate(wf), name, "exec")


def test_most_templates_run_on_adk():
    assert len(TEMPLATE_SUPPORT) >= 20, TEMPLATE_SUPPORT


@needs_adk
@pytest.mark.parametrize("name", TEMPLATE_SUPPORT)
async def test_supported_templates_run_on_adk(app, api_client, name):
    """Every template ADK can run does, end to end, with the demo model (like `test_templates` on LangGraph)."""
    from test_templates import demo_resources, template_input

    demo = demo_resources(app)
    app.state.resources_factory = lambda thread_id, wf: demo
    P = "/v1/projects/prj_default"
    if name == "knowledge_qa":
        assert (await api_client.post(f"{P}/knowledge", json={"sample": "handbook"})).status_code == 201
        demo.knowledge = app.state.projects.knowledge.for_project("prj_default")
    wf = (await api_client.post(f"{P}/workflows", json={"name": name, "template": name})).json()
    ir = {**wf["ir"], "framework": "google_adk"}
    r = await api_client.put(f"/v1/workflows/{wf['id']}", json={"ir": ir}, headers={"If-Match": str(wf["version"])})
    assert r.status_code == 200 and not [d for d in r.json()["diagnostics"] if d["severity"] == "error"], r.text
    text = (await api_client.post(f"/v1/workflows/{wf['id']}/runs/stream", json={"input": template_input(name)})).text
    last = [json.loads(ln[6:]) for ln in text.splitlines() if ln.startswith("data: ")][-1]
    assert last["type"] == "RUN_FINISHED", last


# ------------------------------------------------------------------ export ----
def _export_wf() -> Workflow:
    data = _agent_wf(approve_tools=["lookup_order"]).model_dump(mode="json")
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


def test_export_is_an_adk_project(tmp_path):
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
    name, data = create_adapter("google_adk").package(_export_wf(), workspace=ws, test_cases=cases, version="1.2.0")
    assert name == "support-google-adk-1.2.0.zip"
    root = tmp_path / "out"
    zipfile.ZipFile(__import__("io").BytesIO(data)).extractall(root)
    project = root / "support-google-adk"
    names = {p.relative_to(project).as_posix() for p in project.rglob("*") if p.is_file()}
    assert {"src/support/workflow.py", "src/support/host.py", "src/tools/orders.py", "README.md", ".env.example",
            "src/agentcanvas/adapters/google_adk/runtime.py", "tests/test_workflow.py"} <= names  # fmt: skip
    assert not any("langchain" in n for n in names)
    pyproject = (project / "pyproject.toml").read_text()
    assert "google-adk==2.13.0" in pyproject and "litellm==1.105.7" in pyproject
    assert (project / ".env.example").read_text() == "OPENAI_API_KEY=\n"
    for py in project.rglob("*.py"):
        compile(py.read_text(), str(py), "exec")
    python = adk_adapter.adk_python()
    if python is None:
        return
    # the command-line runner pauses at the tool call and at the approval, saves the run and continues it, with ADK
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
    last = step({"resume": {"approved": True}})
    assert last["outcome"] == "success" and last["output"]["ok"] is True, last
    assert not (tmp_path / "runs" / "t9.json").exists()
