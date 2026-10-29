"""The sandbox service: runs workspace code for the API from a container of its own (`script_execution = "remote"`).

The API sends one request per call: the project's workspace (a tar.gz), the function to run and its input. This
service unpacks the workspace into a fresh temporary directory, runs the sandbox child with limits and no network
(`runners.subprocess_command`), or only the egress proxy when the call carries an egress list, returns the child's
result and deletes the directory. It holds no database credentials, no API keys and no other project's files, and its
container should have no route to the network but the egress proxy, so the code it runs can reach nothing beyond the
files it was sent and the hosts its project allows. Run it as:

    AGENTCANVAS_SANDBOX_TOKEN=... uvicorn agentcanvas.sandbox.service:app --host 0.0.0.0 --port 8090

Only the standard library, FastAPI and `runners` are used: no settings, no database.
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import io
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

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from agentcanvas.sandbox.runners import MEMORY_MB, QUEUE_WAIT_S, TIMEOUT_S, ScriptError, _exchange

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
        app = create_service()
        globals()["app"] = app
        return app
    raise AttributeError(name)
