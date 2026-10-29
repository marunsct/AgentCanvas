"""The OpenAI Agents SDK adapter (S25): generated flows, agents with tools, approvals and typed answers."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from agentcanvas.adapters import create_adapter
from agentcanvas.adapters.openai_agents import codegen
from agentcanvas.ids import new_id
from agentcanvas.ir.models import Workflow
from agentcanvas.registry.adapters import all_manifests
from agentcanvas.runtime.types import ResumeSpec, RunSpec

TEMPLATES = Path(__file__).parents[1] / "agentcanvas" / "registry" / "templates"


def _agent_wf(**config: Any) -> Workflow:
    limit = config.pop("run_limit", None)
    resources = [
        {"id": "r_model", "type": "model.chat", "name": "model", "config": {"model": "openai:gpt-5.5"}},
        {"id": "r_send", "type": "tool.python", "name": "send", "config": {"import_path": "examples.tools:send_reply"}},
    ]
    edges = [
        {"id": "e1", "kind": "flow", "source": {"node": "START"}, "target": {"node": "n_agent"}},
        {"id": "e2", "kind": "flow", "source": {"node": "n_agent"}, "target": {"node": "END"}},
        {"id": "w1", "kind": "wiring", "source": {"node": "r_model"}, "target": {"node": "n_agent", "port": "model"}},
        {"id": "w2", "kind": "wiring", "source": {"node": "r_send"}, "target": {"node": "n_agent", "port": "tools"}},
    ]
    if limit:
        resources.append({"id": "r_limit", "type": "mw.model_call_limit", "name": "limit",
                          "config": {"run_limit": limit}})  # fmt: skip
        edges.append({"id": "w3", "kind": "wiring", "source": {"node": "r_limit"},
                      "target": {"node": "n_agent", "port": "middleware"}})  # fmt: skip
    channels = [{"name": "messages", "type": {"type": "array"}, "reducer": "add_messages"}]
    if config.get("response_format"):
        channels.append({"name": "structured_response", "type": {"type": "object"}})
    return Workflow.model_validate(
        {
            "id": "wf_oa", "name": "Support", "framework": "openai_agents",
            "state": {"channels": channels},
            "resources": resources,
            "nodes": [{"id": "n_agent", "type": "core.agent", "name": "support",
                       "config": {"system_prompt": "You help customers.", **config}}],
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
    a = create_adapter("openai_agents")
    async with a.persistence(None):
        yield a


ASK = {"role": "user", "content": "Tell the customer hi"}
CALL = {"content": "", "tool_calls": [{"name": "send_reply", "args": {"text": "hi"}}]}


async def test_agent_calls_a_canvas_tool(adapter):
    runs = await _run(adapter, _agent_wf(), {"messages": [ASK]}, {"model": [CALL, "Done: I said hi."]})
    assert runs[0][-1]["outcome"] == "success", runs[0][-1]
    assert _said(runs[0]) == ["Done: I said hi."]
    assert [(io["tool"], io["args"], io["output"]) for io in _tool_io(runs)] == [
        ("send_reply", {"text": "hi"}, "sent: hi")
    ]


async def test_tool_approval_pauses_and_approve_runs_the_tool(adapter):
    wf = _agent_wf(approve_tools=["send_reply"])
    runs = await _run(
        adapter, wf, {"messages": [ASK]}, {"model": [CALL, "Sent."]}, [{"decisions": [{"type": "approve"}]}]
    )
    first = runs[0][-1]
    assert first["outcome"] == "interrupt"
    intr = first["interrupts"][0]
    assert intr["kind"] == "tool_approval" and intr["canvas_node_id"] == "n_agent"
    assert intr["payload"]["action_requests"] == [
        {"name": "send_reply", "args": {"text": "hi"}, "description": "support wants to call send_reply"}
    ]
    assert not _tool_io(runs[:1])  # nothing ran before the person said yes
    assert runs[1][-1]["outcome"] == "success"
    assert _said(runs[1]) == ["Sent."]
    assert [io["output"] for io in _tool_io(runs)] == ["sent: hi"]


async def test_rejected_tool_call_never_runs(adapter):
    wf = _agent_wf(approve_tools=["send_reply"])
    answer = {"decisions": [{"type": "reject", "message": "Not now"}]}
    runs = await _run(adapter, wf, {"messages": [ASK]}, {"model": [CALL, "Okay, I did not send it."]}, [answer])
    assert runs[1][-1]["outcome"] == "success"
    assert _said(runs[1]) == ["Okay, I did not send it."]
    assert _tool_io(runs) == []


async def test_edited_tool_call_runs_with_the_new_arguments(adapter):
    wf = _agent_wf(approve_tools=["send_reply"])
    again = {"content": "", "tool_calls": [{"name": "send_reply", "args": {"text": "Hello there"}}]}
    edit = {"decisions": [{"type": "edit", "edited_action": {"name": "send_reply", "args": {"text": "Hello there"}}}]}
    runs = await _run(adapter, wf, {"messages": [ASK]}, {"model": [CALL, again, "Sent the edited reply."]}, [edit])
    assert len(runs) == 2, "the call made again as the person wrote it does not ask a second time"
    assert runs[1][-1]["outcome"] == "success", runs[1][-1]
    assert [io["output"] for io in _tool_io(runs)] == ["sent: Hello there"]
    assert _said(runs[1]) == ["Sent the edited reply."]


async def test_a_tool_block_name_asks_before_its_tool_runs(adapter):
    """`approve_tools` names the tool block, as the canvas checks it: its tool asks first."""
    runs = await _run(adapter, _agent_wf(approve_tools=["send"]), {"messages": [ASK]}, {"model": [CALL, "Sent."]})
    assert runs[0][-1]["outcome"] == "interrupt", runs[0][-1]
    assert _tool_io(runs) == []


async def test_a_failed_continuation_can_be_tried_again(adapter, monkeypatch):
    """A continuation that ends in RUN_ERROR keeps the paused run: continuing again runs the approved call once."""
    from agentcanvas.adapters.openai_agents import runtime as rt

    wf, thread, mocks = _agent_wf(approve_tools=["send_reply"]), new_id("th"), {"model": [CALL, "Sent."]}
    resources = adapter.default_resources()

    async def go(**how: Any) -> list[dict]:
        spec = RunSpec(workflow=wf, thread_id=thread, run_id=new_id("run"), mocks=mocks, **how)
        return [ev async for ev in adapter.execute(spec, resources)]

    async def failing(*args: Any, **kwargs: Any):
        yield {"type": "RUN_ERROR", "code": "error", "message": "the model provider is down"}

    first = await go(input={"messages": [ASK]})
    assert first[-1]["outcome"] == "interrupt", first[-1]
    yes = ResumeSpec(value={"decisions": [{"type": "approve"}]})
    with monkeypatch.context() as m:
        m.setattr(rt, "run", failing)
        failed = await go(resume=yes)
    assert failed[-1]["type"] == "RUN_ERROR", failed[-1]
    again = await go(resume=yes)
    assert again[-1]["outcome"] == "success", again[-1]
    assert [io["output"] for io in _tool_io([first, failed, again])] == ["sent: hi"]
    assert (await go(resume=yes))[-1]["message"] == "nothing to resume on this conversation"


async def test_structured_answer_fills_structured_response(adapter):
    schema = {"type": "object",
              "properties": {"category": {"enum": ["billing", "other"]}, "urgent": {"type": "boolean"}},
              "required": ["category", "urgent"]}  # fmt: skip
    wf = _agent_wf(response_format=schema)
    reply = json.dumps({"category": "billing", "urgent": True})
    runs = await _run(adapter, wf, {"messages": [ASK]}, {"model": [reply]})
    out = runs[0][-1]["output"]
    assert out["structured_response"] == {"category": "billing", "urgent": True}
    assert json.loads(out["messages"][-1]["content"]) == {"category": "billing", "urgent": True}
    assert "class SupportAnswer(BaseModel):" in create_adapter("openai_agents").generate(wf)


async def test_model_call_limit_stops_the_agent(adapter):
    wf = _agent_wf(run_limit=2)
    runs = await _run(adapter, wf, {"messages": [ASK]}, {"model": [CALL]})  # the last reply repeats: a tool loop
    assert runs[0][-1]["outcome"] == "success"
    assert _said(runs[0])[-1].startswith("Stopped: support reached its limit of 2 model calls")
    assert "max_turns=2" in codegen.generate(wf)


async def test_unsupported_blocks_end_the_run_with_a_plain_error(adapter):
    wf = json.loads((TEMPLATES / "research_brief.ir.json").read_text())
    events = [ev async for ev in adapter.execute(
        RunSpec(workflow=Workflow.model_validate({**wf, "framework": "openai_agents"}), thread_id="t", run_id="r"),
        adapter.default_resources(),
    )]  # fmt: skip
    assert events[-1]["type"] == "RUN_ERROR"
    assert "OpenAI Agents SDK cannot run 'agent.deep' blocks" in events[-1]["message"]


def test_generated_flow_reads_like_sdk_code():
    wf = Workflow.model_validate(
        {**json.loads((TEMPLATES / "classify_and_route.ir.json").read_text()), "framework": "openai_agents"}
    )
    source = codegen.generate(wf)
    ast.parse(source)
    assert "from agents import Agent" in source
    assert "agent = Agent(name=" in source
    for n in wf.nodes:
        assert f"@canvas-node {n.id} " in source
    assert "FLOW = rt.Flow(" in source


TEMPLATE_SUPPORT = sorted(
    p.name.removesuffix(".ir.json")
    for p in TEMPLATES.glob("*.ir.json")
    if all(
        all_manifests()["openai_agents"].support(el["type"]) != "unsupported"
        for el in [*json.loads(p.read_text())["nodes"], *json.loads(p.read_text()).get("resources", [])]
    )
    and not any(n.get("engine") for n in json.loads(p.read_text())["nodes"])  # mixed flows run on LangGraph (S28)
)


@pytest.mark.parametrize("name", TEMPLATE_SUPPORT)
def test_supported_templates_generate_valid_python(name):
    wf = Workflow.model_validate(
        {**json.loads((TEMPLATES / f"{name}.ir.json").read_text()), "framework": "openai_agents"}
    )
    compile(codegen.generate(wf), name, "exec")


def test_most_templates_run_on_the_sdk():
    assert len(TEMPLATE_SUPPORT) >= 22, TEMPLATE_SUPPORT


# ------------------------------------------------------------------ export ----
def _export_wf() -> Workflow:
    wf = _agent_wf(approve_tools=["lookup_order"])
    data = wf.model_dump(mode="json")
    data["resources"][1]["config"]["import_path"] = "tools.orders:lookup_order"
    data["resources"][1]["name"] = "lookup_order"
    data["state"]["channels"] += [{"name": "draft"}, {"name": "ok", "type": {"type": "boolean"}}]
    data["nodes"] += [
        {"id": "n_draft", "type": "core.llm", "name": "draft_reply",
         "config": {"prompt": "Reply to: hi", "output": "draft"}},
        {"id": "n_ok", "type": "core.approval", "name": "check", "config": {"show": "draft", "output": "ok"}},
    ]  # fmt: skip
    data["edges"] = [e for e in data["edges"] if not (e["kind"] == "flow" and e["source"]["node"] == "n_agent")]
    data["edges"] += [
        {"id": "e3", "kind": "flow", "source": {"node": "n_agent"}, "target": {"node": "n_draft"}},
        {"id": "e4", "kind": "flow", "source": {"node": "n_draft"}, "target": {"node": "n_ok"}},
        {"id": "e5", "kind": "flow", "source": {"node": "n_ok"}, "target": {"node": "END"}},
        {"id": "w9", "kind": "wiring", "source": {"node": "r_model"}, "target": {"node": "n_draft", "port": "model"}},
    ]  # fmt: skip
    return Workflow.model_validate(data)


def test_export_is_a_project_that_runs_its_saved_cases(tmp_path):
    import subprocess
    import sys
    import zipfile

    ws = tmp_path / "ws"
    (ws / "tools").mkdir(parents=True)
    (ws / "tools" / "orders.py").write_text(
        'def lookup_order(order_id: str) -> str:\n    """Look up an order."""\n'
        '    return f"order {order_id}: shipped"\n'
    )
    call = {"content": "", "tool_calls": [{"name": "lookup_order", "args": {"order_id": "A1"}}]}
    cases = [
        {"name": "pauses for the tool", "input": {"messages": [ASK]}, "mocks": {"model": [call]},
         "expect": {"status": "interrupted"}},
        {"name": "no tool", "input": {"messages": [ASK]}, "mocks": {"model": ["It shipped."]},
         "expect": {"status": "interrupted", "channels": {"draft": "It shipped."}}},
    ]  # fmt: skip
    name, data = create_adapter("openai_agents").package(_export_wf(), workspace=ws, test_cases=cases, version="1.2.0")
    assert name == "support-openai-agents-1.2.0.zip"
    z = zipfile.ZipFile(__import__("io").BytesIO(data))
    root = tmp_path / "out"
    z.extractall(root)
    project = root / "support-openai-agents"
    names = {p.relative_to(project).as_posix() for p in project.rglob("*") if p.is_file()}
    assert {"src/support/flow.py", "src/support/host.py", "src/tools/orders.py", "README.md", ".env.example",
            "src/agentcanvas/adapters/openai_agents/runtime.py", "tests/test_flow.py"} <= names  # fmt: skip
    assert not any("langchain" in n for n in names)
    assert "openai-agents==0.23.1" in (project / "pyproject.toml").read_text()
    assert (project / ".env.example").read_text() == "OPENAI_API_KEY=\n"
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=project, env=env, capture_output=True,
        text=True, timeout=120, check=False,
    )  # fmt: skip
    assert out.returncode == 0, out.stdout + out.stderr
    assert "4 passed" in out.stdout
    # the command-line runner pauses, saves the run and continues it
    run = [
        sys.executable,
        "-c",
        "import sys; sys.path.insert(0, 'src'); from support.__main__ import main; main(sys.argv[1])",
    ]
    mocks = {"model": ["It shipped."]}
    script = json.dumps({"input": {"messages": [ASK]}, "thread_id": "t9", "mocks": mocks})
    env["RUNS_DIR"] = str(tmp_path / "runs")
    first = subprocess.run([*run, script], cwd=project, env=env, capture_output=True, text=True, timeout=60,
                           check=False)  # fmt: skip
    last = json.loads(first.stdout.splitlines()[-1])
    assert last["outcome"] == "interrupt" and last["interrupts"][0]["kind"] == "approval", first.stdout + first.stderr
    assert (tmp_path / "runs" / "t9.json").is_file()
    script = json.dumps({"thread_id": "t9", "resume": {"approved": True}, "mocks": mocks})
    second = subprocess.run([*run, script], cwd=project, env=env, capture_output=True, text=True, timeout=60,
                           check=False)  # fmt: skip
    last = json.loads(second.stdout.splitlines()[-1])
    assert last["outcome"] == "success" and last["output"]["ok"] is True, second.stdout + second.stderr
    assert not (tmp_path / "runs" / "t9.json").exists()


# ------------------------------------------------------------------ convert and compare ----
class _Keywords:
    """A deterministic model for the template: it classifies by keyword and answers questions with "Tomorrow."."""

    @staticmethod
    def make():
        from langchain_core.language_models import BaseChatModel
        from langchain_core.messages import AIMessage
        from langchain_core.outputs import ChatGeneration, ChatResult

        class Keywords(BaseChatModel):
            @property
            def _llm_type(self) -> str:
                return "keywords-test"

            def _generate(self, messages, stop=None, run_manager=None, **kw):
                text = str(messages[-1].content)
                if text.startswith("Answer briefly"):
                    reply = "Tomorrow."
                elif "?" in text:
                    reply = "question"
                elif "never" in text:
                    reply = "complaint"
                else:
                    reply = "other"
                usage = {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}
                return ChatResult(generations=[ChatGeneration(message=AIMessage(content=reply, usage_metadata=usage))])

        return Keywords()


async def test_one_template_runs_on_both_frameworks_in_one_experiment(app, api_client):
    """S25 exit: a template compiles to LangGraph and to the OpenAI Agents SDK and compares in one experiment."""
    from test_datasets import new_dataset
    from test_experiments import finished

    from agentcanvas.adapters.langchain.nodes import Resources

    resources = Resources(overrides={"fast_model": _Keywords.make()})
    app.state.resources_factory = lambda thread_id, wf: resources
    P = "/v1/projects/prj_default"
    wf = (await api_client.post(f"{P}/workflows", json={"name": "Router", "template": "classify_and_route"})).json()
    sdk = (await api_client.get(f"/v1/workflows/{wf['id']}/code?framework=openai_agents")).text
    assert "from agents import Agent" in sdk and "FLOW = rt.Flow(" in sdk
    ds = await new_dataset(api_client, wf["id"])
    examples = [
        {"input": {"request": "Where is my order?"}, "reference": {"reply": "Tomorrow."}},
        {"input": {"request": "My parcel never came"},
         "reference": {"reply": "Sorry to hear that — a person will follow up on: My parcel never came"}},
        {"input": {"request": "Nice shop"}, "reference": {"reply": "Thanks, we received your message."}},
    ]  # fmt: skip
    assert (await api_client.post(f"/v1/datasets/{ds['id']}/examples", json={"examples": examples})).status_code == 200
    body = {"name": "exact", "kind": "exact", "config": {"channel": "reply"}}
    assert (await api_client.post(f"/v1/workflows/{wf['id']}/evaluators", json=body)).status_code == 201
    r = await api_client.post(
        f"/v1/workflows/{wf['id']}/experiments",
        json={"dataset_id": ds["id"], "targets": [{}, {"framework": "openai_agents"}]},
    )
    assert r.status_code == 202, r.text
    assert [(t["label"], t["framework"]) for t in r.json()["targets"]] == [
        ("draft", None), ("draft on OpenAI Agents SDK", "openai_agents"),
    ]  # fmt: skip
    exp = await finished(app, api_client, r.json()["id"])
    graph, sdk_target = exp["summary"]
    assert graph["pass_rate"] == sdk_target["pass_rate"] == 1.0, exp["summary"]
    assert sdk_target["tokens"] == graph["tokens"] > 0  # the SDK's model calls are counted like LangGraph's
    results = (await api_client.get(f"/v1/experiments/{exp['id']}/results")).json()
    by_target = [[r["output"]["reply"] for r in results if r["target"] == t] for t in (0, 1)]
    assert by_target[0] == by_target[1]
    trajectories = [[r["trajectory"] for r in results if r["target"] == t] for t in (0, 1)]
    assert trajectories[0] == trajectories[1]


async def test_a_target_on_a_framework_that_cannot_run_the_workflow_is_refused(api_client):
    P = "/v1/projects/prj_default"
    wf = (await api_client.post(f"{P}/workflows", json={"name": "Research", "template": "research_brief"})).json()
    from test_datasets import new_dataset

    ds = await new_dataset(api_client, wf["id"])
    url = f"/v1/workflows/{wf['id']}/experiments"
    r = await api_client.post(url, json={"dataset_id": ds["id"], "targets": [{"framework": "openai_agents"}]})
    assert r.status_code == 422 and "cannot run 'agent.deep' blocks" in r.text
    r = await api_client.post(url, json={"dataset_id": ds["id"], "targets": [{"framework": "nope"}]})
    assert r.status_code == 422 and "no framework 'nope'" in r.text


def test_an_agent_designed_for_the_sdk_is_native_in_an_sdk_flow():
    from agentcanvas.registry import default_registry
    from agentcanvas.validation import validate

    data = _agent_wf().model_dump(mode="json")
    data["nodes"][0]["engine"] = "openai_agents"
    found = {(d.code, d.node_id) for d in validate(Workflow.model_validate(data), default_registry())}
    assert not [f for f in found if f[0] in ("P001", "P004", "P005")], found


@pytest.mark.parametrize("name", TEMPLATE_SUPPORT)
async def test_supported_templates_run_on_the_sdk(app, api_client, name):
    """Every template the SDK can run does, end to end, with the demo model (like `test_templates` on LangGraph)."""
    from test_templates import demo_resources, template_input

    demo = demo_resources(app)
    app.state.resources_factory = lambda thread_id, wf: demo
    P = "/v1/projects/prj_default"
    if name == "knowledge_qa":
        assert (await api_client.post(f"{P}/knowledge", json={"sample": "handbook"})).status_code == 201
        demo.knowledge = app.state.projects.knowledge.for_project("prj_default")
    wf = (await api_client.post(f"{P}/workflows", json={"name": name, "template": name})).json()
    ir = {**wf["ir"], "framework": "openai_agents"}
    r = await api_client.put(f"/v1/workflows/{wf['id']}", json={"ir": ir}, headers={"If-Match": str(wf["version"])})
    assert r.status_code == 200 and not [d for d in r.json()["diagnostics"] if d["severity"] == "error"], r.text
    text = (await api_client.post(f"/v1/workflows/{wf['id']}/runs/stream", json={"input": template_input(name)})).text
    last = [json.loads(ln[6:]) for ln in text.splitlines() if ln.startswith("data: ")][-1]
    assert last["type"] == "RUN_FINISHED", last
