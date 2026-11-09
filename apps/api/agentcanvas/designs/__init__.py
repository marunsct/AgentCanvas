"""Design any agent (S21): the canvas Agent block, generated as a native agent for another framework.

An Agent block (`core.agent`) whose `engine` names one of `AGENT_FRAMEWORKS` is turned into a small Python module in
that framework's own style (`generate`): `build(tools, model=, base_url=, api_key=)` makes the agent and `ask(agent,
text)` runs it once. The same module is used three ways:

* in a LangGraph flow (mixed flows): the host runs it as one step, with the canvas tools as plain functions and the
  canvas model served to it through an OpenAI- or Anthropic-compatible address (`adapters.langchain.bridge`);
* in a single-agent export (`export_files`): a native project with its own runner, pinned packages and tests;
* in the code view, so a person sees exactly what runs.

Mixed-flow export (S28): five of the frameworks pin packages that clash with LangGraph's (`own_env`), so in an
exported project, and on a server that cannot import them, their agents run as *parts*: the same module in a Python
environment of its own, driven over a pipe by `adapters.langchain.parts`. Each part has a typed contract
(`contract`): text in, and its structured output (or text) out, checked where the answer crosses back.

This module is framework-neutral: it writes code as text and never imports the frameworks it writes for.
"""

from __future__ import annotations

import importlib.util
import json
import keyword
import os
import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any, Literal

from agentcanvas.ir.models import Node, Workflow
from agentcanvas.sampling import without_sampling


@dataclass(frozen=True)
class AgentFramework:
    id: str
    label: str
    packages: tuple[str, ...]  # what a single-agent export installs (CI's `frameworks` job uses the same pins)
    module: str  # importable when the framework is installed
    api: Literal["openai", "anthropic"]  # the model API the generated agent speaks
    structured_output: bool = True  # can give a typed answer (the block's `response_format`)
    own_env: bool = False  # its pins clash with the LangGraph host's (S28): an export runs it as a part of its own


AGENT_FRAMEWORKS: dict[str, AgentFramework] = {
    f.id: f
    for f in [
        AgentFramework("openai_agents", "OpenAI Agents SDK", ("openai-agents==0.24.0",), "agents", "openai"),
        AgentFramework("crewai", "CrewAI", ("crewai==1.15.33",), "crewai", "openai", own_env=True),
        AgentFramework("google_adk", "Google ADK", ("google-adk==2.13.0", "litellm==1.105.7"), "google.adk", "openai",
                       own_env=True),
        AgentFramework("pydantic_ai", "Pydantic AI", ("pydantic-ai-slim[openai]==2.66.0",), "pydantic_ai", "openai"),
        AgentFramework("agno", "Agno", ("agno==3.1.11", "openai==2.54.0"), "agno", "openai", own_env=True),
        AgentFramework("strands", "Strands Agents", ("strands-agents[openai]==1.62.0",), "strands", "openai",
                       own_env=True),
        AgentFramework(
            "llamaindex", "LlamaIndex",
            ("llama-index-core==0.14.25", "llama-index-workflows==2.25.0", "llama-index-llms-openai-like==0.8.1"),
            "llama_index.core", "openai", own_env=True,
        ),
        AgentFramework(
            "microsoft_agent_framework", "Microsoft Agent Framework",
            ("agent-framework-core==1.23.0", "agent-framework-openai==1.17.0"), "agent_framework", "openai",
        ),
        AgentFramework("claude_agent_sdk", "Claude Agent SDK", ("claude-agent-sdk==0.2.174",), "claude_agent_sdk",
                       "anthropic"),
    ]
}  # fmt: skip


FRAMEWORKS_DIR = Path(__file__).parents[2] / ".frameworks"  # `make framework FW=<id>`: one environment each


@cache
def importable(framework: str) -> bool:
    """Whether `framework` can be imported by this server itself."""
    try:
        return importlib.util.find_spec(AGENT_FRAMEWORKS[framework].module) is not None
    except (ImportError, ValueError):
        return False


def part_python(framework: str) -> str | None:
    """The Python of `framework`'s own environment on this server (S28), if there is one: AGENTCANVAS_<ID>_PYTHON,
    else `.frameworks/<id>` (made by `make framework FW=<id>`; CrewAI's is the one its flows use too)."""
    found = os.environ.get(f"AGENTCANVAS_{framework.upper()}_PYTHON") or str(FRAMEWORKS_DIR / framework / "bin/python")
    return found if Path(found).is_file() else None


def installed(framework: str) -> bool:
    """Whether drawn agents for `framework` run on this server: in the server's process, or as a part (S28)."""
    return importable(framework) or part_python(framework) is not None


# Where an exported agent reaches each catalog provider over the OpenAI API (None: the model's base_url is needed).
# Inside AgentCanvas every provider works with every framework: the host serves the canvas model to the agent.
OPENAI_URLS: dict[str, str | None] = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1/",
    "google_genai": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "mistralai": "https://api.mistral.ai/v1",
    "groq": "https://api.groq.com/openai/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "xai": "https://api.x.ai/v1",
    "ollama": "http://localhost:11434/v1",
    "lmstudio": "http://localhost:1234/v1",
    "vllm": "http://localhost:8000/v1",
    "llamacpp": "http://localhost:8080/v1",
    "openai_compatible": None,
}
ANTHROPIC_URL = "https://api.anthropic.com"
KEY_ENVS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google_genai": "GOOGLE_API_KEY",
    "mistralai": "MISTRAL_API_KEY",
    "groq": "GROQ_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "xai": "XAI_API_KEY",
}


# ------------------------------------------------------------------ the design ----
@dataclass
class ModelChoice:
    resource: str  # the canvas model block's name
    provider: str
    name: str
    base_url: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None

    @property
    def key_env(self) -> str:
        return KEY_ENVS.get(self.provider, "MODEL_API_KEY")


@dataclass
class ToolChoice:
    resource: str  # the canvas tool block's name
    type: str  # tool.python, tool.http or mcp.server
    config: dict[str, Any]


@dataclass
class AgentDesign:
    """What the canvas says about one Agent block, in framework-neutral terms."""

    name: str
    workflow: str
    instructions: str
    model: ModelChoice | None
    tools: list[ToolChoice] = field(default_factory=list)
    approve_tools: list[str] = field(default_factory=list)
    output_schema: dict[str, Any] | None = None
    middleware: list[str] = field(default_factory=list)
    other_tools: list[str] = field(default_factory=list)  # wired tools of kinds a design cannot carry (e.g. memory)


TEXT_IN: dict[str, Any] = {
    "type": "object",
    "properties": {"text": {"type": "string", "description": "the conversation, as the host hands it over"}},
    "required": ["text"],
}
TEXT_OUT: dict[str, Any] = {"type": "string"}


def contract(design: AgentDesign) -> dict[str, Any]:
    """The typed boundary of an agent designed for another framework (S28): what crosses into it and back out."""
    return {"input": TEXT_IN, "output": design.output_schema or TEXT_OUT}


DESIGN_TYPES = {"core.agent"}  # blocks that can be designed for another framework
TOOL_TYPES = {"tool.python", "tool.http", "mcp.server"}


def design_for(wf: Workflow, node: Node) -> AgentDesign:
    """The design of an Agent block: its settings and the model, tools and add-ons wired into it."""
    by_id = {r.id: r for r in wf.resources}
    wired: dict[str, list[Any]] = {}
    for e in sorted((e for e in wf.edges if e.kind == "wiring" and e.target.node == node.id),
                    key=lambda e: (e.order is None, e.order or 0)):  # fmt: skip
        if e.source.node in by_id:
            wired.setdefault(e.target.port or "", []).append(by_id[e.source.node])
    model = None
    for r in wired.get("model", [])[:1]:
        provider, _, name = str(r.config.get("model", "")).partition(":")
        # W020: no temperature for a model that refuses it (the provider would answer with a 400)
        kept, _ = without_sampling(str(r.config.get("model", "")), {"temperature": r.config.get("temperature")})
        model = ModelChoice(r.name, provider, name, r.config.get("base_url"), kept.get("temperature"),
                            r.config.get("max_tokens"))  # fmt: skip
    tools = [ToolChoice(r.name, r.type, dict(r.config)) for r in wired.get("tools", []) if r.type in TOOL_TYPES]
    cfg = node.config
    return AgentDesign(
        name=node.name,
        workflow=wf.name,
        instructions=str(cfg.get("system_prompt") or ""),
        model=model,
        tools=tools,
        approve_tools=list(cfg.get("approve_tools") or []),
        output_schema=cfg.get("response_format") or None,
        middleware=[r.name for r in wired.get("middleware", [])],
        other_tools=[r.name for r in wired.get("tools", []) if r.type not in TOOL_TYPES],
    )


def problems(framework: str, design: AgentDesign) -> list[str]:
    """What keeps this design from running as a `framework` agent inside AgentCanvas, in plain words."""
    fw = AGENT_FRAMEWORKS[framework]
    found = []
    if design.middleware:
        found.append(f"{fw.label} agents cannot use add-ons (middleware): remove {', '.join(design.middleware)}, "
                     "or design this agent for LangGraph")  # fmt: skip
    if design.other_tools:
        found.append(f"{fw.label} agents can use Python, web API and MCP tools, not {', '.join(design.other_tools)}")
    if design.output_schema is not None:
        if not fw.structured_output:
            found.append(f"{fw.label} agents cannot give structured output yet")
        else:
            why = schema_problem(design.output_schema)
            if why:
                found.append(f"the structured output schema {why}")
    return found


def export_problems(framework: str, design: AgentDesign) -> list[str]:
    """What keeps this design from being exported as a native `framework` project (beyond `problems`)."""
    fw = AGENT_FRAMEWORKS[framework]
    found = problems(framework, design)
    m = design.model
    if m is None:
        found.append("connect an AI model block to the agent")
    elif fw.api == "anthropic" and m.provider != "anthropic":
        found.append(f"a {fw.label} export talks to Anthropic models only; this agent uses '{m.provider}'")
    elif fw.api == "openai" and m.provider not in OPENAI_URLS:
        found.append(f"an export reaches models over the OpenAI API, which '{m.provider}' does not offer here")
    elif fw.api == "openai" and OPENAI_URLS[m.provider] is None and not m.base_url:
        found.append("the model block needs a base URL")
    for t in design.tools:
        if t.type == "mcp.server" and t.config.get("transport", "http") != "http":
            found.append(f"the MCP server '{t.resource}' starts a local process; exports connect to http servers only")
    return found


# ------------------------------------------------------------------ code ----
def article(label: str) -> str:
    return "an" if label[:1] in "AEIOU" else "a"


def literal(text: str) -> str:
    """A Python string literal that reads back as exactly `text`, for every code generator: triple quotes for
    multi-line text when nothing in it would change on the way back (a backslash escape, a `\\r` the tokenizer turns
    into `\\n`, a quote that ends it, a control character), else one double-quoted line.

    Triple-quoted text keeps its lines as they are, so it belongs at module level or in code that is never
    re-indented: a generator that indents a body line by line must use the one-line form there."""
    if (
        "\n" in text
        and '"""' not in text
        and "\\" not in text
        and not text.endswith('"')
        and text.replace("\n", "").replace("\t", "").isprintable()
    ):
        return '"""' + text + '"""'
    return json.dumps(text, ensure_ascii=False)


# field names that would hide a name the generated classes' annotations refer to
_TAKEN = frozenset({"str", "int", "float", "bool", "list", "dict", "None", "Any", "Literal", "Field", "BaseModel"})
_JSON_TYPES = {"string": "str", "integer": "int", "number": "float", "boolean": "bool"}


def schema_problem(schema: dict[str, Any]) -> str | None:
    """Why a structured-output schema cannot be written as Pydantic models (None when it can)."""
    if schema.get("type") != "object" or not isinstance(schema.get("properties"), dict):
        return "must be an object with properties"
    for name, prop in schema["properties"].items():
        if not name.isidentifier() or keyword.iskeyword(name) or name.startswith(("_", "model_")) or name in _TAKEN:
            return f"has a field '{name}' that is not a valid name"
        why = _type_problem(prop)
        if why:
            return f"field '{name}' {why}"
    return None


def _type_problem(prop: Any) -> str | None:
    if not isinstance(prop, dict):
        return "has no type"
    if "enum" in prop:
        return None if all(isinstance(v, str | int | float | bool) for v in prop["enum"]) else "has an unusual enum"
    t = prop.get("type")
    if isinstance(t, list):  # ["string", "null"]: an optional value of one type
        rest = [x for x in t if x != "null"]
        return _type_problem({**prop, "type": rest[0]}) if len(rest) == 1 else f"has several types ({t!r})"
    if t in _JSON_TYPES:
        return None
    if t == "array":
        return _type_problem(prop.get("items") or {"type": "string"})
    if t == "object":
        return schema_problem(prop) if prop.get("properties") else None
    return f"has a type this generator does not know ({t!r})"


def _camel(name: str) -> str:
    return "".join(part[:1].upper() + part[1:] for part in re.split(r"[^A-Za-z0-9]+", name) if part)


def output_models(schema: dict[str, Any], name: str = "Answer") -> str:
    """Pydantic models for a JSON Schema object (nested objects become their own classes, written first)."""
    classes: list[str] = []

    def type_of(prop: dict[str, Any], owner: str, field_name: str) -> str:
        if "enum" in prop:
            return "Literal[" + ", ".join(json.dumps(v) if isinstance(v, str) else repr(v) for v in prop["enum"]) + "]"
        t = prop.get("type")
        if isinstance(t, list):  # ["string", "null"]
            rest = [x for x in t if x != "null"]
            return f"{type_of({**prop, 'type': rest[0]}, owner, field_name)} | None" if rest else "None"
        if t == "array":
            return f"list[{type_of(prop.get('items') or {'type': 'string'}, owner, field_name)}]"
        if t == "object":
            if not prop.get("properties"):
                return "dict[str, Any]"
            return write(prop, owner + _camel(field_name))
        return _JSON_TYPES.get(str(t), "Any")

    def write(obj: dict[str, Any], cls: str) -> str:
        required = set(obj.get("required") or [])
        lines = [f"class {cls}(BaseModel):"]
        if obj.get("description"):
            lines.append(f"    {literal(str(obj['description']))}")
            lines.append("")
        for fname, prop in obj["properties"].items():
            hint = type_of(prop, cls, fname)
            desc = prop.get("description")
            if fname in required:
                value = f" = Field(description={literal(str(desc))})" if desc else ""
            else:
                hint += " | None"
                value = f" = Field(default=None, description={literal(str(desc))})" if desc else " = None"
            lines.append(f"    {fname}: {hint}{value}")
        if len(lines) == 1:
            lines.append("    pass")
        classes.append("\n".join(lines))
        return cls

    write(schema, name)
    return "\n\n\n".join(classes)


def generate(framework: str, design: AgentDesign) -> str:
    """The agent as a Python module in `framework`'s own style."""
    fw = AGENT_FRAMEWORKS[framework]
    m = design.model or ModelChoice("model", "openai", "gpt-5.5")
    structured = design.output_schema is not None and fw.structured_output and not schema_problem(design.output_schema)
    if fw.api == "anthropic":
        base_url = ANTHROPIC_URL
    else:
        base_url = m.base_url or OPENAI_URLS.get(m.provider) or "http://localhost:8000/v1"
    code = _WRITERS[framework](_Ctx(m, structured, bool(design.tools)))
    settings = [f"TEMPERATURE = {m.temperature!r}"] if m.temperature is not None else []
    settings += [f"MAX_TOKENS = {m.max_tokens!r}"] if m.max_tokens is not None else []
    models = output_models(design.output_schema) if structured and design.output_schema else ""
    typing_names = [n for n in ("Any", "Literal") if re.search(rf"\b{n}\[", models)]
    third_party = list(code.imports)
    if models:
        third_party.append("from pydantic import BaseModel" + (", Field" if "Field(" in models else ""))
    imports = sorted(["import os", *code.stdlib])
    if typing_names:
        imports.append(f"from typing import {', '.join(typing_names)}")
    head = [
        f'"""{design.name}: {article(fw.label)} {fw.label} agent designed on AgentCanvas.',
        "",
        f'Generated from the block "{design.name}" of the workflow {json.dumps(design.workflow, ensure_ascii=False)}.',
        "Change the agent on the canvas and generate it again rather than editing this file.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        *imports,
        "",
        *sorted(third_party),
        *(["", *code.after_imports] if code.after_imports else []),
        "",
        f"NAME = {json.dumps(design.name)}",
        f"INSTRUCTIONS = {literal(design.instructions)}",
        f"MODEL = {json.dumps(m.name)}",
        f"BASE_URL = {json.dumps(base_url)}",
        f"API_KEY_ENV = {json.dumps(m.key_env)}",
        *settings,
    ]
    parts = ["\n".join(head)]
    if models:
        parts.append(models)
    parts.extend(code.helpers)
    parts.append(
        "def build(tools=(), *, model: str = MODEL, base_url: str | None = None, api_key: str | None = None):\n"
        '    """The agent. `tools` are plain Python functions it may call: their names, docstrings and type hints\n'
        '    tell the model what they do."""\n'
        '    base_url = base_url or os.environ.get("MODEL_BASE_URL") or BASE_URL\n'
        '    api_key = api_key or os.environ.get(API_KEY_ENV) or "not-needed"\n' + code.build
    )
    parts.append(
        "async def ask(agent, text: str):\n"
        f'    """One run of the agent on `text`: its answer ({"an Answer" if structured else "text"})."""\n' + code.ask
    )
    return "\n\n\n".join(parts) + "\n"


@dataclass
class _Ctx:
    model: ModelChoice
    structured: bool
    tools: bool

    def kw(self, mapping: dict[str, str], indent: int = 12) -> str:
        """Keyword arguments for the model's optional settings, e.g. `temperature=TEMPERATURE,` lines."""
        pad = " " * indent
        out = ""
        if self.model.temperature is not None and "temperature" in mapping:
            out += f"{pad}{mapping['temperature']}=TEMPERATURE,\n"
        if self.model.max_tokens is not None and "max_tokens" in mapping:
            out += f"{pad}{mapping['max_tokens']}=MAX_TOKENS,\n"
        return out

    def settings_dict(self) -> str:
        items = []
        if self.model.temperature is not None:
            items.append('"temperature": TEMPERATURE')
        if self.model.max_tokens is not None:
            items.append('"max_tokens": MAX_TOKENS')
        return "{" + ", ".join(items) + "}" if items else ""


@dataclass
class _Code:
    imports: list[str]
    build: str
    ask: str
    stdlib: list[str] = field(default_factory=list)
    after_imports: list[str] = field(default_factory=list)
    helpers: list[str] = field(default_factory=list)


def _openai_agents(ctx: _Ctx) -> _Code:
    settings = ", ".join(
        line.strip().rstrip(",")
        for line in ctx.kw({"temperature": "temperature", "max_tokens": "max_tokens"}).splitlines()
    )
    lines = [
        "    client = AsyncOpenAI(base_url=base_url, api_key=api_key)",
        "    return Agent(",
        "        name=NAME,",
        "        instructions=INSTRUCTIONS,",
        "        model=OpenAIChatCompletionsModel(model=model, openai_client=client),",
    ]
    if settings:
        lines.append(f"        model_settings=ModelSettings({settings}),")
    lines.append("        tools=[function_tool(t) for t in tools],")
    if ctx.structured:
        lines.append("        output_type=Answer,")
    lines.append("    )")
    names = ["Agent", *(["ModelSettings"] if settings else []), "OpenAIChatCompletionsModel", "Runner",
             "function_tool", "set_tracing_disabled"]  # fmt: skip
    return _Code(
        imports=[f"from agents import {', '.join(names)}", "from openai import AsyncOpenAI"],
        after_imports=["set_tracing_disabled(True)  # runs are traced by AgentCanvas or your own tracer, not OpenAI's"],
        build="\n".join(lines),
        ask="    return (await Runner.run(agent, text)).final_output",
    )


def _pydantic_ai(ctx: _Ctx) -> _Code:
    lines = [
        "    provider = OpenAIProvider(base_url=base_url, api_key=api_key)",
        "    return Agent(",
        "        OpenAIChatModel(model, provider=provider),",
        "        name=NAME,",
        "        instructions=INSTRUCTIONS,",
        "        tools=list(tools),",
    ]
    if ctx.structured:
        lines.append("        output_type=NativeOutput(Answer),")
    if ctx.settings_dict():
        lines.append(f"        model_settings={ctx.settings_dict()},")
    lines.append("    )")
    return _Code(
        imports=[
            "from pydantic_ai import Agent" + (", NativeOutput" if ctx.structured else ""),
            "from pydantic_ai.models.openai import OpenAIChatModel",
            "from pydantic_ai.providers.openai import OpenAIProvider",
        ],
        build="\n".join(lines),
        ask="    return (await agent.run(text)).output",
    )


def _agno(ctx: _Ctx) -> _Code:
    lines = [
        "    return Agent(",
        "        name=NAME,",
        "        model=OpenAILike(",
        "            id=model,",
        "            base_url=base_url,",
        "            api_key=api_key,",
        ctx.kw({"temperature": "temperature", "max_tokens": "max_tokens"}).rstrip("\n"),
        "        ),",
        "        instructions=INSTRUCTIONS,",
        "        tools=list(tools),",
        *(["        output_schema=Answer,"] if ctx.structured else []),
        "    )",
    ]
    return _Code(
        imports=[
            "from agno.agent import Agent",
            "from agno.models.openai.like import OpenAILike",
        ],
        build="\n".join(line for line in lines if line),
        ask="    return (await agent.arun(text)).content",
    )


def _strands(ctx: _Ctx) -> _Code:
    params = ctx.settings_dict()
    lines = [
        "    return Agent(",
        "        name=NAME,",
        "        model=OpenAIModel(",
        '            client_args={"base_url": base_url, "api_key": api_key},',
        "            model_id=model,",
        *([f"            params={params},"] if params else []),
        "        ),",
        "        system_prompt=INSTRUCTIONS,",
        "        tools=[tool(t) for t in tools],",
        *(["        structured_output_model=Answer,"] if ctx.structured else []),
        "        callback_handler=None,  # quiet: no printing while it works",
        "    )",
    ]
    ask = ["    result = await agent.invoke_async(text)"]
    ask.append("    return result.structured_output" if ctx.structured else "    return str(result).strip()")
    return _Code(
        imports=[
            "from strands import Agent, tool",
            "from strands.models.openai import OpenAIModel",
        ],
        build="\n".join(lines),
        ask="\n".join(ask),
    )


def _google_adk(ctx: _Ctx) -> _Code:
    lines = [
        "    return LlmAgent(",
        "        name=NAME,",
        "        model=LiteLlm(",
        '            model=f"openai/{model}",',
        "            api_base=base_url,",
        "            api_key=api_key,",
        "            drop_params=True,  # LiteLLM leaves out settings a model does not take",
        ctx.kw({"temperature": "temperature", "max_tokens": "max_tokens"}).rstrip("\n"),
        "        ),",
        "        instruction=instructions,",
        "        tools=list(tools),",
        *(["        output_schema=Answer,"] if ctx.structured else []),
        "    )",
    ]
    ask = [
        '    runner = InMemoryRunner(agent=agent, app_name="agentcanvas")',
        '    session = await runner.session_service.create_session(app_name="agentcanvas", user_id="user")',
        '    message = types.Content(role="user", parts=[types.Part(text=text)])',
        '    answer = ""',
        '    async for event in runner.run_async(user_id="user", session_id=session.id, new_message=message):',
        "        if event.is_final_response() and event.content and event.content.parts:",
        '            answer = "".join(part.text or "" for part in event.content.parts)',
        "    return Answer.model_validate_json(answer)" if ctx.structured else "    return answer",
    ]
    helper = (
        "def instructions(context) -> str:\n"
        '    """The instructions as a function, so ADK does not read {braces} in them as session state."""\n'
        "    return INSTRUCTIONS"
    )
    return _Code(
        imports=[
            "from google.adk.agents import LlmAgent",
            "from google.adk.models.lite_llm import LiteLlm",
            "from google.adk.runners import InMemoryRunner",
            "from google.genai import types",
        ],
        helpers=[helper],
        build="\n".join(line for line in lines if line),
        ask="\n".join(ask),
    )


def _crewai(ctx: _Ctx) -> _Code:
    lines = [
        "    llm = LLM(",
        '        model=f"openai/{model}",',
        "        base_url=base_url,",
        "        api_key=api_key,",
        ctx.kw({"temperature": "temperature", "max_tokens": "max_tokens"}, 8).rstrip("\n"),
        "    )",
        "    return Agent(",
        '        role=NAME.replace("_", " "),',
        '        goal=INSTRUCTIONS or "Help the user.",',
        '        backstory="Designed on AgentCanvas.",',
        "        llm=llm,",
        "        tools=[tool(t.__name__)(t) for t in tools],",
        "        verbose=False,",
        "    )",
    ]
    if ctx.structured:
        ask = "    return (await agent.kickoff_async(text, response_format=Answer)).pydantic"
    else:
        ask = "    return (await agent.kickoff_async(text)).raw"
    return _Code(
        imports=["from crewai import LLM, Agent", "from crewai.tools import tool"],
        build="\n".join(line for line in lines if line),
        ask=ask,
    )


def _llamaindex(ctx: _Ctx) -> _Code:
    lines = [
        "    llm = OpenAILike(",
        "        model=model,",
        "        api_base=base_url,",
        "        api_key=api_key,",
        "        is_chat_model=True,",
        "        is_function_calling_model=True,",
        ctx.kw({"temperature": "temperature", "max_tokens": "max_tokens"}, 8).rstrip("\n"),
        "    )",
        "    return FunctionAgent(",
        "        name=NAME,",
        "        system_prompt=INSTRUCTIONS,",
        "        llm=llm,",
        "        tools=[FunctionTool.from_defaults(t) for t in tools],",
        *(["        output_cls=Answer,"] if ctx.structured else []),
        "    )",
    ]
    ask = ["    result = await agent.run(user_msg=text)"]
    if ctx.structured:
        ask += [
            "    answer = result.get_pydantic_model(Answer)",
            "    return answer if answer is not None else Answer.model_validate_json(str(result))",
        ]
    else:
        ask.append("    return str(result)")
    return _Code(
        imports=[
            "from llama_index.core.agent.workflow import FunctionAgent",
            "from llama_index.core.tools import FunctionTool",
            "from llama_index.llms.openai_like import OpenAILike",
        ],
        build="\n".join(line for line in lines if line),
        ask="\n".join(ask),
    )


def _microsoft(ctx: _Ctx) -> _Code:
    lines = [
        "    client = OpenAIChatCompletionClient(model, base_url=base_url, api_key=api_key)",
        "    return client.as_agent(",
        "        name=NAME,",
        "        instructions=INSTRUCTIONS,",
        "        tools=list(tools),",
        "    )",
    ]
    options = ctx.settings_dict()[1:-1].split(", ") if ctx.settings_dict() else []
    if ctx.structured:
        options.append('"response_format": Answer')
    ask = "    response = await agent.run(text)"
    if options:
        ask = "    options = {" + ", ".join(options) + "}\n    response = await agent.run(text, options=options)"
    ask += "\n    return response.value" if ctx.structured else "\n    return response.text"
    return _Code(
        imports=["from agent_framework.openai import OpenAIChatCompletionClient"],
        build="\n".join(lines),
        ask=ask,
    )


def _claude(ctx: _Ctx) -> _Code:
    lines = [
        '    server = create_sdk_mcp_server("canvas", tools=[sdk_tool(t) for t in tools])',
        "    return ClaudeAgentOptions(",
        "        system_prompt=INSTRUCTIONS,",
        "        model=model,",
        "        tools=[],  # no built-in tools (files, shell, web): only the ones designed on the canvas",
        '        mcp_servers={"canvas": server},',
        '        allowed_tools=[f"mcp__canvas__{t.__name__}" for t in tools],',
        "        setting_sources=[],  # ignore any Claude settings files on this machine",
        *(['        output_format={"type": "json_schema", "schema": Answer.model_json_schema()},']
          if ctx.structured else []),
        '        env={"ANTHROPIC_BASE_URL": base_url, "ANTHROPIC_API_KEY": api_key},',
        "    )",
    ]  # fmt: skip
    ask = [
        "    said: list[str] = []",
        "    async for message in query(prompt=text, options=agent):",
        "        if isinstance(message, AssistantMessage):",
        "            said += [block.text for block in message.content if isinstance(block, TextBlock)]",
        "        elif isinstance(message, ResultMessage):",
        "            if message.is_error:",
        '                raise RuntimeError(f"the agent stopped with an error: {message.result or message.subtype}")',
    ]
    if ctx.structured:
        ask += [
            "            if message.structured_output is not None:",
            "                return Answer.model_validate(message.structured_output)",
            '            return Answer.model_validate_json(message.result or "")',
        ]
    else:
        ask += ["            if message.result is not None:", "                return message.result"]
    if ctx.structured:
        ask.append('    raise RuntimeError("the agent gave no answer")')
    else:
        ask.append('    return "\\n".join(said)')
    helper = (
        "def sdk_tool(fn):\n"
        '    """A plain function as a tool of the in-process MCP server the Claude Agent SDK talks to."""\n'
        "    hints = typing.get_type_hints(fn)\n"
        "    params = {p: hints.get(p, str) for p in inspect.signature(fn).parameters}\n"
        "\n"
        "    async def handler(args: dict) -> dict:\n"
        "        result = fn(**args)\n"
        "        if inspect.isawaitable(result):\n"
        "            result = await result\n"
        '        return {"content": [{"type": "text", "text": str(result)}]}\n'
        "\n"
        "    return tool(fn.__name__, inspect.getdoc(fn) or fn.__name__, params)(handler)"
    )
    return _Code(
        stdlib=["import inspect", "import typing"],
        imports=[
            "from claude_agent_sdk import (\n    AssistantMessage,\n    ClaudeAgentOptions,\n    ResultMessage,\n"
            "    TextBlock,\n    create_sdk_mcp_server,\n    query,\n    tool,\n)",  # one entry, so imports sort
        ],
        helpers=[helper],
        build="\n".join(lines),
        ask="\n".join(ask),
    )


_WRITERS = {
    "openai_agents": _openai_agents,
    "pydantic_ai": _pydantic_ai,
    "agno": _agno,
    "strands": _strands,
    "google_adk": _google_adk,
    "crewai": _crewai,
    "llamaindex": _llamaindex,
    "microsoft_agent_framework": _microsoft,
    "claude_agent_sdk": _claude,
}
