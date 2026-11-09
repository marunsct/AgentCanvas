"""Blast radius (FR-X-02, basic): what each step can do to the outside world, and whether a human gates it.

Capabilities come from the node manifests (`side_effects`), per-node declarations (`config.side_effects` on
scripts and Python tools), wired tools (HTTP tools write externally; MCP tools that announce
`destructive_hint`) and the steps' own nature (model calls spend money). A capability is **gated** when a
human approves it first: the agent's tool approvals cover that tool, or every path from START to the step passes
an approval / human-input step.
"""

from __future__ import annotations

import contextlib
from collections import deque
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from agentcanvas.ir.models import Node, Resource, Workflow
from agentcanvas.registry import Registry, default_registry
from agentcanvas.validation.analysis import node_side_effects

Effect = Literal["external_write", "message_send", "code_exec", "spend", "pii_read"]
RISKY = {"external_write", "message_send", "code_exec"}  # what a dry run stubs and the overlay flags when ungated
GATES = {"core.approval", "core.human_input"}


class Capability(BaseModel):
    effect: str
    source: str  # the step itself, or "<tool name>" wired to it
    gated: bool


class NodeBlast(BaseModel):
    node_id: str
    name: str
    capabilities: list[Capability] = Field(default_factory=list)

    @property
    def ungated_risky(self) -> list[Capability]:
        return [c for c in self.capabilities if c.effect in RISKY and not c.gated]


class BlastRadius(BaseModel):
    nodes: list[NodeBlast] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict)  # effect -> number of steps that have it
    ungated: list[str] = Field(default_factory=list)  # node ids with a risky capability no human gates


def tool_effects(r: Resource, registry: Registry) -> list[tuple[str, str]]:
    """(effect, tool name) pairs a tool resource brings to the steps it is wired to."""
    m = registry.get(r.type)
    effects = [(e, r.name) for e in sorted(set(m.side_effects) if m else set())]
    declared = r.config.get("side_effects")
    if isinstance(declared, list):
        effects += [(e, r.name) for e in declared if isinstance(e, str) and (e, r.name) not in effects]
    if r.type == "mcp.server":
        wanted = r.config.get("tool_filter") or []  # only the tools the workflow can call
        for t in r.config.get("discovered") or []:
            if isinstance(t, dict) and t.get("destructive") and (not wanted or t.get("name") in wanted):
                effects.append(("external_write", str(t.get("name"))))
    return effects


def _gated_by_humans(wf: Workflow) -> set[str]:
    """Steps every START path reaches only through an approval / human-input step."""
    succ: dict[str, list[str]] = {}
    for e in wf.flow_edges():
        succ.setdefault(e.source.node, []).append(e.target.node)
    gates = {n.id for n in wf.nodes if n.type in GATES and not n.disabled}
    seen, queue = {"START"}, deque(["START"])
    while queue:  # reachable without passing a gate
        for nxt in succ.get(queue.popleft(), []):
            if nxt not in seen:
                seen.add(nxt)
                if nxt not in gates:
                    queue.append(nxt)
    return {n.id for n in wf.nodes} - seen - gates


def blast_radius(wf: Workflow, registry: Registry | None = None) -> BlastRadius:
    registry = registry or default_registry()
    res = {r.id: r for r in wf.resources}
    wired: dict[str, list[Resource]] = {}
    for e in wf.edges:
        if e.kind == "wiring" and e.source.node in res:
            wired.setdefault(e.target.node, []).append(res[e.source.node])
    behind_gate = _gated_by_humans(wf)
    out = BlastRadius()

    def analyse(n: Node) -> NodeBlast:
        m = registry.get(n.type)
        gate_all = n.id in behind_gate
        approve = set(n.config.get("approve_tools") or [])
        nb = NodeBlast(node_id=n.id, name=n.name)
        for effect in sorted(node_side_effects(n, m) if m else set()):
            nb.capabilities.append(Capability(effect=effect, source=n.name, gated=gate_all))
        for r in wired.get(n.id, []):
            if r.type.startswith("model.") or r.type.startswith("mw."):
                continue
            for effect, tool in tool_effects(r, registry):
                gated = gate_all or tool in approve or r.name in approve
                nb.capabilities.append(Capability(effect=effect, source=tool, gated=gated))
        return nb

    for n in wf.nodes:
        if registry.bypassed(n):  # a bypassed path step still runs
            continue
        nb = analyse(n)
        if n.type == "core.subgraph":  # inner steps count for the subgraph step on this canvas
            try:
                inner = blast_radius(wf.subworkflow(n), registry)
            except ValidationError:  # reported by the subgraph rule (E018); it never runs
                inner = BlastRadius()
            for inb in inner.nodes:
                nb.capabilities += [
                    c.model_copy(update={"source": f"{inb.name}/{c.source}", "gated": c.gated or n.id in behind_gate})
                    for c in inb.capabilities
                ]
        if nb.capabilities:
            out.nodes.append(nb)
            if nb.ungated_risky:
                out.ungated.append(n.id)
    for nb in out.nodes:
        for effect in {c.effect for c in nb.capabilities}:
            out.summary[effect] = out.summary.get(effect, 0) + 1
    return out


STUBBED_NODE_EFFECTS = {"external_write", "message_send"}  # a step that acts outside by itself (a remote agent)


def stubbed_in_dry_run(n: Node, registry: Registry | None = None) -> bool:
    """Whether a dry run replaces step `n` by a recorder: its block writes externally or sends messages by itself
    (an A2A or MCP agent step), as its manifest's `side_effects` say. Code steps still run: what they run is checked
    before a test run instead."""
    m = (registry or default_registry()).get(n.type)
    return bool(m and set(m.side_effects or []) & STUBBED_NODE_EFFECTS)


def dry_run_tools(wf: Workflow, registry: Registry | None = None) -> set[str]:
    """Tool names a dry run replaces by recorders: every tool with a risky capability (subgraphs included)."""
    registry = registry or default_registry()
    out: set[str] = set()
    for r in wf.resources:
        for effect, tool in tool_effects(r, registry):
            if effect in RISKY:
                out.add(tool)
    for n in wf.nodes:
        if n.type == "core.subgraph":
            with contextlib.suppress(ValidationError):  # an invalid subgraph never runs (E018)
                out |= dry_run_tools(wf.subworkflow(n), registry)
    return out
