"""AgentCanvas Runner API (MVP). Run with:  uvicorn agentcanvas.api.app:app --reload

Endpoints (JSON; run endpoints stream Server-Sent Events):
  GET  /healthz                         liveness probe
  POST /workflows                       create or replace a workflow (IR JSON)
  GET  /workflows/{wf_id}               -> IR JSON
  POST /workflows/{wf_id}/validate      -> diagnostics
  GET  /workflows/{wf_id}/code          -> generated Python
  POST /workflows/{wf_id}/runs/stream   start a run on a thread (SSE)
  POST /threads/{thread_id}/resume      answer an interrupt and continue (SSE)
  GET  /threads/{thread_id}/state       current state + pending interrupts
  GET  /threads/{thread_id}/history     checkpoints, newest first
  POST /threads/{thread_id}/state       edit state ("time travel" edits)
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse, StreamingResponse
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import BaseModel

from agentcanvas.compiler.codegen import CompileError, generate
from agentcanvas.ir.models import Workflow
from agentcanvas.ir.validate import has_errors, validate
from agentcanvas.runtime.events import node_index, translate
from agentcanvas.runtime.interpreter import build_graph
from agentcanvas.runtime.nodes import Resources

_SHARED_RESOURCES = Resources()


class AppState:
    """Process-wide state. MVP keeps workflows in memory; M2 moves them to Postgres."""

    def __init__(self) -> None:
        self.workflows: dict[str, Workflow] = {}
        self.thread_workflow: dict[str, str] = {}  # which workflow owns a thread
        self.checkpointer: Any = None
        # Returns the Resources (models, tools) used for a thread. Real models are
        # stateless, so the default shares one Resources for every thread. Tests and the
        # no-API-key dev server install a per-thread factory, because scripted fake
        # models are stateful (a resume must continue the same script).
        self.resources_factory: Callable[[str], Resources] = lambda thread_id: _SHARED_RESOURCES


S = AppState()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = os.getenv("DATABASE_URL")
    if db:  # production-like: durable checkpoints in Postgres
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        async with AsyncPostgresSaver.from_conn_string(db) as saver:
            await saver.setup()
            S.checkpointer = saver
            yield
    else:  # dev: in-memory (lost on restart)
        S.checkpointer = InMemorySaver()
        yield


app = FastAPI(title="AgentCanvas Runner", lifespan=lifespan)


class RunRequest(BaseModel):
    input: dict[str, Any] = {}
    thread_id: str | None = None


class ResumeRequest(BaseModel):
    value: Any


class StateEdit(BaseModel):
    values: dict[str, Any]
    as_node: str | None = None


def _cfg(thread_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": thread_id}}


def _checkpoint_id(config: RunnableConfig) -> str | None:
    return config.get("configurable", {}).get("checkpoint_id")


def _wf(wf_id: str) -> Workflow:
    wf = S.workflows.get(wf_id)
    if not wf:
        raise HTTPException(404, detail=f"workflow '{wf_id}' not found")
    return wf


def _graph(wf: Workflow, thread_id: str):
    return build_graph(wf, S.resources_factory(thread_id), checkpointer=S.checkpointer)


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event)}\n\n"


async def _stream(wf: Workflow, graph_input: Any, thread_id: str) -> AsyncIterator[str]:
    graph = _graph(wf, thread_id)
    cfg = _cfg(thread_id)
    names = node_index(wf)
    yield _sse({"type": "RUN_STARTED", "threadId": thread_id, "workflowId": wf.id})
    try:
        async for part in graph.astream(
            graph_input, cfg, stream_mode=["tasks", "updates", "messages", "custom"], subgraphs=True, version="v2"
        ):
            for ev in translate(part, names):
                yield _sse(ev)
        snap = await graph.aget_state(cfg)
        outcome = "interrupt" if snap.interrupts else "success"
        yield _sse({"type": "RUN_FINISHED", "threadId": thread_id, "outcome": outcome})
    except Exception as exc:  # noqa: BLE001 - any failure must reach the UI as RUN_ERROR
        yield _sse({"type": "RUN_ERROR", "message": f"{type(exc).__name__}: {exc}"})


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness/readiness probe used by Docker and Kubernetes."""
    return {"status": "ok"}


@app.post("/workflows")
def put_workflow(wf: Workflow):
    S.workflows[wf.id] = wf
    return {"id": wf.id, "diagnostics": [d.model_dump() for d in validate(wf)]}


@app.get("/workflows/{wf_id}")
def get_workflow(wf_id: str) -> Workflow:
    return _wf(wf_id)


@app.post("/workflows/{wf_id}/validate")
def validate_workflow(wf_id: str):
    return [d.model_dump() for d in validate(_wf(wf_id))]


@app.get("/workflows/{wf_id}/code", response_class=PlainTextResponse)
def get_code(wf_id: str):
    try:
        return generate(_wf(wf_id))
    except CompileError as exc:
        raise HTTPException(422, detail=str(exc)) from exc


@app.post("/workflows/{wf_id}/runs/stream")
async def run_stream(wf_id: str, req: RunRequest):
    wf = _wf(wf_id)
    diags = validate(wf)
    if has_errors(diags):
        raise HTTPException(422, detail=[d.model_dump() for d in diags])
    thread_id = req.thread_id or f"th_{uuid.uuid4().hex[:12]}"
    S.thread_workflow[thread_id] = wf_id
    return StreamingResponse(_stream(wf, req.input, thread_id), media_type="text/event-stream")


def _thread_wf(thread_id: str) -> Workflow:
    wf_id = S.thread_workflow.get(thread_id)
    if not wf_id:
        raise HTTPException(404, detail=f"thread '{thread_id}' not found")
    return _wf(wf_id)


@app.post("/threads/{thread_id}/resume")
async def resume(thread_id: str, req: ResumeRequest):
    wf = _thread_wf(thread_id)
    return StreamingResponse(_stream(wf, Command(resume=req.value), thread_id), media_type="text/event-stream")


@app.get("/threads/{thread_id}/state")
async def get_state(thread_id: str):
    snap = await _graph(_thread_wf(thread_id), thread_id).aget_state(_cfg(thread_id))
    from agentcanvas.runtime.events import _jsonable

    return {
        "values": _jsonable(snap.values),
        "next": list(snap.next),
        "interrupts": [{"id": i.id, "payload": _jsonable(i.value)} for i in snap.interrupts],
        "checkpoint_id": _checkpoint_id(snap.config),
    }


@app.get("/threads/{thread_id}/history")
async def get_history(thread_id: str):
    graph = _graph(_thread_wf(thread_id), thread_id)
    out = []
    async for snap in graph.aget_state_history(_cfg(thread_id)):
        out.append(
            {
                "checkpoint_id": _checkpoint_id(snap.config),
                "step": (snap.metadata or {}).get("step"),
                "next": list(snap.next),
            }
        )
    return out


@app.post("/threads/{thread_id}/state")
async def edit_state(thread_id: str, edit: StateEdit):
    graph = _graph(_thread_wf(thread_id), thread_id)
    cfg = await graph.aupdate_state(_cfg(thread_id), edit.values, as_node=edit.as_node)
    return {"checkpoint_id": _checkpoint_id(cfg)}
