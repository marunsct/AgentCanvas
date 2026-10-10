"""Structural rules: identities, references, known types, edge kinds."""

from __future__ import annotations

import keyword
from collections import Counter
from collections.abc import Iterator

from agentcanvas.validation.engine import Diagnostic, ValidationContext, error, info, rule

SPECIAL = {"START", "END"}
# names the exported graph code itself uses for variables (adapters/langchain/templates/graph.py.j2): a
# resource of that name would replace them
GENERATED = {"ac", "interop", "builder", "resources", "checkpointer", "store", "cache", "annotations"}


@rule
def unique_ids_and_names(ctx: ValidationContext) -> Iterator[Diagnostic]:
    wf = ctx.wf
    ids = Counter([n.id for n in wf.nodes] + [r.id for r in wf.resources] + [e.id for e in wf.edges])
    for dup, n in sorted(ids.items()):
        if n > 1 or dup in SPECIAL:
            yield error("E000", f"id '{dup}' is used {n} times or is reserved", node_id=dup)
    names = Counter([n.name for n in wf.nodes] + [r.name for r in wf.resources])
    for dup, n in sorted(names.items()):
        if n > 1:
            yield error("E000", f"name '{dup}' is used by {n} elements; names become code identifiers")
    for r in wf.resources:  # resources become Python variables in the exported code
        if keyword.iskeyword(r.name) or r.name in GENERATED:
            yield error("E007", f"'{r.name}' is a name Python code reserves; rename this resource", node_id=r.id)
    channels = Counter(c.name for c in wf.state.channels)
    for dup, n in sorted(channels.items()):
        if n > 1:
            yield error("E000", f"state channel '{dup}' is declared {n} times")


@rule
def known_types(ctx: ValidationContext) -> Iterator[Diagnostic]:
    for n in ctx.wf.nodes:
        m = ctx.manifest(n)
        if m is None:
            yield error("E005", f"unknown node type '{n.type}'", node_id=n.id)
        elif m.kind == "resource":
            yield error("E005", f"'{n.type}' is a resource type; add it to `resources`, not `nodes`", node_id=n.id)
    for r in ctx.wf.resources:
        m = ctx.manifest(r)
        if m is None:
            yield error("E005", f"unknown resource type '{r.type}'", node_id=r.id)
        elif m.kind == "runtime":
            yield error("E005", f"'{r.type}' is a runtime node type; add it to `nodes`", node_id=r.id)


@rule
def edge_references(ctx: ValidationContext) -> Iterator[Diagnostic]:
    wf = ctx.wf
    node_ids = {n.id for n in wf.nodes}
    res_ids = {r.id for r in wf.resources}
    for e in wf.edges:
        for end in (e.source.node, e.target.node):
            if end not in ctx.element_ids | SPECIAL:
                yield error("E003", f"edge '{e.id}' references unknown element '{end}'", edge_id=e.id)
        src, dst = e.source.node, e.target.node
        if e.kind == "flow":
            if src in res_ids or dst in res_ids:
                yield error(
                    "E006", f"flow edge '{e.id}' touches a resource; resources are wired, not sequenced", edge_id=e.id
                )
            if src == "END" or dst == "START":
                yield error("E006", f"flow edge '{e.id}' leaves END or enters START", edge_id=e.id)
            if src in node_ids and e.source.port and e.source.port.startswith("route:") and src not in ctx.router_ids:
                yield error("E006", f"edge '{e.id}' uses a route port on a node that is not a router", edge_id=e.id)
            port = e.source.port
            if src in node_ids and src not in ctx.router_ids and port and not port.startswith("route:"):
                m = ctx.manifest(wf.require_node(src))
                if m is not None and not any(o.name == port and o.type == "Flow" for o in m.outputs):
                    yield error("E006", f"edge '{e.id}' leaves '{src}' through unknown output '{port}'", edge_id=e.id)
            if src in ctx.router_ids and not (e.source.port or "").startswith("route:"):
                yield error(
                    "E006",
                    f"edge '{e.id}' leaves router without a 'route:<name>' port",
                    edge_id=e.id,
                    hint="connect from one of the router's route handles",
                )
        elif e.kind == "wiring":
            if src not in res_ids:
                yield error("E006", f"wiring edge '{e.id}' must start at a resource (model, tool, …)", edge_id=e.id)
            if dst in SPECIAL:
                yield error("E006", f"wiring edge '{e.id}' cannot target {dst}", edge_id=e.id)
    for n in wf.nodes:
        route = n.policies.error_route if n.policies else None
        if route and route not in node_ids:
            yield error(
                "E003", f"'{n.name}' error route '{route}' is not a node", node_id=n.id, path="policies.error_route"
            )


@rule
def upstream_stability(ctx: ValidationContext) -> Iterator[Diagnostic]:
    for el in [*ctx.wf.nodes, *ctx.wf.resources]:
        m = ctx.manifest(el)
        if m and m.stability != "stable":
            yield info("I010", f"'{el.name}' uses a {m.stability} upstream API ({m.type})", node_id=el.id)
