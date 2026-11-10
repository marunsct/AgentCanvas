"""A workflow's external contract and breaking changes between versions (FR-X-14, basic).

The contract is what callers of a published workflow depend on: the input and output fields, the run context
they must pass, and where a run can pause for a human (callers must resume it).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from agentcanvas.ir.files import is_file_schema
from agentcanvas.ir.models import Workflow

PAUSE_TYPES = {"core.approval": "approval", "core.human_input": "input"}


class Contract(BaseModel):
    input: dict[str, dict[str, Any]] = Field(default_factory=dict)  # field -> JSON Schema
    output: dict[str, dict[str, Any]] = Field(default_factory=dict)
    context: dict[str, dict[str, Any]] = Field(default_factory=dict)  # caller-supplied context fields
    required_context: list[str] = Field(default_factory=list)
    pauses: dict[str, str] = Field(default_factory=dict)  # step name -> approval | input | tool_approval


class ContractChange(BaseModel):
    severity: Literal["breaking", "compatible"]
    where: Literal["input", "output", "context", "pauses"]
    field: str
    message: str


def output_names(wf: Workflow) -> list[str]:
    """The fields a run returns: the chosen output fields, else every output and input-output field.

    Every framework and every export uses this one definition, so an input-only field never comes back.
    """
    return list(wf.settings.output_channels or [c.name for c in wf.state.channels if c.io in ("inout", "out")])


def contract(wf: Workflow) -> Contract:
    public = [c for c in wf.state.channels if c.io != "private"]
    names_in = wf.settings.input_channels or [c.name for c in public if c.io in ("inout", "in")]
    names_out = output_names(wf)
    by_name = {c.name: c for c in wf.state.channels}
    out = Contract(
        input={n: by_name[n].type for n in names_in if n in by_name},
        output={n: by_name[n].type for n in names_out if n in by_name},
        context={f.name: f.type for f in wf.context.fields if f.source == "input"},
        required_context=sorted(f.name for f in wf.context.fields if f.source == "input" and f.required),
    )
    for n in wf.nodes:
        if n.disabled:
            continue
        if n.type in PAUSE_TYPES:
            out.pauses[n.name] = PAUSE_TYPES[n.type]
        elif n.config.get("approve_tools"):
            out.pauses[n.name] = "tool_approval"
    return out


def _enum(schema: dict[str, Any]) -> set[Any] | None:
    values = schema.get("enum")
    return set(map(repr, values)) if isinstance(values, list) else None


def _type(schema: dict[str, Any]) -> Any:
    """A field's type as callers see it: a file field (G-5) is a `file`, not the `object` that carries it."""
    if is_file_schema(schema):
        return "file" if schema.get("type") != "array" else "file list"
    return schema.get("type")


def breaking_changes(old: Contract, new: Contract) -> list[ContractChange]:
    """What a caller of `old` would notice in `new`; `breaking` ones can break existing integrations."""
    changes: list[ContractChange] = []

    def add(severity: Any, where: Any, field: str, message: str) -> None:
        changes.append(ContractChange(severity=severity, where=where, field=field, message=message))

    for f in sorted(old.output.keys() - new.output.keys()):
        add("breaking", "output", f, f"output field '{f}' was removed")
    for f in sorted(new.output.keys() - old.output.keys()):
        add("compatible", "output", f, f"output field '{f}' was added")
    for f in sorted(old.output.keys() & new.output.keys()):
        a, b = old.output[f], new.output[f]
        if _type(a) != _type(b):
            add("breaking", "output", f, f"output field '{f}' changed type {_type(a)} → {_type(b)}")
        elif (ea := _enum(a)) is not None and ((eb := _enum(b)) is None or not eb <= ea):
            add("breaking", "output", f, f"output field '{f}' can now return values callers have not seen")
    for f in sorted(old.input.keys() - new.input.keys()):
        add("breaking", "input", f, f"input field '{f}' was removed (callers sending it are affected)")
    for f in sorted(new.input.keys() - old.input.keys()):
        add("compatible", "input", f, f"input field '{f}' was added")
    for f in sorted(old.input.keys() & new.input.keys()):
        a, b = old.input[f], new.input[f]
        if _type(a) != _type(b):
            add("breaking", "input", f, f"input field '{f}' changed type {_type(a)} → {_type(b)}")
        elif (eb := _enum(b)) is not None and ((ea := _enum(a)) is None or not ea <= eb):
            add("breaking", "input", f, f"input field '{f}' accepts fewer values (enum narrowed)")
    for f in sorted(set(new.required_context) - set(old.required_context)):
        add("breaking", "context", f, f"context field '{f}' is now required")
    for f in sorted(old.context.keys() - new.context.keys()):
        add("breaking", "context", f, f"context field '{f}' was removed")
    for step in sorted(new.pauses.keys() - old.pauses.keys()):
        add("breaking", "pauses", step, f"runs can now pause at '{step}' ({new.pauses[step]}); callers must resume")
    for step in sorted(old.pauses.keys() - new.pauses.keys()):
        add("compatible", "pauses", step, f"runs no longer pause at '{step}'")
    return changes
