"""S20 framework foundation: adapter per workflow, abilities, IR 1.2 engine, portability checks (P001-P004)."""

from __future__ import annotations

import io
import json
import zipfile

import httpx
import pytest

from agentcanvas.ir.models import Workflow
from agentcanvas.registry import default_registry
from agentcanvas.registry.adapters import all_manifests, portability
from agentcanvas.security.imports import is_script_path
from agentcanvas.validation import validate

P = "/v1/projects/prj_default/workflows"


def events(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


@pytest.fixture
async def two(monkeypatch, settings):
    """An app offering LangGraph and the Echo demo engine."""
    monkeypatch.setenv("AGENTCANVAS_ADAPTERS", "langchain,echo")
    from agentcanvas.api.main import create_app

    application = create_app(settings)
    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as c:
            yield c


GREETER = {
    "state": {"channels": [{"name": "who"}, {"name": "greeting"}]},
    "nodes": [
        {"id": "n_hi", "type": "core.set", "name": "hello", "config": {"values": {"greeting": "Hi {{ state.who }}"}}}
    ],
    "edges": [
        {"id": "e1", "kind": "flow", "source": {"node": "START"}, "target": {"node": "n_hi"}},
        {"id": "e2", "kind": "flow", "source": {"node": "n_hi"}, "target": {"node": "END"}},
    ],
}


async def greeter(api, framework: str = "langchain") -> dict:
    wf = (await api.post(P, json={"name": f"Greeter {framework}"})).json()
    ir = {**wf["ir"], **GREETER, "framework": framework}
    r = await api.put(f"/v1/workflows/{wf['id']}", json={"ir": ir}, headers={"If-Match": str(wf["version"])})
    assert r.status_code == 200, r.text
    return r.json()


async def test_the_default_server_offers_ten_frameworks(api_client):
    ids = [f["id"] for f in (await api_client.get("/v1/frameworks")).json()]
    assert ids == [
        "langchain",
        "openai_agents",
        "crewai",
        "google_adk",
        "pydantic_ai",
        "agno",
        "microsoft_agent_framework",
        "strands",
        "llamaindex",
        "mastra",
    ]


async def test_each_framework_says_whether_it_is_set_up_and_how(api_client, monkeypatch):
    from agentcanvas.adapters.agno import AgnoAdapter

    monkeypatch.setattr(AgnoAdapter, "python", lambda self: None)  # `make framework FW=agno` not run here
    listed = {f["id"]: f for f in (await api_client.get("/v1/frameworks")).json()}
    assert listed["langchain"]["ready"] is True and listed["langchain"]["setup"] is None
    assert listed["agno"]["ready"] is False and "make framework FW=agno" in listed["agno"]["setup"]
    monkeypatch.setenv("AGENTCANVAS_IN_DOCKER", "1")  # the Docker app: a make on the host does not reach it
    agno = {f["id"]: f for f in (await api_client.get("/v1/frameworks")).json()}["agno"]
    assert "make docker-up FRAMEWORKS=agno" in agno["setup"]


async def test_a_workflow_exports_for_another_installed_framework(api_client):
    wf = (await api_client.post(P, json={"name": "Any", "template": "classify_and_route"})).json()
    r = await api_client.get(f"/v1/workflows/{wf['id']}/export.zip?framework=crewai")
    assert r.status_code == 200, r.text
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert any(n.endswith("crew.py") or "crewai" in n for n in names)
    assert (await api_client.get(f"/v1/workflows/{wf['id']}")).json()["ir"]["framework"] == "langchain"  # unchanged
    plan = await api_client.get(f"/v1/workflows/{wf['id']}/export/plan?framework=crewai")
    assert plan.status_code == 409  # the deploy kit is for LangGraph services


async def test_the_server_lists_its_frameworks(two):
    both = (await two.get("/v1/frameworks")).json()
    assert [f["id"] for f in both] == ["langchain", "echo"]
    assert both[1]["abilities"] == [] and both[1]["nodes"]["*"] == "unsupported"
    assert all_manifests()["langchain"].has("time_travel") and not all_manifests()["echo"].has("time_travel")


async def test_a_workflow_runs_on_its_own_framework(two):
    wf = await greeter(two, "echo")
    r = await two.post(f"/v1/workflows/{wf['id']}/runs/stream", json={"input": {"who": "Ada"}, "thread_id": "th_echo"})
    evs = events(r.text)
    assert evs[0]["type"] == "RUN_STARTED" and evs[0]["adapter"] == "echo"
    assert evs[-1]["type"] == "RUN_FINISHED" and evs[-1]["output"]["greeting"] == "Hi Ada"
    # what Echo lacks is refused with a plain message
    thread = (await two.get("/v1/threads/th_echo")).json()
    assert thread["state_available"] is False and thread["values"] == {}
    r = await two.post("/v1/threads/th_echo/state", json={"values": {"greeting": "x"}})
    assert r.status_code == 409 and "does not support" in r.json()["detail"]
    r = await two.get("/v1/threads/th_echo/history")
    assert r.status_code == 409 and "run history" in r.json()["detail"]
    # its code view and export are its own
    assert "Echo demo engine" in (await two.get(f"/v1/workflows/{wf['id']}/code")).text
    z = zipfile.ZipFile(io.BytesIO((await two.get(f"/v1/workflows/{wf['id']}/export.zip")).content))
    assert {"workflow.ir.json", "README.md"} <= set(z.namelist())
    assert (await two.get(f"/v1/workflows/{wf['id']}/export/plan")).status_code == 409
    # switching the framework is an undoable command
    r = await two.post(
        f"/v1/workflows/{wf['id']}/commands",
        json={"commands": [{"op": "UpdateMeta", "framework": "langchain"}]},
        headers={"If-Match": str(wf["version"])},
    )
    assert r.json()["ir"]["framework"] == "langchain" and r.json()["inverse"][0]["framework"] == "echo"
    r = await two.post(f"/v1/workflows/{wf['id']}/runs/stream", json={"input": {"who": "Bo"}, "thread_id": "th_lc"})
    assert events(r.text)[0]["adapter"] == "langchain"
    assert (await two.get("/v1/threads/th_lc")).json()["values"]["greeting"] == "Hi Bo"


async def test_a_framework_the_server_lacks_is_refused(api_client):
    wf = await greeter(api_client, "echo")  # this server offers LangGraph only
    assert any(d["code"] == "P001" for d in (await api_client.post(f"/v1/workflows/{wf['id']}/validate")).json())
    r = await api_client.post(f"/v1/workflows/{wf['id']}/runs/stream", json={"input": {"who": "x"}})
    assert r.status_code in (409, 422)
    assert (await api_client.get(f"/v1/workflows/{wf['id']}/code")).status_code in (409, 422)


async def test_portability_names_what_blocks_each_framework(two):
    wf = (await two.post(P, json={"name": "Triage", "template": "support_triage"})).json()
    found = {p["adapter"]: p for p in (await two.get(f"/v1/workflows/{wf['id']}/portability")).json()}
    assert found["langchain"]["ok"] and not found["echo"]["ok"]
    assert "core.agent" in {b["type"] for b in found["echo"]["blockers"]}
    codes = {d["code"] for d in (await two.post(f"/v1/workflows/{wf['id']}/validate")).json()}
    assert "P003" in codes  # runs on 1 of 2 installed frameworks


def test_portability_rules(monkeypatch):
    monkeypatch.setenv("AGENTCANVAS_ADAPTERS", "langchain,echo")
    model = {"id": "r_m", "type": "model.chat", "name": "m", "config": {"model": "anthropic:claude-haiku-4-5"}}
    llm = {"id": "n_ai", "type": "core.llm", "name": "ai", "config": {"prompt": "hi", "output": "out"}}
    wf = Workflow.model_validate({
        "id": "wf_p", "name": "p", "framework": "echo",
        "state": {"channels": [{"name": "out"}]},
        "resources": [model],
        "nodes": [llm, {"id": "n_set", "type": "core.set", "name": "s", "config": {"values": {"out": "x"}},
                        "engine": "echo"}],
        "edges": [
            {"id": "e1", "kind": "flow", "source": {"node": "START"}, "target": {"node": "n_ai"}},
            {"id": "e2", "kind": "flow", "source": {"node": "n_ai"}, "target": {"node": "n_set"}},
            {"id": "e3", "kind": "flow", "source": {"node": "n_set"}, "target": {"node": "END"}},
            {"id": "w1", "kind": "wiring", "source": {"node": "r_m"}, "target": {"node": "n_ai", "port": "model"}},
        ],
    })  # fmt: skip
    found = {(d.code, d.node_id) for d in validate(wf, default_registry())}
    assert ("P002", "n_ai") in found  # Echo only emulates AI steps
    assert ("P001", "n_set") in found  # only AI blocks may name an engine
    lc = Workflow.model_validate({
        **wf.model_dump(mode="json"), "framework": "langchain", "nodes": [{**llm, "engine": "echo"}],
        "edges": [{"id": "e1", "kind": "flow", "source": {"node": "START"}, "target": {"node": "n_ai"}},
                  {"id": "e2", "kind": "flow", "source": {"node": "n_ai"}, "target": {"node": "END"}},
                  wf.edges[3].model_dump()],
    })  # fmt: skip
    found = {(d.code, d.node_id) for d in validate(lc, default_registry())}
    assert ("P004", "n_ai") in found  # designed for Echo, runs on LangGraph for now
    assert [p["ok"] for p in portability(wf)] == [True, True]


def test_engine_is_left_out_of_the_json_when_unset():
    wf = Workflow.model_validate({"id": "wf_x", "name": "x", "ir_version": "1.1", "nodes": [
        {"id": "n_a", "type": "core.set", "name": "a", "config": {"values": {"x": "1"}}}]})  # fmt: skip
    dumped = wf.model_dump(mode="json")
    assert dumped["ir_version"] == "1.3" and "engine" not in dumped["nodes"][0]
    with_engine = Workflow.model_validate({**dumped, "nodes": [{**dumped["nodes"][0], "engine": "echo"}]})
    assert with_engine.model_dump(mode="json")["nodes"][0]["engine"] == "echo"


def test_per_workflow_scripts_go_to_the_sandbox():
    """S17 put a workflow's own Script steps under `flows.<wf>.scripts.`; they must be sandboxed like shared ones."""
    from agentcanvas.adapters.langchain.nodes import Resources, script_node

    calls = []
    runner = lambda path: calls.append(path) or (lambda state: state)  # noqa: E731
    res = Resources(script_runner=runner)
    script_node(import_path="flows.wf_abc.scripts.shout:run", resources=res)
    script_node(import_path="scripts.shout:run", resources=res)
    assert calls == ["flows.wf_abc.scripts.shout:run", "scripts.shout:run"]
    assert is_script_path("flows.wf_abc.scripts.x:run") and not is_script_path("flows.wf_abc.tools.x:look")


async def test_echo_keeps_a_pause_when_its_continuation_fails(two):
    """R-4: Echo dropped the paused state before running the continuation, so a continuation that failed left
    "nothing to resume on this conversation" and the question could not be answered again."""
    wf = (await two.post(P, json={"name": "Echo pause"})).json()
    ir = {**wf["ir"], "framework": "echo",
          "state": {"channels": [{"name": "who"}, {"name": "ok", "type": {"type": "object"}}]},
          "nodes": [{"id": "n_ok", "type": "core.approval", "name": "check", "config": {"show": "who", "output": "ok"}},
                    {"id": "n_bad", "type": "core.script", "name": "bad",
                     "config": {"import_path": "scripts.missing:run"}}],
          "edges": [{"id": "e1", "kind": "flow", "source": {"node": "START"}, "target": {"node": "n_ok"}},
                    {"id": "e2", "kind": "flow", "source": {"node": "n_ok"}, "target": {"node": "n_bad"}},
                    {"id": "e3", "kind": "flow", "source": {"node": "n_bad"}, "target": {"node": "END"}}]}  # fmt: skip
    r = await two.put(f"/v1/workflows/{wf['id']}", json={"ir": ir}, headers={"If-Match": str(wf["version"])})
    assert r.status_code == 200, r.text
    run = await two.post(f"/v1/workflows/{wf['id']}/runs/stream", json={"input": {"who": "Ada"}, "thread_id": "th_ep"})
    assert events(run.text)[-1]["status"] == "interrupted"
    for _ in range(2):  # answering again after a failure reaches the continuation again
        r = await two.post("/v1/threads/th_ep/resume", json={"value": {"approved": True}})
        last = events(r.text)[-1]
        assert last["type"] == "RUN_ERROR" and "nothing to resume" not in last["message"], last
