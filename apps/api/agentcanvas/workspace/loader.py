"""Load workspace code by file, per project, with hot reload (M5-T3).

Two projects may both have `scripts/score.py`: each workspace gets its own synthetic package
(`_acws_<hash>`), so modules never collide in `sys.modules`. A file is re-executed when it changes,
so editing a script and running again uses the new code without restarting the server.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import sys
import threading
import types
from pathlib import Path
from typing import Any

from agentcanvas.security.imports import WORKSPACE_IMPORT_PREFIXES, is_allowed_import_path

_lock = threading.Lock()
_loaded: dict[Path, tuple[int, types.ModuleType]] = {}


def _package(root: Path) -> str:
    name = "_acws_" + hashlib.sha1(str(root).encode()).hexdigest()[:10]
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(root)]  # type: ignore[attr-defined]
        sys.modules[name] = pkg
    return name


def load_module(root: Path, module: str) -> types.ModuleType:
    path = root.joinpath(*module.split(".")).with_suffix(".py")
    if not path.is_file():
        raise ModuleNotFoundError(f"workspace module '{module}' not found ({path.relative_to(root)})")
    mtime = path.stat().st_mtime_ns
    with _lock:
        cached = _loaded.get(path)
        if cached and cached[0] == mtime:
            return cached[1]
        name = f"{_package(root)}.{module}"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        _loaded[path] = (mtime, mod)
        return mod


def load_attr(import_path: str, workspace: Path | None = None) -> Any:
    """`package.module:attr` from the project workspace (scripts./tools./nodes./events.), else a normal import.

    Refuses anything outside the allowed prefixes (never "os:system", validator E080).
    """
    if not is_allowed_import_path(import_path):
        raise PermissionError(f"import path '{import_path}' is not allowed")
    module, _, attr = import_path.partition(":")
    if workspace is not None and module.startswith(WORKSPACE_IMPORT_PREFIXES):
        mod = load_module(workspace, module)
    else:
        mod = importlib.import_module(module)
    try:
        return getattr(mod, attr)
    except AttributeError as exc:
        raise AttributeError(f"'{module}' has no '{attr}'") from exc
