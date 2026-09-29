"""Interpreter: build a runnable LangGraph graph directly from the IR (canvas runs).

Mirrors `codegen.py` line by line through the shared Execution Plan (`plan.py`) and node library
(`nodes.py`); `tests/test_equivalence.py` proves both produce the same behaviour.
"""

from __future__ import annotations

from typing import Any

from langgraph.cache.base import BaseCache
from langgraph.graph import END, START, StateGraph

from agentcanvas.adapters.langchain import nodes as ac
from agentcanvas.adapters.langchain.plan import Plan, lower
from agentcanvas.ir.models import Workflow

_SPECIAL = {"START": START, "END": END}


def build_graph(wf: Workflow, resources: ac.Resources, checkpointer=None, store=None, cache: BaseCache | None = None):
    return build_from_plan(lower(wf), resources, checkpointer=checkpointer, store=store, cache=cache)


def build_from_plan(plan: Plan, resources: ac.Resources, checkpointer=None, store=None, cache: BaseCache | None = None):
    State = ac.make_state_type("State", plan.channels)
    Input, Output = ac.make_io_types(plan.channels, plan.settings)
    Context = ac.make_context_type(plan.context_fields)
    builder: Any = StateGraph(State, context_schema=Context, input_schema=Input, output_schema=Output)
    ac.apply_node_defaults(builder, plan.settings.get("node_defaults"))
    routes: dict[str, Any] = {}

    for p in plan.nodes:
        n, cfg = p.node, p.node.config
        if p.bypass:
            fn: Any = ac.passthrough
        elif n.type == "core.llm":
            assert p.model is not None
            fn = ac.llm_node(
                model=resources.model(p.model.name, p.model.config), prompt=cfg["prompt"], output=cfg["output"]
            )
        elif n.type == "core.agent":
            assert p.model is not None
            fn = ac.agent_node(
                name=n.name,
                model=resources.model(p.model.name, p.model.config),
                tools=[resources.tool(t.name, t.config) for t in p.tools],
                system_prompt=cfg.get("system_prompt", ""),
                approve_tools=cfg.get("approve_tools"),
            )
        elif n.type == "core.router":
            routes[n.name] = ac.router_fn(cases=cfg.get("cases", []), default=cfg.get("default", "default"))
            fn = ac.passthrough
        elif n.type == "core.approval":
            fn = ac.approval_node(node_id=n.id, show=cfg["show"], output=cfg["output"])
        elif n.type == "core.set":
            fn = ac.set_node(values=cfg["values"])
        elif n.type == "core.script":
            fn = ac.script_node(import_path=cfg["import_path"])
        else:  # pragma: no cover - the validator rejects unknown types first
            raise ValueError(f"unsupported node type {n.type}")
        builder.add_node(n.name, fn, metadata={"canvas_node_id": n.id}, **ac.node_options(**p.options))

    for src, dst in plan.plain_edges:
        builder.add_edge(_SPECIAL.get(src, src), _SPECIAL.get(dst, dst))
    for router, mapping in plan.router_edges.items():
        builder.add_conditional_edges(router, routes[router], {k: _SPECIAL.get(v, v) for k, v in mapping.items()})

    if cache is None and plan.needs_cache:
        from langgraph.cache.memory import InMemoryCache

        cache = InMemoryCache()
    return builder.compile(
        checkpointer=checkpointer, store=store, cache=cache if plan.needs_cache else None, name=plan.wf.id
    )
