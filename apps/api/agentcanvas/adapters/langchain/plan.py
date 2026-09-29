"""Lowering: Workflow IR -> Execution Plan (spec doc 03 §5).

The plan is the single input of both the interpreter and the code generator, so decisions such as
"which node is a router", "where does an error route go", "which nodes are bypassed", "in which order
are resources built" or "which edges form a join" are made in exactly one place.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from agentcanvas.ir.models import Node, Resource, Workflow
from agentcanvas.registry import default_registry

ERROR_CHANNEL = "last_error"  # if declared, error routes write "<node>: <Error>: <message>" into it


LIST_RESOURCES = {"mcp.server"}  # resources that stand for several objects (all tools of an MCP server)


@dataclass
class PlannedResource:
    resource: Resource
    wired: dict[str, list[str]]  # input port -> resource names wired into it (in edge order)

    @property
    def many(self) -> bool:
        return self.resource.type in LIST_RESOURCES


@dataclass
class PlannedNode:
    node: Node
    router: bool
    bypass: bool  # disabled nodes pass state through unchanged
    options: dict[str, Any]  # arguments for nodes.node_options(...)
    wired: dict[str, list[str]] = field(default_factory=dict)  # input port -> resource names
    fan_out: bool = False  # core.map: conditional edge returning Send(...)s


@dataclass
class Plan:
    wf: Workflow
    channels: list[dict[str, Any]]
    settings: dict[str, Any]
    context_fields: list[dict[str, Any]]
    resources: list[PlannedResource]  # dependency order: a resource comes after what is wired into it
    nodes: list[PlannedNode]
    plain_edges: list[tuple[str, str]]  # (source name | START, target name | END)
    router_edges: dict[str, dict[str, str]]  # router name -> {route: target name | END}
    map_edges: dict[str, tuple[str, str]]  # map name -> (worker name, empty-list target | END)
    join_edges: list[tuple[list[str], str]]  # ([source names], join name): the join waits for all sources
    needs_cache: bool
    names: dict[str, str]  # canvas node id -> graph node name
    user_key: str | None  # context field filled with the authenticated user (source "user")

    @property
    def many(self) -> set[str]:
        """Names of list-valued resources (spread with `*name` where they are wired)."""
        return {pr.resource.name for pr in self.resources if pr.many}

    @property
    def mcp(self) -> list[PlannedResource]:
        return [pr for pr in self.resources if pr.resource.type == "mcp.server"]


def _ep(wf: Workflow, node_id: str) -> str:
    return node_id if node_id in ("START", "END") else wf.require_node(node_id).name


def _wired(wf: Workflow, element_id: str, ports: list[str]) -> dict[str, list[str]]:
    return {p: [r.name for r in wf.wired_into(element_id, p)] for p in ports if wf.wiring_into(element_id, p)}


def _resource_order(wf: Workflow) -> list[Resource]:
    """Topological order over resource -> resource wiring (e.g. model -> fallback middleware -> agent)."""
    by_id = {r.id: r for r in wf.resources}
    deps: dict[str, set[str]] = defaultdict(set)
    for e in wf.edges:
        if e.kind == "wiring" and e.source.node in by_id and e.target.node in by_id:
            deps[e.target.node].add(e.source.node)
    position = {r.id: i for i, r in enumerate(wf.resources)}
    ordered: list[Resource] = []
    state: dict[str, int] = {}

    def visit(rid: str) -> None:
        if state.get(rid) == 2:
            return
        if state.get(rid) == 1:
            raise ValueError(f"resource wiring cycle through '{by_id[rid].name}'")
        state[rid] = 1
        for d in sorted(deps[rid], key=position.__getitem__):
            visit(d)
        state[rid] = 2
        ordered.append(by_id[rid])

    for r in wf.resources:
        visit(r.id)
    return ordered


def lower(wf: Workflow) -> Plan:
    registry = default_registry()
    names = {n.id: n.name for n in wf.nodes}
    error_channel = ERROR_CHANNEL if wf.state.channel(ERROR_CHANNEL) else None
    router_ids = {n.id for n in wf.nodes if (m := registry.get(n.type)) and m.dynamic_outputs == "routes"}
    map_ids = {n.id for n in wf.nodes if n.type == "core.map"}  # like routers, maps stay active when disabled
    join_ids = {n.id for n in wf.nodes if n.type == "core.join"}

    resources = []
    for r in _resource_order(wf):
        m = registry.get(r.type)
        resources.append(PlannedResource(r, _wired(wf, r.id, [p.name for p in m.inputs] if m else [])))

    planned: list[PlannedNode] = []
    needs_cache = bool(wf.settings.node_defaults and wf.settings.node_defaults.cache)
    for n in wf.nodes:
        policies: dict[str, Any] | None = None
        if n.policies:  # keep configured-but-default sub-policies present, e.g. {"retry": {}} = default retry
            dumped = n.policies.model_dump(mode="json", exclude_defaults=True)
            policies = {k: dumped.get(k, {}) for k in ("retry", "timeout", "cache") if getattr(n.policies, k)}
            policies |= {k: v for k, v in dumped.items() if k not in ("retry", "timeout", "cache")}
        route = n.policies.error_route if n.policies else None
        options: dict[str, Any] = {"policies": policies}
        if route:
            options["error_target"] = names[route]
            options["error_channel"] = error_channel
        needs_cache = needs_cache or bool(n.policies and n.policies.cache)
        m = registry.get(n.type)
        planned.append(
            PlannedNode(
                node=n,
                router=n.id in router_ids,
                bypass=n.disabled and n.id not in router_ids | map_ids,
                options=options,
                wired=_wired(wf, n.id, [p.name for p in m.inputs] if m else []),
                fan_out=n.id in map_ids,
            )
        )

    conditional = router_ids | map_ids
    plain: list[tuple[str, str]] = []
    joins: dict[str, list[str]] = defaultdict(list)
    for e in wf.flow_edges():
        if e.source.node in conditional:
            continue
        if e.target.node in join_ids:
            joins[names[e.target.node]].append(_ep(wf, e.source.node))
        else:
            plain.append((_ep(wf, e.source.node), _ep(wf, e.target.node)))
    routers = {
        names[rid]: {e.source.route: _ep(wf, e.target.node) for e in wf.flow_edges() if e.source.node == rid}
        for rid in sorted(router_ids)
    }
    maps: dict[str, tuple[str, str]] = {}
    for mid in sorted(map_ids):
        out = {e.source.port or "next": _ep(wf, e.target.node) for e in wf.flow_edges() if e.source.node == mid}
        maps[names[mid]] = (out["each"], out.get("empty", "END"))
    user_key = next((f.name for f in wf.context.fields if f.source == "user"), None)
    return Plan(
        wf=wf,
        channels=[c.model_dump(mode="json", exclude_defaults=True) for c in wf.state.channels],
        settings=wf.settings.model_dump(mode="json", exclude_none=True, exclude_defaults=True),
        context_fields=[f.model_dump(mode="json", exclude_defaults=True) for f in wf.context.fields],
        resources=resources,
        nodes=planned,
        plain_edges=plain,
        router_edges=routers,
        map_edges=maps,
        join_edges=[(sorted(srcs), join) for join, srcs in sorted(joins.items())],
        needs_cache=needs_cache,
        names=names,
        user_key=user_key,
    )
