"""Sprint 6 tools: MCP servers (M3-T5) and HTTP request tools (M3-T6), in both execution modes."""

from __future__ import annotations

import json

import httpx
import pytest
from fastmcp import FastMCP
from langgraph.checkpoint.memory import InMemorySaver
from test_catalog import MESSAGES, cfg, compiled, errors, workflow

from agentcanvas.adapters.langchain.interpreter import abuild_graph
from agentcanvas.adapters.langchain.nodes import Resources
from agentcanvas.testing import scripted, tool_call


def support_server() -> FastMCP:
    srv = FastMCP("support")

    @srv.tool
    def lookup_order(order_id: str) -> str:
        """Look up an order."""
        return f"order {order_id}: shipped"

    @srv.tool(annotations={"destructiveHint": True})
    def delete_ticket(ticket_id: str) -> str:
        """Delete a ticket."""
        return f"deleted {ticket_id}"

    return srv


def mcp_wf(config: dict | None = None, agent: dict | None = None):
    return workflow(
        channels=[MESSAGES],
        resources=[
            ("model", "model.chat", {"model": "fake:scripted"}),
            ("support", "mcp.server", {"url": "https://mcp.example.com/mcp", **(config or {})}),
        ],
        nodes=[("agent", "core.agent", agent or {})],
        flow=[("START", "agent"), ("agent", "END")],
        wiring=[("model", "agent", "model"), ("support", "agent", "tools")],
    )


async def run_both(wf, tmp_path, make_resources, run):
    assert not errors(wf)
    mod = compiled(wf, tmp_path)
    a = await run(await abuild_graph(wf, make_resources(), checkpointer=InMemorySaver()))
    b = await run(await mod.abuild_graph(make_resources(), checkpointer=InMemorySaver()))
    return a, b


def tool_results(out: dict) -> list[str]:
    """Tool message texts (MCP results are content blocks with random ids; compare their text)."""

    def text(content) -> str:
        if isinstance(content, list):
            return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
        return str(content)

    return [text(m.content) for m in out["messages"] if m.type == "tool"]


# ------------------------------------------------------------------------------------------------- MCP ----
async def test_agent_calls_mcp_tools(tmp_path):
    wf = mcp_wf()

    def res():
        return Resources(
            overrides={
                "support": support_server(),  # in-process FastMCP server stands in for the URL
                "model": scripted(tool_call("lookup_order", {"order_id": "A1"}), "It shipped."),
            }
        )

    async def run(graph):
        out = await graph.ainvoke({"messages": [("user", "where is A1?")]}, cfg())
        return tool_results(out), out["messages"][-1].content

    a, b = await run_both(wf, tmp_path, res, run)
    assert a == b
    assert "order A1: shipped" in a[0][0] and a[1] == "It shipped."
    assert "tools=[*support]" in compiled_source(wf)


def compiled_source(wf) -> str:
    from agentcanvas.adapters.langchain.codegen import generate

    return generate(wf).replace("\n", " ")


async def test_tool_filter_limits_the_offered_tools():
    res = Resources(overrides={"support": support_server()})
    tools = await res.load_mcp("support", {"url": "https://x.example/mcp", "tool_filter": ["lookup_order"]})
    assert [t.name for t in tools] == ["lookup_order"]


def test_mcp_approval_rules():
    discovered = {
        "discovered": [
            {"name": "lookup_order", "destructive": False},
            {"name": "delete_ticket", "destructive": True},
        ]
    }
    from agentcanvas.validation import validate

    unsafe = {d.code for d in validate(mcp_wf(discovered))}
    assert "W002" in unsafe
    safe = {d.code for d in validate(mcp_wf(discovered, {"approve_tools": ["delete_ticket"]}))}
    assert "W002" not in safe and "E033" not in safe
    assert "E033" in errors(mcp_wf(discovered, {"approve_tools": ["drop_database"]}))
    filtered = {d.code for d in validate(mcp_wf({**discovered, "tool_filter": ["lookup_order"]}))}
    assert "W002" not in filtered  # the destructive tool is not offered to the agent
    assert "E042" in errors(mcp_wf({"transport": "stdio", "command": "python", "url": None}))


async def test_mcp_inspect_endpoint(app, api_client):
    app.state.resources_factory = lambda thread_id, wf: Resources(overrides={"support": support_server()})
    r = await api_client.post(
        "/v1/mcp/inspect?project_id=prj_default",
        json={"name": "support", "config": {"url": "https://mcp.example.com/mcp"}},
    )
    assert r.status_code == 200, r.text
    tools = {t["name"]: t for t in r.json()}
    assert tools["delete_ticket"]["destructive"] is True and tools["lookup_order"]["destructive"] is False
    r = await api_client.post(
        "/v1/mcp/inspect?project_id=prj_default",
        json={"name": "local", "config": {"transport": "stdio", "command": "python"}},
    )
    assert r.status_code == 403  # local servers need the admin setting (E042)
    other = await api_client.post(
        "/v1/mcp/inspect?project_id=prj_elsewhere", json={"name": "support", "config": {"url": "https://x.test/mcp"}}
    )
    assert other.status_code == 404  # only a project of the caller's organisation lends its secrets


# ------------------------------------------------------------------------------------------------ HTTP ----
def api_transport() -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/orders/A1":
            return httpx.Response(200, json={"id": "A1", "status": "shipped", "auth": request.headers.get("x-key")})
        return httpx.Response(500, text="boom")

    return httpx.MockTransport(handle)


def http_wf(url: str = "https://api.example.com/orders/{{ args.order_id }}"):
    return workflow(
        channels=[MESSAGES],
        resources=[
            ("model", "model.chat", {"model": "fake:scripted"}),
            (
                "get_order",
                "tool.http",
                {
                    "description": "Fetch an order by id",
                    "url": url,
                    "args_schema": {
                        "type": "object",
                        "properties": {"order_id": {"type": "string"}},
                        "required": ["order_id"],
                    },
                    "headers": {"x-key": {"$secret": "ORDERS_KEY"}},
                    "allowed_domains": ["example.com"],
                },
            ),
        ],
        nodes=[("agent", "core.agent", {})],
        flow=[("START", "agent"), ("agent", "END")],
        wiring=[("model", "agent", "model"), ("get_order", "agent", "tools")],
    )


@pytest.mark.parametrize(
    ("order", "expected"),
    [
        ("A1", '"status":"shipped"'),  # allowed host, secret header resolved
        ("B2", "HTTP 500"),  # upstream error -> tool error message, the run goes on
    ],
)
async def test_http_tool(tmp_path, order, expected):
    wf = http_wf()

    def res():
        return Resources(
            overrides={"model": scripted(tool_call("get_order", {"order_id": order}), "Done.")},
            secrets={"ORDERS_KEY": "k-123"},
            http_transport=api_transport(),
        )

    async def run(graph):
        out = await graph.ainvoke({"messages": [("user", "order?")]}, cfg())
        return tool_results(out)[0].replace(" ", ""), out["messages"][-1].content

    a, b = await run_both(wf, tmp_path, res, run)
    assert a == b and expected.replace(" ", "") in a[0] and a[1] == "Done."
    if order == "A1":
        assert json.loads(a[0])["auth"] == "k-123"


async def test_http_tool_refuses_other_domains(tmp_path):
    wf = http_wf("https://{{ args.order_id }}/steal")

    def res():
        return Resources(
            overrides={"model": scripted(tool_call("get_order", {"order_id": "evil.com"}), "Done.")},
            secrets={"ORDERS_KEY": "k"},
            http_transport=api_transport(),
        )

    async def run(graph):
        return tool_results(await graph.ainvoke({"messages": [("user", "x")]}, cfg()))

    a, b = await run_both(wf, tmp_path, res, run)
    assert a == b and "not an allowed domain" in a[0]


def test_http_tool_rules():
    assert "E004" in errors(http_wf("ftp://example.com/x"))  # only http(s) URL templates
    bad = http_wf()
    bad.resource("get_order").config["allowed_domains"] = []  # type: ignore[union-attr]
    assert "E004" in errors(bad)
    leaked = http_wf()
    leaked.resource("get_order").config["headers"] = {"x-key": "sk-ant-" + "a" * 30}  # type: ignore[union-attr]
    assert "E050" in errors(leaked)


@pytest.mark.parametrize(
    "host", ["169.254.169.254", "127.0.0.1", "10.1.2.3", "192.168.0.10", "172.16.5.4", "[::1]", "localhost"]
)
async def test_http_tool_refuses_internal_addresses(host):
    """S7: an allowed domain that is (or resolves to) a metadata, loopback or private address is refused."""
    from agentcanvas.adapters.langchain.nodes import http_tool

    bare = host.strip("[]")
    tool = http_tool(name="t", config={"url": f"http://{host}/latest/meta-data", "allowed_domains": [bare]})
    out = await tool.ainvoke({})
    assert "private or internal address" in out
