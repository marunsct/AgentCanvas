"""The trajectory of a conversation (G-10): what the main agent, its helpers and the workflow's steps did, as a tree.

Built from the runs' kept events (`runtime/trajectory.py`) and the workflow they ran:

    conversation
    └─ run 1 ─┬─ frame "Triage" (consecutive steps of one frame on the canvas)
              │   └─ step ─┬─ model reply, tool call, framework part (an agent of another framework answered)
              │            └─ helper agent "researcher" (a Planning Agent handed it a task) ─┬─ model reply
              │                                                                              └─ tool call
              ├─ step "subflow" ── its inner steps
              └─ waits for a person / error

Events name the agent that produced them (LangChain's `lc_agent_name`), so a Planning Agent's helpers are told apart
from the agent itself; a step inside a subflow is nested under the subflow's step. Times are milliseconds since the
run began. Framework-neutral: it reads canonical events only.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from agentcanvas.ir.models import Workflow

Kind = Literal["run", "group", "step", "agent", "model", "tool", "message", "part", "interrupt", "error", "note"]
LABEL_CHARS = 140


class TrajectoryNode(BaseModel):
    id: str
    kind: Kind
    label: str
    canvas_node_id: str | None = None
    status: str | None = Field(None, description="steps and runs: running, ok, error, interrupted, success, …")
    start_ms: int | None = Field(None, description="milliseconds since the run began")
    duration_ms: int | None = None
    tokens: int = 0
    started_at: datetime | None = Field(None, description="runs: when it began")
    detail: dict[str, Any] | None = Field(None, description="what the row shows when opened: arguments, replies, …")
    children: list[TrajectoryNode] = Field(default_factory=list)


class ThreadTrajectory(BaseModel):
    thread_id: str
    workflow_id: str
    runs: list[TrajectoryNode] = Field(description="one per run of the conversation, oldest first")


def _short(text: Any) -> str:
    s = " ".join(str(text).split())
    return s if len(s) <= LABEL_CHARS else s[: LABEL_CHARS - 1] + "…"


class _Shape:
    """What the tree needs from the workflow: step labels and names, the subflow each inner step belongs to and the
    canvas frame each top-level step is in."""

    def __init__(self, wf: Workflow | None):
        self.labels: dict[str, str] = {}
        self.names: dict[str, str] = {}
        self.parent: dict[str, str] = {}
        self.frame: dict[str, str] = {}
        if wf is None:
            return
        self._walk(wf, None)
        for g in (wf.metadata or {}).get("groups") or []:
            if isinstance(g, dict) and isinstance(g.get("label"), str) and isinstance(g.get("members"), list):
                for m in g["members"]:
                    if isinstance(m, str):
                        self.frame.setdefault(m, g["label"])

    def _walk(self, wf: Workflow, parent: str | None) -> None:
        for n in wf.nodes:
            self.labels[n.id] = n.label or n.name.replace("_", " ").capitalize()
            self.names[n.id] = n.name
            if parent is not None:
                self.parent[n.id] = parent
            if n.type == "core.subgraph":
                try:
                    self._walk(wf.subworkflow(n), n.id)
                except Exception:  # noqa: BLE001 - an invalid subflow never ran (E018)
                    continue


class _Builder:
    def __init__(self, shape: _Shape, run_id: str):
        self.shape, self.run_id, self.n = shape, run_id, 0
        self.top: list[TrajectoryNode] = []
        self.open: dict[str, list[TrajectoryNode]] = {}  # canvas node id -> its steps still running, innermost last
        self.running: list[TrajectoryNode] = []  # every step still running, in the order they started
        self.last: dict[str, TrajectoryNode] = {}  # canvas node id -> its latest step (events drained after it ended)
        self.by_task: dict[str, TrajectoryNode] = {}

    def node(self, kind: Kind, label: str, t: int | None, **kw: Any) -> TrajectoryNode:
        self.n += 1
        return TrajectoryNode(id=f"{self.run_id}:{self.n}", kind=kind, label=_short(label), start_ms=t, **kw)

    def step_of(self, canvas_id: str | None) -> TrajectoryNode | None:
        if not canvas_id:  # an event that names no step: the step that started last and is still running
            return self.running[-1] if self.running else None
        running = self.open.get(canvas_id)
        return running[-1] if running else self.last.get(canvas_id)

    def into(self, step: TrajectoryNode | None, agent: str | None, t: int | None) -> list[TrajectoryNode]:
        """Where an event of `step` goes: the step, or its helper agent's row when another agent produced it."""
        if step is None:
            return self.top
        own = self.shape.names.get(step.canvas_node_id or "")
        if not agent or agent == own:
            return step.children
        latest = step.children[-1] if step.children else None
        if latest is None or latest.kind != "agent" or (latest.detail or {}).get("agent") != agent:
            latest = self.node("agent", f"Helper agent {agent}", t, detail={"agent": agent})
            step.children.append(latest)
        return latest.children

    def started(self, ev: dict[str, Any]) -> None:
        cid, t = ev.get("node"), ev.get("t")
        step = self.node("step", self.shape.labels.get(cid or "", ev.get("name") or cid or "step"), t,
                         canvas_node_id=cid, status="running")  # fmt: skip
        parent = self.shape.parent.get(cid or "")
        outer = self.step_of(parent) if parent else None
        (outer.children if outer is not None else self.top).append(step)
        self.running.append(step)
        if cid:
            self.open.setdefault(cid, []).append(step)
            self.last[cid] = step
        if ev.get("task"):
            self.by_task[ev["task"]] = step

    def finished(self, ev: dict[str, Any]) -> None:
        cid = ev.get("node") or ""
        step = self.by_task.pop(ev.get("task") or "", None) or self.step_of(cid)
        if step is None:
            return
        step.status = ev.get("status") or "ok"
        if ev.get("duration_ms") is not None:
            step.duration_ms = int(ev["duration_ms"])
        elif step.start_ms is not None and ev.get("t") is not None:
            step.duration_ms = max(0, int(ev["t"]) - step.start_ms)
        if ev.get("error"):
            step.detail = {**(step.detail or {}), "error": ev["error"]}
        for running in (self.open.get(cid) or [], self.running):
            if step in running:
                running.remove(step)

    def custom(self, ev: dict[str, Any]) -> None:
        kind, t, value = ev["type"], ev.get("t"), ev.get("value")
        value = value if isinstance(value, dict) else {"value": value}
        cid = ev.get("node") or value.get("canvas_node_id")
        step = self.step_of(cid)
        if kind == "ac.usage":
            if step is not None:
                step.tokens += int(value.get("input_tokens") or 0) + int(value.get("output_tokens") or 0)
            return
        where = self.into(step, value.get("agent"), t)
        if kind == "ac.model_reply":
            where.append(self.model(value, t))
        elif kind == "ac.tool_io":
            tool = value.get("tool") or value.get("resource") or "tool"
            label = f"{tool} failed" if value.get("error") else f"Tool {tool}"
            detail = {k: value[k] for k in ("tool", "resource", "args", "output", "error") if k in value}
            where.append(self.node("tool", label, t, status="error" if value.get("error") else "ok", detail=detail))
        elif kind == "ac.dry_run":
            where.append(self.node("tool", f"Would call {value.get('tool') or 'a tool'} (dry run)", t, detail=value))
        elif kind == "ac.agent":
            framework = value.get("framework") or "agent"
            label = f"{framework} agent {value.get('node') or ''}".strip()
            label += " skipped" if value.get("skipped") else " answered"
            where.append(self.node("part", label, t, detail=value))
        elif kind == "ac.interrupt":
            label = f"Waits for a person ({value.get('kind') or 'a decision'})"
            detail = {k: value[k] for k in ("id", "kind", "payload") if k in value}
            self.top.append(self.node("interrupt", label, t, canvas_node_id=cid, detail=detail))
        elif kind == "ac.warning":
            self.top.append(self.node("note", str(value.get("message") or "warning"), t, detail=value))
        elif kind == "ac.truncated":
            self.top.append(self.node("note", "Later events were not kept (a very long run)", t))

    def model(self, value: dict[str, Any], t: int | None) -> TrajectoryNode:
        reply, resource = value.get("reply"), value.get("resource") or "model"
        calls = reply.get("tool_calls") if isinstance(reply, dict) else None
        text = reply.get("content") if isinstance(reply, dict) else reply
        if calls:
            helpers = [c.get("args", {}).get("subagent_type") for c in calls if c.get("name") == "task"]
            others = [c.get("name") for c in calls if c.get("name") != "task"]
            parts = [f"hands a task to {h}" for h in helpers if h] + (
                [f"asks for {', '.join(others)}"] if others else []
            )
            label = f"{resource}: {'; '.join(parts) or 'asks for tools'}"
        else:
            label = f"{resource}: {text or '(no text)'}"
        return self.node("model", label, t, detail={"resource": resource, "reply": reply})


def build_run(
    run_id: str,
    events: Iterable[dict[str, Any]] | None,
    wf: Workflow | None,
    *,
    status: str,
    started_at: datetime | None = None,
    index: int = 1,
    error: str | None = None,
) -> TrajectoryNode:
    """One run's tree, from its kept events (None: a run from before they were kept)."""
    shape = _Shape(wf)
    b = _Builder(shape, run_id)
    root = b.node("run", f"Run {index}", 0, status=status, started_at=started_at)
    if events is None:
        root.children = [b.node("note", "This run was recorded before trajectories were kept", None)]
        return root
    events = list(events)
    replied = {
        e.get("node") or (e.get("value") or {}).get("canvas_node_id") for e in events if e["type"] == "ac.model_reply"
    }
    for ev in events:
        kind = ev.get("type")
        if kind == "RUN_STARTED":
            continue
        if kind == "STEP_STARTED":
            b.started(ev)
        elif kind == "STEP_FINISHED":
            b.finished(ev)
        elif kind == "TEXT":
            if ev.get("node") in replied and not ev.get("agent"):
                continue  # the model reply of the same step says it
            step = b.step_of(ev.get("node"))
            b.into(step, ev.get("agent"), ev.get("t")).append(
                b.node("message", ev.get("text") or "", ev.get("t"), detail={"text": ev.get("text")})
            )
        elif kind == "RUN_FINISHED":
            root.status = ev.get("status") or root.status
            root.duration_ms = ev.get("duration_ms") if ev.get("duration_ms") is not None else ev.get("t")
            root.detail = {"output": ev.get("output") or {}}
        elif kind == "RUN_ERROR":
            root.status = "error" if ev.get("code") not in ("cancelled",) else "cancelled"
            root.duration_ms = ev.get("t")
            b.top.append(b.node("error", str(ev.get("message") or error or "the run failed"), ev.get("t"),
                                detail={"code": ev.get("code"), "message": ev.get("message")}))  # fmt: skip
        elif isinstance(kind, str) and kind.startswith("ac."):
            b.custom(ev)
    root.children = _framed(b, shape)
    _total(root)
    return root


def _total(n: TrajectoryNode) -> int:
    """Tokens of a row and everything under it (usage is counted on the outermost step a model ran in)."""
    inner = sum(_total(c) for c in n.children)
    if n.kind == "step":
        n.tokens += inner
    elif n.kind in ("run", "group", "agent"):
        n.tokens = inner
    return n.tokens


def _framed(b: _Builder, shape: _Shape) -> list[TrajectoryNode]:
    """Consecutive top-level steps in the same canvas frame, under one row named after the frame."""
    out: list[TrajectoryNode] = []
    for item in b.top:
        frame = shape.frame.get(item.canvas_node_id or "") if item.kind == "step" else None
        latest = out[-1] if out else None
        if frame and latest is not None and latest.kind == "group" and latest.label == frame:
            latest.children.append(item)
        elif frame:
            out.append(b.node("group", frame, item.start_ms, children=[item]))
        else:
            out.append(item)
    for g in out:
        if g.kind == "group" and g.children:
            ends = [(c.start_ms or 0) + (c.duration_ms or 0) for c in g.children]
            g.duration_ms = max(ends) - (g.start_ms or 0)
            g.status = "error" if any(c.status == "error" for c in g.children) else g.children[-1].status
    return out
