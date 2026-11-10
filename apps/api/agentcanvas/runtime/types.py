"""Framework-neutral run/thread types exchanged between the runner, the API and adapters."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from agentcanvas.ir.models import Workflow

InterruptKind = Literal["tool_approval", "approval", "input"]
RunMode = Literal["normal", "debug"]


@dataclass
class ResumeSpec:
    """Answer to one pending interrupt. The runner fills in `interrupt_id` when the caller left it out and one
    waits (several waiting need it): through the runner, None reaches an adapter only to go on from a breakpoint."""

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
    labels: dict[str, str] = field(default_factory=dict)  # trace metadata: origin, deployment, version (M6-T6)
    dry_run: bool = False  # stub every risky tool with a recorder (FR-X-02)
    org_id: str | None = None  # the workflow's organisation: long-term memory lives under it (M7-T2)
    user_id: str | None = None  # the person who started or resumed this run; app connectors may act as them (G-7)
    # usage the adapter had not reported when its event stream stopped (cancelled, timed out): its stream is
    # closed by then, so it is handed over here and still counts for the run's tokens and cost
    on_usage: Callable[[dict[str, Any]], None] | None = None
    service_connection: bool = False  # connectors set to the service identity may use it though a person started it
    # deployment and trigger runs: app connectors offer only the tools pinned at publish, unchanged (OWASP ASI04)
    pin_tools: bool = False


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


REPLAYABLE = ("success", "interrupted")  # run statuses whose recording is a full cassette (FR-X-13)


def is_tool_recording(r: Any) -> bool:
    """A recorded tool call in a cassette's `mocks` ({"tool", "args", "output" | "error"}), as opposed to a model
    reply (FR-X-13; the adapter's `Resources.with_mocks` tells them apart the same way)."""
    return isinstance(r, dict) and "tool" in r and ("output" in r or "error" in r) and "content" not in r
