"""S15: a deployment answers as an MCP server and as an A2A agent, with its key."""

from __future__ import annotations

from test_publish import bearer, make_greeter
from test_versions import HIGH_CASE, new_triage


async def _publish(api, wf_id: str) -> tuple[str, str]:
    pub = (await api.post(f"/v1/workflows/{wf_id}/publish", json={})).json()
    return pub["deployment"]["id"], pub["api_key"]


def rpc(method: str, params: dict | None = None, id_: int | None = 1) -> dict:
    msg: dict = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if id_ is not None:
        msg["id"] = id_
    return msg


async def test_mcp_lists_and_runs_the_workflow(api_client):
    dep, key = await _publish(api_client, await make_greeter(api_client))
    url, auth = f"/v1/deployments/{dep}/mcp", bearer(key)
    assert (await api_client.post(url, json=rpc("tools/list"))).status_code == 401
    init = (await api_client.post(url, json=rpc("initialize", {"protocolVersion": "2025-06-18"}), headers=auth)).json()
    assert init["result"]["protocolVersion"] == "2025-06-18" and "tools" in init["result"]["capabilities"]
    note = await api_client.post(url, json=rpc("notifications/initialized", id_=None), headers=auth)
    assert note.status_code == 202
    tools = (await api_client.post(url, json=rpc("tools/list"), headers=auth)).json()["result"]["tools"]
    assert [t["name"] for t in tools] == ["greeter"]  # no pauses: no answer tool
    assert "who" in tools[0]["inputSchema"]["properties"]
    call = rpc("tools/call", {"name": "greeter", "arguments": {"who": "Ada"}})
    res = (await api_client.post(url, json=call, headers=auth)).json()["result"]
    assert res["isError"] is False and res["structuredContent"]["output"]["out"] == "Hello Ada"
    assert "Hello Ada" in res["content"][0]["text"]
    unknown = (await api_client.post(url, json=rpc("tools/call", {"name": "nope"}), headers=auth)).json()
    assert unknown["error"]["code"] == -32602
    assert (await api_client.post(url, json=rpc("sampling/x"), headers=auth)).json()["error"]["code"] == -32601
    assert (await api_client.get(url)).status_code == 405


async def test_mcp_pause_and_answer(app, api_client):
    from agentcanvas.testing import DemoResources

    app.state.resources_factory = lambda thread_id, wf: DemoResources().with_mocks(HIGH_CASE["mocks"])
    wf = await new_triage(api_client)
    dep, key = await _publish(api_client, wf["id"])
    url, auth = f"/v1/deployments/{dep}/mcp", bearer(key)
    tools = (await api_client.post(url, json=rpc("tools/list"), headers=auth)).json()["result"]["tools"]
    run_tool = tools[0]["name"]
    assert [t["name"] for t in tools][1:] == ["answer"]
    call = rpc("tools/call", {"name": run_tool, "arguments": {"ticket": "down"}})
    paused = (await api_client.post(url, json=call, headers=auth)).json()["result"]
    assert paused["structuredContent"]["status"] == "interrupted" and "answer" in paused["content"][0]["text"]
    thread = paused["structuredContent"]["thread_id"]
    answer = rpc("tools/call", {"name": "answer", "arguments": {"thread_id": thread, "approve": True}})
    done = (await api_client.post(url, json=answer, headers=auth)).json()["result"]
    assert done["structuredContent"]["status"] == "success" and done["structuredContent"]["output"]["approved"] is True
    wrong = rpc("tools/call", {"name": "answer", "arguments": {"thread_id": "th_nope", "approve": True}})
    assert (await api_client.post(url, json=wrong, headers=auth)).json()["result"]["isError"] is True


async def test_a_real_mcp_client_connects(app, api_client):
    """The MCP SDK's own client (as Claude Desktop, Cursor and agent frameworks use) talks to the endpoint."""
    import httpx2
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    dep, key = await _publish(api_client, await make_greeter(api_client))

    def factory(headers=None, timeout=None, auth=None, **kw):
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), headers=headers, timeout=timeout, auth=auth, **kw
        )

    transport = StreamableHttpTransport(
        f"http://test/v1/deployments/{dep}/mcp", headers=bearer(key), httpx_client_factory=factory
    )
    async with Client(transport) as client:
        tools = await client.list_tools()
        assert [t.name for t in tools] == ["greeter"]
        result = await client.call_tool("greeter", {"who": "Lin"})
        assert result.structured_content["output"]["out"] == "Hello Lin"


async def test_a2a_card_send_and_get(api_client):
    dep, key = await _publish(api_client, await make_greeter(api_client))
    card = (await api_client.get(f"/v1/deployments/{dep}/a2a/.well-known/agent-card.json")).json()
    assert card["name"] == "Greeter" and card["url"].endswith(f"/v1/deployments/{dep}/a2a")
    assert card["securitySchemes"]["deploymentKey"]["scheme"] == "bearer"
    assert (await api_client.get("/v1/deployments/dep_nope/a2a/.well-known/agent-card.json")).status_code == 404
    url, auth = f"/v1/deployments/{dep}/a2a", bearer(key)
    text = {"message": {"role": "user", "messageId": "m0", "parts": [{"kind": "text", "text": "Grace"}]}}
    unclear = (await api_client.post(url, json=rpc("message/send", text), headers=bearer(key))).json()
    assert unclear["error"]["code"] == -32602 and "who" in unclear["error"]["message"]  # two text fields: which?
    msg = {"message": {"role": "user", "messageId": "m1", "parts": [{"kind": "data", "data": {"who": "Grace"}}]}}
    assert (await api_client.post(url, json=rpc("message/send", msg))).status_code == 401
    task = (await api_client.post(url, json=rpc("message/send", msg), headers=auth)).json()["result"]
    assert task["kind"] == "task" and task["status"]["state"] == "completed"
    assert task["artifacts"][0]["parts"][0]["data"]["out"] == "Hello Grace"
    again = (await api_client.post(url, json=rpc("tasks/get", {"id": task["id"]}), headers=auth)).json()["result"]
    assert again["status"]["state"] == "completed" and again["contextId"] == task["contextId"]
    v1 = {"message": {"role": "user", "messageId": "m2", "parts": [{"kind": "data", "data": {"who": "Alan"}}]}}
    named = (await api_client.post(url, json=rpc("SendMessage", v1), headers=auth)).json()["result"]
    assert named["artifacts"][0]["parts"][0]["data"]["out"] == "Hello Alan"
    missing = (await api_client.post(url, json=rpc("tasks/get", {"id": "run_nope"}), headers=auth)).json()
    assert missing["error"]["code"] == -32001
    assert (await api_client.post(url, json=rpc("message/stream", msg), headers=auth)).json()["error"]["code"] == -32004


async def test_a2a_input_required_then_answer(app, api_client):
    from agentcanvas.testing import DemoResources

    app.state.resources_factory = lambda thread_id, wf: DemoResources().with_mocks(HIGH_CASE["mocks"])
    wf = await new_triage(api_client)
    dep, key = await _publish(api_client, wf["id"])
    url, auth = f"/v1/deployments/{dep}/a2a", bearer(key)
    msg = {"message": {"role": "user", "messageId": "m1", "parts": [{"kind": "text", "text": "down"}]}}
    task = (await api_client.post(url, json=rpc("message/send", msg), headers=auth)).json()["result"]
    assert task["status"]["state"] == "input-required"
    reply = {"message": {"role": "user", "messageId": "m2", "taskId": task["id"], "contextId": task["contextId"],
                         "parts": [{"kind": "text", "text": "yes"}]}}  # fmt: skip
    done = (await api_client.post(url, json=rpc("message/send", reply), headers=auth)).json()["result"]
    assert done["id"] == task["id"] and done["status"]["state"] == "completed"
    assert done["artifacts"][0]["parts"][0]["data"]["approved"] is True
