"""Sprint 8 templates (M6-T7): every template validates and runs end to end with fake models."""

from __future__ import annotations

import json

import pytest

from agentcanvas.ir.models import Workflow
from agentcanvas.services.workflows import GALLERY_CATEGORIES, TEMPLATES, template_gallery

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
    assert set(INPUTS) == set(NAMES)  # a new template needs a sample input here
    assert len(NAMES) >= 25  # S23: the template gallery


@pytest.mark.parametrize("name", NAMES)
def test_template_has_gallery_metadata(name):
    """S23: every template says where it goes in the gallery, and its sample input fits its input fields."""
    doc = json.loads((TEMPLATES / f"{name}.ir.json").read_text())
    g = template_gallery(doc)
    assert g.get("category") in GALLERY_CATEGORIES
    assert g.get("difficulty") in ("beginner", "intermediate")
    assert g.get("tags") and all(isinstance(t, str) and len(t) <= 24 for t in g["tags"])
    wf = Workflow.model_validate(doc)
    inputs = {c.name for c in wf.state.channels if c.io in ("in", "inout")}
    assert g.get("sample_input") and set(g["sample_input"]) <= inputs, (set(g["sample_input"]), inputs)
    assert 3 <= len(wf.nodes) + len(wf.resources) <= 9  # small enough to read at a glance


async def test_gallery_endpoint_and_created_workflows_drop_gallery_metadata(api_client):
    infos = {t["id"]: t for t in (await api_client.get("/v1/templates")).json()}
    faq = infos["faq_bot"]
    assert faq["category"] == "Customer support" and faq["difficulty"] == "beginner" and faq["tags"]
    assert faq["block_count"] == 3 and faq["node_count"] == 1 and faq["sample_input"]["messages"]
    assert infos["support_triage"]["category"] is None  # a template without gallery metadata still lists
    wf = (await api_client.post(f"{P}/workflows", json={"name": "FAQ", "template": "faq_bot"})).json()
    assert "gallery" not in wf["ir"].get("metadata", {})


@pytest.mark.parametrize("name", NAMES)
async def test_template_validates_and_runs(app, api_client, name):
    from agentcanvas.testing import DemoResources

    demo = DemoResources()
    app.state.resources_factory = lambda thread_id, wf: demo
    if name == "knowledge_qa":  # S24: searches the sample knowledge base, like a project that loaded it
        assert (await api_client.post(f"{P}/knowledge", json={"sample": "handbook"})).status_code == 201
        demo.knowledge = app.state.projects.knowledge.for_project("prj_default")
    wf = (await api_client.post(f"{P}/workflows", json={"name": name, "template": name})).json()
    assert not [d for d in wf["diagnostics"] if d["severity"] == "error"], wf["diagnostics"]
    text = (await api_client.post(f"/v1/workflows/{wf['id']}/runs/stream", json={"input": INPUTS[name]})).text
    last = [json.loads(ln[6:]) for ln in text.splitlines() if ln.startswith("data: ")][-1]
    assert last["type"] == "RUN_FINISHED", last
    code = (await api_client.get(f"/v1/workflows/{wf['id']}/code")).text
    assert "def build_graph(" in code
