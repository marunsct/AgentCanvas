"""Export a flow of the flow engine (Pydantic AI, Agno, Microsoft Agent Framework, Strands, LlamaIndex) as a
project of its own (S32).

The project holds the generated flow, the runtime it runs on (the flow engine, the framework's runtime and the
expression and knowledge-search modules, vendored), a host that reaches each model over its provider's
OpenAI-compatible API, a command-line runner that saves a paused run in a file and continues it, and tests that
replay the workflow's saved test cases with a scripted model. Nothing from LangChain is needed.
"""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentcanvas.adapters.agno.codegen import AGNO_VERSION, OPENAI_VERSION
from agentcanvas.adapters.langchain.packager import (
    RESERVED,
    _dist,
    _exportable_cases,
    _secret_names,
    _version,
    gaps_md,
    package_name,
)
from agentcanvas.adapters.llamaindex.codegen import LLAMA_INDEX_CORE_VERSION, OPENAI_LIKE_VERSION, WORKFLOWS_VERSION
from agentcanvas.adapters.microsoft_agent_framework.codegen import (
    AGENT_FRAMEWORK_OPENAI_VERSION,
    AGENT_FRAMEWORK_VERSION,
)
from agentcanvas.adapters.openai_agents.packager import model_specs
from agentcanvas.adapters.pydantic_ai.codegen import PYDANTIC_AI_VERSION
from agentcanvas.adapters.strands.codegen import STRANDS_VERSION
from agentcanvas.errors import InvalidInput
from agentcanvas.ir.models import Workflow
from agentcanvas.tomltext import toml_string
from agentcanvas.validation.analysis import refuse_plain_secrets
from agentcanvas.workspace import bundle

API_ROOT = Path(__file__).parents[1]  # the `agentcanvas` package directory
PINNED = ["jsonschema", "cel-python", "jinja2"]


@dataclass
class Target:
    label: str
    slug: str  # the project folder's suffix
    pins: list[str]
    docs: str
    how: str  # what the README says about the flow
    models: str  # the host's model code (make_model, scripted_model)


PYDANTIC_AI_MODELS = '''from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider


def make_model(resource: str, spec: dict[str, Any], settings: dict[str, Any]) -> Any:
    """The model block `resource` as a Pydantic AI chat model over its provider's OpenAI-compatible API."""
    env = resource.upper()
    provider = OpenAIProvider(
        base_url=os.environ.get(f"{env}_BASE_URL") or os.environ.get("MODEL_BASE_URL") or spec["base_url"],
        api_key=os.environ.get(spec["key_env"]) or "not-needed",
    )
    return OpenAIChatModel(os.environ.get(f"{env}_MODEL") or spec["model"], provider=provider,
                           settings=settings or None)  # type: ignore[arg-type]


def scripted_model(replies: list[Any]) -> Any:
    """Scripted replies, as the canvas's mock mode gives them: the n-th model turn of an agent's run gets reply n
    (the last one repeats). A reply is text, or {"content": ..., "tool_calls": [{"name": ..., "args": {...}}]}."""

    def answer(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        turn = sum(1 for m in messages if isinstance(m, ModelResponse))
        reply = (replies or [""])[min(turn, len(replies or [""]) - 1)]
        calls = reply.get("tool_calls") or [] if isinstance(reply, dict) else []
        text = reply.get("content", "") if isinstance(reply, dict) else str(reply)
        parts: list[Any] = [TextPart(text)] if text or not calls else []
        parts += [ToolCallPart(c["name"], c.get("args") or {}, tool_call_id=f"mock_{turn}_{i}")
                  for i, c in enumerate(calls)]
        return ModelResponse(parts=parts)

    return FunctionModel(answer)
'''

AGNO_MODELS = '''import httpx
from agno.models.openai.like import OpenAILike


def make_model(resource: str, spec: dict[str, Any], settings: dict[str, Any]) -> Any:
    """The model block `resource` as an Agno model over its provider's OpenAI-compatible API."""
    env = resource.upper()
    return OpenAILike(
        id=os.environ.get(f"{env}_MODEL") or spec["model"],
        base_url=os.environ.get(f"{env}_BASE_URL") or os.environ.get("MODEL_BASE_URL") or spec["base_url"],
        api_key=os.environ.get(spec["key_env"]) or "not-needed",
        **settings,
    )


def scripted_model(replies: list[Any]) -> Any:
    """Scripted replies, as the canvas's mock mode gives them: the n-th model turn of an agent's run gets reply n
    (the last one repeats). A reply is text, or {"content": ..., "tool_calls": [{"name": ..., "args": {...}}]}.
    They are answered by an OpenAI-compatible endpoint in memory, so the model is Agno's own."""

    def answer(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        turn = sum(1 for m in body.get("messages", []) if m.get("role") == "assistant")
        reply = (replies or [""])[min(turn, len(replies or [""]) - 1)]
        calls = reply.get("tool_calls") or [] if isinstance(reply, dict) else []
        message: dict[str, Any] = {"role": "assistant",
                                   "content": reply.get("content", "") if isinstance(reply, dict) else str(reply)}
        if calls:
            message["tool_calls"] = [{"id": f"mock_{turn}_{i}", "type": "function",
                                      "function": {"name": c["name"], "arguments": json.dumps(c.get("args") or {})}}
                                     for i, c in enumerate(calls)]
        return httpx.Response(200, json={
            "id": f"mock_{turn}", "object": "chat.completion", "created": 0, "model": "scripted",
            "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if calls else "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        })

    # Agno passes on only an httpx client (it ignores others), which openai 3 still takes: so httpx, not httpx2
    client = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    return OpenAILike(id="scripted", base_url="http://scripted/v1", api_key="scripted", http_client=client)
'''

AGENT_FRAMEWORK_MODELS = '''from agent_framework.openai import OpenAIChatCompletionClient

from agentcanvas.adapters.microsoft_agent_framework.runtime import Scripted, Settings


def make_model(resource: str, spec: dict[str, Any], settings: dict[str, Any]) -> Any:
    """The model block `resource` as an Agent Framework chat client over its provider's OpenAI-compatible API."""
    env = resource.upper()
    return OpenAIChatCompletionClient(
        os.environ.get(f"{env}_MODEL") or spec["model"],
        base_url=os.environ.get(f"{env}_BASE_URL") or os.environ.get("MODEL_BASE_URL") or spec["base_url"],
        api_key=os.environ.get(spec["key_env"]) or "not-needed",
        middleware=[Settings(settings)] if settings else None,
    )


def scripted_model(replies: list[Any]) -> Any:
    """Scripted replies, as the canvas's mock mode gives them (see `Scripted`): the chat client is the Agent
    Framework's own, and nothing reaches a provider."""
    return OpenAIChatCompletionClient("scripted", base_url="http://scripted/v1", api_key="scripted",
                                      middleware=[Scripted(replies)])
'''

STRANDS_MODELS = '''import httpx2
from openai import AsyncOpenAI
from strands.models.openai import OpenAIModel


def make_model(resource: str, spec: dict[str, Any], settings: dict[str, Any]) -> Any:
    """The model block `resource` as a Strands `OpenAIModel` over its provider's OpenAI-compatible API."""
    env = resource.upper()
    client_args = {
        "base_url": os.environ.get(f"{env}_BASE_URL") or os.environ.get("MODEL_BASE_URL") or spec["base_url"],
        "api_key": os.environ.get(spec["key_env"]) or "not-needed",
    }
    return OpenAIModel(client_args=client_args, model_id=os.environ.get(f"{env}_MODEL") or spec["model"],
                       params=settings or None)


def scripted_model(replies: list[Any]) -> Any:
    """Scripted replies (see `_streamed`): the model is Strands' own, and nothing reaches a provider."""
    client = AsyncOpenAI(base_url="http://scripted/v1", api_key="scripted", http_client=_streamed(replies))
    return OpenAIModel(client=client, model_id="scripted")


def _streamed(replies: list[Any]) -> Any:
    """An OpenAI-compatible endpoint in memory that streams scripted replies, as the canvas's mock mode gives them:
    the n-th model turn of an agent's run gets reply n (the last one repeats). A reply is text, or
    {"content": ..., "tool_calls": [{"name": ..., "args": {...}}]}."""

    def answer(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        turn = sum(1 for m in body.get("messages", []) if m.get("role") == "assistant")
        reply = (replies or [""])[min(turn, len(replies or [""]) - 1)]
        calls = reply.get("tool_calls") or [] if isinstance(reply, dict) else []
        text = reply.get("content", "") if isinstance(reply, dict) else str(reply)
        base = {"id": f"mock_{turn}", "object": "chat.completion.chunk", "created": 0, "model": "scripted"}
        deltas: list[dict[str, Any]] = [{"role": "assistant", "content": text}]
        deltas += [{"tool_calls": [{"index": i, "id": f"mock_{turn}_{i}", "type": "function",
                                    "function": {"name": c["name"], "arguments": json.dumps(c.get("args") or {})}}]}
                   for i, c in enumerate(calls)]
        chunks = [{**base, "choices": [{"index": 0, "delta": d, "finish_reason": None}]} for d in deltas]
        chunks.append({**base, "choices": [{"index": 0, "delta": {},
                                            "finish_reason": "tool_calls" if calls else "stop"}]})
        chunks.append({**base, "choices": [], "usage": {"prompt_tokens": 0, "completion_tokens": 0,
                                                        "total_tokens": 0}})
        data = "".join(f"data: {json.dumps(c)}\\n\\n" for c in chunks) + "data: [DONE]\\n\\n"
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=data.encode())

    return httpx2.AsyncClient(transport=httpx2.MockTransport(answer))  # openai 3's own HTTP client
'''

LLAMAINDEX_MODELS = '''import httpx
from llama_index.llms.openai_like import OpenAILike


def make_model(resource: str, spec: dict[str, Any], settings: dict[str, Any]) -> Any:
    """The model block `resource` as a LlamaIndex `OpenAILike` LLM over its provider's OpenAI-compatible API."""
    env = resource.upper()
    return OpenAILike(
        model=os.environ.get(f"{env}_MODEL") or spec["model"],
        api_base=os.environ.get(f"{env}_BASE_URL") or os.environ.get("MODEL_BASE_URL") or spec["base_url"],
        api_key=os.environ.get(spec["key_env"]) or "not-needed",
        is_chat_model=True,
        is_function_calling_model=True,
        **settings,
    )


def scripted_model(replies: list[Any]) -> Any:
    """Scripted replies (see `_streamed`): the LLM is LlamaIndex's own, and nothing reaches a provider."""
    return OpenAILike(model="scripted", api_base="http://scripted/v1", api_key="scripted", is_chat_model=True,
                      is_function_calling_model=True, async_http_client=_streamed(replies))


def _streamed(replies: list[Any]) -> Any:
    """An OpenAI-compatible endpoint in memory that streams scripted replies, as the canvas's mock mode gives them:
    the n-th model turn of an agent's run gets reply n (the last one repeats). A reply is text, or
    {"content": ..., "tool_calls": [{"name": ..., "args": {...}}]}."""

    def answer(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        turn = sum(1 for m in body.get("messages", []) if m.get("role") == "assistant")
        reply = (replies or [""])[min(turn, len(replies or [""]) - 1)]
        calls = reply.get("tool_calls") or [] if isinstance(reply, dict) else []
        text = reply.get("content", "") if isinstance(reply, dict) else str(reply)
        base = {"id": f"mock_{turn}", "object": "chat.completion.chunk", "created": 0, "model": "scripted"}
        deltas: list[dict[str, Any]] = [{"role": "assistant", "content": text}]
        deltas += [{"tool_calls": [{"index": i, "id": f"mock_{turn}_{i}", "type": "function",
                                    "function": {"name": c["name"], "arguments": json.dumps(c.get("args") or {})}}]}
                   for i, c in enumerate(calls)]
        chunks = [{**base, "choices": [{"index": 0, "delta": d, "finish_reason": None}]} for d in deltas]
        chunks.append({**base, "choices": [{"index": 0, "delta": {},
                                            "finish_reason": "tool_calls" if calls else "stop"}]})
        chunks.append({**base, "choices": [], "usage": {"prompt_tokens": 0, "completion_tokens": 0,
                                                        "total_tokens": 0}})
        data = "".join(f"data: {json.dumps(c)}\\n\\n" for c in chunks) + "data: [DONE]\\n\\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=data.encode())

    return httpx.AsyncClient(transport=httpx.MockTransport(answer))
'''

TARGETS = {
    "pydantic_ai": Target(
        label="Pydantic AI",
        slug="pydantic-ai",
        pins=[f"pydantic-ai-slim[openai]=={PYDANTIC_AI_VERSION}"],
        docs="https://ai.pydantic.dev/",
        how="a Pydantic AI `Agent` for each AI block (an `OpenAIChatModel`, `Tool`s, `NativeOutput` for typed answers)",
        models=PYDANTIC_AI_MODELS,
    ),
    "agno": Target(
        label="Agno",
        slug="agno",
        pins=[f"agno=={AGNO_VERSION}", f"openai=={OPENAI_VERSION}"],
        docs="https://docs.agno.com/",
        how="an Agno `Agent` for each AI block (an `OpenAILike` model, tools, an `output_schema` for typed answers)",
        models=AGNO_MODELS,
    ),
    "microsoft_agent_framework": Target(
        label="Microsoft Agent Framework",
        slug="microsoft-agent-framework",
        pins=[
            f"agent-framework-core=={AGENT_FRAMEWORK_VERSION}",
            f"agent-framework-openai=={AGENT_FRAMEWORK_OPENAI_VERSION}",
        ],
        docs="https://learn.microsoft.com/agent-framework/",
        how="an Agent Framework `Agent` for each AI block (an `OpenAIChatCompletionClient`, tools, a `response_format` "
        "for typed answers)",
        models=AGENT_FRAMEWORK_MODELS,
    ),
    "strands": Target(
        label="Strands Agents",
        slug="strands",
        pins=[f"strands-agents[openai]=={STRANDS_VERSION}", f"openai=={OPENAI_VERSION}"],
        docs="https://strandsagents.com/",
        how="a Strands `Agent` for each AI block (an `OpenAIModel`, tools, an `AskBefore` hook for approvals, a "
        "`structured_output_model` for typed answers)",
        models=STRANDS_MODELS,
    ),
    "llamaindex": Target(
        label="LlamaIndex",
        slug="llamaindex",
        pins=[
            f"llama-index-core=={LLAMA_INDEX_CORE_VERSION}",
            f"llama-index-workflows=={WORKFLOWS_VERSION}",
            f"llama-index-llms-openai-like=={OPENAI_LIKE_VERSION}",
        ],
        docs="https://docs.llamaindex.ai/",
        how="a LlamaIndex `FunctionAgent` for each AI block (an `OpenAILike` LLM, tools, an `output_cls` for typed "
        "answers)",
        models=LLAMAINDEX_MODELS,
    ),
}


def flow_package(name: str, workspace: set[str] | None = None) -> str:
    """The export's package for a workflow named `name`: `package_name`, prefixed with `wf_` when it would hide a
    module the export imports (`RESERVED`) or a top-level module of the workspace code bundled next to it."""
    pkg = package_name(name)
    return f"wf_{pkg}" if pkg in RESERVED | (workspace or set()) else pkg


def _pyproject(wf: Workflow, t: Target, pkg: str, version: str, extra: list[str]) -> str:
    pinned = [*t.pins, *(f"{d}=={v}" if (v := _version(d)) else d for d in PINNED)]
    have = {_dist(d) for d in pinned}
    deps = "".join(f"  {json.dumps(d)},\n" for d in [*pinned, *(r for r in extra if _dist(r) not in have)])
    return f'''[project]
name = "{pkg.replace("_", "-")}"
version = "{version}"
description = {toml_string(f"{wf.name} — a {t.label} flow exported from AgentCanvas")}
requires-python = ">=3.12,<3.15"
dependencies = [
{deps}]

[dependency-groups]
dev = ["pytest>=8", "pytest-asyncio>=0.24"]

[tool.uv]
package = false

[tool.pytest.ini_options]
pythonpath = ["src"]
asyncio_mode = "auto"
'''


def _readme(wf: Workflow, t: Target, framework: str, pkg: str, models: dict[str, dict[str, Any]]) -> str:
    env = "\n".join(
        f"| `{name}` | `{m['model']}` | `{m['base_url']}` | `{m['key_env']}` | `{name.upper()}_MODEL`, "
        f"`{name.upper()}_BASE_URL` |"
        for name, m in models.items()
    )
    return f"""# {wf.name}

A [{t.label}]({t.docs}) flow exported from AgentCanvas. The flow is in `src/{pkg}/workflow.py`: {t.how}, an
`async def` for each block of the canvas and a `FLOW` that says which step follows which.
`src/agentcanvas/adapters/supersteps.py` runs it as the canvas draws it (steps side by side in the same round, "wait
for all" steps, paths by CEL rules, loops with a limit, field reducers) and
`src/agentcanvas/adapters/{framework}/runtime.py` adds the {t.label} parts: pauses for people and tool calls that wait
for approval, saved with the framework's own state.

```bash
uv sync
cp .env.example .env    # then fill in your keys, and `set -a; . ./.env; set +a`
uv run python -m {pkg} '{{"input": {{}}}}'
```

A run that pauses (an approval, a question or a tool call to approve) prints what it waits for and saves itself in
`.runs/<thread>.json`; continue it with the answer:

```bash
uv run python -m {pkg} '{{"thread_id": "t1", "resume": {{"approved": true}}}}'
```

## Models

Each model block reaches its provider over the OpenAI chat-completions API (any provider with such an endpoint,
local servers included). Override the model or address per block with the variables in the last column. Backup
models and the answer cache of the canvas are not exported for {t.label} flows: `GAPS.md` lists the blocks that
use them, and the steps whose retry or time limit does not apply.

| Block | Model | Address | Key variable | Overrides |
|---|---|---|---|---|
{env}

## Tests

`uv run pytest` replays the workflow's saved test cases with a scripted model: no keys, no network.

Change the workflow on the canvas and export it again rather than editing the generated files.
"""


HOST = '''"""Where the flow's steps get their models, tools, knowledge bases and code when it runs on its own."""

from __future__ import annotations

import importlib
import inspect
import json
import os
from pathlib import Path
from typing import Any

{models_code}
from agentcanvas import knowledge_search as ks

MODELS: dict[str, dict[str, Any]] = {models}
TOOLS: dict[str, str] = {tools}
KNOWLEDGE_DIR = Path(__file__).resolve().parents[1] / "knowledge"


class Host:
    """`mocks` (model block -> replies, as saved test cases have them) script the models instead of calling them."""

    def __init__(self, mocks: dict[str, list[Any]] | None = None):
        self.mocks = mocks or {{}}
        self._kbs: dict[str, ks.Index] = {{}}

    def model(self, resource: str, step: str, **settings: Any) -> Any:
        if resource in self.mocks:
            return scripted_model(list(self.mocks[resource]))
        return make_model(resource, MODELS[resource], settings)

    def tools(self, resources: list[str], step: str) -> list[Any]:
        return [plain_tool(_load(TOOLS[name])) for name in resources]

    async def search(
        self, knowledge_base: str, query: str, *, mode: str, top_k: int, min_score: float
    ) -> list[dict[str, Any]]:
        if knowledge_base not in self._kbs:
            path = Path(os.environ.get("AGENTCANVAS_KNOWLEDGE_DIR") or KNOWLEDGE_DIR) / f"{{knowledge_base}}.jsonl"
            if not ks.NAME.match(knowledge_base) or not path.is_file():
                raise ValueError(f"knowledge base '{{knowledge_base}}' not found (looked for {{path}})")
            header, chunks = ks.load_jsonl(path.read_text(encoding="utf-8"))
            if (header.get("embedding", {{}}).get("model") or ks.BUILTIN) != ks.BUILTIN:
                for c in chunks:  # vectors of a provider's embedding model: search by words here
                    c["vector"] = None
            self._kbs[knowledge_base] = ks.Index(chunks)
        index = self._kbs[knowledge_base]
        vector = ks.builtin_embed([query])[0] if mode != "keyword" and query.strip() else None
        return ks.search(index, query, mode=mode, top_k=top_k, min_score=min_score, query_vector=vector)

    async def script(self, import_path: str, state: dict[str, Any]) -> dict[str, Any]:
        out = _load(import_path)(state)
        return await out if inspect.isawaitable(out) else out


def _load(import_path: str) -> Any:
    module, _, attr = import_path.partition(":")
    return getattr(importlib.import_module(module), attr)


def plain_tool(found: Any) -> Any:
    """A workspace tool as a plain function: as it is, or a LangChain tool as a function with its arguments."""
    if not hasattr(found, "ainvoke"):
        return found
    schema = getattr(found, "tool_call_schema", None) or getattr(found, "args_schema", None)
    fields = getattr(schema, "model_fields", {{}}) if schema is not None else {{}}

    async def invoke(**kwargs: Any) -> str:
        out = await found.ainvoke({{k: v for k, v in kwargs.items() if v is not None}})
        return out if isinstance(out, str) else json.dumps(out, default=str)

    invoke.__name__ = invoke.__qualname__ = found.name
    invoke.__doc__ = found.description or found.name
    invoke.__signature__ = inspect.Signature([  # type: ignore[attr-defined]
        inspect.Parameter(k, inspect.Parameter.KEYWORD_ONLY, annotation=f.annotation,
                          default=inspect.Parameter.empty if f.is_required() else None)
        for k, f in fields.items()
    ], return_annotation=str)
    invoke.__annotations__ = {{**{{k: f.annotation for k, f in fields.items()}}, "return": str}}
    return invoke
'''

MAIN = '''"""`python -m {pkg} '{{"input": {{...}}, "thread_id": "t1"}}'`: one run of the flow, its events as JSON lines.

A paused run is saved in `.runs/<thread_id>.json` (or $RUNS_DIR); `{{"thread_id": ..., "resume": <answer>}}` continues
it with a person's answer. `"mocks": {{"<model block>": ["reply", ...]}}` scripts the models (no keys, no network).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from agentcanvas.adapters.{framework} import runtime as rt
from {pkg}.host import Host
from {pkg}.workflow import FLOW

_NONE: Any = object()


def _saved(thread_id: str) -> Path:
    return Path(os.environ.get("RUNS_DIR") or ".runs") / f"{{thread_id}}.json"


async def run_once(
    input: dict[str, Any] | None = None,
    *,
    thread_id: str = "t1",
    resume: Any = _NONE,
    mocks: dict[str, list[Any]] | None = None,
    quiet: bool = False,
) -> dict[str, Any]:
    """Run (or continue) the flow; returns its last event (RUN_FINISHED or RUN_ERROR)."""
    paused = None
    if resume is not _NONE:
        path = _saved(thread_id)
        if not path.is_file():
            raise SystemExit(f"nothing to continue on thread '{{thread_id}}'")
        paused = (rt.Paused.from_json(path.read_text()), resume)
    last: dict[str, Any] = {{}}
    async for ev in rt.run(FLOW, Host(mocks), input=input, resume=paused, run_id=thread_id):
        saved = ev.pop("paused", None)
        if saved is not None:
            _saved(thread_id).parent.mkdir(parents=True, exist_ok=True)
            _saved(thread_id).write_text(saved.to_json())
        elif ev["type"] == "RUN_FINISHED":  # a failed continue keeps the paused run, to try again
            _saved(thread_id).unlink(missing_ok=True)
        if not quiet:
            print(json.dumps(ev, default=str), flush=True)
        last = ev
    return last


def main(arg: str) -> None:
    req = json.loads(arg or "{{}}")
    asyncio.run(
        run_once(
            req.get("input"), thread_id=req.get("thread_id", "t1"), resume=req.get("resume", _NONE),
            mocks=req.get("mocks"),
        )
    )


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read())
'''

TESTS = '''"""Generated with the project: the flow loads, and saved test cases that script every model pass."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import agentcanvas
from {pkg}.__main__ import run_once
from {pkg}.workflow import FLOW

CASES = json.loads((Path(__file__).parent / "test_cases.json").read_text())


def test_uses_the_vendored_runtime():
    assert Path(agentcanvas.__file__).resolve().parents[1] == Path(__file__).resolve().parents[1] / "src"


def test_flow_loads():
    assert FLOW.steps and set(FLOW.start) <= set(FLOW.steps)


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES] or None)
async def test_saved_case(case, tmp_path, monkeypatch):
    monkeypatch.setenv("RUNS_DIR", str(tmp_path))
    last = await run_once(case["input"], mocks=case["mocks"], quiet=True)
    status = {{"success": "success", "interrupt": "interrupted"}}.get(last.get("outcome"), "error")
    expect = case["expect"] or {{}}
    assert status == expect.get("status", "success"), last
    for name, value in (expect.get("channels") or {{}}).items():
        assert last["output"].get(name) == value, name
'''


def package(
    wf: Workflow,
    source: str,
    framework: str,
    *,
    workspace: Path | None = None,
    test_cases: list[dict[str, Any]] | None = None,
    version: str = "0.0.0",
) -> tuple[str, bytes]:
    """(file name, zip bytes) of the standalone project for `wf`, whose generated flow for `framework` is `source`."""
    refuse_plain_secrets(wf)
    t = TARGETS[framework]
    found = bundle.collect(workspace, wf.id, wf.model_dump_json(), source) if workspace is not None else None
    if found is not None and found.missing:
        raise InvalidInput(
            "the workflow uses code that is not in the workspace: " + ", ".join(found.missing)
            + "; create the file or fix the step's import path, then export again"
        )  # fmt: skip
    pkg = flow_package(wf.name, {rel.split("/")[0].removesuffix(".py") for rel in found.files} if found else set())
    root = f"{pkg.replace('_', '-')}-{t.slug}"
    models = model_specs(wf)
    for m in models.values():
        m.pop("fallbacks", None)  # not exported for these flows (see the README)
    tools = {r.name: r.config["import_path"] for r in wf.resources if r.type == "tool.python"}
    keys = sorted({m["key_env"] for m in models.values()} | _secret_names(wf.model_dump(mode="json")))
    host = HOST.format(models_code=t.models, models=json.dumps(models, indent=4), tools=json.dumps(tools, indent=4))
    files: dict[str, str | bytes] = {
        "pyproject.toml": _pyproject(wf, t, pkg, version, found.requirements if found else []),
        "GAPS.md": gaps_md(wf, f"a {t.label} flow"),
        "README.md": _readme(wf, t, framework, pkg, models),
        ".env.example": "".join(f"{k}=\n" for k in keys),
        ".gitignore": ".venv/\n__pycache__/\n.env\n.runs/\n",
        f"src/{pkg}/__init__.py": json.dumps(f"{wf.name} (a {t.label} flow exported from AgentCanvas).") + "\n",
        f"src/{pkg}/workflow.py": source,
        f"src/{pkg}/host.py": host,
        f"src/{pkg}/__main__.py": MAIN.format(pkg=pkg, framework=framework),
        "src/agentcanvas/__init__.py": '"""The parts of AgentCanvas this flow runs on (Apache-2.0), vendored."""\n',
        "src/agentcanvas/adapters/__init__.py": "",
        f"src/agentcanvas/adapters/{framework}/__init__.py": "",
        "tests/test_workflow.py": TESTS.format(pkg=pkg),
        "tests/test_cases.json": json.dumps(_exportable_cases(wf, test_cases or []), indent=2) + "\n",
    }
    for rel in ["expr.py", "knowledge_search.py", "adapters/supersteps.py", f"adapters/{framework}/runtime.py"]:
        files[f"src/agentcanvas/{rel}"] = (API_ROOT / rel).read_bytes()
    for rel in found.files if found else []:
        files[f"src/{rel}"] = (workspace / rel).read_bytes()  # pyright: ignore[reportOptionalOperand]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, content in sorted(files.items()):
            info = zipfile.ZipInfo(f"{root}/{rel}", date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, content)
    return f"{root}-{version}.zip", buf.getvalue()
