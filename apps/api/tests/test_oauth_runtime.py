"""App connectors that sign in (G-7) at run time: one list of tools serves everyone, each call carries the
connection of whoever the run acts for; the runner tells the connector who that is; browsing tools uses your own."""

import contextlib

import httpx2
import pytest
from fake_oauth import MCP
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers
from test_auth import settings  # noqa: F401 - turns OIDC sign-in on
from test_oauth_connections import ALICE, BOB, ORG, client, connect, user_id, world  # noqa: F401 - fixtures

from agentcanvas.adapters.langchain import nodes
from agentcanvas.adapters.langchain.nodes import Resources
from agentcanvas.api.v1.tools import _connection_problem
from agentcanvas.runtime.identity import CURRENT, RunIdentity
from agentcanvas.services.oauth import ConnectionRequired


def whoami_app(fake):
    """An MCP server (over ASGI) that refuses requests without a token the fake authorization server issued, and has
    one tool: it returns the bearer token it was called with."""
    srv = FastMCP("whoami")

    @srv.tool
    def whoami() -> str:
        """The token this call carried."""
        return get_http_headers(include_all=True).get("authorization", "").removeprefix("Bearer ")

    inner = srv.http_app(path="/mcp")

    async def guarded(scope, receive, send):
        if scope["type"] == "http":
            sent = dict(scope["headers"]).get(b"authorization", b"").decode().removeprefix("Bearer ")
            if sent not in fake.access:
                await send(
                    {"type": "http.response.start", "status": 401, "headers": [(b"www-authenticate", b"Bearer")]}
                )
                await send({"type": "http.response.body", "body": b"{}"})
                return
        await inner(scope, receive, send)

    guarded.lifespan = inner.router.lifespan_context  # type: ignore[attr-defined]
    guarded.inner = inner  # type: ignore[attr-defined]
    return guarded


@pytest.fixture
def serving(world, monkeypatch):  # noqa: F811
    """`async with serving():` runs the MCP server and points the MCP client at it. (A context manager the test enters,
    not an async fixture: the server's lifespan holds a cancel scope that must open and close in one task.)"""

    @contextlib.asynccontextmanager
    async def serve():
        app = whoami_app(world)

        def factory(headers=None, timeout=None, auth=None, **_asked):
            return httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app), headers=headers, timeout=timeout or httpx2.Timeout(30.0),
                auth=auth, follow_redirects=False,
            )  # fmt: skip

        monkeypatch.setattr(nodes, "MCP_CLIENT_FACTORY", factory)
        async with app.inner.router.lifespan_context(app.inner):  # type: ignore[attr-defined]
            yield

    return serve


def connector(identity: str = "starter") -> dict:
    return {"url": MCP, "oauth": {"identity": identity}}


async def call_as(tools, who: RunIdentity | None) -> str:
    CURRENT.set(who)
    return str(await tools[0].ainvoke({}))


async def test_one_list_of_tools_serves_everyone_each_call_carries_its_own_connection(app, client, world, serving):  # noqa: F811
    async with serving():
        await connect(client, world, ALICE, who="alice")
        await connect(client, world, BOB, who="bob")
        await connect(client, world, ALICE, who="the-service", service=True)
        alice, bob = await user_id(app, "alice-sub"), await user_id(app, "bob-sub")
        tokens = {w: t for t, w in world.access.items()}
        res = Resources()
        CURRENT.set(RunIdentity(ORG, alice))
        tools = await res.load_mcp("crm", connector())  # discovered once, as Alice
        assert [t.name for t in tools] == ["whoami"]
        assert tools is await res.load_mcp("crm", connector())  # and shared
        assert tokens["alice"] in await call_as(tools, RunIdentity(ORG, alice))
        assert tokens["bob"] in await call_as(tools, RunIdentity(ORG, bob))  # the same tools, Bob's token
        assert tokens["alice"] in await call_as(tools, RunIdentity(ORG, alice))
        CURRENT.set(RunIdentity(ORG, alice, service_ok=True))  # an admin's
        svc_tools = await Resources().load_mcp("crm-svc", connector("service"))
        deployment_run = RunIdentity(ORG, bob, service_ok=True)  # e.g. Bob resumed it from the inbox
        assert tokens["the-service"] in await call_as(svc_tools, deployment_run)
        assert tokens["the-service"] in await call_as(svc_tools, RunIdentity(ORG, None))  # a schedule: nobody
        CURRENT.set(RunIdentity(ORG, bob))  # Bob's draft run (CP-3)
        with pytest.raises(BaseException) as seen:
            await svc_tools[0].ainvoke({})
        problem = _connection_problem(seen.value)
        assert problem is not None and "not by a draft run" in problem.detail


async def test_a_run_nobody_started_cannot_use_a_personal_connection(app, client, world, serving):  # noqa: F811
    async with serving():
        await connect(client, world, ALICE, who="alice")
        alice = await user_id(app, "alice-sub")
        CURRENT.set(RunIdentity(ORG, alice))
        tools = await Resources().load_mcp("crm", connector())
        CURRENT.set(RunIdentity(ORG, None))  # a webhook started it
        with pytest.raises(BaseException) as seen:  # the MCP client wraps what its transport raises
            await tools[0].ainvoke({})
        problem = _connection_problem(seen.value)
        assert problem is not None and "service connection" in problem.detail
        CURRENT.set(RunIdentity(ORG, await user_id(app, "bob-sub")))  # Bob has no connection of his own
        with pytest.raises(BaseException) as seen:
            await tools[0].ainvoke({})
        problem = _connection_problem(seen.value)
        assert problem is not None and "Connect button" in problem.detail


async def test_a_connector_with_oauth_outside_the_server_says_so():
    nodes.OAUTH_AUTH, saved = None, nodes.OAUTH_AUTH
    try:
        with pytest.raises(RuntimeError, match="only the AgentCanvas server supports"):
            nodes.mcp_target({"url": MCP, "oauth": {"identity": "starter"}})
    finally:
        nodes.OAUTH_AUTH = saved


async def test_without_oauth_nothing_changes():
    target = nodes.mcp_target({"url": "https://plain.example/mcp", "headers": {"X-Key": "k"}})
    assert target.auth is None and target.headers == {"X-Key": "k"}


async def test_browsing_tools_uses_your_own_connection_and_says_when_there_is_none(app, client, world, serving):  # noqa: F811
    async with serving():
        app.state.resources_factory = lambda thread_id, wf: Resources()
        body = {"name": "crm", "config": connector()}
        r = await client.post("/v1/mcp/inspect?project_id=prj_default", json=body, headers=ALICE)
        assert r.status_code == 409 and "Connect button" in r.json()["detail"], r.text
        await connect(client, world, ALICE, who="alice")
        r = await client.post("/v1/mcp/inspect?project_id=prj_default", json=body, headers=ALICE)
        assert r.status_code == 200 and [t["name"] for t in r.json()] == ["whoami"], r.text
        r = await client.post(
            "/v1/mcp/inspect?project_id=prj_default", json=body, headers=BOB
        )  # Alice's does not help Bob
        assert r.status_code == 409, r.text


def test_connection_problems_are_found_inside_exception_groups():
    inner = ConnectionRequired("connect it")
    group = ExceptionGroup("outer", [ValueError("x"), ExceptionGroup("inner", [inner])])
    assert _connection_problem(group) is inner
    assert _connection_problem(ValueError("x")) is None
    wrapped = RuntimeError("tool failed")
    wrapped.__cause__ = inner
    assert _connection_problem(wrapped) is inner


async def test_the_runner_lets_only_deployments_and_admins_use_the_service_connection(app, client, monkeypatch):  # noqa: F811
    """CP-3: what the runner tells connectors about the service connection, by who starts the run and how."""
    from test_publish import greeter

    from agentcanvas.runtime.runner import Runner

    seen: list[bool | None] = []
    pump = Runner._pump

    async def spy(self, h, spec):
        seen.append(getattr(spec, "service_connection", None))
        return await pump(self, h, spec)

    monkeypatch.setattr(Runner, "_pump", spy)
    wf = (await client.post("/v1/projects/prj_default/workflows", json={"name": "g"}, headers=ALICE)).json()["id"]
    r = await client.put(f"/v1/workflows/{wf}", json={"ir": greeter(wf, "Hi")}, headers={**ALICE, "If-Match": "1"})
    assert r.status_code == 200, r.text
    for who in (ALICE, BOB):
        r = await client.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"who": "x"}}, headers=who)
        assert r.status_code == 200, r.text
    key = (await client.post(f"/v1/workflows/{wf}/publish", json={}, headers=ALICE)).json()
    r = await client.post(
        f"/v1/deployments/{key['deployment']['id']}/runs/wait", json={"input": {"who": "x"}},
        headers={"Authorization": f"Bearer {key['api_key']}"},
    )  # fmt: skip
    assert r.status_code == 200, r.text
    assert seen == [True, False, True]  # the admin's draft run, the builder's, the deployment's
