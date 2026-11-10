"""Export a workflow designed for the OpenAI Agents SDK as a project of its own (S25).

The project holds the generated flow, the runtime that runs it (with the expression and knowledge-search modules it
needs, vendored), a host that reaches each model over its provider's OpenAI-compatible API, a command-line runner
that saves a paused run in a file and continues it, and tests that replay the workflow's saved test cases with the
SDK's own scripted model. Nothing from LangChain is needed.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

from agentcanvas import designs, licensing
from agentcanvas.adapters.langchain.packager import (
    _dist,
    _exportable_cases,
    _secret_names,
    _version,
    package_name,
)
from agentcanvas.adapters.openai_agents.codegen import SDK_VERSION
from agentcanvas.errors import InvalidInput
from agentcanvas.ir.models import Workflow, budget_gap
from agentcanvas.tomltext import toml_string
from agentcanvas.validation.analysis import refuse_plain_secrets
from agentcanvas.workspace import bundle

API_ROOT = Path(__file__).parents[2]  # the `agentcanvas` package directory
RUNTIME_FILES = ["expr.py", "knowledge_search.py", "adapters/openai_agents/runtime.py"]
PINNED = ["jsonschema", "cel-python", "jinja2", "pydantic"]


def model_specs(wf: Workflow) -> dict[str, dict[str, Any]]:
    """Each model block as the export reaches it: model name, OpenAI-compatible address and the key's variable."""
    out = {}
    for r in wf.resources:
        if r.type != "model.chat":
            continue
        out[r.name] = _spec(str(r.config.get("model", "")), r.config.get("base_url"), r.config.get("api_key"))
        backups = [_spec(str(b), None, None) for b in r.config.get("fallbacks") or [] if b]
        if backups:  # S26: tried in order when the block's own model fails
            out[r.name]["fallbacks"] = backups
    return out


def _spec(model: str, base_url: Any, key: Any) -> dict[str, str]:
    provider, _, name = model.partition(":")
    return {
        "model": name,
        "base_url": base_url or designs.OPENAI_URLS.get(provider) or "http://localhost:8000/v1",
        "key_env": key["$secret"] if isinstance(key, dict) else designs.KEY_ENVS.get(provider, "MODEL_API_KEY"),
    }


def _pyproject(wf: Workflow, pkg: str, version: str, extra: list[str]) -> str:
    pinned = [f"openai-agents=={SDK_VERSION}"]
    pinned += [f"{d}=={v}" if (v := _version(d)) else d for d in PINNED]
    have = {_dist(d) for d in pinned}
    deps = "".join(f"  {json.dumps(d)},\n" for d in [*pinned, *(r for r in extra if _dist(r) not in have)])
    return f'''[project]
name = "{pkg.replace("_", "-")}"
version = "{version}"
description = {toml_string(f"{wf.name} — an OpenAI Agents SDK flow exported from AgentCanvas")}
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

An [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/) flow exported from AgentCanvas. The flow is
in `src/{pkg}/flow.py`: an `Agent` for each AI block of the canvas and a function for each other block, run by
`src/agentcanvas/adapters/openai_agents/runtime.py`.

```bash
uv sync
cp .env.example .env    # then fill in your keys, and `set -a; . ./.env; set +a`
uv run python -m {pkg} '{{"input": {{}}}}'
```

A run that pauses (an approval, a question, a tool that needs a yes) prints what it waits for and saves itself in
`.runs/<thread>.json`; continue it with the answer:

```bash
uv run python -m {pkg} '{{"thread_id": "t1", "resume": {{"approved": true}}}}'
```

## Models

Each model block reaches its provider over the OpenAI chat-completions API (any provider with such an endpoint,
local servers included). Override the model or address per block with the variables in the last column. A block with
backup models tries them in order when its own model fails; the answer cache of the canvas is not exported.

| Block | Model | Address | Key variable | Overrides |
|---|---|---|---|---|
{env}

## Tests

`uv run pytest` replays the workflow's saved test cases with the SDK's scripted model: no keys, no network.

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

from agents import FunctionTool, OpenAIChatCompletionsModel, function_tool
from openai import AsyncOpenAI

from agentcanvas import knowledge_search as ks
from agentcanvas.adapters.openai_agents.runtime import FallbackModel

MODELS: dict[str, dict[str, Any]] = {models}
TOOLS: dict[str, str] = {tools}
KNOWLEDGE_DIR = Path(__file__).resolve().parents[1] / "knowledge"


class Host:
    """`mocks` (model block -> replies, as saved test cases have them) script the models instead of calling them."""

    def __init__(self, mocks: dict[str, list[Any]] | None = None):
        self.mocks = mocks or {{}}
        self._scripted: dict[str, Any] = {{}}
        self._kbs: dict[str, ks.Index] = {{}}

    def model(self, resource: str, step: str) -> Any:
        if resource in self.mocks:
            if resource not in self._scripted:
                self._scripted[resource] = scripted(self.mocks[resource])
            return self._scripted[resource]
        spec, env = MODELS[resource], resource.upper()
        client = AsyncOpenAI(
            base_url=os.environ.get(f"{{env}}_BASE_URL") or os.environ.get("MODEL_BASE_URL") or spec["base_url"],
            api_key=os.environ.get(spec["key_env"]) or "not-needed",
        )
        name = os.environ.get(f"{{env}}_MODEL") or spec["model"]
        model = OpenAIChatCompletionsModel(model=name, openai_client=client)
        if not spec.get("fallbacks"):
            return model
        backups = [
            OpenAIChatCompletionsModel(
                model=b["model"],
                openai_client=AsyncOpenAI(base_url=b["base_url"], api_key=os.environ.get(b["key_env"]) or "not-needed"),
            )
            for b in spec["fallbacks"]
        ]
        return FallbackModel([model, *backups])  # backup models, tried in order when this one fails

    def tools(self, resources: list[str], approve: list[str], step: str) -> list[Any]:
        out = []
        for name in resources:
            found = _load(TOOLS[name])
            label = getattr(found, "name", None) or getattr(found, "__name__", name)
            out.append(sdk_tool(found, name in approve or label in approve))
        return out

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


def sdk_tool(found: Any, needs_approval: bool) -> Any:
    """A workspace tool as an SDK function tool: a plain function, or a LangChain tool if that is what it is."""
    if not hasattr(found, "ainvoke"):
        return function_tool(found, needs_approval=needs_approval)
    schema = getattr(found, "tool_call_schema", None) or getattr(found, "args_schema", None)

    async def invoke(_ctx: Any, args_json: str) -> str:
        out = await found.ainvoke(json.loads(args_json or "{{}}"))
        return out if isinstance(out, str) else json.dumps(out, default=str)

    return FunctionTool(
        name=found.name,
        description=found.description or found.name,
        params_json_schema=schema.model_json_schema() if schema is not None else {{"type": "object"}},
        on_invoke_tool=invoke,
        strict_json_schema=False,
        needs_approval=needs_approval,
    )


def scripted(replies: list[Any]) -> Any:
    """The SDK's scripted model answering with `replies` in order (text, or {{"content", "tool_calls"}}); the last
    one repeats."""
    from agents.testing import ScriptedModel, assistant_message, function_call

    def step(i: int, reply: Any) -> list[Any]:
        if isinstance(reply, dict) and reply.get("tool_calls"):
            return [function_call(c["name"], c.get("args", {{}}), call_id=f"mock_{{i}}_{{j}}")
                    for j, c in enumerate(reply["tool_calls"])]
        text = reply.get("content", "") if isinstance(reply, dict) else str(reply)
        return [assistant_message(text)]

    replies = list(replies) or [""]
    return ScriptedModel([step(i, r) for i, r in enumerate(replies)] + [step(len(replies), replies[-1])] * 100)
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

from agentcanvas.adapters.openai_agents import runtime as rt
from {pkg}.flow import FLOW
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
    async for ev in rt.run(FLOW, Host(mocks), input=input, resume=paused, run_id=thread_id):
        saved = ev.pop("paused", None)
        if saved is not None:
            _saved(thread_id).parent.mkdir(parents=True, exist_ok=True)
            _saved(thread_id).write_text(saved.to_json())
        elif ev["type"] in ("RUN_FINISHED", "RUN_ERROR"):
            _saved(thread_id).unlink(missing_ok=True)
        if not quiet:
            print(json.dumps(ev, default=str))
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
from {pkg}.flow import FLOW

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
    *,
    workspace: Path | None = None,
    test_cases: list[dict[str, Any]] | None = None,
    version: str = "0.0.0",
) -> tuple[str, bytes]:
    """(file name, zip bytes) of the standalone project for `wf`, whose generated flow is `source`."""
    refuse_plain_secrets(wf)
    pkg = package_name(wf.name)
    root = f"{pkg.replace('_', '-')}-openai-agents"
    models = model_specs(wf)
    tools = {r.name: r.config["import_path"] for r in wf.resources if r.type == "tool.python"}
    found = bundle.collect(workspace, wf.id, wf.model_dump_json(), source) if workspace is not None else None
    if found is not None and found.missing:
        raise InvalidInput(
            "the workflow uses code that is not in the workspace: " + ", ".join(found.missing)
            + "; create the file or fix the step's import path, then export again"
        )  # fmt: skip
    backup_keys = {b["key_env"] for m in models.values() for b in m.get("fallbacks", [])}
    keys = sorted({m["key_env"] for m in models.values()} | backup_keys | _secret_names(wf.model_dump(mode="json")))
    files: dict[str, str | bytes] = {
        "pyproject.toml": _pyproject(wf, pkg, version, found.requirements if found else []),
        "README.md": _readme(wf, pkg, models),
        ".env.example": "".join(f"{k}=\n" for k in keys),
        ".gitignore": ".venv/\n__pycache__/\n.env\n.runs/\n",
        f"src/{pkg}/__init__.py": json.dumps(f"{wf.name} (an OpenAI Agents SDK flow exported from AgentCanvas).")
        + "\n",
        f"src/{pkg}/flow.py": source,
        f"src/{pkg}/host.py": HOST.format(models=json.dumps(models, indent=4), tools=json.dumps(tools, indent=4)),
        f"src/{pkg}/__main__.py": MAIN.format(pkg=pkg),
        "src/agentcanvas/__init__.py": '"""The parts of AgentCanvas this flow runs on (Apache-2.0), vendored."""\n',
        "src/agentcanvas/adapters/__init__.py": "",
        "src/agentcanvas/adapters/openai_agents/__init__.py": "",
        "tests/test_flow.py": TESTS.format(pkg=pkg),
        "tests/test_cases.json": json.dumps(_exportable_cases(wf, test_cases or []), indent=2) + "\n",
    }
    if gap := budget_gap(wf, "an OpenAI Agents SDK flow"):  # the AgentCanvas runner enforces it; the flow does not
        files["GAPS.md"] = f"# What runs differently in an OpenAI Agents SDK flow\n\n- {gap}\n"
    for rel in RUNTIME_FILES:
        files[f"src/agentcanvas/{rel}"] = (API_ROOT / rel).read_bytes()
    for rel in found.files if found else []:
        files[f"src/{rel}"] = (workspace / rel).read_bytes()  # pyright: ignore[reportOptionalOperand]
    files = licensing.with_license(files)  # it carries AgentCanvas code: the License goes with each copy
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, content in sorted(files.items()):
            info = zipfile.ZipInfo(f"{root}/{rel}", date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, content)
    return f"{root}-{version}.zip", buf.getvalue()
