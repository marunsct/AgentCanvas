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

from sqlalchemy import func, select, update

from agentcanvas.adapters.spi import FrameworkAdapter
from agentcanvas.db.models import InterruptTask, Run, Thread
from agentcanvas.db.repositories import ScopeRepo
from agentcanvas.db.session import Database
from agentcanvas.errors import Conflict, NotFound, Unavailable
from agentcanvas.ids import new_id
from agentcanvas.ir.canonical import ir_hash
from agentcanvas.ir.models import Budget, Workflow
from agentcanvas.log import get_logger
from agentcanvas.registry import default_registry
from agentcanvas.runtime.tracing import trace_ref
from agentcanvas.runtime.types import ResumeSpec, RunMode, RunSpec

log = get_logger(__name__)
ResourcesFactory = Callable[[str, Workflow | None], Any]


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
    usage: dict[str, Any] = field(
        default_factory=lambda: {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "priced": True}
    )
    workflow_name: str = ""
    # "event": started by an event handler (its lifecycle events are not re-delivered); "test": a saved test case
    origin: str = "user"
    emitted: list[dict[str, Any]] = field(default_factory=list)  # sdk.emit() events, delivered after the run
    traced: bool = False  # sent to LangSmith ("Open trace", M6-T6)
    input: dict[str, Any] | None = None  # a fresh run's input: its model replies are recorded (FR-X-13)
    drained: bool = False  # stopped by a server shutdown; another instance continues it (FR-X-10)


def _recording(h: RunHandle, status: str) -> dict[str, Any] | None:
    """The run as a cassette (FR-X-13): replaying its model replies should reproduce status, route and output."""
    if h.input is None or status not in ("success", "interrupted"):
        return None
    mocks: dict[str, list[Any]] = {}
    route: list[str] = []
    output: dict[str, Any] = {}
    for ev in h.events:
        if ev["type"] == "CUSTOM" and ev.get("name") == "ac.model_reply":
            mocks.setdefault(ev["value"]["resource"], []).append(ev["value"]["reply"])
        elif ev["type"] == "STEP_STARTED" and ev.get("canvas_node_id") not in route:
            route.append(ev["canvas_node_id"])
        elif ev["type"] == "RUN_FINISHED":
            output = ev.get("output") or {}
    return {"input": h.input, "mocks": mocks, "status": status, "route": route, "output": output}


class BudgetExceeded(Exception):
    """A run went over its workflow budget (FR-X-15)."""


def price(model_id: str | None, tokens_in: int, tokens_out: int) -> float | None:
    """USD for a model call from catalog prices; None when the model or its prices are unknown."""
    info = default_registry().model(model_id or "")
    if info is None or info.price_in_per_mtok is None or info.price_out_per_mtok is None:
        return None
    return (tokens_in * info.price_in_per_mtok + tokens_out * info.price_out_per_mtok) / 1_000_000


def check_budget(budget: Budget | None, usage: dict[str, Any], before: tuple[int, float]) -> None:
    """Raise BudgetExceeded when this run (or the thread including earlier runs) is over a limit."""
    if budget is None:
        return
    tokens, cost = usage["input_tokens"] + usage["output_tokens"], usage["cost_usd"]
    limits = [
        (budget.max_tokens, tokens, "tokens for this run"),
        (budget.max_cost_usd, cost, "USD for this run"),
        (budget.thread_max_tokens, before[0] + tokens, "tokens for this conversation"),
        (budget.thread_max_cost_usd, before[1] + cost, "USD for this conversation"),
    ]
    for limit, used, what in limits:
        if limit is not None and used > limit:
            raise BudgetExceeded(f"budget exceeded: {used:g} of {limit:g} {what}")


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
        # called after every run (event delivery: run.completed / run.failed / interrupt.raised, sdk.emit events)
        self.finish_hooks: list[Callable[[RunHandle, str, str | None, list[dict[str, Any]]], Any]] = []
        # called when a run starts (metrics)
        self.start_hooks: list[Callable[[RunHandle], None]] = []
        self.draining = False  # shutting down: no new runs (FR-X-10)

    def resources(self, thread_id: str, wf: Workflow | None) -> Any:
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
        pins: dict[str, dict[str, Any]] | None = None,
        mocks: dict[str, list[Any]] | None = None,
        dry_run: bool = False,
        origin: str = "user",
        deployment_id: str | None = None,
        version_id: str | None = None,
    ) -> RunHandle:
        """Start a run. `context=None` reuses the context of the last run on `context_from` (default: this thread)."""
        if self.draining:
            raise Unavailable("this server is restarting; retry the request", retry_after=5)
        thread_id = thread_id or new_id("th")
        if thread_id in self.active_by_thread:  # double-texting policy "reject" (FR-RUN-11 later adds others)
            raise Conflict(f"thread '{thread_id}' already has an active run", run_id=self.active_by_thread[thread_id])
        run_id = new_id("run")
        async with self.db.sessionmaker() as s:
            thread = await s.get(Thread, thread_id)
            if thread is None:
                s.add(
                    Thread(
                        id=thread_id,
                        workflow_id=wf.id,
                        deployment_id=deployment_id,
                        status="running",
                        created_by=user_id,
                        title=_title(input),
                    )
                )
            elif thread.workflow_id != wf.id:
                raise Conflict(f"thread '{thread_id}' belongs to workflow '{thread.workflow_id}'")
            elif thread.deployment_id != deployment_id:  # published and draft conversations never mix
                raise Conflict(
                    f"thread '{thread_id}' belongs to {'a deployment' if thread.deployment_id else 'the draft'}"
                )
            else:
                thread.status = "running"
            if context is None:
                context = await self._last_context(s, context_from or thread_id)
            org_id = await ScopeRepo(s).org_of("workflow", wf.id)  # None for workflows outside the database
            await s.flush()
            s.add(
                Run(
                    id=run_id,
                    thread_id=thread_id,
                    workflow_version_id=version_id,
                    ir_hash=ir_hash(wf),
                    mode=mode,
                    status="running",
                    context=context,
                    trace_url=trace_ref(run_id),
                )
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
            pins=pins or {},
            dry_run=dry_run,
            mocks=mocks or {},
            org_id=org_id,
            labels={
                k: v
                for k, v in {"origin": origin, "deployment_id": deployment_id, "version_id": version_id}.items()
                if v
            },
        )
        handle = RunHandle(run_id=run_id, thread_id=thread_id, workflow_id=wf.id, workflow_name=wf.name, origin=origin)
        handle.traced = trace_ref(run_id) is not None
        if input is not None and resume is None and checkpoint_id is None and origin != "test":
            handle.input = input
        self.handles[run_id] = handle
        self.active_by_thread[thread_id] = run_id
        for hook in self.start_hooks:
            try:
                hook(handle)
            except Exception:
                log.exception("run.start_hook_failed", run_id=run_id)
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
        budget = spec.workflow.settings.budget
        events: Any = None  # the adapter's async generator (closed in `finally`)
        try:
            before = await self._thread_spend(spec.thread_id, h.run_id) if budget else (0, 0.0)
            check_budget(budget, h.usage, before)  # a conversation that is already over budget does not start
            resources = self.resources(spec.thread_id, spec.workflow)
            events = self.adapter.execute(spec, resources)
            async with asyncio.timeout(budget.max_seconds if budget and budget.max_seconds else None):
                async for raw in events:
                    if raw["type"] == "CUSTOM" and raw.get("name") == "ac.usage":
                        await self._emit(h, self._priced(h, raw))
                        check_budget(budget, h.usage, before)
                        continue
                    ev = raw
                    if ev["type"] == "CUSTOM" and isinstance(ev.get("value"), dict) and "ac_event" in ev["value"]:
                        h.emitted.append(ev["value"])
                    if ev["type"] == "RUN_FINISHED":
                        interrupts = ev.get("interrupts", [])
                        status = "interrupted" if ev["outcome"] == "interrupt" else "success"
                        ev = {
                            **raw,
                            "runId": h.run_id,
                            "threadId": h.thread_id,
                            "status": status,
                            "duration_ms": round((time.perf_counter() - started) * 1000),
                            "usage": dict(h.usage),
                            "traced": h.traced,
                        }
                    elif ev["type"] == "RUN_ERROR":
                        status, error = "error", ev.get("message")
                        ev = {**raw, "runId": h.run_id, "threadId": h.thread_id}
                    await self._emit(h, ev)
        except (BudgetExceeded, TimeoutError) as exc:
            status = "error"
            seconds = budget.max_seconds if budget else None
            error = str(exc) if isinstance(exc, BudgetExceeded) else f"budget exceeded: ran longer than {seconds} s"
            await self._emit(
                h,
                {
                    "type": "RUN_ERROR",
                    "code": "budget_exceeded",
                    "message": error,
                    "usage": dict(h.usage),
                    "runId": h.run_id,
                    "threadId": h.thread_id,
                },
            )
        except asyncio.CancelledError:
            if h.drained:  # paused at the last checkpoint; another instance continues it (FR-X-10)
                status, error = "interrupted", "paused for a server restart; it continues automatically"
                code = "redeploy"
            else:
                status, error, code = "cancelled", "cancelled by user", "cancelled"
            await self._emit(
                h,
                {
                    "type": "RUN_ERROR",
                    "code": code,
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
            if events is not None:
                await events.aclose()  # stops the graph if we left early (budget, cancel)
            h.status = status
            await self._finish(h, status, error, interrupts)
            for hook in self.finish_hooks:
                try:
                    await hook(h, status, error, interrupts)
                except Exception:
                    log.exception("run.finish_hook_failed", run_id=h.run_id)
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
                    update(Run)
                    .where(Run.id == h.run_id)
                    .values(
                        status=status,
                        error=error,
                        ended_at=datetime.now(UTC),
                        tokens_in=h.usage["input_tokens"],
                        tokens_out=h.usage["output_tokens"],
                        cost_usd=h.usage["cost_usd"],
                        recording=_recording(h, status),
                        handover=h.drained,
                    )
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

    def _priced(self, h: RunHandle, raw: dict[str, Any]) -> dict[str, Any]:
        """Add the cost to a usage event and to the run's totals."""
        v = dict(raw["value"])
        v["cost_usd"] = price(v.get("model"), v["input_tokens"], v["output_tokens"])
        h.usage["input_tokens"] += v["input_tokens"]
        h.usage["output_tokens"] += v["output_tokens"]
        if v["cost_usd"] is None:
            h.usage["priced"] = False  # at least one model has no catalog price: the cost is a lower bound
        else:
            h.usage["cost_usd"] += v["cost_usd"]
        return {**raw, "value": v}

    async def _thread_spend(self, thread_id: str, run_id: str) -> tuple[int, float]:
        """Tokens and USD spent by the thread's earlier runs."""
        async with self.db.sessionmaker() as s:
            q = select(
                func.coalesce(func.sum(Run.tokens_in + Run.tokens_out), 0), func.coalesce(func.sum(Run.cost_usd), 0)
            ).where(Run.thread_id == thread_id, Run.id != run_id)
            tokens, cost = (await s.execute(q)).one()
        return int(tokens), float(cost)

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

    async def drain(self, grace_s: float) -> list[str]:
        """Stop taking runs, give running ones `grace_s` to finish, then stop the rest at their last checkpoint
        and mark them for hand-over (`Run.handover`); returns the ids of the runs handed over (FR-X-10)."""
        self.draining = True
        running = [h for h in self.handles.values() if h.task and not h.done.is_set()]
        if running and grace_s > 0:
            await asyncio.wait([asyncio.ensure_future(h.done.wait()) for h in running], timeout=grace_s)
        left = [h for h in running if not h.done.is_set()]
        for h in left:
            h.drained = True
            h.task.cancel()  # type: ignore[union-attr]
        await asyncio.gather(*(h.task for h in left if h.task), return_exceptions=True)
        if left:
            log.info("runner.drained", runs=[h.run_id for h in left])
        return [h.run_id for h in left]

    def active(self) -> list[str]:
        return [h.run_id for h in self.handles.values() if h.task and not h.done.is_set()]

    async def heartbeat(self) -> None:
        """Mark this instance's running runs as alive (the orphan sweep skips them)."""
        ids = self.active()
        if not ids:
            return
        async with self.db.sessionmaker() as s:
            await s.execute(
                update(Run).where(Run.id.in_(ids), Run.status == "running").values(heartbeat_at=datetime.now(UTC))
            )
            await s.commit()

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
