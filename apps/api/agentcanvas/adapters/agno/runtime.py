"""The runtime for workflows designed for Agno (S32).

A workflow becomes plain async Python (see `codegen`): one function per block, Agno `Agent`s for the AI blocks, and a
`FLOW` that the flow engine (`supersteps`) runs as the canvas draws it. This module adds the Agno parts: models
(`OpenAILike` over an OpenAI-compatible address), tools (Agno's `@tool(requires_confirmation=True)` for the tools a
person approves first), "Ask AI" and agent steps (`Agent.arun`). A tool call that needs approval pauses the agent's
run the Agno way; the paused `RunOutput` is saved with the pause and the agent continues it with `acontinue_run`
once a person has confirmed, edited or rejected each call.

The same module runs inside AgentCanvas (in a worker process with Agno) and in exports. What differs is the `Host`,
which gives steps their models, tools, knowledge bases and workspace functions.

Only the standard library, `agno`, `jsonschema` and `agentcanvas` (vendored into exports) are imported here.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any

os.environ.setdefault("AGNO_TELEMETRY", "false")  # runs are traced by AgentCanvas (or yours)

from agno.agent import Agent
from agno.run.agent import RunOutput
from agno.run.base import RunStatus
from agno.tools import tool

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
    """What one step can do: Agno models, tools and agents, people, knowledge bases and workspace code."""

    # ---- models and tools ----
    def model(self, resource: str, **settings: Any) -> Any:
        """The Agno model for the canvas model block `resource` (with `temperature`, `max_tokens`)."""
        return self.host.model(resource, self.name, **settings)

    def tools(self, resources: list[str], *, approve: list[str] | None = None) -> list[Any]:
        """Agno tools for the canvas tool blocks `resources`; calls of the tools named in `approve` wait for a
        person (they approve, edit the arguments or reject the call)."""
        found = self.host.tools(resources, self.name)
        return [tool(requires_confirmation=True)(fn) if fn.__name__ in (approve or []) else fn for fn in found]

    # ---- AI ----
    async def text(self, agent: Agent, prompt: str) -> str:
        """One answer from `agent` to `prompt` (an "Ask AI" step)."""
        out = _checked(await agent.arun(prompt))
        return str(out.content or "")

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
        """An agent's turn in the conversation `messages`: its answer is appended to `output` (and, with an output
        schema, also written to `structured_response`). Tools that need approval pause the run here. After
        `max_calls` model calls the agent stops with a note (or, `stop_at_limit=False`, the run fails)."""
        name = agent.name or self.name
        calls = _count_calls(agent, max_calls)
        paused = RunOutput.from_dict(json.loads(self._native)) if self._native else None
        found = self.tool_answers(len(_waiting(paused))) if paused is not None else None
        if paused is not None and found is not None:
            for call, decision in zip(_waiting(paused), found, strict=True):
                kind = decision.get("type", "approve")
                call.confirmed = kind in ("approve", "edit")
                if (args := supersteps.edited_args(decision)) is not None:
                    call.tool_args = args
                if not call.confirmed:
                    call.confirmation_note = str(decision.get("message") or "Rejected by a person.")
            result = await agent.acontinue_run(run_response=paused)
        else:
            result = await agent.arun(conversation(messages))
        if max_calls is not None and calls[0] > max_calls:
            if not stop_at_limit:
                raise StopRun("model_call_limit", f"stopped: '{self.name}' asked its model more than {max_calls} times")
            return self.limit_note(name, max_calls, output=output)
        if result.is_paused:
            waiting = _waiting(result)
            native = json.dumps(result.to_dict(), default=str)
            raise self.tool_request([(c.tool_name or "", dict(c.tool_args or {})) for c in waiting], native)
        return self.reply(name, _checked(result).content, output=output, structured=structured)


class _LimitReached(Exception):
    pass


def _count_calls(agent: Agent, limit: int | None) -> list[int]:
    """Count the agent's model calls; past `limit`, the next call ends its run (Agno has no model call limit)."""
    calls = [0]
    model: Any = agent.model
    if limit is None or model is None:
        return calls
    invoke = model.ainvoke

    async def counted(*args: Any, **kwargs: Any) -> Any:
        calls[0] += 1
        if calls[0] > limit:
            raise _LimitReached(f"more than {limit} model calls")
        return await invoke(*args, **kwargs)

    model.ainvoke = counted
    return calls


def _waiting(result: RunOutput) -> list[Any]:
    """The tool calls a paused run waits on a person for, in order."""
    return [t for t in result.tools or [] if t.requires_confirmation and t.confirmed is None]


def _checked(result: RunOutput) -> RunOutput:
    """Agno reports a failed model call as a run with an error status: make it the step's failure."""
    if result.status == RunStatus.error:
        raise RuntimeError(f"the agent failed: {result.content}")
    return result
