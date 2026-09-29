"""Sprint 6 subgraphs (M2-T13): converting steps into a subgraph keeps the behaviour, in both execution modes."""

from __future__ import annotations

import json

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from test_catalog import MESSAGES, cfg, ch, compiled, errors, workflow

from agentcanvas.adapters.langchain.codegen import generate
from agentcanvas.adapters.langchain.interpreter import build_graph
from agentcanvas.adapters.langchain.nodes import Resources
from agentcanvas.errors import InvalidInput
from agentcanvas.ir.commands import apply_batch
from agentcanvas.services.refactor import inline_subgraph, to_subgraph
from agentcanvas.testing import DemoChatModel, scripted


def chain_wf():
    """START → greet → ask → shout → tally → END, with an append channel written inside and outside."""
    return workflow(
        channels=[ch("name"), ch("greeting"), ch("answer"), ch("loud"), ch("log", "array", "append"), MESSAGES],
        resources=[("model", "model.chat", {"model": "fake:scripted"})],
        nodes=[
            ("greet", "core.set", {"values": {"greeting": "Hello {{ state.name }}", "log": "greet"}}),
            ("ask", "core.llm", {"prompt": "{{ state.greeting }}, how are you?", "output": "answer"}),
            ("shout", "core.set", {"values": {"loud": "{{ state.answer | upper }}", "log": "shout"}}),
            ("tally", "core.set", {"values": {"log": "{{ state.log | length }} entries before tally"}}),
        ],
        flow=[("START", "greet"), ("greet", "ask"), ("ask", "shout"), ("shout", "tally"), ("tally", "END")],
        wiring=[("model", "ask", "model")],
    )


def converted(wf, ids, name=None):
    out, _ = apply_batch(wf, to_subgraph(wf, ids, name))
    return out


async def run_chain(graph):
    out = await graph.ainvoke({"name": "Ada", "log": ["start"]}, cfg())
    return {k: out.get(k) for k in ("greeting", "answer", "loud", "log")}


def demo():
    return Resources(overrides={"model": DemoChatModel()})


@pytest.mark.asyncio
async def test_three_steps_as_a_subgraph_behave_identically(tmp_path):
    """M2-T13 AC: a 3-node group converted to a subgraph gives the same result, interpreted and generated."""
    original = chain_wf()
    sub = converted(original, ["greet", "ask", "shout"], "Greeting")
    assert not errors(sub), [d for d in errors(sub)]
    node = next(n for n in sub.nodes if n.type == "core.subgraph")
    assert node.config["inputs"] == ["answer", "greeting", "name"]  # read inside; `log` is only written there
    assert node.config["outputs"] == ["answer", "greeting", "log", "loud"]
    assert {n.id for n in sub.nodes} == {node.id, "tally"} and sub.resources == []  # the model moved inside

    before = await run_chain(build_graph(original, demo(), checkpointer=InMemorySaver()))
    after = await run_chain(build_graph(sub, demo(), checkpointer=InMemorySaver()))
    generated = await run_chain(compiled(sub, tmp_path).build_graph(demo(), checkpointer=InMemorySaver()))
    assert before == after == generated
    assert before["log"] == ["start", "greet", "shout", "3 entries before tally"]  # append: no duplicates
    assert "def _build_greeting(" in generate(sub)


@pytest.mark.asyncio
async def test_approval_inside_a_subgraph_pauses_and_resumes_the_run(triage, tmp_path):
    sub = converted(triage, ["n_esc", "n_ok"], "Escalation")
    assert not errors(sub)

    def res():
        return Resources(overrides={"fast_model": scripted("high"), "smart_model": scripted("unused")})

    async def run(graph):
        await graph.ainvoke({"ticket": "Site is down!"}, cfg())
        state = await graph.aget_state(cfg(), subgraphs=True)
        pending = state.interrupts[0].value
        final = await graph.ainvoke(Command(resume={"approved": True}), cfg())
        return pending["canvas_node_id"], final["summary"], final["approved"]

    a = await run(build_graph(sub, res(), checkpointer=InMemorySaver()))
    b = await run(compiled(sub, tmp_path).build_graph(res(), checkpointer=InMemorySaver()))
    assert a == b == ("n_ok", "ESCALATED: Site is down!", True)


def test_inline_restores_the_original_steps():
    original = chain_wf()
    sub = converted(original, ["greet", "ask", "shout"])
    node = next(n for n in sub.nodes if n.type == "core.subgraph")
    back, _ = apply_batch(sub, inline_subgraph(sub, node.id))
    assert not errors(back)
    assert {n.id for n in back.nodes} == {n.id for n in original.nodes}
    flow = {(e.source.node, e.target.node) for e in back.flow_edges()}
    assert flow == {(e.source.node, e.target.node) for e in original.flow_edges()}
    assert [r.name for r in back.resources] == ["model"]


def test_groups_that_cannot_become_one_step(triage):
    with pytest.raises(InvalidInput, match="one way in"):
        to_subgraph(triage, ["n_esc", "n_ag"])  # entered at two steps (two router routes)
    with pytest.raises(InvalidInput, match="one way out"):
        to_subgraph(triage, ["n_cls", "n_rt"])  # leaves through router routes
    bad = chain_wf()
    bad.require_node("ask").config = {"prompt": "x", "output": "answer"}
    sub = converted(bad, ["greet", "ask"])
    node = next(n for n in sub.nodes if n.type == "core.subgraph")
    node.config["outputs"] = [*node.config["outputs"], "nowhere"]
    assert "E021" in errors(sub)
    node.config["workflow"]["nodes"][0]["type"] = "core.nope"
    assert "E018" in errors(sub)


@pytest.mark.asyncio
async def test_api_refactor_and_nested_activity(app, api_client):
    from agentcanvas.testing import DemoResources

    demo_res = DemoResources()
    app.state.resources_factory = lambda thread_id, wf: demo_res
    wf = (
        await api_client.post("/v1/projects/prj_default/workflows", json={"name": "t", "template": "support_triage"})
    ).json()
    r = await api_client.post(f"/v1/workflows/{wf['id']}/refactor/subgraph", json={"node_ids": ["n_esc", "n_ok"]})
    assert r.status_code == 200, r.text
    cmds = r.json()["commands"]
    assert cmds[0]["op"] == "AddNode" and cmds[0]["node"]["type"] == "core.subgraph"
    r = await api_client.post(
        f"/v1/workflows/{wf['id']}/commands", json={"commands": cmds}, headers={"If-Match": str(wf["version"])}
    )
    assert r.status_code == 200 and not [d for d in r.json()["diagnostics"] if d["severity"] == "error"]
    ir = r.json()["ir"]
    router = next(n for n in ir["nodes"] if n["id"] == "n_rt")
    router["config"]["cases"][0]["when"] = "true"  # always escalate
    await api_client.put(f"/v1/workflows/{wf['id']}", json={"ir": ir}, headers={"If-Match": str(r.json()["version"])})
    body = {"input": {"ticket": "down"}, "thread_id": "th_sub"}
    text = (await api_client.post(f"/v1/workflows/{wf['id']}/runs/stream", json=body)).text
    evs = [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]
    started = [e["canvas_node_id"] for e in evs if e["type"] == "STEP_STARTED"]
    assert "n_esc" in started and "n_ok" in started  # inner steps light up (drill-in view)
    assert evs[-1]["interrupts"][0]["canvas_node_id"] == "n_ok"
    bad = await api_client.post(f"/v1/workflows/{wf['id']}/refactor/subgraph", json={"node_ids": ["n_cls", "n_rt"]})
    assert bad.status_code == 422
