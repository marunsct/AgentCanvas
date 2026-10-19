"""Portability rules (S20, spec doc 12 §C.6 and doc 03): which framework can run each block.

The workflow's `framework` (and a block's optional `engine`, IR 1.2) must be installed adapters, and every block must
be one that framework can run, natively or by emulation, as its manifest (`registry/adapters/*.json`) says.

* P001 error — the framework is not installed, or cannot run this block (or the block cannot have an engine).
* P002 warning — the framework only emulates this block.
* P003 info — with more than one framework installed: which others this workflow could move to, and what blocks it.
* P004 info — a block is designed for another installed framework than the workflow's; it runs on the workflow's
  framework.
* P005 error — an Agent block designed for another agent framework (S21) uses something that framework cannot take.
* P006 warning — that agent framework is not installed on this server, so the block's runs fail.
* P007 warning — the workflow's framework does not apply node policies (retry, timeout, cache, error route).

An Agent block may name an agent framework (`designs.AGENT_FRAMEWORKS`, e.g. OpenAI Agents SDK) as its engine: the
workflow's framework then runs it as a step in that framework's own code, if it can (the `agent_designs` ability).
"""

from __future__ import annotations

from collections.abc import Iterator

from agentcanvas import designs
from agentcanvas.registry import adapters
from agentcanvas.validation.engine import Diagnostic, ValidationContext, error, info, rule, warning

ENGINE_TYPES = {"core.llm", "core.agent", "agent.deep"}  # AI blocks a framework can design differently


@rule
def framework_runs_every_block(ctx: ValidationContext) -> Iterator[Diagnostic]:
    wf = ctx.wf
    installed = {m.id: m for m in adapters.installed()}
    target = installed.get(wf.framework)
    if target is None:
        names = ", ".join(m.label for m in installed.values())
        yield error(
            "P001",
            f"this workflow is designed for '{wf.framework}', which this server does not have (it has {names})",
            hint="Pick an installed framework in the workflow settings (click empty canvas → Framework).",
        )
        return
    for el in [*wf.nodes, *wf.resources]:
        level = target.support(el.type)
        if level == "unsupported":
            yield error("P001", f"{target.label} cannot run '{el.type}' blocks", node_id=el.id)
        elif level == "emulated":
            how = target.notes.get(el.type, "it behaves differently there")
            yield warning("P002", f"{target.label} only emulates '{el.type}': {how}", node_id=el.id)
    for n in wf.nodes:
        if n.engine is None:
            continue
        if n.type not in ENGINE_TYPES:
            yield error("P001", f"only AI blocks can name a framework of their own, not '{n.type}'", node_id=n.id)
            continue
        if n.engine == wf.framework:  # e.g. an OpenAI agent in an OpenAI Agents SDK flow: native there
            continue
        if n.engine in designs.AGENT_FRAMEWORKS:
            yield from _designed(ctx, n, target)
            continue
        engine = installed.get(n.engine)
        if engine is None:
            yield error(
                "P001", f"this block is designed for '{n.engine}', which this server does not have", node_id=n.id
            )
        elif engine.support(n.type) == "unsupported":
            yield error("P001", f"{engine.label} cannot run '{n.type}' blocks", node_id=n.id)
        elif n.engine != wf.framework:
            yield info(
                "P004",
                f"designed for {engine.label}; it runs on {target.label}, the workflow's framework, until "
                "mixed-framework flows arrive",
                node_id=n.id,
            )


@rule
def portability_summary(ctx: ValidationContext) -> Iterator[Diagnostic]:
    installed = adapters.installed()
    if len(installed) < 2 or adapters.manifest(ctx.wf.framework) is None:
        return
    others = [p for p in adapters.portability(ctx.wf, installed) if p["adapter"] != ctx.wf.framework]
    if all(p["ok"] for p in others):
        return
    parts = []
    for p in others:
        if p["ok"]:
            parts.append(f"{p['label']}: ready")
        else:
            names = ", ".join(b["name"] for b in p["blockers"][:3]) + ("…" if len(p["blockers"]) > 3 else "")
            parts.append(f"{p['label']}: blocked by {names}")
    ready = sum(p["ok"] for p in others) + 1
    yield info("P003", f"runs on {ready} of {len(installed)} installed frameworks ({'; '.join(parts)})")


def _designed(ctx: ValidationContext, n, target: adapters.AdapterManifest) -> Iterator[Diagnostic]:
    """An Agent block designed for another agent framework (S21)."""
    fw = designs.AGENT_FRAMEWORKS[str(n.engine)]
    if n.type not in designs.DESIGN_TYPES:
        yield error("P001", f"only Agent blocks can be designed for {fw.label}", node_id=n.id)
        return
    if not target.has("agent_designs"):
        yield error("P001", f"{target.label} cannot run agents designed for {fw.label}", node_id=n.id)
        return
    for problem in designs.problems(fw.id, designs.design_for(ctx.wf, n)):
        yield error("P005", problem, node_id=n.id)
    if not designs.installed(fw.id):
        yield warning(
            "P006",
            f"{fw.label} is not installed on this server, so this agent's runs stop with an error",
            node_id=n.id,
            hint=f"Run `make framework FW={fw.id}` on the server to give {fw.label} an environment of its own, or "
            "export the flow to run it elsewhere.",
        )


POLICY_FRAMEWORKS = {"langchain"}  # frameworks that apply node policies (retry, timeout, cache, error route)


def _policy_names(p: object) -> list[str]:
    names = [("retry", "retry"), ("timeout", "timeout"), ("cache", "cache"), ("error_route", "error route")]
    return [label for attr, label in names if getattr(p, attr, None)]


@rule
def framework_applies_node_policies(ctx: ValidationContext) -> Iterator[Diagnostic]:
    """P007 warning: the workflow's framework ignores node policies (2026-10-18 review). They used to be dropped
    without a word on CrewAI, Google ADK, the OpenAI Agents SDK, the flow-engine frameworks and Mastra."""
    wf = ctx.wf
    target = adapters.manifest(wf.framework)
    if target is None or wf.framework in POLICY_FRAMEWORKS:
        return
    if defaults := _policy_names(wf.settings.node_defaults):
        yield warning(
            "P007",
            f"{target.label} does not apply the workflow's default {', '.join(defaults)}: steps run once, with no "
            "time limit, and an error ends the run",
            path="settings.node_defaults",
        )
    for n in wf.nodes:
        if names := _policy_names(n.policies):
            yield warning(
                "P007",
                f"{target.label} does not apply this block's {', '.join(names)}: it runs once, with no time limit, "
                "and an error ends the run",
                node_id=n.id,
                path="policies",
            )
