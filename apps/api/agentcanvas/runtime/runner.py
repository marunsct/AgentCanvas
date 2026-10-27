"""The run orchestrator (build-guide/05 §5, review v0.6 V-10). Framework-neutral: it talks to adapters.

Each run executes in a background task that writes canonical events into a per-run buffer. HTTP
clients *subscribe* to that buffer, so a run survives a browser disconnect, can be re-attached
(`after=seq`) and cancelled. The runner also keeps the control DB in sync: thread/run status and
the Inbox (`interrupt_task` rows).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Literal, cast

from sqlalchemy import case, func, select, update
from sqlalchemy.exc import IntegrityError

from agentcanvas.adapters.spi import FrameworkAdapter, ModelService, ThreadStateAbility, supports
from agentcanvas.db.models import CostAlert, InterruptTask, Run, RunScore, Thread, WorkflowRow
from agentcanvas.db.repositories import ScopeRepo
from agentcanvas.db.session import Database
from agentcanvas.errors import Conflict, NotFound, Unavailable
from agentcanvas.ids import new_id
from agentcanvas.ir.canonical import ir_hash
from agentcanvas.ir.models import Budget, Workflow
from agentcanvas.log import get_logger
from agentcanvas.registry import default_registry
from agentcanvas.runtime.tracing import trace_ref
from agentcanvas.runtime.types import REPLAYABLE, ResumeSpec, RunMode, RunSpec

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
        default_factory=lambda: {
            "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "priced": True, "cache_hits": 0, "fallbacks": 0,
        }
    )  # fmt: skip
    workflow_name: str = ""
    # "event": started by an event handler (its lifecycle events are not re-delivered); "test": a saved test case
    origin: str = "user"
    emitted: list[dict[str, Any]] = field(default_factory=list)  # sdk.emit() events, delivered after the run
    traced: bool = False  # sent to LangSmith ("Open trace", M6-T6)
    input: dict[str, Any] | None = None  # a fresh run's input: its model replies are recorded (FR-X-13)
    drained: bool = False  # stopped by a server shutdown; another instance continues it (FR-X-10)
    superseded: bool = False  # swept while this instance stalled: another instance continues it (S13)
    deployment_id: str | None = None  # a run of a published deployment (online evaluators score these, S13)
    version_id: str | None = None
    labels: dict[str, str] = field(default_factory=dict)  # what the deployment API caller tagged it with
    trace_redact: list[str] = field(default_factory=list)  # the workflow's fields that never reach a trace
    held: list[dict[str, Any]] = field(default_factory=list)  # last events, published once the run is saved (see _pump)
    size: int = 0  # about how many bytes its events take in memory (finished runs are evicted by this too)
    event_times: list[int] = field(default_factory=list)  # wall clock (ns) of each event, for trace export (S15)
    alerted: set[tuple[str, float]] = field(default_factory=set)  # (period, share) cost alerts this run checked


def _plain(value: Any) -> Any:
    """`value` as plain JSON data, a copy nobody else holds: what a database column can store."""
    return json.loads(json.dumps(value, default=str))


def _recording(h: RunHandle, status: str) -> dict[str, Any] | None:
    """The run as a cassette (FR-X-13): replaying its model replies and tool outputs should reproduce status, route
    and output. A fresh run that failed keeps only its input, so a reviewer can see it and correct it (S13)."""
    if h.input is None:
        return None
    if status not in REPLAYABLE:
        return {"input": h.input, "status": status} if status == "error" else None
    mocks: dict[str, list[Any]] = {}
    route: list[str] = []
    output: dict[str, Any] = {}
    for ev in [*h.events, *h.held]:  # the last events may still be held back until the run is saved
        if ev["type"] == "CUSTOM" and ev.get("name") == "ac.model_reply":
            mocks.setdefault(ev["value"]["resource"], []).append(ev["value"]["reply"])
        elif ev["type"] == "CUSTOM" and ev.get("name") == "ac.tool_io":  # tool resources replay these (S13)
            io = ev["value"]
            mocks.setdefault(io["resource"], []).append(
                {k: io[k] for k in ("tool", "args", "output", "error") if k in io}
            )
        elif ev["type"] == "STEP_STARTED" and ev.get("canvas_node_id") not in route:
            route.append(ev["canvas_node_id"])
        elif ev["type"] == "RUN_FINISHED":
            output = ev.get("output") or {}
    return {"input": h.input, "mocks": mocks, "status": status, "route": route, "output": output}


class BudgetExceeded(Exception):
    """A run went over its workflow budget (FR-X-15)."""


def rubric_scores(h: Any) -> list[RunScore]:
    """The run's rubric grading rounds (FR-EVAL-08 `ac.rubric` end events) as run scores, so the thread's timeline
    keeps them after the live events are gone: 1 when the grader was satisfied, 0 when it asked for a revision or
    gave up, none when grading itself failed."""
    out = []
    for ev in [*h.events, *getattr(h, "held", [])]:  # the last events may still be held back until the run is saved
        if ev.get("type") != "CUSTOM" or ev.get("name") != "ac.rubric" or ev["value"].get("phase") != "end":
            continue
        v = ev["value"]
        result = str(v.get("result") or "")
        value = None if result in ("grader_error", "failed") else float(result == "satisfied")
        gaps = "; ".join(f"{c.get('name')}: {c.get('gap')}" for c in v.get("criteria") or [] if not c.get("passed"))
        out.append(
            RunScore(
                id=new_id("rsc"),
                run_id=h.run_id,
                thread_id=h.thread_id,
                workflow_id=h.workflow_id,
                deployment_id=h.deployment_id,
                evaluator="rubric",
                source="rubric",
                score=value,
                passed=value == 1.0,
                comment=f"{ev.get('canvas_node_id') or 'agent'}, round {int(v.get('iteration') or 0) + 1}: {result}. "
                f"{v.get('explanation') or ''} {gaps}".strip()[:2000],
                created_at=datetime.now(UTC),
            )
        )
    return out


def price(model_id: str | None, tokens_in: int, tokens_out: int) -> float | None:
    """USD for a model call from catalog prices; None when the model or its prices are unknown."""
    info = default_registry().model(model_id or "")
    if info is None or info.price_in_per_mtok is None or info.price_out_per_mtok is None:
        return None
    return (tokens_in * info.price_in_per_mtok + tokens_out * info.price_out_per_mtok) / 1_000_000


@dataclass
class Spent:
    """What was spent before a run: by its thread's earlier runs, and by all the workflow's runs today and this
    month (UTC; runs still going count once they finish)."""

    thread_tokens: int = 0
    thread_cost: float = 0.0
    day_cost: float = 0.0
    month_cost: float = 0.0


def periods(now: datetime | None = None) -> dict[str, tuple[str, datetime]]:
    """The current budget periods (UTC): name -> (label, start)."""
    day = (now or datetime.now(UTC)).replace(hour=0, minute=0, second=0, microsecond=0)
    month = day.replace(day=1)
    return {"day": (day.strftime("%Y-%m-%d"), day), "month": (month.strftime("%Y-%m"), month)}


def period_limits(budget: Budget | None, before: Spent, cost: float) -> list[tuple[str, float, float]]:
    """(period, limit, spent including this run) of each day or month limit the budget sets."""
    if budget is None:
        return []
    out = [("day", budget.daily_max_cost_usd, before.day_cost + cost),
           ("month", budget.monthly_max_cost_usd, before.month_cost + cost)]  # fmt: skip
    return [(p, limit, spent) for p, limit, spent in out if limit is not None]


def check_budget(budget: Budget | None, usage: dict[str, Any], before: Spent) -> None:
    """Raise BudgetExceeded when this run (or its thread, or the workflow today or this month) is over a limit."""
    if budget is None:
        return
    tokens, cost = usage["input_tokens"] + usage["output_tokens"], usage["cost_usd"]
    limits = [
        (budget.max_tokens, tokens, "tokens for this run"),
        (budget.max_cost_usd, cost, "USD for this run"),
        (budget.thread_max_tokens, before.thread_tokens + tokens, "tokens for this conversation"),
        (budget.thread_max_cost_usd, before.thread_cost + cost, "USD for this conversation"),
    ]
    for period, limit, spent in period_limits(budget, before, cost):
        if spent >= limit:  # an exhausted day or month does not start new runs
            raise BudgetExceeded(f"budget exceeded: {spent:g} of {limit:g} USD for this workflow this {period}")
    for limit, used, what in limits:
        if limit is not None and used > limit:
            raise BudgetExceeded(f"budget exceeded: {used:g} of {limit:g} {what}")


class SpendTally:
    """What a workflow's runs spend while they are going, per budget period, in this process. A run's cost reaches
    the database only when it finishes, so runs going at the same time would each see the same spending before
    them and together pass a daily or monthly limit. Each run's check adds what the workflow's runs already going
    had spent when it started, and what the workflow's other runs spent since.

    Limitation: the tally is per process. Runs on other API instances count only once they finish (the database),
    so concurrent runs spread over several instances can still overshoot a period limit by what they spend
    together while going."""

    def __init__(self) -> None:
        self.total: dict[tuple[str, str], float] = {}  # (workflow, period label) -> USD reported in this process
        self.running: dict[str, dict[str, float]] = {}  # workflow -> run -> USD so far
        self.marks: dict[str, tuple[dict[str, str], dict[str, float]]] = {}  # run -> (labels, totals) at its start

    def start(self, workflow_id: str, run_id: str) -> float:
        """Note where a run starts (before reading the database's spending); returns what the workflow's other
        runs going here had spent by then, which the database does not have yet."""
        labels = {p: label for p, (label, _) in periods().items()}
        current = set(labels.values())
        for key in [k for k in self.total if k[0] == workflow_id and k[1] not in current]:
            del self.total[key]  # a past day or month
        self.marks[run_id] = (labels, {p: self.total.get((workflow_id, lb), 0.0) for p, lb in labels.items()})
        going = self.running.setdefault(workflow_id, {})
        carry = sum(cost for run, cost in going.items() if run != run_id)
        going[run_id] = 0.0
        return carry

    def add(self, workflow_id: str, run_id: str, cost: float) -> None:
        if not cost:
            return
        going = self.running.get(workflow_id, {})
        if run_id in going:
            going[run_id] += cost
        for label, _ in periods().values():
            self.total[(workflow_id, label)] = self.total.get((workflow_id, label), 0.0) + cost

    def others(self, workflow_id: str, run_id: str, own: float) -> dict[str, float]:
        """USD the workflow's other runs in this process spent since this run started, per period."""
        labels, marks = self.marks.get(run_id, ({}, {}))
        return {p: max(0.0, self.total.get((workflow_id, lb), 0.0) - marks[p] - own) for p, lb in labels.items()}

    def finish(self, workflow_id: str, run_id: str) -> None:
        """The run's cost is in the database now."""
        self.marks.pop(run_id, None)
        going = self.running.get(workflow_id)
        if going is not None:
            going.pop(run_id, None)
            if not going:
                del self.running[workflow_id]


async def workflow_spend(s: Any, workflow_id: str, *, exclude_run: str | None = None) -> tuple[float, float]:
    """USD the workflow's runs spent today and this month (UTC)."""
    p = periods()
    day, month = p["day"][1], p["month"][1]
    q = (
        select(
            func.coalesce(func.sum(case((Run.started_at >= day, Run.cost_usd), else_=0)), 0),
            func.coalesce(func.sum(Run.cost_usd), 0),
        )
        .join(Thread, Thread.id == Run.thread_id)
        .where(Thread.workflow_id == workflow_id, Run.started_at >= month)
    )
    if exclude_run is not None:
        q = q.where(Run.id != exclude_run)
    today, this_month = (await s.execute(q)).one()
    return float(today), float(this_month)


class Runner:
    def __init__(
        self,
        db: Database,
        adapter: FrameworkAdapter,
        resources_factory: ResourcesFactory | None = None,
        keep_finished: int = 500,
        keep_bytes: int = 256 * 1024 * 1024,
        adapters: dict[str, FrameworkAdapter] | None = None,
        models: ModelService | None = None,
    ):
        self.db = db
        self.adapter = adapter  # the host's default framework: long-term memory, MCP browsing, retention
        self.adapters: dict[str, FrameworkAdapter] = {adapter.id: adapter, **(adapters or {})}  # S20: by framework
        self._models = models
        self.resources_factory: ResourcesFactory = resources_factory or (lambda _t, _w: adapter.default_resources())
        self.handles: OrderedDict[str, RunHandle] = OrderedDict()
        self.active_by_thread: dict[str, str] = {}
        self._keep = keep_finished
        self._keep_bytes = keep_bytes  # finished runs' events kept in memory: 500 runs of big outputs must not fill it
        # called after every run, before it counts as done (metrics, online evaluators, trace export)
        self.finish_hooks: list[Callable[[RunHandle, str, str | None, list[dict[str, Any]]], Any]] = []
        # called once it is done and its thread free (event delivery: run.completed / run.failed / interrupt.raised,
        # sdk.emit events), so a handler can start the thread's next run and no caller waits for handlers
        self.after_hooks: list[Callable[[RunHandle, str, str | None, list[dict[str, Any]]], Any]] = []
        self.alert_hooks: list[Callable[[CostAlert], Any]] = []  # S26: called on each new cost alert (metrics)
        # called when a run starts (metrics)
        self.start_hooks: list[Callable[[RunHandle], None]] = []
        # awaited before a run starts (the project's secrets, reloaded when another instance changed them)
        self.prepare_hooks: list[Callable[[Workflow], Awaitable[None]]] = []
        self.draining = False  # shutting down: no new runs (FR-X-10)
        self.spend = SpendTally()  # runs going in this process, for daily and monthly budgets

    @property
    def models(self) -> ModelService:
        """Model calls outside workflows (LLM judges), framework-neutral (S20)."""
        if self._models is None:
            from agentcanvas.adapters import model_service

            self._models = model_service()
        return self._models

    def adapter_for(self, wf: Workflow) -> FrameworkAdapter:
        """The adapter of the workflow's framework (S20); a plain 409 when this server does not have it."""
        found = self.adapters.get(wf.framework)
        if found is None:
            raise Conflict(
                f"this workflow is designed for '{wf.framework}', which this server does not have; "
                f"installed: {', '.join(self.adapters)}"
            )
        return found

    async def forget_thread(self, thread_id: str) -> None:
        """Delete a conversation's saved state in every framework that keeps one."""
        for a in self.adapters.values():
            if supports(a, "thread_state"):
                await cast(ThreadStateAbility, a).delete_thread(thread_id)

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
        labels: dict[str, str] | None = None,
    ) -> RunHandle:
        """Start a run. `context=None` reuses the context of the last run on `context_from` (default: this thread)."""
        if self.draining:
            raise Unavailable("this server is restarting; retry the request", retry_after=5)
        adapter = self.adapter_for(wf)
        if mode == "debug" and (breakpoints_before or breakpoints_after) and not supports(adapter, "breakpoints"):
            raise Conflict(f"{adapter.manifest.label} does not support breakpoints; run without them")
        thread_id = thread_id or new_id("th")
        if thread_id in self.active_by_thread:  # double-texting policy "reject" (FR-RUN-11 later adds others)
            raise Conflict(f"thread '{thread_id}' already has an active run", run_id=self.active_by_thread[thread_id])
        run_id = new_id("run")
        self.active_by_thread[thread_id] = run_id  # reserved before any await: a concurrent start is refused
        try:
            for prepare in self.prepare_hooks:
                await prepare(wf)
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
                    # claimed in the database, not only in this process: two replicas cannot run one conversation at
                    # once (an orphaned run's thread is released by the hand-over sweep)
                    free = (Thread.id == thread_id, Thread.status != "running")
                    claimed = await s.execute(update(Thread).where(*free).values(status="running"))
                    if claimed.rowcount == 0:  # type: ignore[attr-defined]
                        raise Conflict(f"thread '{thread_id}' already has an active run")
                if context is None:
                    context = await self._last_context(s, context_from or thread_id)
                org_id = await ScopeRepo(s).org_of("workflow", wf.id)  # None for workflows outside the database
                # the run resolves secrets and workspace code from the workflow's own project: read it here, at every
                # start, instead of trusting a map filled only when some service happened to load the workflow (after
                # a restart, a hand-over or on another replica it was missing and the default project was used)
                project_id = await s.scalar(select(WorkflowRow.project_id).where(WorkflowRow.id == wf.id))
                if project_id is not None:
                    from agentcanvas.services.projects import PROJECT_OF

                    PROJECT_OF[wf.id] = project_id
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
                        labels=labels or None,
                    )
                )
                if resume is not None:
                    await self._decide_interrupts(s, thread_id, resume, decided_by or user_id)
                await s.commit()
        except BaseException:
            self.active_by_thread.pop(thread_id, None)
            raise

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
        handle = RunHandle(
            run_id=run_id, thread_id=thread_id, workflow_id=wf.id, workflow_name=wf.name, origin=origin,
            deployment_id=deployment_id, version_id=version_id, labels=dict(labels or {}),
            trace_redact=list(wf.settings.trace_redact),
        )  # fmt: skip
        handle.traced = trace_ref(run_id) is not None
        if input is not None and resume is None and checkpoint_id is None and origin != "test":
            handle.input = _plain(input)  # a copy: the engine may turn the caller's message dicts into objects in place
        self.handles[run_id] = handle
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
        res = await s.execute(
            q.values(status="decided", decision=decision, decided_by=decided_by, decided_at=datetime.now(UTC))
        )
        if resume.interrupt_id and res.rowcount == 0 and await s.get(InterruptTask, resume.interrupt_id):
            raise Conflict(f"inbox item '{resume.interrupt_id}' was already decided")  # a concurrent decision won

    # ---------------------------------------------------------------- pump ----
    async def _emit(self, h: RunHandle, event: dict[str, Any]) -> None:
        event = {**event, "seq": len(h.events) + 1}
        h.events.append(event)
        h.size += len(json.dumps(event, default=str))
        h.event_times.append(time.time_ns())
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
                "adapter": spec.workflow.framework,
            },
        )
        status, error, interrupts = "error", None, []
        # The run's last events are published only after the run is finalised (saved, thread released): a client
        # that sends its next message the moment it reads RUN_FINISHED must find the thread free, not 'already has
        # an active run'. Everything from the first terminal event on waits here, in order.
        held = h.held

        async def emit(event: dict[str, Any]) -> None:
            if held or event["type"] in ("RUN_FINISHED", "RUN_ERROR"):
                held.append(event)
            else:
                await self._emit(h, event)

        budget = spec.workflow.settings.budget
        events: Any = None  # the adapter's async generator (closed in `finally`)
        carry = self.spend.start(h.workflow_id, h.run_id)
        try:
            before = await self._spent(h, budget) if budget else Spent()
            before = replace(before, day_cost=before.day_cost + carry, month_cost=before.month_cost + carry)
            check_budget(budget, h.usage, before)  # a conversation that is already over budget does not start
            resources = self.resources(spec.thread_id, spec.workflow)
            events = self.adapter_for(spec.workflow).execute(spec, resources)
            async with asyncio.timeout(budget.max_seconds if budget and budget.max_seconds else None):
                async for raw in events:
                    if raw["type"] == "CUSTOM" and raw.get("name") == "ac.usage":
                        priced = self._priced(h, raw)
                        await emit(priced)
                        self.spend.add(h.workflow_id, h.run_id, priced["value"]["cost_usd"] or 0.0)
                        others = self.spend.others(h.workflow_id, h.run_id, h.usage["cost_usd"])
                        now = replace(
                            before,
                            day_cost=before.day_cost + others.get("day", 0.0),
                            month_cost=before.month_cost + others.get("month", 0.0),
                        )  # with what the workflow's concurrent runs here spent meanwhile
                        await self._cost_alerts(h, budget, now)
                        check_budget(budget, h.usage, now)
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
                    await emit(ev)
        except (BudgetExceeded, TimeoutError) as exc:
            status = "error"
            seconds = budget.max_seconds if budget else None
            error = str(exc) if isinstance(exc, BudgetExceeded) else f"budget exceeded: ran longer than {seconds} s"
            await emit(
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
            if h.superseded:
                status, error, code = "interrupted", "another server took this run over", "superseded"
            elif h.drained:  # paused at the last checkpoint; another instance continues it (FR-X-10)
                status, error = "interrupted", "paused for a server restart; it continues automatically"
                code = "redeploy"
            else:
                status, error, code = "cancelled", "cancelled by user", "cancelled"
            await emit(
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
            await emit(
                {"type": "RUN_ERROR", "code": "internal", "message": error, "runId": h.run_id, "threadId": h.thread_id},
            )
        finally:
            try:
                if events is not None:
                    await events.aclose()  # stops the graph if we left early (budget, cancel)
                h.status = status
                await self._finish(h, status, error, interrupts)
                self.spend.finish(h.workflow_id, h.run_id)  # after its cost is saved: later runs read it there
            finally:  # even if finalising was cut short: the thread is released and the last events are published
                if self.active_by_thread.get(h.thread_id) == h.run_id:  # the thread is free for its next run
                    del self.active_by_thread[h.thread_id]
                for event in held:
                    await self._emit(h, event)
            await self._hooks(self.finish_hooks, h, status, error, interrupts)
            h.finished_at = time.monotonic()
            h.done.set()
            self._evict()  # a quiet server must not hold finished runs' events beyond its limits
            async with h.changed:
                h.changed.notify_all()
            # event handlers may take long and start or resume runs (on this thread too): nobody waiting for the
            # run waits for them, but they still run in its task, so a shutdown lets them finish
            await self._hooks(self.after_hooks, h, status, error, interrupts)

    @staticmethod
    async def _hooks(hooks: list[Callable[..., Any]], h: RunHandle, *args: Any) -> None:
        for hook in hooks:
            try:
                await hook(h, *args)
            except Exception:
                log.exception("run.finish_hook_failed", run_id=h.run_id)

    async def _finish(self, h: RunHandle, status: str, error: str | None, interrupts: list[dict[str, Any]]) -> None:
        thread_status = {"success": "idle", "interrupted": "interrupted", "cancelled": "idle"}.get(status, "error")
        try:
            async with self.db.sessionmaker() as s:
                res = await s.execute(
                    update(Run)
                    .where(Run.id == h.run_id, Run.status == "running")  # a swept run belongs to its new instance
                    .values(
                        status=status,
                        error=error,
                        ended_at=datetime.now(UTC),
                        tokens_in=h.usage["input_tokens"],
                        tokens_out=h.usage["output_tokens"],
                        cost_usd=h.usage["cost_usd"],
                        cache_hits=h.usage["cache_hits"],
                        fallbacks=h.usage["fallbacks"],
                        recording=_recording(h, status),
                        handover=h.drained,
                    )
                )
                if res.rowcount == 0:  # type: ignore[attr-defined]
                    await s.commit()
                    return
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
                s.add_all(rubric_scores(h))
                await s.commit()
        except Exception:
            log.exception("run.finish_failed", run_id=h.run_id)
            await self._finish_bare(h, status, error, thread_status)

    async def _finish_bare(self, h: RunHandle, status: str, error: str | None, thread_status: str) -> None:
        """The least a finished run must leave in the database when saving everything failed (a recording or a usage
        figure the column cannot store): its status and a free thread. Without it the thread would stay 'running' and
        refuse every later message."""
        try:
            async with self.db.sessionmaker() as s:
                await s.execute(
                    update(Run)
                    .where(Run.id == h.run_id, Run.status == "running")
                    .values(status=status, error=error, ended_at=datetime.now(UTC), handover=h.drained)
                )
                await s.execute(update(Thread).where(Thread.id == h.thread_id).values(status=thread_status))
                await s.commit()
        except Exception:
            log.exception("run.finish_bare_failed", run_id=h.run_id)

    def _priced(self, h: RunHandle, raw: dict[str, Any]) -> dict[str, Any]:
        """Add the cost to a usage event and to the run's totals."""
        v = dict(raw["value"])
        if v.get("cached"):  # the model gateway answered from its cache (S26)
            h.usage["cache_hits"] += 1
        if v.get("failed"):  # ... or a backup model answered
            h.usage["fallbacks"] += 1
        has_tokens = v["input_tokens"] or v["output_tokens"]
        v["cost_usd"] = price(v.get("model"), v["input_tokens"], v["output_tokens"]) if has_tokens else 0.0
        h.usage["input_tokens"] += v["input_tokens"]
        h.usage["output_tokens"] += v["output_tokens"]
        if v["cost_usd"] is None:
            h.usage["priced"] = False  # at least one model has no catalog price: the cost is a lower bound
        else:
            h.usage["cost_usd"] += v["cost_usd"]
        return {**raw, "value": v}

    async def _spent(self, h: RunHandle, budget: Budget) -> Spent:
        """What the run's thread, and its workflow today and this month, spent before it."""
        async with self.db.sessionmaker() as s:
            q = select(
                func.coalesce(func.sum(Run.tokens_in + Run.tokens_out), 0), func.coalesce(func.sum(Run.cost_usd), 0)
            ).where(Run.thread_id == h.thread_id, Run.id != h.run_id)
            tokens, cost = (await s.execute(q)).one()
            out = Spent(thread_tokens=int(tokens), thread_cost=float(cost))
            if budget.daily_max_cost_usd or budget.monthly_max_cost_usd:
                out.day_cost, out.month_cost = await workflow_spend(s, h.workflow_id, exclude_run=h.run_id)
        return out

    async def _cost_alerts(self, h: RunHandle, budget: Budget | None, before: Spent) -> None:
        """Raise the cost alerts (S26) this run's spending crosses: once per workflow, period and share, however many
        runs and servers cross it together."""
        if budget is None:
            return
        for period, limit, spent in period_limits(budget, before, h.usage["cost_usd"]):
            for share in budget.alerts():
                if spent < limit * share or (period, share) in h.alerted:
                    continue
                h.alerted.add((period, share))
                label = periods()[period][0]
                what = "reached its" if share >= 1 else f"used {share:.0%} of its"
                message = f"{h.workflow_name or h.workflow_id} {what} {period} budget: {spent:.4g} of {limit:g} USD"
                alert = CostAlert(
                    id=new_id("cal"), workflow_id=h.workflow_id, run_id=h.run_id, period=period, period_start=label,
                    share=share, spent_usd=spent, limit_usd=limit, message=message,
                )  # fmt: skip
                try:
                    async with self.db.sessionmaker() as s:
                        s.add(alert)
                        await s.commit()
                except IntegrityError:
                    continue  # another run raised it first
                except Exception:
                    log.exception("cost_alert.save_failed", run_id=h.run_id)
                    continue
                log.warning("cost_alert", workflow_id=h.workflow_id, period=period, share=share, spent_usd=spent)
                for hook in self.alert_hooks:
                    try:
                        hook(alert)
                    except Exception:
                        log.exception("cost_alert.hook_failed", run_id=h.run_id)
                value = {"period": period, "period_start": label, "share": share, "spent_usd": round(spent, 6),
                         "limit_usd": limit, "message": message}  # fmt: skip
                await self._emit(h, {"type": "CUSTOM", "name": "ac.cost_alert", "value": value})

    def _evict(self) -> None:
        """Forget the oldest finished runs beyond `keep_finished` and `keep_bytes` of events (running ones stay)."""
        finished = [rid for rid, h in self.handles.items() if h.done.is_set()]
        for rid in finished[: max(0, len(self.handles) - self._keep)]:
            self.handles.pop(rid, None)
        total = sum(h.size for h in self.handles.values() if h.done.is_set())
        for rid in [rid for rid, h in self.handles.items() if h.done.is_set()]:
            if total <= self._keep_bytes:
                break
            total -= self.handles.pop(rid).size

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

    async def heartbeat(self) -> list[str]:
        """Mark this instance's running runs as alive (the orphan sweep skips them). A run that is no longer
        `running` was swept while this instance stalled and another instance continues it: it stops here.
        Returns the ids of the runs stopped."""
        ids = self.active()
        if not ids:
            return []
        async with self.db.sessionmaker() as s:
            await s.execute(
                update(Run).where(Run.id.in_(ids), Run.status == "running").values(heartbeat_at=datetime.now(UTC))
            )
            gone = list((await s.scalars(select(Run.id).where(Run.id.in_(ids), Run.status != "running"))).all())
            await s.commit()
        for run_id in gone:
            h = self.handles.get(run_id)
            if h is not None and h.task is not None and not h.done.is_set():
                log.warning("run.superseded", run_id=run_id)
                h.superseded = True
                h.task.cancel()
        return gone

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
