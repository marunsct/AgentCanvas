"""Sandbox child process: run ONE workspace function and exit (M5-T4).

Standard library only (it also runs inside a bare python image):

    python child.py scripts.score:run   < state.json   > {"ok": true, "update": {...}}

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
    path = os.path.join(os.getcwd(), *module.split(".")) + ".py"
    spec = importlib.util.spec_from_file_location(module, path)
    if spec is None or spec.loader is None or not os.path.isfile(path):
        raise ModuleNotFoundError(f"workspace module '{module}' not found")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module] = mod
    spec.loader.exec_module(mod)
    return getattr(mod, attr)


def main() -> int:
    if os.environ.get("AC_BLOCK_NETWORK") == "1":
        _block_network()
    out = sys.stdout
    try:
        state = json.loads(sys.stdin.read() or "{}")
        with contextlib.redirect_stdout(sys.stderr):  # user prints must not corrupt the result
            fn = _load(sys.argv[1])
            result = fn(state)
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
