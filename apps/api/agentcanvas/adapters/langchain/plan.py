"""Lowering: Workflow IR -> Execution Plan (spec doc 03 §5).

The plan is the single input of both the interpreter and the code generator, so decisions such as
"which node is a router", "where does an error route go" or "which nodes are bypassed" are made in
exactly one place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentcanvas.ir.models import Node, Resource, Workflow
from agentcanvas.registry import default_registry

ERROR_CHANNEL = "last_error"  # if declared, error routes write "<node>: <Error>: <message>" into it


@dataclass
class PlannedNode:
    node: Node
    router: bool
    bypass: bool  # disabled nodes pass state through unchanged
    options: dict[str, Any]  # arguments for nodes.node_options(...)
    model: Resource | None = None
    tools: list[Resource] = field(default_factory=list)


@dataclass
class Plan:
    wf: Workflow
    channels: list[dict[str, Any]]
    settings: dict[str, Any]
    context_fields: list[dict[str, Any]]
    nodes: list[PlannedNode]
    plain_edges: list[tuple[str, str]]  # (source name | START, target name | END)
    router_edges: dict[str, dict[str, str]]  # router name -> {route: target name | END}
    needs_cache: bool
    names: dict[str, str]  # canvas node id -> graph node name


def _ep(wf: Workflow, node_id: str) -> str:
    return node_id if node_id in ("START", "END") else wf.require_node(node_id).name


def lower(wf: Workflow) -> Plan:
    registry = default_registry()
    names = {n.id: n.name for n in wf.nodes}
    error_channel = ERROR_CHANNEL if wf.state.channel(ERROR_CHANNEL) else None
    router_ids = {n.id for n in wf.nodes if (m := registry.get(n.type)) and m.dynamic_outputs == "routes"}

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
        models = wf.wired_into(n.id, "model")
        planned.append(
            PlannedNode(
                node=n,
                router=n.id in router_ids,
                bypass=n.disabled and n.id not in router_ids,
                options=options,
                model=models[0] if models else None,
                tools=wf.wired_into(n.id, "tools"),
            )
        )

    plain = [
        (_ep(wf, e.source.node), _ep(wf, e.target.node)) for e in wf.flow_edges() if e.source.node not in router_ids
    ]
    routers = {
        names[rid]: {e.source.route: _ep(wf, e.target.node) for e in wf.flow_edges() if e.source.node == rid}
        for rid in sorted(router_ids)
    }
    return Plan(
        wf=wf,
        channels=[c.model_dump(mode="json", exclude_defaults=True) for c in wf.state.channels],
        settings=wf.settings.model_dump(mode="json", exclude_none=True, exclude_defaults=True),
        context_fields=[f.model_dump(mode="json", exclude_defaults=True) for f in wf.context.fields],
        nodes=planned,
        plain_edges=plain,
        router_edges=routers,
        needs_cache=needs_cache,
        names=names,
    )
