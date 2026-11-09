"""Interpreter: build a runnable LangGraph graph directly from the IR (canvas runs).

Mirrors `codegen.py` line by line through the shared Execution Plan (`plan.py`) and node library
(`nodes.py`); `tests/test_run.py` and `tests/test_catalog.py` prove both produce the same behaviour.
"""

from __future__ import annotations

from typing import Any

from langgraph.cache.base import BaseCache
from langgraph.graph import END, START, StateGraph

from agentcanvas import designs
from agentcanvas.adapters.langchain import interop
from agentcanvas.adapters.langchain import nodes as ac
from agentcanvas.adapters.langchain.plan import Plan, PlannedResource, lower
from agentcanvas.ir.models import Workflow
from agentcanvas.validation.blast import stubbed_in_dry_run

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
        return resources.middleware(r.name, r.type, cfg, models=wired("models") + wired("model"), tools=wired("tools"))
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


GUARDS = {"guard.pii", "guard.injection", "guard.moderation", "guard.schema"}


def guard_node(n: Any, cfg: dict[str, Any], models: list[Any]) -> Any:
    """A safety block (S23). Optional settings use the manifest defaults, like the generated code."""
    model = models[0] if models else None
    common = {
        "name": n.name,
        "input": cfg.get("input", "messages"),
        "flag_output": cfg.get("flag_output"),
        "report_output": cfg.get("report_output"),
    }
    if n.type == "guard.pii":
        return ac.pii_guard_node(
            **common,
            types=cfg.get("types", list(ac.guard.PII_TYPES)),
            custom_patterns=cfg.get("custom_patterns", []),
            strategy=cfg.get("strategy", "redact"),
            on_fail=cfg.get("on_fail", "redact"),
            output=cfg.get("output"),
        )
    if n.type == "guard.injection":
        return ac.injection_guard_node(
            **common, model=model, threshold=cfg.get("threshold", 0.5), on_fail=cfg.get("on_fail", "stop")
        )
    if n.type == "guard.moderation":
        return ac.moderation_guard_node(
            **common,
            model=model,
            categories=cfg.get("categories", list(ac.guard.MODERATION_CATEGORIES)),
            threshold=cfg.get("threshold", 0.5),
            on_fail=cfg.get("on_fail", "stop"),
        )
    return ac.schema_guard_node(
        **common,
        schema=cfg["schema"],
        model=model,
        repair=cfg.get("repair", False),
        on_fail=cfg.get("on_fail", "stop"),
        output=cfg.get("output"),
    )


def designed_agent(plan: Plan, n: Any, model: Any, tools: list[Any]) -> Any:
    """An Agent block designed for another framework: its generated code, run as one step (S21)."""
    fw = designs.AGENT_FRAMEWORKS[str(n.engine)]
    design = designs.design_for(plan.wf, n)
    source = designs.generate(fw.id, design)
    module = None
    python = None if designs.importable(fw.id) else designs.part_python(fw.id)
    if python is not None:  # not importable here, but in an environment of its own: run it as a part (S28)
        from agentcanvas.adapters.langchain.parts import PartModule

        module = PartModule(python, source=source)
    return interop.designed_agent_node(
        node_id=n.id,
        name=n.name,
        framework=fw.id,
        label=fw.label,
        api=fw.api,
        requires=fw.module,
        source=source,
        module=module,
        model=model,
        tools=tools,
        approve_tools=n.config.get("approve_tools"),
        structured=design.output_schema is not None,
        contract=designs.contract(design),
    )


def _registry(resources: Any) -> Any:
    provider = getattr(resources, "registry", None)
    return provider() if callable(provider) else None


def _dry_run_step(n: Any) -> Any:
    """Step `n` (a remote agent) as a dry run has it: it records what it would have sent and calls nobody."""
    cfg = n.config
    if n.type == "agent.mcp":
        arguments = cfg.get("arguments") or {}
        return interop.dry_run_node(
            name=n.name, framework="mcp", output=cfg.get("output", "messages"),
            sent=lambda state: {k: interop.render_template(v, state) for k, v in arguments.items()},
        )  # fmt: skip
    prompt = cfg.get("prompt", "")
    return interop.dry_run_node(
        name=n.name, framework=n.type.removeprefix("agent."), output=cfg.get("output", "messages"),
        sent=lambda state: {"message": interop._input_text(prompt, state)},
    )  # fmt: skip


def build_from_plan(plan: Plan, resources: ac.Resources, checkpointer=None, store=None, cache: BaseCache | None = None):
    State = ac.make_state_type("State", plan.channels)
    Input, Output = ac.make_io_types(plan.channels, plan.settings)
    Context = ac.make_context_type(plan.context_fields)
    builder: Any = StateGraph(State, context_schema=Context, input_schema=Input, output_schema=Output)
    ac.apply_node_defaults(builder, plan.settings.get("node_defaults"))
    routes: dict[str, Any] = {}
    objs: dict[str, Any] = {}
    registry = _registry(resources) if resources.dry_run is not None else None
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
            fn = ac.llm_node(
                model=wired("model")[0], prompt=cfg["prompt"], output=cfg["output"],
                files=ac.file_fields(plan.channels, cfg.get("files")), read_file=getattr(resources, "files", None),
            )  # fmt: skip
        elif n.type == "core.agent" and n.engine in designs.AGENT_FRAMEWORKS:  # S21: designed for another framework
            fn = designed_agent(plan, n, wired("model")[0], wired("tools"))
        elif n.type == "core.agent":
            fn = ac.agent_node(
                name=n.name,
                model=wired("model")[0],
                tools=wired("tools"),
                system_prompt=cfg.get("system_prompt", ""),
                approve_tools=cfg.get("approve_tools"),
                middleware=wired("middleware"),
                response_format=cfg.get("response_format"),
                files=ac.file_fields(plan.channels, cfg.get("files")),
                read_file=getattr(resources, "files", None),
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
        elif n.type == "core.decide":
            fn = ac.decide_node(model=wired("model")[0], prompt=cfg["prompt"], cases=cfg.get("cases", []),
                                output=cfg["output"])  # fmt: skip
            routes[n.name] = ac.decide_fn(output=cfg["output"], default=cfg.get("default", "default"),
                                          min_confidence=cfg.get("min_confidence"))  # fmt: skip
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
        elif n.type == "knowledge.search":
            fn = ac.knowledge_search_node(
                knowledge_base=cfg["knowledge_base"],
                query=cfg.get("query", "{{ state.question }}"),
                output=cfg.get("output", "passages"),
                mode=cfg.get("mode", "hybrid"),
                top_k=cfg.get("top_k", 5),
                min_score=cfg.get("min_score", 0.0),
                sources_output=cfg.get("sources_output"),
                resources=resources,
            )
        elif n.type in GUARDS:
            fn = guard_node(n, cfg, wired("model"))
        elif n.type == "core.script":
            fn = ac.script_node(import_path=cfg["import_path"], resources=resources)
        elif n.type == "agent.framework":
            if resources.script_runner is not None:  # E041 refuses it; this holds for versions validation skipped
                raise RuntimeError(
                    f"'{n.name}' would run workspace code inside the server, and this server runs workspace code in "
                    "a sandbox: design the agent on an AI agent block for its framework, or call your code from a "
                    "Script step"
                )
            fn = interop.framework_agent_node(
                node_id=n.id,
                name=n.name,
                framework=cfg["framework"],
                import_path=cfg["import_path"],
                workspace=resources.workspace,
                prompt=cfg.get("prompt", ""),
                output=cfg["output"],
                approve=cfg.get("approve", False),
            )
        elif n.type == "agent.a2a":
            fn = interop.a2a_agent_node(
                node_id=n.id,
                name=n.name,
                url=cfg["url"],
                prompt=cfg.get("prompt", ""),
                output=cfg["output"],
                headers=cfg.get("headers"),
                approve=cfg.get("approve", False),
                allow_private_network=cfg.get("allow_private_network", False) and not resources.public_only,
                timeout=cfg.get("timeout", 120),
                resolve=resources.resolve,
                transport=resources.http_transport,
            )
        elif n.type == "agent.mcp":
            fn = interop.mcp_agent_node(
                node_id=n.id,
                name=n.name,
                tools=wired("tools"),
                tool=cfg["tool"],
                arguments=cfg.get("arguments") or {},
                output=cfg["output"],
                approve=cfg.get("approve", False),
            )
        else:  # pragma: no cover - the validator rejects unknown types first
            raise ValueError(f"unsupported node type {n.type}")
        if resources.dry_run is not None and p.pinned is None and not p.bypass and stubbed_in_dry_run(n, registry):
            fn = _dry_run_step(n)  # a remote agent: a dry run (a test run, the Copilot's too) never calls it
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
