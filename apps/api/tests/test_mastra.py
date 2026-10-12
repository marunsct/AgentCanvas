"""The Mastra adapter (S36): TypeScript flows run by the flow engine's TypeScript twin on Node.js, with Mastra's own
agents, tools that wait for a person (Mastra's `requireApproval`), typed answers (Zod), model call limits, every
template it can run, and exports that are TypeScript projects of their own.

Runs need Node.js 22.18+ and the Mastra environment (`make mastra`); without them these tests skip, except those that
only generate code or check what a server without it says.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import pytest
from test_flow_frameworks import ASK, CALL, TEMPLATES, _agent_wf, _export_wf, _run, _said, _supported, _tool_io

from agentcanvas.adapters import create_adapter
from agentcanvas.adapters import mastra as mastra_adapter
from agentcanvas.adapters.mastra import codegen
from agentcanvas.ir.models import Workflow
from agentcanvas.runtime.types import RunSpec

installed = pytest.mark.skipif(mastra_adapter.mastra_dir() is None, reason="no Mastra here (make mastra)")
SCHEMA = {"type": "object", "properties": {"category": {"enum": ["billing", "other"]}, "urgent": {"type": "boolean"},
                                           "note": {"type": ["string", "null"], "description": "Anything else"}},
          "required": ["category", "urgent"]}  # fmt: skip


@pytest.fixture
async def adapter():
    a = create_adapter("mastra")
    async with a.persistence(None):
        yield a


def _node_check(source: str, tmp_path: Path) -> None:
    """Node.js reads the module as TypeScript it can run (types stripped, syntax checked)."""
    node = mastra_adapter.node()
    if node is None:
        pytest.skip("no Node.js 22.18+ here")
    path = tmp_path / "workflow.ts"
    path.write_text(source)
    out = subprocess.run([node, "--check", str(path)], capture_output=True, text=True, timeout=60, check=False)
    assert out.returncode == 0, out.stderr


# ------------------------------------------------------------------ runs ----
@installed
async def test_agent_calls_a_canvas_tool(adapter):
    runs = await _run(adapter, _agent_wf("mastra"), {"messages": [ASK]}, {"model": [CALL, "Done: I said hi."]})
    assert runs[0][-1]["outcome"] == "success", runs[0][-1]
    assert _said(runs[0]) == ["Done: I said hi."]
    assert [(io["tool"], io["args"], io["output"]) for io in _tool_io(runs)] == [
        ("send_reply", {"text": "hi"}, "sent: hi")
    ]
    replies = [ev["value"] for ev in runs[0] if ev.get("name") == "ac.model_reply"]
    assert [r["canvas_node_id"] for r in replies] == ["n_agent", "n_agent"]  # both model calls are recorded


@installed
async def test_structured_answer_fills_structured_response(adapter):
    reply = json.dumps({"category": "billing", "urgent": True})
    runs = await _run(adapter, _agent_wf("mastra", response_format=SCHEMA), {"messages": [ASK]}, {"model": [reply]})
    out = runs[0][-1]["output"]
    assert out["structured_response"] == {"category": "billing", "urgent": True}, runs[0][-1]
    assert json.loads(out["messages"][-1]["content"]) == {"category": "billing", "urgent": True}


@installed
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
    wf = _agent_wf("mastra", approve_tools=["send_reply"])
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


@installed
async def test_two_calls_that_wait_are_asked_one_after_the_other(adapter):
    """Mastra suspends the agent's run at each call that needs approval: two calls in one model turn are two pauses."""
    wf = _agent_wf("mastra", approve_tools=["send_reply"])
    two = {"content": "", "tool_calls": [{"name": "send_reply", "args": {"text": "a"}},
                                         {"name": "send_reply", "args": {"text": "b"}}]}  # fmt: skip
    runs = await _run(adapter, wf, {"messages": [ASK]}, {"model": [two, "Both sent."]},
                      [{"approved": True}, {"decisions": [{"type": "reject"}]}])  # fmt: skip
    asked = [r[-1]["interrupts"][0]["payload"]["action_requests"][0]["args"] for r in runs[:2]]
    assert asked == [{"text": "a"}, {"text": "b"}]
    assert runs[2][-1]["outcome"] == "success", runs[2][-1]
    assert [io["output"] for io in _tool_io(runs)] == ["sent: a"]


@installed
async def test_a_model_call_limit_stops_the_agent(adapter):
    events = (await _run(adapter, _agent_wf("mastra", run_limit=2), {"messages": [ASK]}, {"model": [CALL]}))[0]
    assert events[-1]["outcome"] == "success", events[-1]  # the last reply repeats: a tool loop, cut short
    assert _said(events) == ["Stopped: support reached its limit of 2 model calls before finishing."]
    assert len([ev for ev in events if ev.get("name") == "ac.model_reply"]) == 2


@installed
async def test_a_model_call_limit_can_fail_the_run(adapter):
    wf = _agent_wf("mastra", run_limit=2, fail_at_limit=True)
    events = (await _run(adapter, wf, {"messages": [ASK]}, {"model": [CALL]}))[0]
    assert events[-1]["type"] == "RUN_ERROR" and events[-1]["code"] == "model_call_limit", events[-1]


@installed
async def test_paths_follow_cel_rules_with_whole_numbers(adapter):
    """CEL in Node.js reads whole numbers as ints, as cel-python does: `state.n + 1 > 2` works there too."""
    wf = Workflow.model_validate({
        "id": "wf_count", "name": "Count", "framework": "mastra",
        "state": {"channels": [{"name": "n", "default": 0, "reducer": "sum"}, {"name": "lane", "default": ""},
                               {"name": "note", "default": ""}]},
        "nodes": [{"id": "n_add", "type": "core.set", "name": "add", "config": {"values": {"n": 1}}},
                  {"id": "n_r", "type": "core.router", "name": "more",
                   "config": {"cases": [{"when": "state.n + 1 > 3", "route": "done"}], "default": "again"}},
                  {"id": "n_done", "type": "core.set", "name": "done",
                   "config": {"values": {"lane": "done after {{ state.n }}", "note": "{{ state }}"}}}],
        "edges": [{"id": "e1", "kind": "flow", "source": {"node": "START"}, "target": {"node": "n_add"}},
                  {"id": "e2", "kind": "flow", "source": {"node": "n_add"}, "target": {"node": "n_r"}},
                  {"id": "e3", "kind": "flow", "source": {"node": "n_r", "port": "route:again"},
                   "target": {"node": "n_add"}},
                  {"id": "e4", "kind": "flow", "source": {"node": "n_r", "port": "route:done"},
                   "target": {"node": "n_done"}},
                  {"id": "e5", "kind": "flow", "source": {"node": "n_done"}, "target": {"node": "END"}}],
    })  # fmt: skip
    out = (await _run(adapter, wf, {}, {}))[0][-1]
    assert out["outcome"] == "success", out
    assert out["output"]["lane"] == "done after 3"
    assert json.loads(out["output"]["note"])["n"] == 3  # an object in a template is written as JSON


async def test_without_mastra_a_run_says_how_to_install_it(monkeypatch):
    monkeypatch.setattr(mastra_adapter, "mastra_dir", lambda: None)
    a = create_adapter("mastra")
    events = [ev async for ev in a.execute(
        RunSpec(workflow=_agent_wf("mastra"), thread_id="t", run_id="r"), a.default_resources()
    )]  # fmt: skip
    assert events[-1]["type"] == "RUN_ERROR" and "make mastra" in events[-1]["message"]


# ------------------------------------------------------------------ code ----
def test_generated_flow_is_typescript_with_mastra_agents(tmp_path):
    source = codegen.generate(_agent_wf("mastra", approve_tools=["send_reply"], run_limit=3, response_format=SCHEMA))
    assert 'import { Agent } from "@mastra/core/agent";' in source and 'import { z } from "zod";' in source
    assert 'import * as rt from "./runtime.ts";' in source
    assert "function supportAgent(run: rt.Run): Agent {" in source
    assert 'model: run.model("model"),' in source and "instructions: SUPPORT_INSTRUCTIONS," in source
    assert 'tools: run.tools(["send"], { approve: ["send_reply"] }),' in source
    assert 'category: z.enum(["billing", "other"]),' in source
    assert 'note: z.string().nullable().describe("Anything else").optional(),' in source
    assert "run.agent(supportAgent(run), state.messages, { answer: SupportAnswer, maxCalls: 3 })" in source
    assert "export const FLOW: rt.Flow = {" in source and "@canvas-node n_agent " in source
    _node_check(source, tmp_path)


def test_step_names_stay_unique_and_valid(tmp_path):
    wf = Workflow.model_validate({
        "id": "wf_clash", "name": "Clash", "framework": "mastra",
        "state": {"channels": [{"name": "x", "default": ""}]},
        "nodes": [{"id": "n_1", "type": "core.set", "name": "run", "config": {"values": {"x": "1"}}},
                  {"id": "n_2", "type": "core.set", "name": "delete", "config": {"values": {"x": "2"}}},
                  {"id": "n_3", "type": "core.set", "name": "run", "config": {"values": {"x": "3"}}},
                  {"id": "n_4", "type": "core.set", "name": "eval", "config": {"values": {"x": "4"}}}],
        "edges": [{"id": f"e{i}", "kind": "flow", "source": {"node": a}, "target": {"node": b}}
                  for i, (a, b) in enumerate([("START", "n_1"), ("n_1", "n_2"), ("n_2", "n_3"), ("n_3", "n_4"),
                                              ("n_4", "END")])],
    })  # fmt: skip
    source = codegen.generate(wf)
    for name in ("run2(", "delete2(", "run3(", "eval2("):
        assert f"export async function {name}" in source, source
    _node_check(source, tmp_path)


@pytest.mark.parametrize("name", _supported("mastra"))
def test_supported_templates_generate_typescript_node_runs(name, tmp_path):
    wf = Workflow.model_validate({**json.loads((TEMPLATES / f"{name}.ir.json").read_text()), "framework": "mastra"})
    _node_check(codegen.generate(wf), tmp_path)


def test_most_templates_run_on_mastra():
    assert len(_supported("mastra")) >= 20, _supported("mastra")


@installed
@pytest.mark.parametrize("name", _supported("mastra"))
async def test_supported_templates_run(app, api_client, name):
    """Every template Mastra can run does, end to end, with the demo model (like `test_templates`)."""
    from test_templates import INPUTS

    from agentcanvas.testing import DemoResources

    demo = DemoResources()
    app.state.resources_factory = lambda thread_id, wf: demo
    P = "/v1/projects/prj_default"
    if name == "knowledge_qa":
        assert (await api_client.post(f"{P}/knowledge", json={"sample": "handbook"})).status_code == 201
        demo.knowledge = app.state.projects.knowledge.for_project("prj_default")
    wf = (await api_client.post(f"{P}/workflows", json={"name": name, "template": name})).json()
    ir = {**wf["ir"], "framework": "mastra"}
    r = await api_client.put(f"/v1/workflows/{wf['id']}", json={"ir": ir}, headers={"If-Match": str(wf["version"])})
    assert r.status_code == 200 and not [d for d in r.json()["diagnostics"] if d["severity"] == "error"], r.text
    text = (await api_client.post(f"/v1/workflows/{wf['id']}/runs/stream", json={"input": INPUTS[name]})).text
    last = [json.loads(ln[6:]) for ln in text.splitlines() if ln.startswith("data: ")][-1]
    assert last["type"] == "RUN_FINISHED", last


# ------------------------------------------------------------------ export ----
def test_export_is_a_typescript_project_of_its_own(tmp_path):
    ws = tmp_path / "ws"
    (ws / "tools").mkdir(parents=True)
    (ws / "tools" / "orders.py").write_text(
        "from __future__ import annotations\n\n\n"
        'def lookup_order(order_id: str) -> str:\n    """Look up an order."""\n'
        '    return f"order {order_id}: shipped"\n'
    )
    cases = [{"name": "drafts and asks", "input": {"messages": [ASK]}, "mocks": {"model": ["It shipped."]},
              "expect": {"status": "interrupted", "channels": {"draft": "It shipped."}}}]  # fmt: skip
    name, data = create_adapter("mastra").package(_export_wf("mastra"), workspace=ws, test_cases=cases,
                                                  version="1.2.0")  # fmt: skip
    assert name == "support-mastra-1.2.0.zip"
    root = tmp_path / "out"
    zipfile.ZipFile(io.BytesIO(data)).extractall(root)
    project = root / "support-mastra"
    names = {p.relative_to(project).as_posix() for p in project.rglob("*") if p.is_file()}
    assert {"package.json", "tsconfig.json", "README.md", ".env.example", "src/workflow.ts", "src/flow.ts",
            "src/runtime.ts", "src/host.ts", "src/main.ts", "tests/workflow.test.ts", "tests/test_cases.json",
            "python/sidecar.py", "python/agentcanvas/knowledge_search.py",
            "python/tools/orders.py"} <= names  # fmt: skip
    manifest = json.loads((project / "package.json").read_text())
    assert manifest["dependencies"]["@mastra/core"] == codegen.MASTRA_VERSION
    assert manifest["scripts"]["test"] == "node --test tests/workflow.test.ts"
    assert (project / ".env.example").read_text() == "OPENAI_API_KEY=\n"
    for py in project.rglob("*.py"):
        compile(py.read_text(), str(py), "exec")
    env_dir = mastra_adapter.mastra_dir()
    node = mastra_adapter.node()
    if env_dir is None or node is None or shutil.which("python3") is None:
        return
    (project / "node_modules").symlink_to(Path(env_dir) / "node_modules")  # what `npm install` gives it
    env = {"PATH": f"{Path(node).parent}:/usr/bin:/bin", "HOME": str(tmp_path), "RUNS_DIR": str(tmp_path / "runs"),
           "NODE_NO_WARNINGS": "1"}  # fmt: skip
    mocks = {"model": [{"content": "", "tool_calls": [{"name": "lookup_order", "args": {"order_id": "A1"}}]},
                       "It shipped."]}  # fmt: skip

    def step(req: dict[str, Any]) -> dict[str, Any]:
        request = json.dumps({"thread_id": "t9", "mocks": mocks, **req})
        out = subprocess.run([node, "src/main.ts", request], cwd=project, env=env, capture_output=True, text=True,
                             timeout=120, check=False)  # fmt: skip
        assert out.stdout, out.stderr
        return json.loads(out.stdout.splitlines()[-1])

    # the command-line runner pauses at the tool call and at the approval, saves the run and continues it
    last = step({"input": {"messages": [ASK]}})
    assert last.get("outcome") == "interrupt" and last["interrupts"][0]["kind"] == "tool_approval", last
    assert (tmp_path / "runs" / "t9.json").is_file()
    last = step({"resume": {"approved": True}})
    assert last.get("outcome") == "interrupt" and last["interrupts"][0]["kind"] == "approval", last
    assert last["output"]["draft"] == "It shipped."
    last = step({"resume": {"approved": True}})
    assert last["outcome"] == "success" and last["output"]["ok"] is True, last
    assert not (tmp_path / "runs" / "t9.json").exists()
    # the workspace tool ran in the Python helper: the agent got its answer
    sidecar = subprocess.run(
        ["python3", "python/sidecar.py"], cwd=project, env=env, capture_output=True, text=True, timeout=60,
        input=json.dumps({"op": "describe", "tools": {"lookup_order": "tools.orders:lookup_order"}}) + "\n"
        + json.dumps({"op": "tool", "path": "tools.orders:lookup_order", "args": {"order_id": "A1"}}) + "\n",
        check=False,
    )  # fmt: skip
    described, called = [json.loads(ln)["value"] for ln in sidecar.stdout.splitlines()]
    assert described["lookup_order"][0]["parameters"]["properties"] == {"order_id": {"type": "string"}}
    assert called == "order A1: shipped"
    # the project's own tests pass too (the saved case, with a scripted model)
    tests = subprocess.run([node, "--test", "tests/workflow.test.ts"], cwd=project, env=env, capture_output=True,
                           text=True, timeout=180, check=False)  # fmt: skip
    assert tests.returncode == 0, tests.stdout + tests.stderr
