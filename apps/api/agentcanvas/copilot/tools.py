"""The Copilot's tools (S14): one framework-neutral layer over the IR command API, the registry and the validator.

Read tools look at the workflow and the product; `propose_changes` applies edit commands to a *working copy* only
(the person accepts the proposal on the canvas, which sends the same commands through the normal edit endpoint),
and `set_brief` records what the workflow is for. Tools never raise to the model: a failure comes back as
`{"ok": false, "error": ...}` so the model can repair its own commands.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from agentcanvas import designs
from agentcanvas.copilot import docs, rules
from agentcanvas.copilot.pack import block_summary
from agentcanvas.copilot.runs import RunAccess
from agentcanvas.ir.commands import Command, CommandError, apply_batch, parse_commands
from agentcanvas.ir.models import Workflow
from agentcanvas.registry import Registry
from agentcanvas.registry import adapters as adapter_manifests
from agentcanvas.services.knowledge import used_knowledge_bases
from agentcanvas.services.testcases import Expect
from agentcanvas.services.workflows import without_gallery
from agentcanvas.validation import Diagnostic
from agentcanvas.validation.help import diagnostic_help
from agentcanvas.validation.policies import CODES as POLICY_CODES
from agentcanvas.workspace.store import Workspace, WorkspaceError

MAX_COMMANDS = 80  # per proposal: a whole workflow fits, a runaway loop does not
MAX_RESULT = 12_000  # characters of one tool result sent back to the model


@dataclass
class Proposal:
    summary: list[str] = field(default_factory=list)
    commands: list[dict[str, Any]] = field(default_factory=list)
    tests: list[dict[str, Any]] = field(default_factory=list)  # saved as test cases when accepted (S15)
    notes: list[str] = field(default_factory=list)  # added to the project's Copilot notes when accepted
    files: list[dict[str, Any]] = field(
        default_factory=list
    )  # workspace files {path, content, before}, written when accepted

    def __bool__(self) -> bool:
        return bool(self.commands or self.tests or self.notes or self.files)


KnowledgeLister = Callable[[], Awaitable[list[dict[str, Any]]]]


@dataclass
class ToolContext:
    workflow: Workflow  # the working copy: the saved draft plus this turn's proposal
    registry: Registry
    templates: dict[str, dict[str, Any]]
    validate: Callable[[Workflow], Awaitable[list[Diagnostic]]]
    brief: dict[str, Any] | None = None
    proposal: Proposal = field(default_factory=Proposal)
    runs: RunAccess | None = None  # past runs, test runs, reproductions (S15)
    secret_names: list[str] = field(default_factory=list)  # names only, never values
    events: list[dict[str, Any]] = field(default_factory=list)  # brief/proposal updates for the stream
    allow_real_runs: bool = False  # the person allowed test runs that call real models in this conversation
    sources: list[dict[str, str]] = field(default_factory=list)  # guide sections this turn read: the answer cites them
    workspace: Workspace | None = None  # the project's code: read here, changed only through an accepted proposal
    carried: dict[str, Any] | None = None  # the open proposal this turn started from: its id and what it added
    knowledge: KnowledgeLister | None = None  # the project's knowledge bases: names, counts, model; never contents


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    run: Callable[[ToolContext, dict[str, Any]], Awaitable[dict[str, Any]]]
    writes: bool = False

    def spec(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}


def _diagnostics(diags: list[Diagnostic], *, explained: bool = False) -> list[dict[str, Any]]:
    keep = ("code", "severity", "message", "node_id", "edge_id", "path", "hint")
    out = []
    for d in diags:
        item = {k: getattr(d, k) for k in keep if getattr(d, k, None) not in (None, "")}
        if explained and (h := diagnostic_help(d.code)):
            item["what"], item["fix"] = h["what"], h["fix"]
        out.append(item)
    return out


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
    return {"ok": True, "template": {k: v for k, v in without_gallery(doc).items() if k not in ("id", "ir_version")}}


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


def carry_pending(ctx: ToolContext, proposals: list[dict[str, Any]]) -> None:
    """Start the turn from the latest open proposal: its changes are in the working copy (so tests and reproductions
    see the fix) and in this turn's proposal (so the new one, which replaces it, does not drop them). A proposal whose
    changes no longer apply to the saved draft is left out."""
    pending = next((p for p in reversed(proposals) if p.get("status") == "pending"), None)
    if pending is None:
        return
    commands = list(pending.get("commands") or [])
    if commands:
        try:
            ctx.workflow, _ = apply_batch(ctx.workflow, parse_commands(commands))
        except (CommandError, ValidationError):
            return
    tests, notes = list(pending.get("tests") or []), list(pending.get("notes") or [])
    files = list(pending.get("files") or [])
    ctx.proposal.commands.extend(commands)
    ctx.proposal.tests.extend(tests)
    ctx.proposal.notes.extend(notes)
    ctx.proposal.files.extend(files)
    if summary := str(pending.get("summary") or "").strip():
        ctx.proposal.summary.append(summary)
    ctx.carried = {
        "id": pending.get("id"), "commands": len(commands), "tests": len(tests), "notes": len(notes),
        "files": len(files), "summary": summary,
    }  # fmt: skip


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


# ------------------------------------------------------- S15: debug, test ----
async def get_problems(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    diags = await ctx.validate(ctx.workflow)
    return {
        "ok": True, "problems": _diagnostics(diags, explained=True), "count": len(diags),
        # judgement calls (FR-COP-04): mention them when reviewing; fix them only when the person agrees
        "suggestions": rules.suggestions(ctx.workflow),
    }  # fmt: skip


async def list_runs(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    if ctx.runs is None:
        return {"ok": False, "error": "runs are not available here"}
    return {"ok": True, "runs": await ctx.runs.recent(int(args.get("limit") or 10))}


async def get_run(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    if ctx.runs is None:
        return {"ok": False, "error": "runs are not available here"}
    return await ctx.runs.describe(str(args.get("run_id") or ""))


async def _runnable(ctx: ToolContext) -> str | None:
    # organisation policies are enforced at publish; like the Run panel, they do not stop a try-out
    errors = [d for d in await ctx.validate(ctx.workflow) if d.severity == "error" and d.code not in POLICY_CODES]
    if errors:
        return f"fix the {len(errors)} validation error(s) first: {errors[0].code} {errors[0].message}"
    if ctx.runs is not None and (risks := unaccepted_risks(ctx.runs.workflow, ctx.workflow, ctx.registry)):
        return (
            "a test run only runs what the person has accepted for "
            + ", ".join(risks[:3])
            + ": propose the change, and test it after it is accepted"
        )
    return None


def _flat(wf: Workflow) -> tuple[list[Any], list[Any]]:
    """Every node and resource, subgraphs included."""
    nodes, resources = list(wf.nodes), list(wf.resources)
    for n in wf.nodes:
        if n.type == "core.subgraph":
            with contextlib.suppress(Exception):  # an invalid subgraph never runs
                inner_nodes, inner_resources = _flat(wf.subworkflow(n))
                nodes += inner_nodes
                resources += inner_resources
    return nodes, resources


ENDPOINT_KEYS = ("base_url", "url", "headers", "endpoint", "azure_endpoint", "command", "args")


def unaccepted_risks(saved: Workflow, working: Workflow, registry: Any) -> list[str]:
    """What the working copy would run that the person has not accepted and a dry run does not stub: new or changed
    code (script steps, Python tools, custom code steps, local MCP server commands), and secrets sent to a new or
    changed address."""
    old_nodes = {n.id: n for n in _flat(saved)[0]}
    old_resources = {r.name: r for r in _flat(saved)[1]}
    out: list[str] = []
    nodes, resources = _flat(working)
    for n in nodes:
        m = registry.get(n.type) if registry else None
        runs_code = n.type == "core.script" or (m is not None and "code_exec" in (m.side_effects or []))
        old = old_nodes.get(n.id)
        if runs_code and (old is None or old.type != n.type or old.config != n.config):
            out.append(f"the new or changed code step '{n.name}'")
    for r in resources:
        old = old_resources.get(r.name)
        if old is not None and old.type == r.type and old.config == r.config:
            continue
        if r.type == "tool.python":
            out.append(f"the new or changed Python tool '{r.name}'")
        elif r.type == "mcp.server" and r.config.get("transport") == "stdio":
            out.append(f"the new or changed local app connector '{r.name}', which starts a program")
        elif secret_refs(r.config) and _endpoint(r.config) != (_endpoint(old.config) if old else _endpoint({})):
            out.append(f"'{r.name}', which sends a secret to a new address")
    return out


def secret_refs(value: Any) -> set[str]:
    """The secret names a config refers to ({"$secret": NAME}), at any depth."""
    if isinstance(value, dict):
        if isinstance(value.get("$secret"), str):
            return {value["$secret"]}
        return set().union(*(secret_refs(v) for v in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(secret_refs(v) for v in value)) if value else set()
    return set()


def _endpoint(config: dict[str, Any]) -> list[Any]:
    return [config.get(k) for k in ENDPOINT_KEYS]


async def run_test(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    if ctx.runs is None:
        return {"ok": False, "error": "test runs are not available here"}
    if problem := await _runnable(ctx):
        return {"ok": False, "error": problem}
    if not isinstance(args.get("input"), dict):
        return {"ok": False, "error": 'input must be an object of channel values, e.g. {"question": "..."}'}
    expect = args.get("expect") if isinstance(args.get("expect"), dict) else None
    given = args.get("mocks")
    mocks: dict[str, Any] = given if isinstance(given, dict) else {}
    if not ctx.allow_real_runs and (real := real_models(ctx.workflow, mocks)):
        # a run that calls real models costs money and time: the person allows it once per conversation
        ctx.events.append({"type": "permission", "kind": "real_runs", "models": real})
        return {
            "ok": False,
            "error": "the person has not allowed test runs that call real models ("
            + ", ".join(real)
            + "); ask them to allow it in the panel, or pass `mocks` with scripted replies "
            "for these models to test the workflow's logic",
        }
    return await ctx.runs.test(ctx.workflow, args["input"], mocks=mocks or None, expect=expect)


def real_models(wf: Workflow, mocks: dict[str, Any]) -> list[str]:
    """The workflow's chat models a test run would call for real (those without scripted replies in `mocks`)."""
    return sorted(
        str(r.config.get("model") or r.name) for r in wf.resources if r.type == "model.chat" and r.name not in mocks
    )


async def reproduce_run(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    if ctx.runs is None:
        return {"ok": False, "error": "runs are not available here"}
    if problem := await _runnable(ctx):
        return {"ok": False, "error": problem}
    return await ctx.runs.reproduce(ctx.workflow, str(args.get("run_id") or ""))


async def propose_tests(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    raw = args.get("tests")
    if not isinstance(raw, list) or not raw:
        return {"ok": False, "error": "send a non-empty list of tests"}
    tests = []
    for i, t in enumerate(raw[:10]):
        if not isinstance(t, dict) or not isinstance(t.get("input"), dict) or not t.get("name"):
            return {"ok": False, "error": f"test #{i} needs a name and an input object"}
        try:
            expect = Expect.model_validate(t.get("expect") or {}).model_dump(mode="json", exclude_none=True)
        except ValidationError as exc:
            return {"ok": False, "error": f"test #{i} expect: {exc.errors()[0]['msg']}"}
        tests.append({"name": str(t["name"])[:200], "input": t["input"], "expect": expect,
                      "mocks": t.get("mocks") if isinstance(t.get("mocks"), dict) else {}})  # fmt: skip
    ctx.proposal.tests.extend(tests)
    if summary := str(args.get("summary") or "").strip():
        ctx.proposal.summary.append(summary)
    return {"ok": True, "tests": len(tests), "note": "saved as test cases when the person accepts"}


async def search_guide(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    found = docs.search(str(args.get("query") or ""))
    for hit in found[:2]:  # the best matches, cited under the answer (the guide page and section)
        source = {"page": hit["page"].removesuffix(".md"), "section": hit["section"]}
        if source not in ctx.sources and len(ctx.sources) < 4:
            ctx.sources.append(source)
    return {"ok": True, "results": found} if found else {"ok": True, "results": [], "note": "nothing found"}


async def list_models(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    models = [{"id": m.id, "label": m.label, "tools": m.tools} for m in ctx.registry.models()]
    providers = [
        {"id": p.id, "label": p.label, "key_secret": p.key_env, "local": p.local} for p in ctx.registry.providers()
    ]
    return {"ok": True, "models": models, "providers": providers, "secrets_set": ctx.secret_names}


# ------------------------------------------- the project: secrets, knowledge, export ----
async def list_secrets(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """The project's secret names, the ones the workflow refers to, and the API key each model needs. Values never
    reach the Copilot: they are not in its context to begin with."""
    used = sorted(secret_refs(ctx.workflow.model_dump(mode="json")))
    keys = []
    for r in _flat(ctx.workflow)[1]:
        if r.type != "model.chat" or secret_refs(r.config.get("api_key")):
            continue  # a key named in the model's settings is one of `used`
        model = str(r.config.get("model") or "")
        p = ctx.registry.provider(model.partition(":")[0])
        if p is None or not p.key_env:
            continue
        where = "project secret" if p.key_env in ctx.secret_names else "server" if os.environ.get(p.key_env) else None
        keys.append({"model": model, "secret": p.key_env, "set": where})
    return {
        "ok": True, "secrets": ctx.secret_names, "used_by_workflow": used,
        "missing": [n for n in used if n not in ctx.secret_names], "model_keys": keys,
        "note": "names only; the person adds or changes a secret in Settings > Secrets, never in a block's settings",
    }  # fmt: skip


async def list_knowledge_bases(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    if ctx.knowledge is None:
        return {"ok": False, "error": "knowledge bases are not available here"}
    bases = await ctx.knowledge()
    used = sorted(used_knowledge_bases(ctx.workflow.model_dump(mode="json")))
    names = {b["name"] for b in bases}
    return {
        "ok": True, "knowledge_bases": bases, "used_by_workflow": used, "missing": [n for n in used if n not in names],
        "note": "a stale base searches with old settings until it is reindexed; the person adds documents and "
        "reindexes on the Knowledge page",
    }  # fmt: skip


async def export_plan(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """What exporting the workflow (as it would be with your proposal) gives and what it lacks, from the same code as
    the Export box: the framework's portability, designed agents' export problems, the deploy kit's prerequisites and
    the knowledge bases the export ships."""
    from agentcanvas.adapters.langchain import deploykit

    wf = ctx.workflow
    framework = str(args.get("framework") or wf.framework)
    target = str(args.get("target") or "local")
    m = adapter_manifests.manifest(framework)
    if m is None:
        known = ", ".join(a.id for a in adapter_manifests.installed())
        return {"ok": False, "error": f"no installed framework '{framework}'; one of {known}"}
    if target not in deploykit.TARGETS:
        return {"ok": False, "error": f"no deploy target '{target}'; one of {', '.join(deploykit.TARGETS)}"}
    if framework != wf.framework:  # the same workflow written for another framework, as the Export box does
        wf = wf.model_copy(update={"framework": framework})
    fit = adapter_manifests.portability(wf, [m])[0]
    gaps = [f"{b['name']} ({b['type']}): {m.label} cannot run it" for b in fit["blockers"]]
    gaps += [f"{b['name']} ({b['type']}) is emulated: {b['note'] or 'it behaves differently'}" for b in fit["emulated"]]
    for n in _flat(wf)[0]:
        if n.type in designs.DESIGN_TYPES and str(n.engine) in designs.AGENT_FRAMEWORKS:
            gaps += [f"{n.name}: {p}" for p in designs.export_problems(str(n.engine), designs.design_for(wf, n))]
    errors = [d for d in await ctx.validate(wf) if d.severity == "error"]
    out: dict[str, Any] = {
        "ok": True, "framework": {"id": m.id, "label": m.label, "what": m.description}, "gaps": gaps,
        "errors": _diagnostics(errors)[:10],
    }  # fmt: skip
    if framework == "langchain":  # a LangGraph service with a deploy kit for the target (S16)
        facts = deploykit.facts(wf, "workflow", "0.0.0")
        out["targets"] = [{"id": t.id, "label": t.label} for t in deploykit.TARGETS.values()]
        out["deploy"] = deploykit.prerequisites(facts, deploykit.TARGETS[target])
    else:
        out["deploy"] = f"a plain {m.label} project; the deploy kit (Docker, Kubernetes, clouds) is for LangGraph only"
    if used := sorted(used_knowledge_bases(wf.model_dump(mode="json"))):
        have = {b["name"] for b in await ctx.knowledge()} if ctx.knowledge else set()
        out["knowledge"] = [{"name": n, "shipped": n in have} for n in used]  # one the project lacks is left out
    out["note"] = "the person exports from the Export box; nothing is exported or published here"
    return out


MAX_READ = 20_000  # characters of a workspace file given to the model


def _proposed(ctx: ToolContext, path: str) -> dict[str, Any] | None:
    return next((f for f in ctx.proposal.files if f["path"] == path), None)


async def list_workspace_files(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    if ctx.workspace is None:
        return {"ok": False, "error": "this project has no workspace"}
    prefix = str(args.get("prefix") or "").lstrip("/")
    on_disk = [f.path for f in await asyncio.to_thread(ctx.workspace.list) if f.path.startswith(prefix)]
    proposed = [f["path"] for f in ctx.proposal.files if f["path"].startswith(prefix) and f["path"] not in on_disk]
    return {"ok": True, "files": sorted(on_disk + proposed)[:300], "proposed": [f["path"] for f in ctx.proposal.files]}


async def read_workspace_file(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    if ctx.workspace is None:
        return {"ok": False, "error": "this project has no workspace"}
    path = str(args.get("path") or "").lstrip("/")
    if (mine := _proposed(ctx, path)) is not None:
        return {"ok": True, "path": path, "content": mine["content"][:MAX_READ], "note": "as you proposed it"}
    try:
        text = await asyncio.to_thread(ctx.workspace.read, path)
    except FileNotFoundError:
        return {"ok": False, "error": f"no file '{path}'; list_workspace_files shows what there is"}
    except WorkspaceError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "path": path, "content": text[:MAX_READ], "truncated": len(text) > MAX_READ}


async def propose_file(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """A new or changed workspace file, part of the proposal: written (as one commit, credited to the person who
    accepts) only when the person accepts it."""
    if ctx.workspace is None:
        return {"ok": False, "error": "this project has no workspace"}
    path, content = str(args.get("path") or "").strip().lstrip("/"), args.get("content")
    if not isinstance(content, str):
        return {"ok": False, "error": "content must be the whole file, as text"}
    try:
        ctx.workspace.check(path, content)
        before = await asyncio.to_thread(ctx.workspace.read, path)
    except FileNotFoundError:
        before = None
    except WorkspaceError as exc:
        return {"ok": False, "error": str(exc)}
    problems = await asyncio.to_thread(ctx.workspace.lint, path, content)
    ctx.proposal.files = [f for f in ctx.proposal.files if f["path"] != path]  # the latest version of a file wins
    ctx.proposal.files.append({"path": path, "content": content, "before": before})
    if summary := " ".join(str(args.get("summary") or "").split()):
        ctx.proposal.summary.append(summary[:200])
    found = [f"line {d['line']}: {d['code']} {d['message']}" for d in problems[:20]]
    note = "fix these problems with another propose_file" if found else "written when the person accepts"
    return {"ok": True, "path": path, "new": before is None, "problems": found, "note": note}


async def save_note(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    note = " ".join(str(args.get("note") or "").split())
    if not note:
        return {"ok": False, "error": "the note is empty"}
    ctx.proposal.notes.append(note[:500])
    return {"ok": True, "note": "added to the project's Copilot notes when the person accepts"}


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
        Tool(
            "get_problems",
            "The workflow's validation problems, each with a plain explanation and how to fix it, and best-practice "
            "suggestions (a router without a default, a model without a backup, thin instructions).",
            {"type": "object", "properties": {}},
            get_problems,
        ),
        Tool(
            "list_runs",
            "Recent runs of this workflow: id, status and error.",
            {"type": "object", "properties": {"limit": {"type": "integer"}}},
            list_runs,
        ),
        Tool(
            "get_run",
            "What happened in one run: status, error with an explanation, input, output and each step.",
            {"type": "object", "properties": {"run_id": {"type": "string"}}, "required": ["run_id"]},
            get_run,
        ),
        Tool(
            "search_guide",
            "Search the AgentCanvas user guide.",
            {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
            search_guide,
        ),
        Tool(
            "list_models",
            "The AI models and providers this server knows, and which project secrets are set (names only).",
            {"type": "object", "properties": {}},
            list_models,
        ),
        Tool(
            "list_secrets",
            "The project's secret names (never values), which ones the workflow refers to and which of those are "
            "missing, and the API key each model needs and whether it is set.",
            {"type": "object", "properties": {}},
            list_secrets,
        ),
        Tool(
            "list_knowledge_bases",
            "The project's knowledge bases: name, description, documents, embedding model, and whether it is stale "
            "(needs reindexing); and which ones the workflow searches.",
            {"type": "object", "properties": {}},
            list_knowledge_bases,
        ),
        Tool(
            "export_plan",
            "What exporting this workflow would give and lack: for `framework` (default the workflow's), the blocks "
            "that framework cannot run or only emulates and the validation errors; for LangGraph, what the deploy "
            "kit for `target` (local, server, kubernetes, cloudrun, aws, azure) needs: keys, hosts, database, tools.",
            {"type": "object", "properties": {"framework": {"type": "string"}, "target": {"type": "string"}}},
            export_plan,
        ),
        Tool(
            "run_test",
            "Run the workflow as it would be with your proposal, once, as a dry run (risky tools are stubbed). "
            "`mocks` maps a model resource name to scripted replies to avoid model calls; `expect` is "
            '{"status", "channels": {name: value}, "route": [node ids]}.',
            {
                "type": "object",
                "properties": {
                    "input": {"type": "object"},
                    "mocks": {"type": "object"},
                    "expect": {"type": "object"},
                },
                "required": ["input"],
            },
            run_test,
            writes=True,
        ),
        Tool(
            "reproduce_run",
            "Rerun a past run's input on the workflow as it would be with your proposal, replaying the run's "
            "recorded model replies and tool outputs when it has them. Use it to check that a fix works.",
            {"type": "object", "properties": {"run_id": {"type": "string"}}, "required": ["run_id"]},
            reproduce_run,
            writes=True,
        ),
        Tool(
            "propose_tests",
            "Propose saved test cases (from the brief's success criteria): each {name, input, expect, mocks?}.",
            {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "tests": {"type": "array", "items": {"type": "object"}},
                },
                "required": ["tests"],
            },
            propose_tests,
            writes=True,
        ),
        Tool(
            "list_workspace_files",
            "List the project's workspace files (scripts/, tools/, nodes/, events/, flows/<workflow>/), and those you "
            "proposed this turn. Optional `prefix`, e.g. 'scripts/'.",
            {"type": "object", "properties": {"prefix": {"type": "string"}}},
            list_workspace_files,
        ),
        Tool(
            "read_workspace_file",
            "Read one workspace file (or your proposed version of it) before changing it.",
            {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            read_workspace_file,
        ),
        Tool(
            "propose_file",
            "Propose a new or changed workspace file (the whole content): a script step's `scripts/x.py` with "
            "`def run(state: dict) -> dict`, a `@tool` in `tools/`, a custom block in `nodes/`, an event handler in "
            "`events/`. It is checked with ruff; fix what it reports. Written only when the person accepts.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "summary": {"type": "string", "description": "what the file does, in a few words"},
                },
                "required": ["path", "content"],
            },
            propose_file,
            writes=True,
        ),
        Tool(
            "save_note",
            "Propose a short note for the project's Copilot notes (a convention or preference to remember).",
            {"type": "object", "properties": {"note": {"type": "string"}}, "required": ["note"]},
            save_note,
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
