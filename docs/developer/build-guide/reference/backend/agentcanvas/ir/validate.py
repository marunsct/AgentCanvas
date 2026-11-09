"""Validator: turns a Workflow into a list of Diagnostics (errors block runs)."""

from __future__ import annotations

from collections import defaultdict
from typing import Literal

from pydantic import BaseModel

from agentcanvas.expr import ExpressionError, check_condition
from agentcanvas.ir.models import Workflow

Severity = Literal["error", "warning", "info"]


class Diagnostic(BaseModel):
    code: str
    severity: Severity
    message: str
    node_id: str | None = None


REQUIRED_MODEL = {"core.llm", "core.agent"}
ALLOWED_IMPORT_PREFIXES = ("scripts.", "tools.", "examples.")  # keep in sync with runtime.nodes


def validate(wf: Workflow) -> list[Diagnostic]:
    d: list[Diagnostic] = []
    ids = [n.id for n in wf.nodes] + [r.id for r in wf.resources]
    names = [n.name for n in wf.nodes]

    for dup in {i for i in ids if ids.count(i) > 1}:
        d.append(Diagnostic(code="E000", severity="error", message=f"duplicate id '{dup}'"))
    for dup in {n for n in names if names.count(n) > 1}:
        d.append(Diagnostic(code="E000", severity="error", message=f"duplicate node name '{dup}'"))

    known = set(ids) | {"START", "END"}
    for e in wf.edges:
        for end in (e.source.node, e.target.node):
            if end not in known:
                d.append(Diagnostic(code="E003", severity="error", message=f"edge {e.id} references unknown '{end}'"))

    # E080: user code may only come from workspace packages
    import_paths = [(n.id, n.config.get("import_path")) for n in wf.nodes]
    import_paths += [(r.id, r.config.get("import_path")) for r in wf.resources]
    for owner, path in import_paths:
        if path and not (":" in path and path.startswith(ALLOWED_IMPORT_PREFIXES)):
            d.append(
                Diagnostic(
                    code="E080",
                    severity="error",
                    node_id=owner,
                    message=f"import path '{path}' must be 'scripts.|tools.<module>:<name>'",
                )
            )

    # E001: required ports
    for n in wf.nodes:
        if n.type in REQUIRED_MODEL and not wf.wired_into(n.id, "model"):
            d.append(
                Diagnostic(
                    code="E001",
                    severity="error",
                    node_id=n.id,
                    message=f"'{n.name}' needs a Chat Model wired into its 'model' port",
                )
            )
        for key in ("output", "channel"):
            ch = n.config.get(key)
            if ch and not wf.state.channel(ch):
                d.append(
                    Diagnostic(
                        code="E021",
                        severity="error",
                        node_id=n.id,
                        message=f"'{n.name}' writes unknown state channel '{ch}'",
                    )
                )
        if n.type == "core.router":
            for case in n.config.get("cases", []):
                try:
                    check_condition(case["when"])
                except ExpressionError as exc:
                    d.append(Diagnostic(code="E013", severity="error", node_id=n.id, message=str(exc)))
            routes = {c["route"] for c in n.config.get("cases", [])} | {n.config.get("default", "default")}
            wired = {e.source.route for e in wf.flow_edges() if e.source.node == n.id and e.source.port}
            for r in sorted(routes - wired):
                d.append(
                    Diagnostic(
                        code="E012",
                        severity="error",
                        node_id=n.id,
                        message=f"router '{n.name}' route '{r}' has no outgoing edge",
                    )
                )

    # E010/E011: reachability over flow edges
    succ: dict[str, set[str]] = defaultdict(set)
    for e in wf.flow_edges():
        succ[e.source.node].add(e.target.node)
    seen, stack = set(), ["START"]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(succ[cur])
    if "END" not in seen:
        d.append(Diagnostic(code="E010", severity="error", message="no path from START to END"))
    for n in wf.nodes:
        if n.id not in seen:
            d.append(Diagnostic(code="E011", severity="error", node_id=n.id, message=f"'{n.name}' is unreachable"))

    # W001: cycles without a router are unbounded loops
    if _has_cycle_without_router(wf, succ):
        d.append(
            Diagnostic(
                code="W001",
                severity="warning",
                message="a loop has no router exit; it will stop only at the recursion limit",
            )
        )
    return d


def _has_cycle_without_router(wf: Workflow, succ: dict[str, set[str]]) -> bool:
    routers = {n.id for n in wf.nodes if n.type == "core.router"}
    color: dict[str, int] = {}

    def dfs(u: str, path: list[str]) -> bool:
        color[u] = 1
        for v in succ[u]:
            if color.get(v) == 1:
                loop = path[path.index(v) :] if v in path else path
                if not routers.intersection(loop):
                    return True
            elif color.get(v) is None and dfs(v, [*path, v]):
                return True
        color[u] = 2
        return False

    return dfs("START", ["START"])


def has_errors(diags: list[Diagnostic]) -> bool:
    return any(x.severity == "error" for x in diags)
