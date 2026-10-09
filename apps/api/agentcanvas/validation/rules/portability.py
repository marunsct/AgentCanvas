"""Portability rules (S20, spec doc 12 §C.6 and doc 03): which framework can run each block.

The workflow's `framework` (and a block's optional `engine`, IR 1.2) must be installed adapters, and every block must
be one that framework can run, natively or by emulation, as its manifest (`registry/adapters/*.json`) says.

* P001 error — the framework is not installed, or cannot run this block (or the block cannot have an engine).
* P002 warning — the framework only emulates this block.
* P003 info — with more than one framework installed: which others this workflow could move to, and what blocks it.
* P004 info — a block is designed for another framework than the workflow's; it runs on the workflow's framework
  until mixed-framework flows arrive.
"""

from __future__ import annotations

from collections.abc import Iterator

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
