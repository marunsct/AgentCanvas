"""Sprint 8 templates (M6-T7): every template validates and runs end to end with fake models."""

from __future__ import annotations

import json

import pytest

from agentcanvas import designs
from agentcanvas.ir.models import Workflow
from agentcanvas.services.workflows import GALLERY_CATEGORIES, TEMPLATES, template_gallery

P = "/v1/projects/prj_default"
NAMES = sorted(p.name.removesuffix(".ir.json") for p in TEMPLATES.glob("*.ir.json"))
INPUTS = {
    "mixed_team": {"messages": [{"role": "user", "content": "Honey bees in cities"}]},
    "approval_workflow": {"brief": "New office opens Monday"},
    "chat_assistant": {"messages": [{"role": "user", "content": "hi"}]},
    "classify_and_route": {"request": "Where is my order?"},
    "data_extraction": {"messages": [{"role": "user", "content": "Invoice from ACME, total 120 EUR"}]},
    "rag_qa": {"question": "When are we open?"},
    "research_brief": {"question": "Energy mix", "topics": ["solar", "wind"]},
    "ticket_triage": {"ticket": "Site is down!"},
    # S23 gallery
    "blog_writer": {"topic": "Automating weekly reports", "audience": "operations managers"},
    "code_review": {"diff": "-return items[0]\n+return items[0] if items else None", "change_goal": "No crash"},
    "document_qa": {"document": "Cancel with 30 days' notice.", "question": "How do I cancel?"},
    "email_reply": {"email": "My order arrived damaged.", "tone": "friendly"},
    "faq_bot": {"messages": [{"role": "user", "content": "Can I pause my subscription?"}]},
    "feedback_triage": {"feedback": "Support never answered my email."},
    "incident_summary": {"incident_log": "14:02 checkout errors\n14:11 back to normal"},
    "lead_qualification": {"lead": "Ops lead at Northwind, budget approved, need it in 6 weeks."},
    "meeting_notes": {"transcript": "Ben: I'll finish the onboarding emails by Friday."},
    "onboarding_buddy": {"messages": [{"role": "user", "content": "When do I get paid? ana@example.com"}]},
    "policy_checker": {"policy": "Always include an unsubscribe link.", "document": "Spring sale! Shop now."},
    "product_description": {"product_facts": "Trail Runner 2 backpack, 18 litres, $89"},
    "resume_screening": {"job_description": "Python backend engineer", "resume": "5 years of Python APIs"},
    "social_posts": {"announcement": "Our new mobile app is out today."},
    "sql_helper": {"table_schema": "orders(id, total)", "question": "Total revenue?", "dialect": "PostgreSQL"},
    "translation_glossary": {"text": "Start your free trial.", "target_language": "German", "glossary": "trial = Test"},
    "trip_planner": {"trip_request": "3 days in Lisbon in May, 600 EUR"},
    "web_research": {"question": "How was Hubble's mirror fixed?"},
    # S24
    "knowledge_qa": {"question": "How many vacation days do I get?"},
}


def test_the_catalogue_has_the_mvp_templates():
    assert {"ticket_triage", "research_brief", "rag_qa", "approval_workflow", "data_extraction"} <= set(NAMES)
    assert set(INPUTS) <= set(NAMES)  # a template without an input here runs with its gallery sample input
    assert len(NAMES) >= 25  # S23: the template gallery


def demo_resources(app, **kw):
    """Demo models with the project's workspace (a template kit's code runs from it) and registry (its custom
    blocks are known)."""
    from agentcanvas.testing import DemoResources

    return DemoResources(
        workspace=app.state.projects.workspace("prj_default").root,
        registry=lambda: app.state.projects.registry("prj_default"),
        **kw,
    )


def template_input(name: str) -> dict:
    """The run input of a template: its entry here, else its gallery sample input."""
    return _input(name)


def _input(name: str) -> dict:
    return INPUTS.get(name) or template_gallery(json.loads((TEMPLATES / f"{name}.ir.json").read_text()))["sample_input"]


@pytest.mark.parametrize("name", NAMES)
def test_template_has_gallery_metadata(name):
    """S23: every template says where it goes in the gallery, and its sample input fits its input fields."""
    doc = json.loads((TEMPLATES / f"{name}.ir.json").read_text())
    g = template_gallery(doc)
    assert g.get("category") in GALLERY_CATEGORIES
    assert g.get("difficulty") in ("beginner", "intermediate", "advanced")
    assert g.get("tags") and all(isinstance(t, str) and len(t) <= 24 for t in g["tags"])
    wf = Workflow.model_validate(doc)
    inputs = {c.name for c in wf.state.channels if c.io in ("in", "inout")}
    assert g.get("sample_input") and set(g["sample_input"]) <= inputs, (set(g["sample_input"]), inputs)
    blocks = len(wf.nodes) + len(wf.resources)
    assert 3 <= blocks <= (18 if g["difficulty"] == "advanced" else 9)  # small enough to read at a glance


async def test_gallery_endpoint_and_created_workflows_drop_gallery_metadata(api_client):
    infos = {t["id"]: t for t in (await api_client.get("/v1/templates")).json()}
    faq = infos["faq_bot"]
    assert faq["category"] == "Customer support" and faq["difficulty"] == "beginner" and faq["tags"]
    assert faq["block_count"] == 3 and faq["node_count"] == 1 and faq["sample_input"]["messages"]
    assert infos["support_triage"]["category"] is None  # a template without gallery metadata still lists
    wf = (await api_client.post(f"{P}/workflows", json={"name": "FAQ", "template": "faq_bot"})).json()
    assert "gallery" not in wf["ir"].get("metadata", {})


def _agent_network(monkeypatch) -> dict:
    """agent_network's remote agents in process: the platform agent (A2A) behind a mock transport, the log analyst
    and the issue tracker (MCP) as FastMCP servers, and the kit's OpenAI Agents SDK agent without its own model."""
    from types import SimpleNamespace

    import agents
    import httpx
    from fastmcp import FastMCP

    def platform(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":  # its Agent Card
            return httpx.Response(200, json={"name": "Platform", "url": "https://agents.example.com/platform/a2a"})
        said = {"kind": "message", "role": "agent", "parts": [{"kind": "text", "text": "Clusters are healthy."}]}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": said})

    logs, tracker = FastMCP("log analyst"), FastMCP("issue tracker")
    logs.tool(lambda question: "502s from pods restarting since 14:02", name="investigate")
    tracker.tool(lambda query: "No open issue matches.", name="search_issues")
    tracker.tool(lambda title, body: "Opened issue #481", name="create_issue")

    async def run(agent, input, **kw):
        return SimpleNamespace(final_output=f"{agent.name}: v2.14.0 deployed at 13:55 UTC")

    monkeypatch.setattr(agents.Runner, "run", run)
    return {
        "http_transport": httpx.MockTransport(platform),
        "overrides": {"log_analyst": logs, "issue_tracker": tracker},  # by the resource's name
        "secrets": {"PLATFORM_AGENT_KEY": "Bearer test"},
    }


@pytest.mark.parametrize("name", NAMES)
async def test_template_validates_and_runs(app, api_client, name, monkeypatch):
    from agentcanvas.adapters.crewai import crewai_python

    if name == "mixed_team" and designs.part_python("crewai") is None and not designs.importable("crewai"):
        pytest.skip("its CrewAI writer needs CrewAI's environment (make crewai)")
    demo = demo_resources(app, **(_agent_network(monkeypatch) if name == "agent_network" else {}))
    app.state.resources_factory = lambda thread_id, wf: demo
    if name == "knowledge_qa":  # S24: searches the sample knowledge base, like a project that loaded it
        assert (await api_client.post(f"{P}/knowledge", json={"sample": "handbook"})).status_code == 201
        demo.knowledge = app.state.projects.knowledge.for_project("prj_default")
    wf = (await api_client.post(f"{P}/workflows", json={"name": name, "template": name})).json()
    assert not [d for d in wf["diagnostics"] if d["severity"] == "error"], wf["diagnostics"]
    crewai = wf["ir"].get("framework") == "crewai"
    code = (await api_client.get(f"/v1/workflows/{wf['id']}/code")).text
    assert ("(Flow[dict]):" if crewai else "def build_graph(") in code  # a CrewAI workflow exports as a flow
    if crewai and crewai_python() is None:
        pytest.skip("it runs on CrewAI, which needs CrewAI's environment (make crewai); validated and exported")
    text = (await api_client.post(f"/v1/workflows/{wf['id']}/runs/stream", json={"input": _input(name)})).text
    last = [json.loads(ln[6:]) for ln in text.splitlines() if ln.startswith("data: ")][-1]
    assert last["type"] == "RUN_FINISHED", last
