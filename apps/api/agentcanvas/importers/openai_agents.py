"""OpenAI Agents SDK code (what OpenAI's Agent Builder exports, and what people write by hand) becomes a workflow.

The Python is read with `ast` and never run. What carries over:

- every `Agent(...)`: its name, `instructions` (text), `model`, `model_settings` (temperature, max tokens), `tools`
  and `output_type`, as an AI agent block with its model block;
- `@function_tool` functions, as Python tools: their source goes to the project's workspace
  (`tools/<workflow key>_tools.py`, written as a LangChain `@tool`), added when the workflow is created;
- `handoffs=[...]` and `other.as_tool(...)`: the agent becomes a Planning Agent and the others its specialists;
- the order of the `Runner.run(...)` calls (an Agent Builder export runs its agents one after another), else the
  agent no other agent hands off to.

Hosted tools (web search, file search, code interpreter), guardrails and branching code are listed in the report with
what to use instead.
"""

from __future__ import annotations

import ast
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
MAX_SOURCE = 500_000  # characters of Python in one file


def looks_like(text: Any) -> bool:
    return isinstance(text, str) and ("from agents import" in text or "import agents" in text) and "Agent(" in text


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
        self.order: list[str] = []  # agents in the order Runner.run calls them
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
                if owner is not None and _name(owner) == "Runner" and node.args:
                    first = _name(node.args[0])
                    if first and first not in self.order:
                        self.order.append(first)
            elif isinstance(node, ast.If | ast.Match) and self._inside_workflow(node):
                self.b.note("The code branches (if/else); the workflow runs its agents in order. Add a Choose a path "
                            "step where the original chose between agents.")  # fmt: skip
        if not self.agents:
            raise ImportError_("no Agent(...) found in this code")

    def _inside_workflow(self, node: ast.AST) -> bool:
        return "Runner." in ast.unparse(node)[:2000]

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
        module = f"tools/{self.key}_tools.py"
        rid = f"r_{len(self.b.resources) + 1}"
        self.b.resources.append({"id": rid, "type": "tool.python", "name": ident(name, self.b.names, "tool"),
                                 "config": {"import_path": f"tools.{self.key}_tools:{name}"}})  # fmt: skip
        body = ast.get_source_segment(self.source, fn) or ""
        lines = [ln for ln in body.splitlines() if not ln.lstrip().startswith("@function_tool")]
        self.files.setdefault(module, '"""Tools imported from OpenAI Agents SDK code."""\n\nfrom langchain_core.tools '
                              "import tool\n")  # fmt: skip
        self.files[module] += "\n\n@tool\n" + "\n".join(lines) + "\n"
        self.tool_rids[name] = rid
        return rid

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
        return self.files


def read(text: str, *, wf_id: str, name: str | None) -> tuple[Builder, dict[str, str]]:
    if len(text) > MAX_SOURCE:
        raise ImportError_(f"the code is longer than {MAX_SOURCE} characters")
    b = Builder("OpenAI Agents SDK", wf_id, name or "Imported agents")
    files = _Reader(text, b, wf_id.lower()).read()
    return b, files
