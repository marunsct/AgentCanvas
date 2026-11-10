"""Local (stdio) app connectors in the sandbox: the MCP server runs in the sandbox service (or a Docker container),
the client's messages are relayed to its stdio, and it gets the project's egress list and limits, for one run or
one conversation. The server here is `tiny_mcp_server.py` (standard library only); no real browser is needed."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import sys
from pathlib import Path

import mcp_types
import pytest
import uvicorn
from fastmcp import Client
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from test_catalog import MESSAGES, workflow

from agentcanvas.adapters.langchain.interpreter import abuild_graph
from agentcanvas.adapters.langchain.nodes import Resources
from agentcanvas.sandbox import mcp as sandbox_mcp
from agentcanvas.sandbox import service as sandbox_service
from agentcanvas.sandbox.egress import EgressProxy
from agentcanvas.sandbox.mcp import McpSandboxError, McpSessions, docker_mcp_command
from agentcanvas.sandbox.policy import McpPolicy
from agentcanvas.sandbox.runners import SandboxRunner
from agentcanvas.settings import Settings
from agentcanvas.testing import scripted, tool_call

TOKEN = "t" * 32
TINY = Path(__file__).with_name("tiny_mcp_server.py")


@contextlib.asynccontextmanager
async def serving(app, host: str = "127.0.0.1"):
    """An ASGI app on a real port (the relay is a WebSocket, which the in-process transports do not carry)."""
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=0, lifespan="off", log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.02)
    try:
        yield server.servers[0].sockets[0].getsockname()[1]
    finally:
        server.should_exit = True
        await task


@pytest.fixture
async def service():
    async with serving(sandbox_service.create_service(TOKEN)) as port:
        yield f"http://127.0.0.1:{port}"


@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    shutil.copy(TINY, root / "tiny.py")
    return root


@pytest.fixture
def sessions(monkeypatch):
    pool = McpSessions(limit=8)
    monkeypatch.setattr(sandbox_mcp, "SESSIONS", pool)
    yield pool


def remote(service: str, **extra) -> Settings:
    return Settings(script_execution="remote", sandbox_url=service, sandbox_token=TOKEN, sandbox_mcp=True, **extra)


def tiny(runner: SandboxRunner, env: dict | None = None):
    return runner.mcp_transport("tiny", sys.executable, ["tiny.py"], env or {})


async def text(client: Client, tool: str, args: dict | None = None) -> str:
    result = await client.call_tool(tool, args or {})
    first = result.content[0]
    assert isinstance(first, mcp_types.TextContent)
    return first.text


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z" if sys.platform == "linux" else True


# --------------------------------------------------------------------------------- the sandbox service ----
async def test_a_connector_runs_in_the_sandbox_with_only_its_own_environment(service, ws, monkeypatch):
    monkeypatch.setenv("AGENTCANVAS_SECRET_KEY", "the-api-key")  # the service's (and API's) own: never passed on
    transport = tiny(SandboxRunner(remote(service), ws, {}), {"TINY_TOKEN": "from-the-block"})
    async with Client(transport) as client:
        assert {t.name for t in await client.list_tools()} >= {"remember", "recall", "whoami"}
        me = json.loads(await text(client, "whoami"))
        assert me["token"] == "from-the-block" and me["secret"] == "" and me["proxy"] == ""
        assert me["pid"] != os.getpid()
        await text(client, "remember", {"text": "a"})
        assert json.loads(await text(client, "recall")) == ["a"]
    for _ in range(50):  # the connection ended: so did the process
        if not _alive(me["pid"]):
            break
        await asyncio.sleep(0.05)
    assert not _alive(me["pid"])


async def test_the_service_checks_the_token(service, ws):
    settings = remote(service).model_copy(update={"sandbox_token": "x" * 32})
    with pytest.raises(McpSandboxError, match=r"not reachable.*403"):
        async with McpSessions().once(lambda: Client(tiny(SandboxRunner(settings, ws, {})))):
            pass


async def test_a_program_that_does_not_start_says_why(service, ws):
    runner = SandboxRunner(remote(service), ws, {})
    pool = McpSessions()
    with pytest.raises(McpSandboxError, match="did not start in the sandbox"):
        async with pool.once(lambda: Client(runner.mcp_transport("gone", "no-such-program-xyz", [], {}))):
            pass


@pytest.mark.skipif(sys.platform != "linux", reason="memory is measured from /proc")
async def test_a_connector_over_its_memory_is_stopped(service, ws):
    (ws / "sandbox.json").write_text(json.dumps({"mcp": {"memory_mb": 128}}))
    transport = tiny(SandboxRunner(remote(service), ws, {}))
    with contextlib.suppress(Exception):  # the call itself fails: its server was stopped under it
        async with Client(transport) as client:
            await text(client, "grow", {"mb": 400})
            for _ in range(60):
                await asyncio.sleep(0.1)
                await text(client, "recall")
    assert "more than 128 MB" in transport.problem


async def test_a_connector_lives_no_longer_than_its_lifetime(service, ws):
    (ws / "sandbox.json").write_text(json.dumps({"mcp": {"lifetime_s": 1}}))
    transport = tiny(SandboxRunner(remote(service), ws, {}))
    with contextlib.suppress(Exception):
        async with Client(transport) as client:
            for _ in range(40):
                await asyncio.sleep(0.1)
                await text(client, "recall")
    assert "time limit" in transport.problem


async def test_a_connector_takes_a_sandbox_slot(ws, monkeypatch):
    """One call or connector at a time per sandbox: a second waits, then hears that every sandbox is busy."""
    monkeypatch.setattr(sandbox_service, "QUEUE_WAIT_S", 0.3)
    async with serving(sandbox_service.create_service(TOKEN)) as port:
        runner = SandboxRunner(remote(f"http://127.0.0.1:{port}"), ws, {})
        async with Client(tiny(runner)):
            with pytest.raises(McpSandboxError, match="busy"):
                async with McpSessions().once(lambda: Client(tiny(runner))):
                    pass


async def test_egress_goes_through_a_relay_that_holds_the_credential(service, ws, monkeypatch):
    """The server's proxy is a relay on 127.0.0.1 (browsers send no credential in a proxy URL), and the relay adds
    the signed list on the way to the egress proxy: listed hosts work, others are refused."""

    async def upstream(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 7\r\nConnection: close\r\n\r\nallowed")
        await writer.drain()
        writer.close()

    origin = await asyncio.start_server(upstream, "127.0.0.1", 0)
    port = origin.sockets[0].getsockname()[1]

    async def resolve(host: str, _port: int) -> list[str]:
        return ["127.0.0.1"]  # the test's hosts all live here

    proxy = EgressProxy("e" * 32, resolve=resolve, allow_private=True)
    proxy_port = await proxy.start("127.0.0.1", 0)
    try:
        (ws / "sandbox.json").write_text(json.dumps({"egress": [f"allowed.test:{port}"]}))
        settings = remote(service, egress_proxy=f"127.0.0.1:{proxy_port}", egress_secret="e" * 32)
        async with Client(tiny(SandboxRunner(settings, ws, {}))) as client:
            me = json.loads(await text(client, "whoami"))
            assert me["proxy"].startswith("http://127.0.0.1:") and "@" not in me["proxy"]
            assert me["upstream"] == ""  # the credential stays with the relay
            assert await text(client, "fetch", {"url": f"http://allowed.test:{port}/"}) == "allowed"
            refused = await text(client, "fetch", {"url": f"http://other.test:{port}/"})
            assert "403" in refused
    finally:
        await proxy.close()
        origin.close()


# ------------------------------------------------------------------------------------------- docker ----
def test_a_docker_connector_is_confined():
    limits = McpPolicy(memory_mb=768)
    server = sandbox_mcp.McpServer("b", "npx", ("-y", "@playwright/mcp@0.0.83"), (("KEY", "v"),))
    argv = docker_mcp_command("img", "ac-mcp-x", Path("/w"), server, limits, "/tmp/env", online=False)
    assert argv[argv.index("--network") + 1] == "none" and argv[argv.index("--user") + 1] == "65534:65534"
    assert argv[argv.index("--memory") + 1] == "768m" and argv[argv.index("--memory-swap") + 1] == "768m"
    for flag in ("--read-only", "--init", "--cap-drop", "--pids-limit", "--cpus"):
        assert flag in argv
    assert "/w:/ws:ro" in argv and argv[argv.index("--env-file") + 1] == "/tmp/env"
    assert "KEY=v" not in " ".join(argv)  # secrets go in the env file, not on the command line
    assert argv[-3:] == ["npx", "-y", "@playwright/mcp@0.0.83"]
    online = docker_mcp_command("img", "n", Path("/w"), server, limits, "/tmp/env", online=True)
    assert online[online.index("--network") + 1] == "agentcanvas-sandbox"


def test_a_connector_variable_that_an_env_file_cannot_carry_is_refused():
    with pytest.raises(McpSandboxError, match="line break"):
        sandbox_mcp._env_lines({"A": "x\nB=y"})  # would set B too
    for name in ("A=B", "#A", "A B", ""):
        with pytest.raises(McpSandboxError, match="not an environment variable name"):
            sandbox_mcp._env_lines({name: "v"})
    assert sandbox_mcp._env_lines({"TOKEN": "a=b c"}) == "TOKEN=a=b c\n"


def _docker_ready() -> bool:
    import subprocess

    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "info"], capture_output=True, check=False, timeout=20).returncode == 0


@pytest.mark.skipif(not _docker_ready(), reason="needs a Docker daemon (CI has one)")
async def test_a_docker_connector_runs_offline_and_is_removed(tmp_path):
    import subprocess

    ws = tmp_path / "ws"
    ws.mkdir()
    shutil.copy(TINY, ws / "tiny.py")
    for p in [ws, *ws.parents][:4]:  # pytest's tmp dirs are private; the sandbox user is nobody
        p.chmod(0o755)
    (ws / "tiny.py").chmod(0o644)
    settings = Settings(script_execution="docker", sandbox_image="python:3.12-slim", sandbox_mcp=True)
    transport = SandboxRunner(settings, ws, {}).mcp_transport("tiny", "python3", ["tiny.py"], {"TINY_TOKEN": "k"})
    try:
        async with McpSessions().once(lambda: Client(transport)) as client:
            me = json.loads(await text(client, "whoami"))
            assert me["token"] == "k"
            assert "error" in await text(client, "fetch", {"url": "http://example.com/"})  # no network at all
    except McpSandboxError as exc:
        if "mounts denied" in str(exc):
            pytest.skip("this Docker may not mount pytest's temporary folder: run with --basetemp under $HOME")
        raise
    left = subprocess.run(
        ["docker", "ps", "-aq", "--filter", "label=agentcanvas.mcp=1"], capture_output=True, text=True, check=False
    )
    assert left.stdout.strip() == ""


# ---------------------------------------------------------------------- one sandbox per run or thread ----
TINY_BLOCK = {
    "transport": "stdio",
    "command": sys.executable,
    "args": ["tiny.py"],
    "env": {"TINY_TOKEN": {"$secret": "TINY"}},
}


def connector_wf(session: str = "run"):
    return workflow(
        channels=[MESSAGES],
        resources=[
            ("model", "model.chat", {"model": "fake:scripted"}),
            ("tiny", "mcp.server", TINY_BLOCK | {"session": session}),
        ],
        nodes=[("agent", "core.agent", {})],
        flow=[("START", "agent"), ("agent", "END")],
        wiring=[("model", "agent", "model"), ("tiny", "agent", "tools")],
    )


def run_cfg(thread: str, run: str) -> dict:
    return {"configurable": {"thread_id": thread}, "metadata": {"run_id": run}}


def resources(settings: Settings, ws: Path, *replies) -> Resources:
    res = Resources(overrides={"model": scripted(*replies)}, secrets={"TINY": "s3cret"}, workspace=ws)
    res.mcp_sandbox = SandboxRunner(settings, ws, {})
    return res


def tool_texts(out: dict) -> list[str]:
    def flat(content) -> str:
        if isinstance(content, list):
            return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
        return str(content)

    return [flat(m.content) for m in out["messages"] if m.type == "tool"]


def two_calls(first: tuple[str, dict], second: tuple[str, dict]) -> list:
    return [tool_call(first[0], first[1], "c1"), tool_call(second[0], second[1], "c2"), AIMessage(content="done")]


async def test_a_runs_tool_calls_share_its_sandbox_which_stops_when_the_run_ends(service, ws, sessions):
    settings = remote(service)
    res = resources(settings, ws, *two_calls(("remember", {"text": "n1"}), ("whoami", {})))
    graph = await abuild_graph(connector_wf(), res, checkpointer=InMemorySaver())
    out = await graph.ainvoke({"messages": [("user", "go")]}, run_cfg("t1", "run_1"))
    me = json.loads(tool_texts(out)[1])
    assert me["token"] == "s3cret"  # the block's secret, resolved into its server's environment
    assert len(sessions) == 1 and _alive(me["pid"])
    await sessions.end("run", "run_1")
    assert len(sessions) == 0
    for _ in range(50):
        if not _alive(me["pid"]):
            break
        await asyncio.sleep(0.05)
    assert not _alive(me["pid"])


async def test_each_run_gets_a_fresh_sandbox_and_a_thread_scoped_one_keeps_its_state(service, ws, sessions):
    settings = remote(service)
    for session, expected in (("run", "[]"), ("thread", '["n1"]')):
        res = resources(settings, ws, tool_call("remember", {"text": "n1"}), AIMessage(content="ok"))
        graph = await abuild_graph(connector_wf(session), res, checkpointer=InMemorySaver())
        await graph.ainvoke({"messages": [("user", "go")]}, run_cfg(f"t-{session}", "r1"))
        await sessions.end("run", "r1")  # what the runner does when a run ends
        res._overrides["model"] = scripted(tool_call("recall", {}), AIMessage(content="ok"))
        graph = await abuild_graph(connector_wf(session), res, checkpointer=InMemorySaver())  # tools listed once
        out = await graph.ainvoke({"messages": [("user", "go")]}, run_cfg(f"t-{session}", "r2"))
        assert tool_texts(out)[-1] == expected, session
        await sessions.aclose()


async def test_a_sandbox_idle_too_long_is_stopped(service, ws, sessions, monkeypatch):
    monkeypatch.setattr(sandbox_mcp, "REAP_S", 0.05)
    (ws / "sandbox.json").write_text(json.dumps({"mcp": {"idle_s": 0.2}}))
    runner = SandboxRunner(remote(service), ws, {})
    async with sessions.use(("thread", "t", "c"), lambda: Client(tiny(runner))) as client:
        await text(client, "recall")
    assert len(sessions) == 1
    for _ in range(40):
        await asyncio.sleep(0.05)
        if not len(sessions):
            break
    assert len(sessions) == 0


async def test_a_server_runs_a_bounded_number_of_connector_sandboxes(service, ws, sessions):
    sessions.limit = 1
    runner = SandboxRunner(remote(service), ws, {})
    async with sessions.use(("run", "a", "c"), lambda: Client(tiny(runner))):
        with pytest.raises(McpSandboxError, match="1 app connector sandboxes are in use"):
            async with sessions.use(("run", "b", "c"), lambda: Client(tiny(runner))):
                pass
    async with sessions.use(("run", "b", "c"), lambda: Client(tiny(runner))) as client:  # the idle one made room
        assert await text(client, "recall") == "[]"
    assert len(sessions) == 1
    await sessions.aclose()


async def test_the_hosted_edition_never_starts_a_connector_on_the_server(ws):
    res = Resources(workspace=ws)
    res.public_only = True  # the hosted edition, without connector sandboxes
    config = {"transport": "stdio", "command": sys.executable, "args": ["tiny.py"]}
    with pytest.raises(McpSandboxError, match="only in the sandbox"):
        await res.load_mcp("tiny", config)


def test_connector_sandboxes_need_a_sandboxing_server():
    from agentcanvas.security.auth import check_auth_settings

    with pytest.raises(RuntimeError, match="AGENTCANVAS_SANDBOX_MCP"):
        check_auth_settings(Settings(sandbox_mcp=True, script_execution="inprocess"))
    check_auth_settings(Settings(sandbox_mcp=True, script_execution="docker"))


async def test_the_server_runs_connectors_in_the_sandbox_and_stops_them_with_the_run(settings, service, sessions):
    from types import SimpleNamespace

    from agentcanvas.api.main import create_app

    on = settings.model_copy(update={
        "script_execution": "remote", "sandbox_url": service, "sandbox_token": TOKEN, "sandbox_mcp": True,
        "allow_local_mcp": True, "sandbox_mcp_sessions": 3,
    })  # fmt: skip
    app = create_app(on)
    async with app.router.lifespan_context(app):
        res = app.state.projects.resources("prj_default")
        assert res.mcp_sandbox is not None and res.mcp_sandbox is res.script_runner
        assert sessions.limit == 3
        shutil.copy(TINY, res.workspace / "tiny.py")
        async with sessions.use(("run", "run_x", "c"), lambda: Client(tiny(res.mcp_sandbox))) as client:
            assert await text(client, "recall") == "[]"
        handle = SimpleNamespace(run_id="run_x")
        for hook in app.state.runner.finish_hooks:
            if getattr(hook, "__name__", "") == "stop_connectors":
                await hook(handle, "success", None, [])
        assert len(sessions) == 0
    off = create_app(settings)
    async with off.router.lifespan_context(off):  # off by default: nothing changes
        assert off.state.projects.resources("prj_default").mcp_sandbox is None


# ------------------------------------------------------------------------- the hosted edition (E046) ----
PLAYWRIGHT = {"transport": "stdio", "command": "npx", "args": ["-y", "@playwright/mcp@0.0.83", "--headless"]}


def agent_with(config: dict):
    model = ("model", "model.chat", {"model": "fake:scripted"})
    return workflow(
        channels=[MESSAGES], resources=[model, ("c", "mcp.server", config)], nodes=[("agent", "core.agent", {})],
        flow=[("START", "agent"), ("agent", "END")], wiring=[("model", "agent", "model"), ("c", "agent", "tools")],
    )  # fmt: skip


def codes(config: dict, **kw) -> set[str]:
    from agentcanvas.validation.engine import validate

    return {d.code for d in validate(agent_with(config), allow_local_mcp=True, **kw) if d.severity == "error"}


def test_the_hosted_edition_runs_only_the_sandboxs_own_connectors_and_only_there():
    custom = {"transport": "stdio", "command": "npx", "args": ["-y", "some-server@1.0.0"]}
    assert "E046" in codes(PLAYWRIGHT, edition="hosted")  # not in the sandbox: refused
    assert "E046" not in codes(PLAYWRIGHT, edition="hosted", sandbox_mcp=True)
    assert "E046" in codes(custom, edition="hosted", sandbox_mcp=True)
    assert "E046" not in codes(custom, sandbox_mcp=True) and "E046" not in codes(custom)  # one company's own server
    assert "E046" not in codes({"url": "https://mcp.example.com/mcp"}, edition="hosted")


async def test_the_run_gate_refuses_what_the_hosted_edition_does_not_run():
    from agentcanvas.errors import WorkflowHasErrors
    from agentcanvas.services.run_gate import RunGate

    custom = {"transport": "stdio", "command": "npx", "args": ["-y", "some-server@1.0.0"]}
    wf = agent_with(custom)
    hosted = Settings(edition="hosted", allow_local_mcp=True, sandbox_mcp=True, script_execution="remote")
    with pytest.raises(WorkflowHasErrors, match="E046"):
        await RunGate(hosted)(wf)
    await RunGate(hosted.model_copy(update={"edition": "dev"}))(wf)  # one company's own server runs it


async def test_browsing_a_connector_the_hosted_edition_does_not_run_is_refused(app, api_client):
    app.state.settings.edition = "hosted"
    app.state.settings.allow_local_mcp = True
    try:
        body = {"name": "c", "config": PLAYWRIGHT}
        r = await api_client.post("/v1/mcp/inspect?project_id=prj_default", json=body)
        assert r.status_code == 403 and "sandbox" in r.text  # this server does not run connectors in the sandbox
    finally:
        app.state.settings.edition = "dev"
        app.state.settings.allow_local_mcp = False


# ------------------------------------------------------------------- connectors the image comes with ----
def fake_servers(root: Path, name: str = "@ac-test/tiny", version: str = "1.0.0") -> Path:
    """An /opt/mcp-servers look-alike whose `node` runs Python, so the tiny server stands in for a Node.js one."""
    folder = root / "node_modules" / name
    folder.mkdir(parents=True)
    (folder / "package.json").write_text(json.dumps({"name": name, "version": version, "bin": {"tiny": "tiny.py"}}))
    shutil.copy(TINY, folder / "tiny.py")
    (root / "bin").mkdir()
    (root / "bin" / "node").write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    (root / "bin" / "node").chmod(0o755)
    return root


def test_npx_starts_the_installed_copy_of_that_exact_version(tmp_path):
    from agentcanvas.sandbox.mcp_child import installed

    servers = str(fake_servers(tmp_path / "servers"))
    node, program = f"{servers}/bin/node", f"{servers}/node_modules/@ac-test/tiny/tiny.py"
    assert installed(["npx", "-y", "@ac-test/tiny@1.0.0", "--headless"], servers) == [node, program, "--headless"]
    for argv in (
        ["npx", "-y", "@ac-test/tiny@1.0.1"],  # another version: npx as asked (offline, it fails)
        ["npx", "-y", "@ac-test/tiny"], ["npx", "-y", "../../etc@1.0.0"], ["uvx", "tiny==1.0.0"], ["npx", "-y"],
    ):  # fmt: skip
        assert installed(argv, servers) == argv, argv
    assert installed(["npx", "-y", "@ac-test/tiny@1.0.0"], "") == ["npx", "-y", "@ac-test/tiny@1.0.0"]


async def test_a_gallery_style_connector_starts_from_the_images_copy(service, ws, tmp_path, monkeypatch):
    monkeypatch.setenv("AC_MCP_SERVERS", str(fake_servers(tmp_path / "servers")))  # what the image sets
    runner = SandboxRunner(remote(service), ws, {})
    transport = runner.mcp_transport("tiny", "npx", ["-y", "@ac-test/tiny@1.0.0"], {})
    async with McpSessions().once(lambda: Client(transport)) as client:
        assert await text(client, "recall") == "[]"
