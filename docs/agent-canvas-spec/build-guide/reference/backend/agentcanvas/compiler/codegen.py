"""Compiler: IR -> readable Python module (the code users export and deploy)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from agentcanvas.ir.models import Workflow
from agentcanvas.ir.validate import has_errors, validate

_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    undefined=StrictUndefined,
    keep_trailing_newline=True,
    trim_blocks=False,
    lstrip_blocks=False,
)
_env.filters["py"] = repr  # emit Python literals (e.g. 'text'), not JSON


class CompileError(Exception):
    pass


def _ep(wf: Workflow, node_id: str) -> str:
    return {"START": "START", "END": "END"}.get(node_id) or repr(wf.require_node(node_id).name)


def generate(wf: Workflow, *, format_code: bool = True) -> str:
    diags = validate(wf)
    if has_errors(diags):
        raise CompileError("; ".join(f"{d.code} {d.message}" for d in diags if d.severity == "error"))

    router_ids = {n.id for n in wf.nodes if n.type == "core.router"}
    plain = [
        (_ep(wf, e.source.node), _ep(wf, e.target.node)) for e in wf.flow_edges() if e.source.node not in router_ids
    ]
    routers = []
    for rid in sorted(router_ids):
        mapping = {e.source.route: _ep(wf, e.target.node) for e in wf.flow_edges() if e.source.node == rid}
        body = ", ".join(f"{k!r}: {v}" for k, v in mapping.items())
        routers.append((wf.require_node(rid).name, "{" + body + "}"))

    model_of = {n.id: wf.wired_into(n.id, "model")[0].name for n in wf.nodes if wf.wired_into(n.id, "model")}
    tools_of = {n.id: [t.name for t in wf.wired_into(n.id, "tools")] for n in wf.nodes}

    source = _env.get_template("graph.py.j2").render(
        wf=wf,
        channels=[c.model_dump() for c in wf.state.channels],
        plain_edges=plain,
        router_edges=routers,
        model_of=model_of,
        tools_of=tools_of,
    )
    return ruff_format(source) if format_code else source


def ruff_format(source: str) -> str:
    """Format with ruff so output is deterministic and readable (requires `ruff` installed)."""
    proc = subprocess.run(["ruff", "format", "-"], input=source, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise CompileError(f"generated code is not valid Python:\n{proc.stderr}")
    return proc.stdout
