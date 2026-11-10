"""S22: whole workflows on other frameworks through Agent Spec (preview): gaps, the export and its API.

The exports run for real in CI (`scripts/smoke_frameworks.py bridge_<target>`); here the documents are checked.
"""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Callable
from functools import partial

import pytest

from agentcanvas import agentspec, bridges
from agentcanvas.adapters import flowpackager
from agentcanvas.adapters.crewai import packager as crewai
from agentcanvas.adapters.google_adk import packager as google_adk
from agentcanvas.adapters.mastra import packager as mastra
from agentcanvas.ir.models import Workflow
from agentcanvas.services.workflows import TEMPLATES

P = "/v1/projects/prj_default/workflows"


def template(name: str) -> Workflow:
    return Workflow.model_validate(json.loads((TEMPLATES / f"{name}.ir.json").read_text()))


@pytest.mark.parametrize("target", list(bridges.TARGETS))
def test_simple_flows_have_nothing_blocking(target):
    for name in ("chat_assistant", "data_extraction"):
        assert not [g for g in bridges.gaps(template(name), target) if g.blocks], (name, target)


def test_what_blocks_each_framework_is_named():
    wf = template("research_brief")
    texts = [g.text for g in bridges.gaps(wf, "langgraph") if g.blocks]
    assert any("each_topic" in t and "steps.py" in t for t in texts)
    assert any("side by side" in t for t in texts)
    oa = [g.text for g in bridges.gaps(template("ticket_triage"), "openai_agents") if g.blocks]
    assert any("question for a person" in t for t in oa) and any("escalate" in t for t in oa)
    assert not [g for g in bridges.gaps(template("ticket_triage"), "langgraph") if g.blocks]
    gaps = bridges.gaps(template("ticket_triage"), "wayflow")
    assert gaps[-1].blocks is False and any("nothing reports back" in g.text for g in gaps)


def _differs() -> Workflow:
    """ticket_triage with what only the canvas does: a backup model and a cache, a retry, a collecting field, and an
    approval with a step after it."""
    ir = template("ticket_triage").model_dump(mode="json")
    ir["resources"][0]["config"].update(fallbacks=["openai:gpt-5.5"], cache_ttl=600)
    draft = next(n for n in ir["nodes"] if n["name"] == "draft_reply")
    draft["policies"] = {"retry": {"max_attempts": 3}}
    ir["state"]["channels"].append({"name": "log", "type": {"type": "array"}, "reducer": "append", "io": "private"})
    ok = next(e for e in ir["edges"] if e["kind"] == "flow" and e["source"]["node"] == "n_ok")
    ok["target"]["node"] = "n_draft"
    return Workflow.model_validate(ir)


@pytest.mark.parametrize("target", list(bridges.TARGETS))
def test_gaps_name_what_only_the_canvas_does(target):
    """X-6: backup models, the answer cache, policies and collecting fields are listed; an approval that guards
    later steps must be dealt with (it no longer waits for a yes)."""
    found = bridges.gaps(_differs(), target)
    differs = [g.text for g in found if not g.blocks]
    assert any("'fast_model'" in t and "backup models (openai:gpt-5.5)" in t for t in differs)
    assert any("'fast_model'" in t and "answer cache (600 s)" in t for t in differs)
    assert any("'draft_reply': its retry does not apply" in t for t in differs)
    assert any("'log' combines what steps write on the canvas (append)" in t for t in differs)
    assert any("Approval 'manager_approval' is a question here" in g.text for g in found if g.blocks)
    assert not any(t.startswith("Approval 'manager_approval'") for t in differs)
    plain = bridges.gaps(template("ticket_triage"), target)  # nothing after the approval: it only differs
    assert any(g.text.startswith("Approval 'manager_approval': written as a question") for g in plain if not g.blocks)


def test_gaps_found_by_the_gallery_templates():
    """S23: two patterns the bridges' own runs refused, now named before export."""
    oa = [g.text for g in bridges.gaps(template("sql_helper"), "openai_agents") if g.blocks]
    assert any("'sql' comes from 'write_query'" in t for t in oa)
    assert not [g for g in bridges.gaps(template("sql_helper"), "wayflow") if g.blocks]
    wayflow = [g.text for g in bridges.gaps(template("meeting_notes"), "wayflow") if g.blocks]
    assert wayflow == ["'hand_over' writes text into the list field 'messages'; WayFlow expects a list there."]
    assert not [g for g in bridges.gaps(template("meeting_notes"), "langgraph") if g.blocks]


def test_tool_steps_read_the_state_their_settings_use():
    doc, _ = agentspec.export_flow(template("ticket_triage"))
    step = next(c for c in doc["$referenced_components"].values() if c.get("name") == "escalate"
                and c["component_type"] == "ToolNode")  # fmt: skip
    assert [i["title"] for i in step["inputs"]] == ["ticket"]
    end = next(c for c in doc["$referenced_components"].values() if c["component_type"] == "EndNode")
    assert {o["title"]: o["default"] for o in end["inputs"]} == {"summary": "", "approved": False, "reply": ""}


def test_a_typed_agent_has_its_answer_as_an_output():
    doc, _ = agentspec.export_flow(template("data_extraction"))
    node = next(c for c in doc["$referenced_components"].values() if c["component_type"] == "AgentNode")
    assert node["outputs"][0]["title"] == "structured_response" and "vendor" in node["outputs"][0]["properties"]
    assert doc["inputs"] == []  # the conversation is implicit in Agent Spec


def test_the_export_is_a_project_with_its_gaps():
    files = bridges.export_files(template("classify_and_route"), "wayflow")
    root = "classify-and-route-wayflow"
    assert {
        f"{root}/{p}"
        for p in (
            "flow.json",
            "flowkit.py",
            "steps.py",
            "main.py",
            "GAPS.md",
            "README.md",
            "tests/test_flow.py",
            "tests/fake_model.py",
            "pyproject.toml",
        )
    } <= set(files)
    assert '"wayflowcore==26.3.1"' in files[f"{root}/pyproject.toml"]
    assert "RUNS = True" in files[f"{root}/tests/test_flow.py"]
    assert json.loads(files[f"{root}/models.json"])  # each model's address and key variable
    assert "ANTHROPIC_API_KEY=" in files[f"{root}/.env.example"]
    blocked = bridges.export_files(template("rag_qa"), "langgraph")
    other = next(iter(blocked)).split("/")[0]
    assert "RUNS = False" in blocked[f"{other}/tests/test_flow.py"]
    assert "def retrieve(**state):" in blocked[f"{other}/steps.py"]
    assert "**Must fix:**" in blocked[f"{other}/GAPS.md"]
    for path, text in {**files, **blocked}.items():
        if path.endswith(".py"):
            compile(text, path, "exec")


async def test_the_api_lists_bridges_and_exports(api_client):
    wf = (await api_client.post(P, json={"name": "Triage", "template": "classify_and_route"})).json()
    got = (await api_client.get(f"/v1/workflows/{wf['id']}/bridges")).json()
    assert [t["id"] for t in got["targets"]] == list(bridges.TARGETS)
    lg = got["targets"][0]
    assert lg["preview"] is True and lg["runs"] is True and lg["gaps"]
    assert got["targets"][2]["runs"] is False  # OpenAI Agents SDK flows take no tool steps
    assert {n["label"] for n in got["not_offered"]} == set(bridges.NOT_OFFERED)
    r = await api_client.get(f"/v1/workflows/{wf['id']}/bridges/wayflow/export.zip")
    assert r.status_code == 200 and "triage-wayflow.zip" in r.headers["content-disposition"]
    assert "triage-wayflow/flow.json" in zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert (await api_client.get(f"/v1/workflows/{wf['id']}/bridges/crewai/export.zip")).status_code == 404


def test_odd_names_secrets_and_filtered_reads():
    ir = json.loads((TEMPLATES / "research_brief.ir.json").read_text())
    for n in ir["nodes"]:
        if n["name"] == "each_topic":
            n["name"] = "global"  # a Python keyword: allowed as a step name
    for r in ir["resources"]:
        if r["type"] == "model.chat":
            r["config"]["api_key"] = {"$secret": "TEAM_KEY"}
    ir["nodes"].append(
        {
            "id": "n_up",
            "type": "core.set",
            "name": "shout",
            "config": {"values": {"loud": "{{ state.topic | upper }} {{ state.doc.title }}"}},
        }
    )
    wf = Workflow.model_validate(ir)
    files = bridges.export_files(wf, "langgraph")
    root = next(iter(files)).split("/")[0]
    assert "def global_(**state):" in files[f"{root}/steps.py"]
    compile(files[f"{root}/steps.py"], "steps.py", "exec")
    assert {m["key_env"] for m in json.loads(files[f"{root}/models.json"]).values()} == {"TEAM_KEY"}
    doc = json.loads(files[f"{root}/flow.json"])
    shout = next(c for c in doc["$referenced_components"].values() if c.get("name") == "shout"
                 and c["component_type"] == "ToolNode")  # fmt: skip
    assert [i["title"] for i in shout["inputs"]] == ["topic", "doc"]


EXPORTS: dict[str, Callable[[Workflow], tuple[str, bytes]]] = {
    "crewai": partial(crewai.package, source=""),
    "google_adk": partial(google_adk.package, source=""),
    "mastra": partial(mastra.package, source=""),
    **{fw: partial(flowpackager.package, source="", framework=fw) for fw in flowpackager.TARGETS},
}


def _read(data: bytes, suffix: str) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return z.read(next(n for n in z.namelist() if n.endswith(suffix))).decode()


@pytest.mark.parametrize("framework", list(EXPORTS))
def test_framework_exports_say_what_runs_differently(framework):
    """X-6: an export that drops backup models, the answer cache and step policies names the blocks in GAPS.md."""
    _, data = EXPORTS[framework](_differs())
    gaps = _read(data, "/GAPS.md")
    assert "Model block 'fast_model': its backup models (openai:gpt-5.5) are not tried" in gaps
    assert "answer cache (600 s)" in gaps and "'draft_reply': its retry does not apply" in gaps
    assert "`GAPS.md`" in _read(data, "/README.md")
    assert "Nothing found" in _read(EXPORTS[framework](template("chat_assistant"))[1], "/GAPS.md")


def test_a_malformed_list_of_tools_needing_approval_names_only_the_tools():
    """A hand edit or an import can leave a list inside `approve_tools` (the schema reports it): the bridges page
    answered 500 joining it. Only the names in it are listed. Found by the workflow fuzz test."""
    wf = template("web_research")
    agent = next(n for n in wf.nodes if "approve_tools" in (n.config or {}))
    agent.config = {**agent.config, "approve_tools": [["refund"], "send_email", None, 3]}
    texts = [g.text for g in bridges.gaps(wf, "langgraph")]
    assert any("send_email asked a person" in t for t in texts), texts
