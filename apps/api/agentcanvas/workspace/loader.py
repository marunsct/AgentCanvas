"""Load workspace code by file, per project, with hot reload (M5-T3).

Two projects may both have `scripts/score.py`: each workspace gets its own synthetic package
(`_acws_<hash>`), so modules never collide in `sys.modules`. A file is re-executed when it changes,
so editing a script and running again uses the new code without restarting the server.

Workspace files import each other the way they will in an export (S17): `from scripts.textutil import shout` or
`from .helpers import x` inside workspace code resolves to that project's files, through an `__import__` given to
each workspace module (never a process-wide hook, so projects stay apart).
"""

from __future__ import annotations

import builtins
import hashlib
import importlib
import importlib.util
import sys
import threading
import types
from pathlib import Path
from typing import Any

from agentcanvas.security.imports import WORKSPACE_IMPORT_PREFIXES, is_allowed_import_path

_lock = threading.RLock()  # re-entrant: executing a module imports its workspace neighbours
ROOTS = tuple(p.rstrip(".") for p in WORKSPACE_IMPORT_PREFIXES)
_loaded: dict[Path, tuple[int, types.ModuleType]] = {}


def _package(root: Path) -> str:
    name = "_acws_" + hashlib.sha1(str(root).encode()).hexdigest()[:10]
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(root)]  # type: ignore[attr-defined]
        sys.modules[name] = pkg
    return name


def _namespace(root: Path, module: str) -> types.ModuleType:
    """A folder without `__init__.py` (e.g. `flows/<wf>/`), as a namespace package of this workspace."""
    name = f"{_package(root)}.{module}"
    mod = sys.modules.get(name)
    if mod is None:
        mod = types.ModuleType(name)
        mod.__path__ = [str(root.joinpath(*module.split(".")))]  # type: ignore[attr-defined]
        mod.__package__ = name
        sys.modules[name] = mod
    return mod


def _workspace_module(root: Path, module: str) -> types.ModuleType:
    """`module` (dotted, workspace-relative) and its parent packages, each set as an attribute of its parent."""
    parts = module.split(".")
    mod: types.ModuleType | None = None
    for i in range(1, len(parts) + 1):
        sub = ".".join(parts[:i])
        base = root.joinpath(*parts[:i])
        if base.with_suffix(".py").is_file() or (base / "__init__.py").is_file():
            child = load_module(root, sub)
        elif base.is_dir():
            child = _namespace(root, sub)
        else:
            raise ModuleNotFoundError(f"No module named '{sub}' in the workspace", name=sub)
        if mod is not None:
            setattr(mod, parts[i - 1], child)
        mod = child
    assert mod is not None
    return mod


def _in_workspace(root: Path, top: str) -> bool:
    return (root / top).is_dir() or (root / f"{top}.py").is_file()


def _importer(root: Path) -> dict[str, Any]:
    """`__builtins__` for workspace code: imports of workspace packages resolve to this workspace's files."""
    real = builtins.__import__
    prefix = f"{_package(root)}."

    def ws_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
        if level:  # relative: from the importing module's own place in the workspace
            here = str((globals or {}).get("__name__", ""))
            if not here.startswith(prefix):
                return real(name, globals, locals, fromlist, level)
            pkg = here[len(prefix) :].split(".")
            if not (globals or {}).get("__path__"):  # a module, not a package: its package is the parent
                pkg = pkg[:-1]
            if level - 1 > len(pkg):
                raise ImportError("attempted relative import beyond the workspace")
            base = pkg[: len(pkg) - (level - 1)]
            name = ".".join([*base, name] if name else base)
        elif name.split(".")[0] not in ROOTS or not _in_workspace(root, name.split(".")[0]):
            return real(name, globals, locals, fromlist, level)  # an installed package of that name, if any
        leaf = _workspace_module(root, name)
        if not fromlist:
            return _workspace_module(root, name.split(".")[0]) if not level else leaf
        for item in fromlist:  # `from scripts import textutil`: a submodule, loaded on demand
            if item != "*" and not hasattr(leaf, item):
                sub = f"{name}.{item}"
                base = root.joinpath(*sub.split("."))
                if base.with_suffix(".py").is_file() or base.is_dir():
                    setattr(leaf, item, _workspace_module(root, sub))
        return leaf

    return {**builtins.__dict__, "__import__": ws_import}


def load_module(root: Path, module: str) -> types.ModuleType:
    base = root.joinpath(*module.split("."))
    path = base / "__init__.py" if not base.with_suffix(".py").is_file() and base.is_dir() else base.with_suffix(".py")
    if not path.is_file():
        raise ModuleNotFoundError(f"workspace module '{module}' not found ({path.relative_to(root)})")
    mtime = path.stat().st_mtime_ns
    with _lock:
        cached = _loaded.get(path)
        if cached and cached[0] == mtime:
            return cached[1]
        name = f"{_package(root)}.{module}"
        is_pkg = path.name == "__init__.py"
        spec = importlib.util.spec_from_file_location(
            name, path, submodule_search_locations=[str(path.parent)] if is_pkg else None
        )
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        mod.__dict__["__builtins__"] = _importer(root)
        sys.modules[name] = mod
        try:
            spec.loader.exec_module(mod)
        except BaseException:
            sys.modules.pop(name, None)
            raise
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
