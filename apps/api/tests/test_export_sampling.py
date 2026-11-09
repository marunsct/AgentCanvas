"""Exported code leaves out the sampling settings a model refuses (W020).

A model the catalog marks `"sampling": false` (current Claude models, GPT-5 class models) answers a temperature with a
400, so every code generator writes `temperature` only for models that take it; `max_tokens` always goes through.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from agentcanvas import designs
from agentcanvas.adapters.agno import codegen as agno
from agentcanvas.adapters.crewai import codegen as crewai
from agentcanvas.adapters.google_adk import codegen as google_adk
from agentcanvas.adapters.mastra import codegen as mastra
from agentcanvas.adapters.openai_agents import codegen as openai_agents
from agentcanvas.ir.models import Workflow
from agentcanvas.sampling import refusing

REFUSES = "anthropic:claude-sonnet-5-5"
TAKES = "ollama:llama3.3"
GENERATORS: dict[str, Callable[[Workflow], str]] = {
    "crewai": crewai.generate, "google_adk": google_adk.generate, "mastra": mastra.generate,
    "openai_agents": openai_agents.generate, "agno": agno.generate,
}  # fmt: skip


def _workflow(model: str) -> Workflow:
    return Workflow.model_validate({
        "id": "wf_t", "name": "T",
        "state": {"channels": [{"name": "answer", "default": ""},
                               {"name": "messages", "type": {"type": "array"}, "reducer": "add_messages"}]},
        "resources": [{"id": "r_model", "type": "model.chat", "name": "model",
                       "config": {"model": model, "temperature": 0.3, "max_tokens": 321}}],
        "nodes": [{"id": "n_llm", "type": "core.llm", "name": "think", "config": {"prompt": "hi", "output": "answer"}},
                  {"id": "n_agent", "type": "core.agent", "name": "helper", "config": {"system_prompt": "Help."}}],
        "edges": [
            {"id": "e1", "kind": "flow", "source": {"node": "START"}, "target": {"node": "n_llm"}},
            {"id": "e2", "kind": "flow", "source": {"node": "n_llm"}, "target": {"node": "n_agent"}},
            {"id": "e3", "kind": "flow", "source": {"node": "n_agent"}, "target": {"node": "END"}},
            *({"id": f"w_{n}", "kind": "wiring", "source": {"node": "r_model"}, "target": {"node": n, "port": "model"}}
              for n in ("n_llm", "n_agent")),
        ],
    })  # fmt: skip


def test_the_catalog_has_both_kinds():
    assert REFUSES in refusing() and TAKES not in refusing()


@pytest.mark.parametrize("framework", list(GENERATORS))
def test_flows_write_temperature_only_for_models_that_take_it(framework):
    refused, taken = (GENERATORS[framework](_workflow(m)) for m in (REFUSES, TAKES))
    assert "temperature" not in refused and "321" in refused
    assert "temperature" in taken and "0.3" in taken


@pytest.mark.parametrize("framework", list(designs.AGENT_FRAMEWORKS))
def test_designed_agents_write_temperature_only_for_models_that_take_it(framework):
    for model, wanted in ((REFUSES, False), (TAKES, True)):
        wf = _workflow(model)
        source = designs.generate(framework, designs.design_for(wf, wf.nodes[1]))
        assert ("TEMPERATURE = 0.3" in source) is wanted and "MAX_TOKENS = 321" in source
