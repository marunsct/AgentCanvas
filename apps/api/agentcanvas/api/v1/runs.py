"""Runs, threads and the Inbox (tickets M1-T6, M4-T4…T8, M4-T10 — backend).

Run endpoints stream canonical events as Server-Sent Events (ADR 0007). Streams are views of a
background run: disconnecting does not stop it, `GET /v1/runs/{id}/events?after=<seq>` re-attaches.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.deps import CurrentUser, current_user, get_session, get_settings_dep
from agentcanvas.db.models import InterruptTask, Run, Thread, WorkflowRow
from agentcanvas.errors import InvalidInput, NotFound, WorkflowHasErrors
from agentcanvas.ir.models import Workflow
from agentcanvas.runtime.runner import RunHandle, Runner
from agentcanvas.runtime.types import ResumeSpec
from agentcanvas.services.explain import explain
from agentcanvas.services.workflows import WorkflowService
from agentcanvas.settings import Settings
from agentcanvas.validation import has_errors

router = APIRouter(tags=["runs"])
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def runner(request: Request) -> Runner:
    return request.app.state.runner


# ------------------------------------------------------------------ models ----
class Breakpoints(BaseModel):
    before: list[str] = Field(default_factory=list)  # canvas node ids
    after: list[str] = Field(default_factory=list)


class RunRequest(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)
    thread_id: str | None = None
    mode: Literal["normal", "debug"] = "normal"
    breakpoints: Breakpoints | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    durability: Literal["exit", "async", "sync"] | None = None
    stream: bool = True  # false: return 202 {run_id} and let the client attach later


class ResumeRequest(BaseModel):
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
    )


class ThreadOut(BaseModel):
    id: str
    workflow_id: str
    title: str | None
    status: str
    created_at: datetime
    updated_at: datetime


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

    return gen()


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


async def _runnable_workflow(svc: WorkflowService, wf_id: str) -> Workflow:
    view = await svc.get(wf_id)
    if has_errors(view.diagnostics):
        errors = [d.model_dump() for d in view.diagnostics if d.severity == "error"]
        raise WorkflowHasErrors(f"workflow has {len(errors)} error(s); fix them before running", errors=errors)
    return view.workflow


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
    )
    return _respond(r, h, body.stream)


@router.get("/runs/{run_id}", response_model=RunOut)
async def get_run(run_id: str, session: AsyncSession = Depends(get_session)) -> RunOut:
    run = await session.get(Run, run_id)
    if run is None:
        raise NotFound(f"run '{run_id}' not found")
    return _run_out(run)


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str, after: int = Query(0, ge=0), r: Runner = Depends(runner)) -> Response:
    return StreamingResponse(_sse(r.subscribe(run_id, after)), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/runs/{run_id}/cancel", status_code=202)
async def cancel_run(run_id: str, r: Runner = Depends(runner)) -> dict[str, Any]:
    return {"cancelled": await r.cancel(run_id)}


# ----------------------------------------------------------------- threads ----
@router.get("/threads", response_model=ThreadPage)
async def list_threads(
    workflow_id: str | None = None,
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> ThreadPage:
    q = select(Thread)
    if workflow_id:
        q = q.where(Thread.workflow_id == workflow_id)
    if status:
        q = q.where(Thread.status == status)
    if cursor:
        q = q.where(Thread.id < cursor)
    rows = list((await session.scalars(q.order_by(Thread.id.desc()).limit(limit + 1))).all())
    items = [
        ThreadOut(
            id=t.id,
            workflow_id=t.workflow_id,
            title=t.title,
            status=t.status,
            created_at=t.created_at,
            updated_at=t.updated_at,
        )
        for t in rows[:limit]
    ]
    return ThreadPage(items=items, next_cursor=items[-1].id if len(rows) > limit else None)


@router.get("/threads/{thread_id}")
async def get_thread(
    thread_id: str,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
    r: Runner = Depends(runner),
) -> dict[str, Any]:
    t = await _thread(session, thread_id)
    wf = (await _svc(session, settings).get(t.workflow_id)).workflow
    state = await r.adapter.get_state(wf, thread_id, r.resources(thread_id, wf))
    return {
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
    }


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
    wf = (await _svc(session, settings).get(t.workflow_id)).workflow
    items = await r.adapter.history(wf, thread_id, r.resources(thread_id, wf), limit, include_values)
    return [{k: v for k, v in c.__dict__.items() if include_values or k != "values"} for c in items]


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
    wf = (await _svc(session, settings).get(t.workflow_id)).workflow
    history = await r.adapter.history(wf, thread_id, r.resources(thread_id, wf), limit, include_values=True)
    return explain(wf, history)


@router.get("/threads/{thread_id}/runs", response_model=list[RunOut])
async def thread_runs(thread_id: str, session: AsyncSession = Depends(get_session)) -> list[RunOut]:
    await _thread(session, thread_id)
    rows = (await session.scalars(select(Run).where(Run.thread_id == thread_id).order_by(Run.id))).all()
    return [_run_out(x) for x in rows]


@router.post("/threads/{thread_id}/resume")
async def resume_thread(
    thread_id: str,
    body: ResumeRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
    r: Runner = Depends(runner),
    user: CurrentUser = Depends(current_user),
) -> Response:
    t = await _thread(session, thread_id)
    wf = await _runnable_workflow(_svc(session, settings), t.workflow_id)
    h = await r.start(wf, thread_id=thread_id, resume=ResumeSpec(body.value, body.interrupt_id), user_id=user.id)
    return _respond(r, h, body.stream)


@router.post("/threads/{thread_id}/continue")
async def continue_thread(
    thread_id: str,
    stream: bool = True,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
    r: Runner = Depends(runner),
    user: CurrentUser = Depends(current_user),
) -> Response:
    """Continue after a breakpoint (input None)."""
    t = await _thread(session, thread_id)
    wf = await _runnable_workflow(_svc(session, settings), t.workflow_id)
    h = await r.start(wf, thread_id=thread_id, input=None, mode="debug", user_id=user.id)
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
    wf = (await _svc(session, settings).get(t.workflow_id)).workflow
    cid = await r.adapter.update_state(
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
    wf = await _runnable_workflow(_svc(session, settings), t.workflow_id)
    target, checkpoint = thread_id, body.checkpoint_id
    if body.mode == "copy":
        from agentcanvas.ids import new_id

        target = new_id("th")
        session.add(
            Thread(id=target, workflow_id=t.workflow_id, title=f"fork of {t.title or t.id}", created_by=user.id)
        )
        await session.commit()
        checkpoint = await r.adapter.copy_thread(wf, thread_id, target, r.resources(target, wf), body.checkpoint_id)
    if body.values is not None:
        checkpoint = await r.adapter.update_state(
            wf, target, r.resources(target, wf), body.values, body.as_node, checkpoint
        )
    h = await r.start(
        wf, thread_id=target, input=None, checkpoint_id=checkpoint, user_id=user.id, context_from=thread_id
    )
    return _respond(r, h, body.stream)


@router.delete("/threads/{thread_id}", status_code=204)
async def delete_thread(
    thread_id: str, session: AsyncSession = Depends(get_session), r: Runner = Depends(runner)
) -> Response:
    await _thread(session, thread_id)
    if thread_id in r.active_by_thread:
        await r.cancel(r.active_by_thread[thread_id])
    await r.adapter.delete_thread(thread_id)
    await session.execute(delete(Thread).where(Thread.id == thread_id))
    await session.commit()
    return Response(status_code=204)


# ------------------------------------------------------------------- inbox ----
def _intr(x: InterruptTask, workflow_id: str | None = None, workflow_name: str | None = None) -> InterruptOut:
    return InterruptOut(
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
) -> list[InterruptOut]:
    q = (
        select(InterruptTask, Thread.workflow_id, WorkflowRow.name)
        .join(Thread, Thread.id == InterruptTask.thread_id)
        .join(WorkflowRow, WorkflowRow.id == Thread.workflow_id)
        .where(InterruptTask.status == status)
    )
    if workflow_id:
        q = q.where(Thread.workflow_id == workflow_id)
    rows = (await session.execute(q.order_by(InterruptTask.created_at).limit(limit))).all()
    return [_intr(x, wid, name) for x, wid, name in rows]


@router.post("/inbox/{interrupt_id}/decision", tags=["inbox"])
async def decide(
    interrupt_id: str,
    body: Decision,
    stream: bool = False,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
    r: Runner = Depends(runner),
    user: CurrentUser = Depends(current_user),
) -> Response:
    task = await session.get(InterruptTask, interrupt_id)
    if task is None:
        raise NotFound(f"inbox item '{interrupt_id}' not found")
    if task.status != "pending":
        raise InvalidInput(f"inbox item '{interrupt_id}' is already {task.status}")
    t = await _thread(session, task.thread_id)
    wf = await _runnable_workflow(_svc(session, settings), t.workflow_id)
    h = await r.start(
        wf, thread_id=t.id, resume=ResumeSpec(body.value, interrupt_id), user_id=user.id, decided_by=user.id
    )
    return _respond(r, h, stream)
