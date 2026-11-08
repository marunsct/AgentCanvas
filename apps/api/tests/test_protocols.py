"""S15: a deployment answers as an MCP server and as an A2A agent, with its key."""

from __future__ import annotations

from typing import Any

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
        assert (result.structured_content or {})["output"]["out"] == "Hello Lin"


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
    m2 = {"message": {"role": "user", "messageId": "m2", "parts": [{"kind": "data", "data": {"who": "Alan"}}]}}
    other = (await api_client.post(url, json=rpc("message/send", m2), headers=auth)).json()["result"]
    assert other["artifacts"][0]["parts"][0]["data"]["out"] == "Hello Alan"
    first = (await api_client.post(url, json=rpc("tasks/get", {"id": task["id"]}), headers=auth)).json()["result"]
    assert first["artifacts"][0]["parts"][0]["data"]["out"] == "Hello Grace"  # each task keeps its own result
    late = {"message": {**m2["message"], "messageId": "m3", "taskId": task["id"], "contextId": task["contextId"]}}
    refused = (await api_client.post(url, json=rpc("message/send", late), headers=auth)).json()
    assert refused["error"]["code"] == -32602  # a finished task takes no more messages
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


async def test_a2a_no_rejects_even_without_a_cached_run(app, api_client):
    from agentcanvas.testing import DemoResources

    app.state.resources_factory = lambda thread_id, wf: DemoResources().with_mocks(HIGH_CASE["mocks"])
    wf = await new_triage(api_client)
    dep, key = await _publish(api_client, wf["id"])
    url, auth = f"/v1/deployments/{dep}/a2a", bearer(key)
    msg = {"message": {"role": "user", "messageId": "m1", "parts": [{"kind": "text", "text": "down"}]}}
    task = (await api_client.post(url, json=rpc("message/send", msg), headers=auth)).json()["result"]
    assert task["status"]["state"] == "input-required"
    app.state.runner.handles.clear()  # as if another API replica took the answer
    reply = {"message": {"role": "user", "messageId": "m2", "taskId": task["id"], "contextId": task["contextId"],
                         "parts": [{"kind": "text", "text": "no"}]}}  # fmt: skip
    done = (await api_client.post(url, json=rpc("message/send", reply), headers=auth)).json()["result"]
    assert done["status"]["state"] == "completed"
    assert done["artifacts"][0]["parts"][0]["data"]["approved"] is False


async def test_wrong_typed_requests_are_errors_not_crashes(api_client):
    dep, key = await _publish(api_client, await make_greeter(api_client))
    auth = bearer(key)
    a2a, mcp = f"/v1/deployments/{dep}/a2a", f"/v1/deployments/{dep}/mcp"
    for url in (a2a, mcp):
        bad = {"jsonrpc": "2.0", "id": 1, "method": "tasks/get" if url == a2a else "tools/list", "params": [1]}
        res = await api_client.post(url, json=bad, headers=auth)
        assert res.status_code in (200, 400) and res.json()["error"]["code"] == -32602
    odd = {"message": {"role": "user", "messageId": "m1", "parts": ["x", {"kind": "data", "data": {"who": "Bo"}}]}}
    task = (await api_client.post(a2a, json=rpc("message/send", odd), headers=auth)).json()["result"]
    assert task["artifacts"][0]["parts"][0]["data"]["out"] == "Hello Bo"
    call = rpc("tools/call", {"name": "greeter", "arguments": {"who": "Al"}, "_meta": 5})
    assert (await api_client.post(mcp, json=call, headers=auth)).status_code == 200
    note = await api_client.post(a2a, json=rpc("tasks/get", {"id": "x"}, id_=None), headers=auth)
    assert note.status_code == 204


async def test_mcp_refuses_foreign_origins_and_unknown_protocol_versions(api_client):
    """Streamable HTTP: servers validate `Origin` (DNS rebinding) and answer 400 to an unsupported
    `MCP-Protocol-Version`; a request without either header (a server-side client) is fine."""
    dep, key = await _publish(api_client, await make_greeter(api_client))
    url, auth = f"/v1/deployments/{dep}/mcp", bearer(key)
    evil = await api_client.post(url, json=rpc("tools/list"), headers={**auth, "Origin": "http://attacker.example"})
    assert evil.status_code == 403 and "attacker.example" in evil.json()["error"]["message"]
    for origin in ("http://test", "http://localhost:5173"):  # the server itself, and a configured CORS origin
        ok = await api_client.post(url, json=rpc("tools/list"), headers={**auth, "Origin": origin})
        assert ok.status_code == 200, (origin, ok.text)
    old = await api_client.post(url, json=rpc("tools/list"), headers={**auth, "MCP-Protocol-Version": "1999-01-01"})
    assert old.status_code == 400 and "1999-01-01" in old.json()["error"]["message"]
    for version in ("2025-11-25", "2025-06-18", "2025-03-26"):
        ok = await api_client.post(url, json=rpc("tools/list"), headers={**auth, "MCP-Protocol-Version": version})
        assert ok.status_code == 200, version
    assert (await api_client.post(url, json=rpc("tools/list"), headers=auth)).status_code == 200


def modern(method: str, params: dict | None = None, id_: int = 1, name: str | None = None) -> tuple[dict, dict]:
    """A 2026-07-28 request: the version and client in `_meta`, mirrored in the headers."""
    meta = {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "1"},
            "io.modelcontextprotocol/clientCapabilities": {}}  # fmt: skip
    headers = {"MCP-Protocol-Version": "2026-07-28", "Mcp-Method": method}
    if name is not None:
        headers["Mcp-Name"] = name
    return rpc(method, {**(params or {}), "_meta": meta}, id_), headers


async def test_mcp_speaks_the_2026_revision_without_a_handshake(api_client):
    """MCP 2026-07-28: no `initialize`, `server/discover`, every result `complete`, list results cacheable, and the
    mirrored headers checked against the body. Legacy clients keep their handshake (dual-era server)."""
    dep, key = await _publish(api_client, await make_greeter(api_client))
    url, auth = f"/v1/deployments/{dep}/mcp", bearer(key)
    body, headers = modern("server/discover")
    found = (await api_client.post(url, json=body, headers={**auth, **headers})).json()["result"]
    assert found["resultType"] == "complete" and "2026-07-28" in found["supportedVersions"]
    assert "2025-11-25" in found["supportedVersions"] and "tools" in found["capabilities"]
    assert found["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "agentcanvas-greeter"
    body, headers = modern("tools/list")
    listed = (await api_client.post(url, json=body, headers={**auth, **headers})).json()["result"]
    assert [t["name"] for t in listed["tools"]] == ["greeter"]
    assert listed["ttlMs"] > 0 and listed["cacheScope"] == "private" and listed["resultType"] == "complete"
    body, headers = modern("tools/call", {"name": "greeter", "arguments": {"who": "Ada"}}, name="greeter")
    called = (await api_client.post(url, json=body, headers={**auth, **headers})).json()["result"]
    assert called["resultType"] == "complete" and called["structuredContent"]["output"]["out"] == "Hello Ada"
    # the mirrored headers must match the body, or a gateway routing on them would see something else run
    body, headers = modern("tools/call", {"name": "greeter", "arguments": {"who": "Ada"}}, name="other_tool")
    mismatch = await api_client.post(url, json=body, headers={**auth, **headers})
    assert mismatch.status_code == 400 and mismatch.json()["error"]["code"] == -32020
    body, headers = modern("tools/list")
    del headers["Mcp-Method"]
    assert (await api_client.post(url, json=body, headers={**auth, **headers})).json()["error"]["code"] == -32020
    encoded = "=?base64?Z3JlZXRlcg==?="  # "greeter", as clients send names that are not plain ASCII
    body, headers = modern("tools/call", {"name": "greeter", "arguments": {"who": "B"}}, name=encoded)
    assert (await api_client.post(url, json=body, headers={**auth, **headers})).status_code == 200
    body, headers = modern("sampling/createMessage")
    assert (await api_client.post(url, json=body, headers={**auth, **headers})).status_code == 404
    body, headers = modern("tools/list")
    body["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"] = headers["MCP-Protocol-Version"] = "2099-01-01"
    future = await api_client.post(url, json=body, headers={**auth, **headers})
    assert future.status_code == 400 and future.json()["error"]["code"] == -32022
    assert "2026-07-28" in future.json()["error"]["data"]["supported"]
    legacy = await api_client.post(url, json=rpc("initialize", {"protocolVersion": "2026-07-28"}), headers=auth)
    assert legacy.json()["result"]["protocolVersion"] == "2025-11-25"  # `initialize` is the legacy era


async def test_a2a_1_0_methods_and_card(app, api_client):
    """A2A 1.0: PascalCase methods, the `A2A-Version` header (none means 0.3), ProtoJSON enum names, parts without
    `kind`, `SendMessage` answering `{"task": …}`; the card in 1.0's shape when asked for, and 0.3's (listing the
    1.0 interface) otherwise. A pause is `TASK_STATE_INPUT_REQUIRED`, answered on the same task."""
    from agentcanvas.testing import DemoResources

    dep, key = await _publish(api_client, await make_greeter(api_client))
    card_url, url = f"/v1/deployments/{dep}/a2a/.well-known/agent-card.json", f"/v1/deployments/{dep}/a2a"
    v1 = {"A2A-Version": "1.0"}
    card = (await api_client.get(card_url, headers=v1)).json()
    assert "url" not in card and "protocolVersion" not in card
    assert {(i["protocolBinding"], i["protocolVersion"]) for i in card["supportedInterfaces"]} == {
        ("JSONRPC", "1.0"), ("JSONRPC", "0.3")}  # fmt: skip
    assert card["securitySchemes"]["deploymentKey"]["httpAuthSecurityScheme"]["scheme"] == "bearer"
    old_card = (await api_client.get(card_url)).json()
    assert old_card["protocolVersion"] == "0.3.0" and old_card["supportedInterfaces"]

    auth = {**bearer(key), **v1}
    msg = {"message": {"role": "ROLE_USER", "messageId": "m1", "parts": [{"data": {"who": "Grace"}}]}}
    no_header = (await api_client.post(url, json=rpc("SendMessage", msg), headers=bearer(key))).json()
    assert no_header["error"]["code"] == -32009  # no header is 0.3, which does not have SendMessage
    sent = (await api_client.post(url, json=rpc("SendMessage", msg), headers=auth)).json()["result"]["task"]
    assert sent["status"]["state"] == "TASK_STATE_COMPLETED" and "kind" not in sent
    [part] = sent["artifacts"][0]["parts"]
    assert "kind" not in part and part["data"]["out"] == "Hello Grace"
    got = (await api_client.post(url, json=rpc("GetTask", {"id": sent["id"]}), headers=auth)).json()["result"]
    assert got["id"] == sent["id"] and got["status"]["state"] == "TASK_STATE_COMPLETED"
    gone = (await api_client.post(url, json=rpc("GetTask", {"id": "run_nope"}), headers=auth)).json()
    assert gone["error"]["code"] == -32001
    cancel = (await api_client.post(url, json=rpc("CancelTask", {"id": sent["id"]}), headers=auth)).json()
    assert cancel["error"]["code"] == -32002
    stream = (await api_client.post(url, json=rpc("SendStreamingMessage", msg), headers=auth)).json()
    assert stream["error"]["code"] == -32004

    app.state.resources_factory = lambda thread_id, wf: DemoResources().with_mocks(HIGH_CASE["mocks"])
    triage, key2 = await _publish(api_client, (await new_triage(api_client))["id"])
    url2, auth2 = f"/v1/deployments/{triage}/a2a", {**bearer(key2), **v1}
    ask = {"message": {"role": "ROLE_USER", "messageId": "t1", "parts": [{"text": "down"}]}}
    paused = (await api_client.post(url2, json=rpc("SendMessage", ask), headers=auth2)).json()["result"]["task"]
    assert paused["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
    assert paused["status"]["message"]["role"] == "ROLE_AGENT" and "kind" not in paused["status"]["message"]
    reply = {"message": {"role": "ROLE_USER", "messageId": "t2", "taskId": paused["id"],
                         "contextId": paused["contextId"], "parts": [{"text": "yes"}]}}  # fmt: skip
    done = (await api_client.post(url2, json=rpc("SendMessage", reply), headers=auth2)).json()["result"]["task"]
    assert done["status"]["state"] == "TASK_STATE_COMPLETED"


def test_the_reference_sdk_reads_the_agent_card():
    """Both card shapes parse with a2a-sdk's own card parser, when the SDK is installed."""
    import pytest

    resolver = pytest.importorskip("a2a.client.card_resolver")
    from test_frameworks import GREETER

    from agentcanvas.api.v1.protocols import agent_card
    from agentcanvas.ir.models import Workflow

    wf = Workflow.model_validate({"id": "wf_c", "name": "Greeter", **GREETER})
    for version in ("1.0", None):
        card = resolver.parse_agent_card(agent_card(wf, "1.0.0", "https://x.example/a2a", version))
        assert card.name == "Greeter" and card.supported_interfaces[0].protocol_version == "1.0"


async def test_another_deployments_thread_answers_like_an_unknown_one(api_client):
    """S-6: a key holder could tell a thread id of another deployment (or organisation) from one that does not
    exist: 404 for unknown, a different 422 for someone else's. Both are now the same 404."""
    dep_a, key_a = await _publish(api_client, await make_greeter(api_client))
    dep_b, key_b = await _publish(api_client, await make_greeter(api_client, "Hi"))
    ran = (await api_client.post(f"/v1/deployments/{dep_a}/runs/wait", json={"input": {"who": "Ada"}},
                                 headers=bearer(key_a))).json()  # fmt: skip
    url_b = f"/v1/deployments/{dep_b}/runs/wait"
    theirs = await api_client.post(url_b, json={"input": {"who": "x"}, "thread_id": ran["thread_id"]},
                                   headers=bearer(key_b))  # fmt: skip
    unknown = await api_client.post(url_b, json={"input": {"who": "x"}, "thread_id": "th_nope"}, headers=bearer(key_b))
    assert theirs.status_code == unknown.status_code == 404
    assert theirs.json()["detail"].replace(ran["thread_id"], "X") == unknown.json()["detail"].replace("th_nope", "X")


async def test_the_agent_card_names_the_address_callers_reach_behind_nginx(app, api_client):
    """OP-1: nginx serves the API under /api (and strips it) and terminates TLS: the card's address is the public one,
    not http://host/v1/..., which nginx answers with the web app."""
    import httpx
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    dep, _ = await _publish(api_client, await make_greeter(api_client))
    path = f"/v1/deployments/{dep}/a2a/.well-known/agent-card.json"
    proxied: Any = ProxyHeadersMiddleware(app, trusted_hosts="*")  # uvicorn --proxy-headers, as in the API image
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=proxied), base_url="http://canvas.test") as c:
        headers = {"X-Forwarded-Proto": "https", "X-Forwarded-Prefix": "/api"}
        card = (await c.get(path, headers=headers)).json()
        assert card["url"] == f"https://canvas.test/api/v1/deployments/{dep}/a2a"
        odd = (await c.get(path, headers={"X-Forwarded-Prefix": "//evil.example"})).json()
        assert odd["url"] == f"http://canvas.test/v1/deployments/{dep}/a2a"  # not a path: ignored
    app.state.settings = app.state.settings.model_copy(update={"public_url": "https://agents.example.com/"})
    card = (await api_client.get(path)).json()
    assert card["url"] == f"https://agents.example.com/api/v1/deployments/{dep}/a2a"  # the configured address wins
