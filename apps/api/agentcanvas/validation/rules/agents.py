"""Agent rules: model capabilities, tool names, approvals, middleware, subagents, file backends."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator

from agentcanvas.ir.models import Node, Resource
from agentcanvas.validation.engine import Diagnostic, ValidationContext, error, rule, warning


@rule
def model_catalog(ctx: ValidationContext) -> Iterator[Diagnostic]:
    for r in ctx.wf.resources:
        if r.type == "model.chat" and isinstance(r.config.get("model"), str):
            if ctx.registry.model(r.config["model"]) is None:
                yield warning(
                    "W010",
                    f"model '{r.config['model']}' is not in the model catalog; capabilities are not checked",
                    node_id=r.id,
                )


@rule
def agent_tools(ctx: ValidationContext) -> Iterator[Diagnostic]:
    for el in [*ctx.wf.nodes, *ctx.wf.resources]:
        m = ctx.manifest(el)
        if m is None or m.input("tools") is None:
            continue
        tools = ctx.wf.wired_into(el.id, "tools")
        for name, count in sorted(Counter(t.name for t in tools).items()):
            if count > 1:
                yield error("E031", f"'{el.name}' has tool '{name}' wired {count} times", node_id=el.id)
        models = ctx.wf.wired_into(el.id, "model")
        info = ctx.registry.model(str(models[0].config.get("model", ""))) if models else None
        if tools and info is not None and not info.tools:
            yield error("E030", f"'{el.name}' has tools but model '{info.id}' cannot call tools", node_id=el.id)
        if el.config.get("response_format") and info is not None and not info.structured_output:
            yield error(
                "E030",
                f"'{el.name}' asks for structured output but model '{info.id}' does not support it",
                node_id=el.id,
            )
        if isinstance(el, Resource) and not tools:
            continue  # a subagent without its own tools inherits the main agent's tools
        approve = el.config.get("approve_tools") or []
        approve = approve if isinstance(approve, list) else []
        names, known = set(m.builtin_tools), True
        for t in tools:
            if t.type == "mcp.server":
                offered = mcp_tools(t)
                known = known and offered is not None
                names |= {x["name"] for x in offered or []}
                for x in offered or []:
                    if x.get("destructive") and x["name"] not in approve:
                        yield warning(
                            "W002",
                            f"'{el.name}' can call destructive MCP tool '{x['name']}' ({t.name}) without approval",
                            node_id=el.id,
                            hint="add it to the tools that need approval",
                        )
            else:
                names.add(t.name)
        for t in approve if known else []:
            if t not in names:
                yield error(
                    "E033",
                    f"'{el.name}' requires approval for '{t}', which is not wired to it",
                    node_id=el.id,
                    path="config.approve_tools",
                )


def mcp_tools(r: Resource) -> list[dict] | None:
    """Tools an MCP server offers, as cached by "Browse tools" (narrowed by the tool filter); None if unknown."""
    discovered = r.config.get("discovered")
    wanted = r.config.get("tool_filter") or []
    if not isinstance(discovered, list) or not discovered:
        return [{"name": n} for n in wanted] if wanted else None
    return [x for x in discovered if isinstance(x, dict) and (not wanted or x.get("name") in wanted)]


def _mw_key(r: Resource) -> str:
    """The upstream middleware `name` (create_agent rejects two middleware with the same name)."""
    if r.type == "mw.pii":
        return f"{r.type}[{r.config.get('pii_type')}]"
    if r.type == "mw.tool_call_limit":
        return f"{r.type}[{r.config.get('tool_name') or ''}]"
    return r.type


_DEEP_BUILTIN_MW = {"mw.summarization": "summarization"}


@rule
def middleware_stack(ctx: ValidationContext) -> Iterator[Diagnostic]:
    """E035: the same middleware twice on one agent, or one the Deep Agent already has built in."""
    for el in [*ctx.wf.nodes, *ctx.wf.resources]:
        m = ctx.manifest(el)
        if m is None or m.input("middleware") is None:
            continue
        mws = ctx.wf.wired_into(el.id, "middleware")
        for key, count in sorted(Counter(_mw_key(r) for r in mws).items()):
            if count > 1:
                yield error("E035", f"'{el.name}' has middleware {key} wired {count} times", node_id=el.id)
        if el.type == "agent.deep":
            for r in mws:
                if r.type in _DEEP_BUILTIN_MW:
                    yield error(
                        "E035",
                        f"Deep Agent '{el.name}' already has {_DEEP_BUILTIN_MW[r.type]} built in; remove '{r.name}'",
                        node_id=el.id,
                    )
                if r.type == "mw.todo" and el.config.get("planning", True):
                    yield error(
                        "E035",
                        f"Deep Agent '{el.name}' has planning on, which already adds to-dos; remove '{r.name}'",
                        node_id=el.id,
                        hint="or turn planning off in the Deep Agent's settings",
                    )


@rule
def deep_agents(ctx: ValidationContext) -> Iterator[Diagnostic]:
    """E032 subagents, E034 permission paths, E036 file mounts, E071 user-scoped folders."""
    has_user = any(f.source == "user" for f in ctx.wf.context.fields)
    for r in ctx.wf.resources:
        if r.type == "agent.subagent" and not str(r.config.get("description", "")).strip():
            yield error("E032", f"subagent '{r.name}' needs a description (when to delegate to it)", node_id=r.id)
        if r.type == "backend.store" and r.config.get("scope", "user") == "user" and not has_user:
            yield error(
                "E071",
                f"'{r.name}' is shared per user but the workflow has no context field with source 'user'",
                node_id=r.id,
                hint="add a context field (e.g. user_id) with source 'user' in the workflow settings",
            )
    for n in ctx.wf.nodes:
        if n.type != "agent.deep":
            continue
        subs = ctx.wf.wired_into(n.id, "subagents")
        for name, count in sorted(Counter(s.name for s in subs).items()):
            if count > 1:
                yield error("E032", f"'{n.name}' has subagent '{name}' wired {count} times", node_id=n.id)
        yield from _permissions(n)
        backends = ctx.wf.wired_into(n.id, "backend")
        mounts = Counter(b.config.get("mount", "/memories/") for b in backends if b.type == "backend.store")
        for path, count in sorted(mounts.items()):
            if count > 1:
                yield error("E036", f"'{n.name}' mounts two folders at '{path}'", node_id=n.id)
        if sum(1 for b in backends if b.type == "backend.state") > 1:
            yield error("E036", f"'{n.name}' has more than one default file backend", node_id=n.id)


def _permissions(n: Node) -> Iterator[Diagnostic]:
    perms = n.config.get("permissions")
    for i, p in enumerate(perms if isinstance(perms, list) else []):
        for j, path in enumerate(p.get("paths", []) if isinstance(p, dict) else []):
            if not isinstance(path, str) or not path.startswith("/") or ".." in path or "~" in path:
                yield error(
                    "E034",
                    f"'{n.name}': permission path '{path}' must be absolute and must not contain '..' or '~'",
                    node_id=n.id,
                    path=f"config.permissions[{i}].paths[{j}]",
                )
