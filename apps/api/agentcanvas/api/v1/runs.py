"""Runs, threads and the Inbox (tickets M1-T6, M4-T4…T8, M4-T10 — backend).

Run endpoints stream canonical events as Server-Sent Events (ADR 0007). Streams are views of a
background run: disconnecting does not stop it, `GET /v1/runs/{id}/events?after=<seq>` re-attaches.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, Literal, cast

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from agentcanvas.adapters.spi import ThreadStateAbility, require, supports
from agentcanvas.api.audit import note, payload_hash
from agentcanvas.api.deps import RANK, CurrentUser, current_user, get_session, get_settings_dep
from agentcanvas.db.models import Deployment, InterruptTask, Project, Run, Thread, WorkflowRow, WorkflowVersion
from agentcanvas.db.repositories import LIGHT_RUN, delete_thread_rows
from agentcanvas.errors import Conflict, Forbidden, InvalidInput, NotFound, UpstreamError, WorkflowHasErrors
from agentcanvas.ir.models import Workflow
from agentcanvas.observability import counted_stream
from agentcanvas.runtime.admission import Priority
from agentcanvas.runtime.runner import CANCELLED_BY_USER, RunHandle, Runner
from agentcanvas.runtime.tracing import web_url
from agentcanvas.runtime.trajectory import compact
from agentcanvas.runtime.types import ResumeSpec, ThreadState, is_tool_recording
from agentcanvas.services.explain import explain
from agentcanvas.services.redeploy import thread_version
from agentcanvas.services.run_search import RunSearchResult, RunSearchService
from agentcanvas.services.secrets import hide_secrets
from agentcanvas.services.trajectory import ThreadTrajectory, build_run
from agentcanvas.services.workflows import WorkflowService
from agentcanvas.settings import Settings
from agentcanvas.validation.policies import CODES as POLICY_CODES

router = APIRouter(tags=["runs"])


def _travel(r: Runner, wf: Workflow) -> ThreadStateAbility:
    """The workflow's adapter, for history, replay and forks (S20: refused when its framework has no time travel)."""
    return require(r.adapter_for(wf), "time_travel", ThreadStateAbility)


SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def runner(request: Request) -> Runner:
    return request.app.state.runner


# ------------------------------------------------------------------ models ----
class Breakpoints(BaseModel):
    before: list[str] = Field(default_factory=list)  # canvas node ids
    after: list[str] = Field(default_factory=list)


class DebugOptions(BaseModel):
    """Debug-run aids (M4-T9); never part of the workflow or its exported code."""

    pins: dict[str, dict[str, Any]] | None = None  # canvas node id -> output to replay instead of running it
    mocks: dict[str, list[Any]] | None = None  # model resource name -> replies (text or {"tool_calls": [...]})
    dry_run: bool = False  # risky tools only record what they would do (FR-X-02)


class RunRequest(DebugOptions):
    input: dict[str, Any] = Field(default_factory=dict)
    thread_id: str | None = None
    mode: Literal["normal", "debug"] = "normal"
    breakpoints: Breakpoints | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    durability: Literal["exit", "async", "sync"] | None = None
    stream: bool = True  # false: return 202 {run_id} and let the client attach later
    priority: Priority | None = Field(
        None,
        description="its place in the organisation's run queue, when a concurrent-run limit holds it back "
        "(default: normal)",
    )


class ResumeRequest(DebugOptions):
    value: Any = None
    interrupt_id: str | None = None
    stream: bool = True


class StateEdit(BaseModel):
    values: dict[str, Any]
    as_node: str | None = None  # canvas node id (or node name) the edit is attributed to
    checkpoint_id: str | None = None


class ForkRequest(BaseModel):
    checkpoint_id: str
    values: dict[str, Any] | None = None  # optional edit applied before continuing ("edit & branch")
    as_node: str | None = None
    mode: Literal["branch", "copy"] = "branch"  # copy = continue in a new thread, original untouched
    stream: bool = True


class RunOut(BaseModel):
    id: str
    thread_id: str
    status: str
    mode: str
    ir_hash: str
    error: str | None
    started_at: datetime
    ended_at: datetime | None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    traced: bool = False  # GET /v1/runs/{id}/trace opens it in LangSmith


def _run_out(run: Run) -> RunOut:
    return RunOut(
        id=run.id,
        thread_id=run.thread_id,
        status=run.status,
        mode=run.mode,
        ir_hash=run.ir_hash,
        error=run.error,
        started_at=run.started_at,
        ended_at=run.ended_at,
        tokens_in=run.tokens_in or 0,
        tokens_out=run.tokens_out or 0,
        cost_usd=float(run.cost_usd or 0),
        traced=bool(run.trace_url),
    )


class ThreadOut(BaseModel):
    id: str
    workflow_id: str
    title: str | None
    status: str
    created_at: datetime
    updated_at: datetime
    # the Runs page's summary (filled by GET /threads)
    workflow_name: str | None = None
    deployment_id: str | None = Field(None, description="set when the conversation came through a deployment's API")
    runs: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    last_started_at: datetime | None = None
    last_ended_at: datetime | None = None
    last_error: str | None = None
    last_run_id: str | None = None


class ThreadPage(BaseModel):
    items: list[ThreadOut]
    next_cursor: str | None


class InterruptOut(BaseModel):
    id: str
    workflow_id: str | None = None
    workflow_name: str | None = None
    project_id: str | None = Field(None, description="the workflow's project: the Inbox spans the organisation")
    project_name: str | None = None
    thread_id: str
    run_id: str
    canvas_node_id: str
    node_label: str | None = Field(None, description="the waiting block's label (or name) in the workflow's draft")
    kind: str
    payload: dict[str, Any]
    status: str
    decision: dict[str, Any] | None
    created_at: datetime
    decided_at: datetime | None


class Decision(BaseModel):
    value: Any


# ----------------------------------------------------------------- helpers ----
def _sse(events: AsyncIterator[dict[str, Any]]) -> AsyncIterator[str]:
    async def gen() -> AsyncIterator[str]:
        async for ev in events:
            yield f"id: {ev.get('seq', '')}\ndata: {json.dumps(ev, default=str)}\n\n"

    return counted_stream(gen())


def _respond(r: Runner, h: RunHandle, stream: bool) -> Response:
    headers = {"x-run-id": h.run_id, "x-thread-id": h.thread_id}
    if not stream:
        return Response(
            json.dumps({"run_id": h.run_id, "thread_id": h.thread_id}),
            status_code=202,
            media_type="application/json",
            headers=headers,
        )
    return StreamingResponse(
        _sse(r.subscribe(h.run_id)), media_type="text/event-stream", headers={**SSE_HEADERS, **headers}
    )


async def _thread_target(
    session: AsyncSession, settings: Settings, t: Thread, runnable: bool = True
) -> tuple[Workflow, dict[str, Any]]:
    """What a thread runs: its deployment's pinned version (M6-T4), or the draft; plus `Runner.start` kwargs."""
    svc = _svc(session, settings)
    if t.deployment_id:
        dep = await session.get(Deployment, t.deployment_id)
        ver = await session.get(WorkflowVersion, dep.version_id) if dep else None
        if ver is None:
            raise NotFound(f"the deployment of thread '{t.id}' no longer exists")
        ver = await thread_version(session, t.id, ver)  # FR-X-10: the new version only if it can resume it
        await svc.get(t.workflow_id)  # registers the workflow's project (workspace code)
        return Workflow.model_validate(ver.ir), {"deployment_id": dep.id, "version_id": ver.id}  # type: ignore[union-attr]
    if runnable:
        return await _runnable_workflow(svc, t.workflow_id), {}
    return (await svc.get(t.workflow_id)).workflow, {}


async def handover_target(
    session: AsyncSession, settings: Settings, thread_id: str, version_id: str | None
) -> tuple[Workflow, dict[str, Any]]:
    """What a run handed over by a draining server continues on (FR-X-10): the version it was running, or the
    draft for a conversation in the editor."""
    t = await _thread(session, thread_id)
    ver = await session.get(WorkflowVersion, version_id) if t.deployment_id and version_id else None
    if ver is None:
        return await _thread_target(session, settings, t)
    await _svc(session, settings).get(t.workflow_id)  # registers the workflow's project (workspace code)
    return Workflow.model_validate(ver.ir), {"deployment_id": t.deployment_id, "version_id": ver.id}


async def _runnable_workflow(svc: WorkflowService, wf_id: str) -> Workflow:
    view = await svc.get(wf_id)
    # organisation policies are enforced at publish; they do not stop builders trying a draft
    errors = [d.model_dump() for d in view.diagnostics if d.severity == "error" and d.code not in POLICY_CODES]
    if errors:
        raise WorkflowHasErrors(f"workflow has {len(errors)} error(s); fix them before running", errors=errors)
    if not view.workflow.nodes:
        # a new workflow is not "wrong" (no red on an empty canvas), but there is nothing to run
        raise InvalidInput("this workflow has no steps yet: add one from the palette and connect it from START to END")
    return view.workflow


def _debug(wf: Workflow, opts: DebugOptions, mode: str) -> dict[str, Any]:
    """Validated pins/mocks as Runner.start keyword arguments."""
    if not (opts.pins or opts.mocks or opts.dry_run):
        return {}
    if mode != "debug":
        raise InvalidInput("pinned outputs, mock models and dry runs require mode 'debug'")
    for node_id in opts.pins or {}:
        n = wf.node(node_id)
        if n is None or n.type in ("core.router", "core.map"):
            raise InvalidInput(f"'{node_id}' cannot be pinned (unknown node, router or map)")
    models = {r.name for r in wf.resources if r.type == "model.chat"}
    tools = {r.name for r in wf.resources if r.type in ("tool.python", "tool.http", "mcp.server")}
    for name, replies in (opts.mocks or {}).items():
        if name in tools and replies and all(is_tool_recording(r) for r in replies):
            continue  # recorded tool outputs (FR-X-13 replay)
        if name not in models:
            raise InvalidInput(f"'{name}' is not a chat model resource of this workflow")
    return {"pins": opts.pins or {}, "mocks": opts.mocks or {}, "dry_run": opts.dry_run}


def _context(wf: Workflow, given: dict[str, Any], user: CurrentUser) -> dict[str, Any]:
    ctx: dict[str, Any] = {}
    for f in wf.context.fields:
        if f.source == "user":
            ctx[f.name] = user.id  # never from client input or model output (doc 08 rule 2)
        elif f.source == "tenant":
            ctx[f.name] = user.org_id
        elif f.name in given:
            ctx[f.name] = given[f.name]
        elif f.required:
            raise InvalidInput(f"context field '{f.name}' is required")
    return ctx


async def _thread(session: AsyncSession, thread_id: str) -> Thread:
    t = await session.get(Thread, thread_id)
    if t is None:
        raise NotFound(f"thread '{thread_id}' not found")
    return t


def _svc(session: AsyncSession, settings: Settings) -> WorkflowService:
    return WorkflowService(session, settings)


# -------------------------------------------------------------------- runs ----
@router.post("/workflows/{wf_id}/runs/stream")
async def start_run(
    wf_id: str,
    body: RunRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
    r: Runner = Depends(runner),
    user: CurrentUser = Depends(current_user),
) -> Response:
    wf = await _runnable_workflow(_svc(session, settings), wf_id)
    if body.breakpoints and body.mode != "debug":
        raise InvalidInput("breakpoints require mode 'debug'")
    if body.thread_id:
        # thread ids are one namespace across organisations: an id in use anywhere else gets the same answer, so it
        # tells nothing about which workflow or organisation holds it
        t = await session.get(Thread, body.thread_id)
        if t is not None and t.workflow_id != wf_id:
            raise InvalidInput(
                f"thread id '{body.thread_id}' cannot be used here: continue a conversation of this workflow, or "
                "leave thread_id out (or choose another id) to start a new one"
            )
    bp = body.breakpoints or Breakpoints()
    h = await r.start(
        wf,
        thread_id=body.thread_id,
        input=body.input,
        mode=body.mode,
        breakpoints_before=bp.before,
        breakpoints_after=bp.after,
        context=_context(wf, body.context, user),
        durability=body.durability,
        user_id=user.id,
        service_connection=user.role == "admin",  # the organisation's service connection is the admins'
        priority=body.priority,
        **_debug(wf, body, body.mode),
    )
    return _respond(r, h, body.stream)


@router.get("/runs/{run_id}", response_model=RunOut)
async def get_run(run_id: str, session: AsyncSession = Depends(get_session)) -> RunOut:
    run = await session.get(Run, run_id)
    if run is None:
        raise NotFound(f"run '{run_id}' not found")
    return _run_out(run)


@router.get("/runs/{run_id}/trace", status_code=307)
async def open_trace(
    run_id: str,
    format: Literal["redirect", "json"] = Query("redirect", description="json: return {url} instead of redirecting"),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Redirect to the run's LangSmith trace (M6-T6). The web app asks for `format=json`: a browser link
    cannot carry the sign-in token."""
    run = await session.get(Run, run_id)
    if run is None or not run.trace_url:
        raise NotFound(f"run '{run_id}' was not traced (set LANGSMITH_TRACING=true and LANGSMITH_API_KEY)")
    try:
        url = await asyncio.to_thread(web_url, run.trace_url)
    except Exception as exc:  # noqa: BLE001 - LangSmith unreachable or misconfigured
        raise UpstreamError(f"could not reach LangSmith: {exc}") from None
    if format == "json":
        return JSONResponse({"url": url})
    return Response(status_code=307, headers={"Location": url})


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str, after: int = Query(0, ge=0), r: Runner = Depends(runner)) -> Response:
    if run_id not in r.handles:  # a 404 now, not a 200 stream that fails once the response has started
        raise NotFound(f"run '{run_id}' is not active on this server (finished runs are kept briefly)")
    return StreamingResponse(_sse(r.subscribe(run_id, after)), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/runs/{run_id}/cancel", status_code=202)
async def cancel_run(
    run_id: str, session: AsyncSession = Depends(get_session), r: Runner = Depends(runner)
) -> dict[str, Any]:
    """Stop a run. One running on this instance stops now. Requests are spread over the replicas, so the run may be
    running on another one: it is marked in the database and that instance stops it at its next heartbeat
    (`pending`, within `AGENTCANVAS_HANDOVER_INTERVAL_S`)."""
    if await r.cancel(run_id):
        return {"cancelled": True, "pending": False}
    if (h := r.handles.get(run_id)) is not None and not h.done.is_set():  # here, saving its outcome: let it
        return {"cancelled": False, "pending": False}
    asked = await session.execute(
        update(Run)
        .where(Run.id == run_id, Run.status == "running")
        .values(stop_requested=True, stop_reason=func.coalesce(Run.stop_reason, CANCELLED_BY_USER))
    )
    if asked.rowcount == 1:  # type: ignore[attr-defined]
        await session.commit()
        return {"cancelled": True, "pending": True}
    # paused by a redeploy and waiting to be continued on another instance: cancelled here, so none continues it
    ended = await session.execute(
        update(Run)
        .where(Run.id == run_id, Run.handover.is_(True), Run.status != "running")
        .values(status="cancelled", error=CANCELLED_BY_USER, handover=False, stop_requested=True,
                stop_reason=CANCELLED_BY_USER, ended_at=datetime.now(UTC))
    )  # fmt: skip
    cancelled = ended.rowcount == 1  # type: ignore[attr-defined]
    if cancelled:
        thread_id = await session.scalar(select(Run.thread_id).where(Run.id == run_id))
        await session.execute(update(Thread).where(Thread.id == thread_id).values(status="idle"))
    await session.commit()
    return {"cancelled": cancelled, "pending": False}


# ------------------------------------------------------------------ search ----
def run_search(request: Request) -> RunSearchService:
    return request.app.state.run_search


@router.get("/projects/{project_id}/runs/search", response_model=RunSearchResult)
async def search_runs(
    project_id: str,
    q: str = Query(..., min_length=1, max_length=500, description='what to find: "customers asking for a refund"'),
    workflow_id: str | None = None,
    mode: Literal["hybrid", "semantic", "keyword"] = "hybrid",
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    svc: RunSearchService = Depends(run_search),
) -> RunSearchResult:
    """Find conversations by meaning (G-10): the project's runs ranked against `q` with its embedding model, the
    best run of each conversation first. `stale` counts runs not in the index yet (`POST …/runs/search/index`)."""
    if workflow_id:
        row = await session.get(WorkflowRow, workflow_id)
        if row is None or row.project_id != project_id:
            raise NotFound(f"workflow '{workflow_id}' not found")
    return await svc.search(project_id, q, workflow_id=workflow_id, limit=limit, mode=mode)


@router.post("/projects/{project_id}/runs/search/index")
async def index_runs(project_id: str, svc: RunSearchService = Depends(run_search)) -> dict[str, int]:
    """Index the project's runs that are not in the search index (from before it, or made with another embedding
    model), newest first, at most 2000 a call."""
    return await svc.reindex(project_id)


# ----------------------------------------------------------------- threads ----
def _utc(t: datetime) -> datetime:
    return t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)


@router.get("/threads", response_model=ThreadPage)
async def list_threads(
    workflow_id: str | None = None,
    project_id: str | None = Query(None, description="only this project's conversations"),
    status: str | None = None,
    since: datetime | None = Query(None, description="only conversations that began at or after this time (UTC)"),
    until: datetime | None = Query(None, description="only conversations that began before this time (UTC)"),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> ThreadPage:
    """Conversations (threads) newest first, each with its workflow's name and a summary of its runs."""
    q = (
        select(Thread, WorkflowRow.name)
        .join(WorkflowRow, WorkflowRow.id == Thread.workflow_id)
        .join(Project, Project.id == WorkflowRow.project_id)
        .where(Project.org_id == user.org_id)
    )
    if workflow_id:
        q = q.where(Thread.workflow_id == workflow_id)
    if project_id:
        q = q.where(WorkflowRow.project_id == project_id)
    if status:
        q = q.where(Thread.status == status)
    if since:
        q = q.where(Thread.created_at >= _utc(since))
    if until:
        q = q.where(Thread.created_at < _utc(until))
    if cursor:
        # ordered by when each began, not by id: a caller may name its own threads ("support-42"), which sort anywhere
        # a thread of another organisation is as unknown as a missing one, so cursors cannot probe ids
        known = await session.scalar(
            select(Thread.id)
            .join(WorkflowRow, WorkflowRow.id == Thread.workflow_id)
            .join(Project, Project.id == WorkflowRow.project_id)
            .where(Thread.id == cursor, Project.org_id == user.org_id)
        )
        if known is None:
            raise InvalidInput(f"unknown cursor '{cursor}'")
        # compared in SQL, not through a bound value: SQLite keeps CURRENT_TIMESTAMP as text a datetime never equals
        after = select(Thread.created_at).where(Thread.id == cursor).scalar_subquery()
        q = q.where(or_(Thread.created_at < after, and_(Thread.created_at == after, Thread.id < cursor)))
    order = (Thread.created_at.desc(), Thread.id.desc())
    rows = list((await session.execute(q.order_by(*order).limit(limit + 1))).all())
    page = rows[:limit]
    ids = [t.id for t, _ in page]
    totals: dict[str, tuple[int, int, float]] = {}
    last: dict[str, Any] = {}
    if page:  # added up in SQL: a conversation with thousands of runs does not load them
        found = await session.execute(
            select(Run.thread_id, func.count(), func.sum(Run.tokens_in + Run.tokens_out), func.sum(Run.cost_usd))
            .where(Run.thread_id.in_(ids))
            .group_by(Run.thread_id)
        )
        totals = {tid: (int(n), int(tokens or 0), float(cost or 0)) for tid, n, tokens, cost in found.tuples()}
        newest = func.row_number().over(partition_by=Run.thread_id, order_by=(Run.started_at.desc(), Run.id.desc()))
        ranked = (
            select(Run.thread_id, Run.id, Run.started_at, Run.ended_at, Run.error, newest.label("n"))
            .where(Run.thread_id.in_(ids))
            .subquery()
        )
        last = {r.thread_id: r for r in (await session.execute(select(ranked).where(ranked.c.n == 1))).all()}
    items = []
    for t, wf_name in page:
        n, tokens, cost = totals.get(t.id, (0, 0, 0.0))
        lr = last.get(t.id)
        items.append(
            ThreadOut(
                id=t.id,
                workflow_id=t.workflow_id,
                title=t.title,
                status=t.status,
                created_at=t.created_at,
                updated_at=t.updated_at,
                workflow_name=wf_name,
                deployment_id=t.deployment_id,
                runs=n,
                tokens=tokens,
                cost_usd=cost,
                last_started_at=lr.started_at if lr else None,
                last_ended_at=lr.ended_at if lr else None,
                last_error=lr.error if lr else None,
                last_run_id=lr.id if lr else None,
            )
        )
    return ThreadPage(items=items, next_cursor=items[-1].id if len(rows) > limit else None)


@router.get("/threads/{thread_id}")
async def get_thread(
    thread_id: str,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
    r: Runner = Depends(runner),
) -> dict[str, Any]:
    t = await _thread(session, thread_id)
    wf, _ = await _thread_target(session, settings, t, runnable=False)
    res = r.resources(thread_id, wf)
    adapter = r.adapter_for(wf)
    if supports(adapter, "thread_state"):
        state = await cast(ThreadStateAbility, adapter).get_state(wf, thread_id, res)
    else:  # S20: a framework without saved state still shows the conversation, without its values
        state = ThreadState(values={}, next=[], interrupts=[], checkpoint_id=None)
    return hide_secrets({
        "id": t.id,
        "workflow_id": t.workflow_id,
        "status": t.status,
        "title": t.title,
        "active_run_id": r.active_by_thread.get(thread_id),
        "values": state.values,
        "next": state.next,
        "checkpoint_id": state.checkpoint_id,
        "step": state.step,
        "interrupts": [i.__dict__ for i in state.interrupts],
        "state_available": supports(adapter, "thread_state"),
    }, _secrets(res))  # fmt: skip


def _idle(r: Runner, thread_id: str) -> None:
    """Checkpoints of a running thread are the run's to write: an edit now would race it."""
    if thread_id in r.active_by_thread:
        raise Conflict(f"thread '{thread_id}' has an active run", run_id=r.active_by_thread[thread_id])


def _secrets(resources: Any) -> dict[str, str]:
    return getattr(resources, "_secrets", None) or {}


@router.get("/threads/{thread_id}/history")
async def thread_history(
    thread_id: str,
    limit: int = Query(100, ge=1, le=1000),
    include_values: bool = False,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
    r: Runner = Depends(runner),
) -> list[dict[str, Any]]:
    t = await _thread(session, thread_id)
    wf, _ = await _thread_target(session, settings, t, runnable=False)
    res = r.resources(thread_id, wf)
    items = await _travel(r, wf).history(wf, thread_id, res, limit, include_values)
    out = [{k: v for k, v in c.__dict__.items() if include_values or k != "values"} for c in items]
    return hide_secrets(out, _secrets(res))


@router.get("/threads/{thread_id}/checkpoints/{checkpoint_id}")
async def thread_checkpoint(
    thread_id: str,
    checkpoint_id: str,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
    r: Runner = Depends(runner),
) -> dict[str, Any]:
    """The state saved at one checkpoint: the Timeline lists checkpoints without it and reads the one picked."""
    t = await _thread(session, thread_id)
    wf, _ = await _thread_target(session, settings, t, runnable=False)
    res = r.resources(thread_id, wf)
    state = await _travel(r, wf).get_state(wf, thread_id, res, checkpoint_id)
    if state.checkpoint_id != checkpoint_id:
        raise NotFound(f"checkpoint '{checkpoint_id}' not found in thread '{thread_id}'")
    out = {"checkpoint_id": state.checkpoint_id, "step": state.step, "next": state.next, "values": state.values}
    return hide_secrets(out, _secrets(res))


@router.get("/threads/{thread_id}/explain")
async def explain_thread(
    thread_id: str,
    limit: int = Query(200, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
    r: Runner = Depends(runner),
) -> list[dict[str, Any]]:
    """Explain mode (FR-X-17): what ran at each super-step, what it wrote and why routers chose their route."""
    t = await _thread(session, thread_id)
    wf, _ = await _thread_target(session, settings, t, runnable=False)
    res = r.resources(thread_id, wf)
    history = await _travel(r, wf).history(wf, thread_id, res, limit, include_values=True)
    return hide_secrets(explain(wf, history), _secrets(res))


@router.get("/threads/{thread_id}/runs", response_model=list[RunOut])
async def thread_runs(thread_id: str, session: AsyncSession = Depends(get_session)) -> list[RunOut]:
    await _thread(session, thread_id)
    q = select(Run).options(*LIGHT_RUN).where(Run.thread_id == thread_id).order_by(Run.id)
    rows = (await session.scalars(q)).all()
    return [_run_out(x) for x in rows]


@router.get("/threads/{thread_id}/trajectory", response_model=ThreadTrajectory)
async def thread_trajectory(
    thread_id: str,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
    r: Runner = Depends(runner),
) -> ThreadTrajectory:
    """The conversation as a tree (G-10): each run's steps, helper agents, model replies, tool calls and framework
    parts, with times and tokens. A run still going on shows what it did so far."""
    t = await _thread(session, thread_id)
    try:
        wf: Workflow | None = (await _thread_target(session, settings, t, runnable=False))[0]
    except NotFound:  # its deployment is gone: the tree still shows, with step ids for labels
        wf = None
    q = select(Run).options(defer(Run.recording), defer(Run.context)).where(Run.thread_id == thread_id)
    runs = (await session.scalars(q.order_by(Run.started_at, Run.id))).all()
    out = []
    for i, run in enumerate(runs, 1):
        events = run.events
        h = r.handles.get(run.id)
        if h is not None and not h.done.is_set():  # still running here: what it did so far
            events = compact(list(h.events), h.event_times, time.time_ns())
        out.append(build_run(run.id, events, wf, status=run.status, started_at=run.started_at, index=i,
                             error=run.error))  # fmt: skip
    res = r.resources(thread_id, wf) if wf is not None else None
    return ThreadTrajectory.model_validate(
        hide_secrets(ThreadTrajectory(thread_id=t.id, workflow_id=t.workflow_id, runs=out).model_dump(), _secrets(res))
    )


@router.post("/threads/{thread_id}/resume")
async def resume_thread(
    thread_id: str,
    body: ResumeRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
    r: Runner = Depends(runner),
    user: CurrentUser = Depends(current_user),
) -> Response:
    if (body.pins or body.mocks or body.dry_run) and RANK.get(user.role, -1) < RANK["builder"]:
        # reviewers answer what the run asks; pins and scripted replies would let them drive its tools
        raise Forbidden(f"your role '{user.role}' can resume but not with debug pins, mocks or dry runs")
    t = await _thread(session, thread_id)
    wf, target_kw = await _thread_target(session, settings, t)
    note(request, interrupt_id=body.interrupt_id, payload_sha256=payload_hash(body.value))
    h = await r.start(
        wf,
        thread_id=thread_id,
        resume=ResumeSpec(body.value, body.interrupt_id),
        user_id=user.id,
        service_connection=user.role == "admin",
        mode="debug" if body.pins or body.mocks or body.dry_run else "normal",
        **_debug(wf, body, "debug"),
        **target_kw,
    )
    return _respond(r, h, body.stream)


@router.post("/threads/{thread_id}/continue")
async def continue_thread(
    thread_id: str,
    stream: bool = True,
    body: DebugOptions | None = None,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
    r: Runner = Depends(runner),
    user: CurrentUser = Depends(current_user),
) -> Response:
    """Continue after a breakpoint (input None)."""
    t = await _thread(session, thread_id)
    wf, target_kw = await _thread_target(session, settings, t)
    debug = _debug(wf, body or DebugOptions(), "debug")
    h = await r.start(wf, thread_id=thread_id, input=None, mode="debug", user_id=user.id, **debug, **target_kw)
    return _respond(r, h, stream)


@router.post("/threads/{thread_id}/state")
async def edit_state(
    thread_id: str,
    body: StateEdit,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
    r: Runner = Depends(runner),
) -> dict[str, Any]:
    t = await _thread(session, thread_id)
    wf, _ = await _thread_target(session, settings, t, runnable=False)
    _idle(r, thread_id)
    cid = await require(r.adapter_for(wf), "thread_state", ThreadStateAbility).update_state(
        wf, thread_id, r.resources(thread_id, wf), body.values, body.as_node, body.checkpoint_id
    )
    return {"checkpoint_id": cid}


@router.post("/threads/{thread_id}/fork")
async def fork_thread(
    thread_id: str,
    body: ForkRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
    r: Runner = Depends(runner),
    user: CurrentUser = Depends(current_user),
) -> Response:
    """Time travel: continue from a past checkpoint, optionally after editing it (M4-T7)."""
    t = await _thread(session, thread_id)
    wf, target_kw = await _thread_target(session, settings, t)
    target, checkpoint = thread_id, body.checkpoint_id
    if body.mode == "branch":
        _idle(r, thread_id)
    if body.mode == "copy":
        from agentcanvas.ids import new_id

        target = new_id("th")
        session.add(
            Thread(
                id=target,
                workflow_id=t.workflow_id,
                deployment_id=t.deployment_id,
                title=f"fork of {t.title or t.id}",
                created_by=user.id,
            )
        )
        await session.commit()
        checkpoint = await _travel(r, wf).copy_thread(
            wf, thread_id, target, r.resources(target, wf), body.checkpoint_id
        )
    if body.values is not None:
        checkpoint = await _travel(r, wf).update_state(
            wf, target, r.resources(target, wf), body.values, body.as_node, checkpoint
        )
    h = await r.start(
        wf,
        thread_id=target,
        input=None,
        checkpoint_id=checkpoint,
        user_id=user.id,
        context_from=thread_id,
        **target_kw,
    )
    return _respond(r, h, body.stream)


@router.delete("/threads/{thread_id}", status_code=204)
async def delete_thread(
    thread_id: str,
    session: AsyncSession = Depends(get_session),
    r: Runner = Depends(runner),
    settings: Settings = Depends(get_settings_dep),
) -> Response:
    """Delete a conversation. Its run, if one is going, is stopped first: it writes its last checkpoint and status as
    it stops, and deleting under it would leave that state behind. A run on another replica is asked to stop through
    its row and stops at that replica's next heartbeat; this waits for it, or answers 409 if it did not stop."""
    await _thread(session, thread_id)
    if r.handles.get(r.active_by_thread.get(thread_id, "")) is None:  # running elsewhere, or not at all
        await _stop_elsewhere(session, thread_id, settings.handover_interval_s + STOP_MARGIN_S)
    await r.forget_threads([thread_id])  # a run here is stopped first: it writes its last checkpoint as it stops
    await delete_thread_rows(session, thread_id)
    await session.commit()
    return Response(status_code=204)


STOP_MARGIN_S = 5.0  # beyond one heartbeat interval: the run's replica stops it and saves its outcome


async def _stop_elsewhere(session: AsyncSession, thread_id: str, timeout_s: float) -> None:
    """Ask the thread's run on another replica to stop (its heartbeat reads the mark) and wait until it has."""
    running = (Run.thread_id == thread_id, Run.status == "running")
    asked = await session.execute(
        update(Run)
        .where(*running)
        .values(stop_requested=True, stop_reason=func.coalesce(Run.stop_reason, CANCELLED_BY_USER))
    )
    await session.commit()
    if asked.rowcount == 0:  # type: ignore[attr-defined]
        return
    deadline = time.monotonic() + timeout_s
    while await session.scalar(select(func.count()).select_from(Run).where(*running)):
        if time.monotonic() >= deadline:
            raise Conflict("this conversation's run is still stopping on another server; try again in a few seconds")
        await asyncio.sleep(0.5)
        await session.commit()  # a fresh snapshot for the next look


# ------------------------------------------------------------------- inbox ----
def _intr(
    x: InterruptTask,
    workflow_id: str | None = None,
    workflow_name: str | None = None,
    ir: dict | None = None,
    project: Project | None = None,
) -> InterruptOut:
    node = next((n for n in (ir or {}).get("nodes", []) if n.get("id") == x.canvas_node_id), None)
    return InterruptOut(
        node_label=(node.get("label") or str(node.get("name", "")).replace("_", " ").capitalize() or None)
        if node
        else None,
        id=x.id,
        workflow_id=workflow_id,
        workflow_name=workflow_name,
        project_id=project.id if project else None,
        project_name=project.name if project else None,
        thread_id=x.thread_id,
        run_id=x.run_id,
        canvas_node_id=x.canvas_node_id,
        kind=x.kind,
        payload=x.payload,
        status=x.status,
        decision=x.decision,
        created_at=x.created_at,
        decided_at=x.decided_at,
    )


@router.get("/inbox", response_model=list[InterruptOut], tags=["inbox"])
async def inbox(
    status: Literal["pending", "decided", "expired"] = "pending",
    workflow_id: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> list[InterruptOut]:
    q = (
        select(InterruptTask, Thread.workflow_id, WorkflowRow.name, Project)
        .join(Thread, Thread.id == InterruptTask.thread_id)
        .join(WorkflowRow, WorkflowRow.id == Thread.workflow_id)
        .join(Project, Project.id == WorkflowRow.project_id)
        .where(InterruptTask.status == status, Project.org_id == user.org_id)
    )
    if workflow_id:
        q = q.where(Thread.workflow_id == workflow_id)
    # newest first: a long backlog must not hide what is waiting right now
    rows = (await session.execute(q.order_by(InterruptTask.created_at.desc()).limit(limit))).all()
    # each workflow's document once, for its steps' labels: not once per waiting item (F-2)
    wids = {wid for _, wid, _, _ in rows}
    irs = dict(
        (await session.execute(select(WorkflowRow.id, WorkflowRow.draft_ir).where(WorkflowRow.id.in_(wids)))).all()
    )
    return [_intr(x, wid, name, irs.get(wid), project) for x, wid, name, project in rows]


@router.post("/inbox/{interrupt_id}/decision", tags=["inbox"])
async def decide(
    interrupt_id: str,
    body: Decision,
    request: Request,
    stream: bool = False,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
    r: Runner = Depends(runner),
    user: CurrentUser = Depends(current_user),
) -> Response:
    task = await session.get(InterruptTask, interrupt_id)
    if task is None:
        raise NotFound(f"inbox item '{interrupt_id}' not found")
    note(request, kind=task.kind, payload_sha256=payload_hash(body.value))
    if task.status != "pending":
        raise Conflict(f"inbox item '{interrupt_id}' is already {task.status}")
    t = await _thread(session, task.thread_id)
    wf, target_kw = await _thread_target(session, settings, t)
    h = await r.start(
        wf,
        thread_id=t.id,
        resume=ResumeSpec(body.value, interrupt_id),
        user_id=user.id,
        decided_by=user.id,
        **target_kw,
    )
    return _respond(r, h, stream)
