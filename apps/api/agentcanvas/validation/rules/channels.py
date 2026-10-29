"""State-channel rules: unknown channels, unused channels, settings references."""

from __future__ import annotations

from collections.abc import Iterator

from agentcanvas.validation.analysis import iter_formatted
from agentcanvas.validation.engine import Diagnostic, ValidationContext, error, rule, warning


@rule
def channels_exist(ctx: ValidationContext) -> Iterator[Diagnostic]:
    declared = {c.name for c in ctx.wf.state.channels}
    for n in ctx.wf.nodes:
        m = ctx.manifest(n)
        if m is None:
            continue
        for ch in sorted(ctx.writes.get(n.id, set()) - declared):
            yield error(
                "E021",
                f"'{n.name}' writes unknown state channel '{ch}'",
                node_id=n.id,
                hint="add the channel in the state designer",
            )
        for ch in sorted(ctx.reads.get(n.id, set()) - declared):
            yield error("E023", f"'{n.name}' reads unknown state channel '{ch}'", node_id=n.id)
        optional = set(m.config_schema.get("properties", {})) - set(m.config_schema.get("required", []))
        for path, fmt, value in iter_formatted(m.config_schema, n.config):
            if value == "" and path.removeprefix("config.") in optional:
                continue  # an optional field cleared in the inspector: nothing is written
            if fmt == "channel" and value not in declared:
                yield error("E021", f"'{n.name}' refers to unknown state channel '{value}'", node_id=n.id, path=path)
    s = ctx.wf.settings
    for key, chans in (("input_channels", s.input_channels), ("output_channels", s.output_channels)):
        for ch in sorted(set(chans or []) - declared):
            yield error("E021", f"settings.{key} lists unknown channel '{ch}'", path=f"settings.{key}")


@rule
def unused_private_channels(ctx: ValidationContext) -> Iterator[Diagnostic]:
    written = set().union(*ctx.writes.values()) if ctx.writes else set()
    read = set().union(*ctx.reads.values()) if ctx.reads else set()
    for c in ctx.wf.state.channels:
        if c.io == "private" and c.name in written and c.name not in read:
            yield warning("W003", f"private channel '{c.name}' is written but never read")
