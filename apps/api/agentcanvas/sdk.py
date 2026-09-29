"""AgentCanvas SDK for workspace code (spec doc 05 §3, §5; doc 15 §6).

Decorators only attach metadata: the control plane discovers them by reading the source (AST), never by
importing it. The same module ships with exported projects.

    from agentcanvas.sdk import canvas_node, on

    @canvas_node(name="acme.score_lead", label="Score lead", category="CRM", writes=["score"])
    def score_lead(state: dict, cfg: dict) -> dict: ...

    @on("crm.lead.created", filter="event.data.amount > 50000", dedup_key="event.data.lead_id")
    def qualify(event, act):
        act.start_workflow("Lead qualification", input={"lead": event.data})
"""

from __future__ import annotations

import contextvars
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

_SECRETS: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar("agentcanvas_secrets", default=None)


def canvas_node(
    *,
    name: str,
    label: str | None = None,
    category: str = "Workspace",
    icon: str = "box",
    color: str | None = None,
    description: str = "",
    config: Any = None,
    reads: Sequence[str] = (),
    writes: Sequence[str] = (),
    side_effects: Sequence[str] = (),
) -> Callable:
    """Declare a custom widget: `fn(state, cfg) -> dict` (or `fn(state, cfg, runtime)`; sync or async).

    `config` is a Pydantic model (its fields become the config form) or a JSON Schema dict.
    """

    def wrap(fn: Callable) -> Callable:
        fn.__canvas_node__ = {  # type: ignore[attr-defined]
            "name": name, "label": label, "category": category, "icon": icon, "color": color,
            "description": description, "config": config, "reads": list(reads), "writes": list(writes),
            "side_effects": list(side_effects),
        }  # fmt: skip
        return fn

    return wrap


def on(event_type: str, *, filter: str | None = None, dedup_key: str | None = None) -> Callable:
    """Subscribe a handler `fn(event, act)` to an event type (FR-X-22). `filter` is a CEL expression over `event`."""

    def wrap(fn: Callable) -> Callable:
        fn.__canvas_on__ = {"type": event_type, "filter": filter, "dedup_key": dedup_key}  # type: ignore[attr-defined]
        return fn

    return wrap


@dataclass
class Event:
    type: str
    data: dict[str, Any]
    id: str = ""
    source: str = "api"


@dataclass
class Actions:
    """What a handler wants to happen. Handlers only *describe* actions; the platform performs them afterwards
    (or not, in a dry run), so handler code never holds platform credentials."""

    recorded: list[dict[str, Any]] = field(default_factory=list)

    def start_workflow(
        self, workflow: str, *, input: dict[str, Any] | None = None, thread_id: str | None = None
    ) -> None:
        self.recorded.append(
            {"action": "start_workflow", "workflow": workflow, "input": input or {}, "thread_id": thread_id}
        )

    def resume(self, thread_id: str, value: Any = None) -> None:
        self.recorded.append({"action": "resume", "thread_id": thread_id, "value": value})

    def emit(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        self.recorded.append({"action": "emit", "type": event_type, "data": data or {}})


def secret(name: str) -> str:
    """A project secret inside a run (in-process scripts and custom nodes; not available in sandboxes)."""
    values = _SECRETS.get()
    if values is None or name not in values:
        raise KeyError(f"secret '{name}' is not available here")
    return values[name]
