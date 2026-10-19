"""The sandbox service: runs workspace code for the API from a container of its own (`script_execution = "remote"`).

The API sends one request per call: the project's workspace (a tar.gz), the function to run and its input. This
service unpacks the workspace into a fresh temporary directory, runs the sandbox child with limits and no network
(`runners.subprocess_command`), returns the child's result and deletes the directory. It holds no database
credentials, no API keys and no other project's files, and its container should have no route to the network, so the
code it runs can reach nothing beyond the files it was sent. Run it as:

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
import tarfile
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from agentcanvas.sandbox.runners import MEMORY_MB, TIMEOUT_S, ScriptError, _exchange

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


def create_service(token: str | None = None, run: Callable[..., Any] = _exchange) -> FastAPI:
    secret = token if token is not None else os.environ.get("AGENTCANVAS_SANDBOX_TOKEN", "")
    if len(secret) < 16:
        raise RuntimeError(
            "AGENTCANVAS_SANDBOX_TOKEN (16+ characters) is required: it is what lets the API call this service"
        )
    slots = asyncio.Semaphore(int(os.environ.get("AGENTCANVAS_SANDBOX_CONCURRENCY", "8")))
    app = FastAPI(title="AgentCanvas sandbox", docs_url=None, redoc_url=None, openapi_url=None)

    def authorised(request: Request) -> None:
        sent = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(sent.encode(), secret.encode()):
            raise HTTPException(401, "unauthorised")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/run", dependencies=[Depends(authorised)])
    async def run_script(body: RunIn) -> dict[str, Any]:
        """The child's result (`{"ok": true, "update": …}`), or `{"ok": false, "error": …, "timeout": bool}`."""
        try:
            packed = base64.b64decode(body.workspace, validate=True)
            if len(packed) > MAX_WORKSPACE_BYTES:
                raise ValueError("the workspace is too large")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        async with slots:
            work = Path(tempfile.mkdtemp(prefix="ac-run-"))
            try:
                try:
                    await asyncio.to_thread(unpack_workspace, packed, work)
                except (ValueError, tarfile.TarError, OSError) as exc:
                    raise HTTPException(422, f"bad workspace: {exc}") from None
                try:
                    return await run(
                        "subprocess", work, body.import_path, body.payload, "", body.timeout_s, body.memory_mb
                    )
                except TimeoutError:
                    return {"ok": False, "timeout": True, "error": f"ran longer than {body.timeout_s:g} s"}
                except ScriptError as exc:
                    return {"ok": False, "error": str(exc)}
            finally:
                shutil.rmtree(work, ignore_errors=True)

    return app


def __getattr__(name: str) -> Any:  # `uvicorn agentcanvas.sandbox.service:app` builds the app on first use
    if name == "app":
        app = create_service()
        globals()["app"] = app
        return app
    raise AttributeError(name)
