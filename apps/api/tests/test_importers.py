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


def _prompt_of(wf: Workflow) -> str:
    (step,) = [n for n in wf.nodes if n.type == "core.llm"]
    return step.config["prompt"]


def test_langflow_prompts_keep_literal_braces_as_text():
    """Langflow prompts are Python format strings: a doubled brace is a literal one. A JSON example or a Markdown
    anchor imports as plain text that renders as written, not as a broken template."""
    from agentcanvas.expr import render_template

    doc = _load("langflow_basic_prompting")
    (prompt,) = [n for n in doc["data"]["nodes"] if n["data"].get("type") == "Prompt"]
    prompt["data"]["node"]["template"]["template"]["value"] = (
        'Reply as JSON like {{"answer": "..."}}. See {{#setup}}, {{{{ not a tag }}}} and {%x}. Q: {user_input}'
    )
    _, ir, report, _ = import_document(doc, wf_id="wf_new")
    wf = Workflow.model_validate(ir)
    assert not [d for d in validate(wf) if d.severity == "error"]
    text = render_template(_prompt_of(wf), {"user_input": "hi", "messages": [{"content": "hello"}]})
    assert text.startswith('Reply as JSON like {"answer": "..."}. See {#setup}, {{ not a tag }} and {%x}. Q: hi')
    assert any("kept as plain text" in line for line in report)


def test_flowise_prompts_keep_literal_braces_and_values_as_text():
    from agentcanvas.expr import render_template

    doc = _load("flowise_chatflow")
    (prompt,) = [n for n in doc["nodes"] if n["data"]["name"] == "promptTemplate"]
    prompt["data"]["inputs"]["template"] = 'A name for {product} as {{"name": "..."}} {{#tag}}?'
    prompt["data"]["inputs"]["promptValues"] = json.dumps({"product": "shoes {% endraw %}{#"})
    _, ir, _, _ = import_document(doc, wf_id="wf_new")
    wf = Workflow.model_validate(ir)
    assert not [d for d in validate(wf) if d.severity == "error"]
    chain = next(n for n in wf.nodes if n.name == "llm_chain")
    assert render_template(chain.config["prompt"], {}) == 'A name for shoes {% endraw %}{# as {"name": "..."} {#tag}?'


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


def test_langflow_1_13_loop_imports_without_its_loop_back():
    """Langflow 1.13's Research Translation Loop starter: the model reads a parser listed after it in the file (that
    failed to import), its model is the 1.13 model selector, and the loop body hands items back to the Loop."""
    _, wf, report, _ = _imported("langflow_loop")
    nodes = {n.name: n for n in wf.nodes}
    assert nodes["language_model"].type == "core.llm"
    assert nodes["language_model"].config["prompt"] == "{{ state.parser_result }}"
    (model,) = [r for r in wf.resources if r.type == "model.chat"]
    assert model.config["model"] == "openai:gpt-5.5"
    assert not [n for n in wf.nodes if n.type == "core.join"]  # no wait for a step that waits for it
    assert ("language_model", None, "END") in _flows(wf)
    assert "loop_result" in {c.name for c in wf.state.channels if c.io in ("out", "inout")}  # Chat Output shows it
    text = "\n".join(report)
    assert "loop back from 'Language Model' into 'Loop' was left out" in text
    assert "placeholder to replace with a Repeat for each item step" in text
    assert "not a step" not in text and "had no model name" not in text


def test_langflow_1_13_agent_model_selector():
    doc = _load("langflow_agent")
    agent = next(n for n in doc["data"]["nodes"] if n["data"]["type"] == "Agent")
    template = agent["data"]["node"]["template"]
    for old in ("agent_llm", "model_name"):
        template.pop(old, None)
    template["model"] = {"type": "model", "value": [{"name": "claude-sonnet-5-5", "provider": "Anthropic"}]}
    doc["data"]["edges"] = [
        e
        for e in doc["data"]["edges"]
        if e["target"] != agent["id"] or e["data"]["targetHandle"].get("fieldName") not in ("agent_llm", "llm", "model")
    ]
    _, ir, _, _ = import_document(doc, wf_id="wf_new")
    models = [r["config"]["model"] for r in ir["resources"] if r["type"] == "model.chat"]
    assert models == ["anthropic:claude-sonnet-5-5"]


def test_n8n_ai_agent():
    _, wf, report, _ = _imported("n8n_ai_agent")
    (agent,) = wf.nodes
    assert agent.config == {"system_prompt": "You are a helpful weather assistant."}
    assert {r.type for r in wf.resources} == {"model.chat", "tool.http"}
    assert any("Wikipedia" in r for r in report) and any("Simple Memory" in r for r in report)


def _n8n_v2_export() -> dict:
    """An n8n 2.x workflow using nodes n8n 3.0 removed: a Cron start, a v1 SQL Agent, LangChain Code, an OpenAI
    Assistant and a Pinecone Load vector store plugged in as a tool."""
    lc = "@n8n/n8n-nodes-langchain."
    nodes = [
        {"name": "Every hour", "type": "n8n-nodes-base.cron", "parameters": {}},
        {
            "name": "Ask the DB",
            "type": lc + "agent",
            "typeVersion": 1.5,
            "parameters": {"agent": "sqlAgent", "text": "How many orders today?"},
        },
        {"name": "Reshape", "type": lc + "code", "parameters": {}},
        {
            "name": "Helper",
            "type": lc + "openAiAssistant",
            "parameters": {"instructions": "You help with refunds.", "text": "={{ $json.output }}"},
        },
        {"name": "Docs", "type": lc + "vectorStorePineconeLoad", "parameters": {}},
        {"name": "Chatty", "type": lc + "agent", "parameters": {"agent": "conversationalAgent"}},
    ]
    main = lambda *names: {"main": [[{"node": n, "type": "main", "index": 0} for n in names]]}  # noqa: E731
    return {"nodes": nodes, "connections": {
        "Every hour": main("Ask the DB"), "Ask the DB": main("Reshape"), "Reshape": main("Helper"),
        "Helper": main("Chatty"), "Docs": {"ai_tool": [[{"node": "Helper", "type": "ai_tool", "index": 0}]]},
    }}  # fmt: skip


def test_n8n_nodes_removed_in_3_0_say_what_to_use_instead():
    _, ir, report, _ = import_document(_n8n_v2_export(), wf_id="wf_new")
    wf = Workflow.model_validate(ir)
    assert not [d for d in validate(wf) if d.severity == "error"]
    nodes = {n.name: n for n in wf.nodes}
    assert "every_hour" not in nodes  # a Cron node starts the workflow (it was a placeholder step)
    assert ("START", None, "ask_the_db") in _flows(wf)
    assert nodes["helper"].type == "core.agent"
    assert nodes["helper"].config["system_prompt"] == "You help with refunds."
    text = "\n".join(report)
    assert "'Every hour' (cron) started the n8n workflow" in text
    assert "SQL Agent (removed in n8n 3.0)" in text and "database tool" in text
    assert "LangChain Code, removed in n8n 3.0" in text and "placeholder to replace with a Run Python code" in text
    assert "OpenAI Assistant (removed in n8n 3.0)" in text
    assert "PineconeLoad vector store, removed in n8n 3.0" in text and "Search knowledge step" in text
    assert "Conversational Agent mode (removed in n8n 3.0)" in text


def test_n8n_3_vector_store_is_not_called_removed():
    doc = _n8n_v2_export()
    doc["nodes"][4]["type"] = "@n8n/n8n-nodes-langchain.vectorStorePinecone"
    _, _, report, _ = import_document(doc, wf_id="wf_new")
    (line,) = [r for r in report if "Pinecone" in r]
    assert "removed" not in line and "Search knowledge step" in line


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


@pytest.mark.parametrize("value", ["two\nlines", 'a "quote" and \\ back', "tab\tand \x00 nul", "é ünï 😀"])
def test_compared_text_becomes_a_cel_string_that_matches_itself(value):
    from agentcanvas.expr import check_condition, eval_condition
    from agentcanvas.importers import compare

    for op, expected in (("equals", True), ("not equals", False), ("contains", True)):
        rule = compare("state.x", op, value)
        assert rule is not None
        check_condition(rule)
        assert eval_condition(rule, {"x": value}) is expected, rule


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", float("nan"), "1e400"])
def test_a_number_that_is_not_finite_is_not_compared(value):
    from agentcanvas.importers import compare

    assert compare("state.x", "gt", value) is None  # nan/inf are no CEL numbers: the importer reports the rule


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
    assert '@tool(description="Look up an order\'s status by its id.")\ndef lookup_order' in code
    assert "@function_tool" not in code
    compile(code, path, "exec")
    text = " ".join(report)
    assert "web search" in text and "guardrails" in text and "Planning Agent" in text
    wf = Workflow.model_validate({**ir, "metadata": {}})
    assert not [d for d in validate(wf) if d.severity == "error"]


SDK_TOOLS = '''
import json
import math as m
from dataclasses import dataclass

from agents import Agent, RunContextWrapper, function_tool

RATE = 0.2
UNUSED = 42


@dataclass
class Account:
    owner: str


def _fmt(x: float) -> str:
    return json.dumps({"total": round(x, 2)})


@function_tool(description_override="Adds the tax to an amount.")
def with_tax(amount: float) -> str:
    return _fmt(amount * (1 + RATE))


@function_tool
def root(ctx: RunContextWrapper[Account], x: float) -> float:
    """The square root of x."""
    return m.sqrt(x)


@function_tool
def whoami(ctx: RunContextWrapper[Account]) -> str:
    return ctx.context.owner


@function_tool(name_override="describe_account")
def describe(owner: str) -> str:
    """Describes an account."""
    return repr(Account(owner))


helper = Agent(name="Helper", instructions="Use the tools.", tools=[with_tax, root, whoami, describe])
first = Agent(name="First", instructions="Say hi.")
result = first.run_sync("hi")
answer = helper.run_sync(result.final_output)
'''


def test_agents_sdk_tools_keep_what_they_use_and_import(tmp_path, monkeypatch):
    """Only the @function_tool bodies were copied: the module's imports, constants and helpers were lost (NameError),
    a tool without a docstring could not be made (LangChain needs a description) and the run-context parameter was
    shown to the model."""
    import importlib
    import sys

    _, ir, report, _ = import_document(SDK_TOOLS, wf_id="wf_t")
    [(path, code)] = ir["metadata"]["kit"]["files"].items()
    assert "UNUSED" not in code and "Agent(" not in code and "RunContextWrapper" not in code
    (tmp_path / "tools").mkdir()
    (tmp_path / path).write_text(code)
    monkeypatch.syspath_prepend(str(tmp_path))
    mod = importlib.import_module(path.removesuffix(".py").replace("/", "."))
    try:
        assert mod.with_tax.description == "Adds the tax to an amount."
        assert json.loads(mod.with_tax.invoke({"amount": 10})) == {"total": 12.0}
        assert mod.root.description == "The square root of x." and set(mod.root.args) == {"x"}
        assert mod.root.invoke({"x": 9}) == 3.0
        assert mod.whoami.description == "Whoami." and mod.whoami.args == {}
        assert mod.describe.name == "describe_account"
        assert mod.describe.invoke({"owner": "ana"}) == "Account(owner='ana')"  # the class keeps its @dataclass
    finally:
        sys.modules.pop(mod.__name__, None)
    text = " ".join(report)
    assert "Tool 'whoami' had no docstring" in text and "Tool 'whoami' reads the SDK's run context (ctx)" in text
    assert "Tool 'root' reads" not in text  # root never reads its context
    order = [n["name"] for n in sorted(ir["nodes"], key=lambda n: n["id"])]
    assert order == ["first", "helper"]  # agent.run_sync(...) calls, in the order the code makes them
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


def test_agents_sdk_code_written_against_agents_classic_imports_too():
    """The Agents SDK's next major release keeps today's API under `agents.classic` (its root package becomes a new
    runtime). Code written that way is the same workflow."""
    from agentcanvas.importers import import_document

    classic = SDK_CODE.replace("from agents import", "from agents.classic import")
    assert "from agents.classic import" in classic
    kind, ir, _, _ = import_document(classic, wf_id="wf_SDK1")
    plain_kind, plain_ir, _, _ = import_document(SDK_CODE, wf_id="wf_SDK1")
    assert kind == plain_kind == "openai_agents"
    shape = lambda d: sorted((n["type"], n["name"]) for n in d["nodes"])  # noqa: E731
    assert shape(ir) == shape(plain_ir)


def test_an_agent_builder_export_keeps_hosted_tools_and_instruction_functions():
    """How Agent Builder writes code (export: Code > Agents SDK > Python): hosted tools are variables
    (`web_search_preview = WebSearchTool(...)`), and instructions with variables are a function returning an f-string.
    Both were reported as missing ("the tool 'web_search_preview' is not defined", "computed by code")."""
    text = (FILES / "openai_agent_builder_export.py.txt").read_text()
    kind, ir, report, _ = import_document(text, wf_id="wf_AB1")
    wf = Workflow.model_validate(ir)
    assert kind == "openai_agents" and not [d for d in validate(wf) if d.severity == "error"]
    nodes = {n.name: n for n in wf.nodes}
    assert ("START", None, "classifier") in _flows(wf) and ("classifier", None, "trend_query") in _flows(wf)
    prompt = nodes["trend_query"].config["system_prompt"]
    assert prompt == "Find the latest official figures for the region {state_region}. Cite each source."
    joined = "\n".join(report)
    assert "not defined in this file" not in joined and "computed by code" not in joined
    assert "Agent 'trend query' used web search" in joined
    assert "hosted MCP server: add it as an App connector (MCP) ('docs' at https://mcp.example.com/mcp)" in joined
    assert "its instructions use state_region" in joined


def test_agents_made_by_a_function_and_instructions_in_a_constant_import():
    text = '''from agents import Agent, Runner

PROMPT = """You plan carousels."""


def build_planner() -> Agent:
    return Agent(name="Planner", instructions=PROMPT, model="gpt-5-mini")


async def main(text: str):
    return await Runner.run(build_planner(), text)
'''
    _, ir, report, _ = import_document(text, wf_id="wf_AB2")
    (node,) = ir["nodes"]
    assert node["name"] == "planner" and node["config"]["system_prompt"] == "You plan carousels."
    assert not [r for r in report if "computed by code" in r or "left out" in r]


def test_agents_sdk_code_nested_too_deeply_is_refused_plainly():
    code = 'from agents import Agent\na = Agent(name="a")\nx = ' + "-" * 200_000 + "1\n"
    with pytest.raises(ImportError_, match="nested too deeply"):
        import_document(code, wf_id="wf_new")
