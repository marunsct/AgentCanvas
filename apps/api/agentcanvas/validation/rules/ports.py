"""Port rules: required inputs, type compatibility, cardinality."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator

from agentcanvas.validation.engine import Diagnostic, ValidationContext, error, rule


@rule
def required_ports(ctx: ValidationContext) -> Iterator[Diagnostic]:
    for n in [*ctx.wf.nodes, *ctx.wf.resources]:
        m = ctx.manifest(n)
        if m is None:
            continue
        for port in m.inputs:
            if port.required and not ctx.wf.wiring_into(n.id, port.name):
                yield error(
                    "E001",
                    f"'{n.name}' needs a {port.type} connected to its '{port.name}' port",
                    node_id=n.id,
                    path=f"ports.{port.name}",
                )


@rule
def wiring_types(ctx: ValidationContext) -> Iterator[Diagnostic]:
    wf = ctx.wf
    per_port: Counter[tuple[str, str]] = Counter()
    for e in wf.edges:
        if e.kind != "wiring":
            continue
        src, dst = wf.resource(e.source.node), wf.element(e.target.node)
        if src is None or dst is None:
            continue  # reported by E003/E006
        sm, dm = ctx.manifest(src), ctx.manifest(dst)
        if sm is None or dm is None:
            continue  # reported by E005
        out_port = sm.output(e.source.port) if e.source.port else (sm.outputs[0] if len(sm.outputs) == 1 else None)
        in_port = dm.input(e.target.port or "")
        if out_port is None:
            yield error("E002", f"edge '{e.id}': '{src.name}' has no output port '{e.source.port}'", edge_id=e.id)
            continue
        if in_port is None:
            yield error(
                "E002", f"edge '{e.id}': '{dst.name}' has no input port '{e.target.port}'", edge_id=e.id, node_id=dst.id
            )
            continue
        if out_port.type != in_port.type:
            yield error(
                "E002",
                f"'{src.name}' provides {out_port.type} but '{dst.name}.{in_port.name}' expects {in_port.type}",
                edge_id=e.id,
                node_id=dst.id,
            )
        per_port[(dst.id, in_port.name)] += 1
        if per_port[(dst.id, in_port.name)] > 1 and not in_port.multi:
            yield error(
                "E002", f"'{dst.name}.{in_port.name}' accepts a single connection", edge_id=e.id, node_id=dst.id
            )
