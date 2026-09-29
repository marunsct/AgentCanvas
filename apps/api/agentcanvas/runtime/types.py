"""Framework-neutral run/thread types exchanged between the runner, the API and adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from agentcanvas.ir.models import Workflow

InterruptKind = Literal["tool_approval", "approval", "input"]
RunMode = Literal["normal", "debug"]


@dataclass
class ResumeSpec:
    """Answer to one pending interrupt (or to all, when `interrupt_id` is None)."""

    value: Any
    interrupt_id: str | None = None


@dataclass
class RunSpec:
    workflow: Workflow
    thread_id: str
    run_id: str
    input: dict[str, Any] | None = None  # None = continue (after a breakpoint) / resume
    resume: ResumeSpec | None = None
    mode: RunMode = "normal"
    breakpoints_before: list[str] = field(default_factory=list)  # canvas node ids (debug runs only)
    breakpoints_after: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    durability: Literal["exit", "async", "sync"] | None = None
    checkpoint_id: str | None = None  # start from a past checkpoint (replay / fork)
    pins: dict[str, dict[str, Any]] = field(default_factory=dict)  # canvas node id -> output replayed (debug)
    mocks: dict[str, list[Any]] = field(default_factory=dict)  # model resource name -> scripted replies (debug)


@dataclass
class InterruptInfo:
    id: str
    kind: InterruptKind
    canvas_node_id: str | None
    payload: Any


@dataclass
class ThreadState:
    values: dict[str, Any]
    next: list[str]  # canvas node ids that will run next
    interrupts: list[InterruptInfo]
    checkpoint_id: str | None
    step: int | None = None


@dataclass
class CheckpointInfo:
    checkpoint_id: str
    parent_checkpoint_id: str | None
    step: int | None
    source: str | None  # input | loop | update | fork
    next: list[str]  # canvas node ids
    writes: dict[str, Any] | None  # {canvas_node_id: update} written by the tasks that ran *after* this checkpoint
    created_at: str | None
    values: dict[str, Any] | None = None  # state at this checkpoint (only when requested)


def interrupt_kind(payload: Any) -> InterruptKind:
    if isinstance(payload, dict):
        if "action_requests" in payload:
            return "tool_approval"
        if payload.get("kind") == "approval":
            return "approval"
    return "input"
