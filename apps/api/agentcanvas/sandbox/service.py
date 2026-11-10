"""The sandbox service: runs workspace code for the API from a container of its own (`script_execution = "remote"`).

The API sends one request per call: the project's workspace (a tar.gz), the function to run and its input. This
service unpacks the workspace into a fresh temporary directory, runs the sandbox child with limits and no network
(`runners.subprocess_command`), or only the egress proxy when the call carries an egress list, returns the child's
result and deletes the directory. It holds no database credentials, no API keys and no other project's files, and its
container should have no route to the network but the egress proxy, so the code it runs can reach nothing beyond the
files it was sent and the hosts its project allows. Run it as:

    AGENTCANVAS_SANDBOX_TOKEN=... uvicorn agentcanvas.sandbox.service:app --host 0.0.0.0 --port 8090

`/mcp` (a WebSocket) runs a local (stdio) app connector's server for as long as the connection lasts
(`agentcanvas.sandbox.mcp`): the first message says what to start, then binary frames carry its stdin and stdout.
It takes the same slot as a call, so a connector and other code never share this container at the same moment.

Only the standard library, FastAPI and `runners` are used: no settings, no database.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hmac
import io
import math
import os
import shutil
import signal
import sys
import tarfile
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, ValidationError

from agentcanvas.sandbox.runners import (
    MCP_CHILD,
    MCP_PROCESSES,
    MEMORY_MB,
    QUEUE_WAIT_S,
    TIMEOUT_S,
    ScriptError,
    _exchange,
    _tasks_of_this_user,
)

MAX_WORKSPACE_BYTES = 20 * 1024 * 1024  # packed
MAX_UNPACKED_BYTES = 100 * 1024 * 1024
MAX_FILES = 5000
SKIP = {".git", "__pycache__", ".venv", "node_modules", ".mypy_cache", ".ruff_cache"}


class WorkspaceTooLarge(ValueError):
    pass


def pack_workspace(root: Path, limit: int = MAX_WORKSPACE_BYTES) -> bytes:
    """The workspace's files as a tar.gz (no git history, caches or virtual environments)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        count = 0
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(root)
            if SKIP & set(rel.parts) or path.is_symlink() or not path.is_file():
                continue
            count += 1
            if count > MAX_FILES:
                raise WorkspaceTooLarge(f"the workspace has more than {MAX_FILES} files")
            tar.add(path, arcname=rel.as_posix(), recursive=False)
    data = buf.getvalue()
    if len(data) > limit:
        raise WorkspaceTooLarge(
            f"the workspace is {len(data) // 1024 // 1024} MB packed; the sandbox takes {limit // 1024 // 1024} MB"
        )
    return data


def unpack_workspace(data: bytes, dest: Path) -> None:
    """Extract a tar.gz made by `pack_workspace`, refusing anything that could write outside `dest`: absolute or
    `..` paths, links, devices, too many files or too many bytes."""
    root = dest.resolve()
    total = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        members = tar.getmembers()
        if len(members) > MAX_FILES:
            raise ValueError("too many files in the workspace")
        for m in members:
            target = (root / m.name).resolve()
            if not m.isfile() or not target.is_relative_to(root) or target == root:
                raise ValueError(f"'{m.name}' is not a plain file inside the workspace")
            total += m.size
            if total > MAX_UNPACKED_BYTES:
                raise ValueError("the workspace is too large unpacked")
            target.parent.mkdir(parents=True, exist_ok=True)
            src = tar.extractfile(m)
            assert src is not None
            with open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            target.chmod(0o644)


class RunIn(BaseModel):
    import_path: str = Field(min_length=3, max_length=300, pattern=r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$")
    payload: dict[str, Any]
    workspace: str = Field(description="the workspace as a base64 tar.gz")
    timeout_s: float = Field(default=TIMEOUT_S, gt=0, le=600)
    memory_mb: int = Field(default=MEMORY_MB, ge=32, le=4096)
    egress_proxy: str | None = Field(
        None, max_length=8000, pattern=r"^http://[^/\s]+$",
        description="the call's egress proxy with its signed allow-list (sandbox.json egress); None = no network",
    )  # fmt: skip


DISCONNECT_POLL_S = 0.5  # how soon a call whose caller went away is stopped


class McpStartIn(BaseModel):
    """What a connector session starts (the first message on `/mcp`)."""

    command: str = Field(min_length=1, max_length=300, pattern=r"^\S+$")
    args: list[str] = Field(default_factory=list, max_length=200)
    env: dict[str, str] = Field(default_factory=dict, max_length=200)
    workspace: str = Field(description="the workspace as a base64 tar.gz")
    memory_mb: int = Field(default=1024, ge=128, le=16384)
    lifetime_s: float = Field(default=1800, gt=0, le=4 * 3600)
    egress_proxy: str | None = Field(None, max_length=8000, pattern=r"^http://[^/\s]+$")


MCP_MAX_LINE = 32 * 1024 * 1024
MEMORY_POLL_S = 1.0  # how often a connector's memory is measured
# what of the service's environment a connector's server gets: where programs are and how npm, uv, Node.js and
# Playwright are set up in the image (never the service's token)
PASS_ENV = ("PATH", "LANG", "LC_ALL", "TZ", "AC_MCP_SERVERS")
PASS_PREFIXES = ("npm_config_", "NPM_CONFIG_", "PLAYWRIGHT_", "UV_", "NODE_")


def _connector_env(body: McpStartIn, home: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in PASS_ENV or k.startswith(PASS_PREFIXES)}
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", **env, "HOME": str(home), **body.env}
    env.pop("AGENTCANVAS_SANDBOX_TOKEN", None)
    if body.egress_proxy:
        env["AC_EGRESS_UPSTREAM"] = body.egress_proxy  # `mcp_child` relays to it; the server never sees it
    return env


def _connector_limits(cpu_s: int, processes: int | None) -> Callable[[], None]:
    """A connector's limits: CPU seconds (per process) and processes; no address-space cap, which Node.js and
    Chromium reserve far beyond what they use (memory is measured instead, `_memory_mb`)."""

    def apply() -> None:  # pragma: no cover - runs in the child before exec
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s + 1))
        resource.setrlimit(resource.RLIMIT_NOFILE, (4096, 4096))
        if processes is not None:
            hard = resource.getrlimit(resource.RLIMIT_NPROC)[1]
            cap = processes if hard == resource.RLIM_INFINITY else min(processes, hard)
            resource.setrlimit(resource.RLIMIT_NPROC, (cap, cap))

    return apply


def _memory_mb(root: int) -> float:
    """Memory of a process and everything it started (Linux; 0 elsewhere): proportional set sizes, so pages a
    browser's processes share count once, not once per process; resident size where the kernel has no rollup."""
    if sys.platform != "linux":
        return 0.0
    page = os.sysconf("SC_PAGE_SIZE")
    total = 0
    for pid in [root, *_descendants(root)]:
        try:
            rollup = Path(f"/proc/{pid}/smaps_rollup").read_text()
            total += next(int(ln.split()[1]) * 1024 for ln in rollup.splitlines() if ln.startswith("Pss:"))
            continue
        except (OSError, ValueError, IndexError, StopIteration):
            pass
        try:
            total += int(Path(f"/proc/{pid}/statm").read_text().split()[1]) * page
        except (OSError, ValueError, IndexError):
            pass
    return total / (1024 * 1024)


def _kill_tree(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    pids = _descendants(proc.pid) if sys.platform == "linux" else []
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)  # its own process group (start_new_session)
    for pid in pids:  # and whatever left the group
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGKILL)


async def _run_connector(ws: WebSocket, body: McpStartIn, work: Path) -> None:
    """Start the server and relay its stdio until either side ends, its time is up or it uses too much memory."""
    home = work / ".home"
    home.mkdir()
    running = await asyncio.to_thread(_tasks_of_this_user) if sys.platform == "linux" else None
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-I", str(MCP_CHILD), body.command, *body.args,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=_connector_env(body, home), cwd=work, limit=MCP_MAX_LINE, start_new_session=True,
            preexec_fn=_connector_limits(math.ceil(body.lifetime_s), running + MCP_PROCESSES if running else None)
            if os.name == "posix" else None,
        )  # fmt: skip
    except OSError as exc:
        await ws.send_json({"error": f"could not start it: {exc}"})
        return
    assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
    stdin, stdout, stderr = proc.stdin, proc.stdout, proc.stderr
    tail = bytearray()
    await ws.send_json({"ready": True})

    async def up() -> str:  # the API -> the server
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                return "the caller went away"
            data = msg.get("bytes") or (msg.get("text") or "").encode()
            stdin.write(data)
            await stdin.drain()

    async def down() -> str:  # the server -> the API
        try:
            while line := await stdout.readline():
                await ws.send_bytes(line)
        except ValueError:
            return f"it sent a message over {MCP_MAX_LINE // (1024 * 1024)} MB"
        await proc.wait()
        return f"it exited ({proc.returncode})"

    async def errors() -> str:
        while chunk := await stderr.read(65536):
            tail.extend(chunk)
            del tail[:-16384]
        await asyncio.Event().wait()  # never the reason it ended
        return ""

    async def limits() -> str:
        deadline = time.monotonic() + body.lifetime_s
        while time.monotonic() < deadline:
            await asyncio.sleep(MEMORY_POLL_S)
            if await asyncio.to_thread(_memory_mb, proc.pid) > body.memory_mb:
                return f"it used more than {body.memory_mb} MB of memory and was stopped"
        return f"it ran for {body.lifetime_s:g} s, its time limit, and was stopped"

    tasks = [asyncio.ensure_future(t()) for t in (up, down, errors, limits)]
    reason = "stopped"
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        first = next(iter(done))
        reason = first.result() if not first.cancelled() and first.exception() is None else "the relay failed"
    finally:
        _kill_tree(proc)
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), 5)
    said = bytes(tail).decode(errors="replace").strip()[-500:]
    with contextlib.suppress(Exception):  # the caller may be gone already
        await ws.send_json({"exit": proc.returncode, "error": reason + (f": {said}" if said else "")})
        await ws.close()


def _become_subreaper() -> None:
    """Linux: processes a call's code starts and abandons (a daemon that forks away) become this service's
    children rather than init's, so `sweep` finds them."""
    if sys.platform != "linux":
        return
    try:
        import ctypes

        ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0)  # PR_SET_CHILD_SUBREAPER
    except (OSError, AttributeError):
        pass


def _descendants(root: int) -> list[int]:
    children: dict[int, list[int]] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            ppid = int((entry / "stat").read_text().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        children.setdefault(ppid, []).append(int(entry.name))
    out, stack = [], list(children.get(root, []))
    while stack:
        pid = stack.pop()
        out.append(pid)
        stack.extend(children.get(pid, []))
    return out


def sweep(since: float, tmp: Path | None = None) -> tuple[int, int]:
    """After a call (one at a time): kill every process it left behind and remove what it wrote to the temporary
    folder, so the next call, perhaps another project's, finds nothing of it (2026-10-29 review, S-2). Returns
    (processes killed, entries removed)."""
    killed = []
    for pid in _descendants(os.getpid()):
        try:
            os.kill(pid, signal.SIGKILL)
            killed.append(pid)
        except (ProcessLookupError, PermissionError):
            pass
    for pid in killed:  # orphans became this process's children (subreaper): collect them, or they stay zombies
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
    removed = 0
    tmp = tmp or Path(tempfile.gettempdir())
    for entry in tmp.iterdir() if tmp.is_dir() else []:
        try:
            if entry.lstat().st_mtime >= since - 1:
                shutil.rmtree(entry) if entry.is_dir() and not entry.is_symlink() else entry.unlink()
                removed += 1
        except OSError:
            pass
    return len(killed), removed


def hide_from_children() -> None:
    """Workspace code runs as this service's own user, and the service is the container's PID 1: without this, that
    code could read AGENTCANVAS_SANDBOX_TOKEN from /proc/1/environ (its own environment has it removed) or ptrace
    this process. A non-dumpable process's /proc files belong to root, so they are closed to it. Linux only."""
    if sys.platform != "linux":
        return
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(4, 0, 0, 0, 0) != 0:  # PR_SET_DUMPABLE, 0
        raise OSError(ctypes.get_errno(), "prctl(PR_SET_DUMPABLE, 0) failed: the service would expose its token")


def create_service(token: str | None = None, run: Callable[..., Any] = _exchange) -> FastAPI:
    secret = token if token is not None else os.environ.get("AGENTCANVAS_SANDBOX_TOKEN", "")
    if len(secret) < 16:
        raise RuntimeError(
            "AGENTCANVAS_SANDBOX_TOKEN (16+ characters) is required: it is what lets the API call this service"
        )
    # one call at a time by default: calls of different projects then never run side by side as the same user, and
    # the sweep after each (AGENTCANVAS_SANDBOX_SWEEP, set in the containers) leaves nothing for the next to find
    concurrency = int(os.environ.get("AGENTCANVAS_SANDBOX_CONCURRENCY", "1"))
    slots = asyncio.Semaphore(concurrency)
    sweeping = os.environ.get("AGENTCANVAS_SANDBOX_SWEEP") == "1" and concurrency == 1
    if sweeping:
        _become_subreaper()
    app = FastAPI(title="AgentCanvas sandbox", docs_url=None, redoc_url=None, openapi_url=None)

    def authorised(request: Request) -> None:
        sent = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(sent.encode(), secret.encode()):
            raise HTTPException(401, "unauthorised")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.websocket("/mcp")
    async def mcp_session(ws: WebSocket) -> None:
        """A local app connector's server, for as long as this connection lasts (`agentcanvas.sandbox.mcp`)."""
        sent = ws.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(sent.encode(), secret.encode()):
            await ws.close(code=1008)
            return
        await ws.accept()
        try:
            body = McpStartIn.model_validate_json(await ws.receive_text())
            packed = base64.b64decode(body.workspace, validate=True)
            if len(packed) > MAX_WORKSPACE_BYTES:
                raise ValueError("the workspace is too large")
        except (ValidationError, ValueError, WebSocketDisconnect, KeyError) as exc:
            with contextlib.suppress(Exception):
                await ws.send_json({"error": f"bad request: {str(exc)[:300]}"})
                await ws.close(code=1003)
            return
        try:  # one call or connector at a time, like /run
            await asyncio.wait_for(slots.acquire(), QUEUE_WAIT_S)
        except TimeoutError:
            await ws.send_json({"error": f"every sandbox was busy for {QUEUE_WAIT_S} s; add sandbox replicas"})
            await ws.close(code=1013)
            return
        started = time.time()
        work = Path(tempfile.mkdtemp(prefix="ac-mcp-"))
        try:
            try:
                await asyncio.to_thread(unpack_workspace, packed, work)
            except (ValueError, tarfile.TarError, OSError) as exc:
                await ws.send_json({"error": f"bad workspace: {exc}"})
                await ws.close(code=1003)
                return
            await _run_connector(ws, body, work)
        finally:
            shutil.rmtree(work, ignore_errors=True)
            if sweeping:
                await asyncio.to_thread(sweep, started)
            slots.release()

    @app.post("/run", dependencies=[Depends(authorised)])
    async def run_script(body: RunIn, request: Request) -> dict[str, Any]:
        """The child's result (`{"ok": true, "update": …}`), or `{"ok": false, "error": …, "timeout": bool}`.
        When the API stops waiting (its run was cancelled), the code is stopped too."""
        try:
            packed = base64.b64decode(body.workspace, validate=True)
            if len(packed) > MAX_WORKSPACE_BYTES:
                raise ValueError("the workspace is too large")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        try:  # one call at a time: wait a while for this sandbox, then let the API say they are all busy
            await asyncio.wait_for(slots.acquire(), QUEUE_WAIT_S)
        except TimeoutError:
            raise HTTPException(503, f"busy: no free slot within {QUEUE_WAIT_S} s") from None
        try:
            started = time.time()
            work = Path(tempfile.mkdtemp(prefix="ac-run-"))
            job: asyncio.Future[Any] | None = None
            try:
                try:
                    await asyncio.to_thread(unpack_workspace, packed, work)
                except (ValueError, tarfile.TarError, OSError) as exc:
                    raise HTTPException(422, f"bad workspace: {exc}") from None
                job = running = asyncio.ensure_future(run(
                    "subprocess", work, body.import_path, body.payload, "", body.timeout_s, body.memory_mb,
                    body.egress_proxy,
                ))  # fmt: skip
                try:
                    while not running.done():
                        await asyncio.wait([running], timeout=DISCONNECT_POLL_S)
                        if not running.done() and await request.is_disconnected():
                            raise HTTPException(499, "the caller went away")
                    return running.result()
                except TimeoutError:
                    return {"ok": False, "timeout": True, "error": f"ran longer than {body.timeout_s:g} s"}
                except ScriptError as exc:
                    return {"ok": False, "error": str(exc)}
            finally:
                if job is not None and not job.done():  # the caller went away, or this handler was cancelled
                    job.cancel()  # `_exchange` kills the child on the way out
                    await asyncio.gather(job, return_exceptions=True)
                shutil.rmtree(work, ignore_errors=True)
                if sweeping:
                    await asyncio.to_thread(sweep, started)
        finally:
            slots.release()

    return app


def __getattr__(name: str) -> Any:  # `uvicorn agentcanvas.sandbox.service:app` builds the app on first use
    if name == "app":
        hide_from_children()  # the service itself (tests build theirs with create_service)
        app = create_service()
        globals()["app"] = app
        return app
    raise AttributeError(name)
