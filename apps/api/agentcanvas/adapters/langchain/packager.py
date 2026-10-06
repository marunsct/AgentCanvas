"""Project packager (M6-T2): a workflow as a standalone Python project, zipped.

    <project>/
      pyproject.toml            pinned stack (uv), pytest config
      README.md  .env.example   provider keys + the project secrets the workflow references
      Dockerfile  langgraph.json
      src/<pkg>/graph.py        generated code (the same file as the Code tab)
      src/<pkg>/server.py       `make_graph` (ServerRuntime factory) for LangGraph Server / `langgraph dev`
      src/<pkg>/__main__.py     `python -m <pkg> '{"input": ...}'`
      src/agentcanvas/...       the node library the generated code calls (vendored, Apache-2.0)
      src/scripts|tools|nodes/  the workspace code at export time
      tests/test_smoke.py       builds the graph; replays saved test cases that use mock models
      tests/test_cases.json

Nothing in the package talks to AgentCanvas: it runs anywhere LangGraph runs.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from importlib import metadata
from pathlib import Path
from typing import Any

from agentcanvas.ir.models import Workflow

API_ROOT = Path(__file__).parents[2]  # the `agentcanvas` package directory
RUNTIME_FILES = [
    "__init__.py",
    "adapters/langchain/__init__.py",
    "adapters/langchain/nodes.py",
    "adapters/langchain/testing.py",
    "expr.py",
    "sdk.py",
    "security/__init__.py",
    "security/imports.py",
    "workspace/__init__.py",
    "workspace/loader.py",
    "workspace/store.py",
]
WORKSPACE_DIRS = ("scripts", "tools", "nodes", "events")
PINNED = ["langgraph", "langchain", "langchain-core", "deepagents", "jsonschema", "cel-python", "jinja2", "httpx"]
PROVIDERS = {  # model id prefix -> (integration package, API key variable)
    "openai": ("langchain-openai", "OPENAI_API_KEY"),
    "azure_openai": ("langchain-openai", "AZURE_OPENAI_API_KEY"),
    "anthropic": ("langchain-anthropic", "ANTHROPIC_API_KEY"),
    "google_genai": ("langchain-google-genai", "GOOGLE_API_KEY"),
    "google_vertexai": ("langchain-google-vertexai", None),
    "bedrock_converse": ("langchain-aws", None),
    "mistralai": ("langchain-mistralai", "MISTRAL_API_KEY"),
    "groq": ("langchain-groq", "GROQ_API_KEY"),
    "deepseek": ("langchain-deepseek", "DEEPSEEK_API_KEY"),
    "xai": ("langchain-xai", "XAI_API_KEY"),
    "ollama": ("langchain-ollama", None),
    "lmstudio": ("langchain-openai", None),
    "vllm": ("langchain-openai", None),
    "llamacpp": ("langchain-openai", None),
    "openai_compatible": ("langchain-openai", None),
}


def package_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "workflow"
    return slug if slug[0].isalpha() else f"wf_{slug}"


def _secret_names(value: Any) -> set[str]:
    if isinstance(value, dict):
        if "$secret" in value and isinstance(value["$secret"], str):
            return {value["$secret"]}
        return set().union(*(_secret_names(v) for v in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(_secret_names(v) for v in value)) if value else set()
    return set()


def _models(wf: Workflow) -> list[tuple[str, str]]:
    """(resource name, model id) of every chat model, subgraphs included."""
    out = [(r.name, str(r.config.get("model", ""))) for r in wf.resources if r.type == "model.chat"]
    for n in wf.nodes:
        if n.type == "core.subgraph":
            out += _models(wf.subworkflow(n))
    return out


def _version(dist: str) -> str | None:
    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:
        return None


def dependencies(wf: Workflow) -> list[str]:
    deps = []
    for dist in PINNED:
        v = _version(dist)
        extra = "[mcp]" if dist == "langchain" else ""
        deps.append(f"{dist}{extra}=={v}" if v else f"{dist}{extra}")
    for _, model in _models(wf):
        pkg = PROVIDERS.get(model.partition(":")[0], (None, None))[0]
        if pkg and not any(d.startswith(pkg) for d in deps):
            v = _version(pkg)
            deps.append(f"{pkg}=={v}" if v else pkg)
    return deps


def _env_example(wf: Workflow) -> str:
    lines = ["# Model provider keys"]
    keys = {PROVIDERS.get(m.partition(":")[0], (None, None))[1] for _, m in _models(wf)} - {None}
    lines += [f"{k}=" for k in sorted(k for k in keys if k)] or ["# (no hosted models)"]
    secrets = sorted(_secret_names(wf.model_dump(mode="json")))
    if secrets:
        lines += ["", '# Project secrets referenced by the workflow ({"$secret": NAME})'] + [f"{s}=" for s in secrets]
    lines += ["", "# Optional: LangSmith tracing", "LANGSMITH_TRACING=false", "LANGSMITH_API_KEY="]
    return "\n".join(lines) + "\n"


def _pyproject(wf: Workflow, pkg: str, version: str) -> str:
    deps = "".join(f'  "{d}",\n' for d in dependencies(wf))
    return f'''[project]
name = "{pkg.replace("_", "-")}"
version = "{version}"
description = {json.dumps(f"{wf.name} — exported from AgentCanvas")}
requires-python = ">=3.12"
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


def _readme(wf: Workflow, pkg: str) -> str:
    return f"""# {wf.name}

Exported from AgentCanvas. `src/{pkg}/graph.py` is generated from the canvas; `src/agentcanvas/` is the node
library it calls; `src/scripts|tools|nodes/` is the project's workspace code.

```bash
uv sync
cp .env.example .env               # fill in the keys
uv run pytest                      # builds the graph, replays saved test cases that use mock models
uv run --env-file .env python -m {pkg} '{{"input": {{}}}}'
uv run --env-file .env langgraph dev   # LangGraph Server + Studio (pip install "langgraph-cli[inmem]")
docker build -t {pkg.replace("_", "-")} .
```

`python -m {pkg}` runs one turn with an in-memory checkpointer and prints the final state (or the pending
interrupts). In your own code: `from {pkg}.graph import build_graph` and pass your checkpointer and store.
"""


SERVER = '''"""LangGraph Server entry point (`langgraph.json` -> make_graph): resources from the environment."""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # src/: agentcanvas + workspace code

from agentcanvas.adapters.langchain.nodes import Resources  # noqa: E402

try:  # the server resolves the annotation below to inject its runtime
    from langgraph_sdk.runtime import ServerRuntime
except ImportError:  # pragma: no cover
    from typing import Any as ServerRuntime

from {pkg} import graph as generated  # noqa: E402

SECRETS = {secrets}


def make_resources() -> Resources:
    return Resources(secrets={{k: os.environ[k] for k in SECRETS if k in os.environ}})


async def _build():
    build = getattr(generated, "abuild_graph", None)  # MCP tools are discovered asynchronously
    return await build(make_resources()) if build else generated.build_graph(make_resources())


@contextlib.asynccontextmanager
async def make_graph(runtime: ServerRuntime):
    """Graph factory for Agent Server (it injects a ServerRuntime): the same topology for runs and introspection."""
    yield await _build()
'''

MAIN = '''"""`python -m {pkg} '{{"input": {{...}}, "thread_id": "t1"}}'`: one turn, printed as JSON."""

from __future__ import annotations

import asyncio
import json
import sys

from langgraph.checkpoint.memory import InMemorySaver

from {pkg}.server import make_resources
from {pkg} import graph as generated


async def main(arg: str) -> None:
    req = json.loads(arg or "{{}}")
    build = getattr(generated, "abuild_graph", None)
    graph = await build(make_resources(), checkpointer=InMemorySaver()) if build else generated.build_graph(
        make_resources(), checkpointer=InMemorySaver()
    )
    config = {{"configurable": {{"thread_id": req.get("thread_id", "t1")}}}}
    out = await graph.ainvoke(req.get("input", {{}}), config, context=req.get("context") or {{}})
    print(json.dumps(out, default=str, indent=2))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()))
'''

SMOKE = '''"""Generated with the project: the graph builds, and saved test cases that use mock models still pass."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver

import agentcanvas
from agentcanvas.adapters.langchain.nodes import Resources
from {pkg} import graph as generated

MODELS = {models}
CASES = json.loads((Path(__file__).parent / "test_cases.json").read_text())


async def build(mocks: dict):
    resources = Resources().with_mocks(mocks)
    build = getattr(generated, "abuild_graph", None)
    if build:
        return await build(resources, checkpointer=InMemorySaver())
    return generated.build_graph(resources, checkpointer=InMemorySaver())


def test_uses_the_vendored_node_library():
    assert Path(agentcanvas.__file__).resolve().parents[1] == Path(__file__).resolve().parents[1] / "src"


async def test_graph_builds():
    graph = await build({{m: ["ok"] for m in MODELS}})
    assert "graph" in graph.get_graph().draw_mermaid()


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES] or None)
async def test_saved_case(case):
    graph = await build(case["mocks"])
    config = {{"configurable": {{"thread_id": "test"}}}}
    status = "success"
    try:
        out = await graph.ainvoke(case["input"], config, context=case.get("context") or {{}})
    except Exception:  # noqa: BLE001
        status, out = "error", {{}}
    if status == "success" and (await graph.aget_state(config)).interrupts:
        status = "interrupted"
    expect = case["expect"]
    assert status == expect.get("status", "success")
    for name, value in (expect.get("channels") or {{}}).items():
        assert out.get(name) == value, name
'''

DOCKERFILE = """FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml ./
RUN uv sync --no-dev
COPY . .
ENV PYTHONPATH=/app/src
ENTRYPOINT ["uv", "run", "--no-dev", "python", "-m", "{pkg}"]
"""


def _exportable_cases(wf: Workflow, cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cases that run offline: every chat model mocked, no pinned outputs (the export has no debug pins)."""
    models = {name for name, _ in _models(wf)}
    out = []
    for c in cases:
        if not c.get("pins") and models <= set(c.get("mocks") or {}):
            out.append({k: c.get(k) for k in ("name", "input", "context", "mocks", "expect")})
    return out


def package(
    wf: Workflow,
    source: str,
    *,
    workspace: Path | None = None,
    test_cases: list[dict[str, Any]] | None = None,
    version: str = "0.0.0",
) -> tuple[str, bytes]:
    """(file name, zip bytes) of the standalone project for `wf`, whose generated code is `source`."""
    pkg = package_name(wf.name)
    root = pkg.replace("_", "-")
    secrets = sorted(_secret_names(wf.model_dump(mode="json")))
    files: dict[str, str | bytes] = {
        "pyproject.toml": _pyproject(wf, pkg, version),
        "README.md": _readme(wf, pkg),
        ".env.example": _env_example(wf),
        ".gitignore": ".venv/\n__pycache__/\n.env\n",
        "Dockerfile": DOCKERFILE.format(pkg=pkg),
        "langgraph.json": json.dumps(
            {"dependencies": ["."], "graphs": {pkg: f"./src/{pkg}/server.py:make_graph"}, "env": ".env"}, indent=2
        )
        + "\n",
        f"src/{pkg}/__init__.py": f'"""{wf.name} (exported from AgentCanvas)."""\n',
        f"src/{pkg}/graph.py": source,
        f"src/{pkg}/server.py": SERVER.format(pkg=pkg, secrets=json.dumps(secrets)),
        f"src/{pkg}/__main__.py": MAIN.format(pkg=pkg),
        "src/agentcanvas/NOTICE.md": "The AgentCanvas node library (Apache-2.0), vendored so the project runs alone.\n",
        "tests/test_smoke.py": SMOKE.format(pkg=pkg, models=json.dumps(sorted({n for n, _ in _models(wf)}))),
        "tests/test_cases.json": json.dumps(_exportable_cases(wf, test_cases or []), indent=2) + "\n",
    }
    for rel in RUNTIME_FILES:
        files[f"src/agentcanvas/{rel}"] = (API_ROOT / rel).read_bytes()
    files["src/agentcanvas/adapters/__init__.py"] = ""  # the control plane's adapter registry is not needed
    if workspace is not None:
        for d in WORKSPACE_DIRS:
            base = workspace / d
            for p in sorted(base.rglob("*")) if base.is_dir() else []:
                if p.is_file() and "__pycache__" not in p.parts:
                    files[f"src/{p.relative_to(workspace).as_posix()}"] = p.read_bytes()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, content in sorted(files.items()):
            z.writestr(f"{root}/{rel}", content)
    return f"{root}.zip", buf.getvalue()
