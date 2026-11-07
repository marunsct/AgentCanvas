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
import traceback
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
from agentcanvas.runtime import masking
from agentcanvas.runtime.identity import CURRENT, RunIdentity
from agentcanvas.runtime.tracing import trace_ref
from agentcanvas.runtime.trajectory import compact
from agentcanvas.runtime.types import REPLAYABLE, ResumeSpec, RunMode, RunSpec
from agentcanvas.services.files import DEFAULT_MAX_BYTES, FileService

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
    event_depth: int = 0  # how many events led to this run (a handler's run emits events one level deeper)
    traced: bool = False  # sent to LangSmith ("Open trace", M6-T6)
    input: dict[str, Any] | None = None  # a fresh run's input: its model replies are recorded (FR-X-13)
    drained: bool = False  # stopped by a server shutdown; another instance continues it (FR-X-10)
    superseded: bool = False  # swept while this instance stalled: another instance continues it (S13)
    finishing: bool = False  # saving its outcome: a cancel now would cut the save short and wedge the thread
    # a task cancelled before it began never runs its code, so nothing would finish the run: until `_pump` starts,
    # a stop is a request it reads first (RT-10)
    started: bool = False
    stop_requested: bool = False
    decided: list[str] = field(default_factory=list)  # Inbox items this run answers (back to pending if it fails)
    deployment_id: str | None = None  # a run of a published deployment (online evaluators score these, S13)
    version_id: str | None = None
    labels: dict[str, str] = field(default_factory=dict)  # what the deployment API caller tagged it with
    trace_redact: list[str] = field(default_factory=list)  # the workflow's fields that never reach a trace
    held: list[dict[str, Any]] = field(default_factory=list)  # last events, published once the run is saved (see _pump)
    size: int = 0  # about how many bytes its events take in memory (finished runs are evicted by this too)
    trimmed_to: int = 0  # events before this index were already looked at by `_trim`
    event_times: list[int] = field(default_factory=list)  # wall clock (ns) of each event, for trace export (S15)
    alerted: set[tuple[str, float]] = field(default_factory=set)  # (period, share) cost alerts this run checked
    routing: list[dict[str, Any]] = field(default_factory=list)  # the model each routed block got (services/routing.py)


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


RUNNING_BYTES = 64 * 1024 * 1024  # a running run's events in memory before its oldest ones are shrunk
KEEP_RECENT = 100  # events at the end that are never emptied
TRIM_TEXT = 200  # what a trimmed event keeps of each long text in it


def _shrunk(value: Any) -> Any:
    """`value` with every string longer than TRIM_TEXT cut to that length (a copy; `value` is left as it is)."""
    if isinstance(value, str):
        return value if len(value) <= TRIM_TEXT else value[:TRIM_TEXT] + "…"
    if isinstance(value, dict):
        return {k: _shrunk(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_shrunk(v) for v in value]
    return value


def _without_secrets(text: str) -> str:
    """`text` with every project secret masked (the values traces are masked with)."""
    values = sorted({v for v in masking.provider() if len(v) >= 6}, key=len, reverse=True)
    return masking.mask_strings(text, values) if values else text


def price(
    model_id: str | None, tokens_in: int, tokens_out: int, cache_read: int = 0, cache_write: int = 0
) -> float | None:
    """USD for a model call from catalog prices; None when the model or its prices are unknown. `tokens_in`
    includes the input read from (`cache_read`) and written to (`cache_write`) the prompt cache, each at its own
    price when the catalog has one. A prompt above the model's long-context threshold is charged its long-context
    prices (the cache prices scaled like the input price)."""
    info = default_registry().model(model_id or "")
    if info is None or info.price_in_per_mtok is None or info.price_out_per_mtok is None:
        return None
    p_in, p_out, scale = info.price_in_per_mtok, info.price_out_per_mtok, 1.0
    if (
        info.price_above_tokens is not None
        and tokens_in > info.price_above_tokens
        and info.price_in_above_per_mtok is not None
        and info.price_out_above_per_mtok is not None
    ):
        scale = info.price_in_above_per_mtok / p_in if p_in else 1.0
        p_in, p_out = info.price_in_above_per_mtok, info.price_out_above_per_mtok
    read = info.price_cache_read_per_mtok * scale if info.price_cache_read_per_mtok is not None else p_in
    write = info.price_cache_write_per_mtok * scale if info.price_cache_write_per_mtok is not None else p_in
    plain = max(0, tokens_in - cache_read - cache_write)
    return (plain * p_in + cache_read * read + cache_write * write + tokens_out * p_out) / 1_000_000


def usage_price(model_id: str | None, usage: dict[str, Any]) -> float | None:
    """`price` of a usage record (`input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`)."""
    return price(
        model_id, int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0)),
        int(usage.get("cache_read_tokens", 0)), int(usage.get("cache_write_tokens", 0)),
    )  # fmt: skip


@dataclass
class Spent:
    """What was spent before a run: by its thread's earlier runs, and by all the workflow's other runs today and this
    month (UTC), on any API instance: runs still going count what they have spent so far (`_concurrent_spend`)."""

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


async def workflow_spend(s: Any, workflow_id: str, *, exclude_run: str | None = None) -> tuple[float, float]:
    """USD the workflow's runs spent today and this month (UTC), those still going at what they have spent so far."""
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
        # picks the models of blocks that list `cheapest_of` (db session, workflow, project id -> workflow, notes)
        self.router: Callable[[Any, Workflow, str], Awaitable[tuple[Workflow, list[dict[str, Any]]]]] | None = None
        # G-5: files sent inline in a run's input (AGENTCANVAS_MAX_UPLOAD_MB, AGENTCANVAS_MAX_PROJECT_FILES_MB)
        self.max_file_bytes, self.max_project_file_bytes = DEFAULT_MAX_BYTES, 0

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
        event_depth: int = 0,
        visitor_hash: str | None = None,
        project_files: bool = False,
    ) -> RunHandle:
        """Start a run. `context=None` reuses the context of the last run on `context_from` (default: this thread).
        A deployment's run takes only files given to that deployment (`visitor_hash`: a share link's visitor, whose
        own uploads on the link it may use); `project_files`: the input was written by a builder (a schedule's), so
        any of the project's files."""
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
        routed, routing = wf, []  # the workflow as this run executes it, and why each routed block got its model
        try:
            # the run resolves secrets and workspace code from the workflow's own project: read it here, at every
            # start, instead of trusting a map filled only when some service happened to load the workflow (after
            # a restart, a hand-over or on another replica it was missing and the default project was used). The
            # prepare hooks (the secrets refresh, the security gate) read it too, so it is known before they run.
            async with self.db.sessionmaker() as s:
                project_id = await s.scalar(select(WorkflowRow.project_id).where(WorkflowRow.id == wf.id))
            if project_id is not None:
                from agentcanvas.services.projects import PROJECT_OF

                PROJECT_OF[wf.id] = project_id
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
                if self.router is not None and project_id is not None:
                    try:
                        routed, routing = await self.router(s, wf, project_id)
                    except Exception:
                        log.exception("run.routing_failed", run_id=run_id)
                if input and project_id is not None:  # G-5: files as the project keeps them, bytes stored first
                    schemas = {c.name: c.type for c in wf.state.channels}
                    input = await FileService(s, self.max_file_bytes, self.max_project_file_bytes).claim(
                        project_id, input, schemas, deployment_id=None if project_files else deployment_id,
                        visitor_hash=visitor_hash,
                    )  # fmt: skip
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
                        origin=origin,
                    )
                )
                decided: list[str] = []
                if resume is not None:
                    decided = await self._decide_interrupts(s, thread_id, resume, decided_by or user_id)
                await s.commit()
        except IntegrityError as exc:
            self.active_by_thread.pop(thread_id, None)
            if _thread_taken(exc):  # another replica created this new thread a moment earlier (R-6)
                raise Conflict(f"thread '{thread_id}' already has an active run") from exc
            raise  # anything else is not a busy thread: never report it as one (RT-20)
        except BaseException:
            self.active_by_thread.pop(thread_id, None)
            raise

        spec = RunSpec(
            workflow=routed,
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
            user_id=user_id,
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
        handle.event_depth = event_depth
        handle.routing = routing
        handle.decided = decided  # an error before the answer is applied puts them back in the Inbox (R-4)
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

    async def _decide_interrupts(self, s: Any, thread_id: str, resume: ResumeSpec, decided_by: str | None) -> list[str]:
        """Mark the answered Inbox items decided; returns their ids."""
        pending = select(InterruptTask.id).where(
            InterruptTask.thread_id == thread_id, InterruptTask.status == "pending"
        )
        if resume.interrupt_id:
            pending = pending.where(InterruptTask.id == resume.interrupt_id)
        ids = list((await s.scalars(pending)).all())
        q = update(InterruptTask).where(InterruptTask.id.in_(ids), InterruptTask.status == "pending")
        decision = resume.value if isinstance(resume.value, dict) else {"value": resume.value}
        res = await s.execute(
            q.values(status="decided", decision=decision, decided_by=decided_by, decided_at=datetime.now(UTC))
        )
        if resume.interrupt_id and res.rowcount == 0 and await s.get(InterruptTask, resume.interrupt_id):
            raise Conflict(f"inbox item '{resume.interrupt_id}' was already decided")  # a concurrent decision won
        return ids

    # ---------------------------------------------------------------- pump ----
    async def _emit(self, h: RunHandle, event: dict[str, Any]) -> None:
        event = {**event, "seq": len(h.events) + 1}
        h.events.append(event)
        h.size += len(json.dumps(event, default=str))
        if h.size > RUNNING_BYTES:
            self._trim(h)
        h.event_times.append(time.time_ns())
        async with h.changed:
            h.changed.notify_all()

    async def _pump(self, h: RunHandle, spec: RunSpec) -> None:
        h.started = True  # from here on a cancel reaches the code below, which always finishes the run
        started = time.perf_counter()
        CURRENT.set(RunIdentity(spec.org_id, spec.user_id))  # app connectors that sign in read it per call (G-7)
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

        def late_usage(record: dict[str, Any]) -> None:  # reported after the run stopped: before its last event
            event = self._priced(h, {"type": "CUSTOM", "name": "ac.usage", "value": record})
            at = next((i for i, e in enumerate(held) if e["type"] in ("RUN_FINISHED", "RUN_ERROR")), len(held))
            held.insert(at, event)

        spec.on_usage = late_usage
        budget = spec.workflow.settings.budget
        events: Any = None  # the adapter's async generator (closed in `finally`)
        by_period = budget is not None and (
            budget.daily_max_cost_usd is not None or budget.monthly_max_cost_usd is not None
        )
        try:
            if h.stop_requested:  # stopped before its task began (`_stop_task`)
                raise asyncio.CancelledError
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
            for note in h.routing:
                await self._emit(h, {"type": "CUSTOM", "name": "ac.route", "value": note})
            before = await self._spent(h, budget) if budget else Spent()
            check_budget(budget, h.usage, before)  # a conversation that is already over budget does not start
            resources = self.resources(spec.thread_id, spec.workflow)
            events = self.adapter_for(spec.workflow).execute(spec, resources)
            async with asyncio.timeout(budget.max_seconds if budget and budget.max_seconds else None):
                async for raw in events:
                    if raw["type"] == "CUSTOM" and raw.get("name") == "ac.usage":
                        priced = self._priced(h, raw)
                        await emit(priced)
                        now = await self._concurrent_spend(h, before) if by_period else before
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
        except Exception as exc:  # noqa: BLE001 - raised before the adapter could redact it (graph build, MCP): R-7
            status, error = "error", _without_secrets(f"{type(exc).__name__}: {exc}")
            trace = _without_secrets("".join(traceback.format_exception(exc)))
            log.error("run.crashed", run_id=h.run_id, error=error, traceback=trace)
            await emit(
                {"type": "RUN_ERROR", "code": "internal", "message": error, "runId": h.run_id, "threadId": h.thread_id},
            )
        finally:
            h.finishing = True  # from here on the run only saves itself; `cancel()` and friends leave it alone
            try:
                await self._close(h, events, status, error, interrupts, held)
            finally:  # however the save went, whoever waits for the run is told it is over
                h.finished_at = time.monotonic()
                h.done.set()
                self._evict()  # a quiet server must not hold finished runs' events beyond its limits
                async with h.changed:
                    h.changed.notify_all()
            # event handlers may take long and start or resume runs (on this thread too): nobody waiting for the
            # run waits for them, but they still run in its task, so a shutdown lets them finish
            await self._hooks(self.after_hooks, h, status, error, interrupts)

    async def _close(
        self,
        h: RunHandle,
        events: Any,
        status: str,
        error: str | None,
        interrupts: list[dict[str, Any]],
        held: list[dict[str, Any]],
    ) -> None:
        """Save a run's outcome, free its thread, publish its last events and run the finish hooks."""
        try:
            if events is not None:
                await events.aclose()  # stops the graph if we left early (budget, cancel)
            h.status = status
            await self._finish(h, status, error, interrupts)
        finally:  # even if finalising was cut short: the thread is released and the last events are published
            if self.active_by_thread.get(h.thread_id) == h.run_id:  # the thread is free for its next run
                del self.active_by_thread[h.thread_id]
            for event in held:
                await self._emit(h, event)
        await self._hooks(self.finish_hooks, h, status, error, interrupts)

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
                        events=compact([*h.events, *h.held], h.event_times, time.time_ns()),
                        handover=h.drained,
                    )
                )
                if res.rowcount == 0:  # type: ignore[attr-defined]
                    await s.commit()
                    return
                await s.execute(update(Thread).where(Thread.id == h.thread_id).values(status=thread_status))
                untouched = not any(e.get("type") == "STEP_FINISHED" for e in h.events)
                # failed, cancelled or stopped by a restart before the answer took: the run still waits for it
                stopped = status in ("error", "cancelled") or (status == "interrupted" and h.drained)
                if stopped and h.decided and untouched:
                    await s.execute(
                        update(InterruptTask)
                        .where(InterruptTask.id.in_(h.decided), InterruptTask.status == "decided")
                        .values(status="pending", decision=None, decided_by=None, decided_at=None)
                    )
                    if thread_status in ("error", "idle"):
                        await s.execute(update(Thread).where(Thread.id == h.thread_id).values(status="interrupted"))
                for intr in interrupts:
                    asked = {
                        "run_id": h.run_id, "canvas_node_id": intr.get("canvas_node_id") or "", "kind": intr["kind"],
                        "payload": _as_object(intr["payload"]),
                    }  # fmt: skip
                    row = await s.get(InterruptTask, intr["id"])
                    if row is None:
                        s.add(InterruptTask(id=intr["id"], thread_id=h.thread_id, **asked))
                    elif row.status != "pending":  # asked again (its answer did not take): open it again
                        await s.execute(
                            update(InterruptTask)
                            .where(InterruptTask.id == intr["id"])
                            .values(status="pending", decision=None, decided_by=None, decided_at=None, **asked)
                        )
                s.add_all(rubric_scores(h))
                await s.commit()
        except Exception:
            log.exception("run.finish_failed", run_id=h.run_id)
            await self._finish_bare(h, status, error, thread_status)
        except asyncio.CancelledError:  # a shutdown that would not wait: leave at least the status and a free thread
            await self._finish_bare(h, status, error, thread_status)
            raise

    async def _finish_bare(self, h: RunHandle, status: str, error: str | None, thread_status: str) -> None:
        """The least a finished run must leave in the database when saving everything failed (a recording or a usage
        figure the column cannot store): its status and a free thread. Without it the thread would stay 'running' and
        refuse every later message. Its tokens and cost are kept too (budgets and reports count them), unless they
        are what could not be stored."""
        usage = {
            "tokens_in": h.usage["input_tokens"], "tokens_out": h.usage["output_tokens"],
            "cost_usd": h.usage["cost_usd"], "cache_hits": h.usage["cache_hits"], "fallbacks": h.usage["fallbacks"],
        }  # fmt: skip
        for extra in (usage, {}):
            try:
                async with self.db.sessionmaker() as s:
                    await s.execute(
                        update(Run)
                        .where(Run.id == h.run_id, Run.status == "running")
                        .values(status=status, error=error, ended_at=datetime.now(UTC), handover=h.drained, **extra)
                    )
                    await s.execute(update(Thread).where(Thread.id == h.thread_id).values(status=thread_status))
                    await s.commit()
                return
            except Exception:
                log.exception("run.finish_bare_failed", run_id=h.run_id, with_usage=bool(extra))

    def _priced(self, h: RunHandle, raw: dict[str, Any]) -> dict[str, Any]:
        """Add the cost to a usage event and to the run's totals."""
        v = dict(raw["value"])
        if v.get("cached"):  # the model gateway answered from its cache (S26)
            h.usage["cache_hits"] += 1
        if v.get("failed"):  # ... or a backup model answered
            h.usage["fallbacks"] += 1
        has_tokens = v["input_tokens"] or v["output_tokens"]
        v["cost_usd"] = usage_price(v.get("model"), v) if has_tokens else 0.0
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

    async def _concurrent_spend(self, h: RunHandle, before: Spent) -> Spent:
        """Save what the run has spent so far on its row, and read what the workflow's other runs have spent, on any
        API instance. Every run does the same after each model call, so runs going at once, here or on other replicas,
        stop a day or month budget within a model call of each other's spending (G-8). If the database does not
        answer, the run carries on with what it knew."""
        try:
            async with self.db.sessionmaker() as s:
                await s.execute(
                    update(Run).where(Run.id == h.run_id, Run.status == "running").values(cost_usd=h.usage["cost_usd"])
                )
                await s.commit()
                day, month = await workflow_spend(s, h.workflow_id, exclude_run=h.run_id)
        except Exception:
            log.warning("run.spend_sync_failed", run_id=h.run_id, exc_info=True)
            return before
        return replace(before, day_cost=day, month_cost=month)

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

    @staticmethod
    def _trim(h: RunHandle) -> None:
        """A run going on for hours keeps every event for late subscribers: past RUNNING_BYTES the oldest are
        shrunk in place, token deltas emptied and long text in other events (step outputs, tool answers) cut to
        TRIM_TEXT characters. Their numbers stay, so `after=` still works; the full text is in the run's final
        events and its checkpoint (R-8). Each event is looked at once: `trimmed_to` remembers where this stopped,
        so a long run does not rescan its whole list on every new event (RT-14)."""
        end = len(h.events) - KEEP_RECENT  # the newest stay whole, whatever the size
        i = h.trimmed_to
        while i < end and h.size > RUNNING_BYTES * 3 // 4:
            ev = h.events[i]
            i += 1
            if ev.get("type") in ("RUN_STARTED", "RUN_FINISHED", "RUN_ERROR") or ev.get("trimmed"):
                continue
            if ev.get("type") == "TEXT_MESSAGE_CONTENT":
                small = {**ev, "delta": "", "trimmed": True} if ev.get("delta") else ev
            else:
                shrunk = _shrunk(ev)
                small = {**shrunk, "trimmed": True} if shrunk != ev else ev
            if small is not ev:
                h.size -= len(json.dumps(ev, default=str)) - len(json.dumps(small, default=str))
                h.events[i - 1] = small
        h.trimmed_to = i

    def _evict(self) -> None:
        """Forget the oldest finished runs beyond `keep_finished` and `keep_bytes` of events (running ones stay)."""
        finished = [rid for rid, h in self.handles.items() if h.done.is_set()]
        for rid in finished[: max(0, len(finished) - self._keep)]:  # running runs do not count (R-8)
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

    @staticmethod
    def _stoppable(h: RunHandle) -> bool:
        """Still doing its work: a run that is saving its outcome finishes that first (a second cancel, a drain or a
        superseded heartbeat used to cut the save short and leave its thread `running`)."""
        return h.task is not None and not h.done.is_set() and not h.finishing

    async def cancel(self, run_id: str) -> bool:
        h = self.handles.get(run_id)
        if h is None or not self._stoppable(h):
            return False
        self._stop_task(h)
        return True

    @staticmethod
    def _stop_task(h: RunHandle) -> None:
        if h.started:
            h.task.cancel()  # type: ignore[union-attr]
        else:
            h.stop_requested = True

    async def drain(self, grace_s: float) -> list[str]:
        """Stop taking runs, give running ones `grace_s` to finish, then stop the rest at their last checkpoint
        and mark them for hand-over (`Run.handover`); returns the ids of the runs handed over (FR-X-10)."""
        self.draining = True
        running = [h for h in self.handles.values() if h.task and not h.done.is_set()]
        if running and grace_s > 0:
            waiters = [asyncio.ensure_future(h.done.wait()) for h in running]
            _, pending = await asyncio.wait(waiters, timeout=grace_s)
            for w in pending:  # runs that did not finish in time: their waiters would otherwise outlive the loop
                w.cancel()
        left = [h for h in running if self._stoppable(h)]
        for h in left:
            h.drained = True
            self._stop_task(h)
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
            if h is not None and self._stoppable(h):
                log.warning("run.superseded", run_id=run_id)
                h.superseded = True
                self._stop_task(h)
        return gone

    async def shutdown(self) -> None:
        for h in list(self.handles.values()):
            if self._stoppable(h):
                self._stop_task(h)
        await asyncio.gather(*(h.task for h in self.handles.values() if h.task), return_exceptions=True)

    # -------------------------------------------------------------- queries ----
    async def pending_interrupts(self, thread_id: str) -> list[InterruptTask]:
        async with self.db.sessionmaker() as s:
            q = select(InterruptTask).where(InterruptTask.thread_id == thread_id, InterruptTask.status == "pending")
            return list((await s.scalars(q)).all())


def _thread_taken(exc: IntegrityError) -> bool:
    """The insert of a new thread lost to one with the same id: SQLite names the column, Postgres the key."""
    text = str(exc.orig)
    return "thread.id" in text or "thread_pkey" in text


def _title(input: dict[str, Any] | None) -> str | None:
    if not input:
        return None
    first = next((v for v in input.values() if isinstance(v, str) and v), None)
    return first[:80] if first else None


def _as_object(payload: Any) -> dict[str, Any]:
    return payload if isinstance(payload, dict) else {"value": payload}
