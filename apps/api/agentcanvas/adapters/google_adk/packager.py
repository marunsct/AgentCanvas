"""Export a workflow designed for Google ADK as a project of its own (S30).

The project holds the generated ADK workflow, the runtime it runs on (with the expression and knowledge-search
modules it needs, vendored), a host that reaches each model through LiteLLM over its provider's OpenAI-compatible
API, a command-line runner that saves a paused run in a file and continues it, and tests that replay the workflow's
saved test cases with a scripted model. Nothing from LangChain is needed.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

from agentcanvas.adapters.google_adk.codegen import ADK_VERSION, LITELLM_VERSION
from agentcanvas.adapters.langchain.packager import (
    _dist,
    _exportable_cases,
    _secret_names,
    _version,
    gaps_md,
    package_name,
)
from agentcanvas.adapters.openai_agents.packager import model_specs
from agentcanvas.errors import InvalidInput
from agentcanvas.ir.models import Workflow
from agentcanvas.tomltext import toml_string
from agentcanvas.validation.analysis import refuse_plain_secrets
from agentcanvas.workspace import bundle

API_ROOT = Path(__file__).parents[2]  # the `agentcanvas` package directory
RUNTIME_FILES = ["expr.py", "knowledge_search.py", "adapters/google_adk/runtime.py"]
PINNED = ["jsonschema", "cel-python", "jinja2"]  # ADK brings its own pydantic


def _pyproject(wf: Workflow, pkg: str, version: str, extra: list[str]) -> str:
    pinned = [f"google-adk=={ADK_VERSION}", f"litellm=={LITELLM_VERSION}"]
    pinned += [f"{d}=={v}" if (v := _version(d)) else d for d in PINNED]
    have = {_dist(d) for d in pinned}
    deps = "".join(f"  {json.dumps(d)},\n" for d in [*pinned, *(r for r in extra if _dist(r) not in have)])
    return f'''[project]
name = "{pkg.replace("_", "-")}"
version = "{version}"
description = {toml_string(f"{wf.name} — a Google ADK workflow exported from AgentCanvas")}
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


def _readme(wf: Workflow, pkg: str, models: dict[str, dict[str, Any]]) -> str:
    env = "\n".join(
        f"| `{name}` | `{m['model']}` | `{m['base_url']}` | `{m['key_env']}` | `{name.upper()}_MODEL`, "
        f"`{name.upper()}_BASE_URL` |"
        for name, m in models.items()
    )
    return f"""# {wf.name}

A [Google ADK](https://google.github.io/adk-docs/) workflow exported from AgentCanvas. The workflow is in
`src/{pkg}/workflow.py`: a function node for each block of the canvas, wired by `build()` with ADK's graph edges
(route dicts after path blocks, `JoinNode`s where the canvas waits for every branch), and ADK `Agent`s for the AI
blocks. `src/agentcanvas/adapters/google_adk/runtime.py` adds what the canvas needs: field reducers, pauses for
people (ADK's `RequestInput`), tool calls that wait for approval and progress events.

```bash
uv sync
cp .env.example .env    # then fill in your keys, and `set -a; . ./.env; set +a`
uv run python -m {pkg} '{{"input": {{}}}}'
```

A run that pauses (an approval or a question) prints what it waits for and saves itself in `.runs/<thread>.json`;
continue it with the answer:

```bash
uv run python -m {pkg} '{{"thread_id": "t1", "resume": {{"approved": true}}}}'
```

## Models

Each model block reaches its provider through LiteLLM over the OpenAI chat-completions API (any provider with such
an endpoint, local servers included). Override the model or address per block with the variables in the last
column. Backup models and the answer cache of the canvas are not exported for ADK workflows: `GAPS.md` lists the
blocks that use them, and the steps whose retry or time limit does not apply.

| Block | Model | Address | Key variable | Overrides |
|---|---|---|---|---|
{env}

## Tests

`uv run pytest` replays the workflow's saved test cases with a scripted model: no keys, no network.

Change the workflow on the canvas and export it again rather than editing the generated files.
"""


HOST = '''"""Where the workflow's steps get their models, tools, knowledge bases and code when it runs on its own."""

from __future__ import annotations

import importlib
import inspect
import json
import os
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from google.adk.models import BaseLlm, LlmRequest, LlmResponse
from google.adk.models.lite_llm import LiteLlm
from google.genai import types

from agentcanvas import knowledge_search as ks

MODELS: dict[str, dict[str, Any]] = {models}
TOOLS: dict[str, str] = {tools}
KNOWLEDGE_DIR = Path(__file__).resolve().parents[1] / "knowledge"


class Host:
    """`mocks` (model block -> replies, as saved test cases have them) script the models instead of calling them."""

    def __init__(self, mocks: dict[str, list[Any]] | None = None):
        self.mocks = mocks or {{}}
        self._scripted: dict[str, ScriptedLlm] = {{}}
        self._kbs: dict[str, ks.Index] = {{}}

    def llm(self, resource: str, step: str, **settings: Any) -> BaseLlm:
        if resource in self.mocks:
            if resource not in self._scripted:
                self._scripted[resource] = ScriptedLlm(model="scripted", replies=list(self.mocks[resource]))
            return self._scripted[resource]
        spec, env = MODELS[resource], resource.upper()
        return LiteLlm(
            model="openai/" + (os.environ.get(f"{{env}}_MODEL") or spec["model"]),
            api_base=os.environ.get(f"{{env}}_BASE_URL") or os.environ.get("MODEL_BASE_URL") or spec["base_url"],
            api_key=os.environ.get(spec["key_env"]) or "not-needed",
            drop_params=True,  # LiteLLM leaves out settings a model does not take
            **settings,
        )

    def tools(self, resources: list[str], step: str) -> list[Any]:
        return [adk_tool(_load(TOOLS[name])) for name in resources]

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


def adk_tool(found: Any) -> Any:
    """A workspace tool for ADK: a plain function as it is, or a LangChain tool as a function with its arguments."""
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
    return invoke


class ScriptedLlm(BaseLlm):
    """Scripted replies, as the canvas's mock mode gives them: each model call gets the next reply (the last one
    repeats). A reply is text, or {{"content": ..., "tool_calls": [{{"name": ..., "args": {{...}}}}]}}."""

    replies: list[Any] = []
    calls: int = 0

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        replies = self.replies or [""]
        reply = replies[min(self.calls, len(replies) - 1)]
        self.calls += 1
        parts: list[types.Part] = []
        if isinstance(reply, dict) and reply.get("tool_calls"):
            parts = [types.Part(function_call=types.FunctionCall(name=c["name"], args=c.get("args") or {{}},
                                                                 id=f"mock_{{self.calls}}_{{i}}"))
                     for i, c in enumerate(reply["tool_calls"])]
        text = reply.get("content", "") if isinstance(reply, dict) else str(reply)
        if text or not parts:
            parts.insert(0, types.Part(text=text))
        yield LlmResponse(content=types.Content(role="model", parts=parts))
'''

MAIN = '''"""`python -m {pkg} '{{"input": {{...}}, "thread_id": "t1"}}'`: one run of the workflow, its events as JSON
lines.

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

from agentcanvas.adapters.google_adk import runtime as rt
from {pkg}.host import Host
from {pkg}.workflow import CANVAS

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
    """Run (or continue) the workflow; returns its last event (RUN_FINISHED or RUN_ERROR)."""
    paused = None
    if resume is not _NONE:
        path = _saved(thread_id)
        if not path.is_file():
            raise SystemExit(f"nothing to continue on thread '{{thread_id}}'")
        paused = (rt.Paused.from_json(path.read_text()), resume)
    last: dict[str, Any] = {{}}
    async for ev in rt.run(CANVAS, Host(mocks), input=input, resume=paused, run_id=thread_id):
        saved = ev.pop("paused", None)
        if saved is not None:
            _saved(thread_id).parent.mkdir(parents=True, exist_ok=True)
            _saved(thread_id).write_text(saved.to_json())
        elif ev["type"] == "RUN_FINISHED" or (ev["type"] == "RUN_ERROR" and paused is None):
            _saved(thread_id).unlink(missing_ok=True)  # a failed continuation keeps the saved run, to try again
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

TESTS = '''"""Generated with the project: the workflow loads, and saved test cases that script every model pass."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import agentcanvas
from {pkg}.__main__ import run_once
from {pkg}.workflow import CANVAS

CASES = json.loads((Path(__file__).parent / "test_cases.json").read_text())


def test_uses_the_vendored_runtime():
    assert Path(agentcanvas.__file__).resolve().parents[1] == Path(__file__).resolve().parents[1] / "src"


def test_workflow_loads():
    assert CANVAS.steps and CANVAS.build().name


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
    *,
    workspace: Path | None = None,
    test_cases: list[dict[str, Any]] | None = None,
    version: str = "0.0.0",
) -> tuple[str, bytes]:
    """(file name, zip bytes) of the standalone project for `wf`, whose generated workflow is `source`."""
    refuse_plain_secrets(wf)
    pkg = package_name(wf.name)
    root = f"{pkg.replace('_', '-')}-google-adk"
    models = model_specs(wf)
    for m in models.values():
        m.pop("fallbacks", None)  # not exported for ADK workflows (see the README)
    tools = {r.name: r.config["import_path"] for r in wf.resources if r.type == "tool.python"}
    found = bundle.collect(workspace, wf.id, wf.model_dump_json(), source) if workspace is not None else None
    if found is not None and found.missing:
        raise InvalidInput(
            "the workflow uses code that is not in the workspace: " + ", ".join(found.missing)
            + "; create the file or fix the step's import path, then export again"
        )  # fmt: skip
    keys = sorted({m["key_env"] for m in models.values()} | _secret_names(wf.model_dump(mode="json")))
    files: dict[str, str | bytes] = {
        "pyproject.toml": _pyproject(wf, pkg, version, found.requirements if found else []),
        "GAPS.md": gaps_md(wf, "a Google ADK workflow"),
        "README.md": _readme(wf, pkg, models),
        ".env.example": "".join(f"{k}=\n" for k in keys),
        ".gitignore": ".venv/\n__pycache__/\n.env\n.runs/\n",
        f"src/{pkg}/__init__.py": json.dumps(f"{wf.name} (a Google ADK workflow exported from AgentCanvas).") + "\n",
        f"src/{pkg}/workflow.py": source,
        f"src/{pkg}/host.py": HOST.format(models=json.dumps(models, indent=4), tools=json.dumps(tools, indent=4)),
        f"src/{pkg}/__main__.py": MAIN.format(pkg=pkg),
        "src/agentcanvas/__init__.py": '"""The parts of AgentCanvas this workflow runs on (Apache-2.0), vendored."""\n',
        "src/agentcanvas/adapters/__init__.py": "",
        "src/agentcanvas/adapters/google_adk/__init__.py": "",
        "tests/test_workflow.py": TESTS.format(pkg=pkg),
        "tests/test_cases.json": json.dumps(_exportable_cases(wf, test_cases or []), indent=2) + "\n",
    }
    for rel in RUNTIME_FILES:
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
