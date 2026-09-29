"""The run orchestrator (build-guide/05 §5, review v0.6 V-10). Framework-neutral: it talks to adapters.

Each run executes in a background task that writes canonical events into a per-run buffer. HTTP
clients *subscribe* to that buffer, so a run survives a browser disconnect, can be re-attached
(`after=seq`) and cancelled. The runner also keeps the control DB in sync: thread/run status and
the Inbox (`interrupt_task` rows).
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select, update

from agentcanvas.adapters.spi import FrameworkAdapter
from agentcanvas.db.models import InterruptTask, Run, Thread
from agentcanvas.db.session import Database
from agentcanvas.errors import Conflict, NotFound
from agentcanvas.ids import new_id
from agentcanvas.ir.canonical import ir_hash
from agentcanvas.ir.models import Workflow
from agentcanvas.log import get_logger
from agentcanvas.runtime.types import ResumeSpec, RunMode, RunSpec

log = get_logger(__name__)
ResourcesFactory = Callable[[str, Workflow], Any]


@dataclass
class RunHandle:
    run_id: str
    thread_id: str
    workflow_id: str
    events: list[dict[str, Any]] = field(default_factory=list)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    changed: asyncio.Condition = field(default_factory=asyncio.Condition)
    task: asyncio.Task | None = None
    status: str = "running"
    finished_at: float | None = None


class Runner:
    def __init__(
        self,
        db: Database,
        adapter: FrameworkAdapter,
        resources_factory: ResourcesFactory | None = None,
        keep_finished: int = 500,
    ):
        self.db = db
        self.adapter = adapter
        self.resources_factory: ResourcesFactory = resources_factory or (lambda _t, _w: adapter.default_resources())
        self.handles: OrderedDict[str, RunHandle] = OrderedDict()
        self.active_by_thread: dict[str, str] = {}
        self._keep = keep_finished

    def resources(self, thread_id: str, wf: Workflow) -> Any:
        return self.resources_factory(thread_id, wf)

    # --------------------------------------------------------------- start ----
    async def start(
        self,
        wf: Workflow,
        *,
        thread_id: str | None = None,
        input: dict[str, Any] | None = None,
        resume: ResumeSpec | None = None,
        mode: RunMode = "normal",
        breakpoints_before: list[str] | None = None,
        breakpoints_after: list[str] | None = None,
        context: dict[str, Any] | None = None,
        durability: Literal["exit", "async", "sync"] | None = None,
        checkpoint_id: str | None = None,
        user_id: str | None = None,
        decided_by: str | None = None,
        context_from: str | None = None,
    ) -> RunHandle:
        """Start a run. `context=None` reuses the context of the last run on `context_from` (default: this thread)."""
        thread_id = thread_id or new_id("th")
        if thread_id in self.active_by_thread:  # double-texting policy "reject" (FR-RUN-11 later adds others)
            raise Conflict(f"thread '{thread_id}' already has an active run", run_id=self.active_by_thread[thread_id])
        run_id = new_id("run")
        async with self.db.sessionmaker() as s:
            thread = await s.get(Thread, thread_id)
            if thread is None:
                s.add(
                    Thread(id=thread_id, workflow_id=wf.id, status="running", created_by=user_id, title=_title(input))
                )
            elif thread.workflow_id != wf.id:
                raise Conflict(f"thread '{thread_id}' belongs to workflow '{thread.workflow_id}'")
            else:
                thread.status = "running"
            if context is None:
                context = await self._last_context(s, context_from or thread_id)
            await s.flush()
            s.add(
                Run(id=run_id, thread_id=thread_id, ir_hash=ir_hash(wf), mode=mode, status="running", context=context)
            )
            if resume is not None:
                await self._decide_interrupts(s, thread_id, resume, decided_by or user_id)
            await s.commit()

        spec = RunSpec(
            workflow=wf,
            thread_id=thread_id,
            run_id=run_id,
            input=input,
            resume=resume,
            mode=mode,
            breakpoints_before=breakpoints_before or [],
            breakpoints_after=breakpoints_after or [],
            context=context or {},
            durability=durability,
            checkpoint_id=checkpoint_id,
        )
        handle = RunHandle(run_id=run_id, thread_id=thread_id, workflow_id=wf.id)
        self.handles[run_id] = handle
        self.active_by_thread[thread_id] = run_id
        handle.task = asyncio.create_task(self._pump(handle, spec), name=f"run:{run_id}")
        self._evict()
        return handle

    @staticmethod
    async def _last_context(s: Any, thread_id: str) -> dict[str, Any]:
        q = select(Run.context).where(Run.thread_id == thread_id).order_by(Run.id.desc()).limit(1)
        return (await s.scalar(q)) or {}

    async def _decide_interrupts(self, s: Any, thread_id: str, resume: ResumeSpec, decided_by: str | None) -> None:
        q = update(InterruptTask).where(InterruptTask.thread_id == thread_id, InterruptTask.status == "pending")
        if resume.interrupt_id:
            q = q.where(InterruptTask.id == resume.interrupt_id)
        decision = resume.value if isinstance(resume.value, dict) else {"value": resume.value}
        await s.execute(
            q.values(status="decided", decision=decision, decided_by=decided_by, decided_at=datetime.now(UTC))
        )

    # ---------------------------------------------------------------- pump ----
    async def _emit(self, h: RunHandle, event: dict[str, Any]) -> None:
        event = {**event, "seq": len(h.events) + 1}
        h.events.append(event)
        async with h.changed:
            h.changed.notify_all()

    async def _pump(self, h: RunHandle, spec: RunSpec) -> None:
        started = time.perf_counter()
        await self._emit(
            h,
            {
                "type": "RUN_STARTED",
                "runId": h.run_id,
                "threadId": h.thread_id,
                "workflowId": h.workflow_id,
                "mode": spec.mode,
            },
        )
        status, error, interrupts = "error", None, []
        try:
            resources = self.resources(spec.thread_id, spec.workflow)
            async for raw in self.adapter.execute(spec, resources):
                ev = raw
                if ev["type"] == "RUN_FINISHED":
                    interrupts = ev.get("interrupts", [])
                    status = "interrupted" if ev["outcome"] == "interrupt" else "success"
                    ev = {
                        **raw,
                        "runId": h.run_id,
                        "threadId": h.thread_id,
                        "status": status,
                        "duration_ms": round((time.perf_counter() - started) * 1000),
                    }
                elif ev["type"] == "RUN_ERROR":
                    status, error = "error", ev.get("message")
                    ev = {**raw, "runId": h.run_id, "threadId": h.thread_id}
                await self._emit(h, ev)
        except asyncio.CancelledError:
            status, error = "cancelled", "cancelled by user"
            await self._emit(
                h,
                {
                    "type": "RUN_ERROR",
                    "code": "cancelled",
                    "message": error,
                    "runId": h.run_id,
                    "threadId": h.thread_id,
                },
            )
        except Exception as exc:
            status, error = "error", f"{type(exc).__name__}: {exc}"
            log.exception("run.crashed", run_id=h.run_id)
            await self._emit(
                h,
                {"type": "RUN_ERROR", "code": "internal", "message": error, "runId": h.run_id, "threadId": h.thread_id},
            )
        finally:
            h.status = status
            await self._finish(h, status, error, interrupts)
            h.finished_at = time.monotonic()
            self.active_by_thread.pop(h.thread_id, None)
            h.done.set()
            async with h.changed:
                h.changed.notify_all()

    async def _finish(self, h: RunHandle, status: str, error: str | None, interrupts: list[dict[str, Any]]) -> None:
        thread_status = {"success": "idle", "interrupted": "interrupted", "cancelled": "idle"}.get(status, "error")
        try:
            async with self.db.sessionmaker() as s:
                await s.execute(
                    update(Run).where(Run.id == h.run_id).values(status=status, error=error, ended_at=datetime.now(UTC))
                )
                await s.execute(update(Thread).where(Thread.id == h.thread_id).values(status=thread_status))
                for intr in interrupts:
                    if await s.get(InterruptTask, intr["id"]) is None:
                        s.add(
                            InterruptTask(
                                id=intr["id"],
                                thread_id=h.thread_id,
                                run_id=h.run_id,
                                canvas_node_id=intr.get("canvas_node_id") or "",
                                kind=intr["kind"],
                                payload=_as_object(intr["payload"]),
                            )
                        )
                await s.commit()
        except Exception:
            log.exception("run.finish_failed", run_id=h.run_id)

    def _evict(self) -> None:
        finished = [rid for rid, h in self.handles.items() if h.done.is_set()]
        for rid in finished[: max(0, len(self.handles) - self._keep)]:
            self.handles.pop(rid, None)

    # ------------------------------------------------------------ consumers ----
    async def subscribe(self, run_id: str, after: int = 0) -> AsyncIterator[dict[str, Any]]:
        h = self.handles.get(run_id)
        if h is None:
            raise NotFound(f"run '{run_id}' is not active on this server (finished runs are kept briefly)")
        i = after
        while True:
            while i < len(h.events):
                yield h.events[i]
                i += 1
            if h.done.is_set():
                return
            async with h.changed:
                if i >= len(h.events) and not h.done.is_set():
                    await h.changed.wait()

    async def wait(self, run_id: str) -> RunHandle:
        h = self.handles[run_id]
        await h.done.wait()
        return h

    async def cancel(self, run_id: str) -> bool:
        h = self.handles.get(run_id)
        if h is None or h.task is None or h.done.is_set():
            return False
        h.task.cancel()
        return True

    async def shutdown(self) -> None:
        for h in list(self.handles.values()):
            if h.task and not h.done.is_set():
                h.task.cancel()
        await asyncio.gather(*(h.task for h in self.handles.values() if h.task), return_exceptions=True)

    # -------------------------------------------------------------- queries ----
    async def pending_interrupts(self, thread_id: str) -> list[InterruptTask]:
        async with self.db.sessionmaker() as s:
            q = select(InterruptTask).where(InterruptTask.thread_id == thread_id, InterruptTask.status == "pending")
            return list((await s.scalars(q)).all())


def _title(input: dict[str, Any] | None) -> str | None:
    if not input:
        return None
    first = next((v for v in input.values() if isinstance(v, str) and v), None)
    return first[:80] if first else None


def _as_object(payload: Any) -> dict[str, Any]:
    return payload if isinstance(payload, dict) else {"value": payload}
