"""Policy-as-code lint profiles (FR-X-19): organisation rules as CEL predicates over the IR.

A policy picks the elements it is about (`scope` node / resource / workflow, narrowed by the `match`
predicate) and states what must hold for each (`require`). Examples:

- every external write needs a human first:  scope=node,
  require=`node.effects.all(e, e.effect != "external_write" || e.gated)`
- only approved models in prod:  scope=resource, match=`resource.type == "model.chat"`,
  require=`resource.config.model in ["anthropic:claude-sonnet-5-5"]`, environments=["prod"]

Policies without `environments` apply everywhere: the editor reports them on every edit and publishing
enforces them. Policies for named environments are enforced when publishing there; the editor shows them as
warnings so builders find out before they publish. A predicate that cannot be evaluated counts as a
violation (fail closed).

CEL names: `node`, `resource` (per scope), `workflow` and `env` (the publish environment, "" in the editor).
"""

from __future__ import annotations

from typing import Any, Literal

from celpy.celtypes import BoolType
from pydantic import BaseModel, Field

from agentcanvas.expr import ExpressionError, check_condition, eval_expression
from agentcanvas.ir.models import Node, Resource, Workflow
from agentcanvas.registry import Registry, default_registry
from agentcanvas.validation.blast import RISKY, blast_radius
from agentcanvas.validation.engine import Diagnostic

Scope = Literal["node", "resource", "workflow"]

VIOLATED, VIOLATED_WARNING, UNEVALUABLE = "E095", "W095", "E096"
CODES = {VIOLATED, VIOLATED_WARNING, UNEVALUABLE}


class Policy(BaseModel):
    id: str = ""
    name: str
    description: str = ""
    scope: Scope = "node"
    match: str = "true"
    require: str
    severity: Literal["error", "warning"] = "error"
    environments: list[str] = Field(default_factory=list)
    enabled: bool = True


def check_policy(match: str, require: str) -> None:
    """Raise ExpressionError when either predicate does not compile (checked when a policy is saved)."""
    check_condition(match or "true")
    check_condition(require)


def _models(wf: Workflow) -> dict[str, str]:
    return {r.id: str(r.config.get("model", "")) for r in wf.resources if r.type.startswith("model.")}


def _context(wf: Workflow, registry: Registry) -> tuple[dict[str, dict], dict[str, dict], dict[str, Any]]:
    """The CEL view of every node and resource, and of the workflow as a whole."""
    blast = {nb.node_id: nb for nb in blast_radius(wf, registry).nodes}
    models = _models(wf)
    resources = {r.id: r for r in wf.resources}
    wired: dict[str, list[Resource]] = {}
    used_by: dict[str, list[str]] = {}
    for e in wf.edges:
        if e.kind == "wiring" and e.source.node in resources:
            wired.setdefault(e.target.node, []).append(resources[e.source.node])
            used_by.setdefault(e.source.node, []).append(e.target.node)

    def node_view(n: Node) -> dict[str, Any]:
        m = registry.get(n.type)
        caps = blast[n.id].capabilities if n.id in blast else []
        return {
            "id": n.id, "type": n.type, "name": n.name, "disabled": n.disabled, "config": n.config,
            "category": m.category if m else "",
            "policies": n.policies.model_dump(mode="json", exclude_none=True) if n.policies else {},
            "effects": [c.model_dump() for c in caps],
            "ungated": sorted({c.effect for c in caps if c.effect in RISKY and not c.gated}),
            "models": [models[r.id] for r in wired.get(n.id, []) if r.id in models],
            "tools": [r.name for r in wired.get(n.id, []) if not r.type.startswith(("model.", "mw."))],
        }  # fmt: skip

    def resource_view(r: Resource) -> dict[str, Any]:
        m = registry.get(r.type)
        return {
            "id": r.id, "type": r.type, "name": r.name, "config": r.config,
            "category": m.category if m else "", "used_by": used_by.get(r.id, []),
        }  # fmt: skip

    nodes = {n.id: node_view(n) for n in wf.nodes}
    res = {r.id: resource_view(r) for r in wf.resources}
    workflow = {
        "id": wf.id, "name": wf.name, "kind": wf.kind, "settings": wf.settings.model_dump(mode="json"),
        "nodes": [v for n, v in nodes.items() if not v["disabled"]], "resources": list(res.values()),
        "node_types": sorted({n.type for n in wf.nodes if not n.disabled}),
        "resource_types": sorted({r.type for r in wf.resources}),
        "models": sorted({m for m in models.values() if m}),
    }  # fmt: skip
    return nodes, res, workflow


def _holds(source: str, names: dict[str, Any]) -> bool:
    result = eval_expression(source, names)
    if not isinstance(result, (bool, BoolType)):
        raise ExpressionError(f"'{source}' must be true or false, got {result!r}")
    return bool(result)


def check_policies(
    wf: Workflow, policies: list[Policy], registry: Registry | None = None, *, environment: str | None = None
) -> list[Diagnostic]:
    """Diagnostics for every element that breaks a policy.

    `environment=None` is the editor: environment-specific policies are reported as warnings. With an
    environment (publishing), the policies for that environment and the general ones are enforced as written.
    """
    active = [p for p in policies if p.enabled and (not p.environments or environment is None
                                                    or environment in p.environments)]  # fmt: skip
    if not active:
        return []
    nodes, resources, workflow = _context(wf, registry or default_registry())
    env = environment or ""
    out: list[Diagnostic] = []
    for p in active:
        early = environment is None and bool(p.environments)  # shown in the editor, enforced at publish
        severity = "warning" if early else p.severity
        when = f" (enforced when publishing to {', '.join(p.environments)})" if early else ""
        if p.scope == "node":
            targets = [(v["id"], v["name"], {"node": v}) for v in nodes.values() if not v["disabled"]]
        elif p.scope == "resource":
            targets = [(v["id"], v["name"], {"resource": v}) for v in resources.values()]
        else:
            targets = [(None, wf.name, {})]
        for element_id, name, own in targets:
            names = {**own, "workflow": workflow, "env": env}
            try:
                if not _holds(p.match or "true", names) or _holds(p.require, names):
                    continue
                code = VIOLATED if severity == "error" else VIOLATED_WARNING
                message = f"'{name}' breaks policy '{p.name}'" + (f": {p.description}" if p.description else "")
            except ExpressionError as exc:
                code, message = UNEVALUABLE, f"policy '{p.name}' cannot be checked on '{name}': {exc}"
            out.append(
                Diagnostic(
                    code=code, severity=severity, message=message + when, node_id=element_id,
                    hint=f"policy requires: {p.require}",
                )
            )  # fmt: skip
    return out
