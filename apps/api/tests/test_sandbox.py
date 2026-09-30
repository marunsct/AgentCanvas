"""Sprint 7 sandbox policy (FR-X-08, basic) and spike SP-2: workspace code in a LangSmith Sandbox.

There are no LangSmith credentials in CI, so the LangSmith mode runs against a fake client that keeps the sandbox
filesystem in a temp directory and executes commands locally; it records every call the real client would get.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

import pytest
from langsmith.sandbox import CommandTimeoutError, ExecutionResult

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
    assert codes == {"E083", "W082", "W074"}  # unknown secret, no network in docker, allow-all egress
    ws.write("sandbox.json", json.dumps(POLICY))
    assert check_policy(ws.root, "langsmith", {"CRM_TOKEN"}) == []
    ws.write("sandbox.json", "{nope")
    assert [p["code"] for p in check_policy(ws.root, "langsmith", set())] == ["E082"]


# --------------------------------------------------------------------------------------- fake LangSmith ----
class FakeBox:
    def __init__(self, name: str, root: Path):
        self.name, self.root = name, root
        self.writes: list[str] = []

    def _local(self, text: str) -> str:
        return re.sub(r"(?<![\w/])/(sandbox|ws)\b", lambda m: f"{self.root}/{m.group(1)}", text)

    async def write(self, path: str, content: str | bytes) -> None:
        self.writes.append(path)
        local = Path(self._local(path))
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(content.encode() if isinstance(content, str) else content)

    async def run(self, command: str, *, timeout: int = 60, cwd: str | None = None, **_: object) -> ExecutionResult:
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

    res = (await api_client.post(f"{P}/events/dry-run", json={"type": "test.ping", "data": {"n": 1}})).json()
    assert res["handlers"][0]["actions"] == [{"action": "emit", "type": "test.pong", "data": {"n": 2}}]
    assert len(fake_langsmith.created) == 1  # one sandbox per project, reused by nodes and handlers
    headers = fake_langsmith.created[0]["proxy_config"]["rules"][0]["headers"]
    assert headers[0]["value"] == "Bearer tok-e2e"
