"""Sprint 8 templates (M6-T7): every template validates and runs end to end with fake models."""

from __future__ import annotations

import json

import pytest

from agentcanvas.services.workflows import TEMPLATES

P = "/v1/projects/prj_default"
NAMES = sorted(p.name.removesuffix(".ir.json") for p in TEMPLATES.glob("*.ir.json"))
INPUTS = {
    "approval_workflow": {"brief": "New office opens Monday"},
    "chat_assistant": {"messages": [{"role": "user", "content": "hi"}]},
    "classify_and_route": {"request": "Where is my order?"},
    "data_extraction": {"messages": [{"role": "user", "content": "Invoice from ACME, total 120 EUR"}]},
    "rag_qa": {"question": "When are we open?"},
    "research_brief": {"question": "Energy mix", "topics": ["solar", "wind"]},
    "ticket_triage": {"ticket": "Site is down!"},
}


def test_the_catalogue_has_the_mvp_templates():
    assert {"ticket_triage", "research_brief", "rag_qa", "approval_workflow", "data_extraction"} <= set(NAMES)
    assert set(INPUTS) == set(NAMES)  # a new template needs a sample input here


@pytest.mark.parametrize("name", NAMES)
async def test_template_validates_and_runs(app, api_client, name):
    from agentcanvas.testing import DemoResources

    demo = DemoResources()
    app.state.resources_factory = lambda thread_id, wf: demo
    wf = (await api_client.post(f"{P}/workflows", json={"name": name, "template": name})).json()
    assert not [d for d in wf["diagnostics"] if d["severity"] == "error"], wf["diagnostics"]
    text = (await api_client.post(f"/v1/workflows/{wf['id']}/runs/stream", json={"input": INPUTS[name]})).text
    last = [json.loads(ln[6:]) for ln in text.splitlines() if ln.startswith("data: ")][-1]
    assert last["type"] == "RUN_FINISHED", last
    code = (await api_client.get(f"/v1/workflows/{wf['id']}/code")).text
    assert "def build_graph(" in code
