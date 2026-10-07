"""The Copilot's tools (S14): one framework-neutral layer over the IR command API, the registry and the validator.

Read tools look at the workflow and the product; `propose_changes` applies edit commands to a *working copy* only
(the person accepts the proposal on the canvas, which sends the same commands through the normal edit endpoint),
and `set_brief` records what the workflow is for. Tools never raise to the model: a failure comes back as
`{"ok": false, "error": ...}` so the model can repair its own commands.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from agentcanvas.copilot.pack import block_summary
from agentcanvas.ir.commands import Command, CommandError, apply_batch, parse_commands
from agentcanvas.ir.models import Workflow
from agentcanvas.registry import Registry
from agentcanvas.validation import Diagnostic

MAX_COMMANDS = 80  # per proposal: a whole workflow fits, a runaway loop does not
MAX_RESULT = 12_000  # characters of one tool result sent back to the model


@dataclass
class Proposal:
    summary: list[str] = field(default_factory=list)
    commands: list[dict[str, Any]] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.commands)


@dataclass
class ToolContext:
    workflow: Workflow  # the working copy: the saved draft plus this turn's proposal
    registry: Registry
    templates: dict[str, dict[str, Any]]
    validate: Callable[[Workflow], Awaitable[list[Diagnostic]]]
    brief: dict[str, Any] | None = None
    proposal: Proposal = field(default_factory=Proposal)
    events: list[dict[str, Any]] = field(default_factory=list)  # brief/proposal updates for the stream


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    run: Callable[[ToolContext, dict[str, Any]], Awaitable[dict[str, Any]]]
    writes: bool = False

    def spec(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}


def _diagnostics(diags: list[Diagnostic]) -> list[dict[str, Any]]:
    keep = ("code", "severity", "message", "node_id", "edge_id", "path", "hint")
    return [{k: getattr(d, k) for k in keep if getattr(d, k, None) not in (None, "")} for d in diags]


def compact_workflow(wf: Workflow) -> dict[str, Any]:
    doc = wf.model_dump(mode="json", exclude_defaults=True)
    doc.pop("ir_version", None)
    return doc


# ------------------------------------------------------------------- read ----
async def get_workflow(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    diags = await ctx.validate(ctx.workflow)
    return {"ok": True, "workflow": compact_workflow(ctx.workflow), "diagnostics": _diagnostics(diags)}


async def find_blocks(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    words = [w for w in str(args.get("query") or "").lower().split() if w]
    found = []
    for m in ctx.registry.all():
        text = f"{m.type} {m.label} {m.category} {m.description}".lower()
        if all(w in text for w in words):
            found.append({"type": m.type, "label": m.label, "kind": m.kind, "description": m.description[:200]})
    return {"ok": True, "blocks": found[:20], "more": max(0, len(found) - 20)}


async def get_block(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    m = ctx.registry.get(str(args.get("type") or ""))
    if m is None:
        return {"ok": False, "error": f"no block type '{args.get('type')}'; use find_blocks"}
    return {"ok": True, "summary": block_summary(m, full=True), "config_schema": m.config_schema}


async def get_template(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    name = str(args.get("name") or "")
    doc = ctx.templates.get(name)
    if doc is None:
        return {"ok": False, "error": f"no template '{name}'; known: {', '.join(ctx.templates)}"}
    return {"ok": True, "template": {k: v for k, v in doc.items() if k not in ("id", "ir_version")}}


# ------------------------------------------------------------------ write ----
def _literal_secrets(commands: list[Command]) -> list[str]:
    """Config keys that hold a literal secret instead of a {"$secret": NAME} reference."""
    risky = ("api_key", "token", "password", "secret", "authorization")
    found: list[str] = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            if "$secret" in value:
                return
            for k, v in value.items():
                if isinstance(v, str) and v and any(r in k.lower() for r in risky) and "{{" not in v:
                    found.append(f"{path}.{k}")
                walk(v, f"{path}.{k}")
        elif isinstance(value, list):
            for i, v in enumerate(value):
                walk(v, f"{path}[{i}]")

    for cmd in commands:
        walk(cmd.model_dump(mode="json"), cmd.op)
    return found


async def propose_changes(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    raw = args.get("commands")
    if not isinstance(raw, list) or not raw:
        return {"ok": False, "error": "send a non-empty list of commands"}
    if len(ctx.proposal.commands) + len(raw) > MAX_COMMANDS:
        return {"ok": False, "error": f"a proposal holds at most {MAX_COMMANDS} commands; propose less at once"}
    try:
        commands = parse_commands(raw)
    except ValidationError as exc:
        errors = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:8])
        return {"ok": False, "error": f"commands are not valid: {errors}"}
    if leaked := _literal_secrets(commands):
        return {
            "ok": False,
            "error": f'never put secrets in config ({", ".join(leaked)}): use {{"$secret": "NAME"}} references',
        }
    try:
        new, _ = apply_batch(ctx.workflow, commands)
    except CommandError as exc:
        return {"ok": False, "error": str(exc)}
    ctx.workflow = new
    ctx.proposal.commands.extend(c.model_dump(mode="json", exclude_none=True) for c in commands)
    if summary := str(args.get("summary") or "").strip():
        ctx.proposal.summary.append(summary)
    diags = await ctx.validate(new)
    errors = [d for d in diags if d.severity == "error"]
    return {
        "ok": True,
        "applied": len(commands),
        "errors": _diagnostics(errors),
        "warnings": _diagnostics([d for d in diags if d.severity != "error"])[:15],
        "note": "fix the errors with more commands" if errors else "the workflow has no errors",
    }


async def set_brief(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    keys = ("goal", "inputs", "outputs", "steps", "tools", "success_criteria", "open_questions")
    brief = {k: args[k] for k in keys if args.get(k) not in (None, "", [])}
    if not brief.get("goal"):
        return {"ok": False, "error": "a brief needs a goal"}
    ctx.brief = brief
    ctx.events.append({"type": "brief", "brief": brief})
    return {"ok": True}


_STR_LIST = {"type": "array", "items": {"type": "string"}}
TOOLS: dict[str, Tool] = {
    t.name: t
    for t in (
        Tool(
            "get_workflow",
            "The current workflow (with your pending changes) as IR, and its validation diagnostics.",
            {"type": "object", "properties": {}},
            get_workflow,
        ),
        Tool(
            "find_blocks",
            "Search the block catalog (steps, models, tools, middleware) by words.",
            {"type": "object", "properties": {"query": {"type": "string"}}},
            find_blocks,
        ),
        Tool(
            "get_block",
            "A block type's ports, full config schema and docs.",
            {"type": "object", "properties": {"type": {"type": "string"}}, "required": ["type"]},
            get_block,
        ),
        Tool(
            "get_template",
            "A starter template's full IR, to copy its structure.",
            {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
            get_template,
        ),
        Tool(
            "set_brief",
            "Record the workflow brief: what it is for, its inputs and outputs, the steps, tools and how to tell it "
            "works. Use it before building something new, then ask the person to confirm.",
            {
                "type": "object",
                "properties": {
                    "goal": {"type": "string"},
                    "inputs": _STR_LIST,
                    "outputs": _STR_LIST,
                    "steps": _STR_LIST,
                    "tools": _STR_LIST,
                    "success_criteria": _STR_LIST,
                    "open_questions": _STR_LIST,
                },
                "required": ["goal"],
            },
            set_brief,
            writes=True,
        ),
        Tool(
            "propose_changes",
            "Apply edit commands to the workflow as a proposal the person reviews. Returns validation errors to fix.",
            {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "one line saying what these commands do"},
                    "commands": {"type": "array", "items": {"type": "object"}},
                },
                "required": ["summary", "commands"],
            },
            propose_changes,
            writes=True,
        ),
    )
}
READ_ONLY = [name for name, t in TOOLS.items() if not t.writes]


async def run_tool(ctx: ToolContext, name: str, args: dict[str, Any], allowed: list[str]) -> dict[str, Any]:
    tool = TOOLS.get(name)
    if tool is None or name not in allowed:
        return {"ok": False, "error": f"tool '{name}' is not available here"}
    try:
        return await tool.run(ctx, args if isinstance(args, dict) else {})
    except Exception as exc:  # noqa: BLE001 - a tool bug must not end the conversation; the model sees it
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def result_text(result: dict[str, Any]) -> str:
    text = json.dumps(result, separators=(",", ":"), default=str)
    return text if len(text) <= MAX_RESULT else text[: MAX_RESULT - 20] + "…(truncated)"
