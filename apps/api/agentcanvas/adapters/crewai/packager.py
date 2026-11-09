"""Export a workflow designed for CrewAI as a project of its own (S27).

The project holds the generated CrewAI flow, the runtime it runs on (with the expression and knowledge-search
modules it needs, vendored), a host that reaches each model over its provider's OpenAI-compatible API, a
command-line runner that saves a paused run in a file and continues it, and tests that replay the workflow's saved
test cases with a scripted model. Nothing from LangChain is needed.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

from agentcanvas.adapters.crewai.codegen import CREWAI_VERSION
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
RUNTIME_FILES = ["expr.py", "knowledge_search.py", "adapters/crewai/runtime.py"]
PINNED = ["jsonschema", "cel-python", "jinja2"]  # CrewAI brings its own pydantic


def _pyproject(wf: Workflow, pkg: str, version: str, extra: list[str]) -> str:
    pinned = [f"crewai=={CREWAI_VERSION}"]
    pinned += [f"{d}=={v}" if (v := _version(d)) else d for d in PINNED]
    have = {_dist(d) for d in pinned}
    deps = "".join(f"  {json.dumps(d)},\n" for d in [*pinned, *(r for r in extra if _dist(r) not in have)])
    return f'''[project]
name = "{pkg.replace("_", "-")}"
version = "{version}"
description = {toml_string(f"{wf.name} — a CrewAI flow exported from AgentCanvas")}
requires-python = ">=3.12,<3.14"  # CrewAI supports Python <3.14
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

A [CrewAI](https://docs.crewai.com/) flow exported from AgentCanvas. The flow is in `src/{pkg}/flow.py`: a `Flow`
with a method for each block of the canvas, chained with `@start`, `@listen` and `@router`, and CrewAI `Agent`s and
`Crew`s for the AI blocks. `src/agentcanvas/adapters/crewai/runtime.py` adds what the canvas needs: field reducers,
pauses for people and progress events.

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

Each model block reaches its provider over the OpenAI chat-completions API (any provider with such an endpoint,
local servers included). Override the model or address per block with the variables in the last column. Backup
models and the answer cache of the canvas are not exported for CrewAI flows: `GAPS.md` lists the blocks that use
them, and the steps whose retry or time limit does not apply.

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

from crewai import LLM
from crewai.llms.base_llm import BaseLLM
from crewai.tools import BaseTool, tool
from pydantic import BaseModel

from agentcanvas import knowledge_search as ks

MODELS: dict[str, dict[str, Any]] = {models}
TOOLS: dict[str, str] = {tools}
KNOWLEDGE_DIR = Path(__file__).resolve().parents[1] / "knowledge"


class Host:
    """`mocks` (model block -> replies, as saved test cases have them) script the models instead of calling them."""

    def __init__(self, mocks: dict[str, list[Any]] | None = None):
        self.mocks = mocks or {{}}
        self._kbs: dict[str, ks.Index] = {{}}

    def llm(self, resource: str, step: str) -> Any:
        if resource in self.mocks:
            return ScriptedLLM(model="scripted", replies=list(self.mocks[resource]))
        spec, env = MODELS[resource], resource.upper()
        return LLM(
            model="openai/" + (os.environ.get(f"{{env}}_MODEL") or spec["model"]),
            base_url=os.environ.get(f"{{env}}_BASE_URL") or os.environ.get("MODEL_BASE_URL") or spec["base_url"],
            api_key=os.environ.get(spec["key_env"]) or "not-needed",
        )

    def tools(self, resources: list[str], step: str) -> list[Any]:
        return [crew_tool(_load(TOOLS[name])) for name in resources]

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


def crew_tool(found: Any) -> Any:
    """A workspace tool as a CrewAI tool: a plain function, or a LangChain tool if that is what it is."""
    if not hasattr(found, "ainvoke"):
        return tool(found.__name__)(found)
    class Wrapped(BaseTool):
        name: str = found.name
        description: str = found.description or found.name
        args_schema: type = getattr(found, "args_schema", None) or NoArgs

        def _run(self, **kwargs: Any) -> Any:
            out = found.invoke(kwargs)
            return out if isinstance(out, str) else json.dumps(out, default=str)

    return Wrapped()


class NoArgs(BaseModel):
    pass


class ScriptedLLM(BaseLLM):
    """Scripted replies, as the canvas's mock mode gives them: the n-th answer of a conversation is reply n (the last
    one repeats). A reply is text, or {{"content": ...}}."""

    replies: list[Any] = []

    def call(self, messages: Any, *args: Any, **kwargs: Any) -> str:
        turns = 0 if isinstance(messages, str) else sum(1 for m in messages if m.get("role") == "assistant")
        reply = (self.replies or [""])[min(turns, len(self.replies or [""]) - 1)]
        return str(reply.get("content", "")) if isinstance(reply, dict) else str(reply)

    async def acall(self, messages: Any, *args: Any, **kwargs: Any) -> str:
        return self.call(messages, *args, **kwargs)

    def supports_function_calling(self) -> bool:
        return True
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

from agentcanvas.adapters.crewai import runtime as rt
from {pkg}.flow import CANVAS
from {pkg}.host import Host

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
    async for ev in rt.run(CANVAS, Host(mocks), input=input, resume=paused, run_id=thread_id):
        saved = ev.pop("paused", None)
        if saved is not None:
            _saved(thread_id).parent.mkdir(parents=True, exist_ok=True)
            _saved(thread_id).write_text(saved.to_json())
        elif ev["type"] in ("RUN_FINISHED", "RUN_ERROR"):
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

TESTS = '''"""Generated with the project: the flow loads, and saved test cases that script every model still pass."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import agentcanvas
from {pkg}.__main__ import run_once
from {pkg}.flow import CANVAS

CASES = json.loads((Path(__file__).parent / "test_cases.json").read_text())


def test_uses_the_vendored_runtime():
    assert Path(agentcanvas.__file__).resolve().parents[1] == Path(__file__).resolve().parents[1] / "src"


def test_flow_loads():
    assert CANVAS.steps and CANVAS.flow.__name__.endswith("Flow")


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
    """(file name, zip bytes) of the standalone project for `wf`, whose generated flow is `source`."""
    refuse_plain_secrets(wf)
    pkg = package_name(wf.name)
    root = f"{pkg.replace('_', '-')}-crewai"
    models = model_specs(wf)
    for m in models.values():
        m.pop("fallbacks", None)  # not exported for CrewAI flows (see the README)
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
        "GAPS.md": gaps_md(wf, "a CrewAI flow"),
        "README.md": _readme(wf, pkg, models),
        ".env.example": "".join(f"{k}=\n" for k in keys),
        ".gitignore": ".venv/\n__pycache__/\n.env\n.runs/\n",
        f"src/{pkg}/__init__.py": json.dumps(f"{wf.name} (a CrewAI flow exported from AgentCanvas).") + "\n",
        f"src/{pkg}/flow.py": source,
        f"src/{pkg}/host.py": HOST.format(models=json.dumps(models, indent=4), tools=json.dumps(tools, indent=4)),
        f"src/{pkg}/__main__.py": MAIN.format(pkg=pkg),
        "src/agentcanvas/__init__.py": '"""The parts of AgentCanvas this flow runs on (Apache-2.0), vendored."""\n',
        "src/agentcanvas/adapters/__init__.py": "",
        "src/agentcanvas/adapters/crewai/__init__.py": "",
        "tests/test_flow.py": TESTS.format(pkg=pkg),
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
