"""The runtime for workflows designed for the Microsoft Agent Framework (S33-S34).

A workflow becomes plain async Python (see `codegen`): one function per block, Agent Framework `Agent`s for the AI
blocks, and a `FLOW` that the flow engine (`supersteps`) runs as the canvas draws it. This module adds the Agent
Framework parts: models (an `OpenAIChatCompletionClient` over an OpenAI-compatible address, with the model block's
settings applied by a chat middleware), tools (`@tool(approval_mode="always_require")` for the tools a person approves
first), "Ask AI" and agent steps (`agent.run`). A tool call that needs approval pauses the agent's run the Agent
Framework way: its `AgentSession` and the response holding the approval requests are saved with the pause, and the
agent continues in that session with a function approval response for each call once a person has approved, edited
or rejected it. The Agent Framework runs an approved call as the model made it, so a function middleware (`Decided`)
applies a person's decision to each call by its call id: an edited call runs with their arguments, and a rejected call
answers the model "Rejected by a person: <why>" without running the tool.

The same module runs inside AgentCanvas (in a worker process with the Agent Framework) and in exports. What differs is
the `Host`, which gives steps their models, tools, knowledge bases and workspace functions.

Only the standard library, `agent_framework`, `jsonschema` and `agentcanvas` (vendored into exports) are imported here.
"""

from __future__ import annotations

import functools
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextvars import ContextVar
from typing import Any

from agent_framework import (
    Agent,
    AgentResponse,
    AgentSession,
    ChatContext,
    ChatMiddleware,
    ChatResponse,
    Content,
    FunctionInvocationContext,
    FunctionMiddleware,
    Message,
    tool,
)

from agentcanvas.adapters import supersteps
from agentcanvas.adapters.supersteps import (  # noqa: F401 - what generated flows use as `rt.<name>`
    Flow,
    Host,
    Interrupted,
    Paused,
    Step,
    StopRun,
    conversation,
    interrupt_kind,
    render,
)


async def run(
    flow: Flow,
    host: Host,
    *,
    input: dict[str, Any] | None = None,
    resume: tuple[Paused, Any] | None = None,
    run_id: str = "run",
) -> AsyncIterator[dict[str, Any]]:
    """Run `flow` from the start with `input`, or continue a paused run with a person's answer (see `supersteps`)."""
    async for ev in supersteps.run(flow, host, Run, input=input, resume=resume, run_id=run_id):
        yield ev


class Run(supersteps.Run):
    """What one step can do: Agent Framework models, tools and agents, people, knowledge bases and workspace code."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._approve: set[str] = set()  # tools of this step's agent that wait for a person

    # ---- models and tools ----
    def model(self, resource: str, **settings: Any) -> Any:
        """The Agent Framework chat client for the canvas model block `resource` (with `temperature`,
        `max_tokens`)."""
        return self.host.model(resource, self.name, **settings)

    def tools(self, resources: list[str], *, approve: list[str] | None = None) -> list[Any]:
        """Tools for the canvas tool blocks `resources`; calls of the tools named in `approve` wait for a person (they
        approve, edit the arguments or reject the call)."""
        found = self.host.tools(resources, self.name)
        self._approve |= {fn.__name__ for fn in found if fn.__name__ in (approve or [])}
        return [
            tool(_edited(fn), approval_mode="always_require") if fn.__name__ in self._approve else fn for fn in found
        ]

    # ---- AI ----
    async def text(self, agent: Agent, prompt: str) -> str:
        """One answer from `agent` to `prompt` (an "Ask AI" step)."""
        return (await agent.run(prompt)).text

    async def agent(
        self,
        agent: Agent,
        messages: Any,
        *,
        output: str = "messages",
        structured: bool = False,
        max_calls: int | None = None,
        stop_at_limit: bool = True,
    ) -> dict[str, Any]:
        """An agent's turn in the conversation `messages`: its answer is appended to `output` (and, with a
        `response_format`, also written to `structured_response`). Tools that need approval pause the run here. After
        `max_calls` model calls the agent stops with a note (or, `stop_at_limit=False`, the run fails)."""
        name = agent.name or self.name
        saved = json.loads(self._native) if self._native else None
        found = self.tool_answers(len(saved["calls"])) if saved is not None else None
        resumed = saved if found is not None else None  # continuing after a person decided on the paused calls
        limit = CallLimit(max_calls, used=(resumed or {}).get("used", 0)) if max_calls is not None else None
        middleware: list[Any] = [limit] if limit is not None else []
        try:
            if resumed is not None and found is not None:
                session = AgentSession.from_dict(resumed["session"])
                decided = dict(zip(resumed["calls"], found, strict=True))
                answers, by_call = _answers(AgentResponse.from_dict(resumed["response"]), decided)
                middleware.append(Decided(by_call))
                result = await agent.run(Message("user", answers), session=session, middleware=middleware)
            else:
                session = AgentSession()
                result = await agent.run(conversation(messages), session=session, middleware=middleware)
        except LimitReached as exc:
            if not stop_at_limit:
                raise StopRun("model_call_limit", f"stopped: '{self.name}' asked its model more than {max_calls} "
                              "times") from exc  # fmt: skip
            return self.limit_note(name, max_calls or 0, output=output)
        if result.user_input_requests:
            waiting = [r for r in result.user_input_requests if _call(r)[0] in self._approve]
            native = json.dumps({"session": session.to_dict(), "response": result.to_dict(),
                                 "calls": [r.id for r in waiting], "used": limit.calls if limit else 0},
                                default=str)  # fmt: skip
            raise self.tool_request([_call(r) for r in waiting], native)
        return self.reply(name, result.value if structured else result.text, output=output, structured=structured)


def _answers(
    paused: AgentResponse, decided: dict[str, dict[str, Any]]
) -> tuple[list[Content], dict[str, dict[str, Any]]]:
    """A function approval response for each approval request of the paused run, and a person's decision on each
    call by its call id (for `Decided`). The Agent Framework asks about every call the model made with a tool that
    needs approval; calls of the other tools go ahead. Every call is approved: `Decided` answers a rejected one."""
    answers, by_call = [], {}
    for request in paused.user_input_requests:
        call: Any = request.function_call
        by_call[str(call.call_id)] = decided.get(str(request.id), {"type": "approve"})
        answers.append(request.to_function_approval_response(True))
    return answers, by_call


class LimitReached(Exception):
    pass


class CallLimit(ChatMiddleware):
    """Counts an agent's model calls in one run (`used`: the calls made before the run paused for a person); past
    the limit, the next call ends the run."""

    def __init__(self, limit: int, used: int = 0):
        self.limit, self.calls = limit, used

    async def process(self, context: ChatContext, call_next: Callable[[], Awaitable[None]]) -> None:
        self.calls += 1
        if self.calls > self.limit:
            raise LimitReached(f"more than {self.limit} model calls")
        await call_next()


_EDITED: ContextVar[dict[str, Any] | None] = ContextVar("agentcanvas_edited_args", default=None)


class Decided(FunctionMiddleware):
    """A person's decision on each approved call, by call id: an edited call runs with the arguments they wrote (see
    `_edited`), a rejected call answers the model with their reason instead of running."""

    def __init__(self, decided: dict[str, dict[str, Any]]):
        self.decided = decided

    async def process(self, context: FunctionInvocationContext, call_next: Callable[[], Awaitable[None]]) -> None:
        decision = self.decided.get(str(context.metadata.get("call_id")), {"type": "approve"})
        if decision.get("type", "approve") not in ("approve", "edit"):
            context.result = f"Rejected by a person: {decision.get('message') or 'no reason given'}"
            return
        token = _EDITED.set(supersteps.edited_args(decision))
        try:
            await call_next()
        finally:
            _EDITED.reset(token)


class Settings(ChatMiddleware):
    """A model block's settings (`temperature`, `max_tokens`) on every call of the chat client."""

    def __init__(self, settings: dict[str, Any]):
        self.settings = settings

    async def process(self, context: ChatContext, call_next: Callable[[], Awaitable[None]]) -> None:
        context.options = {**(context.options or {}), **self.settings}
        await call_next()


class Scripted(ChatMiddleware):
    """Scripted replies, as the canvas's mock mode gives them: the n-th model turn of an agent's run gets reply n (the
    last one repeats). A reply is text, or {"content": ..., "tool_calls": [{"name": ..., "args": {...}}]}. Nothing is
    sent to a provider: the chat client is the Agent Framework's own, answered by this middleware (used by exports'
    tests)."""

    def __init__(self, replies: list[Any]):
        self.replies = replies or [""]

    async def process(self, context: ChatContext, call_next: Callable[[], Awaitable[None]]) -> None:
        turn = sum(1 for m in context.messages if m.role == "assistant")
        offered = {getattr(t, "name", None) for t in (context.options or {}).get("tools") or []}
        reply, calls = self._reply(turn)
        while calls and not offered & {c["name"] for c in calls} and turn < len(self.replies) - 1:
            turn += 1  # a model only calls the tools it is offered: the next reply answers instead
            reply, calls = self._reply(turn)
        text = reply.get("content", "") if isinstance(reply, dict) else str(reply)
        contents = [Content.from_text(text)] if text or not calls else []
        contents += [Content.from_function_call(f"mock_{turn}_{i}", c["name"], arguments=c.get("args") or {})
                     for i, c in enumerate(calls)]  # fmt: skip
        context.result = ChatResponse(messages=[Message("assistant", contents)],
                                      finish_reason="tool_calls" if calls else "stop")  # fmt: skip

    def _reply(self, turn: int) -> tuple[Any, list[dict[str, Any]]]:
        reply = self.replies[min(turn, len(self.replies) - 1)]
        return reply, (reply.get("tool_calls") or [] if isinstance(reply, dict) else [])


def _call(request: Content) -> tuple[str, dict[str, Any]]:
    """The tool name and arguments of a function approval request."""
    call: Any = request.function_call
    args = call.arguments
    if isinstance(args, str):
        args = json.loads(args or "{}")
    return str(call.name), dict(args or {})


def _edited(fn: Callable[..., Any]) -> Callable[..., Any]:
    """`fn`, called with the arguments a person wrote when they edited the call (the Agent Framework approves a call
    as the model made it; `Decided` gives this call's edit)."""

    @functools.wraps(fn)
    async def call(*args: Any, **kwargs: Any) -> Any:
        if (edited := _EDITED.get()) is not None:
            args, kwargs = (), edited
        out = fn(*args, **kwargs)
        return await out if hasattr(out, "__await__") else out

    return call
