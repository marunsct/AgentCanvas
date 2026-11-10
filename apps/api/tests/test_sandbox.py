"""Sprint 7 sandbox policy (FR-X-08, basic) and spike SP-2: workspace code in a LangSmith Sandbox.

There are no LangSmith credentials in CI, so the LangSmith mode runs against a fake client that keeps the sandbox
filesystem in a temp directory and executes commands locally; it records every call the real client would get.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path

import pytest
from langsmith.sandbox import CommandTimeoutError, ExecutionResult, ResourceNotFoundError

from agentcanvas.sandbox.policy import PolicyError, check_policy, parse_policy, proxy_config
from agentcanvas.sandbox.runners import SandboxRunner, ScriptError
from agentcanvas.settings import Settings
from agentcanvas.workspace import Workspace

P = "/v1/projects/prj_default"

POLICY = {
    "egress": ["pypi.org:443"],
    "inject": [
        {
            "name": "crm",
            "hosts": ["api.crm.example.com"],
            "headers": {"Authorization": "Bearer {{ secret.CRM_TOKEN }}", "X-Client": "agentcanvas"},
        }
    ],
    "timeout_s": 5,
    "memory_mb": 256,
}


@pytest.mark.parametrize("entry", ["169.254.169.254", "169.254.169.254:80", "[fe80::1]:443", "[fd00:ec2::254]"])
def test_a_list_naming_a_metadata_address_is_refused(entry):
    with pytest.raises(PolicyError, match="metadata"):
        parse_policy(json.dumps({"egress": [entry]}))
    assert parse_policy(json.dumps({"egress": ["10.0.0.5:5432", "api.github.com"]})).egress  # the operator decides


def test_policy_parse_and_proxy_config(tmp_path):
    policy = parse_policy(json.dumps(POLICY))
    assert policy.secret_names() == {"CRM_TOKEN"}
    cfg = proxy_config(policy, {"CRM_TOKEN": "tok-123"})
    assert cfg == {
        "rules": [
            {
                "name": "crm",
                "match_hosts": ["api.crm.example.com"],
                "match_paths": [],
                "headers": [
                    {"name": "Authorization", "type": "opaque", "value": "Bearer tok-123"},  # write-only in LangSmith
                    {"name": "X-Client", "type": "plaintext", "value": "agentcanvas"},
                ],
            }
        ],
        "access_control": {"allow_list": ["pypi.org:443", "api.crm.example.com:443"]},  # inject hosts reachable
    }
    assert proxy_config(parse_policy("{}"), {}) == {"rules": [], "access_control": {"allow_list": []}}  # offline
    with pytest.raises(PolicyError, match="lacks"):
        proxy_config(policy, {})
    with pytest.raises(PolicyError, match="not a hostname"):
        parse_policy(json.dumps({"inject": [{"name": "x", "hosts": ["https://a.b"], "headers": {"A": "b"}}]}))
    with pytest.raises(PolicyError, match="timeout_s"):
        parse_policy('{"timeout_s": 0}')
    with pytest.raises(PolicyError, match="Extra inputs"):
        parse_policy('{"egres": []}')

    ws = Workspace(tmp_path / "w").ensure()
    assert check_policy(ws.root, "subprocess", set()) == []  # no sandbox.json: nothing to say
    ws.write("sandbox.json", json.dumps({**POLICY, "egress": ["*:443"]}))
    codes = {p["code"] for p in check_policy(ws.root, "docker", set())}
    assert codes == {"E083", "W083", "E085"}  # unknown secret, inject is LangSmith's only, "*" the proxy cannot match
    assert "W074" in {p["code"] for p in check_policy(ws.root, "langsmith", {"CRM_TOKEN"})}  # allows every host
    assert "W082" in {p["code"] for p in check_policy(ws.root, "subprocess", set())}  # cannot restrict: a warning
    assert "E084" in {p["code"] for p in check_policy(ws.root, "inprocess", set(), env="prod")}  # ... refused there
    ws.write("sandbox.json", json.dumps(POLICY))
    assert check_policy(ws.root, "langsmith", {"CRM_TOKEN"}) == []
    ws.write("sandbox.json", "{nope")
    assert [p["code"] for p in check_policy(ws.root, "langsmith", set())] == ["E082"]


@pytest.mark.parametrize("entry", ["*", "*:443", "~.*\\.example\\.com"])
@pytest.mark.parametrize("mode", ["docker", "remote"])
async def test_langsmith_only_egress_entries_are_refused_where_the_proxy_cannot_match_them(tmp_path, mode, entry):
    """RT-17: "*" and "~regex" passed the policy check, but the egress proxy matches hosts and *.domains only, so in
    docker and remote modes such a list silently allowed nothing (and W074 said it allowed everything)."""
    ws = Workspace(tmp_path / "ws")
    ws.write("sandbox.json", json.dumps({"egress": [entry]}))
    ws.write("h.py", "def f(state):\n    return {'x': 1}\n")
    codes = {p["code"] for p in check_policy(ws.root, mode, set())}
    assert "E085" in codes and "W074" not in codes
    runner = SandboxRunner(Settings(env="test", script_execution=mode, secret_key="k" * 64), ws.root, {})
    with pytest.raises(ScriptError, match="LangSmith's syntax"):
        await runner("h:f")({})


# --------------------------------------------------------------------------------------- fake LangSmith ----
class FakeBox:
    def __init__(self, name: str, root: Path):
        self.name, self.root = name, root
        self.writes: list[str] = []
        self.gone = False  # stopped after its idle TTL and deleted

    def _local(self, text: str) -> str:
        return re.sub(r"(?<![\w/])/(sandbox|ws)\b", lambda m: f"{self.root}/{m.group(1)}", text)

    async def write(self, path: str, content: str | bytes) -> None:
        if self.gone:
            raise ResourceNotFoundError(f"sandbox '{self.name}' not found", resource_type="sandbox")
        self.writes.append(path)
        local = Path(self._local(path))
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(content.encode() if isinstance(content, str) else content)

    async def run(self, command: str, *, timeout: int = 60, cwd: str | None = None, **_: object) -> ExecutionResult:
        if self.gone:
            raise ResourceNotFoundError(f"sandbox '{self.name}' not found", resource_type="sandbox")
        (self.root / "ws").mkdir(parents=True, exist_ok=True)
        env = {"PATH": os.environ["PATH"], "HOME": str(self.root)}  # like the real sandbox: no host secrets
        proc = await asyncio.create_subprocess_exec(
            "bash", "-c", self._local(command), cwd=self._local(cwd) if cwd else str(self.root), env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )  # fmt: skip
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise CommandTimeoutError("command timed out", timeout) from None
        return ExecutionResult(stdout=out.decode(), stderr=err.decode(), exit_code=proc.returncode or 0)


class FakeClient:
    def __init__(self, base: Path):
        self.base = base
        self.created: list[dict] = []
        self.deleted: list[str] = []
        self.updated: list[tuple[str, dict]] = []
        self.boxes: dict[str, FakeBox] = {}
        self.closed = False

    async def create_sandbox(self, **kw: object) -> FakeBox:
        self.created.append(kw)
        name = str(kw["name"])
        self.boxes[name] = FakeBox(name, self.base / name)
        return self.boxes[name]

    async def update_sandbox(self, name: str, *, proxy_config: dict) -> FakeBox:
        self.updated.append((name, proxy_config))
        return self.boxes[name]

    async def delete_sandbox(self, name: str) -> None:
        self.deleted.append(name)

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def fake_langsmith(tmp_path, monkeypatch) -> FakeClient:
    client = FakeClient(tmp_path / "boxes")
    monkeypatch.setattr("agentcanvas.sandbox.langsmith._client", lambda settings: client)
    return client


async def test_langsmith_runner_uploads_once_and_follows_policy_changes(tmp_path, fake_langsmith):
    ws = Workspace(tmp_path / "prj").ensure()
    ws.write(
        "scripts/add.py", "import os\ndef run(state):\n    return {'n': state['n'] + 1, 'env': sorted(os.environ)}\n"
    )
    ws.write("scripts/slow.py", "import time\ndef run(state):\n    time.sleep(5)\n    return {}\n")
    ws.write("sandbox.json", json.dumps(POLICY))
    secrets = {"CRM_TOKEN": "tok-123"}
    runner = SandboxRunner(Settings(script_execution="langsmith", langsmith_snapshot="py312"), ws.root, secrets)

    out = await runner("scripts.add:run")({"n": 1})
    assert out["n"] == 2 and not any("TOKEN" in k or "KEY" in k for k in out["env"])
    [created] = fake_langsmith.created
    assert created["snapshot"] == "py312" and created["mem_bytes"] == 256 * 1024 * 1024
    assert created["idle_ttl_seconds"] == 600 and created["proxy_config"]["rules"][0]["headers"][0]["type"] == "opaque"
    box = fake_langsmith.boxes[created["name"]]
    uploaded = box.root / "ws"
    assert not any("tok-123" in p.read_text(errors="ignore") for p in uploaded.rglob("*") if p.is_file())

    assert (await runner("scripts.add:run")({"n": 5}))["n"] == 6
    assert box.writes.count("/sandbox/ws.tgz") == 1  # the workspace is uploaded once, not per call
    ws.write("scripts/add.py", "def run(state):\n    return {'n': state['n'] * 10}\n")
    assert (await runner("scripts.add:run")({"n": 5})) == {"n": 50}
    assert box.writes.count("/sandbox/ws.tgz") == 2 and len(fake_langsmith.created) == 1  # re-upload, same box

    secrets["CRM_TOKEN"] = "tok-456"  # a secret change patches the proxy of the warm sandbox
    await runner("scripts.add:run")({"n": 1})
    [(name, patched)] = fake_langsmith.updated
    assert name == created["name"] and patched["rules"][0]["headers"][0]["value"] == "Bearer tok-456"
    assert len(fake_langsmith.created) == 1
    ws.write("sandbox.json", json.dumps({**POLICY, "memory_mb": 1024}))  # a new size needs a new sandbox
    await runner("scripts.add:run")({"n": 1})
    assert len(fake_langsmith.created) == 2 and fake_langsmith.deleted == [created["name"]]
    assert fake_langsmith.created[1]["mem_bytes"] == 1024 * 1024 * 1024

    ws.write("sandbox.json", json.dumps({**POLICY, "memory_mb": 1024, "timeout_s": 1}))
    with pytest.raises(TimeoutError, match="longer than 1 s"):
        await runner("scripts.slow:run")({})
    del secrets["CRM_TOKEN"]
    with pytest.raises(ScriptError, match="lacks"):
        await runner("scripts.add:run")({"n": 1})
    ws.write("sandbox.json", "{broken")
    with pytest.raises(ScriptError, match="not valid JSON"):
        await runner("scripts.add:run")({"n": 1})

    await runner.aclose()
    assert fake_langsmith.closed and len(fake_langsmith.deleted) == 2


async def test_langsmith_runner_replaces_an_expired_sandbox(tmp_path, fake_langsmith):
    ws = Workspace(tmp_path / "prj").ensure()
    ws.write("scripts/add.py", "def run(state):\n    return {'n': state['n'] + 1}\n")
    runner = SandboxRunner(Settings(script_execution="langsmith"), ws.root, {})
    assert (await runner("scripts.add:run")({"n": 1}))["n"] == 2
    [first] = fake_langsmith.boxes.values()
    first.gone = True  # idle TTL: the remote sandbox stopped and was deleted
    assert (await runner("scripts.add:run")({"n": 2}))["n"] == 3
    assert len(fake_langsmith.created) == 2
    second = fake_langsmith.boxes[fake_langsmith.created[1]["name"]]
    assert "/sandbox/child.py" in second.writes and "/sandbox/ws.tgz" in second.writes  # set up from scratch
    second.gone = True
    fake_langsmith.create_sandbox = lambda **kw: _gone(kw)  # type: ignore[method-assign]
    with pytest.raises(ResourceNotFoundError):  # retried once, then the error surfaces
        await runner("scripts.add:run")({"n": 3})


async def _gone(kw: dict) -> FakeBox:
    box = FakeBox(str(kw["name"]), Path("/nonexistent"))
    box.gone = True
    return box


async def test_langsmith_mode_end_to_end(app, api_client, fake_langsmith):
    """Script nodes, custom nodes and event handlers all run in the LangSmith sandbox; the catalog lints the policy."""
    app.state.settings.script_execution = "langsmith"
    app.state.projects._resources.clear()
    await api_client.put(f"{P}/files/sandbox.json", json={"content": json.dumps(POLICY)})
    problems = (await api_client.get(f"{P}/workspace/catalog")).json()["problems"]
    assert [p["code"] for p in problems if p.get("code")] == ["E083"]
    assert (await api_client.put(f"{P}/secrets/CRM_TOKEN", json={"value": "tok-e2e"})).status_code == 204
    assert not [p for p in (await api_client.get(f"{P}/workspace/catalog")).json()["problems"] if p.get("code")]

    await api_client.put(
        f"{P}/files/scripts/rev.py", json={"content": "def run(state):\n    return {'out': state['text'][::-1]}\n"}
    )
    await api_client.put(
        f"{P}/files/events/hooks.py",
        json={
            "content": "from agentcanvas.sdk import on\n\n\n@on('test.ping')\n"
            "def pong(event, act):\n    act.emit('test.pong', {'n': event.data['n'] + 1})\n"
        },
    )
    ir = {
        "name": "Reverse",
        "state": {"channels": [{"name": "text"}, {"name": "out"}]},
        "nodes": [{"id": "n_r", "type": "core.script", "name": "rev", "config": {"import_path": "scripts.rev:run"}}],
        "edges": [
            {"id": "e1", "kind": "flow", "source": {"node": "START"}, "target": {"node": "n_r"}},
            {"id": "e2", "kind": "flow", "source": {"node": "n_r"}, "target": {"node": "END"}},
        ],
    }
    wf = (await api_client.post(f"{P}/workflows", json={"name": "Reverse"})).json()
    r = await api_client.put(
        f"/v1/workflows/{wf['id']}", json={"ir": {**ir, "id": wf["id"]}}, headers={"If-Match": "1"}
    )
    assert r.status_code == 200, r.text
    text = (await api_client.post(f"/v1/workflows/{wf['id']}/runs/stream", json={"input": {"text": "abc"}})).text
    fin = [json.loads(ln[6:]) for ln in text.splitlines() if ln.startswith("data: ")][-1]
    assert fin["output"]["out"] == "cba", fin
    await asyncio.gather(*(h.task for h in app.state.runner.handles.values() if h.task))  # its events delivered

    res = (await api_client.post(f"{P}/events/dry-run", json={"type": "test.ping", "data": {"n": 1}})).json()
    assert res["handlers"][0]["actions"] == [{"action": "emit", "type": "test.pong", "data": {"n": 2}}]
    assert len(fake_langsmith.created) == 1  # one sandbox per project, reused by nodes and handlers
    headers = fake_langsmith.created[0]["proxy_config"]["rules"][0]["headers"]
    assert headers[0]["value"] == "Bearer tok-e2e"


def test_the_child_supplies_the_sdk_on_a_bare_image(tmp_path):
    """docker and LangSmith sandboxes run the child on an image without AgentCanvas: a handler's or custom node's
    `from agentcanvas.sdk import …` still loads (a stand-in keeps the plain functions)."""
    import subprocess
    import sys

    from agentcanvas.sandbox.runners import CHILD

    (tmp_path / "events").mkdir()
    (tmp_path / "events" / "hooks.py").write_text(
        "from agentcanvas.sdk import Actions, Event, canvas_node, on\n\n\n@on('test.ping', filter='true')\n"
        "def pong(event, act):\n    act.emit('test.pong', {'n': event.data['n'] + 1})\n\n\n"
        "@canvas_node(name='x.y', writes=['n'])\ndef node(state, cfg):\n    return {'n': state['n'] * cfg['k']}\n"
    )
    # a Python that cannot import agentcanvas: -I ignores PYTHONPATH and the bare interpreter has no site-packages
    bare = [sys.executable, "-I", "-S", str(CHILD)]

    def run(import_path: str, payload: dict) -> dict:
        p = subprocess.run([*bare, import_path], input=json.dumps(payload), capture_output=True, text=True, check=False,
                           cwd=tmp_path, env={"PATH": "/usr/bin:/bin"}, timeout=30)  # fmt: skip
        return json.loads(p.stdout.splitlines()[-1])

    probe = subprocess.run([*bare[:3], "-c", "import agentcanvas"], capture_output=True, cwd=tmp_path, check=False)
    assert probe.returncode != 0  # the stand-in is what the handler gets
    event = {"type": "test.ping", "data": {"n": 1}}
    assert run("events.hooks:pong", {"event": event}) == {
        "ok": True, "actions": [{"action": "emit", "type": "test.pong", "data": {"n": 2}}],
    }  # fmt: skip
    assert run("events.hooks:node", {"state": {"n": 3}, "config": {"k": 2}}) == {
        "ok": True, "update": {"n": 6}, "events": [],  # events the code emitted, for the host to relay
    }  # fmt: skip


async def test_network_rules_in_a_mode_without_a_network_are_reported_once(tmp_path):
    """An egress list the mode cannot restrict (development: allowed) is logged once per workspace."""
    from structlog.testing import capture_logs

    from agentcanvas.sandbox import runners

    ws = Workspace(tmp_path / "ws")
    ws.write("sandbox.json", json.dumps(POLICY))
    ws.write("h.py", "def f(state):\n    return {'x': 1}\n")
    runner = SandboxRunner(Settings(env="test", script_execution="subprocess"), ws.root, {})
    runners._WARNED.clear()
    with capture_logs() as logs:
        await runner("h:f")({})
        await runner("h:f")({})
    warned = [e for e in logs if e["event"] == "sandbox.egress_not_enforced"]
    assert len(warned) == 1 and warned[0]["mode"] == "subprocess"


async def test_a_cancelled_call_stops_its_code(tmp_path):
    """A run cancelled (or out of time budget) while workspace code runs: the code stops with it, it does not live
    on with nobody waiting for it."""
    from agentcanvas.sandbox.runners import run_in_sandbox

    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "sleepy.py").write_text(
        "import os, pathlib, time\n\n\ndef run(state):\n"
        f"    pathlib.Path({str(tmp_path / 'pid')!r}).write_text(str(os.getpid()))\n"
        "    time.sleep(60)\n    return {}\n"
    )
    call = asyncio.create_task(run_in_sandbox("subprocess", tmp_path, "scripts.sleepy:run", {}, image="", timeout=30))
    for _ in range(100):
        if (tmp_path / "pid").exists():
            break
        await asyncio.sleep(0.05)
    pid = int((tmp_path / "pid").read_text())
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="prctl and /proc are Linux's")
async def test_another_process_cannot_read_a_calls_proxy_credential(tmp_path):
    """S-2: in a shared sandbox, one call's code could read another call's /proc/<pid>/environ (the signed egress
    credential) because both run as one user. The credential now arrives on stdin and the child is not dumpable."""
    from agentcanvas.sandbox.runners import _exchange

    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "peek.py").write_text(
        "import ctypes, os\n\n\ndef run(state):\n"
        "    try:\n        open('/proc/self/environ', 'rb').read()\n        readable = True\n"
        "    except PermissionError:\n        readable = False\n"
        "    return {'dumpable': ctypes.CDLL(None).prctl(3, 0, 0, 0, 0), 'environ_readable': readable,\n"
        "            'proxy_set': os.environ.get('HTTPS_PROXY', '').endswith('@127.0.0.1:9')}\n"
    )
    out = await _exchange("subprocess", tmp_path, "scripts.peek:run", {"state": {}}, "", 20,
                          proxy="http://list:secretcred@127.0.0.1:9")  # fmt: skip
    # not dumpable: /proc/<pid>/environ is root's, so no other process of the user reads it (nor the call itself)
    assert out["update"] == {"dumpable": 0, "environ_readable": False, "proxy_set": True}
    from agentcanvas.sandbox.runners import subprocess_command

    _, env, _ = subprocess_command(tmp_path, "scripts.peek:run")
    assert not any("secretcred" in v or "PROXY" in k for k, v in env.items())  # the credential is not in the env


async def test_a_child_that_writes_without_end_is_stopped(tmp_path, monkeypatch):
    """RT-5: `communicate()` kept everything the child wrote in the API's memory until it exited or timed out."""
    from agentcanvas.sandbox import runners

    monkeypatch.setattr(runners, "MAX_STDOUT", 1024 * 1024, raising=False)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "flood.py").write_text(
        "import os, time\n\n\ndef run(state):\n    for _ in range(64):\n        os.write(1, b'x' * 65536)\n"
        "    time.sleep(60)\n"
    )
    started = asyncio.get_running_loop().time()
    with pytest.raises(runners.ScriptError, match="more than 1 MB"):
        await runners._exchange("subprocess", tmp_path, "scripts.flood:run", {"state": {}}, "", 5)
    assert asyncio.get_running_loop().time() - started < 4  # stopped at the cap, not at the time limit


class FakeDocker:
    """`asyncio.create_subprocess_exec` for docker commands: records them; `start` answers or hangs."""

    def __init__(self, hang: bool):
        self.calls: list[list[str]] = []
        self.hang = hang

    async def __call__(self, *argv: str, **kw):
        self.calls.append(list(argv))
        hang = self.hang and argv[1] == "start"

        class Proc:
            returncode: int | None = None if hang else 0
            stdin = type("W", (), {"write": lambda s, d: None, "close": lambda s: None,
                                   "drain": staticmethod(lambda: asyncio.sleep(0))})()  # fmt: skip

            def __init__(self):
                self.stdout, self.stderr = asyncio.StreamReader(), asyncio.StreamReader()
                if not hang:
                    self.stdout.feed_data(b'{"ok": true, "update": {"n": 1}}\n')
                    self.stdout.feed_eof()
                self.stderr.feed_eof()

            async def communicate(self):
                return b"", b""

            async def wait(self):
                return self.returncode

            def kill(self):
                self.returncode = -9
                self.stdout.feed_eof()

        return Proc()


@pytest.mark.parametrize("hang", [False, True])
async def test_a_docker_call_always_removes_its_container(tmp_path, monkeypatch, hang):
    """RT-19: `docker run --rm` killed while it was creating the container could leave it running (`docker kill`
    came too early); now the container is created first, started attached, and removed however the call ends."""
    from agentcanvas.sandbox import runners

    docker = FakeDocker(hang)
    monkeypatch.setattr(runners.asyncio, "create_subprocess_exec", docker)
    call = asyncio.create_task(runners._exchange("docker", tmp_path, "s:f", {"state": {}}, "img", 20))
    if hang:
        await asyncio.sleep(0.05)
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call
    else:
        assert (await call)["update"] == {"n": 1}
    verbs = [c[1] for c in docker.calls]
    assert verbs[:2] == ["create", "start"] and verbs[-1] == "rm"
    name = docker.calls[0][docker.calls[0].index("--name") + 1]
    assert docker.calls[-1] == ["docker", "rm", "-f", name] and "--rm" not in docker.calls[0]


async def test_only_the_end_of_what_a_child_printed_is_kept():
    from agentcanvas.sandbox import runners

    stream = asyncio.StreamReader()
    stream.feed_data(b"a" * 300_000 + b"the end")
    stream.feed_eof()
    tail = await runners._read_tail(stream, 1000)
    assert len(tail) == 1000 and tail.endswith(b"the end")


async def test_udp_is_blocked_like_tcp(tmp_path):
    """R-11: the child's network guard covered connections but not datagrams, so a script could send data out over
    UDP (as DNS queries, say). A guard against mistakes only: the container's network is the real wall."""
    from agentcanvas.sandbox.runners import _exchange

    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "udp.py").write_text(
        "import socket\n\n\ndef run(state):\n    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n"
        "    try:\n        s.sendto(b'x', ('192.0.2.1', 53))\n        return {'sent': True}\n"
        "    except PermissionError:\n        return {'sent': False}\n"
    )
    out = await _exchange("subprocess", tmp_path, "scripts.udp:run", {"state": {}}, "", 20)
    assert out["update"] == {"sent": False}


async def test_the_run_gate_checks_again_after_the_workspace_changes():
    from test_frameworks import GREETER

    from agentcanvas.ir.models import Workflow
    from agentcanvas.services.run_gate import RunGate
    from agentcanvas.settings import Settings

    registries = [object()]
    gate = RunGate(Settings(), registry=lambda wf: registries[-1])
    checked: list[object] = []
    gate.blockers = lambda wf, registry=None: checked.append(registry) or []  # type: ignore[method-assign]
    wf = Workflow.model_validate({"id": "wf_g", "name": "g", **GREETER})
    await gate(wf)
    await gate(wf)
    assert len(checked) == 1  # the same workflow and code: checked once
    registries.append(object())  # the workspace code changed: a new registry
    await gate(wf)
    assert len(checked) == 2


async def test_the_run_gate_is_not_fooled_by_a_reused_object_id(monkeypatch):
    """RT-11: the cache key held `id(registry)`, which CPython hands to a new object once the old one is freed, so
    new workspace code could pass on an older check. Keyed on the workspace revision (or the registry itself)."""
    from test_frameworks import GREETER

    from agentcanvas.ir.models import Workflow
    from agentcanvas.services import run_gate
    from agentcanvas.settings import Settings

    monkeypatch.setattr(run_gate, "id", lambda obj: 42, raising=False)  # every registry gets the freed one's id
    registries = [object()]
    gate = run_gate.RunGate(Settings(), registry=lambda wf: registries[-1])
    checked: list[object] = []
    gate.blockers = lambda wf, registry=None: checked.append(registry) or []  # type: ignore[method-assign]
    wf = Workflow.model_validate({"id": "wf_g", "name": "g", **GREETER})
    await gate(wf)
    registries.append(object())
    await gate(wf)
    assert len(checked) == 2
    revisions = ["r1"]
    gate = run_gate.RunGate(Settings(), registry=lambda wf: registries[-1], revision=lambda wf: revisions[-1])
    gate.blockers = lambda wf, registry=None: checked.append(registry) or []  # type: ignore[method-assign]
    await gate(wf)
    await gate(wf)
    revisions.append("r2")
    await gate(wf)
    assert len(checked) == 4


FORKER = """import os
import time


def run(state):
    children = []
    try:
        for _ in range(state["forks"]):
            pid = os.fork()
            if pid == 0:
                time.sleep(30)
                os._exit(0)
            children.append(pid)
    except OSError:  # BlockingIOError: the call's process limit
        pass
    finally:
        for pid in children:
            os.kill(pid, 9)
            os.waitpid(pid, 0)
    return {"started": len(children)}
"""


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the process count comes from /proc")
async def test_code_that_forks_in_a_loop_hits_a_process_limit(tmp_path):
    """Launch review: the child had memory, CPU-time and file limits but none on processes, and a Kubernetes pod has
    no PID limit unless the kubelet sets one, so a fork loop in workspace code could use up the node's PIDs."""
    from agentcanvas.sandbox.runners import PROCESSES, _exchange

    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "forker.py").write_text(FORKER)
    out = await _exchange("subprocess", tmp_path, "scripts.forker:run", {"state": {"forks": 3 * PROCESSES}}, "", 20)
    assert 0 < out["update"]["started"] < 2 * PROCESSES  # about PROCESSES: other processes of the user come and go
