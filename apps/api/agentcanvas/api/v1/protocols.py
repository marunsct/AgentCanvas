"""Deployments as agents other tools can call (S15): an MCP server and an A2A agent per deployment.

Both speak for the deployment's pinned version and take the same `Authorization: Bearer <deployment key>` as the
deployment API, with the same rate limits:

- **MCP** (`POST /v1/deployments/{id}/mcp`, Streamable HTTP, stateless JSON responses): one tool that runs the
  workflow, plus `answer` when it can pause for a person. Claude Desktop, Cursor, ChatGPT and agent frameworks
  connect to it like to any remote MCP server.
- **A2A** (`POST /v1/deployments/{id}/a2a`, JSON-RPC; card at `.../a2a/.well-known/agent-card.json`):
  `message/send` runs the workflow (a pause becomes `input-required`, answered by sending to the same task),
  `tasks/get` reads a task. A2A v1.0 method names (`SendMessage`, `GetTask`) work too.

Runs started here are ordinary deployment runs: they show in Runs, count for online evaluators and keep their
conversation (`thread_id` / A2A `contextId`).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.deps import get_session
from agentcanvas.api.v1.deployments import ApiResult, ApiRunIn, _caller, _continue_on, _result, _start
from agentcanvas.api.v1.runs import _thread
from agentcanvas.db.models import Deployment, Run, Thread, WorkflowVersion
from agentcanvas.errors import Conflict, DomainError, InvalidInput, NotFound
from agentcanvas.ir.contract import Contract, contract
from agentcanvas.ir.models import Workflow
from agentcanvas.runtime.types import ResumeSpec

router = APIRouter(tags=["agent protocols"])

MCP_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")  # newest first
A2A_VERSION = "0.3.0"
TEXT_FIELDS = ("message", "question", "query", "input", "text", "request", "prompt", "ticket")


class RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code, self.message, self.data = code, message, data


def _rpc_error(id_: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": id_, "error": err}


async def _rpc(request: Request) -> dict[str, Any]:
    try:
        msg = json.loads(await request.body())
    except ValueError:
        raise RpcError(-32700, "parse error: the body is not JSON") from None
    if isinstance(msg, list):
        raise RpcError(-32600, "batches are not supported; send one message per request")
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        raise RpcError(-32600, "invalid request: expected a JSON-RPC 2.0 message with a method")
    return msg


def tool_name(wf: Workflow) -> str:
    """The workflow's name as an MCP tool name (letters, digits, _ and -)."""
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", wf.name.strip().lower()).strip("_")[:60]
    return slug or "run_workflow"


def _describe_pause(interrupts: list[dict[str, Any]]) -> str:
    lines = []
    for i in interrupts:
        payload = i.get("payload")
        what = (
            (payload.get("question") or payload.get("message") or payload.get("title"))
            if isinstance(payload, dict)
            else payload
        )
        kind = {"approval": "an approval", "tool_approval": "approval of a tool call", "input": "an answer"}.get(
            i.get("kind") or "", "an answer"
        )
        lines.append(f"- it needs {kind}" + (f": {what}" if what else "") + f" (interrupt_id {i.get('id')})")
    return "\n".join(lines)


def answer_value(interrupt: dict[str, Any] | None, approve: bool | None, text: str | None, value: Any) -> Any:
    """What to resume a pause with, from the simple fields a calling agent fills in."""
    if value is not None:
        return value
    kind = (interrupt or {}).get("kind")
    if kind == "approval":
        out: dict[str, Any] = {"approved": approve is not False}
        if text:
            out["comment"] = text
        return out
    if kind == "tool_approval":
        requests = ((interrupt or {}).get("payload") or {}).get("action_requests") or [{}]
        decision = {"type": "approve"} if approve is not False else {"type": "reject", "message": text or "rejected"}
        return {"decisions": [decision for _ in requests]}
    return text if text is not None else ("yes" if approve is not False else "no")


async def _pending(request: Request, thread_id: str, interrupt_id: str | None) -> dict[str, Any] | None:
    """The pending interrupt of a thread's latest run in this process (to shape a simple answer)."""
    for h in reversed(list(request.app.state.runner.handles.values())):
        if h.thread_id != thread_id:
            continue
        for ev in reversed(h.events):
            if ev["type"] == "RUN_FINISHED":
                found = ev.get("interrupts") or []
                return next((i for i in found if not interrupt_id or i.get("id") == interrupt_id), None)
        return None
    return None


async def _resume(
    request: Request, session: AsyncSession, dep: Deployment, wf: Workflow, thread_id: str, value: Any, iid: str | None
) -> ApiResult:
    t = await _thread(session, thread_id)
    if t.deployment_id != dep.id:
        raise InvalidInput(f"thread '{thread_id}' was not started by this deployment")
    wf, version_id = await _continue_on(session, dep, wf, thread_id)
    h = await request.app.state.runner.start(
        wf, thread_id=thread_id, resume=ResumeSpec(value, iid), deployment_id=dep.id, version_id=version_id
    )
    await session.close()
    return await _result(h)


# ---------------------------------------------------------------------------------------------- MCP ----
def _mcp_tools(wf: Workflow, c: Contract) -> list[dict[str, Any]]:
    props: dict[str, Any] = {k: dict(v) for k, v in c.input.items()}
    if c.context:
        props["context"] = {"type": "object", "properties": c.context, "description": "run context fields"}
    props["thread_id"] = {"type": "string", "description": "continue a conversation: the thread_id of an earlier run"}
    required = ["context"] if c.required_context else []
    tools = [
        {
            "name": tool_name(wf),
            "title": wf.name,
            "description": (wf.description or f"Runs the {wf.name} workflow.")
            + (f" Returns: {', '.join(c.output)}." if c.output else "")
            + (" It can pause for a person: then call `answer`." if c.pauses else ""),
            "inputSchema": {"type": "object", "properties": props, "required": required},
            "annotations": {"title": wf.name, "readOnlyHint": False, "openWorldHint": True},
        }
    ]
    if c.pauses:
        tools.append(
            {
                "name": "answer",
                "title": "Answer a paused run",
                "description": "Continue a run that paused for an approval or a question. Set approve for an "
                "approval, text for a question (or a reason when rejecting); value sends the raw resume value.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "thread_id": {"type": "string"},
                        "interrupt_id": {"type": "string"},
                        "approve": {"type": "boolean"},
                        "text": {"type": "string"},
                        "value": {"description": "raw resume value (advanced)"},
                    },
                    "required": ["thread_id"],
                },
                "annotations": {"title": "Answer a paused run", "readOnlyHint": False, "openWorldHint": True},
            }
        )
    return tools


def _tool_result(res: ApiResult) -> dict[str, Any]:
    data = res.model_dump(exclude_none=True)
    if res.status == "interrupted":
        text = f"The run paused (thread_id {res.thread_id}):\n{_describe_pause(res.interrupts)}\nCall `answer`."
    elif res.status == "success":
        text = json.dumps(res.output, ensure_ascii=False, indent=1)
    elif res.status == "running":
        text = f"Still running after the wait limit (run {res.run_id}); see Runs in AgentCanvas."
    else:
        text = f"The run ended with status {res.status}: {res.error or 'no details'}"
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": data,
        "isError": res.status in ("error", "cancelled"),
    }


async def _mcp_call(
    request: Request, session: AsyncSession, dep: Deployment, wf: Workflow, params: dict[str, Any], auth: str | None
) -> dict[str, Any]:
    name, args = params.get("name"), params.get("arguments") or {}
    if not isinstance(args, dict):
        raise RpcError(-32602, "arguments must be an object")
    c = contract(wf)
    try:
        if name == tool_name(wf):
            fields = {k: v for k, v in args.items() if k not in ("thread_id", "context")}
            body = ApiRunIn(
                input=fields, thread_id=args.get("thread_id"), context=args.get("context") or {}, user_id=None
            )
            h = await _start(request, session, dep.id, body, auth)
            await session.close()
            return _tool_result(await _result(h))
        if name == "answer" and c.pauses:
            thread_id, iid = str(args.get("thread_id") or ""), args.get("interrupt_id")
            pending = await _pending(request, thread_id, iid)
            value = answer_value(pending, args.get("approve"), args.get("text"), args.get("value"))
            return _tool_result(await _resume(request, session, dep, wf, thread_id, value, iid))
    except (InvalidInput, NotFound, Conflict) as exc:  # the caller's mistake: tell the model, it can correct it
        return {"content": [{"type": "text", "text": exc.detail}], "isError": True}
    raise RpcError(-32602, f"unknown tool '{name}'")


@router.post("/deployments/{dep_id}/mcp")
async def mcp_endpoint(
    dep_id: str,
    request: Request,
    authorization: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """The deployment as a remote MCP server (Streamable HTTP; every response is a single JSON message)."""
    dep, wf = await _caller(request, session, dep_id, authorization)
    try:
        msg = await _rpc(request)
    except RpcError as e:
        return JSONResponse(_rpc_error(None, e.code, e.message), status_code=400)
    method, params, id_ = msg["method"], msg.get("params") or {}, msg.get("id")
    if "id" not in msg:  # a notification (initialized, cancelled, ...): nothing to answer
        return Response(status_code=202)
    try:
        if method == "initialize":
            asked = params.get("protocolVersion")
            version = await session.get(WorkflowVersion, dep.version_id)
            result: dict[str, Any] = {
                "protocolVersion": asked if asked in MCP_VERSIONS else MCP_VERSIONS[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": f"agentcanvas-{tool_name(wf)}", "version": version.semver if version else "0"},
                "instructions": wf.description or f"Tools of the {wf.name} workflow, published with AgentCanvas.",
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": _mcp_tools(wf, contract(wf))}
        elif method == "tools/call":
            result = await _mcp_call(request, session, dep, wf, params, authorization)
        elif method in ("resources/list", "prompts/list"):
            result = {method.split("/")[0]: []}
        else:
            raise RpcError(-32601, f"method '{method}' is not supported")
    except RpcError as e:
        return JSONResponse(_rpc_error(id_, e.code, e.message, e.data))
    return JSONResponse({"jsonrpc": "2.0", "id": id_, "result": result})


@router.get("/deployments/{dep_id}/mcp")
async def mcp_no_stream(dep_id: str) -> Response:
    """No server-initiated stream: every answer comes back on its POST."""
    return Response(status_code=405, headers={"Allow": "POST"})


# ---------------------------------------------------------------------------------------------- A2A ----
async def _deployment(session: AsyncSession, dep_id: str) -> tuple[Deployment, Workflow, str]:
    dep = await session.get(Deployment, dep_id)
    version = await session.get(WorkflowVersion, dep.version_id) if dep and dep.active else None
    if dep is None or version is None:
        raise NotFound(f"deployment '{dep_id}' not found")
    return dep, Workflow.model_validate(version.ir), version.semver


def agent_card(wf: Workflow, semver: str, url: str) -> dict[str, Any]:
    c = contract(wf)
    fields = ", ".join(c.input) or "none"
    return {
        "protocolVersion": A2A_VERSION,
        "name": wf.name,
        "description": wf.description or f"The {wf.name} workflow, published with AgentCanvas.",
        "url": url,
        "preferredTransport": "JSONRPC",
        "version": semver,
        "provider": {"organization": "AgentCanvas", "url": "https://github.com/marunsct/AgentCanvas"},
        "capabilities": {"streaming": False, "pushNotifications": False},
        "defaultInputModes": ["text/plain", "application/json"],
        "defaultOutputModes": ["application/json", "text/plain"],
        "skills": [
            {
                "id": tool_name(wf),
                "name": wf.name,
                "description": (wf.description or f"Runs {wf.name}.")
                + f" Input fields: {fields}."
                + (f" Output fields: {', '.join(c.output)}." if c.output else ""),
                "tags": ["agentcanvas", "workflow"],
                "inputModes": ["text/plain", "application/json"],
                "outputModes": ["application/json"],
            }
        ],
        "securitySchemes": {"deploymentKey": {"type": "http", "scheme": "bearer"}},
        "security": [{"deploymentKey": []}],
    }


@router.get("/deployments/{dep_id}/a2a/.well-known/agent-card.json")
@router.get("/deployments/{dep_id}/a2a/.well-known/agent.json")
async def a2a_card(dep_id: str, request: Request, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """The deployment's A2A agent card. Public, as A2A clients read it before they authenticate: it names the
    workflow and its input fields; running it needs the key."""
    _, wf, semver = await _deployment(session, dep_id)
    return agent_card(wf, semver, str(request.url_for("a2a_endpoint", dep_id=dep_id)))


def _input_from(message: dict[str, Any], c: Contract) -> tuple[dict[str, Any], str | None]:
    """The run input from an A2A message: data parts are fields; text goes to the workflow's text field."""
    fields: dict[str, Any] = {}
    texts: list[str] = []
    for part in message.get("parts") or []:
        kind = part.get("kind") or part.get("type") or ("data" if "data" in part else "text")
        if kind == "data" and isinstance(part.get("data"), dict):
            fields.update(part["data"])
        elif kind == "text" and isinstance(part.get("text"), str):
            texts.append(part["text"])
    text = "\n".join(texts) if texts else None
    if text is not None:
        strings = [k for k, v in c.input.items() if v.get("type") in (None, "string") and k not in fields]
        only_in = [k for k in strings if k not in c.output]  # fields that are both in and out are rarely the question
        target = next((k for k in TEXT_FIELDS if k in strings), None) or next(
            (group[0] for group in (strings, only_in) if len(group) == 1), None
        )
        if target:
            fields[target] = text
        elif not fields:
            raise RpcError(-32602, f"send the input as a data part with the fields: {', '.join(c.input) or 'none'}")
    return fields, text


def _task(res: ApiResult, task_id: str) -> dict[str, Any]:
    state = {"success": "completed", "interrupted": "input-required", "running": "working",
             "cancelled": "canceled"}.get(res.status, "failed")  # fmt: skip
    status: dict[str, Any] = {"state": state, "timestamp": datetime.now(UTC).isoformat()}
    if res.status == "interrupted":
        status["message"] = _agent_message(
            task_id, res.thread_id, f"Paused:\n{_describe_pause(res.interrupts)}\nReply to this task to continue.",
            {"interrupts": res.interrupts},
        )  # fmt: skip
    elif state == "failed":
        status["message"] = _agent_message(task_id, res.thread_id, res.error or "the run failed", None)
    task: dict[str, Any] = {"kind": "task", "id": task_id, "contextId": res.thread_id, "status": status,
                            "metadata": {"run_id": res.run_id}}  # fmt: skip
    if res.status == "success":
        task["artifacts"] = [
            {"artifactId": "output", "name": "output", "parts": [{"kind": "data", "data": res.output or {}}]}
        ]
    return task


def _agent_message(task_id: str, context_id: str, text: str, data: dict[str, Any] | None) -> dict[str, Any]:
    parts: list[dict[str, Any]] = [{"kind": "text", "text": text}]
    if data is not None:
        parts.append({"kind": "data", "data": data})
    return {"kind": "message", "role": "agent", "messageId": f"{task_id}-status", "taskId": task_id,
            "contextId": context_id, "parts": parts}  # fmt: skip


async def _task_thread(session: AsyncSession, dep: Deployment, task_id: str) -> str:
    """The conversation of a task (a task is named after the run that started it)."""
    q = select(Run.thread_id).join(Thread, Thread.id == Run.thread_id)
    thread_id = await session.scalar(q.where(Run.id == task_id, Thread.deployment_id == dep.id))
    if thread_id is None:
        raise RpcError(-32001, f"task '{task_id}' not found")
    return thread_id


async def _a2a_send(
    request: Request, session: AsyncSession, dep: Deployment, wf: Workflow, params: dict[str, Any], auth: str | None
) -> dict[str, Any]:
    message = params.get("message")
    if not isinstance(message, dict):
        raise RpcError(-32602, "params.message is required")
    c = contract(wf)
    task_id = message.get("taskId")
    if task_id:  # the answer to a paused task
        thread_id = await _task_thread(session, dep, str(task_id))
        fields, text = _input_from(message, c) if c.input else ({}, None)
        if text is None:
            texts = [p.get("text") for p in message.get("parts") or [] if isinstance(p.get("text"), str)]
            text = "\n".join(texts) or None
        pending = await _pending(request, thread_id, None)
        data = next((p["data"] for p in message.get("parts") or [] if isinstance(p.get("data"), dict)), None)
        approve = data.get("approve") if isinstance(data, dict) else None
        raw = data.get("value") if isinstance(data, dict) else None
        if approve is None and text is not None and (pending or {}).get("kind") in ("approval", "tool_approval"):
            approve = text.strip().lower() in ("yes", "y", "approve", "approved", "ok", "true")
        res = await _resume(request, session, dep, wf, thread_id, answer_value(pending, approve, text, raw), None)
        return _task(res, str(task_id))
    fields, _ = _input_from(message, c)
    try:
        h = await _start(
            request, session, dep.id, ApiRunIn(input=fields, thread_id=message.get("contextId"), user_id=None), auth
        )
    except NotFound:
        raise RpcError(-32001, f"context '{message.get('contextId')}' not found") from None
    await session.close()
    res = await _result(h)
    return _task(res, res.run_id)


async def _a2a_get(request: Request, session: AsyncSession, dep: Deployment, params: dict[str, Any]) -> dict[str, Any]:
    task_id = str(params.get("id") or "")
    thread_id = await _task_thread(session, dep, task_id)
    latest = await session.scalar(
        select(Run).where(Run.thread_id == thread_id).order_by(Run.started_at.desc()).limit(1)
    )
    handle = request.app.state.runner.handles.get(latest.id) if latest else None
    if handle is not None and handle.done.is_set():
        res = await _result(handle)
    else:
        status = latest.status if latest else "error"
        res = ApiResult(run_id=latest.id if latest else task_id, thread_id=thread_id, status=status,  # type: ignore[arg-type]
                        error=latest.error if latest else None)  # fmt: skip
    return _task(res, task_id)


@router.post("/deployments/{dep_id}/a2a", name="a2a_endpoint")
async def a2a_endpoint(
    dep_id: str,
    request: Request,
    authorization: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """The deployment as an A2A agent (JSON-RPC)."""
    dep, wf = await _caller(request, session, dep_id, authorization)
    id_: Any = None
    try:
        msg = await _rpc(request)
        method, params, id_ = msg["method"], msg.get("params") or {}, msg.get("id")
        if method in ("message/send", "SendMessage"):
            result = await _a2a_send(request, session, dep, wf, params, authorization)
        elif method in ("tasks/get", "GetTask"):
            result = await _a2a_get(request, session, dep, params)
        elif method in ("tasks/cancel", "CancelTask"):
            raise RpcError(-32002, "tasks cannot be cancelled through A2A; a paused task simply waits")
        elif method in ("message/stream", "SendStreamingMessage", "tasks/resubscribe"):
            raise RpcError(-32004, "streaming is not supported (the agent card says so); use message/send")
        else:
            raise RpcError(-32601, f"method '{method}' is not supported")
    except RpcError as e:
        return JSONResponse(_rpc_error(id_, e.code, e.message, e.data))
    except DomainError as e:
        return JSONResponse(_rpc_error(id_, -32602, e.detail))
    return JSONResponse({"jsonrpc": "2.0", "id": id_, "result": result})
