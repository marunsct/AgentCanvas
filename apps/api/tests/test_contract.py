"""Sprint 8 contract view & API diff (FR-X-14)."""

from __future__ import annotations

import copy

from test_publish import make_greeter

from agentcanvas.ir.contract import breaking_changes, contract
from agentcanvas.ir.models import Channel, ContextField, Node


def test_contract_and_breaking_changes(triage):
    c = contract(triage)
    assert set(c.input) >= {"ticket"} and "summary" in c.output
    assert c.pauses == {"manager_approval": "approval", "reply_drafter": "tool_approval"}

    new = copy.deepcopy(triage)
    new.state.channels = [ch for ch in new.state.channels if ch.name != "summary"]  # output removed
    new.state.channels.append(Channel(name="priority", type={"type": "string", "enum": ["p1", "p2"]}, io="in"))
    new.context.fields.append(ContextField(name="tenant_region", required=True))
    new.nodes.append(
        Node(id="n_q", type="core.human_input", name="ask_customer", config={"question": "?", "output": "x"})
    )
    changes = {(ch.severity, ch.field) for ch in breaking_changes(c, contract(new))}
    assert ("breaking", "summary") in changes and ("compatible", "priority") in changes
    assert ("breaking", "tenant_region") in changes and ("breaking", "ask_customer") in changes

    narrowed = copy.deepcopy(new)
    next(ch for ch in narrowed.state.channels if ch.name == "priority").type = {"type": "string", "enum": ["p1"]}
    assert [ch.message for ch in breaking_changes(contract(new), contract(narrowed))] == [
        "input field 'priority' accepts fewer values (enum narrowed)"
    ]
    assert breaking_changes(c, contract(triage)) == []


async def test_contract_api_and_publish_check(api_client):
    wf = await make_greeter(api_client)
    first = (await api_client.post(f"/v1/workflows/{wf}/publish", json={})).json()
    assert (
        next(c for c in first["checklist"] if c["name"] == "contract")["detail"] == "first publish of this environment"
    )
    await api_client.post(f"/v1/workflows/{wf}/triggers", json={"kind": "cron", "config": {"schedule": "0 * * * *"}})
    got = (await api_client.get(f"/v1/workflows/{wf}/contract")).json()
    assert set(got["input"]) == set(got["output"]) == {"who", "out"}  # inout channels
    assert [s["kind"] for s in got["surfaces"]] == ["http", "cron", "export"]

    cur = (await api_client.get(f"/v1/workflows/{wf}")).json()
    ir = cur["ir"]
    ir["state"]["channels"] = [
        {"name": "who", "io": "in"},
        {"name": "out", "io": "private"},
        {"name": "greeting", "io": "out"},
    ]
    ir["nodes"][0]["config"]["values"] = {"greeting": "Hi {{ state.who }}"}
    r = await api_client.put(f"/v1/workflows/{wf}", json={"ir": ir}, headers={"If-Match": str(cur["version"])})
    assert r.status_code == 200, r.text
    diff = (await api_client.get(f"/v1/workflows/{wf}/versions/1.0.0/contract-diff/draft")).json()
    assert {(d["severity"], d["field"]) for d in diff} == {
        ("breaking", "out"),
        ("breaking", "who"),
        ("compatible", "greeting"),
    }
    second = (await api_client.post(f"/v1/workflows/{wf}/publish", json={})).json()  # informational, not blocking
    check = next(c for c in second["checklist"] if c["name"] == "contract")
    assert check["ok"] and check["detail"] == "3 breaking change(s) vs v1.0.0"
    assert "output field 'out' was removed" in check["items"]
