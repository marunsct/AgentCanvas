"""Workspace code reaches exactly the hosts `sandbox.json` allows (FR-X-08), through the egress proxy.

The `remote` mode end to end on this machine: the sandbox service runs the real child process, which may connect only
to the egress proxy, and the proxy admits only the call's signed list. The `docker` mode builds the same from an
internal network (its command line is checked here; `test_sandbox_docker.py` runs it when Docker is there). The
modes that cannot restrict the network refuse an egress list in staging and production.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from test_sandbox_service import TOKEN

from agentcanvas.sandbox import runners
from agentcanvas.sandbox.egress import EgressProxy
from agentcanvas.sandbox.runners import SandboxRunner, ScriptError, docker_command
from agentcanvas.sandbox.service import create_service
from agentcanvas.settings import Settings

SECRET = "egress-runs-secret-0123456789"
NET = """
import socket
import urllib.request


def run(state):
    out = {}
    try:
        out["allowed"] = urllib.request.urlopen(state["ok"], timeout=5).read().decode()
    except Exception as e:
        out["allowed"] = f"error {e}"
    try:
        urllib.request.urlopen(state["no"], timeout=5)
        out["other"] = "reached"
    except Exception as e:
        out["other"] = str(e)
    try:
        socket.create_connection(("127.0.0.1", state["port"]), timeout=2)
        out["direct"] = "reached"
    except Exception as e:
        out["direct"] = type(e).__name__
    return {"out": out}
"""


@pytest.fixture
async def upstream():
    async def answer(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 7\r\nConnection: close\r\n\r\nallowed")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(answer, "127.0.0.1", 0)
    yield server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()


async def test_remote_code_reaches_only_the_listed_hosts(tmp_path, monkeypatch, upstream):
    proxy = EgressProxy(SECRET, timeout_s=5)
    port = await proxy.start("127.0.0.1", 0)
    try:
        ws = tmp_path / "ws"
        (ws / "scripts").mkdir(parents=True)
        (ws / "scripts" / "net.py").write_text(NET)
        (ws / "sandbox.json").write_text(json.dumps({"egress": [f"127.0.0.1:{upstream}"]}))
        monkeypatch.setattr(runners, "REMOTE_TRANSPORT", httpx.ASGITransport(app=create_service(TOKEN)))
        settings = Settings(
            script_execution="remote", sandbox_url="http://sandbox", sandbox_token=TOKEN,
            egress_proxy=f"127.0.0.1:{port}", egress_secret=SECRET,
        )  # fmt: skip
        runner = SandboxRunner(settings, ws, {})
        state = {"ok": f"http://127.0.0.1:{upstream}/", "no": f"http://127.0.0.2:{upstream}/", "port": upstream}
        out = (await runner("scripts.net:run")(state))["out"]
        assert out["allowed"] == "allowed", out
        assert "403" in out["other"] or "Forbidden" in out["other"], out  # not on the list: the proxy refuses
        assert out["direct"] == "PermissionError", out  # around the proxy: no connection at all

        (ws / "sandbox.json").write_text("{}")  # no list: no network, not even the proxy
        out = (await runner("scripts.net:run")(state))["out"]
        assert out["allowed"].startswith("error") and out["direct"] == "PermissionError", out
    finally:
        await proxy.close()


async def test_remote_without_a_proxy_configured_says_what_to_set(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    (ws / "scripts").mkdir(parents=True)
    (ws / "scripts" / "net.py").write_text(NET)
    (ws / "sandbox.json").write_text(json.dumps({"egress": ["api.github.com"]}))
    monkeypatch.setattr(runners, "REMOTE_TRANSPORT", httpx.ASGITransport(app=create_service(TOKEN)))
    settings = Settings(script_execution="remote", sandbox_url="http://sandbox", sandbox_token=TOKEN)
    with pytest.raises(ScriptError, match="AGENTCANVAS_EGRESS_PROXY"):
        await SandboxRunner(settings, ws, {})("scripts.net:run")({})


@pytest.mark.parametrize("env", ["staging", "prod"])
async def test_a_mode_that_cannot_restrict_the_network_refuses_a_list_in_production(tmp_path, env):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "h.py").write_text("def f(state):\n    return {'x': 1}\n")
    (ws / "sandbox.json").write_text(json.dumps({"egress": ["api.github.com"]}))
    runner = SandboxRunner(Settings(env=env, script_execution="subprocess", secret_key="k" * 64), ws, {})
    with pytest.raises(ScriptError, match="cannot restrict"):
        await runner("h:f")({})
    (ws / "sandbox.json").write_text("{}")  # without a list it runs, offline
    assert await runner("h:f")({}) == {"x": 1}


def test_docker_containers_with_a_list_join_the_egress_network_only():
    offline = docker_command("python:3.12-slim", __import__("pathlib").Path("/ws"), "m:f")
    assert offline[offline.index("--network") + 1] == "none" and not any("PROXY" in a for a in offline)
    online = docker_command(
        "python:3.12-slim", __import__("pathlib").Path("/ws"), "m:f", proxy="http://u:p@agentcanvas-egress:3128"
    )
    assert online[online.index("--network") + 1] == "agentcanvas-sandbox"
    assert "HTTPS_PROXY=http://u:p@agentcanvas-egress:3128" in online and "NO_PROXY=" in online
    assert "--cap-drop" in online and "--read-only" in online  # the rest of the confinement is unchanged


def _docker_ready() -> bool:
    import shutil
    import subprocess

    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "info"], capture_output=True, check=False, timeout=20).returncode == 0


UPSTREAM = (
    "import http.server as h\n"
    "class A(h.BaseHTTPRequestHandler):\n"
    "    def do_GET(self):\n"
    "        self.send_response(200); self.send_header('Content-Length', '7'); self.end_headers()\n"
    "        self.wfile.write(b'allowed')\n"
    "h.HTTPServer(('0.0.0.0', 8080), A).serve_forever()\n"
)


@pytest.mark.skipif(not _docker_ready(), reason="needs a Docker daemon (CI has one)")
async def test_docker_code_reaches_only_the_listed_hosts(tmp_path):
    """The real thing: the sandbox container sits on the internal network, the proxy container is the only way out.
    The allowed host is a container on Docker's default bridge (named by its address, as an admin would)."""
    import subprocess
    import uuid

    from agentcanvas.sandbox import docker_egress

    name = f"ac-test-upstream-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", name, "python:3.12-slim", "python", "-c", UPSTREAM],
        capture_output=True, check=True, timeout=120,
    )  # fmt: skip
    ws = tmp_path / "ws"
    try:
        address = '{{(index .NetworkSettings.Networks "bridge").IPAddress}}'
        ip = subprocess.run(
            ["docker", "inspect", "-f", address, name], capture_output=True, text=True, check=True
        ).stdout.strip()
        (ws / "scripts").mkdir(parents=True)
        (ws / "scripts" / "net.py").write_text(
            NET.replace('("127.0.0.1", state["port"])', '(state["host"], state["port"])')
        )
        (ws / "sandbox.json").write_text(json.dumps({"egress": [f"{ip}:8080"]}))
        for p in [ws, *ws.parents][:4]:  # pytest's tmp dirs are private; the sandbox user is nobody
            p.chmod(0o755)
        settings = Settings(script_execution="docker", sandbox_image="python:3.12-slim", secret_key="d" * 64)
        runner = SandboxRunner(settings, ws, {})
        state = {"ok": f"http://{ip}:8080/", "no": "http://example.com/", "host": ip, "port": 8080}
        for _ in range(20):  # the upstream needs a moment to listen
            try:
                out = (await runner("scripts.net:run")(state))["out"]
            except ScriptError as exc:
                if "mounts denied" in str(exc):
                    pytest.skip("this Docker may not mount pytest's temporary folder: run with --basetemp under $HOME")
                raise
            if out["allowed"] == "allowed":
                break
            await asyncio.sleep(0.5)
        assert out["allowed"] == "allowed", out
        assert "403" in out["other"] or "Forbidden" in out["other"], out
        assert out["direct"] != "reached", out  # the internal network has no route anywhere else
    finally:
        subprocess.run(["docker", "rm", "-f", name, docker_egress.CONTAINER], capture_output=True, check=False)
        subprocess.run(["docker", "network", "rm", docker_egress.NETWORK], capture_output=True, check=False)
        docker_egress._ready.clear()


async def test_settings_reads_and_writes_the_sandbox_policy(app, api_client):
    """Settings > Sandbox: the policy as a form, and what this server does with it (here: in-process, dev)."""
    url = "/v1/projects/prj_default/sandbox"
    first = (await api_client.get(url)).json()
    assert first["policy"] == {"egress": [], "inject": [], "timeout_s": 30.0, "memory_mb": 512}
    assert first["mode"] == "inprocess" and not first["enforces_egress"] and not first["refuses_egress"]
    saved = await api_client.put(url, json={"egress": ["api.github.com", "*.googleapis.com:443"], "timeout_s": 60})
    assert saved.status_code == 200, saved.text
    out = saved.json()
    assert out["policy"]["egress"] == ["api.github.com", "*.googleapis.com:443"] and out["policy"]["timeout_s"] == 60
    assert [p["code"] for p in out["problems"]] == ["W082"]  # in-process code cannot be limited: said, in dev
    text = (await api_client.get("/v1/projects/prj_default/files/sandbox.json")).json()["content"]
    assert json.loads(text) == {"egress": ["api.github.com", "*.googleapis.com:443"], "timeout_s": 60.0}
    for bad in ({"egress": ["https://api.github.com/x"]}, {"timeout_s": 0}, {"egres": []}, {"egress": ["x"] * 101}):
        assert (await api_client.put(url, json=bad)).status_code == 422, bad
    await api_client.put("/v1/projects/prj_default/files/sandbox.json", json={"content": "{nope"})
    broken = (await api_client.get(url)).json()
    assert broken["policy"] is None and "not valid JSON" in broken["error"]
    app.state.projects.settings.env = "prod"
    try:
        assert (await api_client.put(url, json={"egress": ["api.github.com"]})).json()["refuses_egress"] is True
    finally:
        app.state.projects.settings.env = "test"
