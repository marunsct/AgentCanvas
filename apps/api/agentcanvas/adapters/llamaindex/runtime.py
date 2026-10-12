"""The runtime for workflows designed for LlamaIndex (S35).

A workflow becomes plain async Python (see `codegen`): one function per block, LlamaIndex `FunctionAgent`s for the AI
blocks, and a `FLOW` that the flow engine (`supersteps`) runs as the canvas draws it. This module adds the LlamaIndex
parts: models (an `OpenAILike` LLM over an OpenAI-compatible address), tools, "Ask AI" and agent steps
(`agent.run`), typed answers (`output_cls`) and model call limits (`max_iterations`). A tool a person approves first
waits inside the agent's own workflow the LlamaIndex way: it emits an `InputRequiredEvent` and waits for a
`HumanResponseEvent`. The agent's `Context` (serialized) and its chat memory are saved with the pause; the agent
continues from them when a person has approved, edited or rejected the call. Calls are asked about one at a time.

The same module runs inside AgentCanvas (in a worker process with LlamaIndex) and in exports. What differs is the
`Host`, which gives steps their models, tools, knowledge bases and workspace functions.

Only the standard library, `llama_index`, `jsonschema` and `agentcanvas` (vendored into exports) are imported here.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import AsyncIterator, Callable
from typing import Any

from llama_index.core.agent.workflow import AgentOutput, FunctionAgent
from llama_index.core.llms import ChatMessage
from llama_index.core.memory import ChatMemoryBuffer
from llama_index.core.workflow import Context, HumanResponseEvent, InputRequiredEvent
from workflows.errors import WorkflowRuntimeError

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
    """What one step can do: LlamaIndex models, tools and agents, people, knowledge bases and workspace code."""

    # ---- models and tools ----
    def model(self, resource: str, **settings: Any) -> Any:
        """The LlamaIndex LLM for the canvas model block `resource` (with `temperature`, `max_tokens`)."""
        return self.host.model(resource, self.name, **settings)

    def tools(self, resources: list[str], *, approve: list[str] | None = None) -> list[Any]:
        """Tools for the canvas tool blocks `resources`; calls of the tools named in `approve` wait for a person (they
        approve, edit the arguments or reject the call)."""
        found = self.host.tools(resources, self.name)
        return [ask_before(fn) if fn.__name__ in (approve or []) else fn for fn in found]

    # ---- AI ----
    async def text(self, agent: FunctionAgent, prompt: str) -> str:
        """One answer from `agent` to `prompt` (an "Ask AI" step)."""
        return str(await agent.run(user_msg=prompt)).strip()

    async def agent(
        self,
        agent: FunctionAgent,
        messages: Any,
        *,
        output: str = "messages",
        structured: bool = False,
        max_calls: int | None = None,
        stop_at_limit: bool = True,
    ) -> dict[str, Any]:
        """An agent's turn in the conversation `messages`: its answer is appended to `output` (and, with an
        `output_cls`, also written to `structured_response`). Tools that need approval pause the run here. After
        `max_calls` model calls the agent stops with a note (or, `stop_at_limit=False`, the run fails)."""
        name = agent.name or self.name
        saved = json.loads(self._native) if self._native else None
        if saved is not None and (found := self.tool_answers(1)) is not None:
            handler = agent.run(ctx=Context.from_dict(agent, saved["context"]))
            history = [ChatMessage.model_validate(m) for m in saved["memory"]]
            await handler.ctx.store.set("memory", ChatMemoryBuffer.from_defaults(chat_history=history))
            handler.ctx.send_event(HumanResponseEvent(response=json.dumps(found[0]), call=saved["call"]))
        else:
            kw: dict[str, Any] = {"max_iterations": max_calls} if max_calls is not None else {}
            handler = agent.run(user_msg=conversation(messages), **kw)
        last: AgentOutput | None = None
        try:
            async for ev in handler.stream_events():
                if isinstance(ev, AgentOutput):
                    last = ev
                elif isinstance(ev, InputRequiredEvent) and ev.get("call"):
                    memory = await handler.ctx.store.get("memory")
                    native = json.dumps({"context": handler.ctx.to_dict(), "call": ev.get("call"),
                                         "memory": [m.model_dump(mode="json") for m in memory.get_all()]})  # fmt: skip
                    await handler.cancel_run()
                    raise self.tool_request([(ev.get("tool"), ev.get("args"))], native)
            result: AgentOutput = await handler
        except WorkflowRuntimeError as exc:
            if max_calls is None or "Max iterations" not in str(exc):
                raise
            if last is not None and not last.tool_calls:  # the last allowed call answered: that is the answer
                result = last
            elif not stop_at_limit:
                raise StopRun("model_call_limit", f"stopped: '{self.name}' asked its model more than {max_calls} "
                              "times") from exc  # fmt: skip
            else:
                return self.limit_note(name, max_calls, output=output)
        answer: Any = str(result.response.content or "").strip()
        if structured:
            answer = result.structured_response or _typed(agent, answer)
        return self.reply(name, answer, output=output, structured=structured)


def _typed(agent: FunctionAgent, text: str) -> Any:
    """The answer as the agent's `output_cls` when the model wrote it as JSON text instead of answering LlamaIndex's
    structured output call (else the text)."""
    if agent.output_cls is not None:
        try:
            return agent.output_cls.model_validate_json(text).model_dump(mode="json")
        except ValueError:
            pass
    return text


def ask_before(fn: Callable[..., Any]) -> Callable[..., Any]:
    """`fn` as a tool that waits for a person inside the agent's workflow: it writes an `InputRequiredEvent` to the
    event stream and waits for the `HumanResponseEvent` that approves, edits or rejects the call."""
    name = fn.__name__

    async def call(ctx: Context, **kwargs: Any) -> Any:
        key = f"{name}:{json.dumps(kwargs, sort_keys=True, default=str)}"
        asked = InputRequiredEvent(prefix=f"approve {name}", call=key, tool=name, args=kwargs)
        answer = await ctx.wait_for_event(HumanResponseEvent, waiter_id=key, waiter_event=asked,
                                          requirements={"call": key})  # fmt: skip
        decision = json.loads(answer.response)
        if (args := supersteps.edited_args(decision)) is not None:
            kwargs = args
        elif decision.get("type", "approve") != "approve":
            return f"Rejected by a person: {decision.get('message') or 'no reason given'}"
        out = fn(**kwargs)
        return await out if inspect.isawaitable(out) else out

    params = [inspect.Parameter("ctx", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=Context)]
    params += [p for p in inspect.signature(fn).parameters.values() if p.name != "ctx"]
    call.__name__ = call.__qualname__ = name
    call.__doc__ = fn.__doc__
    call.__signature__ = inspect.Signature(params, return_annotation=str)  # type: ignore[attr-defined]
    call.__annotations__ = {**getattr(fn, "__annotations__", {}), "ctx": Context}
    return call
