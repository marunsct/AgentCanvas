"""Agent rules: model capabilities, tool names, approvals, middleware, subagents, file backends."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator

from agentcanvas.ir.models import Node, Resource
from agentcanvas.registry import provider_installed
from agentcanvas.validation.engine import Diagnostic, ValidationContext, error, rule, warning


@rule
def model_catalog(ctx: ValidationContext) -> Iterator[Diagnostic]:
    for r in ctx.wf.resources:
        if r.type == "model.chat" and isinstance(r.config.get("model"), str):
            model = r.config["model"]
            if ctx.registry.model(model) is None:
                yield warning(
                    "W010", f"model '{model}' is not in the model catalog; capabilities are not checked", node_id=r.id
                )
            provider = ctx.registry.provider(model.partition(":")[0])
            if provider is None:
                continue
            if provider.base_url_required and not r.config.get("base_url"):
                yield error("E017", f"'{r.name}': {provider.label} models need a Base URL", node_id=r.id)
            if not provider_installed(provider):
                yield warning(
                    "W011",
                    f"'{r.name}': the {provider.label} integration is not installed on this server "
                    f"(install {provider.package})",
                    node_id=r.id,
                )


def _writes(t: Resource, ctx: ValidationContext) -> str | None:
    """The first state-changing effect of a tool (S6): sending messages or writing outside the run. An HTTP tool
    writes unless it only reads (GET/HEAD)."""
    from agentcanvas.validation.blast import tool_effects

    if t.type == "tool.http" and str(t.config.get("method", "GET")).upper() in ("GET", "HEAD"):
        return None
    return next((e for e, _ in tool_effects(t, ctx.registry) if e in ("external_write", "message_send")), None)


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
            elif (tm := ctx.manifest(t)) is not None and tm.builtin_tools:
                names |= set(tm.builtin_tools)  # e.g. memory tools: search_memory, save_memory
            else:
                names.add(t.name)
                if t.name not in approve and (effect := _writes(t, ctx)):
                    yield warning(
                        "W002",
                        f"'{el.name}' can call tool '{t.name}' ({effect.replace('_', ' ')}) without approval",
                        node_id=el.id,
                        hint="add it to the tools that need approval, or declare it read-only",
                    )
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
USER_SCOPED = {"backend.store", "tool.memory", "core.memory_recall", "core.memory_remember", "core.memory_forget"}


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
def retry_inside_fallback(ctx: ValidationContext) -> Iterator[Diagnostic]:
    """W017: "Retry AI calls" connected after "Backup models" and set to carry on when retries run out answers the
    failure itself (an error message to the agent), so the backup models are never tried. The first add-on
    connected wraps the ones after it."""
    for el in [*ctx.wf.nodes, *ctx.wf.resources]:
        mws = ctx.wf.wired_into(el.id, "middleware")
        fallback = next((i for i, r in enumerate(mws) if r.type == "mw.model_fallback"), None)
        if fallback is None:
            continue
        for r in mws[fallback + 1 :]:
            if r.type == "mw.model_retry" and r.config.get("on_failure", "continue") != "error":
                yield warning(
                    "W017",
                    f"'{r.name}' answers the failure itself when its retries run out, so the backup models of "
                    f"'{mws[fallback].name}' on '{el.name}' are never tried",
                    node_id=r.id,
                    hint="set its 'When retries are exhausted' to error, or connect it before the backup models",
                )


@rule
def deep_agents(ctx: ValidationContext) -> Iterator[Diagnostic]:
    """E032 subagents, E034 permission paths, E036 file mounts, E071 user-scoped folders."""
    has_user = any(f.source == "user" for f in ctx.wf.context.fields)
    for r in ctx.wf.resources:
        if r.type == "agent.subagent" and not str(r.config.get("description", "")).strip():
            yield error("E032", f"subagent '{r.name}' needs a description (when to delegate to it)", node_id=r.id)
    for el in [*ctx.wf.resources, *ctx.wf.nodes]:
        if el.type in USER_SCOPED and el.config.get("scope", "user") == "user" and not has_user:
            yield error(
                "E071",
                f"'{el.name}' is shared per user but the workflow has no context field with source 'user'",
                node_id=el.id,
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


@rule
def cost_budget_prices(ctx: ValidationContext) -> Iterator[Diagnostic]:
    """W015: a USD budget can only count models whose catalog entry has prices (backup models included)."""
    b = ctx.wf.settings.budget
    limits = (b.max_cost_usd, b.thread_max_cost_usd, b.daily_max_cost_usd, b.monthly_max_cost_usd) if b else ()
    if all(v is None for v in limits):
        return
    for r in ctx.wf.resources:
        if r.type != "model.chat":
            continue
        for model in [r.config.get("model", ""), *(r.config.get("fallbacks") or [])]:
            info = ctx.registry.model(str(model))
            if info is None or info.price_in_per_mtok is None or info.price_out_per_mtok is None:
                which = f"'{r.name}'" if model == r.config.get("model") else f"the backup model {model} of '{r.name}'"
                yield warning(
                    "W015",
                    f"{which} has no catalog price, so the USD budget does not count its calls",
                    node_id=r.id,
                    hint="an operator sets its price with AGENTCANVAS_MODEL_PRICES, or use a token budget",
                )


@rule
def backup_models(ctx: ValidationContext) -> Iterator[Diagnostic]:
    """W016: a model block's backup models (S26) should differ from it and from each other."""
    for r in ctx.wf.resources:
        if r.type != "model.chat":
            continue
        seen = {str(r.config.get("model", ""))}
        for i, model in enumerate(r.config.get("fallbacks") or []):
            if model in seen:
                yield warning(
                    "W016",
                    f"the backup model {model} of '{r.name}' is already tried before it",
                    node_id=r.id,
                    path=f"config.fallbacks[{i}]",
                    hint="pick a model from another provider, so an outage of one does not stop both",
                )
            seen.add(str(model))
