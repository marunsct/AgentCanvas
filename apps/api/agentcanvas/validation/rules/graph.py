"""Graph rules: reachability, router routes, loops, parallel write conflicts."""

from __future__ import annotations

from collections.abc import Iterator

from agentcanvas.validation.engine import Diagnostic, ValidationContext, error, rule, warning


@rule
def reachability(ctx: ValidationContext) -> Iterator[Diagnostic]:
    succ = ctx.successors
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
        if n.id in seen and not succ.get(n.id) and n.id not in ctx.router_ids:
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
        for r in sorted(r for r in routes - set(wired) if isinstance(r, str)):
            yield error("E012", f"router '{n.name}' route '{r}' has no outgoing edge", node_id=n.id)
        for r in sorted({w for w in wired if wired.count(w) > 1}):
            yield error("E012", f"router '{n.name}' route '{r}' has more than one outgoing edge", node_id=n.id)
        for r in sorted(set(wired) - routes):
            yield warning("W012", f"router '{n.name}' has an edge for undeclared route '{r}'", node_id=n.id)


@rule
def loops_have_exits(ctx: ValidationContext) -> Iterator[Diagnostic]:
    """W001: every cycle should pass through a router (a guarded exit), else it runs to the recursion limit."""
    succ = ctx.successors
    color: dict[str, int] = {}
    reported: set[frozenset[str]] = set()
    names = {n.id: n.name for n in ctx.wf.nodes}

    def dfs(u: str, path: list[str]) -> Iterator[Diagnostic]:
        color[u] = 1
        for v in sorted(succ.get(u, ())):
            if color.get(v) == 1 and v in path:
                loop = frozenset(path[path.index(v) :])
                if not ctx.router_ids.intersection(loop) and loop not in reported:
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
        if src in ctx.router_ids:
            continue  # a router takes exactly one route
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
