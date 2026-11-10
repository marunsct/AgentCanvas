"""AG-UI 1.0 for deployments (F-8): `POST /v1/deployments/{id}/ag-ui`, for CopilotKit-style front ends.

The request is an AG-UI `RunAgentInput` (the official `ag-ui-protocol` models), with the deployment's key or, when the
deployment takes OAuth sign-in, a person's access token for this endpoint (who is then the run's end user, and may
continue only their own conversations); the answer is a stream of AG-UI events (runtime/agui.py). The run is an
ordinary run of the deployment's version: it shows in Runs, counts for rate limits, spending caps and online
evaluators, and keeps its conversation under the caller's `threadId` (a new id starts a conversation, a known one
continues it).

* `messages`: a chat workflow (a message list input) gets the conversation on a new thread, then each new user
  message; any other workflow gets the newest user message in its text input. `state` sets the workflow's other
  input fields. `forwardedProps` may carry `user_id` (ignored for a signed-in person) and `metadata`, like the
  deployment API.
* `tools` (front-end tools) are offered to the workflow's agents. A call of one ends the run as a success with the
  call in `pendingToolCallIds`; the next input's tool message with that `toolCallId` continues the run with the result.
* `context` is shown to the agents as information.
* A pause (approval, question) ends the run with the `interrupt` outcome; `resume` entries answer it, through the same
  approver checks as the deployment API (a step that names its deciders waits for them in the Inbox).
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any

from ag_ui.core import Interrupt, ResumeEntry, RunAgentInput, ToolMessage, UserMessage
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.adapters.spi import supports
from agentcanvas.api.audit import note, payload_hash
from agentcanvas.api.deps import get_session
from agentcanvas.api.v1 import mcp_input
from agentcanvas.api.v1.deployments import (
    ApiRunIn,
    Caller,
    _api_context,
    _continue_on,
    bound_end_user,
    deployment_caller,
    own_files,
    signed_in,
)
from agentcanvas.api.v1.protocols import _people_decide, answer_value
from agentcanvas.api.v1.runs import SSE_HEADERS
from agentcanvas.api.v1.share import _answer_steps
from agentcanvas.db.models import Deployment, Run, Thread
from agentcanvas.errors import Conflict, InvalidInput
from agentcanvas.ir.contract import contract
from agentcanvas.ir.models import Workflow
from agentcanvas.observability import counted_stream
from agentcanvas.runtime import agui, nesting
from agentcanvas.runtime.types import CLIENT_TOOL, ResumeSpec
from agentcanvas.services import approvers
from agentcanvas.services.data_subjects import end_user_label
from agentcanvas.services.disclosure import AI_HEADER, marked
from agentcanvas.services.run_results import ResultRepo
from agentcanvas.services.share import InputShape, answer_text, input_shape

router = APIRouter(tags=["deployments"])
ag_ui_caller = deployment_caller("api_ag_ui")  # its own resource: tokens for this endpoint's address

THREAD_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_MESSAGES, MAX_TOOLS, MAX_CONTEXT = 1000, 64, 50
MAX_TOOL_CHARS, MAX_CONTEXT_CHARS = 16_000, 20_000  # one tool's declaration, all the context
CHAT_ROLES = {"user": "user", "assistant": "assistant", "system": "system", "developer": "system"}


def _obj(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text_of(content: Any) -> str:
    """The text of a message's content: a string, or its text parts (images and files are not taken yet)."""
    if isinstance(content, str):
        return content
    parts = content if isinstance(content, list) else []
    return "".join(str(getattr(p, "text", "")) for p in parts if getattr(p, "type", None) == "text")


def _new_messages(body: RunAgentInput, fresh: bool) -> list[Any]:
    """What the run reads of `messages`: a new conversation all of it, a known one what came after its last answer."""
    if fresh:
        return list(body.messages)
    last = max((i for i, m in enumerate(body.messages) if m.role == "assistant"), default=-1)
    return list(body.messages[last + 1 :])


def _run_input(wf: Workflow, shape: InputShape, body: RunAgentInput, fresh: bool) -> dict[str, Any]:
    """The run's input: `state`'s input fields, and the messages where the workflow takes them (a message wins over
    the same field in `state`; the conversation itself only comes from `messages`)."""
    if body.state is not None and not isinstance(body.state, dict):
        raise InvalidInput("state must be an object of the workflow's input fields")
    inputs = contract(wf).input
    chat = shape.field if shape.chat else None
    payload = {k: v for k, v in (body.state or {}).items() if k in inputs and k != chat}
    new = _new_messages(body, fresh)
    if shape.field is not None and shape.chat:
        said = [
            {"role": CHAT_ROLES[m.role], "content": text}
            for m in new
            if m.role in CHAT_ROLES and (fresh or m.role == "user") and (text := _text_of(m.content))
        ]
        if said:
            payload[shape.field] = said
    elif shape.field is not None:
        text = next((_text_of(m.content) for m in reversed(new) if isinstance(m, UserMessage)), "")
        if text:
            payload[shape.field] = text
    if not payload:
        raise InvalidInput("send a new user message (or the workflow's input fields in state)")
    return payload


def _client_tools(body: RunAgentInput) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """The application's tools and context, checked: names a model accepts, no duplicates, bounded sizes."""
    tools = body.tools or []
    if len(tools) > MAX_TOOLS:
        raise InvalidInput(f"at most {MAX_TOOLS} tools")
    out: list[dict[str, Any]] = []
    for t in tools:
        if not TOOL_NAME.match(t.name):
            raise InvalidInput(f"tool name '{t.name[:80]}' must be 1-64 letters, digits, _ or -")
        if any(o["name"] == t.name for o in out):
            raise InvalidInput(f"tool '{t.name}' is declared twice")
        if t.parameters is not None and not (isinstance(t.parameters, dict) and t.parameters.get("type") == "object"):
            raise InvalidInput(f"tool '{t.name}': parameters must be a JSON Schema of type object")
        decl = {"name": t.name, "description": t.description, "parameters": t.parameters}
        if len(json.dumps(decl, default=str)) > MAX_TOOL_CHARS:
            raise InvalidInput(f"tool '{t.name}' is longer than {MAX_TOOL_CHARS} characters")
        out.append(decl)
    context = [{"description": c.description, "value": c.value} for c in body.context or []]
    if len(context) > MAX_CONTEXT or len(json.dumps(context)) > MAX_CONTEXT_CHARS:
        raise InvalidInput(f"context: at most {MAX_CONTEXT} entries and {MAX_CONTEXT_CHARS} characters")
    return out, context


def _props(body: RunAgentInput) -> tuple[str | None, dict[str, str]]:
    """`forwardedProps`: the end user's id and run labels, as the deployment API takes them; the rest is ignored."""
    props = body.forwarded_props if isinstance(body.forwarded_props, dict) else {}
    user = props.get("user_id")
    if user is not None and (not isinstance(user, str) or not 0 < len(user) <= 500):
        raise InvalidInput("forwardedProps.user_id must be a string of 1-500 characters")
    labels = props.get("metadata") or {}
    if (
        not isinstance(labels, dict)
        or len(labels) > 20
        or not all(isinstance(k, str) and isinstance(v, str) and len(v) <= 200 for k, v in labels.items())
    ):
        raise InvalidInput("forwardedProps.metadata must map at most 20 names to strings of up to 200 characters")
    return user, labels


# -------------------------------------------------------------------------------------------- pauses ----
async def _paused(request: Request, session: AsyncSession, dep: Deployment, thread: Thread) -> list[dict[str, Any]]:
    """What the conversation's last run waits for, from any server (its row keeps the pauses: E-2)."""
    if thread.status != "interrupted":
        return []
    q = select(Run.id).where(Run.thread_id == thread.id).order_by(Run.started_at.desc(), Run.id.desc()).limit(1)
    last = await session.scalar(q)
    found = await ResultRepo(session, request.app.state.runner).of(last, dep.id) if last else None
    return found.interrupts if found is not None and found.status == "interrupted" else []


def _answer(pending: dict[str, Any], entry: ResumeEntry) -> Any:
    """The resume value of one entry, in the shape the pause's `responseSchema` asked for."""
    iid, kind = str(pending.get("id")), pending.get("kind")
    deciding = kind in ("approval", "tool_approval")
    if entry.status == "cancelled":
        if deciding:  # abandoned: a decision that it may not go ahead
            return answer_value(pending, False, "cancelled by the application", None)
        raise InvalidInput(f"interrupt '{iid}' asks a question: answer it (status 'resolved'); it cannot be cancelled")
    p = entry.payload
    if deciding:
        if kind == "tool_approval" and isinstance(p, dict) and isinstance(p.get("decisions"), list):
            return p  # the decisions themselves, one per call (LangChain's shape)
        if isinstance(p, bool):
            p = {"approve": p}
        elif isinstance(p, dict) and "approve" not in p and isinstance(p.get("approved"), bool):
            p = {**p, "approve": p["approved"]}
        content = p
    else:
        flat = mcp_input._flat(_obj(pending.get("payload")).get("response_schema"))
        content = p if isinstance(p, dict) and (flat or "answer" in p) else {"answer": p}
        if p is None:
            content = {}
    outcome, value = mcp_input.answer(
        pending, {"action": "accept", "content": content}, lambda ok, why: answer_value(pending, ok, why, None)
    )
    if outcome != "resume":
        schema = mcp_input.form(pending, "")["params"]["requestedSchema"]
        raise InvalidInput(f"the answer to interrupt '{iid}' does not fit its responseSchema {json.dumps(schema)}")
    return value


def _tool_result(message: ToolMessage) -> dict[str, Any]:
    return {"content": _text_of(message.content), "error": message.error}


def _resume(
    body: RunAgentInput, pending: list[dict[str, Any]], warnings: list[str]
) -> tuple[dict[str, Any], list[str]]:
    """The answers `body` gives to the pauses (interrupt id -> value) and the ids of the decisions among them. Every
    pause must be answered (AG-UI: a resume covers every interrupt): a person's by a resume entry, a tool call the
    application runs by its tool message."""
    by_id = {str(i.get("id")): i for i in pending}
    answers: dict[str, Any] = {}
    decided: list[str] = []
    for entry in body.resume or []:
        found = by_id.get(entry.interrupt_id)
        if found is None or found.get("kind") == CLIENT_TOOL:
            warnings.append(f"resume entry for interrupt '{entry.interrupt_id[:80]}' ignored: nothing waits for it")
            continue
        if entry.interrupt_id in answers:
            raise InvalidInput(f"interrupt '{entry.interrupt_id}' is answered twice")
        answers[entry.interrupt_id] = _answer(found, entry)
        decided.append(entry.interrupt_id)
    results = {m.tool_call_id: m for m in body.messages if isinstance(m, ToolMessage)}
    for iid, i in by_id.items():
        payload = _obj(i.get("payload"))
        if i.get("kind") == CLIENT_TOOL and (m := results.get(str(payload.get("tool_call_id")))) is not None:
            answers[iid] = _tool_result(m)
    missing = [i for i in pending if str(i.get("id")) not in answers]
    if pending and missing:
        asks = [str(i.get("id")) for i in missing if i.get("kind") != CLIENT_TOOL]
        calls = [str(_obj(i.get("payload")).get("tool_call_id")) for i in missing if i.get("kind") == CLIENT_TOOL]
        what = ([f"resume entries for interrupts {', '.join(asks)}"] if asks else []) + (
            [f"tool messages for tool calls {', '.join(calls)}"] if calls else []
        )
        raise InvalidInput(f"this conversation waits for answers: send {' and '.join(what)}")
    return answers, decided


def _interrupt(i: dict[str, Any], wf: Workflow) -> Interrupt:
    """A pause as an AG-UI interrupt: what it asks, the answer's JSON Schema, and AgentCanvas's details."""
    asked = mcp_input.form(i, wf.name)["params"]
    people = _people_decide(i, wf)
    message = asked["message"] + (
        "\n(It is decided by the people its workflow names, in AgentCanvas's Inbox.)" if people else ""
    )
    detail = {"canvas_node_id": i.get("canvas_node_id"), "payload": i.get("payload"), "decided_in_agentcanvas": people}
    return Interrupt(
        id=str(i.get("id")), reason=str(i.get("kind") or "input"), message=message,
        response_schema=asked["requestedSchema"], metadata={agui.META: detail},
    )  # fmt: skip


# ------------------------------------------------------------------------------------------- endpoint ----
@router.post("/deployments/{dep_id}/ag-ui", name="api_ag_ui", response_class=StreamingResponse)
async def api_ag_ui(
    body: RunAgentInput,
    request: Request,
    caller: Caller = Depends(ag_ui_caller),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Run the published version for an AG-UI client (CopilotKit and others): the request is a `RunAgentInput`, the
    answer a stream of AG-UI 1.0 events (SSE). A new `threadId` starts a conversation, a known one continues it;
    `resume` entries and tool messages answer what the last run waits for."""
    dep, wf = caller
    runner = request.app.state.runner
    if not THREAD_ID.match(body.thread_id):
        raise InvalidInput("threadId must be 1-128 letters, digits, '.', '_', ':' or '-'")
    if len(body.messages) > MAX_MESSAGES:
        raise InvalidInput(f"at most {MAX_MESSAGES} messages")
    user_id, labels = _props(body)
    if (who := signed_in(request)) is not None:  # a signed-in person is the run's end user, whatever the props say
        user_id = who.user_id
    tools, context = _client_tools(body)
    warnings: list[str] = []
    thread = await session.get(Thread, body.thread_id)
    mine = bound_end_user(request, dep)
    # ids are one namespace: never say whose it is (another deployment's, or another signed-in person's)
    if thread is not None and (thread.deployment_id != dep.id or (mine is not None and thread.end_user != mine)):
        raise Conflict(f"thread '{body.thread_id}' is taken; start the conversation with another threadId")
    version_id = dep.version_id
    if thread is not None:
        wf, version_id = await _continue_on(session, dep, wf, thread.id)
    adapter = runner.adapter_for(wf)
    if (tools or context) and not supports(adapter, "browser_tools"):
        warnings.append("this deployment's framework cannot offer the application's tools and context to its agents")
        tools, context = [], []
    pending = await _paused(request, session, dep, thread) if thread is not None else []
    answers, decided = _resume(body, pending, warnings)
    for iid in decided:  # the same checks as the deployment API's resume (E-6)
        assert thread is not None  # only a known conversation waits for anything
        await approvers.check_pending(session, thread.id, iid, approvers.CALLER)
    if decided:  # a decision is audited (the plain run calls are not: volume)
        note(request, interrupt_ids=decided, payload_sha256=payload_hash({i: answers[i] for i in decided}))
    for i in pending:  # a tool call being answered is called again: the run must know the tool
        payload = _obj(i.get("payload"))
        if i.get("kind") == CLIENT_TOOL and not any(t["name"] == payload.get("name") for t in tools):
            tools.append({"name": str(payload.get("name")), "description": "", "parameters": None})
    shape = input_shape(wf, None)
    resume: ResumeSpec | None = None
    run_input: dict[str, Any] | None = None
    if answers:
        # LangGraph answers parallel pauses by id; the frameworks that take the application's tools are the ones
        if len(answers) > 1 and not supports(adapter, "browser_tools"):
            raise InvalidInput("this deployment's framework continues one pause at a time; answer its first one")
        iid, value = next(iter(answers.items()))
        resume = ResumeSpec(value, iid) if len(answers) == 1 else ResumeSpec(answers, None, by_id=True)
    else:
        run_input = _run_input(wf, shape, body, fresh=thread is None)
    # a resumed run keeps its conversation's context; identity comes from the key and `user_id`, never the caller
    run_context = (
        None if resume is not None else await _api_context(session, dep, wf, ApiRunIn(thread_id=None, user_id=user_id))
    )
    h = await runner.start(
        wf, thread_id=body.thread_id, input=run_input, resume=resume, context=run_context, deployment_id=dep.id,
        version_id=version_id, labels={**labels, "channel": "ag-ui", "ag_ui_run": body.run_id[:200]},
        caller_run_id=nesting.caller(request.headers.get(nesting.HEADER)),
        end_user=end_user_label(dep.id, user_id) if user_id else None, client_tools=tools, client_context=context,
        visitor_hash=own_files(request),
    )  # fmt: skip
    await session.close()  # no connection held while the run works
    c = contract(wf)
    fields = set(c.input) | set(c.output)
    if shape.chat and shape.field:  # the conversation travels as messages, not as state
        fields.discard(shape.field)
    reducers = {ch.name for ch in wf.state.channels if ch.reducer != "replace"}  # their updates are partial
    shown_input = {k: v for k, v in (run_input or {}).items() if k in fields}
    tr = agui.Translator(
        thread_id=body.thread_id, run_id=body.run_id, agentcanvas_run_id=h.run_id, shown=_answer_steps(wf),
        fields=fields, reducers=reducers, snapshot=shown_input if run_input is not None else None,
        answer=lambda out: answer_text(out, shape), ask=lambda i: _interrupt(i, wf), warnings=warnings,
    )  # fmt: skip

    async def frames() -> AsyncIterator[str]:
        async for ev in marked(runner.subscribe(h.run_id)):
            for out in tr.feed(ev):
                yield agui.encode(out)
        if not tr.ended:  # the run left this server's memory without its last event (it should not)
            for out in tr.feed({"type": "RUN_ERROR", "code": "internal", "message": "the run's events ended early"}):
                yield agui.encode(out)

    return StreamingResponse(
        counted_stream(frames()),
        media_type="text/event-stream",
        headers={**SSE_HEADERS, "x-run-id": h.run_id, "x-thread-id": h.thread_id, **AI_HEADER},
    )
