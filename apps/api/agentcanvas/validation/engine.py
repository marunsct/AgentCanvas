"""Validation engine: runs every registered rule over a workflow (spec doc 03 §4, ticket M1-T8).

A rule is a function ``(ctx: ValidationContext) -> Iterable[Diagnostic]`` registered with
``@rule``. Rules must be pure and fast: the whole suite runs on every edit (target: 200 nodes
in < 150 ms). Shared analysis (manifests, successors, reads/writes) is computed once in the
context and cached.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from functools import cached_property
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from agentcanvas.ir.models import Node, Resource, Workflow
from agentcanvas.registry import NodeManifest, Registry, default_registry
from agentcanvas.validation import analysis

if TYPE_CHECKING:
    from agentcanvas.validation.policies import Policy

Severity = Literal["error", "warning", "info"]
Edition = Literal["dev", "hosted"]


class Diagnostic(BaseModel):
    code: str
    severity: Severity
    message: str
    node_id: str | None = None  # node or resource id
    edge_id: str | None = None
    path: str | None = None  # config path, e.g. "config.cases[0].when"
    hint: str | None = None


@dataclass
class ValidationContext:
    wf: Workflow
    registry: Registry
    edition: Edition = "dev"
    allow_local_mcp: bool = False
    isolated: bool = False  # workspace code stays out of the server process (`Settings.isolates_workspace_code`)
    _cache: dict = field(default_factory=dict)

    def manifest(self, element: Node | Resource) -> NodeManifest | None:
        return self.registry.get(element.type)

    @cached_property
    def element_ids(self) -> set[str]:
        return {n.id for n in self.wf.nodes} | {r.id for r in self.wf.resources}

    @cached_property
    def successors(self) -> dict[str, set[str]]:
        succ: dict[str, set[str]] = defaultdict(set)
        for e in self.wf.flow_edges():
            succ[e.source.node].add(e.target.node)
        return succ

    @cached_property
    def reach_successors(self) -> dict[str, set[str]]:
        """Flow successors plus error routes (a node's error handler can jump to its `error_route`)."""
        succ: dict[str, set[str]] = defaultdict(set)
        for k, v in self.successors.items():
            succ[k] |= v
        for n in self.wf.nodes:
            if n.policies and n.policies.error_route:
                succ[n.id].add(n.policies.error_route)
        return succ

    @cached_property
    def router_ids(self) -> set[str]:
        return {n.id for n in self.wf.nodes if (m := self.manifest(n)) and m.dynamic_outputs == "routes"}

    @cached_property
    def map_ids(self) -> set[str]:
        return {n.id for n in self.wf.nodes if n.type == "core.map"}

    @cached_property
    def join_ids(self) -> set[str]:
        return {n.id for n in self.wf.nodes if n.type == "core.join"}

    @cached_property
    def conditional_ids(self) -> set[str]:
        """Nodes whose outgoing edges are alternatives (routers) or per-item fan-out (maps), not parallel."""
        return self.router_ids | self.map_ids

    @cached_property
    def writes(self) -> dict[str, set[str]]:
        return {n.id: analysis.node_writes(n, m) for n in self.wf.nodes if (m := self.manifest(n))}

    @cached_property
    def reads(self) -> dict[str, set[str]]:
        return {n.id: analysis.node_reads(n, m) for n in self.wf.nodes if (m := self.manifest(n))}


RuleFn = Callable[[ValidationContext], Iterable[Diagnostic]]
RULES: list[RuleFn] = []


def rule(fn: RuleFn) -> RuleFn:
    RULES.append(fn)
    return fn


_ORDER = {"error": 0, "warning": 1, "info": 2}


def validate(
    wf: Workflow,
    registry: Registry | None = None,
    *,
    edition: Edition = "dev",
    allow_local_mcp: bool = False,
    isolated: bool = False,
    policies: list[Policy] | None = None,
    environment: str | None = None,
) -> list[Diagnostic]:
    """Every rule's diagnostics, plus the organisation's `policies` (FR-X-19) as they apply to `environment`
    (None: the editor; see `check_policies`)."""
    from agentcanvas.validation import rules  # noqa: F401  (registers the rules)
    from agentcanvas.validation.policies import check_policies

    ctx = ValidationContext(wf, registry or default_registry(), edition, allow_local_mcp, isolated)
    out: list[Diagnostic] = []
    for fn in RULES:
        try:
            out.extend(fn(ctx))
        except Exception as exc:  # noqa: BLE001 - one broken check must not turn every edit into a 500
            out.append(error("E999", f"the check '{fn.__name__}' failed: {type(exc).__name__}: {exc}"))
    if policies:
        out.extend(check_policies(wf, policies, ctx.registry, environment=environment))
    # de-duplicate and sort deterministically: errors first, then by code/element
    unique = {(d.code, d.node_id, d.edge_id, d.path, d.message): d for d in out}
    return sorted(
        unique.values(), key=lambda d: (_ORDER[d.severity], d.code, d.node_id or "", d.edge_id or "", d.path or "")
    )


def has_errors(diags: list[Diagnostic]) -> bool:
    return any(x.severity == "error" for x in diags)


def error(code: str, message: str, **kw) -> Diagnostic:
    return Diagnostic(code=code, severity="error", message=message, **kw)


def warning(code: str, message: str, **kw) -> Diagnostic:
    return Diagnostic(code=code, severity="warning", message=message, **kw)


def info(code: str, message: str, **kw) -> Diagnostic:
    return Diagnostic(code=code, severity="info", message=message, **kw)
