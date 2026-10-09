"""Which workspace files a workflow's export needs (S17): exactly the code it uses, found by following imports.

Entry points are the workspace import paths (`scripts.x:run`, `flows.<wf>.tools.y:fn`, ...) named anywhere in the
workflow or its generated code. From each, `import` and `from ... import` statements are followed (absolute and
relative) to every module that is a file in the workspace, with the packages' `__init__.py` files. The workflow's own
folder also brings its data files (prompts, JSON, CSV ...), and `requirements.txt` at the workspace root or in the
workflow's folder adds Python packages to the export. Pure functions over files: user code is read, never imported.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from agentcanvas.workspace.store import HIDDEN, workflow_dir

ROOTS = ("scripts", "tools", "nodes", "events", "flows")
IMPORT_PATH = re.compile(
    r"\b((?:scripts|tools|nodes|events|flows)(?:\.[A-Za-z_][A-Za-z0-9_]*)+):[A-Za-z_][A-Za-z0-9_]*"
)
REQUIREMENT = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._\-]*(\[[A-Za-z0-9_,\-]+\])?\s*([<>=!~]=?[^;#\s]+(,\s*[<>=!~]=?[^;#\s]+)*)?$"
)


@dataclass
class Bundle:
    files: list[str] = field(default_factory=list)  # paths relative to the workspace root, sorted
    entry_points: list[str] = field(default_factory=list)  # the import paths the workflow names
    missing: list[str] = field(default_factory=list)  # entry points with no file in the workspace
    requirements: list[str] = field(default_factory=list)  # from requirements.txt files, one per line


def entry_points(*texts: str) -> list[str]:
    """Workspace import paths named in the given texts (the workflow's JSON, its generated code)."""
    return sorted({m.group(0) for t in texts for m in IMPORT_PATH.finditer(t)})


def _module_file(root: Path, module: str) -> Path | None:
    parts = module.split(".")
    if not parts or parts[0] not in ROOTS or any(p in HIDDEN for p in parts):
        return None
    base = root.joinpath(*parts)
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file() and candidate.resolve().is_relative_to(root):
            return candidate
    return None


def _imports(path: Path, module: str) -> set[str]:
    """Modules a file imports (absolute names; for `from a import b`, both `a` and `a.b`)."""
    try:
        tree = ast.parse(path.read_text(errors="replace"))
    except SyntaxError:
        return set()
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base_parts = package.split(".") if package else []
                if node.level - 1 > len(base_parts):
                    continue
                base = ".".join(base_parts[: len(base_parts) - (node.level - 1)])
                name = f"{base}.{node.module}" if node.module else base
            else:
                name = node.module or ""
            if not name:
                continue
            out.add(name)
            out |= {f"{name}.{a.name}" for a in node.names if a.name != "*"}
    return out


def _requirements(path: Path) -> list[str]:
    if not path.is_file():
        return []
    out = []
    for raw in path.read_text(errors="replace").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line and REQUIREMENT.match(line):  # options (-r, --index-url) and URLs are not carried over
            out.append(line)
    return out


def collect(root: Path, wf_id: str, *texts: str) -> Bundle:
    """The workspace files that `wf_id` needs, given texts naming its import paths (see the module docstring)."""
    root = root.resolve()
    bundle = Bundle(entry_points=entry_points(*texts))
    files: set[Path] = set()
    todo: list[str] = []
    for ep in bundle.entry_points:
        module = ep.partition(":")[0]
        if _module_file(root, module) is None:
            bundle.missing.append(ep)
        todo.append(module)
    seen: set[str] = set()
    while todo:
        module = todo.pop()
        if module in seen:
            continue
        seen.add(module)
        path = _module_file(root, module)
        if path is None:
            continue
        files.add(path)
        parts = module.split(".")
        for i in range(1, len(parts)):  # the packages it lives in
            init = root.joinpath(*parts[:i], "__init__.py")
            if init.is_file():
                files.add(init)
        todo.extend(_imports(path, module) - seen)
    own = root / workflow_dir(wf_id)
    if own.is_dir():  # the workflow's data files (its code was followed above)
        for p in own.rglob("*"):
            rel = p.relative_to(root)
            if p.is_file() and p.suffix != ".py" and not any(part in HIDDEN for part in rel.parts):
                if p.resolve().is_relative_to(root):
                    files.add(p)
    bundle.files = sorted(p.relative_to(root).as_posix() for p in files)
    seen_req: set[str] = set()
    for req in (*_requirements(root / "requirements.txt"), *_requirements(own / "requirements.txt")):
        if req not in seen_req:
            seen_req.add(req)
            bundle.requirements.append(req)
    return bundle
