"""Host side of the script sandbox: a child process (limits, no inherited secrets) or a Docker container."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agentcanvas.settings import Settings

CHILD = Path(__file__).with_name("child.py")
TIMEOUT_S = 30.0
MEMORY_BYTES = 512 * 1024 * 1024


class ScriptError(RuntimeError):
    """The script raised, returned something invalid, or the sandbox failed."""


def _jsonable(v: Any) -> Any:
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [_jsonable(x) for x in v]
    if hasattr(v, "model_dump"):
        return _jsonable(v.model_dump(exclude_none=True))
    return v if isinstance(v, str | int | float | bool) or v is None else str(v)


def _limits() -> None:  # pragma: no cover - runs in the child before exec
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES * 2, MEMORY_BYTES * 2))
    resource.setrlimit(resource.RLIMIT_CPU, (int(TIMEOUT_S), int(TIMEOUT_S) + 1))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))


def subprocess_command(workspace: Path, import_path: str) -> tuple[list[str], dict[str, str], Path]:
    env = {"PATH": "/usr/bin:/bin", "AC_BLOCK_NETWORK": "1", "PYTHONDONTWRITEBYTECODE": "1", "HOME": "/tmp"}
    return [sys.executable, "-I", str(CHILD), import_path], env, workspace


def docker_command(image: str, workspace: Path, import_path: str) -> list[str]:
    return [
        "docker", "run", "--rm", "-i",
        "--network", "none", "--memory", "512m", "--cpus", "1", "--pids-limit", "64",
        "--read-only", "--tmpfs", "/tmp:size=16m", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "-v", f"{workspace}:/ws:ro", "-v", f"{CHILD}:/sandbox/child.py:ro", "-w", "/ws",
        "-e", "PYTHONDONTWRITEBYTECODE=1",
        image, "python", "-I", "/sandbox/child.py", import_path,
    ]  # fmt: skip


async def _exchange(
    mode: str, workspace: Path, import_path: str, payload: dict[str, Any], image: str, timeout: float
) -> dict:
    if mode == "docker":
        argv, env, cwd = docker_command(image, workspace, import_path), None, None
    else:
        argv, env, cwd = subprocess_command(workspace, import_path)
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=cwd,
        preexec_fn=_limits if mode == "subprocess" and os.name == "posix" else None,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(json.dumps(payload).encode()), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise TimeoutError(f"{import_path} ran longer than {timeout:g} s") from None
    lines = [ln for ln in out.decode(errors="replace").splitlines() if ln.strip()]
    try:
        result = json.loads(lines[-1])
    except (IndexError, json.JSONDecodeError):
        tail = err.decode(errors="replace")[-500:]
        raise ScriptError(f"{import_path} crashed (exit {proc.returncode}): {tail}") from None
    if not result.get("ok"):
        raise ScriptError(f"{import_path} failed: {result.get('error')}")
    return result


async def run_handler(settings: Settings, workspace: Path, import_path: str, event: dict[str, Any]) -> list[dict]:
    """Run an event handler in the sandbox; returns the actions it recorded (FR-X-22)."""
    mode = settings.script_execution
    result = await _exchange(
        mode, workspace, import_path, {"event": _jsonable(event)}, settings.sandbox_image, TIMEOUT_S
    )
    return list(result.get("actions") or [])


async def run_in_sandbox(
    mode: str,
    workspace: Path,
    import_path: str,
    state: dict[str, Any],
    *,
    image: str,
    timeout: float = TIMEOUT_S,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run `import_path(state)` in the sandbox and return its channel update."""
    payload = {"state": _jsonable(state), "config": _jsonable(config) if config is not None else None}
    try:
        result = await _exchange(mode, workspace, import_path, payload, image, timeout)
    except TimeoutError as exc:
        raise TimeoutError(f"script {exc}") from None
    except ScriptError as exc:
        raise ScriptError(f"script {exc}") from None
    return result.get("update") or {}


def make_runner(settings: Settings, workspace: Path, secrets: dict[str, str]) -> Callable[..., Callable]:
    """`(import_path, config=None) -> async node` running each call in the configured sandbox.

    Secrets are not passed in (FR-SCR-03: they never enter the sandbox)."""

    def node_for(import_path: str, config: dict[str, Any] | None = None) -> Callable:
        async def run(state: Any) -> dict:
            return await run_in_sandbox(
                settings.script_execution,
                workspace,
                import_path,
                dict(state),
                image=settings.sandbox_image,
                config=config,
            )

        run.__name__ = import_path.rpartition(":")[2] or "script"
        return run

    return node_for
