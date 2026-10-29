"""S16: Open Agent Spec export and import. The CI `frameworks` job also loads these files with pyagentspec."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentcanvas.agentspec import AgentSpecError, export_flow, ident, import_flow
from agentcanvas.expr import eval_condition
from agentcanvas.ir.models import Workflow
from agentcanvas.services.workflows import TEMPLATES
from agentcanvas.validation import validate

P = "/v1/projects/prj_default"
SAMPLE = json.loads((Path(__file__).parent / "agentspec_sample.json").read_text())  # written by pyagentspec
NAMES = sorted(p.name.removesuffix(".ir.json") for p in TEMPLATES.glob("*.ir.json"))


def _template(name: str) -> Workflow:
    return Workflow.model_validate(json.loads((TEMPLATES / f"{name}.ir.json").read_text()))


def _components(doc: dict) -> dict[str, dict]:
    return doc["$referenced_components"]


def _strip(doc: dict) -> dict:
    """The file as another tool would write it: no AgentCanvas metadata."""
    doc = json.loads(json.dumps(doc))
    doc["metadata"] = {}
    for c in _components(doc).values():
        c["metadata"] = {}
        for inner in ("agent", "tool"):
            if isinstance(c.get(inner), dict):
                c[inner]["metadata"] = {}
    return doc


@pytest.mark.parametrize("name", NAMES)
def test_every_template_exports_and_comes_back_exactly(name):
    wf = _template(name)
    doc, _report = export_flow(wf, {"n_x": {"x": 1, "y": 2}})
    assert doc["component_type"] == "Flow" and doc["agentspec_version"] == "25.4.2"
    kinds = {c["component_type"] for c in _components(doc).values()}
    assert {"StartNode", "EndNode"} <= kinds
    ir, notes, layout = import_flow(doc, wf_id="wf_new", name="Copy")
    assert ir == {**wf.model_dump(mode="json"), "id": "wf_new", "name": "Copy"}
    assert notes == ["Restored exactly from the AgentCanvas data inside the file."]
    assert layout == {"n_x": {"x": 1, "y": 2}}


@pytest.mark.parametrize("name", NAMES)
def test_every_template_imports_without_our_metadata(name):
    ir, _report, _layout = import_flow(_strip(export_flow(_template(name))[0]), wf_id="wf_new")
    Workflow.model_validate(ir)


def test_export_maps_steps_to_agent_spec_nodes():
    doc, report = export_flow(_template("classify_and_route"))
    comps = _components(doc)
    by_name = {c["name"]: c for c in comps.values() if c["component_type"].endswith("Node")}
    llm = by_name["classify"]
    assert llm["component_type"] == "LlmNode"
    assert llm["prompt_template"].endswith("{{request}}")
    assert llm["outputs"] == [{"title": "category", "type": "string", "description": "question | complaint | other"}]
    assert comps[llm["llm_config"]["$component_ref"]]["component_type"] == "OpenAiCompatibleConfig"
    route = by_name["route"]
    assert route["component_type"] == "BranchingNode"
    assert route["mapping"] == {"question": "question", "complaint": "complaint"}
    assert route["branches"] == ["complaint", "default", "question"]
    assert by_name["apologise"]["component_type"] == "ToolNode"
    flows = doc["control_flow_connections"]
    branches = {(e["from_branch"], comps[e["to_node"]["$component_ref"]]["name"]) for e in flows
                if e["from_node"]["$component_ref"] == route["id"]}  # fmt: skip
    assert branches == {("question", "answer"), ("complaint", "apologise"), ("default", "fallback")}
    data = {(comps[e["source_node"]["$component_ref"]]["name"], e["source_output"], comps[e["destination_node"]
             ["$component_ref"]]["name"], e["destination_input"]) for e in doc["data_flow_connections"]}  # fmt: skip
    assert ("start", "request", "classify", "request") in data
    assert ("classify", "category", "route", "branching_mapping_key") in data
    assert any("anthropic" in r for r in report)


def test_export_is_stable():
    wf = _template("ticket_triage")
    assert export_flow(wf)[0] == export_flow(wf)[0]


def test_export_models_and_remote_agents():
    wf = _template("chat_assistant").model_copy(deep=True)
    wf.resources[0].config.update(model="ollama:llama3.3", temperature=0.2)
    wf.nodes.append(wf.nodes[0].model_copy(update={
        "id": "n_remote", "type": "agent.a2a", "name": "remote",
        "config": {"url": "https://a.example/.well-known/agent-card.json", "output": "messages",
                   "headers": {"Authorization": {"$secret": "TOKEN"}}},
    }))  # fmt: skip
    doc, report = export_flow(wf)
    comps = _components(doc)
    ollama = next(c for c in comps.values() if c["component_type"] == "OllamaConfig")
    assert ollama["url"] == "http://localhost:11434" and ollama["default_generation_parameters"] == {"temperature": 0.2}
    remote = next(c for c in comps.values() if c["name"] == "remote")
    assert remote["agent"]["component_type"] == "A2AAgent"
    assert remote["agent"]["agent_url"] == "https://a.example/.well-known/agent-card.json"
    assert "TOKEN" not in json.dumps(remote["agent"])  # a secret reference stays in our metadata only
    assert any("headers are not in the Agent Spec" in r for r in report)


def test_typed_in_keys_never_reach_the_file():
    wf = _template("chat_assistant").model_copy(deep=True)
    wf.nodes.append(wf.nodes[0].model_copy(update={
        "id": "n_remote", "type": "agent.a2a", "name": "remote",
        "config": {"url": "https://a.example/a2a", "output": "messages",
                   "headers": {"Authorization": "Bearer sk-live-123", "X-Key": {"$secret": "KEY"}}},
    }))  # fmt: skip
    doc, report = export_flow(wf)
    text = json.dumps(doc)
    assert "sk-live-123" not in text and '"$secret": "KEY"' in text
    assert any("typed in directly are left out" in r for r in report)


@pytest.mark.parametrize(
    "doc",
    [
        {"component_type": "Flow", "nodes": ["x"]},
        {"component_type": "Flow", "nodes": [{"component_type": "LlmNode", "name": "a", "outputs": ["o"]}]},
        {"component_type": "Flow", "data_flow_connections": [{"source_node": "x", "destination_node": 3}]},
        {"component_type": "Agent", "name": "a", "metadata": {"agentcanvas": "oops"}},
        {"component_type": "Flow", "metadata": "oops"},
    ],
)
def test_a_malformed_file_is_refused_with_a_message(doc):
    try:
        ir, _, _ = import_flow(doc, wf_id="wf_new")
    except AgentSpecError as exc:
        assert str(exc)
    else:
        Workflow.model_validate(ir)  # or it is read as far as it makes sense


def test_import_a_flow_written_by_pyagentspec():
    ir, report, layout = import_flow(SAMPLE, wf_id="wf_new")
    wf = Workflow.model_validate(ir)
    assert layout == {}
    nodes = {n.name: n for n in wf.nodes}
    assert nodes["classify_request"].type == "core.llm"
    assert nodes["classify_request"].config == {
        "prompt": "One word, question or other: {{ state.request }}",
        "output": "category",
    }
    assert nodes["route"].config["cases"][0] == {"when": 'state.category == "question"', "route": "question"}
    assert nodes["answer"].type == "core.agent" and nodes["answer"].config == {"system_prompt": "Answer briefly."}
    assert nodes["billing_desk"].type == "agent.a2a" and nodes["billing_desk"].config["timeout"] == 90
    assert nodes["ask"].type == "core.human_input"
    assert nodes["say"].config == {"values": {"message": "Thanks: {{ state.details }}"}}
    models = {r.name: r.config for r in wf.resources}
    assert models == {"local_llama": {"model": "ollama:llama3.3"}, "gpt": {"model": "openai:gpt-5.5"}}
    wired = {(e.source.node, e.target.node) for e in wf.edges if e.kind == "wiring"}
    assert wired == {(wf.resources[0].id, nodes["classify_request"].id), (wf.resources[1].id, nodes["answer"].id)}
    ports = {e.source.port for e in wf.edges if e.source.node == nodes["route"].id}
    assert ports == {"route:question", "route:billing", "route:default"}
    io = {c.name: c.io for c in wf.state.channels}
    assert io["request"] == "in" and io["category"] == "out" and io["messages"] == "inout"
    assert any("works on the chat" in r for r in report)


@pytest.mark.parametrize("value", ['he said "hi"', "C:\\temp\\new", "two\nlines", "café ✓"])
def test_branch_values_with_quotes_come_in_and_go_out_unchanged(value):
    doc = json.loads(json.dumps(SAMPLE))
    route = next(c for c in _components(doc).values() if c["component_type"] == "BranchingNode")
    route["mapping"] = {value: "question", "billing": "billing"}
    wf = Workflow.model_validate(import_flow(doc, wf_id="wf_new")[0])
    assert [d.code for d in validate(wf) if d.severity == "error"] == []
    when = next(n for n in wf.nodes if n.type == "core.router").config["cases"][0]["when"]
    assert eval_condition(when, {"category": value}) and not eval_condition(when, {"category": "other"})
    out = next(c for c in _components(_strip(export_flow(wf)[0])).values() if c["component_type"] == "BranchingNode")
    assert out["mapping"] == {value: "question", "billing": "billing"}


def test_import_a_single_agent():
    agent = {"component_type": "Agent", "id": "a1", "name": "Helper", "system_prompt": "Hi",
             "llm_config": {"component_type": "VllmConfig", "id": "m1", "name": "served", "model_id": "qwen",
                            "url": "http://gpu:8000/v1"}}  # fmt: skip
    ir, _report, _ = import_flow(agent, wf_id="wf_new")
    wf = Workflow.model_validate(ir)
    assert [n.type for n in wf.nodes] == ["core.agent"]
    assert wf.resources[0].config == {"model": "vllm:qwen", "base_url": "http://gpu:8000/v1"}
    assert {(e.source.node, e.target.node) for e in wf.edges if e.kind == "flow"} == {
        ("START", wf.nodes[0].id),
        (wf.nodes[0].id, "END"),
    }


def test_import_steps_written_out_in_place_and_names_what_it_leaves_out():
    """A file may write a step out wherever it is used (the start node under `start_node`, again in `nodes` and in
    each edge) instead of referring to it: the copies are one step (X-9). The report names the tools it leaves out."""
    tool = {"component_type": "ServerTool", "id": "t1", "name": "lookup_order"}
    agent = {"component_type": "Agent", "id": "a1", "name": "helper", "system_prompt": "Hi", "tools": [tool]}
    start = {"component_type": "StartNode", "id": "s", "name": "start", "inputs": [{"title": "q", "type": "string"}]}
    step = {"component_type": "AgentNode", "id": "n", "name": "ask", "agent": agent}
    end = {"component_type": "EndNode", "id": "e", "name": "end", "outputs": []}

    def copy(x):
        return json.loads(json.dumps(x))

    doc = {
        "component_type": "Flow", "id": "f", "name": "Inline", "start_node": copy(start),
        "nodes": [copy(start), copy(step), copy(end)], "data_flow_connections": [],
        "control_flow_connections": [{"component_type": "ControlFlowEdge", "id": "c1", "from_node": copy(start),
                                      "to_node": copy(step)},
                                     {"component_type": "ControlFlowEdge", "id": "c2", "from_node": copy(step),
                                      "to_node": copy(end)}],
    }  # fmt: skip
    ir, report, _ = import_flow(doc, wf_id="wf_new")
    wf = Workflow.model_validate(ir)
    assert [n.type for n in wf.nodes] == ["core.agent"]
    assert {(e.source.node, e.target.node) for e in wf.edges if e.kind == "flow"} == {
        ("START", wf.nodes[0].id),
        (wf.nodes[0].id, "END"),
    }
    assert any("tools were not imported (lookup_order)" in r for r in report)


def test_import_unknown_steps_become_placeholders():
    doc = json.loads(json.dumps(SAMPLE))
    say = next(c for c in _components(doc).values() if c["name"] == "Say")
    say["component_type"] = "MapNode"
    ir, report, _ = import_flow(doc, wf_id="wf_new")
    Workflow.model_validate(ir)
    assert any("placeholder" in r and "MapNode" in r for r in report)


@pytest.mark.parametrize(
    ("doc", "message"),
    [
        ({"name": "x"}, "no component_type"),
        ({"component_type": "LlmNode", "id": "x"}, "cannot import a LlmNode"),
        ({"component_type": "Flow", "start_node": {"$component_ref": "nope"}}, "not defined"),
        (
            {
                "component_type": "Flow",
                "start_node": {"$component_ref": "a"},
                "$referenced_components": {"a": {"$component_ref": "b"}, "b": {"$component_ref": "a"}},
            },
            "circle",
        ),
    ],
)
def test_import_refuses_what_it_cannot_read(doc, message):
    with pytest.raises(AgentSpecError, match=message):
        import_flow(doc, wf_id="wf_new")


def test_import_keeps_only_sane_layout_and_names():
    doc, _ = export_flow(_template("chat_assistant"), {"n_agent": {"x": 1, "y": 2.5}, "bad": {"x": "1"}, "n": 3})
    doc["metadata"]["agentcanvas"]["ir"]["name"] = "x" * 500
    ir, _, layout = import_flow(doc, wf_id="wf_new")
    assert layout == {"n_agent": {"x": 1.0, "y": 2.5}}
    assert len(ir["name"]) == 200


def test_ident():
    taken: set[str] = set()
    assert [ident(t, taken) for t in ("Classify Request", "Classify Request", "End", "9 lives", "getHTTPData")] == [
        "classify_request", "classify_request_2", "end_2", "step_9_lives", "get_httpdata",
    ]  # fmt: skip


async def test_api_round_trip(app, api_client):
    from agentcanvas.testing import DemoResources

    wf = (await api_client.post(f"{P}/workflows", json={"name": "Triage", "template": "classify_and_route"})).json()
    await api_client.put(f"/v1/workflows/{wf['id']}/layout", json={"positions": {"n_route": {"x": 5, "y": 6}}})
    out = (await api_client.get(f"/v1/workflows/{wf['id']}/agent-spec")).json()
    assert out["spec"]["component_type"] == "Flow" and out["report"]

    res = await api_client.post(f"{P}/workflows/agent-spec", json={"spec": out["spec"], "name": "Copy"})
    assert res.status_code == 201, res.text
    copy = res.json()
    assert copy["name"] == "Copy" and copy["id"] != wf["id"]
    assert copy["ir"]["nodes"] == wf["ir"]["nodes"] and copy["layout"] == {"n_route": {"x": 5, "y": 6}}
    assert res.headers["Location"] == f"/v1/workflows/{copy['id']}"

    res = await api_client.post(f"{P}/workflows/agent-spec", json={"spec": SAMPLE})
    assert res.status_code == 201, res.text
    imported = res.json()
    assert imported["name"] == "Support desk" and imported["report"]
    assert not [d for d in imported["diagnostics"] if d["severity"] == "error"], imported["diagnostics"]

    demo = DemoResources()
    app.state.resources_factory = lambda thread_id, wf: demo
    text = (await api_client.post(f"/v1/workflows/{imported['id']}/runs/stream",
                                  json={"input": {"request": "question"}})).text  # fmt: skip
    last = [json.loads(ln[6:]) for ln in text.splitlines() if ln.startswith("data: ")][-1]
    assert last["type"] in ("RUN_FINISHED", "RUN_INTERRUPTED") or last.get("name") == "interrupt", last


async def test_api_refuses_a_bad_file(api_client):
    res = await api_client.post(f"{P}/workflows/agent-spec", json={"spec": {"component_type": "Tool", "id": "t"}})
    assert res.status_code == 422 and "cannot import a Tool" in res.text


def test_steps_that_start_side_by_side_export_one_after_another():
    """The agent network template starts three steps at once; Agent Spec's start node takes one outgoing edge (newer
    pyagentspec refuses the file otherwise), and its flows follow one path, so they are written in sequence."""
    import json

    from agentcanvas.agentspec import export_flow
    from agentcanvas.ir.models import Workflow
    from agentcanvas.services.workflows import TEMPLATES

    wf = Workflow.model_validate(json.loads((TEMPLATES / "agent_network.ir.json").read_text()))
    doc, report = export_flow(wf)
    refs = doc["$referenced_components"]
    start = doc["start_node"]["$component_ref"]
    edges = [refs[e["$component_ref"]] if "$component_ref" in e else e for e in doc["control_flow_connections"]]
    assert len([e for e in edges if e["from_node"]["$component_ref"] == start]) == 1
    assert any("one after another" in line for line in report)
