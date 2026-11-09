"""Interpreter: build a runnable LangGraph graph directly from the IR (dev runs)."""

from __future__ import annotations

from collections.abc import Hashable
from typing import Any

from langgraph.graph import END, START, StateGraph

from agentcanvas.ir.models import Workflow
from agentcanvas.runtime import nodes as ac


def _endpoint(wf: Workflow, node_id: str) -> Any:
    if node_id == "START":
        return START
    if node_id == "END":
        return END
    return wf.require_node(node_id).name  # LangGraph node name == IR node name


def build_graph(wf: Workflow, resources: ac.Resources, checkpointer=None, store=None):
    State = ac.make_state_type("State", [c.model_dump() for c in wf.state.channels])
    builder = StateGraph(State)
    routers: dict[str, Any] = {}

    for n in wf.nodes:
        cfg = n.config
        meta = {"canvas_node_id": n.id}
        if n.type == "core.llm":
            m = wf.wired_into(n.id, "model")[0]
            fn = ac.llm_node(model=resources.model(m.name, m.config), prompt=cfg["prompt"], output=cfg["output"])
        elif n.type == "core.agent":
            m = wf.wired_into(n.id, "model")[0]
            tools = [resources.tool(t.name, t.config) for t in wf.wired_into(n.id, "tools")]
            fn = ac.agent_node(
                name=n.name,
                model=resources.model(m.name, m.config),
                tools=tools,
                system_prompt=cfg.get("system_prompt", ""),
                approve_tools=cfg.get("approve_tools"),
            )
        elif n.type == "core.router":
            routers[n.id] = ac.router_fn(cases=cfg.get("cases", []), default=cfg.get("default", "default"))
            fn = ac.passthrough
        elif n.type == "core.approval":
            fn = ac.approval_node(node_id=n.id, show=cfg["show"], output=cfg["output"])
        elif n.type == "core.set":
            fn = ac.set_node(values=cfg["values"])
        elif n.type == "core.script":
            fn = ac.script_node(import_path=cfg["import_path"])
        else:  # pragma: no cover - validator rejects unknown types first
            raise ValueError(f"unsupported node type {n.type}")
        builder.add_node(n.name, fn, metadata=meta)

    for e in wf.flow_edges():
        if e.source.node in routers:
            continue  # handled below as conditional edges
        builder.add_edge(_endpoint(wf, e.source.node), _endpoint(wf, e.target.node))

    for router_id, fn in routers.items():
        mapping: dict[Hashable, str] = {
            e.source.route: _endpoint(wf, e.target.node) for e in wf.flow_edges() if e.source.node == router_id
        }
        builder.add_conditional_edges(wf.require_node(router_id).name, fn, mapping)

    return builder.compile(checkpointer=checkpointer, store=store, name=wf.id)
