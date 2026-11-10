"""F-1: a paused run asks the person through their MCP client (MCP 2026-07-28 Multi Round-Trip Requests)."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from test_protocols import _publish, rpc
from test_publish import bearer
from test_replicas import replica
from test_versions import HIGH_CASE, new_triage

from agentcanvas.api.v1 import mcp_input
from agentcanvas.db.models import InterruptTask, Run

FORMS = {"elicitation": {"form": {}}}


def modern(method: str, params: dict | None = None, caps: dict | None = None, id_: int = 1) -> tuple[dict, dict]:
    meta = {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "1"},
            "io.modelcontextprotocol/clientCapabilities": caps or {}}  # fmt: skip
    headers = {"MCP-Protocol-Version": "2026-07-28", "Mcp-Method": method}
    params = params or {}
    if method == "tools/call":
        headers["Mcp-Name"] = params["name"]
    elif method.startswith("tasks/"):
        headers["Mcp-Name"] = params["taskId"]
    return rpc(method, {**params, "_meta": meta}, id_), headers


async def _triage(app, api) -> tuple[str, dict, str]:
    """A published triage workflow that pauses for a manager's approval: (MCP url, auth, tool name)."""
    from agentcanvas.testing import DemoResources

    app.state.resources_factory = lambda thread_id, wf: DemoResources().with_mocks(HIGH_CASE["mocks"])
    dep, key = await _publish(api, (await new_triage(api))["id"])
    url = f"/v1/deployments/{dep}/mcp"
    body, headers = modern("tools/list")
    tool = (await api.post(url, json=body, headers={**bearer(key), **headers})).json()["result"]["tools"][0]["name"]
    return url, bearer(key), tool


async def _call(api, url: str, auth: dict, params: dict, caps: dict | None = FORMS, id_: int = 1) -> dict:
    body, headers = modern("tools/call", params, caps, id_)
    res = (await api.post(url, json=body, headers={**auth, **headers})).json()
    assert "result" in res, res
    return res["result"]


async def _runs(app, thread_id: str) -> int:
    async with app.state.db.sessionmaker() as s:
        return await s.scalar(select(func.count()).select_from(Run).where(Run.thread_id == thread_id)) or 0


async def test_a_pause_asks_the_person_through_the_client_and_the_retry_resumes(app, api_client):
    url, auth, tool = await _triage(app, api_client)
    body, headers = modern("tools/list", caps=FORMS)
    tools = (await api_client.post(url, json=body, headers={**auth, **headers})).json()["result"]["tools"]
    assert [t["name"] for t in tools] == [tool]  # no `answer`: the model does not answer for the person
    assert "your client then asks you" in tools[0]["description"]
    call = {"name": tool, "arguments": {"ticket": "down"}}
    asked = await _call(api_client, url, auth, call)
    assert asked["resultType"] == "input_required" and asked["requestState"]
    [(iid, form)] = asked["inputRequests"].items()
    assert form["method"] == "elicitation/create" and form["params"]["mode"] == "form"
    schema = form["params"]["requestedSchema"]
    assert schema["properties"]["approve"]["type"] == "boolean" and schema["required"] == ["approve"]
    thread_id = (await _pending(app, iid)).thread_id
    yes = {"action": "accept", "content": {"approve": True, "comment": "go"}}
    answered = {**call, "requestState": asked["requestState"], "inputResponses": {iid: yes}}
    done = await _call(api_client, url, auth, answered, id_=2)
    assert done["resultType"] == "complete" and done["structuredContent"]["status"] == "success"
    assert done["structuredContent"]["output"]["approved"] is True
    assert await _runs(app, thread_id) == 2
    again = await _call(api_client, url, auth, answered, id_=3)  # the same retry once more: answered once
    assert again["structuredContent"]["status"] == "success" and await _runs(app, thread_id) == 2
    [entry] = [e for e in (await api_client.get("/v1/org/audit", params={"action": "mcp_endpoint"})).json()
               if e["detail"].get("interrupt_id") == iid]  # fmt: skip
    assert entry["detail"]["payload_sha256"]


async def _pending(app, iid: str) -> InterruptTask:
    async with app.state.db.sessionmaker() as s:
        row = await s.get(InterruptTask, iid)
        assert row is not None
        return row


async def test_declining_rejects_and_closing_the_form_leaves_the_run_waiting(app, api_client):
    url, auth, tool = await _triage(app, api_client)
    call = {"name": tool, "arguments": {"ticket": "down"}}
    asked = await _call(api_client, url, auth, call)
    [iid] = asked["inputRequests"]
    for response in (None, {"action": "accept", "content": {}}, {"action": "maybe"}):  # incomplete: asked again
        retry = {**call, "requestState": asked["requestState"], "inputResponses": {iid: response} if response else {}}
        again = await _call(api_client, url, auth, retry, id_=2)
        assert again["resultType"] == "input_required" and list(again["inputRequests"]) == [iid]
    closed = await _call(api_client, url, auth, {**call, "requestState": asked["requestState"],
                                                 "inputResponses": {iid: {"action": "cancel"}}})  # fmt: skip
    assert closed["isError"] is True and "still waits" in closed["content"][0]["text"]
    assert (await _pending(app, iid)).status == "pending"
    declined = await _call(api_client, url, auth, {**call, "requestState": asked["requestState"],
                                                   "inputResponses": {iid: {"action": "decline"}}})  # fmt: skip
    assert declined["structuredContent"]["status"] == "success"
    assert declined["structuredContent"]["output"]["approved"] is False


async def test_the_request_state_is_bound_to_the_caller_the_call_and_a_short_time(app, api_client, monkeypatch):
    url, auth, tool = await _triage(app, api_client)
    call = {"name": tool, "arguments": {"ticket": "down"}}
    asked = await _call(api_client, url, auth, call)
    [iid] = asked["inputRequests"]
    yes = {iid: {"action": "accept", "content": {"approve": True}}}
    state = asked["requestState"]
    head, tag = state.split(".")
    for bad, why in [
        (head + "." + tag[:-2] + ("AA" if not tag.endswith("AA") else "BB"), "not one this server issued"),
        ("nonsense", "not one this server issued"),
        (None, "not one this server issued"),
    ]:
        body, headers = modern("tools/call", {**call, "requestState": bad, "inputResponses": yes}, FORMS)
        err = (await api_client.post(url, json=body, headers={**auth, **headers})).json()["error"]
        assert err["code"] == -32602 and why in err["message"], (bad, err)
    other = {"name": tool, "arguments": {"ticket": "something else"}, "requestState": state, "inputResponses": yes}
    body, headers = modern("tools/call", other, FORMS)
    err = (await api_client.post(url, json=body, headers={**auth, **headers})).json()["error"]
    assert "another request" in err["message"]
    body, headers = modern("tools/call", {**call, "requestState": state, "inputResponses": yes}, {})
    err = (await api_client.post(url, json=body, headers={**auth, **headers})).json()["error"]
    assert err["code"] == -32602  # a client without forms cannot answer one
    dep = url.split("/")[3]
    rotated = (await api_client.post(f"/v1/deployments/{dep}/rotate-key")).json()["api_key"]
    body, headers = modern("tools/call", {**call, "requestState": state, "inputResponses": yes}, FORMS)
    err = (await api_client.post(url, json=body, headers={**bearer(rotated), **headers})).json()["error"]
    assert "another caller" in err["message"]  # a new key: what the old one was asked does not carry over
    monkeypatch.setattr(mcp_input, "STATE_TTL_S", -1)  # issued already out of date
    asked = await _call(api_client, url, bearer(rotated), call)
    late = {next(iter(asked["inputRequests"])): yes[iid]}
    body, headers = modern("tools/call", {**call, "requestState": asked["requestState"], "inputResponses": late}, FORMS)
    err = (await api_client.post(url, json=body, headers={**bearer(rotated), **headers})).json()["error"]
    assert "expired" in err["message"]


async def test_any_server_takes_the_answer(app, api_client, settings):
    """The state travels with the client: the retry may reach another replica."""
    url, auth, tool = await _triage(app, api_client)
    call = {"name": tool, "arguments": {"ticket": "down"}}
    asked = await _call(api_client, url, auth, call)
    [iid] = asked["inputRequests"]
    async with replica(settings) as (other, b):
        other.state.resources_factory = app.state.resources_factory
        retry = {**call, "requestState": asked["requestState"],
                 "inputResponses": {iid: {"action": "accept", "content": {"approve": True}}}}  # fmt: skip
        done = await _call(b, url, auth, retry, id_=2)
    assert done["structuredContent"]["status"] == "success"


async def test_clients_without_forms_keep_the_answer_tool(app, api_client):
    url, auth, tool = await _triage(app, api_client)
    body, headers = modern("tools/list")
    tools = (await api_client.post(url, json=body, headers={**auth, **headers})).json()["result"]["tools"]
    assert [t["name"] for t in tools] == [tool, "answer"]
    paused = await _call(api_client, url, auth, {"name": tool, "arguments": {"ticket": "down"}}, caps={})
    assert paused["resultType"] == "complete" and "Call `answer`" in paused["content"][0]["text"]
    url_only = await _call(api_client, url, auth, {"name": tool, "arguments": {"ticket": "x"}},
                           caps={"elicitation": {"url": {}}})  # fmt: skip
    assert url_only["resultType"] == "complete"  # it shows no forms: none is sent to it


@pytest.mark.parametrize(
    ("payload", "content", "value"),
    [
        ({"kind": "input", "prompt": "Your name?"}, {"answer": "Lin"}, "Lin"),
        ({"kind": "input", "prompt": "How many?", "response_schema": {"type": "integer", "minimum": 1}},
         {"answer": 3}, 3),
        ({"kind": "input", "prompt": "Contact", "response_schema": {
            "type": "object", "properties": {"email": {"type": "string"}, "age": {"type": "integer"}}}},
         {"email": "a@b.c", "age": 3}, {"email": "a@b.c", "age": 3}),
    ],
)  # fmt: skip
def test_a_question_becomes_a_form_and_its_answer_the_resume_value(payload, content, value):
    interrupt = {"id": "i1", "kind": "input", "payload": payload}
    form = mcp_input.form(interrupt, "Intake")
    schema = form["params"]["requestedSchema"]
    assert form["params"]["message"] == payload["prompt"] and schema["type"] == "object"
    assert all(p["type"] in mcp_input.PRIMITIVE for p in schema["properties"].values())  # a form can show it
    assert mcp_input.answer(interrupt, {"action": "accept", "content": content}, None) == ("resume", value)
    assert mcp_input.answer(interrupt, {"action": "decline"}, None)[0] == "declined"


def test_a_tool_approval_lists_the_calls():
    interrupt = {"id": "i1", "kind": "tool_approval",
                 "payload": {"action_requests": [{"name": "refund", "args": {"amount": 30}}]}}  # fmt: skip
    form = mcp_input.form(interrupt, "Support")
    assert 'refund({\n "amount": 30\n})' in form["params"]["message"]
    shape = lambda ok, why: {"ok": ok, "why": why}  # noqa: E731
    assert mcp_input.answer(interrupt, {"action": "accept", "content": {"approve": False, "comment": "no"}}, shape) == (
        "resume", {"ok": False, "why": "no"})  # fmt: skip
