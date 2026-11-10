"""Security rules (spec doc 03 §4, review v0.6 §3.4)."""

from __future__ import annotations

from collections.abc import Iterator

from agentcanvas.security.imports import is_allowed_import_path
from agentcanvas.validation.analysis import node_side_effects
from agentcanvas.validation.engine import Diagnostic, ValidationContext, error, info, rule, warning

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
def host_code(ctx: ValidationContext) -> Iterator[Diagnostic]:
    """E041: a block that imports workspace code into the server's own process, where the database password, the
    keys and every organisation's secrets are in reach. Only a server that runs workspace code in-process anyway
    (the local edition, or an operator who trusts every builder) allows it."""
    if ctx.edition != "hosted" and not ctx.isolated:
        return
    for el in [*ctx.wf.nodes, *ctx.wf.resources]:
        m = ctx.manifest(el)
        if not (m and m.hosted_forbidden):
            continue
        if ctx.edition == "hosted":
            message = f"'{el.name}' ({m.type}) executes on the host and is only allowed in the local edition"
        else:
            message = (
                f"'{el.name}' ({m.type}) would run workspace code inside the server, and this server runs workspace "
                "code in a sandbox"
            )
        yield error(
            "E041",
            message,
            node_id=el.id,
            hint="design the agent on an AI agent block for its framework, or call your code from a Script step",
        )


@rule
def hosted_edition(ctx: ValidationContext) -> Iterator[Diagnostic]:
    if ctx.edition != "hosted":
        return
    for el in [*ctx.wf.nodes, *ctx.wf.resources]:
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


def _sets_anything(policy: dict) -> bool:
    from agentcanvas.services.approvers import Approvers

    a = Approvers.of(policy)
    return a.named or a.caller or a.not_initiator or bool(a.escalate_to)


@rule
def approvers(ctx: ValidationContext) -> Iterator[Diagnostic]:
    """Who may decide a pause (E-6): a setting that contradicts itself (E037), names who approves tool calls of an
    agent where no tool needs approval (W027), or that an export of the workflow does not check (I011)."""
    for n in ctx.wf.nodes:
        policy = n.config.get("approvers")
        if not isinstance(policy, dict) or not _sets_anything(policy):  # the inspector saves an empty form too
            continue
        if policy.get("caller") and policy.get("not_initiator"):
            yield error(
                "E037",
                f"'{n.name}': whoever calls the published workflow started its run, so it cannot both answer and be "
                "kept from deciding as the person who started the run",
                node_id=n.id,
                path="config.approvers",
                hint="untick one of the two",
            )
        m = ctx.manifest(n)
        agent = m is not None and "approve_tools" in m.config_schema.get("properties", {})
        if agent and not n.config.get("approve_tools"):
            yield warning(
                "W027",
                f"'{n.name}' names who may approve its tool calls, but none of its tools needs approval",
                node_id=n.id,
                path="config.approvers",
                hint="add tools to the tools that need approval, or clear who may approve",
            )
        if any(policy.get(k) for k in ("roles", "groups", "people", "not_initiator")):
            yield info(
                "I011",
                f"'{n.name}': AgentCanvas checks who may decide; exported code does not",
                node_id=n.id,
                path="config.approvers",
            )
