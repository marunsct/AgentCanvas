"""Sandbox child process: run ONE workspace function and exit (M5-T4).

Standard library only (it also runs inside a bare python image):

    python child.py scripts.score:run   < {"state": {...}, "config": {...}|null}   > {"ok": true, "update": {...}}

The working directory is the workspace root. Anything the script prints goes to stderr, so stdout carries only
the result line. With AC_BLOCK_NETWORK=1 socket connections raise (defense in depth for the subprocess mode;
the Docker mode has no network interface at all).
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


def _block_network() -> None:
    def refuse(*_a, **_k):
        raise PermissionError("network access is disabled in the script sandbox")

    socket.socket.connect = refuse  # type: ignore[method-assign]
    socket.socket.connect_ex = refuse  # type: ignore[method-assign]
    socket.create_connection = refuse  # type: ignore[assignment]
    socket.getaddrinfo = refuse  # type: ignore[assignment]


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


def main() -> int:
    if os.environ.get("AC_BLOCK_NETWORK") == "1":
        _block_network()
    out = sys.stdout
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if "tool_args" in payload:  # one call of a workspace tool: return its output
            _tool_shim()
            with contextlib.redirect_stdout(sys.stderr):
                output = _call_tool(_load(sys.argv[1]), dict(payload["tool_args"] or {}))
                if inspect.isawaitable(output):
                    output = asyncio.run(output)  # type: ignore[arg-type]
            out.write(json.dumps({"ok": True, "output": output}, default=str) + "\n")
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
        out.write(json.dumps({"ok": True, "update": result or {}}, default=str) + "\n")
        return 0
    except BaseException as exc:  # noqa: BLE001 - report everything to the host
        tb = traceback.format_exc(limit=5)
        out.write(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}", "trace": tb}) + "\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
