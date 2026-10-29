"""Keys typed in as plain text never leave in an export (X-1).

A header or environment value that looks like a key (`Authorization: Bearer …` typed in instead of a project
secret) is a warning on the canvas (W021) and every export refuses it, naming the block and the header; secret
references and ordinary headers go through.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Any

import pytest

from agentcanvas import bridges, designs
from agentcanvas.adapters import flowpackager
from agentcanvas.adapters.crewai import packager as crewai
from agentcanvas.adapters.google_adk import packager as google_adk
from agentcanvas.adapters.langchain import packager as langgraph
from agentcanvas.adapters.mastra import packager as mastra
from agentcanvas.adapters.openai_agents import packager as openai_agents
from agentcanvas.designs.export import all_export_problems, export_files
from agentcanvas.errors import InvalidInput
from agentcanvas.ir.models import Workflow
from agentcanvas.validation import validate
from agentcanvas.validation.analysis import secrets_in_plain_text

KEY = "Bearer abc123-not-a-pattern"


def _workflow(headers: dict[str, Any], *, subgraph: bool = False) -> Workflow:
    http = {"id": "r_crm", "type": "tool.http", "name": "crm",
            "config": {"description": "Looks up a customer", "url": "https://crm.example.com/c",
                       "allowed_domains": ["crm.example.com"], "headers": headers}}  # fmt: skip
    model = {"id": "r_model", "type": "model.chat", "name": "model", "config": {"model": "openai:gpt-5.5"}}
    agent = {"id": "n_agent", "type": "core.agent", "name": "helper", "config": {"system_prompt": "Help."}}
    edges = [
        {"id": "e_in", "kind": "flow", "source": {"node": "START"}, "target": {"node": "n_agent"}},
        {"id": "e_out", "kind": "flow", "source": {"node": "n_agent"}, "target": {"node": "END"}},
        {"id": "w_m", "kind": "wiring", "source": {"node": "r_model"}, "target": {"node": "n_agent", "port": "model"}},
        {"id": "w_t", "kind": "wiring", "source": {"node": "r_crm"}, "target": {"node": "n_agent", "port": "tools"}},
    ]
    channels = [{"name": "messages", "type": {"type": "array"}, "reducer": "add_messages"}]
    inner = {"state": {"channels": channels}, "resources": [model, http], "nodes": [agent], "edges": edges}
    if subgraph:
        inner = {
            "state": {"channels": channels},
            "nodes": [{"id": "n_sub", "type": "core.subgraph", "name": "desk",
                       "config": {"workflow": inner, "inputs": ["messages"], "outputs": ["messages"]}}],
            "edges": [{"id": "e_in", "kind": "flow", "source": {"node": "START"}, "target": {"node": "n_sub"}},
                      {"id": "e_out", "kind": "flow", "source": {"node": "n_sub"}, "target": {"node": "END"}}],
        }  # fmt: skip
    return Workflow.model_validate({"id": "wf_keys", "name": "Keys", **inner})


PACKAGERS: dict[str, Callable[[Workflow], object]] = {
    "langgraph": partial(langgraph.package, source=""),
    "openai_agents": partial(openai_agents.package, source=""),
    "crewai": partial(crewai.package, source=""),
    "google_adk": partial(google_adk.package, source=""),
    "mastra": partial(mastra.package, source=""),
    **{f"flow:{fw}": partial(flowpackager.package, source="", framework=fw) for fw in flowpackager.TARGETS},
    **{f"bridge:{t}": partial(bridges.export_files, target=t) for t in bridges.TARGETS},
}


@pytest.mark.parametrize("name", list(PACKAGERS))
def test_every_export_refuses_a_key_typed_in(name: str) -> None:
    with pytest.raises(InvalidInput, match=r"'crm' \(header Authorization\)"):
        PACKAGERS[name](_workflow({"Authorization": KEY}))


@pytest.mark.parametrize("framework", list(designs.AGENT_FRAMEWORKS))
def test_a_single_agent_export_refuses_a_key_typed_in(framework: str) -> None:
    wf = _workflow({"X-Api-Key": "abc"})
    design = designs.design_for(wf, wf.nodes[0])
    assert any("'crm' has the header X-Api-Key typed in" in p for p in all_export_problems(framework, design))
    with pytest.raises(InvalidInput, match="X-Api-Key typed in as plain text"):
        export_files(wf, design, framework)


def test_what_counts_as_a_key() -> None:
    headers = {"Authorization": {"$secret": "CRM_TOKEN"}, "Accept": "application/json", "X-Trace": "",
               "X-Session-Id": "s1", "X-Anything": "token abc", "X-Other": "sk-" + "a" * 24}  # fmt: skip
    assert secrets_in_plain_text(_workflow(headers)) == [
        ("crm", "headers", "X-Session-Id"), ("crm", "headers", "X-Anything"), ("crm", "headers", "X-Other"),
    ]  # fmt: skip
    assert secrets_in_plain_text(_workflow({"Authorization": KEY}, subgraph=True)) == [
        ("desk / crm", "headers", "Authorization")
    ]
    langgraph.package(_workflow({"Authorization": {"$secret": "CRM_TOKEN"}, "Accept": "application/json"}), "")


def test_the_canvas_warns() -> None:
    found = [d for d in validate(_workflow({"Authorization": KEY})) if d.code == "W021"]
    assert len(found) == 1 and found[0].severity == "warning" and found[0].node_id == "r_crm"
    assert "Authorization" in found[0].message and found[0].path == "config.headers.Authorization"
