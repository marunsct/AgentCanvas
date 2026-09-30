"""Code generator: IR -> readable Python module (the code users export and deploy).

Renders the same Execution Plan the interpreter runs (ADR 0004), formatted with ruff so output is
deterministic (TR-COMP-01).
"""

from __future__ import annotations

import pprint
import subprocess
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from agentcanvas.adapters.langchain.plan import lower
from agentcanvas.errors import WorkflowHasErrors
from agentcanvas.ir.models import Workflow
from agentcanvas.registry import Registry
from agentcanvas.validation import has_errors, validate

_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    undefined=StrictUndefined,
    keep_trailing_newline=True,
    trim_blocks=False,
    lstrip_blocks=False,
)
_env.filters["py"] = repr  # emit Python literals (e.g. 'text'), not JSON
_env.filters["pp"] = lambda v: pprint.pformat(v, width=100, sort_dicts=True)

CompileError = WorkflowHasErrors  # backwards-compatible name


def generate(wf: Workflow, *, format_code: bool = True, registry: Registry | None = None) -> str:
    diags = validate(wf, registry)
    if has_errors(diags):
        errors = [d.model_dump() for d in diags if d.severity == "error"]
        raise WorkflowHasErrors("; ".join(f"{d['code']} {d['message']}" for d in errors), errors=errors)
    plan = lower(wf, registry)
    source = _env.get_template("graph.py.j2").render(
        plan=plan, options=_options, refs=_refs, sub_fn=_sub_fn, sub_prefix=_sub_prefix
    )
    return ruff_format(source) if format_code else source


def _options(options: dict[str, Any]) -> str:
    """`**ac.node_options(...)` call for a node, or '' when it has no policies/error route."""
    args = {k: v for k, v in options.items() if v is not None}
    if not args:
        return ""
    return ", **ac.node_options(" + ", ".join(f"{k}={v!r}" for k, v in sorted(args.items())) + ")"


def _refs(names: list[str], many: set[str]) -> str:
    """Render wired resource names as a Python list body; list-valued resources are spread (`*github`)."""
    return ", ".join(f"*{n}" if n in many else n for n in names)


def _sub_fn(path: list[str] | tuple[str, ...]) -> str:
    """Builder function of a (nested) subgraph: `_build_research__summarize`."""
    return "_build_" + "__".join(path)


def _sub_prefix(path: list[str] | tuple[str, ...]) -> str:
    return "_" + "__".join(path).upper() + "_"


def ruff_format(source: str) -> str:
    """Format with ruff so output is deterministic and readable (requires `ruff` installed)."""
    proc = subprocess.run(["ruff", "format", "-"], input=source, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise WorkflowHasErrors(f"generated code is not valid Python:\n{proc.stderr}")
    return proc.stdout
