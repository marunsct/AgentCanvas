"""Agent rules: model capabilities, tool names, approvals."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator

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
    for n in ctx.wf.nodes:
        m = ctx.manifest(n)
        if m is None or m.input("tools") is None:
            continue
        tools = ctx.wf.wired_into(n.id, "tools")
        for name, count in sorted(Counter(t.name for t in tools).items()):
            if count > 1:
                yield error("E031", f"'{n.name}' has tool '{name}' wired {count} times", node_id=n.id)
        models = ctx.wf.wired_into(n.id, "model")
        if tools and models:
            info = ctx.registry.model(str(models[0].config.get("model", "")))
            if info is not None and not info.tools:
                yield error("E030", f"'{n.name}' has tools but model '{info.id}' cannot call tools", node_id=n.id)
        approve = n.config.get("approve_tools") or []
        names = {t.name for t in tools}
        for t in approve if isinstance(approve, list) else []:
            if t not in names:
                yield error(
                    "E033",
                    f"'{n.name}' requires approval for '{t}', which is not wired to it",
                    node_id=n.id,
                    path="config.approve_tools",
                )
