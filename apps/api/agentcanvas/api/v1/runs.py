"""Runs, threads and the Inbox (tickets M1-T6, M4-T4…T8, M4-T10 — backend).

Run endpoints stream canonical events as Server-Sent Events (ADR 0007). Streams are views of a
background run: disconnecting does not stop it, `GET /v1/runs/{id}/events?after=<seq>` re-attaches.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any, Literal, cast

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.adapters.spi import ThreadStateAbility, require, supports
from agentcanvas.api.audit import note, payload_hash
from agentcanvas.api.deps import RANK, CurrentUser, current_user, get_session, get_settings_dep
from agentcanvas.db.models import Deployment, InterruptTask, Project, Run, Thread, WorkflowRow, WorkflowVersion
from agentcanvas.db.repositories import delete_thread_rows
from agentcanvas.errors import Conflict, Forbidden, InvalidInput, NotFound, UpstreamError, WorkflowHasErrors
from agentcanvas.ir.models import Workflow
from agentcanvas.observability import counted_stream
from agentcanvas.runtime.runner import RunHandle, Runner
from agentcanvas.runtime.tracing import web_url
from agentcanvas.runtime.types import ResumeSpec, ThreadState, is_tool_recording
from agentcanvas.services.explain import explain
from agentcanvas.services.redeploy import thread_version
from agentcanvas.services.secrets import hide_secrets
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


class ThreadPage(BaseModel):
    items: list[ThreadOut]
    next_cursor: str | None


class InterruptOut(BaseModel):
    id: str
    workflow_id: str | None = None
    workflow_name: str | None = None
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
        t = await session.get(Thread, body.thread_id)
        if t is not None and t.workflow_id != wf_id:
            raise InvalidInput(f"thread '{body.thread_id}' belongs to another workflow")
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
async def cancel_run(run_id: str, r: Runner = Depends(runner)) -> dict[str, Any]:
    return {"cancelled": await r.cancel(run_id)}


# ----------------------------------------------------------------- threads ----
@router.get("/threads", response_model=ThreadPage)
async def list_threads(
    workflow_id: str | None = None,
    project_id: str | None = Query(None, description="only this project's conversations"),
    status: str | None = None,
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
    if cursor:
        q = q.where(Thread.id < cursor)
    rows = list((await session.execute(q.order_by(Thread.id.desc()).limit(limit + 1))).all())
    page = rows[:limit]
    runs: dict[str, list[Run]] = {}
    if page:
        found = await session.scalars(
            select(Run).where(Run.thread_id.in_([t.id for t, _ in page])).order_by(Run.started_at)
        )
        for r in found:
            runs.setdefault(r.thread_id, []).append(r)
    items = []
    for t, wf_name in page:
        mine = runs.get(t.id, [])
        last = mine[-1] if mine else None
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
                runs=len(mine),
                tokens=sum(r.tokens_in + r.tokens_out for r in mine),
                cost_usd=float(sum(r.cost_usd for r in mine)),
                last_started_at=last.started_at if last else None,
                last_ended_at=last.ended_at if last else None,
                last_error=last.error if last else None,
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
    rows = (await session.scalars(select(Run).where(Run.thread_id == thread_id).order_by(Run.id))).all()
    return [_run_out(x) for x in rows]


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
    thread_id: str, session: AsyncSession = Depends(get_session), r: Runner = Depends(runner)
) -> Response:
    await _thread(session, thread_id)
    h = r.handles.get(r.active_by_thread.get(thread_id, ""))
    if h is not None and await r.cancel(h.run_id):
        try:  # the run writes its last checkpoint and status as it stops: let it, then delete
            await asyncio.wait_for(h.done.wait(), timeout=10)
        except TimeoutError:
            pass
    await r.forget_thread(thread_id)
    await delete_thread_rows(session, thread_id)
    await session.commit()
    return Response(status_code=204)


# ------------------------------------------------------------------- inbox ----
def _intr(
    x: InterruptTask, workflow_id: str | None = None, workflow_name: str | None = None, ir: dict | None = None
) -> InterruptOut:
    node = next((n for n in (ir or {}).get("nodes", []) if n.get("id") == x.canvas_node_id), None)
    return InterruptOut(
        node_label=(node.get("label") or node.get("name")) if node else None,
        id=x.id,
        workflow_id=workflow_id,
        workflow_name=workflow_name,
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
        select(InterruptTask, Thread.workflow_id, WorkflowRow.name, WorkflowRow.draft_ir)
        .join(Thread, Thread.id == InterruptTask.thread_id)
        .join(WorkflowRow, WorkflowRow.id == Thread.workflow_id)
        .join(Project, Project.id == WorkflowRow.project_id)
        .where(InterruptTask.status == status, Project.org_id == user.org_id)
    )
    if workflow_id:
        q = q.where(Thread.workflow_id == workflow_id)
    # newest first: a long backlog must not hide what is waiting right now
    rows = (await session.execute(q.order_by(InterruptTask.created_at.desc()).limit(limit))).all()
    return [_intr(x, wid, name, ir) for x, wid, name, ir in rows]


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
