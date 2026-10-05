"""Sprint 9 blast radius & dry run (FR-X-02)."""

from __future__ import annotations

import json

import httpx

from agentcanvas.ir.models import Workflow
from agentcanvas.validation.blast import blast_radius, dry_run_tools

P = "/v1/projects/prj_default"
CRM = {
    "id": "r_crm", "type": "tool.http", "name": "update_crm",
    "config": {"description": "Update the CRM record", "method": "POST", "url": "https://crm.example.com/update",
               "allowed_domains": ["crm.example.com"],
               "args_schema": {"type": "object", "properties": {"note": {"type": "string"}}, "required": ["note"]}},
}  # fmt: skip


def agent_wf(approve: list[str] | None = None, gate: bool = False) -> dict:
    nodes = [{"id": "n_ag", "type": "core.agent", "name": "assistant",
              "config": {"system_prompt": "Help.", **({"approve_tools": approve} if approve else {})}}]  # fmt: skip
    flow = [("START", "n_ag"), ("n_ag", "END")]
    if gate:
        nodes.insert(
            0,
            {"id": "n_ok", "type": "core.approval", "name": "ok", "config": {"show": "messages", "output": "approved"}},
        )
        flow = [("START", "n_ok"), ("n_ok", "n_ag"), ("n_ag", "END")]
    edges = [
        {"id": f"e{i}", "kind": "flow", "source": {"node": a}, "target": {"node": b}} for i, (a, b) in enumerate(flow)
    ]
    edges += [
        {"id": "w1", "kind": "wiring", "source": {"node": "r_m"}, "target": {"node": "n_ag", "port": "model"}},
        {"id": "w2", "kind": "wiring", "source": {"node": "r_crm"}, "target": {"node": "n_ag", "port": "tools"}},
    ]
    return {
        "id": "wf_blast", "name": "Blast",
        "state": {"channels": [
            {"name": "messages", "type": {"type": "array"}, "reducer": "add_messages"},
            {"name": "approved", "type": {"type": "boolean"}}]},
        "resources": [{"id": "r_m", "type": "model.chat", "name": "model", "config": {"model": "fake:scripted"}}, CRM],
        "nodes": nodes, "edges": edges,
    }  # fmt: skip


def test_blast_radius_marks_gated_and_ungated_capabilities():
    plain = blast_radius(Workflow.model_validate(agent_wf()))
    [node] = plain.nodes
    caps = {(c.effect, c.source, c.gated) for c in node.capabilities}
    assert caps == {("spend", "assistant", False), ("external_write", "update_crm", False)}
    assert plain.ungated == ["n_ag"] and plain.summary == {"spend": 1, "external_write": 1}

    approved = blast_radius(Workflow.model_validate(agent_wf(approve=["update_crm"])))
    assert approved.ungated == [] and ("external_write", "update_crm", True) in {
        (c.effect, c.source, c.gated) for c in approved.nodes[0].capabilities
    }
    gated = blast_radius(Workflow.model_validate(agent_wf(gate=True)))
    assert gated.ungated == []  # every path passes the approval step first
    assert dry_run_tools(Workflow.model_validate(agent_wf())) == {"update_crm"}


async def test_dry_run_records_instead_of_calling(app, api_client):
    from agentcanvas.adapters.langchain.nodes import Resources

    calls: list[httpx.Request] = []
    transport = httpx.MockTransport(lambda req: calls.append(req) or httpx.Response(200, json={"ok": True}))
    app.state.resources_factory = lambda thread_id, wf: Resources(http_transport=transport)
    wf = (await api_client.post(f"{P}/workflows", json={"name": "Blast"})).json()
    ir = {**agent_wf(), "id": wf["id"]}
    assert (
        await api_client.put(f"/v1/workflows/{wf['id']}", json={"ir": ir}, headers={"If-Match": "1"})
    ).status_code == 200
    blast = (await api_client.get(f"/v1/workflows/{wf['id']}/blast-radius")).json()
    assert blast["ungated"] == ["n_ag"]

    mocks = {"model": [{"tool_calls": [{"name": "update_crm", "args": {"note": "VIP"}}]}, "Done."]}
    body = {"input": {"messages": [{"role": "user", "content": "flag them"}]}, "mode": "debug", "mocks": mocks}
    text = (await api_client.post(f"/v1/workflows/{wf['id']}/runs/stream", json={**body, "dry_run": True})).text
    evs = [json.loads(ln[6:]) for ln in text.splitlines() if ln.startswith("data: ")]
    assert evs[-1]["type"] == "RUN_FINISHED", evs[-1]
    recorded = [e["value"] for e in evs if e["type"] == "CUSTOM" and e["name"] == "ac.dry_run"]
    assert recorded == [{"tool": "update_crm", "args": {"note": "VIP"}}] and calls == []
    tool_msg = next(m for m in evs[-1]["output"]["messages"] if m.get("type") == "tool")
    assert tool_msg["content"].startswith("[dry run] 'update_crm' was not called")

    await api_client.post(f"/v1/workflows/{wf['id']}/runs/stream", json=body)  # a normal (mocked) run calls it
    assert len(calls) == 1
    bad = await api_client.post(
        f"/v1/workflows/{wf['id']}/runs/stream", json={**body, "mode": "normal", "dry_run": True}
    )
    assert bad.status_code == 422


def test_blast_radius_respects_the_mcp_tool_filter():
    def mcp(tool_filter: list[str]) -> Workflow:
        wf = agent_wf()
        wf["resources"][1] = {
            "id": "r_mcp", "type": "mcp.server", "name": "crm",
            "config": {"url": "https://mcp.example.com/mcp", "tool_filter": tool_filter,
                       "discovered": [{"name": "lookup", "read_only": True}, {"name": "wipe", "destructive": True}]},
        }  # fmt: skip
        wf["edges"][-1]["source"]["node"] = "r_mcp"
        return Workflow.model_validate(wf)

    assert dry_run_tools(mcp([])) == {"wipe"}
    assert dry_run_tools(mcp(["lookup"])) == set()  # the destructive tool is filtered out, so it cannot be called
    assert blast_radius(mcp(["lookup"])).ungated == []


def test_dry_run_stubs_mcp_tools_that_announce_destructive_hint():
    from langchain_core.tools import StructuredTool

    from agentcanvas.adapters.langchain.nodes import Resources, _config_key

    def make(name: str, destructive: bool) -> StructuredTool:
        t = StructuredTool.from_function(func=lambda: "called", name=name, description=name)
        t.metadata = {"mcp": {"tool": {"annotations": {"destructive_hint": destructive}}}}
        return t

    wipe, lookup = make("wipe", True), make("lookup", False)
    config = {"url": "https://mcp.example.com/mcp"}  # nothing cached by "Browse tools"
    resources = Resources()
    resources._mcp[("crm", _config_key(config))] = [wipe, lookup]
    stubbed = resources.with_dry_run(set()).tool("crm", config, "mcp.server")
    assert stubbed[0] is not wipe and stubbed[0].name == "wipe" and stubbed[1] is lookup
