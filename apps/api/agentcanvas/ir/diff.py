"""Structural diff of two workflow IRs (M6-T1): what a version changed, element by element and field by field."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from agentcanvas.ir.models import Workflow


class FieldChange(BaseModel):
    path: str  # dotted, e.g. "config.prompt" or "config.cases.0.when"
    before: Any = None
    after: Any = None


class ElementChange(BaseModel):
    id: str
    name: str | None = None  # node / resource name, channel name
    fields: list[FieldChange] = Field(default_factory=list)


class ElementDiff(BaseModel):
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    changed: list[ElementChange] = Field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.changed)


class WorkflowDiff(BaseModel):
    nodes: ElementDiff = Field(default_factory=ElementDiff)
    resources: ElementDiff = Field(default_factory=ElementDiff)
    edges: ElementDiff = Field(default_factory=ElementDiff)
    channels: ElementDiff = Field(default_factory=ElementDiff)  # state channels, by name
    workflow: list[FieldChange] = Field(default_factory=list)  # name, description, context, settings

    @property
    def empty(self) -> bool:
        return all(d.empty for d in (self.nodes, self.resources, self.edges, self.channels)) and not self.workflow

    def describe(self) -> str:
        parts = []
        for label, d in (("step", self.nodes), ("resource", self.resources), ("edge", self.edges)):
            for verb, items in (("added", d.added), ("removed", d.removed), ("changed", d.changed)):
                if items:
                    parts.append(f"{len(items)} {label}{'s' if len(items) != 1 else ''} {verb}")
        if not self.channels.empty:
            parts.append("state changed")
        if self.workflow:
            parts.append("settings changed")
        return ", ".join(parts) or "no changes"


def _fields(before: Any, after: Any, path: str = "") -> list[FieldChange]:
    """Leaf-level differences between two JSON values (objects recurse, lists of equal length recurse)."""
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        out: list[FieldChange] = []
        for key in sorted(set(before) | set(after), key=str):
            out += _fields(before.get(key), after.get(key), f"{path}.{key}" if path else str(key))
        return out
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        out = []
        for i, (b, a) in enumerate(zip(before, after, strict=True)):
            out += _fields(b, a, f"{path}.{i}" if path else str(i))
        return out
    return [FieldChange(path=path or "(value)", before=before, after=after)]


def _elements(before: dict[str, dict], after: dict[str, dict], name_key: str | None = "name") -> ElementDiff:
    diff = ElementDiff(
        added=[k for k in after if k not in before],
        removed=[k for k in before if k not in after],
    )
    for key in before.keys() & after.keys():
        fields = _fields(before[key], after[key])
        if fields:
            name = after[key].get(name_key) if name_key else None
            diff.changed.append(ElementChange(id=key, name=name, fields=fields))
    diff.changed.sort(key=lambda c: c.id)
    return diff


def _dump(items: list[Any]) -> dict[str, dict]:
    return {x.id: x.model_dump(mode="json", exclude_none=True) for x in items}


def diff_workflows(before: Workflow, after: Workflow) -> WorkflowDiff:
    channels_b = {c.name: c.model_dump(mode="json", exclude_none=True) for c in before.state.channels}
    channels_a = {c.name: c.model_dump(mode="json", exclude_none=True) for c in after.state.channels}
    top = {"name", "description", "kind", "context", "settings"}
    return WorkflowDiff(
        nodes=_elements(_dump(before.nodes), _dump(after.nodes)),
        resources=_elements(_dump(before.resources), _dump(after.resources)),
        edges=_elements(_dump(before.edges), _dump(after.edges), name_key=None),
        channels=_elements(channels_b, channels_a),
        workflow=_fields(
            before.model_dump(mode="json", include=top, exclude_none=True),
            after.model_dump(mode="json", include=top, exclude_none=True),
        ),
    )
