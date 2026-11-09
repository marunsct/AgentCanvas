"""The runtime for workflows designed for Strands Agents (S35).

A workflow becomes plain async Python (see `codegen`): one function per block, Strands `Agent`s for the AI blocks,
and a `FLOW` that the flow engine (`supersteps`) runs as the canvas draws it. This module adds the Strands parts:
models (an `OpenAIModel` over an OpenAI-compatible address), tools (`@tool`), the `AskBefore` hook that interrupts a
tool call until a person answers (Strands' own interrupts: `BeforeToolCallEvent.interrupt`), typed answers
(`structured_output_model`) and model call limits (`Limits(turns=...)`). A paused agent's messages and interrupt state
are saved with the pause; the agent continues from them with an `interruptResponse` for each call once a person has
approved, edited or rejected it.

The same module runs inside AgentCanvas (in a worker process with Strands) and in exports. What differs is the `Host`,
which gives steps their models, tools, knowledge bases and workspace functions.

Only the standard library, `strands`, `jsonschema` and `agentcanvas` (vendored into exports) are imported here.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from strands import Agent, tool
from strands.hooks import BeforeToolCallEvent, HookProvider, HookRegistry
from strands.interrupt import _InterruptState  # how Strands' session managers save a pending interrupt
from strands.types.exceptions import StructuredOutputException

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


class AskBefore(HookProvider):
    """Interrupts calls of the tools named `tools` until a person approves, edits or rejects them."""

    def __init__(self, tools: list[str]):
        self.tools = set(tools)

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self.ask)

    def ask(self, event: BeforeToolCallEvent) -> None:
        call: Any = event.tool_use
        if call["name"] not in self.tools:
            return
        asked = {"name": call["name"], "args": dict(call["input"] or {})}
        decision = event.interrupt(f"approve-{call['toolUseId']}", reason=asked)
        decision = decision if isinstance(decision, dict) else {"type": "approve" if decision else "reject"}
        if (args := supersteps.edited_args(decision)) is not None:
            call["input"] = args
        elif decision.get("type", "approve") != "approve":
            event.cancel_tool = f"Rejected by a person: {decision.get('message') or 'no reason given'}"


class Run(supersteps.Run):
    """What one step can do: Strands models, tools and agents, people, knowledge bases and workspace code."""

    # ---- models and tools ----
    def model(self, resource: str, **settings: Any) -> Any:
        """The Strands model for the canvas model block `resource` (with `temperature`, `max_tokens`)."""
        return self.host.model(resource, self.name, **settings)

    def tools(self, resources: list[str]) -> list[Any]:
        """Strands tools for the canvas tool blocks `resources`."""
        return [tool(fn) for fn in self.host.tools(resources, self.name)]

    # ---- AI ----
    async def text(self, agent: Agent, prompt: str) -> str:
        """One answer from `agent` to `prompt` (an "Ask AI" step). Strands refuses a blank prompt (1.63, "Input
        prompt is blank"): say which step and why instead."""
        if not str(prompt).strip():
            raise StopRun("empty_prompt", f"'{self.name}': its prompt is empty once the state is filled in")
        return str(await agent.invoke_async(prompt)).strip()

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
        `structured_output_model`, also written to `structured_response`). Tools behind `AskBefore` pause the run here.
        After `max_calls` model calls the agent stops with a note (or, `stop_at_limit=False`, the run fails)."""
        name = agent.name or self.name
        saved = json.loads(self._native) if self._native else None
        prompt: Any = conversation(messages)
        used, replayed = 0, 0  # model calls made before the run paused; the turn that runs the decided calls
        if saved is not None and (found := self.tool_answers(len(saved["calls"]))) is not None:
            used, replayed = saved.get("used", 0), 1
            agent.messages = saved["messages"]
            agent._interrupt_state = _InterruptState.from_dict(saved["interrupts"])
            prompt = [{"interruptResponse": {"interruptId": i, "response": d}}
                      for i, d in zip(saved["calls"], found, strict=True)]  # fmt: skip
        # Strands limits the turns of one invocation; a resumed one first runs the decided calls (a turn with no
        # model call), and then gets what is left of the agent's budget
        limits: Any = {"turns": max(max_calls - used, 0) + replayed or 1} if max_calls is not None else None
        try:
            result = await agent.invoke_async(prompt, limits=limits)
        except StructuredOutputException:
            return self.reply(name, _answer_text(agent), output=output, structured=structured)
        if result.stop_reason == "interrupt":
            waiting = list(result.interrupts or [])
            native = json.dumps({"messages": agent.messages, "interrupts": agent._interrupt_state.to_dict(),
                                 "calls": [i.id for i in waiting], "used": used + _turns(agent) - replayed},
                                default=str)  # fmt: skip
            raise self.tool_request([(i.reason["name"], i.reason["args"]) for i in waiting], native)
        if result.stop_reason == "limit_turns":
            if not stop_at_limit:
                raise StopRun("model_call_limit", f"stopped: '{self.name}' asked its model more than {max_calls} times")
            return self.limit_note(name, max_calls or 0, output=output)
        answer = result.structured_output if structured and result.structured_output is not None else None
        if answer is None:
            answer = _answer_text(agent) if structured else str(result).strip()
        return self.reply(name, answer, output=output, structured=structured)


def _turns(agent: Agent) -> int:
    """The model calls (Strands' turns) of the agent's latest invocation."""
    invocation = agent.event_loop_metrics.latest_agent_invocation
    return len(invocation.cycles) if invocation is not None else 0


def _answer_text(agent: Agent) -> Any:
    """The agent's last answer as its typed answer when the model wrote it as JSON text instead of calling Strands'
    structured output tool (else the text)."""
    text = ""
    for message in reversed(agent.messages):
        if message.get("role") == "assistant":
            text = "".join(block.get("text", "") for block in message.get("content") or [] if isinstance(block, dict))
            if text:
                break
    model = agent._default_structured_output_model
    if model is not None:
        try:
            return model.model_validate_json(text)
        except ValueError:
            pass
    return text.strip()
