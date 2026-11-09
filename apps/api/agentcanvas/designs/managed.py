"""Claude Managed Agents (G-12): an Agent block as a bundle of files that the `ant` CLI applies (`ant apply`).

Managed Agents is Anthropic's hosted agent platform, not a Python framework, so it sits next to `AGENT_FRAMEWORKS`
rather than in it: no module is generated and nothing runs it inside AgentCanvas. A block designed for it runs on
the canvas as the workflow's own agent; the export writes it for the platform:

    agents/<agent>/agent.md          YAML frontmatter = the agent (model, tools, MCP servers, skills), body = system
    agents/<agent>/environment.yaml  the cloud environment its sessions run in
    agents/<agent>/vault.yaml        only when an MCP server needs a credential (the container, never a secret)
    agents/<agent>/skills/<name>/    the workspace's skills (folders with a SKILL.md)
    run.py, custom_tools.py          a session runner: it answers the custom tools and the calls that need approval
    doctor.py, Makefile, tests/      `make doctor`, `make plan` / `make apply`, offline tests

Workspace Python tools and web API tools become custom tools (the platform sends each call to `run.py`, which runs
the same code as the canvas); MCP servers become `mcp_servers` with an `mcp_toolset` each; the approval list becomes
permission policies (`always_ask`) for MCP tools and a terminal question in `run.py` for custom tools, which the
platform's policies do not cover. File shapes follow the `ant apply` reference (one directory per agent).
"""

from __future__ import annotations

import ast
import io
import json
import re
import zipfile
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

from agentcanvas.designs import AgentDesign, AgentFramework, ToolChoice
from agentcanvas.errors import InvalidInput
from agentcanvas.ir.models import SECRET_NAME, Workflow
from agentcanvas.tomltext import toml_string
from agentcanvas.validation.analysis import plain_secrets
from agentcanvas.workspace import bundle
from agentcanvas.workspace.store import HIDDEN, workflow_dir

ID = "claude_managed_agents"
PLATFORM = AgentFramework(ID, "Claude Managed Agents", ("anthropic",), "anthropic", "anthropic",
                          structured_output=False)  # fmt: skip
KIT = Path(__file__).parent / "kit"
TOOLSET = "agent_toolset_20260401"
ANT_MIN = "1.30.0"  # `ant apply`
ANT_VAULTS = "1.34.0"  # vault files
# Claude 4.5 and later (the platform takes no older model); claude-sonnet-4-5 retires on 2026-11-30
MODELS = frozenset({
    "claude-fable-5-1", "claude-fable-5", "claude-opus-5-5", "claude-opus-5", "claude-opus-4-8", "claude-opus-4-7",
    "claude-opus-4-6", "claude-sonnet-5-5", "claude-sonnet-5", "claude-sonnet-4-6", "claude-sonnet-4-5",
    "claude-haiku-5-5", "claude-haiku-4-5",
})  # fmt: skip
RETIRING = {"claude-sonnet-4-5": "2026-11-30"}
BUILT_IN = {"bash", "read", "write", "edit", "glob", "grep", "web_fetch", "web_search"}
TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
PLAIN_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")  # file and directory names that end up in commands


@dataclass
class Gap:
    blocks: bool  # the export cannot be made until this is dealt with
    text: str


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60].strip("-") or "agent"


def tool_name(t: ToolChoice) -> str:
    """The name the model calls a custom tool by, as the canvas names it (a Python tool by its function)."""
    if t.type == "tool.python":
        return str(t.config.get("import_path", "")).partition(":")[2]
    return re.sub(r"\W", "_", t.resource)


# ------------------------------------------------------------------ what carries ----
def export_problems(design: AgentDesign) -> list[str]:
    """What keeps this design from being exported for Claude Managed Agents at all."""
    found: list[str] = []
    m = design.model
    if m is None:
        found.append("connect an AI model block to the agent")
    elif m.provider != "anthropic":
        found.append(f"Claude Managed Agents runs Claude models only; this agent uses '{m.provider}'")
    elif m.name not in MODELS:
        found.append(f"Claude Managed Agents runs Claude 4.5 and later models (such as claude-opus-5-5), not "
                     f"'{m.name}'")  # fmt: skip
    seen: set[str] = set()
    for t in design.tools:
        if t.type == "mcp.server":
            if t.config.get("transport", "http") != "http":
                found.append(f"the MCP server '{t.resource}' starts a local process; Claude Managed Agents connects "
                             "to MCP servers by URL only")  # fmt: skip
            continue
        name = tool_name(t)
        if not TOOL_NAME.fullmatch(name) or name.startswith(("mcp__", "ant__")) or name in BUILT_IN:
            found.append(
                f"the tool '{t.resource}' would be the custom tool '{name}', a name Claude Managed Agents "
                "refuses (letters, digits, _ and -, not a built-in tool's, not starting mcp__ or ant__)"
            )
        elif name in seen:
            found.append(f"two tools are called '{name}'; custom tools need names of their own")
        seen.add(name)
    found += [
        f"the tool '{t.resource}' has the {'header' if key == 'headers' else 'variable'} {name} typed in as plain "
        "text, which would be written into the files: store it as a project secret"
        for t in design.tools
        for key, name in plain_secrets(t.config)
    ]
    return found


def gaps(design: AgentDesign, workspace: Path | None = None, wf_id: str | None = None) -> list[Gap]:
    """What does not carry over to Claude Managed Agents, most serious first."""
    out = [Gap(True, p) for p in export_problems(design)]
    m = design.model
    if m is not None and m.name in RETIRING:
        out.append(Gap(False, f"{m.name} retires on {RETIRING[m.name]}; pick a newer model before then."))
    if m is not None and (m.temperature is not None or m.max_tokens is not None):
        out.append(Gap(False, "The model block's temperature and token limit are not carried: the platform sets them."))
    custom = [t for t in design.tools if t.type != "mcp.server"]
    if custom:
        out.append(Gap(False, f"{', '.join(tool_name(t) for t in custom)} run on your machine as custom tools: a "
                              "session waits for run.py (or your own client) to answer each call."))  # fmt: skip
    plan = _approvals(design)
    if plan.custom:
        out.append(
            Gap(
                False,
                f"{', '.join(plan.custom)} asked a person before running on the canvas; run.py asks on "
                "the terminal (the platform's permission policies do not cover custom tools).",
            )
        )
    if plan.mcp:
        names = ", ".join(sorted({n for ns in plan.mcp.values() for n in ns}))
        out.append(Gap(False, f"The session pauses for your approval before {names} (always_ask); the other MCP tools "
                              "run without asking, as on the canvas."))  # fmt: skip
    if plan.unknown:
        out.append(
            Gap(
                False,
                f"{', '.join(plan.unknown)} need approval on the canvas but are not a tool this export "
                "can place: use Browse tools on the MCP server block, then export again.",
            )
        )
    for t in design.tools:
        if t.type == "mcp.server" and t.config.get("headers"):
            names = ", ".join(sorted(t.config["headers"]))
            out.append(
                Gap(
                    False,
                    f"The MCP server '{t.resource}' sends {names}: on the platform an MCP server "
                    "authenticates with a vault credential instead (see Credentials in the README).",
                )
            )
    if design.output_schema is not None:
        out.append(Gap(False, "The answer shape is not enforced there: it is added to the instructions, and run.py "
                              "prints the answer as the agent writes it."))  # fmt: skip
    if design.middleware:
        out.append(Gap(False, f"Add-ons (middleware) are not carried: {', '.join(design.middleware)}."))
    if design.other_tools:
        out.append(Gap(False, f"Only Python, web API and MCP tools are carried, not {', '.join(design.other_tools)}."))
    if workspace is not None:
        found, skipped = skills(workspace, wf_id)
        if found:
            out.append(
                Gap(
                    False,
                    f"The workspace's skills go with it ({', '.join(sorted(found))}), read with the "
                    "platform's read tool; scripts in them do not run (the agent has no shell).",
                )
            )
        out += [Gap(False, s) for s in skipped]
    out.append(Gap(False, "Sessions, traces and costs stay on the Claude Platform (the Console's session view): "
                          "nothing reports back to AgentCanvas."))  # fmt: skip
    seen: set[str] = set()
    return [g for g in sorted(out, key=lambda g: not g.blocks) if not (g.text in seen or seen.add(g.text))]


@dataclass
class _Approvals:
    custom: list[str]  # custom tools run.py asks about
    mcp: dict[str, list[str]]  # MCP server resource -> its tools that pause the session
    unknown: list[str]


def _approvals(design: AgentDesign) -> _Approvals:
    custom_names = {tool_name(t) for t in design.tools if t.type != "mcp.server"}
    servers = [t for t in design.tools if t.type == "mcp.server"]
    plan = _Approvals([], {}, [])
    for name in design.approve_tools:
        if name in custom_names:
            plan.custom.append(name)
            continue
        homes = [s for s in servers if name in _discovered(s)]
        if not homes and len(servers) == 1 and not _discovered(servers[0]):
            homes = servers  # one server whose tools were never listed: the name can only be one of its own
        for s in homes:
            plan.mcp.setdefault(s.resource, []).append(name)
        if not homes:
            plan.unknown.append(name)
    return plan


def _discovered(t: ToolChoice) -> set[str]:
    return {str(d.get("name")) for d in t.config.get("discovered") or [] if isinstance(d, dict) and d.get("name")}


# ------------------------------------------------------------------ skills ----
def skills(workspace: Path, wf_id: str | None) -> tuple[dict[str, Path], list[str]]:
    """The workspace's skills (`skills/<name>/SKILL.md`, and the workflow's own `flows/<id>/skills/`, which wins on a
    name), and why any were left out."""
    found: dict[str, Path] = {}
    skipped: list[str] = []
    roots = [workspace / "skills"] + ([workspace / workflow_dir(wf_id) / "skills"] if wf_id else [])
    for root in roots:
        for md in sorted(root.glob("*/SKILL.md")) if root.is_dir() else []:
            name = md.parent.name
            if not md.resolve().is_relative_to(workspace.resolve()) or name in HIDDEN:
                continue
            if not PLAIN_NAME.fullmatch(name):
                skipped.append(f"The skill folder '{name}' is left out: its name must be plain (letters, digits, "
                               "-, _ and .).")  # fmt: skip
                continue
            head = _frontmatter(md.read_text(errors="replace"))
            if not head.get("name") or not head.get("description"):
                skipped.append(f"The skill '{name}' is left out: its SKILL.md needs a name and a description in its "
                               "frontmatter.")  # fmt: skip
                continue
            found[name] = md.parent
    return found, skipped


def _frontmatter(text: str) -> dict[str, str]:
    """The top-level `key: value` lines of a Markdown file's `---` frontmatter (enough to check a SKILL.md)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return out
        key, sep, value = line.partition(":")
        if sep and key and not key[0].isspace():
            out[key.strip()] = value.strip().strip("'\"")
    return {}


# ------------------------------------------------------------------ YAML ----
_PLAIN = re.compile(r"^(\./)?[A-Za-z_][A-Za-z0-9_./-]*$")
_RESERVED = {"true", "false", "yes", "no", "on", "off", "null", "y", "n"}


_BREAKS = "\u2028\u2029\x85\ufeff"  # line separators and the byte-order mark: written as escapes


def quote(text: str) -> str:
    """A YAML double-quoted scalar that reads back as exactly `text` (a line break or a control character never
    ends it)."""
    out = ['"']
    for ch in text:
        if ch in '"\\':
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch.isprintable() and ch not in _BREAKS:
            out.append(ch)
        else:
            out.append(f"\\u{ord(ch):04x}" if ord(ch) <= 0xFFFF else f"\\U{ord(ch):08x}")
    return "".join(out) + '"'


def _scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return json.dumps(value)
    text = str(value)
    if _PLAIN.fullmatch(text) and text.lower() not in _RESERVED:
        return text
    return quote(text)


def _flow(value: dict[str, Any]) -> str | None:
    """`{a: 1, b: {c: x}}` for a small mapping of scalars, else None."""
    parts = []
    for k, v in value.items():
        if isinstance(v, dict):
            inner = _flow(v) if v else "{}"
            if inner is None:
                return None
            parts.append(f"{_scalar(k)}: {inner}")
        elif isinstance(v, list):
            return None
        else:
            parts.append(f"{_scalar(k)}: {_scalar(v)}")
    text = "{" + ", ".join(parts) + "}"
    return text if len(text) <= 90 else None


def yaml_lines(value: Any, indent: int = 0) -> list[str]:
    """Block-style YAML for JSON-like data (mappings, lists, scalars)."""
    pad = "  " * indent
    lines: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            key = _scalar(k)
            if isinstance(v, dict) and v and _flow(v) is None:
                lines.append(f"{pad}{key}:")
                lines += yaml_lines(v, indent + 1)
            elif isinstance(v, dict):
                lines.append(f"{pad}{key}: {_flow(v) if v else '{}'}")
            elif isinstance(v, list) and v:
                lines.append(f"{pad}{key}:")
                lines += yaml_lines(v, indent + 1)
            elif isinstance(v, list):
                lines.append(f"{pad}{key}: []")
            else:
                lines.append(f"{pad}{key}: {_scalar(v)}")
    elif isinstance(value, list):
        for v in value:
            if isinstance(v, dict) and v and _flow(v) is None:
                inner = yaml_lines(v, indent + 1)
                lines.append(f"{pad}- {inner[0].lstrip()}")
                lines += inner[1:]
            elif isinstance(v, dict):
                lines.append(f"{pad}- {_flow(v) if v else '{}'}")
            elif isinstance(v, list) and v:
                lines.append(f"{pad}-")
                lines += yaml_lines(v, indent + 1)
            else:
                lines.append(f"{pad}- {_scalar(v) if not isinstance(v, list) else '[]'}")
    else:
        lines.append(pad + _scalar(value))
    return lines


def yaml_text(value: Any) -> str:
    return "\n".join(yaml_lines(value)) + "\n"


# ------------------------------------------------------------------ Python tools' arguments ----
_JSON = {"str": "string", "int": "integer", "float": "number", "bool": "boolean", "list": "array",
         "tuple": "array", "set": "array", "dict": "object", "List": "array", "Sequence": "array",
         "Dict": "object", "Mapping": "object"}  # fmt: skip


def _annotation(node: ast.expr | None) -> tuple[dict[str, Any], bool]:
    """(JSON Schema, optional?) for a type hint, read from the source, never imported."""
    if node is None:
        return {}, False
    if isinstance(node, ast.Constant) and isinstance(node.value, str):  # "str", a hint as text
        try:
            return _annotation(ast.parse(node.value, mode="eval").body)
        except SyntaxError:
            return {}, False
    if isinstance(node, ast.Constant) and node.value is None:
        return {}, True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):  # X | None
        left, right = _annotation(node.left), _annotation(node.right)
        if _is_none(node.right):
            return left[0], True
        if _is_none(node.left):
            return right[0], True
        return {}, left[1] or right[1]
    name = node.attr if isinstance(node, ast.Attribute) else node.id if isinstance(node, ast.Name) else None
    if name in _JSON:
        return {"type": _JSON[name]}, False
    if isinstance(node, ast.Subscript):
        outer = node.value.attr if isinstance(node.value, ast.Attribute) else getattr(node.value, "id", None)
        args = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
        if outer == "Optional":
            return _annotation(args[0])[0], True
        if outer == "Union":
            rest = [a for a in args if not _is_none(a)]
            return (_annotation(rest[0])[0] if len(rest) == 1 else {}), len(rest) < len(args)
        if outer == "Literal":
            values = [a.value for a in args if isinstance(a, ast.Constant)]
            return ({"enum": values} if len(values) == len(args) else {}), False
        if outer == "Annotated":
            return _annotation(args[0])
        if outer in _JSON:
            schema: dict[str, Any] = {"type": _JSON[outer]}
            if schema["type"] == "array" and args:
                items, _ = _annotation(args[0])
                if items:
                    schema["items"] = items
            return schema, False
    return {}, False


def _is_none(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _arg_notes(doc: str) -> tuple[str, dict[str, str]]:
    """A docstring's summary and its `Args:` lines (`name: what` or `name (type): what`)."""
    head, sep, rest = doc.partition("\nArgs:")
    notes: dict[str, str] = {}
    if sep:
        for line in rest.splitlines():
            if line.strip() and not line.startswith((" ", "\t")):
                break
            m = re.match(r"\s+(\w+)\s*(?:\([^)]*\))?\s*:\s*(.+)", line)
            if m:
                notes[m.group(1)] = m.group(2).strip()
    return head.strip(), notes


def python_tool_schema(workspace: Path | None, import_path: str) -> tuple[str, dict[str, Any]] | None:
    """(description, input schema) of a workspace function, from its signature and docstring; None if unreadable."""
    module, _, fn = import_path.partition(":")
    if workspace is None:
        return None
    base = workspace.joinpath(*module.split("."))
    path = next((p for p in (base.with_suffix(".py"), base / "__init__.py") if p.is_file()), None)
    if path is None or not path.resolve().is_relative_to(workspace.resolve()):
        return None
    try:
        tree = ast.parse(path.read_text(errors="replace"))
    except SyntaxError:
        return None
    node = next((n for n in tree.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == fn), None)
    if node is None:
        return None
    summary, notes = _arg_notes(ast.get_docstring(node) or "")
    a = node.args
    positional = [*a.posonlyargs, *a.args]
    defaults = [None] * (len(positional) - len(a.defaults)) + list(a.defaults)
    params = [*zip(positional, defaults, strict=True), *zip(a.kwonlyargs, a.kw_defaults, strict=True)]
    props: dict[str, Any] = {}
    required = []
    for arg, default in params:
        if arg.arg in ("self", "cls"):
            continue
        schema, optional = _annotation(arg.annotation)
        if arg.arg in notes:
            schema = {**schema, "description": notes[arg.arg]}
        props[arg.arg] = schema
        if default is None and not optional:
            required.append(arg.arg)
    out: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        out["required"] = required
    return summary or fn, out


# ------------------------------------------------------------------ the files ----
def _custom_tools(design: AgentDesign, workspace: Path | None) -> list[dict[str, Any]]:
    tools = []
    for t in design.tools:
        if t.type == "tool.python":
            read = python_tool_schema(workspace, str(t.config.get("import_path", "")))
            description, schema = read or (tool_name(t), {"type": "object", "properties": {}})
        elif t.type == "tool.http":
            description = str(t.config.get("description") or t.resource)
            schema = t.config.get("args_schema") or {"type": "object", "properties": {}}
        else:
            continue
        tools.append({"type": "custom", "name": tool_name(t), "description": description, "input_schema": schema})
    return tools


def _mcp(design: AgentDesign) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(mcp_servers, their mcp_toolset entries)."""
    plan = _approvals(design)
    servers, toolsets = [], []
    taken: set[str] = set()
    for t in design.tools:
        if t.type != "mcp.server":
            continue
        name = slug(t.resource)
        while name in taken:
            name += "-2"
        taken.add(name)
        servers.append({"type": "url", "name": name, "url": str(t.config.get("url", ""))})
        only = list(t.config.get("tool_filter") or [])
        ask = plan.mcp.get(t.resource, [])
        entry: dict[str, Any] = {"type": "mcp_toolset", "mcp_server_name": name}
        default: dict[str, Any] = {"permission_policy": {"type": "always_allow"}}  # as on the canvas: asked = listed
        if only:
            default = {"enabled": False, **default}
        entry["default_config"] = default
        configs = [{"name": n, "enabled": True} for n in only]
        for n in ask:
            hit = next((c for c in configs if c["name"] == n), None)
            if hit is None:
                configs.append({"name": n, "permission_policy": {"type": "always_ask"}})
            else:
                hit["permission_policy"] = {"type": "always_ask"}
        if configs:
            entry["configs"] = configs
        toolsets.append(entry)
    return servers, toolsets


def _system(design: AgentDesign) -> str:
    text = design.instructions.strip()
    if design.output_schema is not None:
        text += ("\n\n" if text else "") + (
            "## Answer format\n\nAnswer with one JSON object, and nothing else, that matches this JSON Schema:\n\n"
            "```json\n" + json.dumps(design.output_schema, indent=2, ensure_ascii=False) + "\n```"
        )
    return text or "You are a helpful assistant."


def agent_md(design: AgentDesign, workspace: Path | None = None, skill_names: list[str] | None = None) -> str:
    """`agents/<agent>/agent.md`: the agent's configuration as frontmatter, its system prompt as the body."""
    m = design.model
    servers, toolsets = _mcp(design)
    tools: list[dict[str, Any]] = []
    if skill_names:  # skills are read with the agent toolset's `read`: only that one is on
        off = {"enabled": False, "permission_policy": {"type": "always_allow"}}
        tools.append({"type": TOOLSET, "default_config": off, "configs": [{"name": "read", "enabled": True}]})
    tools += _custom_tools(design, workspace) + toolsets
    config: dict[str, Any] = {
        "name": design.name[:256],
        "description": f"Designed on AgentCanvas: the block {json.dumps(design.name, ensure_ascii=False)} of the "
        f"workflow {json.dumps(design.workflow, ensure_ascii=False)}."[:2048],
        "model": m.name if m else "claude-opus-5-5",
    }
    if tools:
        config["tools"] = tools
    if servers:
        config["mcp_servers"] = servers
    if skill_names:
        config["skills"] = [f"./skills/{s}" for s in skill_names]
    return "---\n" + yaml_text(config) + "---\n\n" + _system(design) + "\n"


def environment_yaml(design: AgentDesign) -> str:
    networking: dict[str, Any] = {"type": "limited"}  # nothing in the sandbox needs the internet: tools run elsewhere
    if any(t.type == "mcp.server" for t in design.tools):
        networking["allow_mcp_servers"] = True
    return yaml_text({"name": f"{slug(design.name)}-env", "config": {"type": "cloud", "networking": networking}})


_IMPORT_PATH = re.compile(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)*:[A-Za-z_]\w*")


def custom_tools_py(design: AgentDesign) -> str:
    imports, entries = [], []
    for i, t in enumerate(design.tools):
        if t.type == "tool.python":
            path = str(t.config.get("import_path", ""))
            if not _IMPORT_PATH.fullmatch(path):  # it is written into the code as an import
                raise InvalidInput(f"tool '{t.resource}': import path {path!r} is not module.path:function")
            module, _, fn = path.partition(":")
            imports.append(f"from {module} import {fn} as _tool_{i}")
            entries.append(f"    {json.dumps(tool_name(t))}: _tool_{i},")
        elif t.type == "tool.http":
            entries.append(f"    {json.dumps(tool_name(t))}: http_tool({json.dumps(t.resource)}, {t.config!r}),")
    approve = _approvals(design).custom
    head = ["from canvas_tools import http_tool"] if any(t.type == "tool.http" for t in design.tools) else []
    return "\n".join([
        f'"""The custom tools of the agent "{_doc(design.name)}": Claude Managed Agents sends each call here.',
        "",
        "run.py answers every `agent.custom_tool_use` event with `call_tool`, so the tools run on this machine with",
        'its settings and keys, exactly as on the canvas (generated by AgentCanvas)."""',
        "",
        "from __future__ import annotations",
        "",
        "import inspect",
        "import json",
        "from typing import Any",
        "",
        *head,
        *sorted(set(imports)),
        *([""] if head or imports else []),
        f"APPROVE = {json.dumps(approve)}  # run.py asks on the terminal before these run",
        "",
        "TOOLS: dict[str, Any] = {",
        *entries,
        "}",
        "",
        "",
        "async def call_tool(name: str, args: dict[str, Any]) -> str:",
        '    """Run the custom tool `name` with the arguments the agent gave; its answer as text."""',
        "    fn = TOOLS.get(name)",
        "    if fn is None:",
        '        return f"Error: there is no tool called {name}"',
        "    try:",
        '        if hasattr(fn, "ainvoke"):  # a LangChain tool',
        "            out = await fn.ainvoke(args)",
        "        else:",
        "            out = fn(**args)",
        "            if inspect.isawaitable(out):",
        "                out = await out",
        "    except Exception as exc:  # noqa: BLE001 - the agent reads the error and can try again",
        '        return f"Error: {type(exc).__name__}: {exc}"',
        "    return out if isinstance(out, str) else json.dumps(out, default=str)",
        "",
    ])  # fmt: skip


def _doc(text: str) -> str:
    """Text safe inside a triple-quoted docstring."""
    return json.dumps(text, ensure_ascii=False)[1:-1].replace('"', "'")


RUN = '''"""Run the agent on Claude Managed Agents: python run.py "your question" (after `make apply`, see the README).

One session per question: it streams the session's events, prints the agent's answer, runs the custom tools
(custom_tools.py) when the agent calls them, and asks on the terminal before a call that needs approval. With no
terminal to ask, such a call is denied.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from custom_tools import APPROVE, call_tool

ROOT = Path(__file__).parent
AGENT = {agent}
ENVIRONMENT = {environment}
VAULT = {vault}
WORKSPACE = os.environ.get("ANTHROPIC_WORKSPACE", "default")  # for the Console link


def lock_id(path: str) -> str:
    """The ID `ant apply` gave the resource in `path`, from claude-lock.json."""
    lock = ROOT / "claude-lock.json"
    if not lock.is_file():
        sys.exit("claude-lock.json is missing: run `make apply` first (see the README)")
    for key, entry in (json.loads(lock.read_text()).get("resources") or {{}}).items():
        if key.removeprefix("./") == path:
            return entry["id"]
    sys.exit(f"{{path}} is not in claude-lock.json: run `make apply` again")


def confirm(what: str) -> bool:
    if not sys.stdin.isatty():
        print(f"[denied {{what}}: nobody at the terminal to approve it]", file=sys.stderr)
        return False
    return input(f"Allow {{what}}? [y/N] ").strip().lower() in ("y", "yes")


def run(client, text: str, *, ask=confirm, out=sys.stdout) -> str:
    """One session on `text`: the agent's answer."""
    extra = {{"vault_ids": [lock_id(VAULT)]}} if VAULT else {{}}
    session = client.beta.sessions.create(
        agent=lock_id(AGENT), environment_id=lock_id(ENVIRONMENT), title=text[:80], **extra
    )
    print(f"Watch it: https://platform.claude.com/workspaces/{{WORKSPACE}}/sessions/{{session.id}}", file=sys.stderr)
    said: list[str] = []
    with client.beta.sessions.events.stream(session_id=session.id) as stream:  # open before sending: see every event
        client.beta.sessions.events.send(
            session_id=session.id, events=[{{"type": "user.message", "content": [{{"type": "text", "text": text}}]}}]
        )
        for event in stream:
            if event.type == "agent.message":
                for block in event.content:
                    if block.type == "text":
                        said.append(block.text)
                        print(block.text, end="", flush=True, file=out)
            elif event.type == "agent.custom_tool_use":
                if event.name in APPROVE and not ask(f"{{event.name}}({{json.dumps(event.input)}})"):
                    result = "A person rejected this tool call. Do not try it again."
                else:
                    result = asyncio.run(call_tool(event.name, dict(event.input or {{}})))
                client.beta.sessions.events.send(session_id=session.id, events=[{{
                    "type": "user.custom_tool_result", "custom_tool_use_id": event.id,
                    "content": [{{"type": "text", "text": result}}],
                }}])
            elif event.type in ("agent.tool_use", "agent.mcp_tool_use") and event.evaluated_permission == "ask":
                allowed = ask(f"{{event.name}}({{json.dumps(getattr(event, 'input', None))}})")
                client.beta.sessions.events.send(session_id=session.id, events=[{{
                    "type": "user.tool_confirmation", "tool_use_id": event.id, "result": "allow" if allowed else "deny",
                }}])
            elif event.type == "session.error":
                print(f"\\n[session error: {{getattr(event, 'error', event)}}]", file=sys.stderr)
            elif event.type == "session.status_idle" and event.stop_reason.type != "requires_action":
                break
            elif event.type == "session.status_terminated":
                break
    print(file=out)
    return "".join(said)


def main() -> None:
    import anthropic

    if len(sys.argv) < 2:
        sys.exit('ask a question: python run.py "Hello!"')
    run(anthropic.Anthropic(), " ".join(sys.argv[1:]))


if __name__ == "__main__":
    main()
'''

TESTS = '''"""Offline checks of the bundle: its files parse as `ant apply` reads them, every custom tool has its code,
and run.py answers a session's events (a scripted stand-in for the platform: no key, no network)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import yaml

import custom_tools
import run

ROOT = Path(__file__).parents[1]
AGENT = ROOT / {agent}


def frontmatter(path: Path) -> tuple[dict, str]:
    text = path.read_text()
    assert text.startswith("---\\n")
    head, _, body = text[4:].partition("\\n---\\n")
    return yaml.safe_load(head), body


def test_the_agent_file_is_what_ant_apply_reads():
    config, body = frontmatter(AGENT)
    assert config["name"] and config["model"].startswith("claude-") and body.strip()
    for skill in config.get("skills") or []:
        assert (AGENT.parent / skill / "SKILL.md").is_file()
    servers = {{s["name"] for s in config.get("mcp_servers") or []}}
    assert {{t["mcp_server_name"] for t in config.get("tools") or [] if t["type"] == "mcp_toolset"}} == servers


def test_the_environment_and_vault_files_parse():
    env = yaml.safe_load((ROOT / {environment}).read_text())
    assert env["config"]["type"] == "cloud"
    if {vault}:
        assert yaml.safe_load((ROOT / {vault}).read_text())["type"] == "vault"


def test_every_custom_tool_has_its_code():
    config, _ = frontmatter(AGENT)
    names = {{t["name"] for t in config.get("tools") or [] if t["type"] == "custom"}}
    assert names == set(custom_tools.TOOLS)
    assert set(custom_tools.APPROVE) <= names


class FakeSessions:
    """Stands in for client.beta.sessions: a session that calls each custom tool once, then answers."""

    def __init__(self, tools):
        self.tools, self.sent = tools, []
        self.events = SimpleNamespace(stream=self.stream, send=self.send)

    def create(self, **kwargs):
        self.created = kwargs
        return SimpleNamespace(id="sesn_test")

    def send(self, session_id, events):
        self.sent += events

    def stream(self, session_id):
        script = [
            SimpleNamespace(type="agent.custom_tool_use", id=f"sevt_{{n}}", name=n, input={{}}) for n in self.tools
        ]
        script.append(SimpleNamespace(type="agent.mcp_tool_use", id="sevt_mcp", name="x", input={{}},
                                      evaluated_permission="ask"))
        script.append(SimpleNamespace(type="agent.message", content=[SimpleNamespace(type="text", text="done")]))
        script.append(SimpleNamespace(type="session.status_idle", stop_reason=SimpleNamespace(type="end_turn")))

        class Stream:
            def __enter__(self):
                return iter(script)

            def __exit__(self, *exc):
                return False

        return Stream()


def test_run_answers_the_tools_and_asks_before_approved_calls(tmp_path, monkeypatch):
    paths = [p for p in (run.AGENT, run.ENVIRONMENT, run.VAULT) if p]
    resources = {{f"./{{p}}": {{"id": f"id_{{i}}"}} for i, p in enumerate(paths)}}
    monkeypatch.setattr(run, "ROOT", tmp_path)
    (tmp_path / "claude-lock.json").write_text(json.dumps({{"resources": resources}}))

    async def fake_tool(name, args):
        return f"ran {{name}}"

    monkeypatch.setattr(run, "call_tool", fake_tool)
    sessions = FakeSessions(list(custom_tools.TOOLS))
    asked = []
    answer = run.run(SimpleNamespace(beta=SimpleNamespace(sessions=sessions)), "hello",
                     ask=lambda what: asked.append(what) or False, out=open("/dev/null", "w"))  # noqa: SIM115
    assert answer == "done" and sessions.created["agent"] == "id_0"
    results = {{e["custom_tool_use_id"]: e for e in sessions.sent if e["type"] == "user.custom_tool_result"}}
    assert set(results) == {{f"sevt_{{n}}" for n in custom_tools.TOOLS}}
    for name in custom_tools.TOOLS:
        text = results[f"sevt_{{name}}"]["content"][0]["text"]
        assert text == ("A person rejected this tool call. Do not try it again." if name in custom_tools.APPROVE
                        else f"ran {{name}}")
    confirmation = next(e for e in sessions.sent if e["type"] == "user.tool_confirmation")
    assert confirmation == {{"type": "user.tool_confirmation", "tool_use_id": "sevt_mcp", "result": "deny"}}
    assert len(asked) == 1 + len(custom_tools.APPROVE)
'''

CONFTEST = '''"""Lets the tests import the bundle's modules."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
'''


def _pin(dist: str) -> str:
    try:
        return f"{dist}=={metadata.version(dist)}"
    except metadata.PackageNotFoundError:
        return dist


def _secrets(value: object) -> set[str]:
    if isinstance(value, dict):
        if "$secret" in value and isinstance(value["$secret"], str):
            if not SECRET_NAME.fullmatch(value["$secret"]):
                raise InvalidInput(f"secret name {value['$secret']!r} is not a variable name (letters, digits and _)")
            return {value["$secret"]}
        return set().union(*(_secrets(v) for v in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(_secrets(v) for v in value)) if value else set()
    return set()


def _makefile(apply: list[str]) -> str:
    files = " ".join(apply)
    return f"""# Claude Managed Agents bundle (generated by AgentCanvas). `make help` lists the commands.
.PHONY: help doctor setup test plan apply run

help:  ## show the commands
\t@grep -E '^[a-z]+:.*##' Makefile | sed 's/:.*## /\\t/'

doctor:  ## is this machine ready? (the ant CLI, a key, the settings the tools need)
\t@python3 doctor.py

setup:  ## install what run.py and the tests need
\tuv sync

test:  ## offline checks of the files and of run.py
\tuv run pytest -q

plan:  ## what ant apply would create or change (changes nothing)
\tant apply --dry-run -v {files}

apply:  ## create or update the agent, its environment and skills (asks first)
\tant apply {files}

run:  ## one session: make run Q="your question"
\tuv run --env-file .env python run.py "$(Q)"
"""


def _readme(design: AgentDesign, root: str, d: str, apply: list[str], secrets: list[str], found: list[Gap],
            skill_names: list[str], vault: str | None) -> str:  # fmt: skip
    custom = [tool_name(t) for t in design.tools if t.type != "mcp.server"]
    servers = [t for t in design.tools if t.type == "mcp.server"]
    files = " ".join(apply)
    creds = ""
    if vault:
        blocks = []
        for t in servers:
            auth = (t.config.get("headers") or {}).get("Authorization")
            env = auth["$secret"] if isinstance(auth, dict) and "$secret" in auth else "MCP_TOKEN"
            var = f"CMA_{env}"
            url = str(t.config.get("url", ""))
            blocks.append(f"""Sends your token for '{t.resource}' to {url} (matched to the agent's MCP server by URL):

```sh
read -rs {var} && export {var}     # paste the token (without "Bearer ") at the silent prompt
jq -n -f /dev/stdin <<'JQ' | ant beta:vaults:credentials create --vault-id "$VAULT_ID"
{{
  display_name: {json.dumps(t.resource)},
  auth: {{type: "static_bearer", mcp_server_url: {json.dumps(url)},
         token: (env.{var} // error("{var} is not set"))}}
}}
JQ
unset {var}
```""")
        creds = (
            f"""
## Credentials

An MCP server signs in with a credential in the vault (`{vault}`), never with a header in a file. After
`make apply`, run each block below **one at a time** in your own terminal:

```sh
VAULT_ID=$(jq -r '.resources["./{vault}"].id' claude-lock.json)
```

"""
            + "\n\n".join(blocks)
            + "\n"
        )
    tools_line = (f"Its custom tools ({', '.join(custom)}) run on your machine: the session waits for `run.py` to "
                  "answer each call, using the settings in `.env`." if custom else "")  # fmt: skip
    must = [g.text for g in found if g.blocks]
    differs = [g.text for g in found if not g.blocks]
    return f"""# {design.name} on Claude Managed Agents

The AI agent "{design.name}" of the workflow "{design.workflow}", designed on AgentCanvas, as files the
[`ant` CLI](https://platform.claude.com/docs/en/cli-sdks-libraries/cli/apply.md) applies to Claude Managed Agents:
the agent, its environment{", its skills" if skill_names else ""}{
        " and a vault for its MCP credentials" if vault else ""
    }.
{tools_line}

## 1. Check this machine

```bash
cd {root}
make doctor      # the ant CLI ({
        ANT_VAULTS if vault else ANT_MIN
    } or later), an Anthropic key, the settings the tools need
cp .env.example .env   # then fill in the values
make setup && make test
```

`ant` signs in with `ant auth login`, or reads `ANTHROPIC_API_KEY`.

## 2. Create the agent

```bash
make plan        # ant apply --dry-run -v {files}
make apply       # ant apply {files}  (prints the plan, then asks)
```

`ant apply` records each resource's ID in `claude-lock.json`: commit it. To change the agent, change it on the
canvas, export again over this folder (keep `claude-lock.json`) and run `make apply` again: the agent gets a new
version. Name the files as above rather than `.`: a walk of the whole folder would also apply look-alike files.
{creds}
## 3. Run a session

```bash
make run Q="Hello!"      # or: uv run --env-file .env python run.py "Hello!"
```

`run.py` starts a session, prints the agent's answer and a link to watch it in the Console, runs the custom tools
and asks before a call that needs approval. With the `ant` CLI alone:

```bash
AGENT_ID=$(jq -r '.resources["./{d}/agent.md"].id' claude-lock.json)
ENV_ID=$(jq -r '.resources["./{d}/environment.yaml"].id' claude-lock.json)
SID=$(ant beta:sessions create --agent "$AGENT_ID" --environment-id "$ENV_ID" --title "Hello" --transform id -r)
ant beta:sessions:events send --session-id "$SID" \\
  --event '{{type: user.message, content: [{{type: text, text: "Hello!"}}]}}'
ant beta:sessions connect "$SID"       # follow it, answer approvals{"; custom tools need run.py" if custom else ""}
```

## Files

- `{d}/agent.md`: the agent: model, tools, MCP servers{
        ", skills" if skill_names else ""
    }; the body is its system prompt.
- `{d}/environment.yaml`: the cloud environment its sessions run in.{
        f'''
- `{vault}`: the vault for MCP credentials (only the container: credentials are added as above).'''
        if vault
        else ""
    }{
        '''
- `'''
        + d
        + '''/skills/`: the workspace's skills.'''
        if skill_names
        else ""
    }
- `run.py`, `custom_tools.py`: the session runner and the custom tools it answers.
- `doctor.py`: `make doctor`. `tests/`: `make test`, offline.
- `GAPS.md`: what did not carry over from the canvas.

## Settings

```
ANTHROPIC_API_KEY=...{"".join(f"{chr(10)}{s}=..." for s in secrets)}
```

## What did not carry over

{chr(10).join(f"- **Must fix:** {g}" for g in must)}{chr(10) if must else ""}{chr(10).join(f"- {g}" for g in differs)}
"""


def _gaps_md(design: AgentDesign, found: list[Gap]) -> str:
    must = [g.text for g in found if g.blocks]
    differs = [g.text for g in found if not g.blocks]
    parts = [f"# What carries over to Claude Managed Agents: {design.name}", ""]
    if must:
        parts += ["## Before it runs there", "", *[f"- {g}" for g in must], ""]
    parts += ["## What works differently", "", *[f"- {g}" for g in differs], ""]
    return "\n".join(parts)


def export_files(
    wf: Workflow, design: AgentDesign, workspace: Path | None = None
) -> dict[str, str | bytes]:  # fmt: skip
    """The bundle's files by path. Raises InvalidInput (with every reason) when the design cannot be exported."""
    problems = export_problems(design)
    if problems:
        raise InvalidInput(f"this agent cannot be exported for {PLATFORM.label}: " + "; ".join(problems))
    assert design.model is not None
    s = slug(design.name)
    root = f"{s}-managed-agents"
    d = f"agents/{s}"
    found_skills, _ = skills(workspace, wf.id) if workspace is not None else ({}, [])
    skill_names = sorted(found_skills)
    vault = f"{d}/vault.yaml" if any(t.type == "mcp.server" and t.config.get("headers") for t in design.tools) else None
    apply = [f"{d}/agent.md", f"{d}/environment.yaml", *([vault] if vault else [])]
    deps = [_pin("anthropic")]
    if any(t.type == "tool.http" for t in design.tools):
        deps.append(_pin("httpx"))
    files: dict[str, str | bytes] = {
        f"{d}/agent.md": agent_md(design, workspace, skill_names),
        f"{d}/environment.yaml": environment_yaml(design),
        "custom_tools.py": custom_tools_py(design),
        "run.py": RUN.format(
            agent=json.dumps(f"{d}/agent.md"), environment=json.dumps(f"{d}/environment.yaml"), vault=json.dumps(vault)
        ),
        "doctor.py": (KIT / "managed_doctor.py").read_text(),
        "tests/conftest.py": CONFTEST,
        "tests/test_bundle.py": TESTS.format(
            agent=json.dumps(f"{d}/agent.md"), environment=json.dumps(f"{d}/environment.yaml"), vault=json.dumps(vault)
        ),
        "Makefile": _makefile(apply),
        ".gitignore": ".venv/\n__pycache__/\n.env\n",
    }
    if vault:
        files[vault] = yaml_text({"type": "vault", "display_name": s})
    if any(t.type == "tool.http" for t in design.tools):
        files["canvas_tools.py"] = (KIT / "canvas_tools.py").read_text()
    for name, folder in found_skills.items():
        for p in sorted(folder.rglob("*")):
            rel = p.relative_to(folder)
            if p.is_file() and not any(part in HIDDEN for part in rel.parts) and p.resolve().is_relative_to(folder):
                files[f"{d}/skills/{name}/{rel.as_posix()}"] = p.read_bytes()
    python_tools = [t.config for t in design.tools if t.type == "tool.python"]
    if workspace is not None and python_tools:
        found = bundle.collect(workspace, wf.id, json.dumps(python_tools))
        if found.missing:
            raise InvalidInput("the agent's tools use code that is not in the workspace: " + ", ".join(found.missing))
        skills_dir = f"{workflow_dir(wf.id)}/skills/"
        for rel in found.files:
            if not rel.startswith(skills_dir):  # those went into the agent's skills
                files[rel] = (workspace / rel).read_bytes()
        deps += [r for r in found.requirements if r not in deps]
    secrets = sorted(_secrets([t.config for t in design.tools if t.type != "mcp.server"]))
    found_gaps = gaps(design, workspace, wf.id)
    hosts = sorted(
        {str(h) for t in design.tools if t.type == "tool.http" for h in t.config.get("allowed_domains") or []}
    )
    files["doctor.json"] = json.dumps({
        "agent": design.name, "files": apply, "ant_min": ANT_VAULTS if vault else ANT_MIN, "settings": secrets,
        "hosts": hosts, "custom_tools": bool(python_tools or hosts),
    }, indent=2) + "\n"  # fmt: skip
    files[".env.example"] = "\n".join(["ANTHROPIC_API_KEY=", "# ANTHROPIC_WORKSPACE=default  # for the Console link",
                                       *[f"{s_}=" for s_ in secrets]]) + "\n"  # fmt: skip
    files["GAPS.md"] = _gaps_md(design, found_gaps)
    files["README.md"] = _readme(design, root, d, apply, secrets, found_gaps, skill_names, vault)
    files["pyproject.toml"] = "\n".join([
        "[project]",
        f'name = "{root}"',
        'version = "0.1.0"',
        f"description = {toml_string(f'{design.name}: an agent for Claude Managed Agents designed on AgentCanvas')}",
        'requires-python = ">=3.11,<3.15"',
        "dependencies = [",
        *[f"    {toml_string(dep)}," for dep in deps],
        "]",
        "",
        "[dependency-groups]",
        f'dev = ["{_pin("pytest")}", "{_pin("pyyaml")}"]',
        "",
        "[tool.uv]",
        "package = false",
        "",
    ])  # fmt: skip
    return {f"{root}/{rel}": content for rel, content in files.items()}


def export_zip(wf: Workflow, design: AgentDesign, workspace: Path | None = None) -> tuple[str, bytes]:
    files = export_files(wf, design, workspace)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, content in sorted(files.items()):
            info = zipfile.ZipInfo(rel, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, content)
    return f"{next(iter(files)).split('/')[0]}.zip", buf.getvalue()
