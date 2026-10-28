"""Project packager (M6-T2, deploy kit S16): a workflow as a standalone, deployable Python project, zipped.

    <project>/
      README.md  Makefile       `make doctor | setup | test | gate | run | deploy | smoke | logs`
      pyproject.toml            pinned stack (uv), pytest config
      .env.example              every setting, with where to get it
      Dockerfile  langgraph.json  workflow.ir.json (re-import into AgentCanvas)
      docs/1-PREREQUISITES.md 2-SETUP.md 3-DEPLOY.md 4-VERIFY-AND-OPERATE.md   for the chosen target
      deploy/<target>/          Compose (+ Caddy), a Helm chart, or a Cloud Run / ECS / Container Apps script
      src/<pkg>/graph.py        generated code (the same file as the Code tab)
      src/<pkg>/app.py          HTTP service with the same API as an AgentCanvas deployment
      src/<pkg>/checks.py smoke.py kit.json   `make doctor` / `make smoke` and the facts they check
      src/<pkg>/server.py       `make_graph` (ServerRuntime factory) for LangGraph Server / `langgraph dev`
      src/<pkg>/agents/<name>.py   agents designed for another framework that installs next to LangGraph (S21)
      parts/<name>/             agents of frameworks that clash with LangGraph (S28): agent.py, worker.py, their own
                                requirements.txt (`make install` sets up parts/<name>/.venv) and contract.json
      contracts.json            every part of a mixed flow: its framework, where it runs, and its typed contract
      src/<pkg>/__main__.py     `python -m <pkg> '{"input": ...}'`
      src/agentcanvas/...       the node library the generated code calls (vendored, Apache-2.0)
      src/scripts|tools|nodes|flows/   exactly the workspace code the workflow uses (imports followed, S17)
      tests/test_workspace_code.py   every workspace import path the workflow names imports in the export
      tests/test_smoke.py       builds the graph; replays saved test cases that use mock models
      tests/test_service.py     the service answers the deployment API (offline)

Nothing in the package talks to AgentCanvas: it runs anywhere Python and LangGraph run.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from importlib import metadata
from pathlib import Path
from typing import Any

from agentcanvas import designs
from agentcanvas.adapters.langchain import deploykit
from agentcanvas.adapters.langchain.codegen import one_line
from agentcanvas.adapters.langchain.interop import FRAMEWORKS
from agentcanvas.designs import article
from agentcanvas.errors import InvalidInput
from agentcanvas.ir.models import Workflow
from agentcanvas.workspace import bundle

API_ROOT = Path(__file__).parents[2]  # the `agentcanvas` package directory
RUNTIME_FILES = [
    "__init__.py",
    "adapters/langchain/__init__.py",
    "adapters/langchain/bridge.py",
    "adapters/langchain/gateway.py",
    "adapters/langchain/interop.py",
    "adapters/langchain/nodes.py",
    "adapters/langchain/parts.py",
    "adapters/langchain/testing.py",
    "expr.py",
    "guardrails.py",
    "knowledge_search.py",
    "safe_regex.py",
    "sdk.py",
    "security/__init__.py",
    "security/imports.py",
    "workspace/__init__.py",
    "workspace/loader.py",
    "workspace/store.py",
]
PINNED = ["langgraph", "langchain", "langchain-core", "deepagents", "jsonschema", "cel-python", "jinja2", "httpx"]
SERVICE = [  # the HTTP service and where it keeps conversations (S16)
    "fastapi", "uvicorn", "langgraph-checkpoint-sqlite", "aiosqlite", "langgraph-checkpoint-postgres", "psycopg",
    "psycopg-pool",
]  # fmt: skip
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
    if not slug[0].isalpha() or _taken(slug):
        return f"wf_{slug}"
    return slug


def _taken(slug: str) -> bool:
    """A package of this name would hide a module the export imports (`langgraph`, `email`, ...) on src/."""
    import importlib.util
    import keyword
    import sys

    if keyword.iskeyword(slug) or slug in sys.stdlib_module_names:
        return True
    try:
        return importlib.util.find_spec(slug) is not None
    except (ImportError, ValueError):
        return False


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


def _frameworks(wf: Workflow) -> set[str]:
    """Frameworks of the wrapped agents (`agent.framework` steps), subgraphs included."""
    out = {str(n.config.get("framework")) for n in wf.nodes if n.type == "agent.framework"}
    for n in wf.nodes:
        if n.type == "core.subgraph":
            out |= _frameworks(wf.subworkflow(n))
    return out & set(FRAMEWORKS)


def designed_agents(wf: Workflow) -> list[tuple[Workflow, Any]]:
    """(workflow, node) of every Agent block designed for another framework (S21), subgraphs included."""
    out = [(wf, n) for n in wf.nodes if n.type in designs.DESIGN_TYPES and n.engine in designs.AGENT_FRAMEWORKS]
    for n in wf.nodes:
        if n.type == "core.subgraph":
            out += designed_agents(wf.subworkflow(n))
    return out


def _version(dist: str) -> str | None:
    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:
        return None


def dependencies(wf: Workflow) -> list[str]:
    deps = []
    for dist in PINNED + SERVICE:
        v = _version(dist)
        extra = {"langchain": "[mcp]", "psycopg": "[binary]", "uvicorn": "[standard]"}.get(dist, "")
        deps.append(f"{dist}{extra}=={v}" if v else f"{dist}{extra}")
    for framework in sorted(_frameworks(wf)):  # wrapped agents: their framework, at the version CI tests
        pkg = FRAMEWORKS[framework].package
        if pkg and pkg not in deps:
            deps.append(pkg)
    for _, node in designed_agents(wf):  # agents designed for another framework: its packages, as CI tests them
        if designs.AGENT_FRAMEWORKS[str(node.engine)].own_env:
            continue  # a part: installed in its own environment (S28)
        for pkg in designs.AGENT_FRAMEWORKS[str(node.engine)].packages:
            if pkg not in deps:
                deps.append(pkg)
    for _, model in _models(wf):
        pkg = PROVIDERS.get(model.partition(":")[0], (None, None))[0]
        if pkg and not any(d.startswith(pkg) for d in deps):
            v = _version(pkg)
            deps.append(f"{pkg}=={v}" if v else pkg)
    return deps


def parts(wf: Workflow) -> list[tuple[Workflow, Any]]:
    """(workflow, node) of the designed agents an export runs in their own environment (S28)."""
    return [(o, n) for o, n in designed_agents(wf) if designs.AGENT_FRAMEWORKS[str(n.engine)].own_env]


def _remote_agents(wf: Workflow) -> list[Any]:
    out = [n for n in wf.nodes if n.type == "agent.a2a"]
    for n in wf.nodes:
        if n.type == "core.subgraph":
            out += _remote_agents(wf.subworkflow(n))
    return out


A2A_CONTRACT = {"input": {"type": "string"}, "output": {"type": "string"}}  # A2A messages: text in, text out


def contracts(wf: Workflow, pkg: str) -> dict[str, Any]:
    """Every part of a mixed flow (S28): the LangGraph host, the agents designed for other frameworks (in the host's
    environment or one of their own) and the remote agents it calls over A2A, each with its typed contract."""
    out: dict[str, Any] = {
        "host": {"framework": "langgraph", "path": f"src/{pkg}/graph.py", "packages": dependencies(wf)},
        "parts": [],
        "remote": [],
    }
    for owner, node in designed_agents(wf):
        fw = designs.AGENT_FRAMEWORKS[str(node.engine)]
        out["parts"].append({
            "name": node.name, "node_id": node.id, "framework": fw.id, "label": fw.label,
            "runs": "own environment" if fw.own_env else "host",
            "path": f"parts/{node.name}" if fw.own_env else f"src/{pkg}/agents/{node.name}.py",
            "packages": list(fw.packages), "contract": designs.contract(designs.design_for(owner, node)),
        })  # fmt: skip
    for node in _remote_agents(wf):
        out["remote"].append({"name": node.name, "node_id": node.id, "protocol": "a2a",
                              "url": node.config.get("url"), "contract": A2A_CONTRACT})  # fmt: skip
    return out


def _part_files(owner: Workflow, node: Any) -> dict[str, str | bytes]:
    from agentcanvas.adapters.langchain.parts import worker_source

    fw = designs.AGENT_FRAMEWORKS[str(node.engine)]
    design = designs.design_for(owner, node)
    base = f"parts/{node.name}"
    return {
        f"{base}/agent.py": designs.generate(fw.id, design),
        f"{base}/worker.py": worker_source(),
        f"{base}/requirements.txt": "".join(f"{p}\n" for p in fw.packages),
        f"{base}/contract.json": json.dumps(
            {"name": node.name, "framework": fw.id, **designs.contract(design)}, indent=2
        )
        + "\n",
    }


def _dist(requirement: str) -> str:
    return re.split(r"[\[<>=!~ ]", requirement, maxsplit=1)[0].lower().replace("_", "-")


def _pyproject(wf: Workflow, pkg: str, version: str, extra: list[str] | None = None) -> str:
    """`extra`: requirements the workspace code declares (requirements.txt); the export's own pins win."""
    pinned = dependencies(wf)
    have = {_dist(d) for d in pinned}
    deps = "".join(f"  {json.dumps(d)},\n" for d in [*pinned, *(r for r in extra or [] if _dist(r) not in have)])
    return f'''[project]
name = "{pkg.replace("_", "-")}"
version = "{version}"
description = {json.dumps(f"{wf.name} — exported from AgentCanvas")}
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


def _readme(wf: Workflow, pkg: str, targets: str) -> str:
    return f"""# {one_line(wf.name)}

Exported from AgentCanvas as a service you run in your own environment. {targets}

```bash
make doctor      # is this machine ready? lists what is missing and where the guide explains it
make setup       # .env with a fresh API key; fill in the empty values
make test        # the graph builds and the saved test cases pass, offline
make deploy      # see docs/3-DEPLOY.md
make smoke URL=https://...   # call the deployed service once
```

The service (`src/{pkg}/app.py`) answers the same API as an AgentCanvas deployment: `POST
/v1/deployments/<name>/runs/wait`, `/runs/stream`, `/threads/<id>/resume` and `/runs/<id>/feedback`, with
`Authorization: Bearer <one of API_KEYS>`. `make run` starts it here without Docker (needs uv).

`src/{pkg}/graph.py` is generated from the canvas; `src/agentcanvas/` is the node library it calls;
`src/scripts|tools|nodes/` is the project's workspace code. In your own code: `from {pkg}.graph import build_graph`
and pass your checkpointer and store. `python -m {pkg} '{{"input": {{}}}}'` runs one turn from the command line, and
`langgraph dev` serves it with LangGraph Studio (`pip install "langgraph-cli[inmem]"`).
{_mixed(wf)}"""


def _mixed(wf: Workflow) -> str:
    """The README's section on a mixed flow's parts (S28), or nothing."""
    designed, remote = designed_agents(wf), _remote_agents(wf)
    if not designed and not remote:
        return ""
    lines = []
    for _, n in designed:
        fw = designs.AGENT_FRAMEWORKS[str(n.engine)]
        where = f"`parts/{n.name}/`, in its own Python environment" if fw.own_env else "inside the host"
        lines.append(f"- **{n.name}**: {article(fw.label)} {fw.label} agent, {where}")
    lines += [f"- **{n.name}**: a remote agent over A2A at {n.config.get('url')}, any language" for n in remote]
    own = any(designs.AGENT_FRAMEWORKS[str(n.engine)].own_env for _, n in designed)
    return f"""
## A mixed flow

The LangGraph host runs these parts, each behind a typed contract (`contracts.json`): the conversation goes in, and
an answer matching the part's structured output comes back, or the step fails saying how it broke the contract.

{chr(10).join(lines)}
""" + (
        """
A part's framework pins packages that clash with LangGraph's, so `make install` (and the Docker image) installs it in
`parts/<name>/.venv`. The host runs it in a worker process there; tool calls come back to the host, and the model is
served to it by the host on 127.0.0.1. Set `PART_<NAME>_PYTHON` to use another environment.
"""
        if own
        else ""
    )


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
    secrets = {{k: os.environ[k] for k in SECRETS if k in os.environ}}
    if os.environ.get("DEMO_MODE", "").lower() in ("1", "true", "yes"):  # canned replies, no model keys needed
        from agentcanvas.adapters.langchain.testing import DemoResources

        return DemoResources(secrets=secrets)
    return Resources(secrets=secrets)


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


PARTS_TEST = '''"""Generated with the project (S28): each part of this mixed flow runs and keeps its typed contract."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
from langgraph.checkpoint.memory import InMemorySaver

from agentcanvas.adapters.langchain.testing import DemoResources
from {pkg} import graph as generated

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = json.loads((ROOT / "contracts.json").read_text())
KIT = json.loads((ROOT / "src" / "{pkg}" / "kit.json").read_text())


def test_each_part_says_its_contract():
    for part in CONTRACTS["parts"]:
        if part["runs"] == "own environment":
            own = json.loads((ROOT / part["path"] / "contract.json").read_text())
            assert {{"input": own["input"], "output": own["output"]}} == part["contract"], part["name"]


async def test_the_flow_runs_through_every_part():
    """One run with canned model answers (DEMO_MODE's): each part answers, in its own environment if it has one."""
    build = getattr(generated, "abuild_graph", None)
    resources = DemoResources()
    graph = await build(resources, checkpointer=InMemorySaver()) if build else generated.build_graph(
        resources, checkpointer=InMemorySaver()
    )
    out = await graph.ainvoke(KIT["example_input"], {{"configurable": {{"thread_id": "parts"}}}},
                              context=KIT.get("example_context") or {{}})
    said = {{getattr(m, "name", None): m.content for m in out.get("messages", []) if getattr(m, "type", "") == "ai"}}
    for part in CONTRACTS["parts"]:
        assert part["name"] in said, f"the part {{part['name']}} did not answer"
        schema = part["contract"]["output"]
        if schema.get("type") == "object":
            jsonschema.validate(json.loads(said[part["name"]]), schema)
'''


WORKSPACE_IMPORTS = '''"""The workspace code this workflow names is bundled and imports."""

import importlib

import pytest

ENTRY_POINTS = {paths}


@pytest.mark.parametrize("import_path", ENTRY_POINTS)
def test_workspace_code_imports(import_path):
    module, _, attr = import_path.partition(":")
    assert hasattr(importlib.import_module(module), attr), import_path
'''


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
    target: str = "local",
) -> tuple[str, bytes]:
    """(file name, zip bytes) of the standalone project for `wf`, whose generated code is `source`, with the
    deploy kit for `target` (one of deploykit.TARGETS)."""
    pkg = package_name(wf.name)
    root = pkg.replace("_", "-")
    secrets = sorted(_secret_names(wf.model_dump(mode="json")))
    cases = _exportable_cases(wf, test_cases or [])
    kit = deploykit.files(wf, deploykit.facts(wf, pkg, version, cases), deploykit.TARGETS[target])
    found = bundle.collect(workspace, wf.id, wf.model_dump_json(), source) if workspace is not None else None
    files: dict[str, str | bytes] = {
        "pyproject.toml": _pyproject(wf, pkg, version, found.requirements if found else None),
        "README.md": _readme(wf, pkg, kit.pop("_readme_targets")),
        ".gitignore": ".venv/\nparts/*/.venv/\n__pycache__/\n.env\ndata/\n",
        **kit,
        "langgraph.json": json.dumps(
            {"dependencies": ["."], "graphs": {pkg: f"./src/{pkg}/server.py:make_graph"}, "env": ".env"}, indent=2
        )
        + "\n",
        f"src/{pkg}/__init__.py": repr(f"{one_line(wf.name)} (exported from AgentCanvas).")
        + "\n",  # a literal the name cannot end
        f"src/{pkg}/graph.py": source,
        f"src/{pkg}/server.py": SERVER.format(pkg=pkg, secrets=json.dumps(secrets)),
        f"src/{pkg}/__main__.py": MAIN.format(pkg=pkg),
        "src/agentcanvas/NOTICE.md": "The AgentCanvas node library (Apache-2.0), vendored so the project runs alone.\n",
        "tests/test_smoke.py": SMOKE.format(pkg=pkg, models=json.dumps(sorted({n for n, _ in _models(wf)}))),
        "tests/test_cases.json": json.dumps(cases, indent=2) + "\n",
    }
    for rel in RUNTIME_FILES:
        files[f"src/agentcanvas/{rel}"] = (API_ROOT / rel).read_bytes()
    designed = designed_agents(wf)
    if designed:  # each in its framework's own code (S21), as the Agent block's code view shows it
        files[f"src/{pkg}/agents/__init__.py"] = '"""Agents designed on the canvas for other frameworks."""\n'
        seen: set[str] = set()
        for owner, node in designed:
            if node.name in seen:
                raise InvalidInput(
                    f"two agents designed for other frameworks are both named '{node.name}' (one is in a subflow); "
                    "rename one so each gets its own file in the export"
                )
            seen.add(node.name)
            if designs.AGENT_FRAMEWORKS[str(node.engine)].own_env:  # a part with its own environment (S28)
                files.update(_part_files(owner, node))
            else:
                files[f"src/{pkg}/agents/{node.name}.py"] = designs.generate(
                    str(node.engine), designs.design_for(owner, node)
                )
    if designed or _remote_agents(wf):
        files["contracts.json"] = json.dumps(contracts(wf, pkg), indent=2, ensure_ascii=False) + "\n"
    if designed:
        files["tests/test_parts.py"] = PARTS_TEST.format(pkg=pkg)
    files["src/agentcanvas/adapters/__init__.py"] = ""  # the control plane's adapter registry is not needed
    if workspace is not None:  # exactly the workspace code the workflow uses (S17), and a test that it imports
        assert found is not None
        if found.missing:
            raise InvalidInput(
                "the workflow uses code that is not in the workspace: "
                + ", ".join(found.missing)
                + "; create the file or fix the step's import path, then export again"
            )
        for rel in found.files:
            files[f"src/{rel}"] = (workspace / rel).read_bytes()
        if found.entry_points:
            files["tests/test_workspace_code.py"] = WORKSPACE_IMPORTS.format(paths=json.dumps(found.entry_points))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, content in sorted(files.items()):
            info = zipfile.ZipInfo(f"{root}/{rel}", date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o755 if rel.endswith(".sh") else 0o644) << 16  # deploy scripts run as ./deploy.sh
            z.writestr(info, content)
    return f"{root}.zip", buf.getvalue()
