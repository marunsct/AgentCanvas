"""S31: Langflow, Flowise and n8n files become workflows that validate and run (exit: a Langflow export runs)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentcanvas.importers import ImportError_, detect, import_document
from agentcanvas.ir.models import Workflow
from agentcanvas.validation import validate

P = "/v1/projects/prj_default"
FILES = Path(__file__).parent / "importers"
NAMES = sorted(p.stem for p in FILES.glob("*.json"))
CHAT = {"messages": [{"role": "user", "content": "I want a refund for my order"}]}
INPUTS = {"n8n_branching": {"ticket": "The site is down, urgent!"}}


def _load(name: str) -> dict:
    return json.loads((FILES / f"{name}.json").read_text())


def _imported(name: str) -> tuple[str, Workflow, list[str], dict]:
    kind, ir, report, layout = import_document(_load(name), wf_id="wf_new")
    return kind, Workflow.model_validate(ir), report, layout


def _flows(wf: Workflow) -> set[tuple[str, str | None, str]]:
    names = {n.id: n.name for n in wf.nodes} | {"START": "START", "END": "END"}
    return {(names[e.source.node], e.source.port, names[e.target.node]) for e in wf.edges if e.kind == "flow"}


@pytest.mark.parametrize("name", NAMES)
def test_every_sample_imports_without_errors(name):
    kind, wf, _report, layout = _imported(name)
    assert kind == name.split("_")[0]
    assert wf.metadata["imported_from"] == kind
    assert not [d for d in validate(wf) if d.severity == "error"]
    assert layout and set(layout) <= {n.id for n in wf.nodes}


def test_detect():
    assert detect({"component_type": "Flow"}) == "agentspec"
    assert detect({"data": {"nodes": [], "edges": []}}) == "langflow"
    assert detect({"nodes": [{"type": "genericNode"}], "edges": []}) == "langflow"
    assert detect({"nodes": [], "connections": {}}) == "n8n"
    assert detect({"nodes": [{"type": "customNode"}], "edges": []}) == "flowise"
    assert detect({"hello": 1}) is None and detect([1]) is None


def test_langflow_basic_prompting():
    _, wf, report, _ = _imported("langflow_basic_prompting")
    (step,) = wf.nodes
    assert step.type == "core.llm" and step.name == "open_ai"
    assert step.config["prompt"].startswith("Answer the user as if you were a GenAI expert")
    assert step.config["prompt"].endswith("{{ state.messages[-1].content }}")  # the chat message
    assert [r.config for r in wf.resources] == [{"model": "openai:gpt-4.1-mini", "temperature": 0.1}]
    assert _flows(wf) == {("START", None, "open_ai"), ("open_ai", None, "END")}
    assert wf.settings.output_channels == ["messages", "open_ai_reply"]
    assert report == []


def test_langflow_agent_with_tools():
    _, wf, report, _ = _imported("langflow_agent")
    (agent,) = wf.nodes
    assert agent.type == "core.agent" and "helpful assistant" in agent.config["system_prompt"]
    kinds = {r.type: r.config for r in wf.resources}
    assert kinds["model.chat"] == {"model": "openai:gpt-4.1", "temperature": 0.1}
    assert kinds["tool.http"]["allowed_domains"] == ["api.open-meteo.com"]
    wired = {(e.target.port) for e in wf.edges if e.kind == "wiring"}
    assert wired == {"model", "tools"}
    assert any("Calculator" in r for r in report) and any("memory" in r for r in report)


def test_langflow_router_paths():
    _, wf, _, _ = _imported("langflow_router")
    router = next(n for n in wf.nodes if n.type == "core.router")
    assert router.config["cases"] == [
        {"when": 'state.messages[size(state.messages) - 1].content.contains("refund")', "route": "true"}
    ]
    assert _flows(wf) == {
        ("START", None, "if_else"),
        ("if_else", "route:true", "refunds"),
        ("if_else", "route:false", "other"),
        ("refunds", None, "END"),
        ("other", None, "END"),
    }
    models = {r.config["model"]: r.config for r in wf.resources}
    assert models["ollama:llama3.3"]["base_url"] == "http://localhost:11434"


def test_flowise_chatflow():
    _, wf, report, _ = _imported("flowise_chatflow")
    chain, agent = wf.nodes
    assert chain.config["prompt"] == "What is a good name for a company that makes {{ state.messages[-1].content }}?"
    assert agent.type == "core.agent" and agent.config["system_prompt"] == "You are a helpful AI assistant."
    assert _flows(wf) == {("START", None, "llm_chain"), ("llm_chain", None, "tool_agent"), ("tool_agent", None, "END")}
    assert {r.type for r in wf.resources} == {"model.chat", "tool.http"}
    assert any("Buffer Memory" in r for r in report)


def test_flowise_agentflow():
    _, wf, report, _ = _imported("flowise_agentflow")
    nodes = {n.name: n for n in wf.nodes}
    assert nodes["is_sales"].config["cases"] == [
        {"when": 'state.classify_reply.contains("sales")', "route": "branch_1"}
    ]
    assert nodes["check_reply"].type == "core.approval" and nodes["check_reply"].config["show"] == "classify_reply"
    assert nodes["approved"].config == {"values": {"approved_message": "Sent: {{ state.classify_reply }}"}}
    assert {
        ("is_sales", "route:branch_1", "sales_agent"),
        ("is_sales", "route:else", "check_reply"),
        ("check_reply_answer", "route:proceed", "approved"),
        ("check_reply_answer", "route:reject", "rejected"),
    } <= _flows(wf)
    assert any("customFunctionAgentflow" in r and "placeholder" in r for r in report)


def test_n8n_ai_agent():
    _, wf, report, _ = _imported("n8n_ai_agent")
    (agent,) = wf.nodes
    assert agent.config == {"system_prompt": "You are a helpful weather assistant."}
    assert {r.type for r in wf.resources} == {"model.chat", "tool.http"}
    assert any("Wikipedia" in r for r in report) and any("Simple Memory" in r for r in report)


def test_n8n_branches_do_not_wait_for_each_other():
    _, wf, report, _ = _imported("n8n_branching")
    nodes = {n.name: n for n in wf.nodes}
    assert nodes["classify"].config["prompt"] == (
        "Answer with one word.\n\nClassify this ticket as urgent or normal: {{ state.ticket }}"
    )
    assert nodes["page_on_call"].config == {"values": {"status": "Paged: {{ state.classify_reply }}"}}
    assert not [n for n in wf.nodes if n.type == "core.join"]  # only one branch runs: no Wait for all
    assert ("page_on_call", None, "send_slack") in _flows(wf) and ("queue_it", None, "send_slack") in _flows(wf)
    assert {c.name: c.io for c in wf.state.channels}["ticket"] == "in"
    assert any("Webhook" in r for r in report)


def test_parallel_langflow_inputs_wait_for_all():
    doc = _load("langflow_router")
    nodes = doc["data"]["nodes"]
    prompt = next(n for n in nodes if n["id"] == "Prompt-3")
    prompt["data"]["node"]["template"]["template"]["value"] = "A: {a} B: {b}"
    doc["data"]["edges"] = [
        e for e in doc["data"]["edges"] if e["target"] not in ("Prompt-3", "ConditionalRouter-2", "OllamaModel-5")
    ]
    doc["data"]["edges"] = [e for e in doc["data"]["edges"] if e["source"] != "Prompt-3"]

    def edge(src: str, output: str, dst: str, field: str) -> dict:
        handles = {"sourceHandle": {"name": output}, "targetHandle": {"fieldName": field}}
        return {"source": src, "target": dst, "data": handles}

    doc["data"]["edges"] += [
        edge("OllamaModel-5", "text_output", "Prompt-3", "a"),
        edge("AnthropicModel-4", "text_output", "Prompt-3", "b"),
        edge("Prompt-3", "prompt", "OpenAIModel-9", "input_value"),
    ]
    template = {"model_name": {"value": "gpt-5.5"}}
    combine = {"type": "OpenAIModel", "node": {"display_name": "Combine", "template": template}}
    nodes.append({"id": "OpenAIModel-9", "type": "genericNode", "position": {"x": 1, "y": 2}, "data": combine})
    _, ir, _, _ = import_document(doc, wf_id="wf_new")
    wf = Workflow.model_validate(ir)
    assert [n.name for n in wf.nodes if n.type == "core.join"] == ["wait_for_combine"]
    combine = next(n for n in wf.nodes if n.name == "combine")
    assert combine.config["prompt"] == "A: {{ state.other_reply }} B: {{ state.refunds_reply }}"


def _n8n_chain(diamonds: int, plain: int = 0, kind: str = "noOp") -> dict:
    """An n8n file: a chain of `diamonds` (two steps meeting again), then `plain` nodes of `kind` and a step."""
    nodes = [{"name": "T", "type": "n8n-nodes-base.manualTrigger", "parameters": {}}]
    conns: dict = {}
    prev = "T"

    def to(*names: str) -> dict:
        return {"main": [[{"node": n, "type": "main", "index": 0} for n in names]]}

    for i in range(diamonds):
        a, b, j = f"A{i}", f"B{i}", f"J{i}"
        nodes += [{"name": x, "type": "n8n-nodes-base.set", "parameters": {}} for x in (a, b, j)]
        conns[prev], conns[a], conns[b], prev = to(a, b), to(j), to(j), j
    for i in range(plain):
        nodes.append({"name": f"N{i}", "type": f"n8n-nodes-base.{kind}", "parameters": {}})
        conns[prev], prev = to(f"N{i}"), f"N{i}"
    nodes.append({"name": "Z", "type": "n8n-nodes-base.set", "parameters": {}})
    conns[prev] = to("Z")
    return {"nodes": nodes, "connections": conns}


def test_large_and_deep_files_import_quickly_or_fail_plainly():
    """Chains of diamonds took exponential time and deep chains overflowed the stack (a 500); now they import in
    linear time, and files too large or too deep get the importer's usual message."""
    import time

    started = time.perf_counter()
    _, ir, _, _ = import_document(_n8n_chain(600), wf_id="wf_new")
    assert len([n for n in ir["nodes"] if n["type"] == "core.join"]) == 600
    _, ir, _, _ = import_document(_n8n_chain(1, plain=1990, kind="set"), wf_id="wf_new")  # deeper than the stack
    assert len(ir["nodes"]) == 1995
    assert time.perf_counter() - started < 5
    with pytest.raises(ImportError_, match="more than 2000 steps"):
        import_document(_n8n_chain(700), wf_id="wf_new")
    with pytest.raises(ImportError_, match="nested too deeply"):
        import_document(_n8n_chain(0, plain=5000), wf_id="wf_new")  # ignored nodes looked through recursively


@pytest.mark.parametrize(
    ("doc", "message"),
    [
        ({"x": 1}, "not a Langflow, Flowise, n8n or Agent Spec"),
        ({"data": {"nodes": [], "edges": []}}, "no components"),
        ({"nodes": [], "connections": {}}, "no nodes"),
        ({"nodes": [{"id": "a", "data": {"name": "chatOpenAI"}}], "edges": []}, "no chain or agent"),
        ({"nodes": [{"name": "Start", "type": "n8n-nodes-base.manualTrigger"}], "connections": {}}, "no steps"),
        ({"data": {"nodes": [{"id": "x", "data": "oops"}], "edges": []}}, "could not be read"),
    ],
)
def test_bad_files(doc, message):
    with pytest.raises(ImportError_, match=message):
        import_document(doc, wf_id="wf_new")


@pytest.mark.parametrize("name", NAMES)
async def test_imported_workflows_run(app, api_client, name):
    """The exit criterion: a Langflow export opens and runs on the canvas (and so do the others)."""
    from agentcanvas.testing import DemoResources

    demo = DemoResources()
    app.state.resources_factory = lambda thread_id, wf: demo
    res = await api_client.post(f"{P}/workflows/import", json={"file": _load(name), "name": "Imported"})
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["format"] == name.split("_")[0] and body["name"] == "Imported" and isinstance(body["report"], list)
    assert res.headers["Location"] == f"/v1/workflows/{body['id']}"
    assert not [d for d in body["diagnostics"] if d["severity"] == "error"], body["diagnostics"]
    text = (
        await api_client.post(f"/v1/workflows/{body['id']}/runs/stream", json={"input": INPUTS.get(name, CHAT)})
    ).text
    events = [json.loads(ln[6:]) for ln in text.splitlines() if ln.startswith("data: ")]
    assert events[-1]["type"] == "RUN_FINISHED", events[-1]
    assert not [e for e in events if e["type"] == "RUN_ERROR"]


async def test_import_endpoint_rejects_other_files(api_client):
    res = await api_client.post(f"{P}/workflows/import", json={"file": {"hello": "world"}})
    assert res.status_code == 422 and "Langflow" in res.text
    spec = json.loads((Path(__file__).parent / "agentspec_sample.json").read_text())
    res = await api_client.post(f"{P}/workflows/import", json={"file": spec})
    assert res.status_code == 201 and res.json()["format"] == "agentspec"


SDK_CODE = (Path(__file__).parent / "importers" / "openai_agents_builder.py.txt").read_text()


def test_openai_agents_sdk_code_imports():
    """G-1: code from OpenAI's Agent Builder (closing 2026-11-30) or written with the Agents SDK becomes a workflow:
    agents in the order the code runs them, handoffs as a Planning Agent's specialists, function tools as Python
    tools whose code goes to the workspace, the output type as an answer shape. The code is read, never run."""
    from agentcanvas.importers import import_document
    from agentcanvas.ir.models import Workflow

    kind, ir, report, _ = import_document(SDK_CODE, wf_id="wf_SDK1")
    assert kind == "openai_agents"
    nodes = {n["label"] if "label" in n else n["name"]: n for n in ir["nodes"]}
    assert nodes["Classifier"]["type"] == "core.agent"
    assert nodes["Classifier"]["config"]["response_format"]["properties"] == {
        "category": {"type": "string"}, "urgent": {"type": "boolean"},
        "tags": {"type": "array", "items": {"type": "string"}}}  # fmt: skip
    assert nodes["Support agent"]["type"] == "agent.deep"  # it hands off: a Planning Agent
    by_type = {r["type"]: r for r in ir["resources"]}
    assert by_type["agent.subagent"]["config"]["description"] == "Questions about invoices and payments"
    assert by_type["tool.python"]["config"]["import_path"] == "tools.wf_sdk1_tools:lookup_order"
    models = [r["config"] for r in ir["resources"] if r["type"] == "model.chat"]
    assert models == [{"model": "openai:gpt-5-mini"}]  # one block: GPT-5 takes no temperature (W020), so 0.2 is dropped
    flow = [(e["source"]["node"], e["target"]["node"]) for e in ir["edges"] if e.get("kind") == "flow"]
    first, second = nodes["Classifier"]["id"], nodes["Support agent"]["id"]
    assert ("START", first) in flow and (first, second) in flow and (second, "END") in flow  # the run order
    [path] = ir["metadata"]["kit"]["files"]
    assert path == "tools/wf_sdk1_tools.py"
    code = ir["metadata"]["kit"]["files"][path]
    assert "@tool\ndef lookup_order" in code and "@function_tool" not in code
    compile(code, path, "exec")
    text = " ".join(report)
    assert "web search" in text and "guardrails" in text and "Planning Agent" in text
    wf = Workflow.model_validate({**ir, "metadata": {}})
    assert not [d for d in validate(wf) if d.severity == "error"]


async def test_the_import_route_takes_agents_sdk_code_and_writes_its_tools(app, api_client):
    r = await api_client.post("/v1/projects/prj_default/workflows/import", json={"code": SDK_CODE, "name": "Support"})
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["format"] == "openai_agents" and "kit" not in out["ir"].get("metadata", {})
    key = out["id"].lower()
    ws = app.state.projects.workspace("prj_default")
    assert "def lookup_order" in (ws.root / f"tools/{key}_tools.py").read_text()
    assert any(f"tools/{key}_tools.py" in line for line in out["report"])
    bad = await api_client.post("/v1/projects/prj_default/workflows/import",
                                json={"code": "from agents import Agent\nAgent(  # broken"})  # fmt: skip
    assert bad.status_code == 422 and "not valid Python" in bad.json()["detail"]
    both = await api_client.post("/v1/projects/prj_default/workflows/import", json={"code": SDK_CODE, "file": {}})
    assert both.status_code == 422
