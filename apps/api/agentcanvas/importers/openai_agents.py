"""OpenAI Agents SDK code (what OpenAI's Agent Builder exports, and what people write by hand) becomes a workflow.

The Python is read with `ast` and never run. What carries over:

- every `Agent(...)`: its name, `instructions` (text), `model`, `model_settings` (temperature, max tokens), `tools`
  and `output_type`, as an AI agent block with its model block;
- `@function_tool` functions, as Python tools: their source goes to the project's workspace
  (`tools/<workflow key>_tools.py`, written as a LangChain `@tool` with its description, without the SDK's run
  context parameter), with the imports, constants, helpers and classes of the file they use, added when the
  workflow is created;
- `handoffs=[...]` and `other.as_tool(...)`: the agent becomes a Planning Agent and the others its specialists;
- the order of the `Runner.run(agent, ...)` and `agent.run(...)` / `agent.run_sync(...)` calls (an Agent Builder
  export runs its agents one after another), else the agent no other agent hands off to.

Hosted tools (web search, file search, code interpreter), guardrails and branching code are listed in the report with
what to use instead.
"""

from __future__ import annotations

import ast
import builtins
import re
from typing import Any

from agentcanvas.agentspec import ident
from agentcanvas.importers import Builder, ImportError_

HOSTED = {
    "WebSearchTool": "web search: add an HTTP tool or an app connector (MCP) for a search API",
    "FileSearchTool": "file search: put the files in a knowledge base and add a Search knowledge step",
    "CodeInterpreterTool": "the code interpreter: write the code as a workspace script or Python tool",
    "ComputerTool": "computer use",
    "HostedMCPTool": "a hosted MCP server: add it as an App connector (MCP)",
    "ImageGenerationTool": "image generation",
}
TYPES = {"str": "string", "int": "integer", "float": "number", "bool": "boolean"}
CONTEXT_TYPES = ("RunContextWrapper", "ToolContext")  # the SDK's run context: the SDK fills it, never the model
TOOLS_HEAD = ('"""Tools imported from OpenAI Agents SDK code."""\n\nfrom __future__ import annotations\n\n'
              "from langchain_core.tools import tool\n")  # fmt: skip
MAX_SOURCE = 500_000  # characters of Python in one file


_SDK_IMPORT = re.compile(r"^\s*(from\s+agents(\.classic)?\s+import|import\s+agents\b)", re.M)


def looks_like(text: Any) -> bool:
    """Agents SDK code: `from agents import ...`, or `from agents.classic import ...` (where the next major release
    of the SDK keeps today's API, beside its new native runtime)."""
    return isinstance(text, str) and bool(_SDK_IMPORT.search(text)) and "Agent(" in text


def _name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _const(node: ast.AST | None) -> Any:
    """A literal's value (strings, numbers, joined f-strings without placeholders); None for anything else."""
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        pass
    if isinstance(node, ast.JoinedStr):  # an f-string: its text, placeholders kept as {name}
        out = []
        for v in node.values:
            if isinstance(v, ast.Constant):
                out.append(str(v.value))
            elif isinstance(v, ast.FormattedValue):
                out.append("{" + (ast.unparse(v.value)[:60]) + "}")
        return "".join(out)
    return None


def _kw(call: ast.Call, key: str) -> ast.AST | None:
    return next((k.value for k in call.keywords if k.arg == key), None)


def _bound(stmt: ast.stmt) -> set[str]:
    """The module-level names a statement defines."""
    if isinstance(stmt, ast.Import | ast.ImportFrom):
        return {(a.asname or a.name).split(".")[0] for a in stmt.names if a.name != "*"}
    if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return {stmt.name}
    targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target] if isinstance(stmt, ast.AnnAssign) else []
    return {n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)}


def _reads(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


def _locals(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    a = fn.args
    params = {x.arg for x in [*a.posonlyargs, *a.args, *a.kwonlyargs, *[v for v in (a.vararg, a.kwarg) if v]]}
    stored = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store | ast.Del)}
    inner = {n.name for n in ast.walk(fn) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)}
    caught = {n.name for n in ast.walk(fn) if isinstance(n, ast.ExceptHandler) and n.name}
    return params | stored | (inner - {fn.name}) | caught


def _is_agents_import(stmt: ast.stmt) -> bool:
    if isinstance(stmt, ast.ImportFrom):
        return (stmt.module or "").split(".")[0] == "agents" or stmt.module == "__future__"
    return isinstance(stmt, ast.Import) and any(a.name.split(".")[0] == "agents" for a in stmt.names)


def _context_param(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.arg | None:
    """The first parameter when it is the SDK's run context (`ctx: RunContextWrapper[...]`)."""
    args = [*fn.args.posonlyargs, *fn.args.args]
    if not args or args[0].annotation is None:
        return None
    ann = args[0].annotation
    base = ann.value if isinstance(ann, ast.Subscript) else ann
    if isinstance(base, ast.Constant) and isinstance(base.value, str):  # a string annotation
        return args[0] if base.value.split("[")[0].split(".")[-1] in CONTEXT_TYPES else None
    return args[0] if _name(base) in CONTEXT_TYPES else None


class _Reader:
    def __init__(self, source: str, b: Builder, key: str):
        self.source, self.b, self.key = source, b, key
        try:
            self.tree = ast.parse(source)
        except SyntaxError as exc:
            raise ImportError_(f"the code is not valid Python (line {exc.lineno}: {exc.msg})") from None
        self.agents: dict[str, ast.Call] = {}  # variable -> Agent(...) call
        self.functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
        self.classes: dict[str, ast.ClassDef] = {}
        self.order: list[str] = []  # agents in the order Runner.run / agent.run calls them
        self.runs: list[tuple[int, int, str | None, str | None]] = []  # (line, column, owner, first argument)
        self.tool_rids: dict[str, str] = {}
        self.files: dict[str, str] = {}

    def scan(self) -> None:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and _name(node.value.func) == "Agent":
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        self.agents[t.id] = node.value
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                if any(_name(d.func if isinstance(d, ast.Call) else d) == "function_tool" for d in node.decorator_list):
                    self.functions[node.name] = node
            elif isinstance(node, ast.ClassDef):
                self.classes[node.name] = node
            elif isinstance(node, ast.Call) and _name(node.func) in ("run", "run_sync", "run_streamed"):
                owner = node.func.value if isinstance(node.func, ast.Attribute) else None
                if owner is not None:
                    first = _name(node.args[0]) if node.args else None
                    self.runs.append((node.lineno, node.col_offset, _name(owner), first))
            elif isinstance(node, ast.If | ast.Match) and self._inside_workflow(node):
                self.b.note("The code branches (if/else); the workflow runs its agents in order. Add a Choose a path "
                            "step where the original chose between agents.")  # fmt: skip
        if not self.agents:
            raise ImportError_("no Agent(...) found in this code")
        for _, _, owner, first in sorted(self.runs, key=lambda r: (r[0], r[1])):  # in the order the code runs them
            # Runner.run(agent, ...) (and Agent.run(agent, ...)), or agent.run(...) / agent.run_sync(...)
            var = first if owner in ("Runner", "Agent") else owner if owner in self.agents else None
            if var and var in self.agents and var not in self.order:
                self.order.append(var)

    def _inside_workflow(self, node: ast.AST) -> bool:
        text = ast.unparse(node)[:2000]
        return "Runner." in text or any(f"{a}.run" in text for a in self.agents)

    # ---- blocks ----
    def model(self, call: ast.Call, label: str) -> str:
        model = _const(_kw(call, "model"))
        model_id = f"openai:{model}" if isinstance(model, str) and model else "openai:gpt-5.5"
        if not (isinstance(model, str) and model):
            self.b.note(f"Agent '{label}' names no model (the SDK's default): it uses openai:gpt-5.5; change it in "
                        "its AI model block.")  # fmt: skip
        settings = _kw(call, "model_settings")
        temperature = max_tokens = None
        if isinstance(settings, ast.Call):
            temperature, max_tokens = _const(_kw(settings, "temperature")), _const(_kw(settings, "max_tokens"))
        return self.b.model(model_id, temperature=temperature, max_tokens=max_tokens)

    def tool(self, name: str) -> str | None:
        if name in self.tool_rids:
            return self.tool_rids[name]
        fn = self.functions.get(name)
        if fn is None:
            return None
        rid = f"r_{len(self.b.resources) + 1}"
        self.b.resources.append({"id": rid, "type": "tool.python", "name": ident(name, self.b.names, "tool"),
                                 "config": {"import_path": f"tools.{self.key}_tools:{name}"}})  # fmt: skip
        self.tool_rids[name] = rid
        return rid

    def _agent_code(self, stmt: ast.stmt) -> bool:
        """A statement that makes or runs agents (it stays behind: the workflow is the agents now)."""
        for n in ast.walk(stmt):
            if isinstance(n, ast.Call) and _name(n.func) in ("Agent", "Runner", "run", "run_sync", "run_streamed"):
                return True
            if isinstance(n, ast.Name) and (n.id in self.agents or n.id == "Runner"):
                return True
        return False

    def _tool_source(self, fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        """`fn` as a LangChain `@tool`: its description given (the override, the docstring, or one made up), the
        SDK's run-context parameter left out."""
        label = fn.name
        deco = next((d for d in fn.decorator_list if _name(d.func if isinstance(d, ast.Call) else d)
                     == "function_tool"), None)  # fmt: skip
        override = _const(_kw(deco, "description_override")) if isinstance(deco, ast.Call) else None
        tool_name = _const(_kw(deco, "name_override")) if isinstance(deco, ast.Call) else None
        description = override if isinstance(override, str) and override.strip() else ast.get_docstring(fn)
        if not description:
            description = fn.name.replace("_", " ").strip().capitalize() + "."
            self.b.note(f"Tool '{label}' had no docstring or description: it is described to the model as "
                        f"'{description}'; write a better one in tools/{self.key}_tools.py.")  # fmt: skip
        ctx = _context_param(fn)
        if ctx is None:
            body = ast.get_source_segment(self.source, fn) or ast.unparse(fn)
        else:
            plain = ast.parse(ast.unparse(fn)).body[0]
            assert isinstance(plain, ast.FunctionDef | ast.AsyncFunctionDef)
            plain.decorator_list = []
            args = plain.args.posonlyargs if plain.args.posonlyargs else plain.args.args
            args.pop(0)
            body = ast.unparse(plain)
            if ctx.arg in _reads(fn):
                self.b.note(f"Tool '{label}' reads the SDK's run context ({ctx.arg}), which a LangChain tool does not "
                            f"get: change it in tools/{self.key}_tools.py.")  # fmt: skip
        head = f"@tool({tool_name!r}, description={description!r})" if isinstance(tool_name, str) and tool_name else (
            f"@tool(description={description!r})")  # fmt: skip
        return head + "\n" + body

    def _segment(self, stmt: ast.stmt) -> str:
        """A module-level statement as written, its decorators included."""
        decorators = getattr(stmt, "decorator_list", [])
        start = min([stmt.lineno, *(d.lineno for d in decorators)]) - 1
        lines = self.source.splitlines()[start : stmt.end_lineno or stmt.lineno]
        return "\n".join(lines) if lines else ast.unparse(stmt)

    def tools_module(self) -> str:
        """The tools' file: each `@function_tool` as a LangChain tool, with the imports, constants, helpers and
        classes of the original file they use (found by name; agent code stays behind)."""
        fns = [self.functions[n] for n in self.tool_rids]
        top = self.tree.body
        defs: dict[str, list[int]] = {}
        held: set[str] = set()  # names only agent code defines
        for i, stmt in enumerate(top):
            if stmt in fns or _is_agents_import(stmt):
                continue
            if not isinstance(stmt, ast.Import | ast.ImportFrom | ast.FunctionDef | ast.AsyncFunctionDef
                              | ast.ClassDef | ast.Assign | ast.AnnAssign):  # fmt: skip
                continue
            if self._agent_code(stmt):
                held |= _bound(stmt)
                continue
            for n in _bound(stmt):
                defs.setdefault(n, []).append(i)
        needed: set[int] = set()
        for fn in fns:
            ctx = _context_param(fn)
            a = fn.args
            parts_of = [*fn.body, *([fn.returns] if fn.returns else []), *a.defaults, *(d for d in a.kw_defaults if d)]
            parts_of += [x.annotation for x in [*a.posonlyargs, *a.args, *a.kwonlyargs, *[v for v in (a.vararg, a.kwarg)
                         if v]] if x.annotation is not None and x is not ctx]  # fmt: skip
            free = {n for part in parts_of for n in _reads(part)} - _locals(fn) - set(dir(builtins))
            missing = sorted(n for n in free if n not in defs and n not in self.functions)
            if missing:
                why = "made by the agent code" if set(missing) <= held else "not defined in this file"
                self.b.note(f"Tool '{fn.name}' uses {', '.join(missing)}, {why}: add it to tools/{self.key}_tools.py.")
            todo, seen = list(free), set()
            while todo:
                n = todo.pop()
                if n in seen:
                    continue
                seen.add(n)
                for i in defs.get(n, []):
                    if i not in needed:
                        needed.add(i)
                        todo += _reads(top[i])
        parts = [TOOLS_HEAD]
        imports = [top[i] for i in sorted(needed) if isinstance(top[i], ast.Import | ast.ImportFrom)]
        if imports:
            parts.append("".join(self._segment(x) + "\n" for x in imports))
        for i in sorted(needed):
            if not isinstance(top[i], ast.Import | ast.ImportFrom):
                parts.append("\n\n" + self._segment(top[i]) + "\n")
        for fn in fns:
            parts.append("\n\n" + self._tool_source(fn) + "\n")
        return "\n".join(p for p in parts[:2]) + "".join(parts[2:])

    def output_schema(self, node: ast.AST | None, label: str) -> dict[str, Any] | None:
        cls = self.classes.get(_name(node) or "") if node is not None else None
        if cls is None:
            if node is not None:
                self.b.note(f"Agent '{label}': its output type {ast.unparse(node)[:60]} was not carried over; set "
                            "an answer shape on the block.")  # fmt: skip
            return None
        props: dict[str, Any] = {}
        for stmt in cls.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                ann = ast.unparse(stmt.annotation)
                inner = ann.removeprefix("list[").removesuffix("]")
                if ann.startswith("list["):
                    props[stmt.target.id] = {"type": "array", "items": {"type": TYPES.get(inner, "string")}}
                else:
                    props[stmt.target.id] = {"type": TYPES.get(ann, "string")}
        return {"type": "object", "properties": props, "required": list(props)} if props else None

    # ---- steps ----
    def agent_step(self, var: str) -> None:
        call = self.agents[var]
        label = str(_const(_kw(call, "name")) or var)
        instructions = _const(_kw(call, "instructions"))
        if instructions is None and _kw(call, "instructions") is not None:
            self.b.note(f"Agent '{label}': its instructions are computed by code; write them on the block.")
        tools, specialists = [], []
        tools_node = _kw(call, "tools")
        for item in tools_node.elts if isinstance(tools_node, ast.List) else []:
            target = item.func if isinstance(item, ast.Call) else item
            n = _name(target)
            if isinstance(item, ast.Call) and n == "as_tool" and isinstance(target, ast.Attribute):
                specialists.append(_name(target.value))
            elif n in HOSTED:
                self.b.note(f"Agent '{label}' used {HOSTED[n]}.")
            elif n and (rid := self.tool(n)):
                tools.append(rid)
            elif n:
                self.b.note(f"Agent '{label}': the tool '{n}' is not defined in this file; add it as a tool.")
        handoffs = _kw(call, "handoffs")
        specialists += [_name(h) for h in (handoffs.elts if isinstance(handoffs, ast.List) else [])]
        specialists = [s for s in specialists if s in self.agents and s != var]
        for g in ("input_guardrails", "output_guardrails"):
            if _kw(call, g) is not None:
                self.b.note(f"Agent '{label}' had {g.replace('_', ' ')}: add a safety step (Prompt injection, "
                            "Harmful content or Answer shape) before or after it.")  # fmt: skip
        model = self.model(call, label)
        config: dict[str, Any] = {"system_prompt": str(instructions or "")}
        if specialists:
            subs = []
            for s in specialists:
                sub = self.agents[s]
                sub_label = str(_const(_kw(sub, "name")) or s)
                rid = f"r_{len(self.b.resources) + 1}"
                description = str(_const(_kw(sub, "handoff_description")) or f"Hand {sub_label} its part.")[:500]
                prompt = str(_const(_kw(sub, "instructions")) or "")
                config_sub = {"description": description, "system_prompt": prompt}
                self.b.resources.append(
                    {
                        "id": rid,
                        "type": "agent.subagent",
                        "name": ident(sub_label, self.b.names, "agent"),
                        "config": config_sub,
                    }
                )
                subs.append(rid)
            names = [str(_const(_kw(self.agents[s], "name")) or s) for s in specialists]
            self.b.note(
                f"Agent '{label}' handed off to {', '.join(names)}: it is a Planning Agent with them as "
                "specialists, so their answers come back to it instead of going straight to the person."
            )
            st = self.b.step(var, "agent.deep", label, config, label=label, model=model, tools=tools)
            st.subagents = subs
        else:
            schema = self.output_schema(_kw(call, "output_type"), label)
            if schema:
                config["response_format"] = schema
            st = self.b.step(var, "core.agent", label, config, label=label, model=model, tools=tools)
            if schema:  # the agent writes its typed answer here
                self.b.channels.setdefault("structured_response", {
                    "name": "structured_response", "type": {"type": "object"}, "io": "out"})  # fmt: skip
                self.b.output("structured_response")
                return

    def read(self) -> dict[str, str]:
        self.scan()
        handed = {_name(h) for c in self.agents.values() for h in getattr(_kw(c, "handoffs"), "elts", [])}
        for c in self.agents.values():
            for t in getattr(_kw(c, "tools"), "elts", []):
                if isinstance(t, ast.Call) and isinstance(t.func, ast.Attribute) and t.func.attr == "as_tool":
                    handed.add(_name(t.func.value))
        order = [a for a in self.order if a in self.agents] or [a for a in self.agents if a not in handed][-1:]
        if not order:
            order = list(self.agents)[:1]
        self.b.use_chat()
        previous = "START"
        for var in order:
            self.agent_step(var)
            self.b.after(var, previous)
            previous = var
        skipped = [a for a in self.agents if a not in order and a not in handed]
        if skipped:
            self.b.note(f"Agents not run by the code were left out: {', '.join(skipped)}.")
        if self.tool_rids:
            self.files[f"tools/{self.key}_tools.py"] = self.tools_module()
        return self.files


def read(text: str, *, wf_id: str, name: str | None) -> tuple[Builder, dict[str, str]]:
    if len(text) > MAX_SOURCE:
        raise ImportError_(f"the code is longer than {MAX_SOURCE} characters")
    b = Builder("OpenAI Agents SDK", wf_id, name or "Imported agents")
    files = _Reader(text, b, wf_id.lower()).read()
    return b, files
