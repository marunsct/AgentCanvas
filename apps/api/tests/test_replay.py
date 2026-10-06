"""Sprint 8 record/replay (FR-X-13): a published run's model replies become a deterministic regression test."""

from __future__ import annotations

P = "/v1/projects/prj_default"


def bearer(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


async def test_production_run_becomes_a_regression_test(app, api_client):
    from agentcanvas.testing import DemoResources

    app.state.resources_factory = lambda thread_id, wf: DemoResources()  # "production" models (the demo model)
    wf = (await api_client.post(f"{P}/workflows", json={"name": "Desk", "template": "ticket_triage"})).json()
    pub = (await api_client.post(f"/v1/workflows/{wf['id']}/publish", json={})).json()
    res = (
        await api_client.post(
            f"/v1/deployments/{pub['deployment']['id']}/runs/wait", json={"input": {"ticket": "How do I log in?"}},
            headers=bearer(pub["api_key"]),
        )
    ).json()  # fmt: skip
    assert res["status"] == "success", res

    case = await api_client.post(f"/v1/runs/{res['run_id']}/test-case", json={"name": "login question"})
    assert case.status_code == 201, case.text
    tc = case.json()
    assert tc["input"] == {"ticket": "How do I log in?"}
    assert set(tc["mocks"]) == {"fast_model", "smart_model"}  # each model resource's recorded replies
    assert tc["expect"]["route"] == ["n_cls", "n_rt", "n_draft"]
    assert tc["expect"]["channels"]["reply"] == res["output"]["reply"]

    base = f"/v1/workflows/{wf['id']}/test-cases/run"
    app.state.resources_factory = lambda thread_id, wf: DemoResources()
    assert (await api_client.post(base, json={})).json()["failed"] == 0  # replays deterministically

    cur = (await api_client.get(f"/v1/workflows/{wf['id']}")).json()  # a new version routes differently
    ir = cur["ir"]
    urgency = res["output"]["urgency"]
    next(n for n in ir["nodes"] if n["id"] == "n_rt")["config"]["cases"][0]["when"] = f'state.urgency == "{urgency}"'
    await api_client.put(f"/v1/workflows/{wf['id']}", json={"ir": ir}, headers={"If-Match": str(cur["version"])})
    red = (await api_client.post(base, json={})).json()
    assert red["failed"] == 1 and any(f.startswith("route:") for f in red["results"][0]["failures"])

    resumed = await api_client.post(f"/v1/runs/{red['results'][0]['run_id']}/test-case", json={"name": "x"})
    assert resumed.status_code == 422  # test runs are not recorded


# --------------------------------------------------------------------------- tool I/O (S13 item 8) ----
async def test_recorded_tool_outputs_replay_without_calling_the_tools(app, api_client, monkeypatch):
    """HTTP, MCP and workspace tools: a run records what each tool resource was asked and answered; its test case
    replays those answers and none of the tools is called again (FR-X-13, S13)."""
    import httpx
    from test_catalog import MESSAGES, workflow
    from test_tools import api_transport, support_server
    from test_workspace import events, make_wf

    import examples.tools
    from agentcanvas.adapters.langchain.nodes import Resources
    from agentcanvas.testing import scripted, tool_call

    calls: list[str] = []

    def send_reply(text: str) -> str:
        """Send the drafted reply to the customer."""
        calls.append("send_reply")
        return f"sent: {text}"

    monkeypatch.setattr(examples.tools, "send_reply", send_reply)
    ir = workflow(
        channels=[MESSAGES],
        resources=[
            ("model", "model.chat", {"model": "fake:scripted"}),
            ("get_order", "tool.http", {
                "description": "Fetch an order", "url": "https://api.example.com/orders/{{ args.order_id }}",
                "allowed_domains": ["example.com"],
                "args_schema": {"type": "object", "properties": {"order_id": {"type": "string"}}},
            }),
            ("support", "mcp.server", {"url": "https://mcp.example.com/mcp"}),
            ("reply", "tool.python", {"import_path": "examples.tools:send_reply"}),
        ],
        nodes=[("agent", "core.agent", {})],
        flow=[("START", "agent"), ("agent", "END")],
        wiring=[("model", "agent", "model"), ("get_order", "agent", "tools"), ("support", "agent", "tools"),
                ("reply", "agent", "tools")],
    ).model_dump(mode="json", by_alias=True, exclude_none=True)  # fmt: skip
    wf = await make_wf(api_client, ir)

    def live(thread_id, wf):
        model = scripted(
            tool_call("get_order", {"order_id": "A1"}), tool_call("lookup_order", {"order_id": "A1"}),
            tool_call("send_reply", {"text": "It shipped."}), "Done.",
        )  # fmt: skip
        return Resources(overrides={"model": model, "support": support_server()}, http_transport=api_transport())

    app.state.resources_factory = live
    body = {"input": {"messages": [{"role": "user", "content": "where is A1?"}]}}
    resp = await api_client.post(f"/v1/workflows/{wf}/runs/stream", json=body)
    assert resp.status_code == 200, resp.text
    evs = events(resp.text)
    assert evs[-1]["type"] == "RUN_FINISHED" and calls == ["send_reply"], evs[-1]
    tool_io = [e["value"] for e in evs if e.get("name") == "ac.tool_io"]
    assert [io["resource"] for io in tool_io] == ["get_order", "support", "reply"]

    tc = (await api_client.post(f"/v1/runs/{evs[0]['runId']}/test-case", json={"name": "order A1"})).json()
    mocks = tc["mocks"]
    assert mocks["get_order"] == [
        {"tool": "get_order", "args": {"order_id": "A1"}, "output": '{"id":"A1","status":"shipped","auth":null}'}
    ]
    [mcp] = mocks["support"]  # MCP results are content blocks
    assert mcp["tool"] == "lookup_order" and mcp["output"][0]["text"] == "order A1: shipped"
    assert mocks["reply"] == [{"tool": "send_reply", "args": {"text": "It shipped."}, "output": "sent: It shipped."}]

    def offline(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"the HTTP tool was called: {request.url}")

    # replay: the MCP server is not reachable (no override), HTTP must not be called, the function is not run
    app.state.resources_factory = lambda thread_id, wf: Resources(http_transport=httpx.MockTransport(offline))
    calls.clear()
    result = (await api_client.post(f"/v1/workflows/{wf}/test-cases/run", json={})).json()
    assert result["failed"] == 0 and calls == [], result
    run = result["results"][0]["run_id"]
    replayed = events((await api_client.get(f"/v1/runs/{run}/events")).text)
    messages = replayed[-1]["output"]["messages"]
    tool_texts = [str(m.get("content")) for m in messages if m.get("type") == "tool"]
    assert len(tool_texts) == 3 and "shipped" in tool_texts[0] and "order A1: shipped" in tool_texts[1]
    assert tool_texts[2] == "sent: It shipped."


def test_replay_queue_prefers_matching_arguments():
    import pytest
    from langchain_core.tools import ToolException

    from agentcanvas.adapters.langchain.nodes import ReplayQueue

    q = ReplayQueue([
        {"tool": "get", "args": {"id": "1"}, "output": "one"},
        {"tool": "get", "args": {"id": "2"}, "output": "two"},
        {"tool": "get", "args": {"id": "3"}, "error": "HTTP 500"},
    ])  # fmt: skip
    assert q.take("get", {"id": "2"}) == "two" and q.take("get", {"id": "9"}) == "one"
    with pytest.raises(ToolException, match="HTTP 500"):
        q.take("get", {"id": "3"})
    assert q.take("get", {"id": "2"}) == "two"  # reused: a resumed run replays from the start
    with pytest.raises(ToolException, match="no recorded output"):
        q.take("get", {"id": "7"})
