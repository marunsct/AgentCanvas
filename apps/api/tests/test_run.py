import importlib.util
import json
import pathlib
import sys

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agentcanvas.adapters.langchain.codegen import generate
from agentcanvas.adapters.langchain.interpreter import build_graph
from agentcanvas.adapters.langchain.nodes import Resources
from agentcanvas.ir.models import NodePolicies, RetryPolicySpec, Workflow
from agentcanvas.testing import scripted, tool_call


def fakes(urgency: str):
    return Resources(
        overrides={
            "fast_model": scripted(urgency),
            "smart_model": scripted(tool_call("send_reply", {"text": "We are on it"}), "Reply sent."),
        }
    )


async def run_low(graph):
    cfg = {"configurable": {"thread_id": "t-low"}}
    await graph.ainvoke({"ticket": "Where is my invoice?"}, cfg)
    state = await graph.aget_state(cfg)
    assert state.next == ("reply_drafter",)  # paused for tool approval
    assert state.interrupts[0].value["action_requests"][0]["name"] == "send_reply"
    final = await graph.ainvoke(Command(resume={"decisions": [{"type": "approve"}]}), cfg)
    return {"urgency": final["urgency"], "last": final["messages"][-1].content}


async def run_high(graph):
    cfg = {"configurable": {"thread_id": "t-high"}}
    await graph.ainvoke({"ticket": "Site is down!"}, cfg)
    state = await graph.aget_state(cfg)
    assert state.interrupts[0].value["canvas_node_id"] == "n_ok"
    final = await graph.ainvoke(Command(resume={"approved": True}), cfg)
    return {"urgency": final["urgency"], "summary": final["summary"], "approved": final["approved"]}


@pytest.mark.asyncio
async def test_interpreter_low_path(triage):
    g = build_graph(triage, fakes("low"), checkpointer=InMemorySaver())
    assert await run_low(g) == {"urgency": "low", "last": "Reply sent."}


@pytest.mark.asyncio
async def test_interpreter_high_path(triage):
    g = build_graph(triage, fakes("high"), checkpointer=InMemorySaver())
    out = await run_high(g)
    assert out == {"urgency": "high", "summary": "ESCALATED: Site is down!", "approved": True}


def load_generated(source: str, tmp_path):
    path = tmp_path / "generated_graph.py"
    path.write_text(source)
    spec = importlib.util.spec_from_file_location("generated_graph", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["generated_graph"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.asyncio
@pytest.mark.parametrize("urgency", ["low", "high"])
async def test_generated_code_matches_interpreter(triage, tmp_path, urgency):
    """The WYSIWYG guarantee: interpreted and compiled modes behave identically."""
    mod = load_generated(generate(triage), tmp_path)
    run = run_low if urgency == "low" else run_high
    interpreted = await run(build_graph(triage, fakes(urgency), checkpointer=InMemorySaver()))
    compiled = await run(mod.build_graph(fakes(urgency), checkpointer=InMemorySaver()))
    assert interpreted == compiled


def test_generation_is_deterministic(triage):
    assert generate(triage) == generate(triage)


def test_optional_config_keys_can_be_omitted(triage):
    """Regression: templates must not require optional keys (StrictUndefined)."""
    triage.require_node("n_ag").config = {}  # no system_prompt, no approve_tools
    triage.require_node("n_rt").config.pop("default")  # router falls back to route "default"
    for e in triage.edges:
        if e.id == "e4":
            e.source.port = "route:default"
    code = generate(triage)
    assert "approve_tools=None" in code and 'default="default"' in code


# ---- equivalence beyond the triage example: routers, policies, bypass, output schema (TR-COMP-40) ----
ROUTE_TEMPLATE = pathlib.Path(__file__).parents[1] / "agentcanvas/registry/templates/classify_and_route.ir.json"


def route_workflow() -> Workflow:
    wf = Workflow.model_validate(json.loads(ROUTE_TEMPLATE.read_text()))
    wf.require_node("n_other").disabled = True  # bypassed node must behave the same in both modes
    wf.settings.node_defaults = NodePolicies(retry=RetryPolicySpec(max_attempts=2))
    return wf


async def run_route(graph, text: str, thread: str) -> dict:
    return await graph.ainvoke({"request": text}, {"configurable": {"thread_id": thread}})


@pytest.mark.asyncio
@pytest.mark.parametrize("category", ["question", "complaint", "other"])
async def test_router_template_equivalence(tmp_path, category):
    wf = route_workflow()
    mod = load_generated(generate(wf), tmp_path)

    def res():
        return Resources(overrides={"fast_model": scripted(category, "An answer.")})

    interpreted = await run_route(build_graph(wf, res(), checkpointer=InMemorySaver()), "hello", "a")
    compiled = await run_route(mod.build_graph(res(), checkpointer=InMemorySaver()), "hello", "a")
    assert interpreted == compiled
    assert set(interpreted) <= {"category", "reply"}  # output schema = settings.output_channels
