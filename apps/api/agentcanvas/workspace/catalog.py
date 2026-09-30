"""Workspace catalog (M5-T5, M5-T7, FR-X-22): tools, custom nodes, scripts and event handlers found by reading
the source (AST) — user code is not imported to list it."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentcanvas.registry import NodeManifest, PortSpec

TYPE_NAME = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


@dataclass
class Catalog:
    tools: list[dict[str, Any]] = field(default_factory=list)  # {name, import_path, description}
    nodes: list[NodeManifest] = field(default_factory=list)
    scripts: list[dict[str, Any]] = field(default_factory=list)  # {name, import_path}
    handlers: list[dict[str, Any]] = field(default_factory=list)  # {type, filter, dedup_key, import_path, targets}
    problems: list[dict[str, str]] = field(default_factory=list)  # {path, message}


def _decorators(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[tuple[str, ast.Call | None]]:
    out = []
    for d in fn.decorator_list:
        call = d if isinstance(d, ast.Call) else None
        target = call.func if call else d
        name = target.attr if isinstance(target, ast.Attribute) else target.id if isinstance(target, ast.Name) else ""
        out.append((name, call))
    return out


def _kwargs(call: ast.Call) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for kw in call.keywords:
        if kw.arg is None:
            continue
        try:
            out[kw.arg] = ast.literal_eval(kw.value)
        except ValueError:
            out[kw.arg] = ast.unparse(kw.value)  # e.g. config=Cfg -> "Cfg" (resolved later)
    return out


def _functions(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [n for n in tree.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]


def _config_schema(root: Path, module: str, fn_name: str, raw: Any, import_code: bool) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if raw is None:
        return {"type": "object", "properties": {}}
    if not import_code:
        return {"type": "object", "additionalProperties": True}
    from agentcanvas.workspace.loader import load_attr

    cfg: Any = getattr(load_attr(f"{module}:{fn_name}", root), "__canvas_node__", {}).get("config")
    if cfg is not None and hasattr(cfg, "model_json_schema"):
        return cfg.model_json_schema()
    return cfg if isinstance(cfg, dict) else {"type": "object"}


def _targets(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Workflows a handler starts: literal first arguments of `act.start_workflow("...")` (Event Map)."""
    out = []
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "start_workflow"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            out.append(str(node.args[0].value))
    return out


def scan(root: Path, *, import_code: bool = False) -> Catalog:
    """Read tools/, nodes/, scripts/ and events/. `import_code` (trusted/dev) allows importing a custom node's
    module to turn a Pydantic config model into a JSON Schema."""
    cat = Catalog()
    for sub in ("tools", "nodes", "scripts", "events"):
        base = root / sub
        if not base.is_dir():
            continue
        for path in sorted(base.glob("*.py")):
            if path.name.startswith("_"):
                continue
            rel, module = path.relative_to(root).as_posix(), f"{sub}.{path.stem}"
            try:
                tree = ast.parse(path.read_text(), filename=rel)
            except SyntaxError as exc:
                cat.problems.append({"path": rel, "message": f"syntax error line {exc.lineno}: {exc.msg}"})
                continue
            for fn in _functions(tree):
                decos = dict(_decorators(fn))
                doc = (ast.get_docstring(fn) or "").strip().split("\n")[0]
                if sub == "tools" and "tool" in decos:
                    call = decos["tool"]
                    named = (
                        call.args[0].value if call and call.args and isinstance(call.args[0], ast.Constant) else None
                    )
                    cat.tools.append(
                        {"name": named or fn.name, "import_path": f"{module}:{fn.name}", "description": doc}
                    )
                elif sub == "nodes" and "canvas_node" in decos and decos["canvas_node"] is not None:
                    meta = _kwargs(decos["canvas_node"])
                    try:
                        cat.nodes.append(_manifest(root, module, fn, meta, doc, import_code))
                    except Exception as exc:  # noqa: BLE001 - one bad node must not hide the others
                        cat.problems.append({"path": rel, "message": f"{fn.name}: {exc}"})
                elif sub == "scripts" and fn.name == "run":
                    cat.scripts.append({"name": path.stem, "import_path": f"{module}:run", "description": doc})
                elif sub == "events" and "on" in decos and decos["on"] is not None:
                    call = decos["on"]
                    etype = call.args[0].value if call.args and isinstance(call.args[0], ast.Constant) else None
                    if not isinstance(etype, str):
                        cat.problems.append({"path": rel, "message": f"{fn.name}: @on needs a literal event type"})
                        continue
                    kw = _kwargs(call)
                    cat.handlers.append({
                        "name": fn.name, "type": etype, "filter": kw.get("filter"), "dedup_key": kw.get("dedup_key"),
                        "import_path": f"{module}:{fn.name}", "targets": _targets(fn),
                    })  # fmt: skip
    _check_filters(root, cat)
    return cat


DATA_REF = re.compile(r"\bevent\.data\.([A-Za-z_][A-Za-z0-9_]*)")


def _check_filters(root: Path, cat: Catalog) -> None:
    """E081: a handler filter (or dedup key) reads a field its event's schema does not declare."""
    import json

    for h in cat.handlers:
        path = root / "events" / "schemas" / f"{h['type']}.json"
        if not path.is_file():
            continue
        try:
            props = set((json.loads(path.read_text()).get("properties") or {}).keys())
        except ValueError:
            cat.problems.append({"path": path.relative_to(root).as_posix(), "message": "schema is not valid JSON"})
            continue
        for expr in (h.get("filter"), h.get("dedup_key")):
            for name in DATA_REF.findall(expr or ""):
                if name not in props:
                    cat.problems.append({
                        "path": f"events/{h['import_path'].split(':')[0].split('.')[-1]}.py", "code": "E081",
                        "message": f"{h['name']}: event '{h['type']}' has no field '{name}' (schema {path.name})",
                    })  # fmt: skip


def _manifest(root: Path, module: str, fn: Any, meta: dict[str, Any], doc: str, import_code: bool) -> NodeManifest:
    name = str(meta.get("name", ""))
    if not TYPE_NAME.match(name):
        raise ValueError(f"custom node name '{name}' must look like 'acme.my_node'")
    return NodeManifest(
        type=name,
        kind="runtime",
        label=meta.get("label") or fn.name.replace("_", " ").capitalize(),
        category=meta.get("category") or "Workspace",
        icon=meta.get("icon") or "box",
        color=meta.get("color"),
        description=meta.get("description") or doc,
        outputs=[PortSpec(name="next", type="Flow")],
        config_schema=_config_schema(root, module, fn.name, meta.get("config"), import_code),
        reads=list(meta.get("reads") or []),
        writes=list(meta.get("writes") or []),
        side_effects=list(meta.get("side_effects") or []),
        impl=f"{module}:{fn.name}",
        provenance="workspace",
    )
