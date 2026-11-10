"""Deployments as agents other tools can call (S15): an MCP server and an A2A agent per deployment.

Both speak for the deployment's pinned version and take the same `Authorization: Bearer <deployment key>` as the
deployment API, with the same rate limits:

- **MCP** (`POST /v1/deployments/{id}/mcp`, Streamable HTTP, stateless JSON responses): one tool that runs the
  workflow, plus `answer` when it can pause for a person. Claude Desktop, Cursor, ChatGPT and agent frameworks
  connect to it like to any remote MCP server. A 2026-07-28 client that shows forms is asked to answer a pause
  itself (input_required, api/v1/mcp_input.py); one that takes the Tasks extension gets a task for a long run.
- **A2A** (`POST /v1/deployments/{id}/a2a`, JSON-RPC; card at `.../a2a/.well-known/agent-card.json`):
  `message/send` runs the workflow (a pause becomes `input-required`, answered by sending to the same task),
  `tasks/get` reads a task (from any server: services/run_results.py), `tasks/cancel` stops a working one.

Runs started here are ordinary deployment runs: they show in Runs, count for online evaluators and keep their
conversation (`thread_id` / A2A `contextId`).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.audit import note, payload_hash
from agentcanvas.api.deps import get_session, get_settings_dep
from agentcanvas.api.urls import api_base, api_url
from agentcanvas.api.v1 import mcp_input
from agentcanvas.api.v1.deployments import (
    ApiResult,
    ApiRunIn,
    _caller,
    _continue_on,
    _result,
    _start,
    api_result,
    bound_end_user,
    oauth_resource,
    own_thread,
    signed_in,
)
from agentcanvas.api.v1.runs import stop_run
from agentcanvas.db.models import Deployment, InterruptTask, Run, Thread, WorkflowVersion
from agentcanvas.errors import (
    Conflict,
    DomainError,
    Forbidden,
    InvalidInput,
    NotFound,
    TooManyRequests,
    Unauthorized,
)
from agentcanvas.ir.contract import Contract, contract
from agentcanvas.ir.models import Workflow
from agentcanvas.runtime import nesting
from agentcanvas.runtime.runner import RunHandle
from agentcanvas.runtime.types import ResumeSpec
from agentcanvas.services import approvers, deployment_oauth
from agentcanvas.services.disclosure import AI_HEADER
from agentcanvas.services.run_results import ResultRepo
from agentcanvas.settings import Settings

router = APIRouter(tags=["agent protocols"])
well_known = APIRouter(tags=["agent protocols"])  # at the server's root, where RFC 9728 puts resource metadata

# 2026-07-28 is "modern": every request carries its version in `_meta`, no `initialize`, no sessions. The older ones are
# "legacy" (an `initialize` handshake); this server speaks both, choosing by how the client opens (dual-era)
MCP_MODERN = ("2026-07-28",)
MCP_LEGACY = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
MCP_VERSIONS = MCP_MODERN + MCP_LEGACY  # newest first
META_VERSION = "io.modelcontextprotocol/protocolVersion"
META_SERVER = "io.modelcontextprotocol/serverInfo"
LIST_TTL_MS = 300_000  # a deployment's tools change only when it is published again
HEADER_MISMATCH, UNSUPPORTED_VERSION = -32020, -32022
CARD_MAX_AGE = 300  # seconds a client or cache may keep a card: a republished deployment shows within five minutes
SERVER_CARD_SCHEMA = "https://static.modelcontextprotocol.io/schemas/v1/server-card.schema.json"
SERVER_CARD_TYPE = "application/mcp-server-card+json"
A2A_VERSION = "0.3.0"  # what a client that sends no `A2A-Version` header speaks (the spec's rule)
A2A_V1 = "1.0"
A2A_V1_METHODS = ("SendMessage", "GetTask", "CancelTask", "ListTasks", "SendStreamingMessage", "SubscribeToTask",
                  "GetExtendedAgentCard")  # fmt: skip
TASK_NOT_FOUND, TASK_NOT_CANCELABLE, UNSUPPORTED_OPERATION, VERSION_NOT_SUPPORTED = -32001, -32002, -32004, -32009
V1_STATES = {
    "completed": "TASK_STATE_COMPLETED", "input-required": "TASK_STATE_INPUT_REQUIRED", "working": "TASK_STATE_WORKING",
    "canceled": "TASK_STATE_CANCELED", "failed": "TASK_STATE_FAILED",
}  # fmt: skip
CANCEL_WAIT_S = 5.0  # how long CancelTask waits for a run stopped on this server to save its outcome
PAUSES_ANSWERED = " It can pause for a person: then call `answer`."
PAUSES_ASKED = " It can pause for a person: your client then asks you, and the run goes on with your answer."
TASK_AFTER_S = 30.0  # a client that takes tasks gets one for a run still working after this long (or queued)
POLL_MS = 3000  # how often a task's client is asked to poll
MISSING_CAPABILITY = -32021
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
    if not isinstance(msg.get("params") or {}, dict):
        raise RpcError(-32602, "params must be an object")
    return msg


def tool_name(wf: Workflow) -> str:
    """The workflow's name as an MCP tool name (letters, digits, _ and -)."""
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", wf.name.strip().lower()).strip("_")[:60]
    return slug or "run_workflow"


def _people_decide(interrupt: dict[str, Any], wf: Workflow | None) -> bool:
    """The caller cannot answer the pause: its step names who decides it (E-6, it waits in the Inbox), or it is a
    Wait step (F-10, it continues on its own)."""
    if _waits(interrupt):
        return True
    stored = approvers.node_approvers(wf).get(interrupt.get("canvas_node_id") or "") if wf is not None else None
    return approvers.caller_refusal(approvers.Approvers.of(stored)) is not None


def _waits(interrupt: dict[str, Any]) -> bool:
    return interrupt.get("kind") == "timer"


def _at_wait(res: ApiResult) -> bool:
    """The run is paused at Wait steps only: it continues on its own, so its task counts as working."""
    return res.status == "interrupted" and bool(res.interrupts) and all(_waits(i) for i in res.interrupts)


def _waiting_for(interrupt: dict[str, Any]) -> str:
    """What a Wait step waits for, in a few words."""
    found = interrupt.get("payload")
    payload: dict[str, Any] = found if isinstance(found, dict) else {}
    due = payload.get("due_at") or "its time"
    if payload.get("event"):
        key = f" with key {payload['key']}" if payload.get("key") else ""
        return f"for the event {payload['event']}{key}, until {due} at most"
    return f"until {due}"


def _describe_pause(interrupts: list[dict[str, Any]], wf: Workflow | None = None) -> str:
    lines = []
    for i in interrupts:
        if _waits(i):
            lines.append(f"- it waits {_waiting_for(i)} (interrupt_id {i.get('id')}), then continues on its own")
            continue
        payload = i.get("payload")
        what = (
            (payload.get("question") or payload.get("message") or payload.get("title"))
            if isinstance(payload, dict)
            else payload
        )
        kind = {"approval": "an approval", "tool_approval": "approval of a tool call", "input": "an answer"}.get(
            i.get("kind") or "", "an answer"
        )
        theirs = " (decided by the people its workflow names, in AgentCanvas)" if _people_decide(i, wf) else ""
        lines.append(f"- it needs {kind}" + (f": {what}" if what else "") + f" (interrupt_id {i.get('id')}){theirs}")
    return "\n".join(lines)


def _next_step(interrupts: list[dict[str, Any]], wf: Workflow | None, answer: str) -> str:
    """What the caller can do about a pause: answer it, or wait for the people who decide it or for its time."""
    if interrupts and all(_waits(i) for i in interrupts):
        return "It continues on its own; you cannot answer it. Read its result later with the run's id."
    if interrupts and all(_people_decide(i, wf) for i in interrupts):
        return "It waits for a person to decide it in AgentCanvas; you cannot answer it."
    return answer


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
    if approve is not None:  # the pause is not known here: an explicit decision, never a truthy "no"
        return {"approved": approve, **({"comment": text} if text else {})}
    return text if text is not None else "yes"


async def _pending(
    request: Request, session: AsyncSession, thread_id: str, interrupt_id: str | None
) -> dict[str, Any] | None:
    """The interrupt an answer is for (to shape a simple answer): the one named, else the only one waiting (several
    waiting and none named is refused). From the inbox table, which every API replica shares, else from a recent run
    in this process. The caller has checked the thread is its own: a refusal lists the ids that wait."""
    row = await approvers.answered(session, thread_id, interrupt_id)
    if row is not None:
        return {"id": row.id, "kind": row.kind, "payload": row.payload}
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
    request: Request,
    session: AsyncSession,
    dep: Deployment,
    wf: Workflow,
    thread_id: str,
    value: Any,
    iid: str | None,
    labels: dict[str, str] | None = None,
) -> ApiResult:
    return await _result(await _resume_run(request, session, dep, wf, thread_id, value, iid, labels))


async def _resume_run(
    request: Request,
    session: AsyncSession,
    dep: Deployment,
    wf: Workflow,
    thread_id: str,
    value: Any,
    iid: str | None,
    labels: dict[str, str] | None = None,
) -> RunHandle:
    """Answer a pause of the deployment's conversation: the run that goes on (no connection held while it works)."""
    await own_thread(session, dep, thread_id, bound_end_user(request, dep))
    iid = await approvers.check_pending(session, thread_id, iid, approvers.CALLER)
    wf, version_id = await _continue_on(session, dep, wf, thread_id)
    h = await request.app.state.runner.start(
        wf,
        thread_id=thread_id,
        resume=ResumeSpec(value, iid),
        deployment_id=dep.id,
        version_id=version_id,
        labels=labels,
        caller_run_id=nesting.caller(request.headers.get(nesting.HEADER)),
    )
    await session.close()
    return h


# ---------------------------------------------------------------------------------------------- MCP ----
@dataclass(frozen=True)
class Caller:
    """What a 2026-07-28 client declared on the request (F-1)."""

    ask: bool = False  # it takes form elicitation: a pause asks the person through the client (input_required)
    tasks: bool = False  # it takes the Tasks extension: a long or queued run answers with a task to poll


LEGACY = Caller()


def _mcp_tools(wf: Workflow, c: Contract, caller: Caller = LEGACY) -> list[dict[str, Any]]:
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
            + ((PAUSES_ASKED if caller.ask else PAUSES_ANSWERED) if c.pauses else ""),
            "inputSchema": {"type": "object", "properties": props, "required": required},
            "annotations": {"title": wf.name, "readOnlyHint": False, "openWorldHint": True},
        }
    ]
    if c.pauses and not caller.ask:  # a client that shows forms asks the person; the calling model does not answer
        tools.append(
            {
                "name": "answer",
                "title": "Answer a paused run",
                "description": "Continue a run that paused for an approval or a question. Set approve for an "
                "approval, text for a question (or a reason when rejecting); value sends the raw resume value. "
                "When several pauses wait, interrupt_id names the one you answer.",
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


def _tool_result(res: ApiResult, result_url: str | None = None, wf: Workflow | None = None) -> dict[str, Any]:
    data = res.model_dump(exclude_none=True)
    if res.status == "interrupted":
        several = len(res.interrupts) > 1  # parallel pauses: each answer names the one it is for
        then = _next_step(res.interrupts, wf, "Call `answer` once for each, with its interrupt_id." if several
                          else "Call `answer`.")  # fmt: skip
        text = f"The run paused (thread_id {res.thread_id}):\n{_describe_pause(res.interrupts, wf)}\n{then}"
    elif res.status == "success":
        text = json.dumps(res.output, ensure_ascii=False, indent=1)
    elif res.status == "running":
        text = (f"Still running after the wait limit (run {res.run_id}). Its result: GET {result_url or 'the run'} "
                "with the deployment key.")  # fmt: skip
    else:
        text = f"The run ended with status {res.status}: {res.error or 'no details'}"
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": data,
        "isError": res.status in ("error", "cancelled"),
    }


def _result_url(request: Request, dep: Deployment, res: ApiResult) -> str:
    return api_url(request, "api_run_get", dep_id=dep.id, run_id=res.run_id)


async def _mcp_call(
    request: Request,
    session: AsyncSession,
    dep: Deployment,
    wf: Workflow,
    params: dict[str, Any],
    auth: str | None,
    caller: Caller = LEGACY,
) -> dict[str, Any]:
    name, args = params.get("name"), params.get("arguments") or {}
    if not isinstance(args, dict):
        raise RpcError(-32602, "arguments must be an object")
    c = contract(wf)
    try:
        if params.get("requestState") is not None or params.get("inputResponses") is not None:
            if not caller.ask or name not in (tool_name(wf), "answer"):
                raise RpcError(-32602, "requestState and inputResponses answer a pause this server asked about")
            return await _answer_asked(request, session, dep, wf, str(name), args, params, caller)
        if name == tool_name(wf):
            fields = {k: v for k, v in args.items() if k not in ("thread_id", "context")}
            try:
                body = ApiRunIn(
                    input=fields, thread_id=args.get("thread_id"), context=args.get("context") or {}, user_id=None
                )
            except ValidationError as exc:
                raise RpcError(-32602, f"invalid arguments: {exc.errors()[0]['msg']}") from None
            h = await _start(request, session, dep.id, body, auth, caller=(dep, wf))
            await session.close()
            return await _answer_call(request, dep, wf, h, str(name), args, caller)
        if name == "answer" and c.pauses:
            thread_id, iid = str(args.get("thread_id") or ""), args.get("interrupt_id")
            iid = str(iid) if iid else None
            await own_thread(session, dep, thread_id)  # before anything about its pauses is said
            pending = await _pending(request, session, thread_id, iid)
            value = answer_value(pending, args.get("approve"), args.get("text"), args.get("value"))
            h = await _resume_run(request, session, dep, wf, thread_id, value, iid)
            return await _answer_call(request, dep, wf, h, str(name), args, caller)
    except (InvalidInput, NotFound, Conflict, Forbidden) as exc:  # tell the model: it can correct it, or must wait
        return {"content": [{"type": "text", "text": exc.detail}], "isError": True}
    raise RpcError(-32602, f"unknown tool '{name}'")


async def _answer_call(
    request: Request, dep: Deployment, wf: Workflow, h: RunHandle, tool: str, args: dict[str, Any], caller: Caller
) -> dict[str, Any]:
    """A started run's answer to the tool call: a task to poll for a client that takes them, once the run waits in
    the run queue or outlasts `TASK_AFTER_S`; otherwise the run's result (after up to the 5-minute wait)."""
    if caller.tasks and not await _settles(h):
        now = datetime.now(UTC).isoformat()
        message = "waiting for a run slot" if h.waiting else "running"
        return {"resultType": "task", "taskId": h.run_id, "status": "working", "statusMessage": message,
                "createdAt": now, "lastUpdatedAt": now, "ttlMs": None, "pollIntervalMs": POLL_MS}  # fmt: skip
    return _call_result(request, dep, wf, await _result(h), tool, args, caller)


async def _settles(h: RunHandle) -> bool:
    """Whether the run ends within `TASK_AFTER_S` without waiting in the run queue."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + TASK_AFTER_S
    while not h.done.is_set():
        left = deadline - loop.time()
        if h.waiting or left <= 0:
            return False
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(h.done.wait(), min(left, 0.25))
    return True


def _call_result(
    request: Request, dep: Deployment, wf: Workflow, res: ApiResult, tool: str, args: dict[str, Any], caller: Caller
) -> dict[str, Any]:
    """A tool call's answer: a pause asks the person through a client that shows forms, else the run's result."""
    # a pause its workflow's named people decide (E-6) is not asked of the caller: it waits in the Inbox
    askable = [i for i in res.interrupts if not _people_decide(i, wf)] if res.status == "interrupted" else []
    if caller.ask and askable:
        return _input_required(request, dep, wf, res.thread_id, askable[0], tool, args)
    return _tool_result(res, _result_url(request, dep, res), wf)


def _presented_key(request: Request) -> str:
    """The hash of the deployment key this call was made with: a request state is bound to that key, so another of
    the deployment's keys (E-8), or this one once revoked, cannot answer the pause it asks about. A person signed in
    with OAuth (E-11) is bound by who they are instead, as their token is refreshed while they answer."""
    from agentcanvas.services.deployments import hash_key

    if (who := signed_in(request)) is not None:
        return hash_key(f"oauth:{who.user_id}")
    raw = request.headers.get("authorization") or ""
    return hash_key(raw.split(" ", 1)[1].strip() if raw.lower().startswith("bearer ") else raw)


def _input_required(
    request: Request,
    dep: Deployment,
    wf: Workflow,
    thread_id: str,
    interrupt: dict[str, Any],
    tool: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """MRTR: one form for the pause (the next one, if several wait: each answer asks the next), and the signed state
    the client echoes when it retries the call with the person's answer."""
    state = mcp_input.issue(
        request.app.state.settings.secret_key, deployment_id=dep.id,
        key=mcp_input.key_fingerprint(_presented_key(request)),
        thread_id=thread_id, interrupt_id=str(interrupt["id"]), tool=tool, arguments=args,
    )  # fmt: skip
    return {
        "resultType": "input_required",
        "inputRequests": {str(interrupt["id"]): mcp_input.form(interrupt, wf.name)},
        "requestState": state,
    }


async def _answer_asked(
    request: Request,
    session: AsyncSession,
    dep: Deployment,
    wf: Workflow,
    tool: str,
    args: dict[str, Any],
    params: dict[str, Any],
    caller: Caller,
) -> dict[str, Any]:
    """The retry of a call that paused: verify the state it echoes, then resume the run with the person's answer.
    The pause must still be open (answered once); an answer that does not fit asks again."""
    try:
        state = mcp_input.verify(
            request.app.state.settings.secret_key, params.get("requestState"), deployment_id=dep.id,
            key=mcp_input.key_fingerprint(_presented_key(request)), tool=tool, arguments=args,
        )  # fmt: skip
    except mcp_input.InvalidState as exc:
        raise RpcError(-32602, f"{exc}; call the tool again without it") from None
    q = select(InterruptTask).where(InterruptTask.id == state.interrupt_id, InterruptTask.thread_id == state.thread_id)
    row = await session.scalar(q.where(InterruptTask.status == "pending"))
    if row is None:  # answered already (this retry repeated, or someone answered in the app): where the run is now
        latest = await session.scalar(
            select(Run.id).where(Run.thread_id == state.thread_id).order_by(Run.started_at.desc(), Run.id.desc())
        )
        found = await ResultRepo(session, request.app.state.runner).of(latest or "", dep.id)
        if found is None:
            raise RpcError(-32602, "the paused run is gone (its conversation was deleted)")
        return _call_result(request, dep, wf, api_result(found), tool, args, caller)
    interrupt = {"id": row.id, "kind": row.kind, "payload": row.payload}
    responses = params.get("inputResponses")
    response = responses.get(row.id) if isinstance(responses, dict) else None
    outcome, value = mcp_input.answer(interrupt, response, lambda ok, why: answer_value(interrupt, ok, why, None))
    if outcome == "ask_again":
        return _input_required(request, dep, wf, state.thread_id, interrupt, tool, args)
    if outcome in ("declined", "dismissed"):
        what = "declined to answer" if outcome == "declined" else "closed the question without answering"
        paused = ApiResult(run_id=row.run_id, thread_id=state.thread_id, status="interrupted", interrupts=[interrupt])
        return {
            "content": [{"type": "text", "text": f"The person {what}. The run still waits (thread_id "
                         f"{state.thread_id}); it can also be answered in AgentCanvas's Inbox."}],
            "structuredContent": paused.model_dump(exclude_none=True),
            "isError": True,
        }  # fmt: skip
    note(request, interrupt_id=row.id, payload_sha256=payload_hash(value))
    h = await _resume_run(request, session, dep, wf, state.thread_id, value, row.id)
    return await _answer_call(request, dep, wf, h, tool, args, caller)


def _mcp_transport_refusal(request: Request, settings: Settings) -> JSONResponse | None:
    """The Streamable HTTP transport's two checks. `Origin`: a browser page may only call from this server's own
    origin or one of the API's CORS origins (403 otherwise; the defence against DNS rebinding, which the transport
    requires). `MCP-Protocol-Version`: when sent, one this server speaks (400 otherwise); absent means 2025-03-26."""
    origin = request.headers.get("origin")
    own = f"{request.url.scheme}://{request.url.netloc}"
    if origin is not None and origin != own and origin not in settings.cors_origins:
        return JSONResponse(_rpc_error(None, -32600, f"requests from the origin '{origin[:200]}' are not allowed"), 403)
    version = request.headers.get("mcp-protocol-version")
    if version is not None and version not in MCP_VERSIONS:
        return _unsupported(None, version)
    return None


def _unsupported(id_: Any, version: str) -> JSONResponse:
    known = ", ".join(MCP_VERSIONS)
    body = _rpc_error(id_, UNSUPPORTED_VERSION, f"MCP protocol version '{version[:40]}' is not supported ({known})",
                      {"supported": list(MCP_VERSIONS), "requested": version[:40]})  # fmt: skip
    return JSONResponse(body, 400)


def _header_value(raw: str | None) -> str | None:
    """A mirrored header's value, decoding the `=?base64?…?=` form clients use for names that are not plain ASCII."""
    if raw is not None and raw.startswith("=?base64?") and raw.endswith("?="):
        import base64
        import binascii

        try:
            return base64.b64decode(raw[9:-2], validate=True).decode()
        except (binascii.Error, UnicodeDecodeError):
            return None
    return raw


def _modern_refusal(request: Request, msg: dict[str, Any], version: str) -> JSONResponse | None:
    """The 2026-07-28 request checks: a version this server speaks, and the mirrored headers (`MCP-Protocol-Version`,
    `Mcp-Method`, `Mcp-Name`) present and equal to the body, so a gateway routing on them sees what runs."""
    id_ = msg.get("id")
    if version not in MCP_MODERN:
        return _unsupported(id_, version)
    params = msg.get("params") or {}
    expected = {"mcp-protocol-version": version, "mcp-method": msg["method"]}
    if msg["method"] in ("tools/call", "prompts/get", "resources/read"):
        expected["mcp-name"] = str(params.get("name") if msg["method"] != "resources/read" else params.get("uri"))
    elif msg["method"] in ("tasks/get", "tasks/update", "tasks/cancel"):  # the Tasks extension routes on the task
        expected["mcp-name"] = str(params.get("taskId"))
    for header, value in expected.items():
        sent = request.headers.get(header)
        if (_header_value(sent) if header == "mcp-name" else sent) != value:
            what = "is missing" if sent is None else f"'{sent[:80]}' does not match the body's '{value[:80]}'"
            return JSONResponse(_rpc_error(id_, HEADER_MISMATCH, f"Header mismatch: {header} {what}"), 400)
    return None


def _card_response(
    request: Request, card: dict[str, Any], media_type: str, *, vary: str | None = None, cors: bool = False
) -> Response:
    """A public card with HTTP caching (A2A 1.0 section 8.6, the MCP Server Card's discovery rules): `Cache-Control`,
    an `ETag` from its content, and 304 for an `If-None-Match` that still matches."""
    raw = json.dumps(card, separators=(",", ":"), ensure_ascii=False).encode()
    etag = '"' + hashlib.sha256(raw).hexdigest()[:32] + '"'
    headers = {"Cache-Control": f"public, max-age={CARD_MAX_AGE}", "ETag": etag}
    if vary:
        headers["Vary"] = vary
    if cors:  # public metadata, read-only: browser clients may read it from anywhere
        headers |= {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET",
                    "Access-Control-Allow-Headers": "Content-Type, If-None-Match",
                    "Access-Control-Expose-Headers": "ETag"}  # fmt: skip
    sent = request.headers.get("if-none-match")
    if sent is not None and (sent.strip() == "*" or etag in (t.strip().removeprefix("W/") for t in sent.split(","))):
        return Response(status_code=304, headers=headers)
    return Response(raw, media_type=media_type, headers=headers)


def _namespace(request: Request) -> str:
    """The Server Card name's namespace: the public host, reversed as reverse-DNS names are (agents.acme.com ->
    com.acme.agents); a host that is not a DNS name (an IPv6 address) gives `local.agentcanvas`."""
    from urllib.parse import urlparse

    host = (urlparse(api_base(request)).hostname or "").lower()
    if not re.fullmatch(r"[a-z0-9.-]+", host) or not host.strip("."):
        return "local.agentcanvas"
    labels = host.strip(".").split(".")
    return host if all(x.isdigit() for x in labels) else ".".join(reversed(labels))


def server_card(request: Request, dep_id: str, wf: Workflow, semver: str, oauth: bool = False) -> dict[str, Any]:
    """The deployment's MCP Server Card (SEP-2127): who it is and where to connect, read before connecting. Its
    tools are not listed (clients list them once connected), and the key is only described, never included."""
    what = "Bearer and an OAuth access token or the deployment's key" if oauth else "Bearer and the deployment's key"
    return {
        "$schema": SERVER_CARD_SCHEMA,
        "name": f"{_namespace(request)}/{tool_name(wf)}",
        "title": wf.name[:100],
        "description": wf.description or f"Tools of the {wf.name} workflow, published with AgentCanvas.",
        "version": semver,
        "remotes": [{
            "type": "streamable-http",
            "url": api_url(request, "mcp_endpoint", dep_id=dep_id),
            "supportedProtocolVersions": list(MCP_VERSIONS),
            "headers": [{"name": "Authorization", "description": what,
                         "isRequired": True, "isSecret": True, "placeholder": "Bearer ac_..."}],
        }],
    }  # fmt: skip


RUN_METHODS = {"tools/call", "SendMessage", "message/send"}  # MCP and A2A methods that answer with a run's output


def _marking(method: str) -> dict[str, str] | None:
    """`X-AI-Generated: true` on an answer that carries what the deployment generated (AI Act Art. 50(2))."""
    return AI_HEADER if method in RUN_METHODS else None


@router.get("/deployments/{dep_id}/mcp/server-card")
async def mcp_server_card(dep_id: str, request: Request, session: AsyncSession = Depends(get_session)) -> Response:
    """The deployment's MCP Server Card, at the place the MCP Server Card extension reserves for it
    (`<streamable-http-url>/server-card`). Public, like the A2A card: discovery happens before authentication."""
    dep, wf, semver = await _deployment(session, dep_id)
    card = server_card(request, dep_id, wf, semver, oauth=dep.oauth is not None)
    return _card_response(request, card, SERVER_CARD_TYPE, cors=True)


@router.post("/deployments/{dep_id}/mcp", name="mcp_endpoint")
async def mcp_endpoint(
    dep_id: str,
    request: Request,
    authorization: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> Response:
    """The deployment as a remote MCP server (Streamable HTTP; every response is a single JSON message)."""
    if (refused := _mcp_transport_refusal(request, settings)) is not None:
        return refused
    dep, wf = await _caller(request, session, dep_id, authorization, oauth="mcp_endpoint")
    try:
        msg = await _rpc(request)
    except RpcError as e:
        return JSONResponse(_rpc_error(None, e.code, e.message), status_code=400)
    method, params, id_ = msg["method"], msg.get("params") or {}, msg.get("id")
    if "id" not in msg:  # a notification (initialized, cancelled, ...): nothing to answer
        return Response(status_code=202)
    modern = ((params.get("_meta") or {}) if isinstance(params.get("_meta"), dict) else {}).get(META_VERSION)
    if modern is not None:
        if (refused := _modern_refusal(request, msg, str(modern))) is not None:
            return refused
        return await _mcp_modern(request, session, dep, wf, msg, authorization)
    try:
        if method == "initialize":
            asked = params.get("protocolVersion")
            version = await session.get(WorkflowVersion, dep.version_id)
            result: dict[str, Any] = {
                "protocolVersion": asked if asked in MCP_LEGACY else MCP_LEGACY[0],  # `initialize` is legacy
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
    return JSONResponse({"jsonrpc": "2.0", "id": id_, "result": result}, headers=_marking(method))


async def _mcp_modern(
    request: Request, session: AsyncSession, dep: Deployment, wf: Workflow, msg: dict[str, Any], auth: str | None
) -> Response:
    """A 2026-07-28 request, served on its own: every result says it is `complete`, names this server, and list
    results say how long they stay fresh (`ttlMs`) and that they are this caller's (`cacheScope`)."""
    method, params, id_ = msg["method"], msg.get("params") or {}, msg.get("id")
    version = await session.get(WorkflowVersion, dep.version_id)
    server = {"name": f"agentcanvas-{tool_name(wf)}", "version": version.semver if version else "0"}
    cached = {"ttlMs": LIST_TTL_MS, "cacheScope": "private"}  # behind the deployment key: never a shared cache
    try:
        if method == "server/discover":
            result: dict[str, Any] = {
                "supportedVersions": list(MCP_VERSIONS),
                "capabilities": {"tools": {"listChanged": False}, "extensions": {mcp_input.TASKS: {}}},
                "instructions": wf.description or f"Tools of the {wf.name} workflow, published with AgentCanvas.",
                **cached,
            }  # fmt: skip
        elif method == "tools/list":
            result = {"tools": _mcp_tools(wf, contract(wf), _modern_caller(params)), **cached}
        elif method == "tools/call":
            result = await _mcp_call(request, session, dep, wf, params, auth, _modern_caller(params))
        elif method in ("tasks/get", "tasks/update", "tasks/cancel"):
            result = await _mcp_task(request, session, dep, wf, method, params, _modern_caller(params))
        elif method in ("resources/list", "prompts/list", "resources/templates/list"):
            result = {method.split("/")[0] if method != "resources/templates/list" else "resourceTemplates": [],
                      **cached}  # fmt: skip
        else:  # the modern transport answers an unknown method with 404 and the JSON-RPC error
            return JSONResponse(_rpc_error(id_, -32601, f"method '{method}' is not supported"), 404)
    except RpcError as e:
        return JSONResponse(_rpc_error(id_, e.code, e.message, e.data))
    result = {"resultType": "complete", **result, "_meta": {META_SERVER: server}}
    return JSONResponse({"jsonrpc": "2.0", "id": id_, "result": result}, headers=_marking(method))


async def _still_open(session: AsyncSession, interrupts: list[dict[str, Any]]) -> bool:
    ids = [str(i.get("id")) for i in interrupts]
    q = select(InterruptTask.id).where(InterruptTask.id.in_(ids), InterruptTask.status == "pending")
    return bool(ids) and await session.scalar(q.limit(1)) is not None


def _modern_caller(params: dict[str, Any]) -> Caller:
    return Caller(ask=mcp_input.elicits(params), tasks=mcp_input.tasks(params))


async def _mcp_task(
    request: Request,
    session: AsyncSession,
    dep: Deployment,
    wf: Workflow,
    method: str,
    params: dict[str, Any],
    caller: Caller,
) -> dict[str, Any]:
    """The Tasks extension (`io.modelcontextprotocol/tasks`): a task is the run its tool call started (task id =
    run id) and the runs that answered its pauses (labelled with it). Only the deployment's own runs are tasks: any
    other id is the same "not found", so a task id is never a credential on its own."""
    if not caller.tasks:
        raise RpcError(MISSING_CAPABILITY, "Missing required client capability",
                       {"requiredCapabilities": {"extensions": {mcp_input.TASKS: {}}}})  # fmt: skip
    task_id = params.get("taskId")
    q = select(Run.id, Run.thread_id, Run.started_at).join(Thread, Thread.id == Run.thread_id)
    q = q.where(Run.id == str(task_id), Thread.deployment_id == dep.id)
    if (mine := bound_end_user(request, dep)) is not None:  # a signed-in person polls only their own tasks
        q = q.where(Thread.end_user == mine)
    task = (await session.execute(q)).first()
    if not isinstance(task_id, str) or task is None:
        raise RpcError(-32602, "Failed to retrieve task: Task not found")
    of_task = or_(Run.id == task_id, Run.labels["mcp_task"].as_string() == task_id)
    latest = (
        await session.execute(
            select(Run.id, Run.started_at, Run.ended_at, Run.queued)
            .where(Run.thread_id == task.thread_id, of_task)
            .order_by(Run.started_at.desc(), Run.id.desc())
            .limit(1)
        )
    ).one()
    results = ResultRepo(session, request.app.state.runner)
    found = await results.of(latest.id, dep.id)
    if found is not None and found.status == "interrupted" and not await _still_open(session, found.interrupts):
        # answered outside the task (the app's Inbox): the conversation's newest run says where it is now
        newest = select(Run.id).where(Run.thread_id == task.thread_id).order_by(Run.started_at.desc(), Run.id.desc())
        found = await results.of(await session.scalar(newest.limit(1)) or latest.id, dep.id)
    res = api_result(found) if found else ApiResult(run_id=latest.id, thread_id=task.thread_id, status="error")
    if method == "tasks/cancel":  # an acknowledgement: a run that works (or is at a Wait step) stops, wherever it runs
        if res.status == "running" or _at_wait(res):
            await stop_run(session, request.app.state.runner, res.run_id)
        return {}
    askable = [i for i in res.interrupts if not _waits(i)] if res.status == "interrupted" else []
    asked = askable[0] if askable and caller.ask else None
    key = f"{res.run_id}:{asked['id']}" if asked else None  # unique over the task: each pause is a new run's
    if method == "tasks/update":
        responses = params.get("inputResponses")
        if asked is not None and isinstance(responses, dict) and key in responses:
            outcome, value = mcp_input.answer(asked, responses[key], lambda ok, why: answer_value(asked, ok, why, None))
            if outcome == "resume":
                note(request, interrupt_id=asked["id"], payload_sha256=payload_hash(value))
                with contextlib.suppress(Conflict):  # answered meanwhile (the app's Inbox, another update)
                    await _resume_run(request, session, dep, wf, res.thread_id, value, str(asked["id"]),
                                      labels={"mcp_task": task_id})  # fmt: skip
        return {}
    changed = latest.ended_at or latest.started_at
    out: dict[str, Any] = {"taskId": task_id, "createdAt": task.started_at.isoformat(),
                           "lastUpdatedAt": changed.isoformat(), "ttlMs": None, "pollIntervalMs": POLL_MS}  # fmt: skip
    if res.status == "running":
        return {**out, "status": "working", "statusMessage": "waiting for a run slot" if latest.queued else "running"}
    if _at_wait(res):
        return {**out, "status": "working", "statusMessage": f"waiting {_waiting_for(res.interrupts[0])}"}
    if asked is not None:
        return {**out, "status": "input_required", "statusMessage": "waiting for a person",
                "inputRequests": {key: mcp_input.form(asked, wf.name)}}  # fmt: skip
    if res.status == "cancelled":
        return {**out, "status": "cancelled", "statusMessage": res.error or "cancelled"}
    # done (a failed run is a tool result with isError, not a protocol failure); a pause the client cannot show a
    # form for completes with the result the tool call itself gives (it says to call `answer`)
    return {**out, "status": "completed", "result": _tool_result(res, _result_url(request, dep, res), wf)}


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


@dataclass(frozen=True)
class CardSignIn:
    """What an agent card says about OAuth sign-in (E-11): where to get a token, and for what."""

    issuer: str
    scopes: list[str]
    audience: str

    @property
    def discovery(self) -> str:
        return self.issuer.rstrip("/") + "/.well-known/openid-configuration"

    @property
    def description(self) -> str:
        scopes = f", with the scopes {' '.join(self.scopes)}" if self.scopes else ""
        return f"An OAuth access token from {self.issuer} for the audience {self.audience}{scopes}."


def agent_card(
    wf: Workflow, semver: str, url: str, version: str | None = None, oauth: CardSignIn | None = None
) -> dict[str, Any]:
    """The agent card: A2A 1.0's shape for a client that asks for 1.x (`A2A-Version`), else 0.3's, which also lists
    the 1.0 interface (`supportedInterfaces`) so a 1.0 client reading it without the header finds it. With OAuth
    sign-in, either the key or an access token will do (two security requirements: one of them must hold)."""
    card = _card_v03(wf, semver, url, oauth)
    interfaces = [{"url": url, "protocolBinding": "JSONRPC", "protocolVersion": A2A_V1},
                  {"url": url, "protocolBinding": "JSONRPC", "protocolVersion": "0.3"}]  # fmt: skip
    if not _v1(version):
        return {**card, "supportedInterfaces": interfaces}
    drop = ("protocolVersion", "url", "preferredTransport", "securitySchemes", "security")
    schemes: dict[str, Any] = {"deploymentKey": {"httpAuthSecurityScheme": {"scheme": "bearer"}}}
    required: list[dict[str, Any]] = [{"schemes": {"deploymentKey": {"list": []}}}]
    if oauth is not None:
        schemes["oauth"] = {"openIdConnectSecurityScheme": {"openIdConnectUrl": oauth.discovery,
                                                            "description": oauth.description}}  # fmt: skip
        required.append({"schemes": {"oauth": {"list": list(oauth.scopes)}}})
    return {
        **{k: v for k, v in card.items() if k not in drop},
        "supportedInterfaces": interfaces,
        "securitySchemes": schemes,
        "securityRequirements": required,
    }


def _v1(version: str | None) -> bool:
    return bool(version) and str(version).strip().split(".")[0] == "1"


def _card_v03(wf: Workflow, semver: str, url: str, oauth: CardSignIn | None = None) -> dict[str, Any]:
    c = contract(wf)
    schemes: dict[str, Any] = {"deploymentKey": {"type": "http", "scheme": "bearer"}}
    security: list[dict[str, list[str]]] = [{"deploymentKey": []}]
    if oauth is not None:
        schemes["oauth"] = {"type": "openIdConnect", "openIdConnectUrl": oauth.discovery,
                            "description": oauth.description}  # fmt: skip
        security.append({"oauth": list(oauth.scopes)})
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
        "securitySchemes": schemes,
        "security": security,
    }


@router.get("/deployments/{dep_id}/a2a/.well-known/agent-card.json")
@router.get("/deployments/{dep_id}/a2a/.well-known/agent.json")
async def a2a_card(dep_id: str, request: Request, session: AsyncSession = Depends(get_session)) -> Response:
    """The deployment's A2A agent card. Public, as A2A clients read it before they authenticate: it names the
    workflow and its input fields; running it needs the key. Cached as A2A 1.0 section 8.6 asks; its shape depends
    on `A2A-Version`, so caches keep one per version."""
    dep, wf, semver = await _deployment(session, dep_id)
    url = api_url(request, "a2a_endpoint", dep_id=dep_id)  # as callers reach it, e.g. behind nginx's /api
    card = agent_card(wf, semver, url, request.headers.get("a2a-version"), _card_sign_in(request, dep, url))
    return _card_response(request, card, "application/json", vary="A2A-Version")


def _card_sign_in(request: Request, dep: Deployment, url: str) -> CardSignIn | None:
    cfg = deployment_oauth.config(dep.oauth)
    issuer = deployment_oauth.issuer_of(cfg, request.app.state.settings) if cfg else None
    if cfg is None or issuer is None:
        return None
    return CardSignIn(issuer=issuer, scopes=cfg.scopes, audience=cfg.audience or url)


ENDPOINT_PATH = re.compile(r"/v1/deployments/([^/]+)/(mcp|a2a)")


@well_known.get(deployment_oauth.WELL_KNOWN + "/{resource_path:path}", include_in_schema=False)
async def protected_resource(
    resource_path: str, request: Request, session: AsyncSession = Depends(get_session)
) -> Response:
    """The protected resource metadata (RFC 9728) of a deployment's MCP or A2A endpoint that takes OAuth sign-in:
    which authorization server issues its tokens and which scopes they need. Public, as clients read it before they
    sign in; only a deployment with OAuth on has one, at the path of the endpoint's public address."""
    path = "/" + resource_path
    found = ENDPOINT_PATH.search(path)
    dep = await session.get(Deployment, found[1]) if found and path.endswith(found[0]) else None
    cfg = deployment_oauth.config(dep.oauth) if dep is not None and dep.active else None
    issuer = deployment_oauth.issuer_of(cfg, request.app.state.settings) if cfg else None
    if found is None or dep is None or cfg is None or issuer is None:
        raise NotFound("no protected resource here")
    resource = oauth_resource(request, "mcp_endpoint" if found[2] == "mcp" else "a2a_endpoint", dep.id)
    if urlsplit(resource).path != path:  # the metadata of an address callers do not use (RFC 9728 section 3.3)
        raise NotFound("no protected resource here")
    version = await session.get(WorkflowVersion, dep.version_id)
    doc: dict[str, Any] = {"resource": resource, "authorization_servers": [issuer],
                           "bearer_methods_supported": ["header"]}  # fmt: skip
    if cfg.scopes:  # RFC 9728: a parameter without values is left out
        doc["scopes_supported"] = list(cfg.scopes)
    if version is not None:
        doc["resource_name"] = Workflow.model_validate(version.ir).name
    return _card_response(request, doc, "application/json", cors=True)


def _parts(message: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """The data fields and the text of an A2A message (parts that are not objects are ignored)."""
    fields: dict[str, Any] = {}
    texts: list[str] = []
    parts = message.get("parts")
    for part in parts if isinstance(parts, list) else []:
        if not isinstance(part, dict):
            continue
        kind = part.get("kind") or part.get("type") or ("data" if "data" in part else "text")
        if kind == "data" and isinstance(part.get("data"), dict):
            fields.update(part["data"])
        elif kind == "text" and isinstance(part.get("text"), str):
            texts.append(part["text"])
    return fields, ("\n".join(texts) if texts else None)


def _input_from(message: dict[str, Any], c: Contract) -> dict[str, Any]:
    """The run input from an A2A message: data parts are fields; text goes to the workflow's text field."""
    fields, text = _parts(message)
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
    return fields


def _task(res: ApiResult, task_id: str, wf: Workflow | None = None) -> dict[str, Any]:
    state = {"success": "completed", "interrupted": "input-required", "running": "working",
             "cancelled": "canceled"}.get(res.status, "failed")  # fmt: skip
    if _at_wait(res):
        state = "working"  # at a Wait step: nothing is asked of the caller
    status: dict[str, Any] = {"state": state, "timestamp": datetime.now(UTC).isoformat()}
    if res.status == "interrupted":
        status["message"] = _agent_message(
            task_id, res.thread_id,
            f"Paused:\n{_describe_pause(res.interrupts, wf)}\n"
            + _next_step(res.interrupts, wf, 'Reply to this task once for each, with a data part {"interrupt_id": '
                         '"..."} naming the one you answer.' if len(res.interrupts) > 1
                         else "Reply to this task to continue."),
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


def _v1_task(task: dict[str, Any]) -> dict[str, Any]:
    """A task in A2A 1.0's wire form: ProtoJSON enum names, parts without `kind`, no `kind` on the task."""

    def parts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{k: v for k, v in p.items() if k != "kind"} for p in items]

    status = dict(task["status"])
    status["state"] = V1_STATES.get(status["state"], "TASK_STATE_FAILED")
    if "message" in status:
        m = status["message"]
        status["message"] = {**{k: v for k, v in m.items() if k != "kind"}, "role": "ROLE_AGENT",
                             "parts": parts(m["parts"])}  # fmt: skip
    out = {k: v for k, v in task.items() if k not in ("kind", "status", "artifacts")}
    out["status"] = status
    if task.get("artifacts"):
        out["artifacts"] = [{**a, "parts": parts(a["parts"])} for a in task["artifacts"]]
    return out


async def _task_thread(session: AsyncSession, dep: Deployment, task_id: str, end_user: str | None) -> Thread:
    """The conversation of a task (a task is named after the run that started it); a signed-in caller's own only."""
    q = select(Thread).join(Run, Thread.id == Run.thread_id)
    thread = await session.scalar(q.where(Run.id == task_id, Thread.deployment_id == dep.id))
    if thread is None or (end_user is not None and thread.end_user != end_user):
        raise RpcError(TASK_NOT_FOUND, f"task '{task_id}' not found")
    return thread


YES = ("yes", "y", "approve", "approved", "ok", "okay", "true")


async def _a2a_send(
    request: Request, session: AsyncSession, dep: Deployment, wf: Workflow, params: dict[str, Any], auth: str | None
) -> dict[str, Any]:
    message = params.get("message")
    if not isinstance(message, dict):
        raise RpcError(-32602, "params.message is required")
    task_id = message.get("taskId")
    if task_id:  # the answer to a paused task
        task_id = str(task_id)
        thread = await _task_thread(session, dep, task_id, bound_end_user(request, dep))
        data, text = _parts(message)
        named = str(data["interrupt_id"]) if data.get("interrupt_id") else None  # one of several pauses
        pending = await _pending(request, session, thread.id, named) if thread.status == "interrupted" else None
        if pending is None:
            raise RpcError(-32602, f"task '{task_id}' is not waiting for input; send a new message (with contextId "
                           f"'{thread.id}' to continue the conversation)")  # fmt: skip
        approve = data.get("approve") if isinstance(data.get("approve"), bool) else None
        if approve is None and text is not None and pending.get("kind") in ("approval", "tool_approval"):
            approve = text.strip().lower() in YES
        value = answer_value(pending, approve, text, data.get("value"))
        res = await _resume(request, session, dep, wf, thread.id, value, named, labels={"a2a_task": task_id})
        return _task(res, task_id, wf)
    fields = _input_from(message, contract(wf))
    context_id = message.get("contextId")
    try:
        body = ApiRunIn(input=fields, thread_id=context_id if context_id is None else str(context_id), user_id=None)
        h = await _start(request, session, dep.id, body, auth, caller=(dep, wf))
    except ValidationError as exc:
        raise RpcError(-32602, f"invalid message: {exc.errors()[0]['msg']}") from None
    except NotFound:
        raise RpcError(TASK_NOT_FOUND, f"context '{context_id}' not found") from None
    await session.close()
    res = await _result(h)
    return _task(res, res.run_id, wf)


async def _a2a_get(request: Request, session: AsyncSession, dep: Deployment, params: dict[str, Any]) -> dict[str, Any]:
    """A task's state: the run that started it, or the latest run that answered it (labelled with the task)."""
    task_id = str(params.get("id") or "")
    return _task(await _task_result(request, session, dep, task_id), task_id)


async def _task_result(request: Request, session: AsyncSession, dep: Deployment, task_id: str) -> ApiResult:
    thread = await _task_thread(session, dep, task_id, bound_end_user(request, dep))
    # the newest run that is the task or answered it, found in SQL: a long conversation has many runs
    task_run = or_(Run.id == task_id, Run.labels["a2a_task"].as_string() == task_id)
    q = select(Run.id).where(Run.thread_id == thread.id, task_run)
    latest = await session.scalar(q.order_by(Run.started_at.desc(), Run.id.desc()).limit(1))
    # from this server's memory or the run's row: the same answer on every replica (E-2)
    found = await ResultRepo(session, request.app.state.runner).of(latest or task_id, dep.id)
    return api_result(found) if found else ApiResult(run_id=task_id, thread_id=thread.id, status="error")


async def _a2a_cancel(
    request: Request, session: AsyncSession, dep: Deployment, params: dict[str, Any]
) -> dict[str, Any]:
    """Stop a working task (or one at a Wait step), wherever its run runs; one that ended, or waits for an answer,
    cannot be cancelled."""
    task_id = str(params.get("id") or "")
    res = await _task_result(request, session, dep, task_id)
    if res.status != "running" and not _at_wait(res):  # a task at a Wait step is working too
        state = "waits for an answer" if res.status == "interrupted" else "has ended"
        raise RpcError(TASK_NOT_CANCELABLE, f"task '{task_id}' {state}: only a working task can be cancelled")
    runner = request.app.state.runner
    stopped = await stop_run(session, runner, res.run_id)
    if stopped["cancelled"] and not stopped["pending"] and (h := runner.handles.get(res.run_id)) is not None:
        with contextlib.suppress(TimeoutError):  # it stops at once here; saving its outcome takes a moment
            await asyncio.wait_for(h.done.wait(), CANCEL_WAIT_S)
    return _task(await _task_result(request, session, dep, task_id), task_id)


async def _a2a_v1(
    request: Request,
    session: AsyncSession,
    dep: Deployment,
    wf: Workflow,
    method: str,
    params: dict[str, Any],
    auth: str | None,
) -> dict[str, Any]:
    """An A2A 1.0 call: the 0.3 handlers do the work, the answer takes 1.0's shape."""
    if method == "SendMessage":
        return {"task": _v1_task(await _a2a_send(request, session, dep, wf, params, auth))}
    if method == "GetTask":
        return _v1_task(await _a2a_get(request, session, dep, params))
    if method == "CancelTask":
        return _v1_task(await _a2a_cancel(request, session, dep, params))
    raise RpcError(UNSUPPORTED_OPERATION, f"{method} is not supported by this agent (see its card's capabilities)")


@router.post("/deployments/{dep_id}/a2a", name="a2a_endpoint")
async def a2a_endpoint(
    dep_id: str,
    request: Request,
    authorization: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """The deployment as an A2A agent (JSON-RPC): A2A 1.0's methods (`SendMessage`, `GetTask`, …) and 0.3's
    (`message/send`, `tasks/get`), told apart by name as the reference SDK does."""
    dep, wf = await _caller(request, session, dep_id, authorization, oauth="a2a_endpoint")
    id_: Any = None
    try:
        msg = await _rpc(request)
        method, params, id_ = msg["method"], msg.get("params") or {}, msg.get("id")
        if "id" not in msg:  # a notification: nothing runs and nothing is answered
            return Response(status_code=204)
        if method in A2A_V1_METHODS:
            version = request.headers.get("a2a-version") or "0.3"  # the spec: no header means 0.3
            if not _v1(version):
                said = f"{method} is A2A {A2A_V1}; send the header A2A-Version: {A2A_V1} (0.3 clients use message/send)"
                raise RpcError(VERSION_NOT_SUPPORTED, said, {"supportedVersions": [A2A_V1, "0.3"]})
            result = await _a2a_v1(request, session, dep, wf, method, params, authorization)
        elif method == "message/send":
            result = await _a2a_send(request, session, dep, wf, params, authorization)
        elif method == "tasks/get":
            result = await _a2a_get(request, session, dep, params)
        elif method == "tasks/cancel":
            result = await _a2a_cancel(request, session, dep, params)
        elif method in ("message/stream", "tasks/resubscribe"):
            raise RpcError(-32004, "streaming is not supported (the agent card says so); use message/send")
        else:
            raise RpcError(-32601, f"method '{method}' is not supported")
    except RpcError as e:
        return JSONResponse(_rpc_error(id_, e.code, e.message, e.data))
    except (TooManyRequests, Unauthorized):
        raise  # HTTP 429 / 401, so clients back off or fix their key
    except DomainError as e:
        return JSONResponse(_rpc_error(id_, -32602, e.detail))
    return JSONResponse({"jsonrpc": "2.0", "id": id_, "result": result}, headers=_marking(method))
