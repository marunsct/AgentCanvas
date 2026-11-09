"""Graph rules: reachability, router routes, loops, parallel write conflicts."""

from __future__ import annotations

from collections.abc import Iterator

from agentcanvas.ir.models import Edge
from agentcanvas.validation.engine import Diagnostic, ValidationContext, error, rule, warning


@rule
def reachability(ctx: ValidationContext) -> Iterator[Diagnostic]:
    succ = ctx.reach_successors
    seen: set[str] = set()
    stack = ["START"]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(succ.get(cur, ()))
    if not ctx.wf.nodes:
        return
    if "END" not in seen:
        yield error("E010", "no path from START to END", hint="connect the last step to END")
    for n in ctx.wf.nodes:
        if n.id not in seen:
            yield error("E011", f"'{n.name}' is unreachable from START", node_id=n.id)
    for n in ctx.wf.nodes:
        if n.id in seen and not succ.get(n.id) and n.id not in ctx.conditional_ids:
            yield error(
                "E015", f"'{n.name}' has no outgoing flow edge (connect it to a next step or END)", node_id=n.id
            )


@rule
def router_routes(ctx: ValidationContext) -> Iterator[Diagnostic]:
    for n in ctx.wf.nodes:
        if n.id not in ctx.router_ids:
            continue
        cases = n.config.get("cases", []) if isinstance(n.config.get("cases"), list) else []
        routes = {c.get("route") for c in cases if isinstance(c, dict)} | {n.config.get("default", "default")}
        wired = [e.source.route for e in ctx.wf.flow_edges() if e.source.node == n.id and e.source.port]
        for r in sorted(r for r in routes - set(wired) if isinstance(r, str) and r):  # '' is E004 already
            yield error("E012", f"router '{n.name}' route '{r}' has no outgoing edge", node_id=n.id)
        for r in sorted({w for w in wired if wired.count(w) > 1}):
            yield error("E012", f"router '{n.name}' route '{r}' has more than one outgoing edge", node_id=n.id)
        for r in sorted(set(wired) - routes):
            yield warning("W012", f"router '{n.name}' has an edge for undeclared route '{r}'", node_id=n.id)


@rule
def loops_have_exits(ctx: ValidationContext) -> Iterator[Diagnostic]:
    """W001: every cycle should pass through a router or a map (a guarded exit: a map leaves by its `empty` output),
    else it runs to the recursion limit."""
    succ = ctx.successors
    color: dict[str, int] = {}
    reported: set[frozenset[str]] = set()
    names = {n.id: n.name for n in ctx.wf.nodes}

    def dfs(u: str, path: list[str]) -> Iterator[Diagnostic]:
        color[u] = 1
        for v in sorted(succ.get(u, ())):
            if color.get(v) == 1 and v in path:
                loop = frozenset(path[path.index(v) :])
                if not ctx.conditional_ids.intersection(loop) and loop not in reported:
                    reported.add(loop)
                    members = ", ".join(sorted(names.get(x, x) for x in loop))
                    yield warning(
                        "W001",
                        f"loop without a router exit ({members}); it stops only at the recursion limit",
                        node_id=v,
                    )
            elif color.get(v) is None:
                yield from dfs(v, [*path, v])
        color[u] = 2

    yield from dfs("START", ["START"])


@rule
def parallel_write_conflicts(ctx: ValidationContext) -> Iterator[Diagnostic]:
    """E020: branches that run in the same super-step must not both write a `replace` channel."""
    replace = {c.name for c in ctx.wf.state.channels if c.reducer == "replace"}
    names = {n.id: n.name for n in ctx.wf.nodes}
    for src, targets in ctx.successors.items():
        if src in ctx.conditional_ids:
            continue  # a router takes exactly one route; a map's outputs are alternatives (items / empty)
        branch = sorted(t for t in targets if t in names)
        for i, a in enumerate(branch):
            for b in branch[i + 1 :]:
                both = (ctx.writes.get(a, set()) & ctx.writes.get(b, set())) & replace
                for ch in sorted(both):
                    yield error(
                        "E020",
                        f"'{names[a]}' and '{names[b]}' run in parallel and both write '{ch}' (reducer 'replace')",
                        node_id=b,
                        hint=f"give '{ch}' a reducer such as 'append', or write different channels",
                    )


@rule
def map_fan_out(ctx: ValidationContext) -> Iterator[Diagnostic]:
    """E015/E006 map outputs; E022: map workers run in parallel, so they may only write merge-safe channels."""
    replace = {c.name for c in ctx.wf.state.channels if c.reducer == "replace"}
    for n in ctx.wf.nodes:
        if n.id not in ctx.map_ids:
            continue
        out = [e for e in ctx.wf.flow_edges() if e.source.node == n.id]
        for port in ("each", "empty"):
            if len([e for e in out if e.source.port == port]) > 1:
                yield error("E006", f"map '{n.name}' has more than one '{port}' edge", node_id=n.id)
        workers = [e.target.node for e in out if e.source.port == "each"]
        if not workers:
            yield error(
                "E015", f"map '{n.name}' needs a flow edge from its 'each' output to the worker step", node_id=n.id
            )
        for w in workers:
            if w in ("START", "END") or ctx.wf.node(w) is None:
                yield error("E006", f"map '{n.name}' must send items to a step, not {w}", node_id=n.id)
                continue
            for ch in sorted(ctx.writes.get(w, set()) & replace):
                yield error(
                    "E022",
                    f"'{ctx.wf.require_node(w).name}' runs once per item of '{n.name}' in parallel and writes "
                    f"'{ch}' (reducer 'replace')",
                    node_id=w,
                    hint=f"give '{ch}' the reducer 'append' so every item's result is kept",
                )


@rule
def joins(ctx: ValidationContext) -> Iterator[Diagnostic]:
    """E016: a join waits for *every* input, so an input that may never arrive blocks it forever."""
    guards = _route_guards(ctx) if ctx.join_ids else {}
    for n in ctx.wf.nodes:
        if n.id not in ctx.join_ids:
            continue
        inputs = [e for e in ctx.wf.flow_edges() if e.target.node == n.id]
        for e in inputs:
            src = e.source.node
            if src == "START" or src in ctx.conditional_ids:
                what = "START" if src == "START" else f"'{ctx.wf.require_node(src).name}' (a conditional branch)"
                yield error(
                    "E016",
                    f"join '{n.name}' waits for {what}, which may never arrive",
                    node_id=n.id,
                    edge_id=e.id,
                    hint="connect conditional branches straight to the next step (joins wait for every branch)",
                )
        # inputs further down different paths of one router (or map): only one of them ever arrives
        later = [e for e in inputs if e.source.node != "START" and e.source.node not in ctx.conditional_ids]
        reported: set[str] = set()
        for i, a in enumerate(later):
            for b in later[i + 1 :]:
                clash = _exclusive(guards.get(a.source.node), guards.get(b.source.node))
                if clash is None or b.id in reported:
                    continue
                reported.add(b.id)
                cond, pa, pb = clash
                yield error(
                    "E016",
                    f"join '{n.name}' waits for '{ctx.wf.require_node(a.source.node).name}' and "
                    f"'{ctx.wf.require_node(b.source.node).name}', which follow different paths of "
                    f"'{ctx.wf.require_node(cond).name}' ('{pa}', '{pb}'): only one of them ever arrives",
                    node_id=n.id,
                    edge_id=b.id,
                    hint="connect the paths straight to the next step (joins wait for every branch)",
                )
        if len({e.source.node for e in inputs}) < 2:
            yield warning("W013", f"join '{n.name}' has fewer than two incoming branches", node_id=n.id)


def _route_guards(ctx: ValidationContext) -> dict[str, frozenset[tuple[str, str]]]:
    """For each reachable node, the router paths and map outputs (node id, port) that every way from START to it
    goes through (a dominator analysis over the flow edges)."""
    into: dict[str, list[Edge]] = {}
    for e in ctx.wf.flow_edges():
        into.setdefault(e.target.node, []).append(e)
    guards: dict[str, frozenset[tuple[str, str]]] = {"START": frozenset()}

    def via(e: Edge) -> frozenset[tuple[str, str]] | None:
        g = guards.get(e.source.node)
        if g is not None and e.source.node in ctx.conditional_ids:
            g = g | {(e.source.node, e.source.route)}
        return g

    changed = True
    while changed:  # values only shrink once set, so this ends
        changed = False
        for node, edges in into.items():
            if node == "START" or not (ins := [g for e in edges if (g := via(e)) is not None]):
                continue
            new = frozenset.intersection(*ins)
            if guards.get(node) != new:
                guards[node], changed = new, True
    return guards


def _exclusive(
    a: frozenset[tuple[str, str]] | None, b: frozenset[tuple[str, str]] | None
) -> tuple[str, str, str] | None:
    """(router or map, path of a, path of b) when a and b lie on different paths of one of them."""
    for cond, pa in sorted(a or ()):
        for other, pb in sorted(b or ()):
            if other == cond and pa != pb:
                return cond, pa, pb
    return None


@rule
def subgraphs(ctx: ValidationContext) -> Iterator[Diagnostic]:
    """E018: the embedded workflow must be valid on its own; its inputs/outputs must exist inside (E021)."""
    from pydantic import ValidationError

    from agentcanvas.validation.engine import validate

    outer_resources = {r.name: r.config for r in ctx.wf.resources}
    for n in ctx.wf.nodes:
        if n.type != "core.subgraph":
            continue
        try:
            inner = ctx.wf.subworkflow(n)
        except ValidationError as exc:
            yield error("E018", f"subgraph '{n.name}' is not a valid workflow: {exc.errors()[0]['msg']}", node_id=n.id)
            continue
        for d in validate(inner, ctx.registry, edition=ctx.edition, allow_local_mcp=ctx.allow_local_mcp):
            if d.severity == "error":
                yield error("E018", f"subgraph '{n.name}': {d.code} {d.message}", node_id=n.id, path="config.workflow")
            elif d.severity == "warning":
                yield warning("W018", f"subgraph '{n.name}': {d.code} {d.message}", node_id=n.id)
        inside = {c.name for c in inner.state.channels}
        for key in ("inputs", "outputs"):
            for ch in n.config.get(key) or []:
                if ch not in inside:
                    yield error(
                        "E021",
                        f"subgraph '{n.name}' passes '{ch}' but has no channel of that name inside",
                        node_id=n.id,
                        path=f"config.{key}",
                    )
        for r in inner.resources:  # resources resolve by name: one name must mean one configuration
            if r.name in outer_resources and outer_resources[r.name] != r.config:
                yield error(
                    "E019",
                    f"subgraph '{n.name}' has resource '{r.name}' configured differently from the workflow's",
                    node_id=n.id,
                    hint="rename one of them",
                )
