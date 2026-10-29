"""The sandbox service (`script_execution = "remote"`): workspace code runs in a container of its own."""

from __future__ import annotations

import asyncio
import base64
import io
import os
import tarfile

import httpx
import pytest

from agentcanvas.sandbox import runners
from agentcanvas.sandbox.runners import SandboxRunner, ScriptError
from agentcanvas.sandbox.service import (
    WorkspaceTooLarge,
    create_service,
    pack_workspace,
    unpack_workspace,
)
from agentcanvas.settings import Settings

TOKEN = "t" * 32


@pytest.fixture
def remote(tmp_path, monkeypatch):
    """A SandboxRunner in remote mode talking to an in-process service."""
    ws = tmp_path / "ws"
    (ws / "scripts").mkdir(parents=True)
    (ws / "scripts" / "hi.py").write_text("def run(state):\n    return {'out': 'hi ' + state['who']}\n")
    (ws / "scripts" / "peek.py").write_text(
        "import os\n"
        "def run(state):\n"
        "    return {'out': sorted(os.listdir('/'))[:0] or [os.environ.get('SECRET', 'no secret'), "
        "os.path.exists('/data/workspaces')]}\n"
    )
    (ws / "scripts" / "slow.py").write_text("import time\ndef run(state):\n    time.sleep(30)\n")
    (ws / ".git").mkdir()
    (ws / ".git" / "config").write_text("[core]\n")
    service = create_service(TOKEN)
    monkeypatch.setattr(runners, "REMOTE_TRANSPORT", httpx.ASGITransport(app=service))
    settings = Settings(script_execution="remote", sandbox_url="http://sandbox", sandbox_token=TOKEN)
    return SandboxRunner(settings, ws, {}), ws, settings, service


async def test_a_script_runs_in_the_service(remote):
    runner, *_ = remote
    assert await runner("scripts.hi:run")({"who": "Ada"}) == {"out": "hi Ada"}


async def test_the_service_sees_only_what_it_was_sent(remote, monkeypatch):
    runner, *_ = remote
    monkeypatch.setenv("SECRET", "top-secret")  # the API's environment never reaches the child
    out = await runner("scripts.peek:run")({})
    assert out == {"out": ["no secret", False]}


async def test_errors_and_timeouts_come_back(remote):
    runner, ws, *_ = remote
    (ws / "sandbox.json").write_text('{"timeout_s": 1}')
    with pytest.raises(TimeoutError, match="ran longer than 1 s"):
        await runner("scripts.slow:run")({})
    with pytest.raises(ScriptError, match="not found"):
        await runner("scripts.nope:run")({})


async def test_the_token_is_checked(remote, monkeypatch):
    _, ws, settings, _ = remote
    bad = SandboxRunner(settings.model_copy(update={"sandbox_token": "x" * 32}), ws, {})
    with pytest.raises(ScriptError, match="answered 401"):
        await bad("scripts.hi:run")({"who": "x"})


async def test_remote_mode_needs_its_settings(remote):
    _, ws, settings, _ = remote
    bare = SandboxRunner(settings.model_copy(update={"sandbox_url": None}), ws, {})
    with pytest.raises(ScriptError, match="SANDBOX_URL"):
        await bare("scripts.hi:run")({"who": "x"})


async def test_an_unreachable_service_is_a_script_error(remote, monkeypatch):
    _, ws, settings, _ = remote

    def refuse(request):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(runners, "REMOTE_TRANSPORT", httpx.MockTransport(refuse))
    with pytest.raises(ScriptError, match="not reachable"):
        await SandboxRunner(settings, ws, {})("scripts.hi:run")({"who": "x"})


def test_the_service_refuses_to_start_without_a_token():
    with pytest.raises(RuntimeError, match="SANDBOX_TOKEN"):
        create_service("short")


def test_packing_skips_git_and_unpacking_refuses_escapes(tmp_path):
    ws = tmp_path / "ws"
    (ws / "a").mkdir(parents=True)
    (ws / "a" / "x.py").write_text("x = 1\n")
    (ws / ".git").mkdir()
    (ws / ".git" / "HEAD").write_text("ref")
    (ws / "link").symlink_to("/etc/passwd")
    packed = pack_workspace(ws)
    out = tmp_path / "out"
    out.mkdir()
    unpack_workspace(packed, out)
    assert [p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()] == ["a/x.py"]

    def tarball(name: str, kind: bytes = tarfile.REGTYPE, link: str = "") -> bytes:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            info = tarfile.TarInfo(name)
            info.type = kind
            info.linkname = link
            data = b"x" if kind == tarfile.REGTYPE else b""
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        return buf.getvalue()

    for bad in (tarball("../evil.py"), tarball("/abs.py"), tarball("l", tarfile.SYMTYPE, "/etc/passwd")):
        with pytest.raises(ValueError, match="plain file"):
            unpack_workspace(bad, out)
    assert not (tmp_path / "evil.py").exists()
    with pytest.raises(WorkspaceTooLarge):
        pack_workspace(ws, limit=1)


async def test_the_service_rejects_a_bad_workspace(remote):
    *_, service = remote
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=service), base_url="http://s", headers={"Authorization": f"Bearer {TOKEN}"}
    ) as c:
        body = {"import_path": "scripts.hi:run", "payload": {}, "workspace": base64.b64encode(b"not a tar").decode()}
        assert (await c.post("/run", json=body)).status_code == 422
        assert (await c.post("/run", json={**body, "import_path": "../../etc:x"})).status_code == 422
        assert (await c.get("/healthz")).json() == {"status": "ok"}
        assert (await c.get("/docs")).status_code == 404


async def test_a_call_whose_caller_went_away_stops_its_code(remote, tmp_path):
    """The API cancelled its run and dropped the connection: the service notices and stops the code, rather than
    leaving it to its time limit with nobody waiting."""
    import json

    *_, ws, _, service = remote
    pid_file = tmp_path / "pid"
    (ws / "scripts" / "sleepy.py").write_text(
        f"import os, pathlib, time\ndef run(state):\n    pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
        "    time.sleep(60)\n"
    )
    body = json.dumps({"import_path": "scripts.sleepy:run", "payload": {"state": {}},
                       "workspace": base64.b64encode(pack_workspace(ws)).decode()}).encode()  # fmt: skip
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "path": "/run",
             "raw_path": b"/run", "query_string": b"", "scheme": "http", "server": ("sandbox", 80),
             "headers": [(b"authorization", f"Bearer {TOKEN}".encode()),
                         (b"content-type", b"application/json")]}  # fmt: skip
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        while not pid_file.exists():  # the caller hangs up once the code is running
            await asyncio.sleep(0.05)
        return {"type": "http.disconnect"}

    async def send(message):
        pass

    await asyncio.wait_for(service(scope, receive, send), 20)
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)
