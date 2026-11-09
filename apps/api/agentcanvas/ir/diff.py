"""Structural diff of two workflow IRs (M6-T1): what a version changed, element by element and field by field."""

from __future__ import annotations

import json
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


# ------------------------------------------------------------------ as Markdown (G-14) ----
def _code(value: Any, limit: int = 80) -> str:
    """A value as a Markdown code span that stays on its line, in a table cell and without a mention or a link."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(text.split()).replace("`", "'").replace("|", "\\|")
    if not text:
        return "(empty)"
    return "`" + (text[: limit - 1] + "…" if len(text) > limit else text) + "`"


def _label(items: dict[str, Any], key: str) -> str:
    el = items.get(key)
    name = getattr(el, "name", None) or key
    kind = getattr(el, "type", None)
    return _code(name) + (f" ({_code(kind)})" if kind else "")


def markdown(diff: WorkflowDiff, before: Workflow, after: Workflow, *, rows: int = 20) -> str:
    """The diff as Markdown for a pull request description: one section per kind of element, a table of field changes
    for each changed element. User text only ever appears in code spans, and long lists are cut at `rows`."""
    if diff.empty:
        return "No changes to the workflow.\n"
    out: list[str] = []
    now = {x.id: x for x in [*after.nodes, *after.resources]}
    was = {x.id: x for x in [*before.nodes, *before.resources]}
    for title, d in (("Steps", diff.nodes), ("Resources (models, tools, connections)", diff.resources)):
        if d.empty:
            continue
        out += [f"#### {title}", ""]
        out += [f"- added {_label(now, i)}" for i in d.added[:rows]]
        out += [f"- removed {_label(was, i)}" for i in d.removed[:rows]]
        if len(d.added) > rows or len(d.removed) > rows:
            out.append("- … and more added or removed")
        for change in d.changed[:rows]:
            out += ["", f"**Changed** {_label(now, change.id)}", "", "| Field | Before | After |", "|---|---|---|"]
            out += [f"| {_code(f.path)} | {_code(f.before)} | {_code(f.after)} |" for f in change.fields[:rows]]
            if len(change.fields) > rows:
                out.append(f"| … | {len(change.fields) - rows} more fields | |")
        if len(d.changed) > rows:
            out += ["", f"… and {len(d.changed) - rows} more changed."]
        out.append("")
    if not diff.edges.empty:
        e = diff.edges
        out += ["#### Connections between steps", "", f"{len(e.added)} added, {len(e.removed)} removed, "
                f"{len(e.changed)} changed.", ""]  # fmt: skip
    if not diff.channels.empty:
        c = diff.channels
        out += ["#### State fields", ""]
        out += [f"- added {_code(n)}" for n in c.added[:rows]] + [f"- removed {_code(n)}" for n in c.removed[:rows]]
        out += [f"- changed {_code(x.id)}: " + ", ".join(_code(f.path) for f in x.fields[:5]) for x in c.changed[:rows]]
        out.append("")
    if diff.workflow:
        out += ["#### Workflow settings", "", "| Field | Before | After |", "|---|---|---|"]
        out += [f"| {_code(f.path)} | {_code(f.before)} | {_code(f.after)} |" for f in diff.workflow[:rows]]
        out.append("")
    return "\n".join(out).rstrip() + "\n"
