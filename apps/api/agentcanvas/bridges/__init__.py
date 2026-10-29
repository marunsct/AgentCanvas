"""Whole workflows on other frameworks through Open Agent Spec (S22, preview).

The workflow is written as an Agent Spec flow (`agentcanvas.agentspec`) and each framework's own Agent Spec loader
runs it: LangGraph and WayFlow through `pyagentspec` and `wayflowcore`, OpenAI Agents SDK through the code
`pyagentspec` generates for it. The export is a small project around `flow.json`: a runner, the steps AgentCanvas
runs itself as Python functions, pinned packages, a README with what does not carry over (the gaps), and tests
against a fake model server.

Nothing here imports those packages: the gaps come from the flow document and from what each loader is known to
accept; CI runs the exports for real (`scripts/smoke_frameworks.py bridge_<target>`).
"""

from __future__ import annotations

import io
import json
import keyword
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentcanvas import agentspec, designs
from agentcanvas.errors import InvalidInput
from agentcanvas.ir.models import Workflow
from agentcanvas.validation.analysis import refuse_plain_secrets

KIT = Path(__file__).resolve().parent / "kit"
FAKE_MODEL = Path(designs.__file__).resolve().parent / "kit" / "fake_model.py"


@dataclass(frozen=True)
class Target:
    id: str
    label: str
    packages: tuple[str, ...]
    nodes: frozenset[str]  # Agent Spec node types its loader runs
    summary: str


_ALL_NODES = frozenset({
    "StartNode", "EndNode", "LlmNode", "AgentNode", "ToolNode", "BranchingNode", "InputMessageNode",
    "OutputMessageNode",
})  # fmt: skip
TARGETS: dict[str, Target] = {
    t.id: t
    for t in [
        Target("langgraph", "LangGraph (through Agent Spec)", ("pyagentspec[langgraph]==26.3.1", "jinja2>=3.1"),
               _ALL_NODES, "pyagentspec builds a LangGraph graph from the flow when it starts."),
        Target("wayflow", "WayFlow", ("wayflowcore==26.3.1", "jinja2>=3.1"), _ALL_NODES,
               "WayFlow, Oracle's Agent Spec runtime, runs the flow as a WayFlow Flow."),
        Target("openai_agents", "OpenAI Agents SDK", ("pyagentspec==26.3.1", "openai-agents==0.23.1"),
               frozenset({"StartNode", "EndNode", "LlmNode", "AgentNode", "BranchingNode"}),
               "pyagentspec writes the flow as OpenAI Agents SDK code when it starts; it takes AI steps, agents and "
               "simple paths only."),
    ]
}  # fmt: skip
NOT_OFFERED = {
    "AutoGen": "its Agent Spec loader takes single agents, not flows",
    "Microsoft Agent Framework": "its Agent Spec loader takes single agents, not flows",
    "CrewAI": "pyagentspec has no CrewAI loader",
}
BUILTIN_STEPS = {"core.set"}  # steps the kit runs itself; others are written in steps.py


@dataclass
class Gap:
    blocks: bool  # the flow cannot run there until this is dealt with
    text: str


def gaps(wf: Workflow, target: str) -> list[Gap]:
    """What does not carry over to `target`, most serious first."""
    t = TARGETS[target]
    doc, report = agentspec.export_flow(wf)
    out: list[Gap] = []
    refs = doc["$referenced_components"]
    nodes = [refs[r["$component_ref"]] for r in doc["nodes"]]
    for n in nodes:
        kind = n["component_type"]
        meta = (n.get("metadata") or {}).get("agentcanvas") or {}
        if kind not in t.nodes:
            what = {"ToolNode": "a step AgentCanvas runs itself", "InputMessageNode": "a question for a person"}
            out.append(Gap(True, f"'{n['name']}' is {what.get(kind, kind)}; {t.label} flows cannot have it."))
        elif kind == "ToolNode" and meta.get("type") not in BUILTIN_STEPS:
            fn = step_function_name(n["name"])
            out.append(Gap(True, f"'{n['name']}' ({meta.get('type')}) runs inside AgentCanvas: write it as the "
                                 f"function {fn} in steps.py before the flow can run."))  # fmt: skip
        elif kind == "BranchingNode" and not (n.get("metadata") or {}).get("branching_key"):
            out.append(Gap(True, f"Router '{n['name']}': its rules are not a simple state.x == \"value\" check, so "
                                 "the other framework cannot pick its path."))  # fmt: skip
        elif kind == "AgentNode" and (n.get("agent") or {}).get("component_type") == "A2AAgent":
            out.append(Gap(True, f"'{n['name']}' is a remote (A2A) agent; this export has not been tried with them."))
    starts: dict[tuple[str, Any], int] = {}
    for e in doc["control_flow_connections"]:
        key = (e["from_node"]["$component_ref"], e.get("from_branch"))
        starts[key] = starts.get(key, 0) + 1
    if any(v > 1 for v in starts.values()):
        out.append(Gap(True, "Steps that run side by side: Agent Spec flows follow one path, and the other framework "
                             "refuses or runs only one of them."))  # fmt: skip
    if any(n.type == "core.subgraph" for n in wf.nodes):
        out.append(Gap(True, "Subflows are not exported to other frameworks yet."))
    if target == "openai_agents":  # its code generator returns only the input or an agent's answer (S23)
        for e in doc.get("data_flow_connections") or []:
            src = refs[e["source_node"]["$component_ref"]]
            if refs[e["destination_node"]["$component_ref"]]["component_type"] == "EndNode" and src[
                "component_type"
            ] not in ("StartNode", "AgentNode"):
                out.append(Gap(True, f"The result '{e['source_output']}' comes from '{src['name']}', which is not an "
                                     f"agent; {t.label} flows can only return an agent's answer."))  # fmt: skip
    if target == "wayflow":  # WayFlow checks types: text written into a list field stops the flow (S23)
        lists = {c.name for c in wf.state.channels if c.type.get("type") == "array"}
        for n in wf.nodes:
            values = n.config.get("values")
            if n.type == "core.set" and isinstance(values, dict) and (bad := sorted(lists & set(values))):
                out.append(Gap(True, f"'{n.name}' writes text into the list field '{bad[0]}'; {t.label} expects a "
                                     "list there."))  # fmt: skip
    out += [Gap(False, r) for r in report]
    out.append(Gap(False, "Runs, approvals, history and traces stay in the other framework: nothing reports back to "
                          "AgentCanvas."))  # fmt: skip
    if target == "openai_agents":
        out.append(Gap(False, "The generated code names each model only, so every model uses the first model's "
                              "address and key."))  # fmt: skip
    seen: set[str] = set()
    return [g for g in sorted(out, key=lambda g: not g.blocks) if not (g.text in seen or seen.add(g.text))]


def models(wf: Workflow) -> dict[str, dict[str, str | None]]:
    """Agent Spec model config id -> its address and the environment variable holding its key."""
    doc, _ = agentspec.export_flow(wf)
    secrets = {r.name: r.config.get("api_key") for r in wf.resources if r.type == "model.chat"}
    out: dict[str, dict[str, str | None]] = {}
    for comp in doc["$referenced_components"].values():
        if not str(comp.get("component_type", "")).endswith("Config") or "model_id" not in comp:
            continue
        meta = (comp.get("metadata") or {}).get("agentcanvas") or {}
        provider = str(meta.get("model", "")).partition(":")[0]
        url = comp.get("url") or designs.OPENAI_URLS.get(provider)
        own = secrets.get(str(meta.get("resource")))  # the model's own secret, when it names one
        key_env = own["$secret"] if isinstance(own, dict) and own.get("$secret") else None
        out[comp["id"]] = {"url": url or None, "key_env": key_env or designs.KEY_ENVS.get(provider, "MODEL_API_KEY")}
    return out


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "workflow"


def export_files(wf: Workflow, target: str) -> dict[str, str]:
    """The project for `target`, path -> text."""
    if target not in TARGETS:
        raise InvalidInput(f"choose one of {', '.join(TARGETS)}")
    t = TARGETS[target]
    if any(n.type == "core.subgraph" for n in wf.nodes):
        raise InvalidInput("whole-flow exports to other frameworks do not take subflows yet")
    refuse_plain_secrets(wf)
    doc, _ = agentspec.export_flow(wf)
    found = gaps(wf, target)
    root = f"{_slug(wf.name)}-{target.replace('_', '-')}"
    key_envs = sorted({m["key_env"] for m in models(wf).values() if m["key_env"]})
    sample = {i["title"]: "hello" for i in doc.get("inputs") or [] if i.get("type", "string") == "string"}
    others = [i["title"] for i in doc.get("inputs") or [] if i.get("type", "string") != "string"]
    blocked = any(g.blocks for g in found)
    tool_steps = sorted(
        c["name"] for c in doc["$referenced_components"].values()
        if c.get("component_type") == "ToolNode"
        and ((c.get("metadata") or {}).get("agentcanvas") or {}).get("type") not in BUILTIN_STEPS
    )  # fmt: skip
    files = {
        "flow.json": json.dumps(doc, indent=2) + "\n",
        "models.json": json.dumps(models(wf), indent=2) + "\n",
        "flowkit.py": (KIT / "flowkit.py").read_text(),
        "steps.py": _steps(tool_steps),
        "main.py": MAIN.replace("{target}", target).replace("{label}", t.label),
        "tests/__init__.py": "",
        "tests/fake_model.py": FAKE_MODEL.read_text(),
        "tests/test_flow.py": TESTS.replace("{target}", target)
        .replace("{sample}", repr(sample))
        .replace("{runs}", "False" if blocked or others else "True"),
        ".env.example": "".join(f"{k}=\n" for k in key_envs) + "# MODEL_BASE_URL=http://localhost:11434/v1  # one "
        "server for every model\n",
        "pyproject.toml": PYPROJECT.replace("{name}", root).replace(
            "{packages}", ",\n".join(f'    "{p}"' for p in t.packages)
        ),
        "GAPS.md": _gaps_md(wf, t, found),
        "README.md": _readme(wf, t, found, key_envs, sample, tool_steps),
    }
    return {f"{root}/{k}": v for k, v in files.items()}


def export_zip(wf: Workflow, target: str) -> tuple[str, bytes]:
    files = export_files(wf, target)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path, text in sorted(files.items()):
            z.writestr(path, text)
    return next(iter(files)).split("/", 1)[0] + ".zip", buf.getvalue()


def step_function_name(step: str) -> str:
    """A step's function in steps.py: its name, with a trailing _ when that is a Python keyword."""
    return f"{step}_" if keyword.iskeyword(step) else step


def _steps(names: list[str]) -> str:
    body = "\n\n".join(
        f"def {step_function_name(n)}(**state):\n"
        f'    """The step \'{n}\': it gets the state fields it reads and returns what it writes '
        f'(one value, or a dict for several)."""\n    raise NotImplementedError("write the step \'{n}\'")'
        for n in names
    )
    return (
        '"""Steps of this flow that ran inside AgentCanvas. A function here, named after the step, replaces the\n'
        'built-in one (AgentCanvas writes the "Set values" steps for you)."""\n'
        + ("\n\n" + body + "\n" if body else "")
    )


def _gaps_md(wf: Workflow, t: Target, found: list[Gap]) -> str:
    lines = [f"# What does not carry over to {t.label}", ""]
    lines += [f"- **{'Must fix' if g.blocks else 'Differs'}:** {g.text}" for g in found] or ["Nothing."]
    return "\n".join(lines) + "\n"


def _readme(wf: Workflow, t: Target, found: list[Gap], keys: list[str], sample: dict, steps: list[str]) -> str:
    must = [g for g in found if g.blocks]
    first = "**Before it runs:** " + " ".join(g.text for g in must) if must else "Nothing blocks it from running."
    return f"""# {wf.name} on {t.label} (preview)

The AgentCanvas workflow "{wf.name}" as an [Open Agent Spec](https://oracle.github.io/agent-spec) flow
(`flow.json`), run by {t.label}. {t.summary}

**Preview.** This path goes through Agent Spec, so some things work differently than in AgentCanvas or not at all:
see [GAPS.md](GAPS.md). {first}

## Run it

```bash
uv sync
cp .env.example .env   # then fill in {", ".join(keys) or "your model keys"}
uv run --env-file .env python main.py '{json.dumps(sample)}' "your message"
```

The first argument is the flow's inputs as JSON; the second is the opening chat message, for agents. Questions
the flow asks are answered on the terminal.

## Files

- `flow.json`: the workflow in Agent Spec. AgentCanvas can import it back (Versions → Import an Agent Spec file).
- `flowkit.py`: loads the flow with {t.label}'s Agent Spec loader, with each model's address (`models.json`) and
  key, and the steps AgentCanvas ran itself.
- `steps.py`: {"write " + ", ".join(steps) + " here" if steps else "your own versions of steps, if you want them"}.
- `tests/`: `uv run pytest` runs the flow against a fake model server, no key needed.
"""


MAIN = '''"""Run the flow on {label}: python main.py '<inputs as JSON>' "<opening message>"."""

import json
import sys

import flowkit


def ask(question: str) -> str:
    return input(f"{question}\\n> ")


if __name__ == "__main__":
    inputs = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    message = sys.argv[2] if len(sys.argv) > 2 else ""
    print(json.dumps(flowkit.run("{target}", inputs, message, ask), indent=2, default=str))
'''

TESTS = '''"""The flow loads on this framework and, when nothing blocks it, runs against a fake model server."""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import fake_model  # noqa: E402
import flowkit  # noqa: E402

RUNS = {runs}  # False when GAPS.md lists something to fix first


@pytest.fixture(autouse=True)
def fake_models(monkeypatch):
    monkeypatch.setenv("MODEL_BASE_URL", fake_model.serve() + "/v1")


def test_the_flow_reads():
    doc = flowkit.load()
    assert doc["component_type"] == "Flow"


@pytest.mark.skipif(not RUNS, reason="GAPS.md lists something to fix before the flow runs")
def test_the_flow_runs():
    out = flowkit.run("{target}", {sample}, "hello", lambda question: "yes")
    assert out, "the flow returned nothing"
'''

PYPROJECT = """[project]
name = "{name}"
version = "0.1.0"
requires-python = ">=3.11,<3.15"
dependencies = [
{packages},
]

[dependency-groups]
dev = ["pytest>=8"]

[tool.uv]
package = false
"""
