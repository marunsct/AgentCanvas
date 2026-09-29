"""Canvas refactorings computed on the server and returned as ordinary command batches (M2-T13).

The editor dispatches the returned commands like any other edit, so they are optimistic, autosaved and undoable.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from agentcanvas.errors import InvalidInput
from agentcanvas.ids import new_id
from agentcanvas.ir.commands import (
    AddNode,
    AddResource,
    Connect,
    RemoveNode,
    RemoveResource,
    _Cmd,
)
from agentcanvas.ir.models import Edge, Endpoint, Node, Workflow
from agentcanvas.registry import default_registry
from agentcanvas.validation.analysis import node_reads, node_writes

_IDENT = re.compile(r"[^a-z0-9_]")


def _unique_name(wf: Workflow, wanted: str) -> str:
    base = _IDENT.sub("_", wanted.lower()).strip("_") or "subgraph"
    if not base[0].isalpha():
        base = f"s_{base}"
    taken = {n.name for n in wf.nodes} | {r.name for r in wf.resources}
    name, i = base, 2
    while name in taken:
        name, i = f"{base}_{i}", i + 1
    return name


def to_subgraph(wf: Workflow, node_ids: list[str], name: str | None = None) -> list[_Cmd]:
    """Commands that replace the given steps by one `core.subgraph` step behaving the same.

    The group needs one way in (every incoming flow edge targets the same step) and one way out (every outgoing
    edge leaves from the same step through its default output); error routes may not cross its border.
    """
    registry = default_registry()
    group = [wf.require_node(i) for i in dict.fromkeys(node_ids)]
    if not group:
        raise InvalidInput("select at least one step")
    ids = {n.id for n in group}
    flow = wf.flow_edges()
    incoming = [e for e in flow if e.target.node in ids and e.source.node not in ids]
    outgoing = [e for e in flow if e.source.node in ids and e.target.node not in ids]
    entries = {e.target.node for e in incoming}
    exits = {e.source.node for e in outgoing}
    if len(entries) != 1:
        raise InvalidInput(f"a subgraph needs exactly one way in; the selection is entered at {len(entries)} steps")
    if len(exits) > 1 or any(e.source.port for e in outgoing):
        raise InvalidInput("a subgraph needs one way out: leave the selection from a single step's default output")
    for n in wf.nodes:
        route = n.policies.error_route if n.policies else None
        if route and (n.id in ids) != (route in ids):
            raise InvalidInput(f"the error route of '{n.name}' crosses the selection border")

    entry, exit_ = next(iter(entries)), next(iter(exits), None)
    manifests = {n.id: registry.get(n.type) for n in group}
    reads = set().union(*(node_reads(n, m) for n in group if (m := manifests[n.id])))
    writes = set().union(*(node_writes(n, m) for n in group if (m := manifests[n.id])))
    used = reads | writes
    channels = [c.model_dump(mode="json", exclude={"io"}) for c in wf.state.channels if c.name in used]
    declared = {c["name"] for c in channels}

    wiring = [e for e in wf.edges if e.kind == "wiring" and e.target.node in ids]
    res_ids = {e.source.node for e in wiring}
    # resources feeding the copied resources (e.g. a model wired into a fallback middleware) come along too
    frontier = set(res_ids)
    while frontier:
        feeding = {e.source.node for e in wf.edges if e.kind == "wiring" and e.target.node in frontier}
        frontier = feeding - res_ids
        res_ids |= feeding
    resources = [r for r in wf.resources if r.id in res_ids]
    inner_edges = [
        e
        for e in wf.edges
        if (e.kind == "flow" and e.source.node in ids and e.target.node in ids)
        or (e.kind == "wiring" and e.target.node in ids | res_ids)
    ]
    start = Edge(id="e_in", kind="flow", source=Endpoint(node="START"), target=Endpoint(node=entry))
    end = [Edge(id="e_out", kind="flow", source=Endpoint(node=exit_), target=Endpoint(node="END"))] if exit_ else []
    inner: dict[str, Any] = {
        "ir_version": wf.ir_version,
        "id": f"{wf.id}.{name or 'subgraph'}",
        "name": name or "Subgraph",
        "state": {"channels": channels},
        "resources": [r.model_dump(mode="json", exclude_none=True) for r in resources],
        "nodes": [n.model_dump(mode="json", exclude_none=True) for n in group],
        "edges": [e.model_dump(mode="json", exclude_none=True) for e in [start, *inner_edges, *end]],
    }
    sub_name = _unique_name(wf, name or f"{wf.require_node(entry).name}_group")
    inner["id"], inner["name"] = f"{wf.id}.{sub_name}", name or sub_name.replace("_", " ").capitalize()
    node = Node(
        id=new_id("n"),
        type="core.subgraph",
        name=sub_name,
        label=inner["name"],
        config={
            "workflow": inner,
            "inputs": sorted(reads & declared),
            "outputs": sorted(writes & declared),
        },
    )
    cmds: list[_Cmd] = [AddNode(node=node)]
    for e in incoming:
        cmds.append(Connect(edge=e.model_copy(update={"id": new_id("e"), "target": Endpoint(node=node.id)})))
    for e in outgoing:
        cmds.append(Connect(edge=e.model_copy(update={"id": new_id("e"), "source": Endpoint(node=node.id)})))
    cmds += [RemoveNode(id=n.id) for n in group]
    # resources nobody outside the group uses any more move into the subgraph (the others are copied)
    outer_nodes = {n.id for n in wf.nodes} - ids
    still_used = {e.source.node for e in wf.edges if e.kind == "wiring" and e.target.node in outer_nodes}
    frontier = set(still_used)
    while frontier:  # ...and whatever feeds a resource that stays
        frontier = {e.source.node for e in wf.edges if e.kind == "wiring" and e.target.node in frontier} - still_used
        still_used |= frontier
    cmds += [RemoveResource(id=r.id) for r in resources if r.id not in still_used]
    return cmds


def inline_subgraph(wf: Workflow, node_id: str) -> list[_Cmd]:
    """Commands that replace a `core.subgraph` step by its steps again (the reverse of `to_subgraph`)."""
    sub = wf.require_node(node_id)
    if sub.type != "core.subgraph":
        raise InvalidInput(f"'{sub.name}' is not a subgraph")
    inner = wf.subworkflow(sub)
    outer_ids = {n.id for n in wf.nodes} | {r.id for r in wf.resources} | {e.id for e in wf.edges}
    outer_res = {r.id: r for r in wf.resources}
    clash = [x.id for x in [*inner.nodes, *inner.edges] if x.id in outer_ids and x.id != node_id]
    clash += [r.id for r in inner.resources if r.id in outer_res and outer_res[r.id].config != r.config]
    if clash:
        raise InvalidInput(f"cannot inline '{sub.name}': ids already used in this workflow: {', '.join(clash[:5])}")
    entries = [e.target.node for e in inner.flow_edges() if e.source.node == "START"]
    exits = [(e.source.node, e.source.port) for e in inner.flow_edges() if e.target.node == "END"]
    cmds: list[_Cmd] = [AddResource(resource=copy.deepcopy(r)) for r in inner.resources if r.id not in outer_res]
    cmds += [AddNode(node=copy.deepcopy(n)) for n in inner.nodes]
    cmds += [
        Connect(edge=e) for e in inner.edges if "START" not in (e.source.node, e.target.node) and e.target.node != "END"
    ]
    for e in wf.flow_edges():
        if e.target.node == node_id:
            for entry in entries:
                cmds.append(Connect(edge=e.model_copy(update={"id": new_id("e"), "target": Endpoint(node=entry)})))
        if e.source.node == node_id:
            for src, port in exits:
                edge = e.model_copy(update={"id": new_id("e"), "source": Endpoint(node=src, port=port)})
                cmds.append(Connect(edge=edge))
    cmds.append(RemoveNode(id=node_id))
    return cmds
