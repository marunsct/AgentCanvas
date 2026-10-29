"""Host side of the script sandbox: a child process (limits, no inherited secrets), a Docker container or a
LangSmith Sandbox (`agentcanvas.sandbox.langsmith`)."""

from __future__ import annotations

import asyncio
import json
import math
import os
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agentcanvas.log import get_logger
from agentcanvas.sandbox.policy import PolicyError, SandboxPolicy, load_policy
from agentcanvas.settings import Settings

log = get_logger(__name__)
_WARNED: set[tuple[str, str]] = set()  # (workspace, mode) pairs already told that sandbox.json network rules are idle

CHILD = Path(__file__).with_name("child.py")
TIMEOUT_S = 30.0
MEMORY_MB = 512


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


def _limits(memory_bytes: int, cpu_s: int) -> Callable[[], None]:
    def apply() -> None:  # pragma: no cover - runs in the child before exec
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes * 2, memory_bytes * 2))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s + 1))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))

    return apply


def proxy_env(proxy: str | None) -> dict[str, str]:
    """The variables HTTP clients (requests, httpx, urllib) read to send everything through the egress proxy."""
    if not proxy:
        return {}
    return {name: proxy for name in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY")} | {
        "NO_PROXY": "", "no_proxy": ""
    }  # fmt: skip


def subprocess_command(
    workspace: Path, import_path: str, proxy: str | None = None
) -> tuple[list[str], dict[str, str], Path]:
    env = {"PATH": "/usr/bin:/bin", "AC_BLOCK_NETWORK": "1", "PYTHONDONTWRITEBYTECODE": "1", "HOME": "/tmp"}
    if proxy:  # the child may connect to the proxy and nothing else (the network around it enforces the same)
        env |= proxy_env(proxy) | {"AC_EGRESS_PROXY": proxy.rpartition("@")[2]}
    return [sys.executable, "-I", str(CHILD), import_path], env, workspace


def docker_command(
    image: str,
    workspace: Path,
    import_path: str,
    memory_mb: int = 512,
    name: str | None = None,
    proxy: str | None = None,
) -> list[str]:
    # no network at all; with an egress allow-list, the internal network whose only way out is the egress proxy
    from agentcanvas.sandbox.docker_egress import NETWORK

    env = [arg for k, v in proxy_env(proxy).items() for arg in ("-e", f"{k}={v}")]
    return [
        "docker", "run", "--rm", "-i", *(["--name", name] if name else []),
        "--user", "65534:65534",  # nobody: never root inside the sandbox (S2)
        "--network", NETWORK if proxy else "none", *env,
        "--memory", f"{memory_mb}m", "--cpus", "1", "--pids-limit", "64",
        "--read-only", "--tmpfs", "/tmp:size=16m", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "-v", f"{workspace}:/ws:ro", "-v", f"{CHILD}:/sandbox/child.py:ro", "-w", "/ws",
        "-e", "PYTHONDONTWRITEBYTECODE=1",
        image, "python", "-I", "/sandbox/child.py", import_path,
    ]  # fmt: skip


def relay_events(result: dict) -> None:
    """Emit, into the run that called the sandbox, the events its code emitted (`agentcanvas.sdk.emit` there only
    records them): they show on the canvas and reach the project's handlers as if the code had run in-process."""
    events = result.get("events") or []
    if not events:
        return
    from langgraph.config import get_stream_writer

    try:
        write = get_stream_writer()
    except RuntimeError:  # not inside a run (a tool tried on its own): nothing to show them in
        return
    for e in events:
        if isinstance(e, dict) and isinstance(e.get("type"), str):
            write({"ac_event": e["type"], "data": e.get("data") if isinstance(e.get("data"), dict) else {}})


def _parse(import_path: str, stdout: str, stderr: str, exit_code: int | None) -> dict:
    """The child's result line (the last line on stdout)."""
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    try:
        result = json.loads(lines[-1])
    except (IndexError, json.JSONDecodeError):
        raise ScriptError(f"{import_path} crashed (exit {exit_code}): {stderr[-500:]}") from None
    if not result.get("ok"):
        raise ScriptError(f"{import_path} failed: {result.get('error')}")
    return result


async def _exchange(
    mode: str,
    workspace: Path,
    import_path: str,
    payload: dict[str, Any],
    image: str,
    timeout: float,
    memory_mb: int = MEMORY_MB,
    proxy: str | None = None,
) -> dict:
    name = f"ac-sbx-{uuid.uuid4().hex[:12]}"
    if mode == "docker":
        argv, env, cwd = docker_command(image, workspace, import_path, memory_mb, name, proxy), None, None
    else:
        argv, env, cwd = subprocess_command(workspace, import_path, proxy)
    limits = _limits(memory_mb * 1024 * 1024, math.ceil(timeout))
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=cwd,
        preexec_fn=limits if mode == "subprocess" and os.name == "posix" else None,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(json.dumps(payload).encode()), timeout)
    except TimeoutError:
        await _stop(proc, mode, name)
        raise TimeoutError(f"{import_path} ran longer than {timeout:g} s") from None
    except BaseException:  # the run was cancelled (or its time budget ran out): its code must not live on
        await asyncio.shield(_stop(proc, mode, name))
        raise
    return _parse(import_path, out.decode(errors="replace"), err.decode(errors="replace"), proc.returncode)


async def _stop(proc: asyncio.subprocess.Process, mode: str, name: str) -> None:
    if proc.returncode is None:
        proc.kill()
        await proc.wait()
    if mode == "docker":  # killing the client leaves the container running: stop the container itself
        killer = await asyncio.create_subprocess_exec(
            "docker", "kill", name, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        await killer.wait()


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
    """Run `import_path(state)` in a local sandbox (subprocess or docker) and return its channel update."""
    payload = {"state": _jsonable(state), "config": _jsonable(config) if config is not None else None}
    try:
        result = await _exchange(mode, workspace, import_path, payload, image, timeout)
    except TimeoutError as exc:
        raise TimeoutError(f"script {exc}") from None
    except ScriptError as exc:
        raise ScriptError(f"script {exc}") from None
    relay_events(result)
    return result.get("update") or {}


# tests point the remote mode at an in-process service
REMOTE_TRANSPORT: Any = None


async def _remote_exchange(
    settings: Settings,
    workspace: Path,
    import_path: str,
    payload: dict[str, Any],
    timeout: float,
    memory_mb: int,
    proxy: str | None = None,
) -> dict:
    """Run one call in the sandbox service (`script_execution = "remote"`): its own container, with no database
    credentials, keys or other projects' files. The workspace travels with each call."""
    import base64

    import httpx

    from agentcanvas.sandbox.service import WorkspaceTooLarge, pack_workspace

    if not settings.sandbox_url or not settings.sandbox_token:
        raise ScriptError("script execution 'remote' needs AGENTCANVAS_SANDBOX_URL and AGENTCANVAS_SANDBOX_TOKEN")
    try:
        packed = await asyncio.to_thread(pack_workspace, workspace)
    except WorkspaceTooLarge as exc:
        raise ScriptError(f"{import_path} failed: {exc}") from None
    body = {
        "import_path": import_path, "payload": payload, "workspace": base64.b64encode(packed).decode(),
        "timeout_s": timeout, "memory_mb": memory_mb, "egress_proxy": proxy,
    }  # fmt: skip
    try:
        async with httpx.AsyncClient(
            base_url=settings.sandbox_url, transport=REMOTE_TRANSPORT, timeout=timeout + 10,
            headers={"Authorization": f"Bearer {settings.sandbox_token}"},
        ) as client:  # fmt: skip
            r = await client.post("/run", json=body)
    except httpx.HTTPError as exc:
        raise ScriptError(
            f"{import_path} failed: the sandbox service is not reachable ({type(exc).__name__})"
        ) from None
    if r.status_code != 200:
        raise ScriptError(f"{import_path} failed: the sandbox service answered {r.status_code}")
    result = r.json()
    if result.get("timeout"):
        raise TimeoutError(f"{import_path} ran longer than {timeout:g} s")
    if not result.get("ok"):
        raise ScriptError(str(result.get("error") or f"{import_path} failed"))
    return result


class SandboxRunner:
    """Sandboxed execution of one project's workspace code in the configured mode.

    `runner(import_path, config=None)` is an async graph node; `await runner.handle(import_path, event)` runs an
    event handler and returns the actions it recorded (FR-X-22). Limits and egress come from the project's
    `sandbox.json` (FR-X-08). Secrets are never passed into the sandbox (FR-SCR-03); in the LangSmith mode the auth
    proxy injects them into outbound requests instead.
    """

    def __init__(
        self,
        settings: Settings,
        workspace: Path,
        secrets: dict[str, str],
        langsmith_client: Callable[[Settings], Any] | None = None,
    ):
        self.settings, self.workspace, self.secrets = settings, workspace, secrets
        self._langsmith_client = langsmith_client
        self._box: Any = None

    def __call__(self, import_path: str, config: dict[str, Any] | None = None) -> Callable:
        async def run(state: Any) -> dict:
            payload = {"state": _jsonable(dict(state)), "config": _jsonable(config) if config is not None else None}
            try:
                result = await self.exchange(import_path, payload)
            except TimeoutError as exc:
                raise TimeoutError(f"script {exc}") from None
            except ScriptError as exc:
                raise ScriptError(f"script {exc}") from None
            relay_events(result)
            return result.get("update") or {}

        run.__name__ = import_path.rpartition(":")[2] or "script"
        return run

    async def handle(self, import_path: str, event: dict[str, Any]) -> list[dict]:
        result = await self.exchange(import_path, {"event": _jsonable(event)})
        return list(result.get("actions") or [])

    async def exchange(self, import_path: str, payload: dict[str, Any]) -> dict:
        try:
            policy = load_policy(self.workspace)
        except PolicyError as exc:
            raise ScriptError(str(exc)) from None
        mode = self.settings.script_execution
        proxy = await self._egress(policy, mode)
        if mode == "remote":
            return await _remote_exchange(
                self.settings, self.workspace, import_path, payload, policy.timeout_s, policy.memory_mb, proxy
            )
        if mode != "langsmith":
            image = self.settings.sandbox_image
            return await _exchange(
                mode, self.workspace, import_path, payload, image, policy.timeout_s, policy.memory_mb, proxy
            )
        if self._box is None:
            from agentcanvas.sandbox.langsmith import LangSmithBox

            self._box = LangSmithBox(self.settings, self.workspace, self.secrets, self._langsmith_client)
        try:
            out, err, code = await self._box.exchange(import_path, json.dumps(payload).encode(), policy)
        except PolicyError as exc:
            raise ScriptError(str(exc)) from None
        return _parse(import_path, out, err, code)

    async def _egress(self, policy: SandboxPolicy, mode: str) -> str | None:
        """The proxy URL that lets this call reach the policy's allow-list, or None for no network at all.

        `docker` and `remote` enforce the list through the egress proxy. `inprocess` and `subprocess` cannot: in
        staging and production a list there is refused (the code would get no network, or all of it, silently);
        in development it is logged once and the code runs with no network (`subprocess`) or the API's own."""
        allow = policy.allow_list()
        if not allow or mode == "langsmith":  # LangSmith's own auth proxy applies the list
            return None
        if mode in ("docker", "remote"):
            from agentcanvas.sandbox import docker_egress
            from agentcanvas.sandbox.egress import proxy_url

            secret = docker_egress.secret(self.settings)
            if mode == "remote":
                if not self.settings.egress_proxy or not self.settings.egress_secret:
                    raise ScriptError(
                        "sandbox.json allows network access, and script execution 'remote' reaches it through the "
                        "egress proxy: set AGENTCANVAS_EGRESS_PROXY and AGENTCANVAS_EGRESS_SECRET"
                    )
                address = self.settings.egress_proxy
            else:
                try:
                    address = self.settings.egress_proxy or await docker_egress.ensure(self.settings)
                except docker_egress.EgressUnavailable as exc:
                    raise ScriptError(f"sandbox.json allows network access, but {exc}") from None
            return proxy_url(address, allow, secret, policy.timeout_s + 60)
        if self.settings.env in ("staging", "prod"):
            raise ScriptError(
                f"sandbox.json allows network access, which script execution '{mode}' cannot restrict; "
                "use AGENTCANVAS_SCRIPT_EXECUTION=docker, remote or langsmith, or empty the egress list"
            )
        if (key := (str(self.workspace), mode)) not in _WARNED:
            _WARNED.add(key)  # once per workspace: sandbox.json allows a network that this mode cannot restrict
            log.warning(
                "sandbox.egress_not_enforced", mode=mode, workspace=str(self.workspace),
                hint="egress rules in sandbox.json are enforced with AGENTCANVAS_SCRIPT_EXECUTION=docker, remote or "
                "langsmith; here the code has " + ("the API's network" if mode == "inprocess" else "no network"),
            )  # fmt: skip
        return None

    async def aclose(self) -> None:
        if self._box is not None:
            await self._box.aclose()
