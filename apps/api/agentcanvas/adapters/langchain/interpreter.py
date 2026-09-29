"""Interpreter: build a runnable LangGraph graph directly from the IR (canvas runs).

Mirrors `codegen.py` line by line through the shared Execution Plan (`plan.py`) and node library
(`nodes.py`); `tests/test_run.py` and `tests/test_catalog.py` prove both produce the same behaviour.
"""

from __future__ import annotations

from typing import Any

from langgraph.cache.base import BaseCache
from langgraph.graph import END, START, StateGraph

from agentcanvas.adapters.langchain import nodes as ac
from agentcanvas.adapters.langchain.plan import Plan, PlannedResource, lower
from agentcanvas.ir.models import Workflow

_SPECIAL = {"START": START, "END": END}
MEMORY_OPS = {"core.memory_recall": "recall", "core.memory_remember": "remember", "core.memory_forget": "forget"}


def build_graph(wf: Workflow, resources: ac.Resources, checkpointer=None, store=None, cache: BaseCache | None = None):
    return build_from_plan(lower(wf), resources, checkpointer=checkpointer, store=store, cache=cache)


async def abuild_graph(wf: Workflow, resources: ac.Resources, checkpointer=None, store=None, cache=None):
    """Like `build_graph`, after loading what needs network I/O first (MCP tool lists)."""
    plan = lower(wf)
    await prepare(plan, resources)
    return build_from_plan(plan, resources, checkpointer=checkpointer, store=store, cache=cache)


async def prepare(plan: Plan, resources: ac.Resources) -> None:
    for pr in plan.mcp:
        await resources.load_mcp(pr.resource.name, pr.resource.config)


def _flat(objs: dict[str, Any], names: list[str]) -> list[Any]:
    """Wired objects in order; a list-valued resource (an MCP server's tools) contributes all its items."""
    out: list[Any] = []
    for name in names:
        value = objs[name]
        out.extend(value) if isinstance(value, list) else out.append(value)
    return out


def _resource(pr: PlannedResource, plan: Plan, resources: ac.Resources, objs: dict[str, Any]) -> Any:
    r, cfg = pr.resource, pr.resource.config

    def wired(port: str) -> list[Any]:
        return _flat(objs, pr.wired.get(port, []))

    if r.type == "model.chat":
        return resources.model(r.name, cfg)
    if r.type == "tool.memory":
        return ac.memory_tools(
            space=cfg.get("space", "memories"),
            scope=cfg.get("scope", "user"),
            workflow_id=plan.wf.id,
            user_key=plan.user_key,
        )
    if r.type.startswith("tool.") or r.type == "mcp.server":
        return resources.tool(r.name, cfg, r.type)
    if r.type.startswith("mw."):
        return resources.middleware(r.name, r.type, cfg, models=wired("models") + wired("model"))
    if r.type == "agent.subagent":
        models = wired("model")
        return ac.subagent(
            name=r.name,
            description=cfg["description"],
            system_prompt=cfg["system_prompt"],
            model=models[0] if models else None,
            tools=wired("tools"),
            middleware=wired("middleware"),
            approve_tools=cfg.get("approve_tools"),
        )
    if r.type.startswith("backend."):
        return ac.backend(r.type, cfg, workflow_id=plan.wf.id, user_key=plan.user_key)
    raise ValueError(f"unsupported resource type {r.type}")  # pragma: no cover - rejected by the validator


def build_from_plan(plan: Plan, resources: ac.Resources, checkpointer=None, store=None, cache: BaseCache | None = None):
    State = ac.make_state_type("State", plan.channels)
    Input, Output = ac.make_io_types(plan.channels, plan.settings)
    Context = ac.make_context_type(plan.context_fields)
    builder: Any = StateGraph(State, context_schema=Context, input_schema=Input, output_schema=Output)
    ac.apply_node_defaults(builder, plan.settings.get("node_defaults"))
    routes: dict[str, Any] = {}
    objs: dict[str, Any] = {}
    for pr in plan.resources:
        objs[pr.resource.name] = _resource(pr, plan, resources, objs)

    for p in plan.nodes:
        n, cfg = p.node, p.node.config

        def wired(port: str, p=p) -> list[Any]:
            return _flat(objs, p.wired.get(port, []))

        if p.pinned is not None:
            fn: Any = ac.pinned_node(p.pinned)
        elif p.bypass:
            fn = ac.passthrough
        elif p.impl:
            fn = ac.custom_node(import_path=p.impl, config=cfg, resources=resources)
        elif n.type == "core.llm":
            fn = ac.llm_node(model=wired("model")[0], prompt=cfg["prompt"], output=cfg["output"])
        elif n.type == "core.agent":
            fn = ac.agent_node(
                name=n.name,
                model=wired("model")[0],
                tools=wired("tools"),
                system_prompt=cfg.get("system_prompt", ""),
                approve_tools=cfg.get("approve_tools"),
                middleware=wired("middleware"),
                response_format=cfg.get("response_format"),
            )
        elif n.type == "agent.deep":
            fn = ac.deep_agent_node(
                name=n.name,
                model=wired("model")[0],
                tools=wired("tools"),
                system_prompt=cfg.get("system_prompt", ""),
                middleware=wired("middleware"),
                subagents=wired("subagents"),
                backends=wired("backend"),
                planning=cfg.get("planning", True),
                approve_tools=cfg.get("approve_tools"),
                permissions=cfg.get("permissions"),
                response_format=cfg.get("response_format"),
            )
        elif n.type == "core.router":
            routes[n.name] = ac.router_fn(cases=cfg.get("cases", []), default=cfg.get("default", "default"))
            fn = ac.passthrough
        elif n.type == "core.map":
            worker, empty = plan.map_edges[n.name]
            routes[n.name] = ac.map_fn(
                items=cfg["items"],
                item=cfg["item"],
                target=worker,
                empty=_SPECIAL.get(empty, empty),
                carry=cfg.get("carry", []),
                max_items=cfg.get("max_items"),
            )
            fn = ac.passthrough
        elif n.type == "core.join":
            fn = ac.passthrough
        elif n.type == "core.subgraph":
            assert p.subplan is not None
            fn = ac.subgraph_node(
                graph=build_from_plan(p.subplan, resources),  # no checkpointer: inherits the parent's
                inputs=cfg.get("inputs", []),
                outputs=cfg.get("outputs", []),
                append=p.append_outputs,
            )
        elif n.type == "core.approval":
            fn = ac.approval_node(node_id=n.id, show=cfg["show"], output=cfg["output"])
        elif n.type == "core.human_input":
            fn = ac.human_input_node(
                node_id=n.id, prompt=cfg["prompt"], output=cfg["output"], response_schema=cfg.get("response_schema")
            )
        elif n.type in MEMORY_OPS:
            fn = ac.memory_node(
                op=MEMORY_OPS[n.type],
                space=cfg.get("space", "memories"),
                scope=cfg.get("scope", "user"),
                workflow_id=plan.wf.id,
                user_key=plan.user_key,
                key=cfg.get("key"),
                query=cfg.get("query"),
                top_k=cfg.get("top_k", 5),
                value=cfg.get("value"),
                output=cfg.get("output"),
            )
        elif n.type == "core.set":
            fn = ac.set_node(values=cfg["values"])
        elif n.type == "core.script":
            fn = ac.script_node(import_path=cfg["import_path"], resources=resources)
        else:  # pragma: no cover - the validator rejects unknown types first
            raise ValueError(f"unsupported node type {n.type}")
        builder.add_node(n.name, fn, metadata={"canvas_node_id": n.id}, **ac.node_options(**p.options))

    for src, dst in plan.plain_edges:
        builder.add_edge(_SPECIAL.get(src, src), _SPECIAL.get(dst, dst))
    for sources, join in plan.join_edges:
        builder.add_edge(sources, join)
    for router, mapping in plan.router_edges.items():
        builder.add_conditional_edges(router, routes[router], {k: _SPECIAL.get(v, v) for k, v in mapping.items()})
    for name, (worker, empty) in plan.map_edges.items():
        builder.add_conditional_edges(name, routes[name], [worker, _SPECIAL.get(empty, empty)])

    if cache is None and plan.needs_cache:
        from langgraph.cache.memory import InMemoryCache

        cache = InMemoryCache()
    return builder.compile(
        checkpointer=checkpointer, store=store, cache=cache if plan.needs_cache else None, name=plan.wf.id
    )
