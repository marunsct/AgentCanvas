"""Security rules (spec doc 03 §4, review v0.6 §3.4)."""

from __future__ import annotations

from collections.abc import Iterator

from agentcanvas.security.imports import is_allowed_import_path
from agentcanvas.validation.analysis import node_side_effects
from agentcanvas.validation.engine import Diagnostic, ValidationContext, error, rule

WRITE_EFFECTS = {"external_write", "message_send"}
# pause before they act (approval first), or remember what they sent so a resumed step does not send it again
REPLAY_SAFE = {"core.agent", "agent.framework", "agent.a2a", "agent.mcp"}


@rule
def import_paths(ctx: ValidationContext) -> Iterator[Diagnostic]:
    for el in [*ctx.wf.nodes, *ctx.wf.resources]:
        path = el.config.get("import_path")
        if isinstance(path, str) and path and not is_allowed_import_path(path):
            yield error(
                "E080",
                f"'{el.name}': import path '{path}' must point into the workspace "
                "(scripts.|tools.|nodes.<module>:<name>, or flows.<workflow>.scripts.<module>:<name>)",
                node_id=el.id,
                path="config.import_path",
            )


@rule
def hosted_edition(ctx: ValidationContext) -> Iterator[Diagnostic]:
    if ctx.edition != "hosted":
        return
    for el in [*ctx.wf.nodes, *ctx.wf.resources]:
        m = ctx.manifest(el)
        if m and m.hosted_forbidden:
            yield error(
                "E041",
                f"'{el.name}' ({m.type}) executes on the host and is only allowed in the local edition",
                node_id=el.id,
            )
        if el.config.get("allow_private_network"):
            yield error(
                "E043",
                f"'{el.name}': private and internal network addresses are not reachable in the hosted edition",
                node_id=el.id,
                path="config.allow_private_network",
            )


@rule
def local_mcp(ctx: ValidationContext) -> Iterator[Diagnostic]:
    for r in ctx.wf.resources:
        if r.type != "mcp.server":
            continue
        target = str(r.config.get("url") or r.config.get("command") or "")
        local = r.config.get("transport") == "stdio" or not target.startswith(("http://", "https://"))
        if local and not ctx.allow_local_mcp:
            yield error(
                "E042",
                f"'{r.name}' launches a local MCP server process; an admin must enable local MCP and "
                "sandboxed execution",
                node_id=r.id,
            )


@rule
def side_effects_before_interrupt(ctx: ValidationContext) -> Iterator[Diagnostic]:
    """E070: a node that may pause must not also write externally — code before interrupt() re-runs on resume."""
    for n in ctx.wf.nodes:
        m = ctx.manifest(n)
        if m and m.interrupts and n.type not in REPLAY_SAFE and node_side_effects(n, m) & WRITE_EFFECTS:
            yield error(
                "E070",
                f"'{n.name}' can pause for a human and also writes externally; the write would repeat on resume",
                node_id=n.id,
                hint="move the write into a separate node after the approval",
            )
