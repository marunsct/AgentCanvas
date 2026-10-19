"""Sprint 7 workspaces: storage & file API (M5-T1), script nodes with hot reload (M5-T3), sandbox (M5-T4)."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from agentcanvas.sandbox.runners import ScriptError, run_in_sandbox
from agentcanvas.workspace import Workspace, WorkspaceError, load_attr

P = "/v1/projects/prj_default"


def events(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


# --------------------------------------------------------------------------------------------- storage ----
def test_workspace_scaffold_git_and_path_safety(tmp_path):
    ws = Workspace(tmp_path / "prj_a").ensure()
    files = {f.path for f in ws.list()}
    assert {"README.md", "scripts/example.py", "events/schemas/.keep"} <= files
    assert (ws.root / ".git").is_dir()
    for bad in ["../escape.py", "scripts/../../x.py", "scripts/../../../etc/passwd", ".git/config", ""]:
        with pytest.raises(WorkspaceError):
            ws.resolve(bad)
    assert ws.resolve("/etc/passwd") == ws.root / "etc/passwd"  # a leading slash is workspace-relative
    with pytest.raises(WorkspaceError):
        ws.write("image.png", "x")
    before = ws.revision()
    time.sleep(0.01)
    ws.write("scripts/hello.py", "def run(state):\n    return {'out': undefined_name}\n")
    assert ws.revision() != before  # graphs built from old code are rebuilt
    assert [d["code"] for d in ws.lint("scripts/hello.py")] == ["F821"]
    log = subprocess.run(["git", "log", "--oneline"], cwd=ws.root, capture_output=True, text=True, check=False).stdout
    assert "edit scripts/hello.py" in log  # every save is a commit
    ws.delete("scripts/hello.py")
    assert "scripts/hello.py" not in {f.path for f in ws.list()}


def test_symlinks_cannot_escape_the_workspace(tmp_path):
    """S12 (S3 follow-up): a symlink inside the workspace that points outside it is never followed."""
    ws = Workspace(tmp_path / "prj_a").ensure()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("top secret")
    (ws.root / "scripts" / "leak.txt").symlink_to(outside / "secret.txt")  # a file link
    (ws.root / "linked").symlink_to(outside, target_is_directory=True)  # a directory link
    for rel in ["scripts/leak.txt", "linked/secret.txt", "linked/new.py"]:
        with pytest.raises(WorkspaceError, match="outside the workspace"):
            ws.read(rel)
        with pytest.raises(WorkspaceError, match="outside the workspace"):
            ws.write(rel, "x = 1")
        with pytest.raises(WorkspaceError, match="outside the workspace"):
            ws.delete(rel)
    assert (outside / "secret.txt").read_text() == "top secret" and not (outside / "new.py").exists()
    listed = {f.path for f in ws.list()}
    assert "scripts/leak.txt" not in listed and not any(p.startswith("linked/") for p in listed)


def test_two_workspaces_never_share_modules(tmp_path):
    a, b = Workspace(tmp_path / "a").ensure(), Workspace(tmp_path / "b").ensure()
    a.write("scripts/same.py", "def run(state):\n    return {'who': 'a'}\n")
    b.write("scripts/same.py", "def run(state):\n    return {'who': 'b'}\n")
    assert load_attr("scripts.same:run", a.root)({}) == {"who": "a"}
    assert load_attr("scripts.same:run", b.root)({}) == {"who": "b"}
    with pytest.raises(PermissionError):
        load_attr("os:system", a.root)


# ----------------------------------------------------------------------------------------------- API ----
async def test_file_api(api_client):
    files = (await api_client.get(f"{P}/files")).json()
    assert "scripts/example.py" in {f["path"] for f in files}
    r = await api_client.put(
        f"{P}/files/scripts/greet.py", json={"content": "import os\ndef run(state):\n    return {}\n"}
    )
    assert r.status_code == 200 and [d["code"] for d in r.json()["diagnostics"]] == ["F401"]  # ruff markers
    got = (await api_client.get(f"{P}/files/scripts/greet.py")).json()
    assert got["content"].startswith("import os") and got["diagnostics"][0]["line"] == 1
    assert (await api_client.get(f"{P}/files/..%2F..%2Fetc%2Fpasswd")).status_code in (404, 422)
    assert (await api_client.put(f"{P}/files/.git/config", json={"content": "x"})).status_code == 422
    assert (await api_client.delete(f"{P}/files/scripts/greet.py")).status_code == 204
    assert (await api_client.get(f"{P}/files/scripts/greet.py")).status_code == 404
    assert (await api_client.get("/v1/projects/prj_nope/files")).status_code == 404
    made = await api_client.post(f"{P}/scripts", json={"name": "score", "writes": ["score"]})
    assert made.status_code == 201 and made.json()["import_path"] == "scripts.score:run"
    assert '"score": None' in (await api_client.get(f"{P}/files/scripts/score.py")).json()["content"]
    assert (await api_client.post(f"{P}/scripts", json={"name": "score"})).status_code == 409


def script_workflow(import_path: str = "scripts.shout:run") -> dict:
    return {
        "name": "script wf",
        "state": {"channels": [{"name": "text"}, {"name": "out"}]},
        "nodes": [{"id": "n_s", "type": "core.script", "name": "shout", "config": {"import_path": import_path}}],
        "edges": [
            {"id": "e1", "kind": "flow", "source": {"node": "START"}, "target": {"node": "n_s"}},
            {"id": "e2", "kind": "flow", "source": {"node": "n_s"}, "target": {"node": "END"}},
        ],
    }


async def make_wf(api, ir: dict) -> str:
    wf = (await api.post(f"{P}/workflows", json={"name": ir["name"]})).json()
    r = await api.put(f"/v1/workflows/{wf['id']}", json={"ir": {**ir, "id": wf["id"]}}, headers={"If-Match": "1"})
    assert r.status_code == 200, r.text
    return wf["id"]


async def run_once(api, wf: str, text: str) -> dict:
    return events((await api.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"text": text}})).text)[-1]


async def test_script_node_runs_workspace_code_and_reloads_on_edit(api_client):
    """M5-T3 AC: editing the script and re-running uses the new code without restarting the server."""
    await api_client.put(
        f"{P}/files/scripts/shout.py", json={"content": "def run(state):\n    return {'out': state['text'].upper()}\n"}
    )
    wf = await make_wf(api_client, script_workflow())
    assert (await run_once(api_client, wf, "hi"))["output"]["out"] == "HI"
    time.sleep(0.01)  # distinct mtime
    await api_client.put(
        f"{P}/files/scripts/shout.py",
        json={"content": "async def run(state):\n    return {'out': state['text'] + '!'}\n"},
    )
    assert (await run_once(api_client, wf, "hi"))["output"]["out"] == "hi!"


# ------------------------------------------------------------------------------------------- sandbox ----
@pytest.fixture
def sandbox_ws(tmp_path) -> Path:
    ws = Workspace(tmp_path / "sbx").ensure()
    ws.write("scripts/ok.py", "import sys\ndef run(state):\n    print('noise')\n    return {'n': state['n'] + 1}\n")
    ws.write(
        "scripts/net.py",
        "import socket\ndef run(state):\n    socket.create_connection(('example.com', 80), timeout=2)\n    return {}\n",
    )
    ws.write("scripts/slow.py", "import time\ndef run(state):\n    time.sleep(10)\n    return {}\n")
    ws.write("scripts/bad.py", "def run(state):\n    return 42\n")
    ws.write(
        "scripts/env.py",
        "import os\ndef run(state):\n    return {'keys': sorted(k for k in os.environ if 'KEY' in k)}\n",
    )
    return ws.root


async def test_subprocess_sandbox(sandbox_ws, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    run = lambda path, **kw: run_in_sandbox("subprocess", sandbox_ws, path, {"n": 1}, image="unused", **kw)  # noqa: E731
    assert await run("scripts.ok:run") == {"n": 2}  # prints do not corrupt the result
    assert await run("scripts.env:run") == {"keys": []}  # the host's environment (keys!) is not inherited
    with pytest.raises(ScriptError, match="network access is disabled"):
        await run("scripts.net:run")
    with pytest.raises(ScriptError, match="must return a dict"):
        await run("scripts.bad:run")
    with pytest.raises(TimeoutError):
        await run("scripts.slow:run", timeout=1)


def _docker_ready() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "info"], capture_output=True, check=False, timeout=20).returncode == 0


@pytest.mark.skipif(not _docker_ready(), reason="needs a Docker daemon (CI has one)")
async def test_docker_sandbox_has_no_network(sandbox_ws):
    """M5-T4 AC: a script trying to open a network socket fails in docker mode."""
    subprocess.run(["docker", "pull", "-q", "python:3.12-slim"], check=False, capture_output=True, timeout=300)
    for p in [sandbox_ws, *sandbox_ws.parents][:3]:  # pytest's tmp dirs are private; the sandbox user is nobody
        p.chmod(0o755)
    run = lambda path, t=120: run_in_sandbox("docker", sandbox_ws, path, {"n": 1}, image="python:3.12-slim", timeout=t)  # noqa: E731
    try:
        assert await run("scripts.ok:run") == {"n": 2}
    except ScriptError as exc:
        if "mounts denied" in str(exc):  # Docker Desktop shares only some folders with containers
            pytest.skip("this Docker may not mount pytest's temporary folder: share it, or run with --basetemp")
        raise
    with pytest.raises(ScriptError):
        await run("scripts.net:run")
    with pytest.raises(TimeoutError):  # S2: the container itself is stopped, not only the docker client
        await run("scripts.slow:run", 3)
    left = subprocess.run(
        ["docker", "ps", "-q", "--filter", "name=ac-sbx-"], capture_output=True, text=True, check=True
    )
    assert left.stdout.strip() == ""


async def test_sandboxed_script_node_in_a_run(app, api_client):
    app.state.settings.script_execution = "subprocess"
    app.state.projects._resources.clear()  # resolvers are created per project with the current settings
    await api_client.put(
        f"{P}/files/scripts/shout.py", json={"content": "def run(state):\n    return {'out': state['text'][::-1]}\n"}
    )
    wf = await make_wf(api_client, script_workflow())
    assert (await run_once(api_client, wf, "abc"))["output"]["out"] == "cba"
    await api_client.put(
        f"{P}/files/scripts/boom.py", json={"content": "def run(state):\n    raise ValueError('nope')\n"}
    )
    wf2 = await make_wf(api_client, script_workflow("scripts.boom:run"))
    evs = events((await api_client.post(f"/v1/workflows/{wf2}/runs/stream", json={"input": {"text": "x"}})).text)
    assert evs[-1]["type"] == "RUN_ERROR" and "ValueError: nope" in evs[-1]["message"]
