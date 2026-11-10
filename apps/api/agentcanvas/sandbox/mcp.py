"""Local (stdio) app connectors in the sandbox: the MCP server process runs in a sandbox container, not on the API
server, and the MCP client's messages are relayed to its stdin and from its stdout.

- `docker`: a container of its own (`docker create` + `docker start -ai`), on no network or only the egress
  network, read-only, as nobody, with the memory, CPU and process limits of `sandbox.json`'s `mcp` section.
- `remote`: one WebSocket to the sandbox service (`/mcp`), which runs the process with the same limits and relays
  its stdio over that connection; the process lives as long as the connection.

Either way the server reaches only the hosts of the project's egress list (`mcp_child` gives it a relay to the
egress proxy), gets only the environment its connector block sets (its own secrets included, never the API's), and
is stopped after the time limits. `McpSessions` keeps one sandbox per run (or per conversation, for a browser that
keeps its pages), bounded per API server.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import tempfile
import time
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Unpack

import anyio
import mcp_types as types
from fastmcp.client.transports import ClientTransport
from fastmcp.client.transports.base import SessionKwargs, TransportOptions
from mcp import ClientSession
from mcp.shared.message import SessionMessage

from agentcanvas.log import get_logger
from agentcanvas.sandbox.policy import McpPolicy, PolicyError, load_policy
from agentcanvas.sandbox.runners import MCP_CHILD, QUEUE_WAIT_S, _docker

log = get_logger(__name__)

MAX_LINE = 32 * 1024 * 1024  # one MCP message (a browser screenshot arrives as base64 in one line)
STDERR_TAIL = 16 * 1024
PROCESSES = 512  # a browser runs a process per page and many threads
TMP_MB = 512  # the connector's /tmp (its home: a browser profile, npm's cache)
START_S = 120.0  # starting the server, after a free sandbox was found
MODES = ("docker", "remote")


class McpSandboxError(RuntimeError):
    """A local app connector could not run in the sandbox (not allowed here, not started, or stopped)."""


@dataclass(frozen=True)
class McpServer:
    label: str  # the connector block's name, for messages
    command: str
    args: tuple[str, ...]
    env: tuple[tuple[str, str], ...]  # resolved: secrets are values here


# ------------------------------------------------------------------ pipes: the server's stdin and stdout ----
class Pipe:
    """One connector process's stdin and stdout, wherever it runs."""

    problem = ""  # why it ended, with the end of what it printed on stderr

    async def receive(self) -> bytes | None:  # one line; None once it has ended
        raise NotImplementedError

    async def send(self, line: bytes) -> None:
        raise NotImplementedError

    async def aclose(self) -> None:
        raise NotImplementedError


class ProcessPipe(Pipe):
    """A local process's pipes (`docker start -ai`): `on_close` removes the container however it ended."""

    def __init__(self, proc: asyncio.subprocess.Process, on_close: Callable[[], Awaitable[Any]] | None = None):
        self.proc, self.on_close = proc, on_close
        self.tail = bytearray()
        self._stderr = asyncio.ensure_future(self._read_stderr())

    async def _read_stderr(self) -> None:
        assert self.proc.stderr is not None
        while chunk := await self.proc.stderr.read(65536):
            self.tail += chunk
            del self.tail[:-STDERR_TAIL]

    async def receive(self) -> bytes | None:
        assert self.proc.stdout is not None
        try:
            line = await self.proc.stdout.readline()
        except ValueError:  # longer than MAX_LINE
            self.problem = f"it sent a message over {MAX_LINE // (1024 * 1024)} MB"
            return None
        if not line:
            await asyncio.wait([self._stderr], timeout=2)
            self.problem = self.problem or _ended(self.proc.returncode, self.tail)
            return None
        return line

    async def send(self, line: bytes) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(line)
        await self.proc.stdin.drain()

    async def aclose(self) -> None:
        if self.proc.stdin is not None and not self.proc.stdin.is_closing():
            self.proc.stdin.close()  # a stdio server ends when its input does
        try:
            await asyncio.wait_for(self.proc.wait(), 2)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                self.proc.kill()
            await self.proc.wait()
        finally:
            self._stderr.cancel()
            if self.on_close is not None:
                await asyncio.shield(self.on_close())


class WebSocketPipe(Pipe):
    """The sandbox service's relay: binary frames carry the server's lines, text frames say how it ended."""

    def __init__(self, ws: Any):
        self.ws = ws

    async def receive(self) -> bytes | None:
        from websockets.exceptions import ConnectionClosed

        while True:
            try:
                msg = await self.ws.recv()
            except ConnectionClosed:
                self.problem = self.problem or "the sandbox service closed the connection"
                return None
            if isinstance(msg, bytes):
                return msg
            with contextlib.suppress(ValueError):
                ended = json.loads(msg)
                if isinstance(ended, dict) and "exit" in ended:
                    self.problem = str(ended.get("error") or f"it exited ({ended['exit']})")

    async def send(self, line: bytes) -> None:
        from websockets.exceptions import ConnectionClosed

        try:
            await self.ws.send(line)
        except ConnectionClosed:
            raise anyio.BrokenResourceError from None

    async def aclose(self) -> None:
        await self.ws.close()


def _ended(code: int | None, tail: bytes | bytearray) -> str:
    said = bytes(tail).decode(errors="replace").strip()[-500:]
    return f"it exited ({code})" + (f": {said}" if said else "")


# ------------------------------------------------------------------ the MCP client's transport ----
class SandboxTransport(ClientTransport):
    """An MCP client transport whose server is a process in the sandbox (`open_pipe` starts it)."""

    def __init__(self, open_pipe: Callable[[], Awaitable[tuple[Pipe, McpPolicy]]], label: str):
        self.open_pipe, self.label = open_pipe, label
        self.limits: McpPolicy | None = None  # the policy's limits, once a sandbox was started
        self.problem = ""  # why the last sandbox ended

    def __repr__(self) -> str:
        return f"<SandboxTransport {self.label}>"

    @contextlib.asynccontextmanager
    async def connect_session(
        self, *, transport_options: TransportOptions | None = None, **session_kwargs: Unpack[SessionKwargs]
    ) -> AsyncGenerator[ClientSession, None]:
        options = transport_options or TransportOptions()
        pipe, self.limits = await self.open_pipe()
        read_writer, read_stream = anyio.create_memory_object_stream[SessionMessage | Exception](0)
        write_stream, write_reader = anyio.create_memory_object_stream[SessionMessage](0)

        async def reader() -> None:
            async with read_writer:
                while (line := await pipe.receive()) is not None:
                    if not line.strip():
                        continue
                    try:
                        msg: SessionMessage | Exception = SessionMessage(
                            types.jsonrpc_message_adapter.validate_json(line, by_name=False)
                        )
                    except ValueError as exc:  # a line that is not a message: the session reports it
                        msg = exc
                    await read_writer.send(msg)
                self.problem = pipe.problem

        async def writer() -> None:
            try:
                async with write_reader:
                    async for m in write_reader:
                        data = m.message.model_dump_json(by_alias=True, exclude_unset=True) + "\n"
                        await pipe.send(data.encode())
            except (anyio.BrokenResourceError, anyio.ClosedResourceError, OSError):
                await read_writer.aclose()  # the server is gone: pending requests end instead of hanging

        try:
            async with anyio.create_task_group() as tg:
                tg.start_soon(reader)
                tg.start_soon(writer)
                try:
                    async with options.session_class(read_stream, write_stream, **session_kwargs) as session:
                        yield session
                finally:
                    tg.cancel_scope.cancel()
        finally:
            with anyio.CancelScope(shield=True):
                await pipe.aclose()
            self.problem = self.problem or pipe.problem


# ------------------------------------------------------------------ starting a server in the sandbox ----
async def open_connector(runner: Any, server: McpServer) -> tuple[Pipe, McpPolicy]:
    """Start `server` in the sandbox `runner` (a `SandboxRunner`) uses, with its project's policy."""
    settings = runner.settings
    mode = settings.script_execution
    if mode not in MODES:
        raise McpSandboxError(
            f"'{server.label}': local app connectors run in the sandbox with AGENTCANVAS_SCRIPT_EXECUTION=docker or "
            f"remote; this server uses '{mode}'"
        )
    try:
        policy = load_policy(runner.workspace)
    except PolicyError as exc:
        raise McpSandboxError(str(exc)) from None
    from agentcanvas.sandbox.runners import ScriptError

    try:  # the egress credential lasts as long as the sandbox may
        proxy = await runner.egress(policy, mode, ttl_s=policy.mcp.lifetime_s + QUEUE_WAIT_S + START_S)
    except ScriptError as exc:
        raise McpSandboxError(f"'{server.label}': {exc}") from None
    opener = _open_docker if mode == "docker" else _open_remote
    return await opener(settings, runner.workspace, server, policy.mcp, proxy), policy.mcp


def docker_mcp_command(
    image: str, name: str, workspace: Path, server: McpServer, limits: McpPolicy, env_file: str, online: bool
) -> list[str]:
    """`docker create` for one connector sandbox (`--init` reaps the processes a browser leaves)."""
    from agentcanvas.sandbox.docker_egress import NETWORK

    return [
        "docker", "create", "-i", "--name", name, "--label", "agentcanvas.mcp=1", "--init",
        "--user", "65534:65534", "--network", NETWORK if online else "none", "--env-file", env_file,
        "--memory", f"{limits.memory_mb}m", "--memory-swap", f"{limits.memory_mb}m", "--cpus", "1",
        "--pids-limit", str(PROCESSES), "--read-only", "--tmpfs", f"/tmp:size={TMP_MB}m",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "-v", f"{workspace}:/ws:ro", "-v", f"{MCP_CHILD}:/sandbox/mcp_child.py:ro", "-w", "/ws",
        image, "python3", "-I", "/sandbox/mcp_child.py", server.command, *server.args,
    ]  # fmt: skip


def _env_lines(env: dict[str, str]) -> str:
    for k, v in env.items():
        if "\n" in v or "\r" in v or "=" in k or not k:
            raise McpSandboxError(f"environment variable '{k}' of a connector cannot hold a line break")
    return "".join(f"{k}={v}\n" for k, v in env.items())


async def _open_docker(settings: Any, workspace: Path, server: McpServer, limits: McpPolicy, proxy: str | None) -> Pipe:
    name = f"ac-mcp-{uuid.uuid4().hex[:12]}"
    env = {"HOME": "/tmp", **dict(server.env), **({"AC_EGRESS_UPSTREAM": proxy} if proxy else {})}
    image = settings.sandbox_mcp_image or settings.sandbox_image
    folder = tempfile.mkdtemp(prefix="ac-mcp-env-")  # 0700: the connector's secrets, only until it is created
    try:
        env_file = os.path.join(folder, "env")
        fd = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(_env_lines(env))
        argv = docker_mcp_command(image, name, workspace, server, limits, env_file, online=bool(proxy))
        failed = await _docker("create", argv)
    finally:
        shutil.rmtree(folder, ignore_errors=True)

    async def remove() -> None:
        await _docker("rm", ["docker", "rm", "-f", name])

    if failed:
        await remove()
        raise McpSandboxError(f"'{server.label}': the connector's sandbox was not created: {failed[-300:]}")
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "start", "-ai", name, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=MAX_LINE,
        )  # fmt: skip
    except BaseException:
        await asyncio.shield(remove())
        raise
    return ProcessPipe(proc, on_close=remove)


def _ws_url(base: str) -> str:
    base = base.rstrip("/")
    if base.startswith("https://"):
        return "wss://" + base.removeprefix("https://") + "/mcp"
    return "ws://" + base.removeprefix("http://") + "/mcp"


async def _open_remote(settings: Any, workspace: Path, server: McpServer, limits: McpPolicy, proxy: str | None) -> Pipe:
    import base64

    from websockets.asyncio.client import connect
    from websockets.exceptions import WebSocketException

    from agentcanvas.sandbox.service import WorkspaceTooLarge, pack_workspace

    url = settings.sandbox_mcp_url or settings.sandbox_url
    if not url or not settings.sandbox_token:
        raise McpSandboxError("local app connectors in the sandbox need AGENTCANVAS_SANDBOX_URL and _TOKEN")
    try:
        packed = await asyncio.to_thread(pack_workspace, workspace)
    except WorkspaceTooLarge as exc:
        raise McpSandboxError(f"'{server.label}': {exc}") from None
    start = {
        "command": server.command, "args": list(server.args), "env": dict(server.env),
        "workspace": base64.b64encode(packed).decode(), "memory_mb": limits.memory_mb,
        "lifetime_s": limits.lifetime_s, "egress_proxy": proxy,
    }  # fmt: skip
    try:  # never through a proxy of the API's own environment: the service is on the sandbox-control network
        ws = await connect(
            _ws_url(url), additional_headers={"Authorization": f"Bearer {settings.sandbox_token}"}, proxy=None,
            max_size=MAX_LINE + 1024, open_timeout=30, ping_interval=20, ping_timeout=60,
        )  # fmt: skip
    except (OSError, TimeoutError, WebSocketException) as exc:
        raise McpSandboxError(
            f"'{server.label}': the sandbox service is not reachable ({type(exc).__name__}: {exc})"
        ) from None
    try:
        await ws.send(json.dumps(start))
        reply = json.loads(await asyncio.wait_for(ws.recv(), QUEUE_WAIT_S + START_S))
    except BaseException as exc:
        await asyncio.shield(ws.close())
        if isinstance(exc, TimeoutError | WebSocketException | ValueError):
            raise McpSandboxError(f"'{server.label}': the sandbox service did not start it ({exc})") from None
        raise
    if not isinstance(reply, dict) or not reply.get("ready"):
        await ws.close()
        error = reply.get("error") if isinstance(reply, dict) else None
        raise McpSandboxError(f"'{server.label}' did not start in the sandbox: {error or reply}")
    return WebSocketPipe(ws)


# ------------------------------------------------------------------ one sandbox per run or conversation ----
@dataclass
class _Live:
    client: Any  # a connected fastmcp Client
    started: float
    used: float
    limits: McpPolicy
    active: int = 0

    def over(self, now: float) -> bool:
        idle = self.active == 0 and now - self.used > self.limits.idle_s
        return idle or now - self.started > self.limits.lifetime_s or not self.client.is_connected()


REAP_S = 1.0  # how often sandboxes past their time limits are looked for


class McpSessions:
    """The connector sandboxes of this API server, keyed by (scope, id, connector): a run's, kept until it ends, or a
    conversation's, kept while it is in use. Each is stopped once idle for its `idle_s`, past its `lifetime_s` or
    when the run ends; at most `limit` exist at once (the least recently used idle one makes room)."""

    def __init__(self, limit: int = 8):
        self.limit = limit
        self._live: dict[tuple[str, str, str], _Live] = {}
        self._opening: dict[tuple[str, str, str], asyncio.Lock] = {}
        self._room = asyncio.Lock()
        self._starting = 0
        self._reaper: asyncio.Task[None] | None = None

    def __len__(self) -> int:
        return len(self._live)

    @contextlib.asynccontextmanager
    async def use(self, key: tuple[str, str, str], open_client: Callable[[], Any]) -> AsyncIterator[Any]:
        """The scope's connected client, started when it has none (or its last one ended)."""
        live = await self._get(key, open_client)
        live.active += 1
        try:
            yield live.client
        finally:
            live.active -= 1
            live.used = time.monotonic()

    @contextlib.asynccontextmanager
    async def once(self, open_client: Callable[[], Any]) -> AsyncIterator[Any]:
        """A sandbox for one use (listing a connector's tools), stopped right after."""
        await self._reserve()
        try:
            client = await _start(open_client())
        finally:
            self._starting -= 1
        try:
            yield client
        finally:
            await asyncio.shield(_stop(client))

    async def _get(self, key: tuple[str, str, str], open_client: Callable[[], Any]) -> _Live:
        try:
            return await self._get_locked(key, open_client)
        finally:
            self._forget(key)  # when it failed to start

    async def _get_locked(self, key: tuple[str, str, str], open_client: Callable[[], Any]) -> _Live:
        async with self._opening.setdefault(key, asyncio.Lock()):
            now = time.monotonic()
            live = self._live.get(key)
            if live is not None and not live.over(now):
                live.used = now
                return live
            if live is not None:
                await self._close(key)
            await self._reserve()
            try:
                client = await _start(open_client())
                limits = getattr(client.transport, "limits", None) or McpPolicy()
                live = self._live[key] = _Live(client, time.monotonic(), time.monotonic(), limits)
            finally:
                self._starting -= 1
            if self._reaper is None or self._reaper.done():
                self._reaper = asyncio.create_task(self._reap())
            return live

    async def _reserve(self) -> None:
        async with self._room:
            while len(self._live) + self._starting >= self.limit:
                idle = sorted((v.used, k) for k, v in self._live.items() if v.active == 0)
                if not idle:
                    raise McpSandboxError(
                        f"{self.limit} app connector sandboxes are in use on this server "
                        "(AGENTCANVAS_SANDBOX_MCP_SESSIONS); try again when a run ends"
                    )
                await self._close(idle[0][1])
            self._starting += 1

    async def _close(self, key: tuple[str, str, str]) -> None:
        live = self._live.pop(key, None)
        self._forget(key)
        if live is not None:
            await asyncio.shield(_stop(live.client))

    def _forget(self, key: tuple[str, str, str]) -> None:
        """Drop a scope's lock once nothing holds it (a run id is never used again)."""
        lock = self._opening.get(key)
        if lock is not None and not lock.locked() and key not in self._live:
            del self._opening[key]

    async def _reap(self) -> None:
        while self._live:
            await asyncio.sleep(REAP_S)
            now = time.monotonic()
            for key in [k for k, v in self._live.items() if v.over(now)]:
                log.info("sandbox.mcp_stopped", scope=key[0], connector=key[2][:40], reason="time limit or idle")
                await self._close(key)

    async def end(self, scope: str, scope_id: str) -> None:
        """Stop the sandboxes of a run (or conversation) that ended."""
        for key in [k for k in self._live if k[0] == scope and k[1] == scope_id]:
            await self._close(key)

    async def aclose(self) -> None:
        for key in list(self._live):
            await self._close(key)
        if self._reaper is not None:
            self._reaper.cancel()


async def _start(client: Any) -> Any:
    transport = client.transport
    try:
        await asyncio.wait_for(client.__aenter__(), QUEUE_WAIT_S + START_S + 10)
    except McpSandboxError:
        raise
    except Exception as exc:  # noqa: BLE001 - the server did not answer the MCP handshake: say why it ended
        if isinstance(exc.__cause__, McpSandboxError):  # the client wraps what the transport raised
            raise exc.__cause__ from None
        why = getattr(transport, "problem", "") or f"{type(exc).__name__}: {exc}"
        raise McpSandboxError(f"'{getattr(transport, 'label', '?')}' did not start in the sandbox: {why}") from None
    return client


async def _stop(client: Any) -> None:
    with contextlib.suppress(Exception):
        await client.close()


SESSIONS = McpSessions()  # this API server's connector sandboxes
