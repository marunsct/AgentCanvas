"""F-4: Decide with AI: a model picks one of the described paths, with its confidence and reason."""

from __future__ import annotations

import pytest
from test_flow_frameworks import installed
from test_runs_v1 import events, make

from agentcanvas.expr import decision
from agentcanvas.ir.models import Workflow
from agentcanvas.validation import validate

ROUTE = {"route": "billing", "description": "invoices and payments"}
FLOWS = ["pydantic_ai", "agno", "microsoft_agent_framework", "strands", "llamaindex"]
REFUSED = ["openai_agents", "google_adk", "crewai", "mastra"]


def decide_wf(**config) -> dict:
    cases = [ROUTE, {"route": "tech", "description": "bugs and outages"}]
    return {
        "id": "wf_d", "name": "triage", "framework": "langchain",
        "state": {"channels": [{"name": "ticket", "io": "in"}, {"name": "team", "io": "out"},
                               {"name": "decided", "io": "out", "type": {"type": "object"}}]},
        "resources": [{"id": "r_m", "type": "model.chat", "name": "model", "config": {"model": "openai:gpt-5.5"}}],
        "nodes": [
            {"id": "n_d", "type": "core.decide", "name": "route_ticket",
             "config": {"prompt": "Which team? {{ state.ticket }}", "cases": cases, "default": "other",
                        "output": "decided", **config}},
            *({"id": f"n_{t}", "type": "core.set", "name": t, "config": {"values": {"team": t}}}
              for t in ("billing", "tech", "other")),
        ],
        "edges": [
            {"id": "e0", "kind": "flow", "source": {"node": "START"}, "target": {"node": "n_d"}},
            *({"id": f"e_{t}", "kind": "flow", "source": {"node": "n_d", "port": f"route:{t}"},
               "target": {"node": f"n_{t}"}} for t in ("billing", "tech", "other")),
            *({"id": f"x_{t}", "kind": "flow", "source": {"node": f"n_{t}"}, "target": {"node": "END"}}
              for t in ("billing", "tech", "other")),
            {"id": "w1", "kind": "wiring", "source": {"node": "r_m"}, "target": {"node": "n_d", "port": "model"}},
        ],
    }  # fmt: skip


async def _run(api, wf_id: str, reply: str) -> dict:
    body = {"input": {"ticket": "I was charged twice"}, "mode": "debug", "mocks": {"model": [reply]}}
    evs = events((await api.post(f"/v1/workflows/{wf_id}/runs/stream", json=body)).text)
    assert evs[-1]["type"] == "RUN_FINISHED", evs[-1]
    return evs[-1]["output"]


@pytest.mark.parametrize(
    ("text", "made"),
    [
        ('{"choice": "billing", "confidence": 0.9, "reason": "a charge"}', ("billing", 0.9)),
        ('Sure! ```json\n{"choice": "tech", "confidence": "0.4"}\n```', ("tech", 0.4)),
        ('{"choice": "sales", "confidence": 1}', (None, 1.0)),  # not one of the choices
        ('{"choice": "billing", "confidence": 7}', ("billing", 1.0)),  # clipped to 0-1
        ("billing, I think", (None, None)),  # no JSON
        ('{"choice": "billing", "confidence": "very"}', ("billing", None)),
    ],
)
def test_the_decision_in_a_model_answer(text, made):
    found = decision(text, ["billing", "tech"])
    assert (found["choice"], found["confidence"]) == made


async def test_the_model_picks_the_path_and_the_decision_is_saved(api_client):
    wf = await make(api_client, decide_wf())
    out = await _run(api_client, wf, '{"choice": "billing", "confidence": 0.92, "reason": "it is about a charge"}')
    assert out["team"] == "billing"
    assert out["decided"] == {"choice": "billing", "confidence": 0.92, "reason": "it is about a charge"}


async def test_an_answer_that_is_not_a_choice_takes_the_default_path(api_client):
    wf = await make(api_client, decide_wf())
    assert (await _run(api_client, wf, "I would say billing"))["team"] == "other"


async def test_a_decision_below_the_minimum_confidence_takes_the_default_path(api_client):
    wf = await make(api_client, decide_wf(min_confidence=0.7))
    assert (await _run(api_client, wf, '{"choice": "tech", "confidence": 0.55}'))["team"] == "other"
    assert (await _run(api_client, wf, '{"choice": "tech", "confidence": 0.8}'))["team"] == "tech"


def test_its_paths_are_outputs_and_other_frameworks_refuse_it():
    wf = Workflow.model_validate(decide_wf())
    assert not [d for d in validate(wf) if d.severity == "error"]
    loose = decide_wf()
    loose["edges"] = [e for e in loose["edges"] if e["id"] != "e_tech"]
    assert any(d.code for d in validate(Workflow.model_validate(loose)) if d.severity == "error")  # a path not wired
    for framework in REFUSED:
        elsewhere = Workflow.model_validate({**decide_wf(), "framework": framework})
        assert any(d.code == "P001" and "core.decide" in d.message for d in validate(elsewhere)), framework


@pytest.mark.parametrize(
    ("reply", "team"),
    [('{"choice": "tech", "confidence": 0.9, "reason": "an outage"}', "tech"), ("no idea", "other")],
)
async def test_the_exported_code_decides_as_the_canvas_does(tmp_path, reply, team):
    """The LangGraph export writes the same step: interpreted and exported runs take the same path."""
    from langgraph.checkpoint.memory import InMemorySaver
    from test_run import load_generated

    from agentcanvas.adapters.langchain.codegen import generate
    from agentcanvas.adapters.langchain.interpreter import build_graph
    from agentcanvas.adapters.langchain.nodes import Resources
    from agentcanvas.testing import scripted

    wf = Workflow.model_validate(decide_wf())
    mod = load_generated(generate(wf), tmp_path)

    def res():
        return Resources(overrides={"model": scripted(reply)})

    cfg = {"configurable": {"thread_id": "t"}}
    interpreted = await build_graph(wf, res(), checkpointer=InMemorySaver()).ainvoke({"ticket": "down"}, cfg)
    exported = await mod.build_graph(res(), checkpointer=InMemorySaver()).ainvoke({"ticket": "down"}, cfg)
    assert interpreted == exported and interpreted["team"] == team


@pytest.mark.parametrize(
    ("framework", "reply", "minimum", "team"),
    [
        pytest.param(f, reply, minimum, team, marks=installed(f))
        for f in FLOWS
        for reply, minimum, team in [
            ('{"choice": "tech", "confidence": 0.9, "reason": "an outage"}', None, "tech"),
            ('{"choice": "tech", "confidence": 0.5}', 0.7, "other"),
            ("I would say billing", None, "other"),
        ]
    ],
)
async def test_flow_frameworks_decide_as_langgraph_does(framework, reply, minimum, team):
    from test_flow_frameworks import _run as run_flow

    from agentcanvas.adapters import create_adapter

    config = {} if minimum is None else {"min_confidence": minimum}
    wf = Workflow.model_validate({**decide_wf(**config), "framework": framework})
    assert not [d for d in validate(wf) if d.severity == "error"]
    adapter = create_adapter(framework)
    async with adapter.persistence(None):
        (events,) = await run_flow(adapter, wf, {"ticket": "the site is down"}, {"model": [reply]})
    assert events[-1].get("outcome") == "success", events[-3:]
    assert events[-1]["output"]["team"] == team
    assert events[-1]["output"]["decided"]["choice"] == ("tech" if "tech" in reply else None)


def test_the_generated_flow_reads_like_the_canvas():
    from agentcanvas.adapters.pydantic_ai import codegen

    source = codegen.generate(Workflow.model_validate({**decide_wf(min_confidence=0.6), "framework": "pydantic_ai"}))
    assert 'ROUTE_TICKET_CHOICES = {"billing": "invoices and payments", "tech": "bugs and outages"}' in source
    assert "run.decide_prompt(rt.render(ROUTE_TICKET_PROMPT, state), ROUTE_TICKET_CHOICES)" in source
    assert 'decides="decided", min_confidence=0.6, default="other"' in source
    assert '"n_d": {"billing": ["n_billing"], "tech": ["n_tech"], "other": ["n_other"]}' in source
