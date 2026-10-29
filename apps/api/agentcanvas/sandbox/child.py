"""Sandbox child process: run ONE workspace function and exit (M5-T4).

Standard library only (it also runs inside a bare python image):

    python child.py scripts.score:run   < {"state": {...}, "config": {...}|null}   > {"ok": true, "update": {...}}

The working directory is the workspace root. Anything the script prints goes to stderr, so stdout carries only
the result line. With AC_BLOCK_NETWORK=1 socket connections raise, except to the egress proxy the payload names
(defense in depth for the subprocess mode; the Docker mode has no network interface, or only the egress network).
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import inspect
import json
import os
import socket
import sys
import traceback


def _block_network(proxy: str = "") -> None:
    """No connections, or only to the egress proxy (`host:port`) when the project allows some
    hosts: HTTP clients reach those through it (HTTPS_PROXY), and the proxy checks the list.

    This is a guard against mistakes, not isolation: code that imports `_socket`, uses ctypes or starts a program
    goes around it. What keeps code in is the container's network (docker: none or the internal egress network;
    the sandbox service: internal networks, and the API refusing requests from them)."""
    host, _, port = proxy.rpartition(":")
    real_getaddrinfo = socket.getaddrinfo
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex
    proxy_ips = {info[4][0] for info in real_getaddrinfo(host, int(port))} if proxy else set()

    def to_proxy(address) -> bool:
        return isinstance(address, tuple) and len(address) >= 2 and address[0] in proxy_ips and address[1] == int(port)

    def refuse(*_a, **_k):
        raise PermissionError(
            "network access is disabled in the script sandbox"
            + ("; the hosts in sandbox.json are reached through the egress proxy (HTTPS_PROXY)" if proxy else "")
        )

    def getaddrinfo(name, *a, **k):
        if proxy and name == host:
            return real_getaddrinfo(name, *a, **k)
        return refuse()

    def connect(self, address):
        return real_connect(self, address) if to_proxy(address) else refuse()

    def connect_ex(self, address):
        return real_connect_ex(self, address) if to_proxy(address) else refuse()

    socket.socket.connect = connect  # type: ignore[method-assign]
    socket.socket.connect_ex = connect_ex  # type: ignore[method-assign]
    socket.getaddrinfo = getaddrinfo  # type: ignore[assignment]
    if not proxy:
        socket.create_connection = refuse  # type: ignore[assignment]


def _load(import_path: str):
    module, _, attr = import_path.partition(":")
    # workspace files import each other (`from scripts.textutil import x`, S17); last on the path, so the
    # standard library always wins over a file of the same name
    if os.getcwd() not in sys.path:
        sys.path.append(os.getcwd())
    path = os.path.join(os.getcwd(), *module.split(".")) + ".py"
    spec = importlib.util.spec_from_file_location(module, path)
    if spec is None or spec.loader is None or not os.path.isfile(path):
        raise ModuleNotFoundError(f"workspace module '{module}' not found")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module] = mod
    spec.loader.exec_module(mod)
    return getattr(mod, attr)


def _tool_shim() -> None:
    """Workspace tools are written with LangChain's `@tool`; a bare sandbox image has no LangChain, so a stand-in
    decorator keeps the plain function (the host already knows the tool's name and arguments)."""
    try:
        import langchain_core.tools  # noqa: F401
    except ImportError:
        import types

        def tool(*args, **_kw):
            if len(args) == 1 and callable(args[0]):
                return args[0]
            return lambda fn: fn

        tools = types.ModuleType("langchain_core.tools")
        tools.tool = tool  # type: ignore[attr-defined]
        core = types.ModuleType("langchain_core")
        core.tools = tools  # type: ignore[attr-defined]
        sys.modules.setdefault("langchain_core", core)
        sys.modules.setdefault("langchain_core.tools", tools)


def _sdk_shim() -> None:
    """Workspace code imports `agentcanvas.sdk` (`@on`, `@canvas_node`, `Event`, `Actions`); a bare sandbox image
    (docker, LangSmith) has no AgentCanvas, so a stand-in with the same decorators keeps the plain functions. `secret`
    needs the host and says so; `emit` is collected (`_collect_emits`)."""
    try:
        import agentcanvas.sdk  # noqa: F401
    except ImportError:
        import types

        def _keep(*_a, **_kw):
            return lambda fn: fn

        def _host_only(name):
            def fail(*_a, **_kw):
                raise RuntimeError(f"agentcanvas.sdk.{name} is not available inside the sandbox")

            return fail

        sdk = types.ModuleType("agentcanvas.sdk")
        sdk.on = _keep  # type: ignore[attr-defined]
        sdk.canvas_node = _keep  # type: ignore[attr-defined]
        sdk.Event = _Event  # type: ignore[attr-defined]
        sdk.Actions = _Actions  # type: ignore[attr-defined]
        sdk.secret = _host_only("secret")  # type: ignore[attr-defined]
        pkg = types.ModuleType("agentcanvas")
        pkg.__path__ = []  # type: ignore[attr-defined]
        pkg.sdk = sdk  # type: ignore[attr-defined]
        sys.modules.setdefault("agentcanvas", pkg)
        sys.modules.setdefault("agentcanvas.sdk", sdk)


_EMITTED: list[dict] = []  # events the code emitted: returned with its result, emitted into the run by the host


def _collect_emits() -> None:
    """`agentcanvas.sdk.emit` here has no run to write to (the run is in the host process): record the events,
    which go back with the result."""

    def emit(event_type, data=None):
        _EMITTED.append({"type": str(event_type), "data": dict(data or {})})

    sys.modules["agentcanvas.sdk"].emit = emit  # type: ignore[attr-defined]


def _call_tool(obj, args: dict):
    """One call of a workspace tool: a LangChain tool object (`ainvoke`/`invoke`) or a plain function."""
    if hasattr(obj, "ainvoke"):
        return obj.ainvoke(args)
    if hasattr(obj, "invoke"):
        return obj.invoke(args)
    return obj(**args)


class _Event:
    def __init__(self, e: dict):
        self.id, self.type, self.data, self.source = (
            e.get("id", ""),
            e["type"],
            e.get("data") or {},
            e.get("source", ""),
        )


class _Actions:
    """Same surface as agentcanvas.sdk.Actions: handlers only record what should happen."""

    def __init__(self):
        self.recorded: list[dict] = []

    def start_workflow(self, workflow, *, input=None, thread_id=None):
        self.recorded.append(
            {"action": "start_workflow", "workflow": workflow, "input": input or {}, "thread_id": thread_id}
        )

    def resume(self, thread_id, value=None):
        self.recorded.append({"action": "resume", "thread_id": thread_id, "value": value})

    def emit(self, event_type, data=None):
        self.recorded.append({"action": "emit", "type": event_type, "data": data or {}})


def _not_dumpable() -> None:
    """Linux: other processes of the same user cannot read this one's /proc/<pid>/environ, memory or file handles,
    or ptrace it. In a shared sandbox service, one call's code cannot read another call's (2026-10-29 review, S-2)."""
    if sys.platform != "linux":
        return
    try:
        import ctypes

        ctypes.CDLL(None, use_errno=True).prctl(4, 0, 0, 0, 0)  # PR_SET_DUMPABLE, 0
    except (OSError, AttributeError):
        pass


def _proxy_from(payload: dict) -> str:
    """The egress proxy (`http://<signed list>@host:port`) from the payload, not the environment, where another
    process of the same user could read it; set where HTTP clients look for it. Returns its `host:port`."""
    proxy = str(payload.pop("egress_proxy", None) or "")
    if proxy:
        for name in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY"):
            os.environ[name] = proxy
        os.environ["NO_PROXY"] = os.environ["no_proxy"] = ""
    return proxy.rpartition("@")[2] if proxy else ""


def main() -> int:
    _not_dumpable()
    out = sys.stdout
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        proxy = _proxy_from(payload)
        if os.environ.get("AC_BLOCK_NETWORK") == "1":
            _block_network(proxy)
        _sdk_shim()
        _collect_emits()
        if "tool_args" in payload:  # one call of a workspace tool: return its output
            _tool_shim()
            with contextlib.redirect_stdout(sys.stderr):
                output = _call_tool(_load(sys.argv[1]), dict(payload["tool_args"] or {}))
                if inspect.isawaitable(output):
                    output = asyncio.run(output)  # type: ignore[arg-type]
            out.write(json.dumps({"ok": True, "output": output, "events": _EMITTED}, default=str) + "\n")
            return 0
        if "event" in payload:  # an event handler: return the actions it recorded
            act = _Actions()
            with contextlib.redirect_stdout(sys.stderr):
                done = _load(sys.argv[1])(_Event(payload["event"]), act)
                if inspect.isawaitable(done):
                    asyncio.run(done)  # type: ignore[arg-type]
            out.write(json.dumps({"ok": True, "actions": act.recorded}, default=str) + "\n")
            return 0
        state, config = payload.get("state", {}), payload.get("config")
        with contextlib.redirect_stdout(sys.stderr):  # user prints must not corrupt the result
            fn = _load(sys.argv[1])
            result = fn(state) if config is None else fn(state, config)  # custom nodes get their config
            if inspect.isawaitable(result):
                result = asyncio.run(result)  # type: ignore[arg-type]
        if result is not None and not isinstance(result, dict):
            raise TypeError(f"a script must return a dict of channel updates, got {type(result).__name__}")
        out.write(json.dumps({"ok": True, "update": result or {}, "events": _EMITTED}, default=str) + "\n")
        return 0
    except BaseException as exc:  # noqa: BLE001 - report everything to the host
        tb = traceback.format_exc(limit=5)
        out.write(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}", "trace": tb}) + "\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
