"""A template's kit: what it brings beside its workflow (workspace code, a dataset with evaluators, saved tests, a
schedule), added once when the workflow is made from it, never over the person's own files."""

from __future__ import annotations

import json

import pytest

from agentcanvas.services import workflows as wf_service

P = "/v1/projects/prj_default"
HANDLER = """from agentcanvas.sdk import on


@on("kit.demo.arrived")
def start(event, act):
    act.start_workflow("{{workflow_id}}", input={"brief": event.data.get("text", "")})
"""
NODE = """from agentcanvas.sdk import canvas_node


@canvas_node(name="kitdemo.shout", label="Shout", writes=["brief"])
def shout(state: dict, cfg: dict) -> dict:
    return {"brief": str(state.get("brief", "")).upper()}
"""


@pytest.fixture
def kit_template(tmp_path, monkeypatch):
    doc = json.loads((wf_service.TEMPLATES / "approval_workflow.ir.json").read_text())
    doc["metadata"]["kit"] = {
        "files": {"events/{{workflow_key}}_arrived.py": HANDLER, "nodes/kitdemo.py": NODE},
        "dataset": {"name": "Briefs", "examples": [{"input": {"brief": "Office opens"}, "reference": {}}]},
        "evaluators": [{"name": "says something", "kind": "regex", "config": {"channel": "draft", "pattern": "."}}],
        "tests": [{"name": "pauses for approval", "input": {"brief": "Office opens"},
                   "expect": {"status": "interrupted"}}],
        "triggers": [{"kind": "cron", "config": {"schedule": "0 9 * * 1-5", "input": {"brief": "Daily"}}}],
    }  # fmt: skip
    (tmp_path / "kit_demo.ir.json").write_text(json.dumps(doc))
    monkeypatch.setattr(wf_service, "TEMPLATES", tmp_path)
    return doc


async def test_a_template_kit_is_added_with_the_workflow(app, api_client, kit_template):
    info = {t["id"]: t for t in (await api_client.get("/v1/templates")).json()}["kit_demo"]
    assert info["kit"] == ["2 workspace files", "a dataset of 1 example", "1 evaluator", "1 saved test",
                           "a schedule (off)"]  # fmt: skip

    made = await api_client.post(f"{P}/workflows", json={"name": "Kit", "template": "kit_demo"})
    assert made.status_code == 201, made.text
    wf = made.json()
    key = wf["id"].lower()
    assert wf["kit"]["files_written"] == [f"events/{key}_arrived.py", "nodes/kitdemo.py"]
    assert wf["kit"]["evaluators"] == 1 and wf["kit"]["tests"] == 1 and wf["kit"]["triggers"] == 1
    assert "kit" not in wf["ir"].get("metadata", {})  # the kit is added once, not carried in the workflow

    ws = app.state.projects.workspace("prj_default")
    assert f'act.start_workflow("{wf["id"]}"' in (ws.root / f"events/{key}_arrived.py").read_text()
    catalog = (await api_client.get(f"{P}/workspace/catalog")).json()
    assert any(h["type"] == "kit.demo.arrived" for h in catalog["handlers"])
    types = {t["type"] for t in (await api_client.get("/v1/node-types", params={"project_id": "prj_default"})).json()}
    assert "kitdemo.shout" in types  # the custom block is in the palette

    datasets = (await api_client.get(f"/v1/workflows/{wf['id']}/datasets")).json()
    assert [(d["name"], d["examples"]) for d in datasets] == [("Briefs", 1)]
    assert [e["name"] for e in (await api_client.get(f"/v1/workflows/{wf['id']}/evaluators")).json()] == [
        "says something"
    ]
    assert [t["name"] for t in (await api_client.get(f"/v1/workflows/{wf['id']}/test-cases")).json()] == [
        "pauses for approval"
    ]
    triggers = (await api_client.get(f"/v1/workflows/{wf['id']}/triggers")).json()
    assert [(t["kind"], t["enabled"]) for t in triggers] == [("cron", False)]  # a template does not start running


async def test_a_file_already_in_the_workspace_is_kept(app, api_client, kit_template):
    ws = app.state.projects.workspace("prj_default")
    ws.ensure()
    ws.write("nodes/kitdemo.py", NODE.replace("Shout", "My shout"), "mine")
    wf = (await api_client.post(f"{P}/workflows", json={"name": "Kit", "template": "kit_demo"})).json()
    assert wf["kit"]["files_kept"] == ["nodes/kitdemo.py"]
    assert "My shout" in (ws.root / "nodes/kitdemo.py").read_text()
    plain = (await api_client.post(f"{P}/workflows", json={"name": "Plain", "template": "approval_workflow"})).json()
    assert plain.get("kit") is None  # a template without a kit adds nothing


async def test_a_kit_that_is_not_valid_creates_nothing(app, api_client, kit_template, tmp_path):
    kit_template["metadata"]["kit"]["evaluators"][0]["config"] = {"channel": "draft", "pattern": "("}
    (tmp_path / "kit_demo.ir.json").write_text(json.dumps(kit_template))
    before = len((await api_client.get(f"{P}/workflows")).json()["items"])
    r = await api_client.post(f"{P}/workflows", json={"name": "Kit", "template": "kit_demo"})
    assert r.status_code == 422 and "invalid pattern" in r.json()["detail"]
    assert len((await api_client.get(f"{P}/workflows")).json()["items"]) == before
    assert not (app.state.projects.workspace("prj_default").root / "nodes/kitdemo.py").exists()
