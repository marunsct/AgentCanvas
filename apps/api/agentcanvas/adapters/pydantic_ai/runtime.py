"""The runtime for workflows designed for Pydantic AI (S32).

Pydantic AI has agents, typed outputs and deferred tools; a workflow's shape is plain async Python (see `codegen`):
one function per block and a `FLOW` that the flow engine (`supersteps`) runs as the canvas draws it. This module adds
the Pydantic AI parts: models (`OpenAIChatModel` over an OpenAI-compatible address), tools (`Tool`, with
`requires_approval` for the tools a person approves first), "Ask AI" and agent steps (`Agent.run`). A tool call that
needs approval ends the agent's run with Pydantic AI's own `DeferredToolRequests`; the message history is saved with
the pause and the agent continues from it with `DeferredToolResults` (approve, approve with edited arguments via
`ToolApproved(override_args=...)`, or `ToolDenied`).

The same module runs inside AgentCanvas (in a worker process with Pydantic AI) and in exports. What differs is the
`Host`, which gives steps their models, tools, knowledge bases and workspace functions.

Only the standard library, `pydantic-ai-slim`, `jsonschema` and `agentcanvas` (vendored into exports) are imported.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults, Tool, ToolApproved, ToolDenied
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai.usage import UsageLimits

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
    """What one step can do: Pydantic AI models, tools and agents, people, knowledge bases and workspace code."""

    _approvals = False  # some tool of this step's agent waits for a person

    # ---- models and tools ----
    def model(self, resource: str, **settings: Any) -> Any:
        """The Pydantic AI model for the canvas model block `resource` (with `temperature`, `max_tokens`)."""
        return self.host.model(resource, self.name, **settings)

    def tools(self, resources: list[str], *, approve: list[str] | None = None) -> list[Tool[Any]]:
        """Pydantic AI tools for the canvas tool blocks `resources`; calls of the tools named in `approve` wait for
        a person (they approve, edit the arguments or reject the call)."""
        found = self.host.tools(resources, self.name)
        self._approvals = self._approvals or any(fn.__name__ in (approve or []) for fn in found)
        return [Tool(fn, requires_approval=fn.__name__ in (approve or [])) for fn in found]

    # ---- AI ----
    async def text(self, agent: Agent[Any, Any], prompt: str) -> str:
        """One answer from `agent` to `prompt` (an "Ask AI" step)."""
        return str((await agent.run(prompt)).output)

    async def agent(
        self,
        agent: Agent[Any, Any],
        messages: Any,
        *,
        output: str = "messages",
        structured: bool = False,
        max_calls: int | None = None,
        stop_at_limit: bool = True,
    ) -> dict[str, Any]:
        """An agent's turn in the conversation `messages`: its answer is appended to `output` (and, with a typed
        output, also written to `structured_response`). Tools that need approval pause the run here. After
        `max_calls` model calls the agent stops with a note (or, `stop_at_limit=False`, the run fails)."""
        limits = UsageLimits(request_limit=max_calls) if max_calls else None
        name = agent.name or self.name
        try:
            out = await self._agent(agent, messages, limits)
        except UsageLimitExceeded as exc:
            if not stop_at_limit:
                raise StopRun("model_call_limit", f"stopped: '{self.name}' asked its model more than {max_calls} "
                              "times") from exc  # fmt: skip
            return self.limit_note(name, max_calls or 0, output=output)
        return self.reply(name, out, output=output, structured=structured)

    async def _agent(self, agent: Agent[Any, Any], messages: Any, limits: UsageLimits | None) -> Any:
        output_type = [agent.output_type, DeferredToolRequests] if self._approvals else None
        saved = json.loads(self._native) if self._native else None
        if saved is not None and (found := self.tool_answers(len(saved["calls"]))) is not None:
            results = DeferredToolResults()
            for call_id, decision in zip(saved["calls"], found, strict=True):
                kind = decision.get("type", "approve")
                if kind in ("approve", "edit"):
                    args = supersteps.edited_args(decision)
                    results.approvals[call_id] = ToolApproved(override_args=args) if args is not None else True
                else:
                    results.approvals[call_id] = ToolDenied(str(decision.get("message") or "Rejected by a person."))
            history = ModelMessagesTypeAdapter.validate_json(saved["history"])
            result = await agent.run(message_history=history, deferred_tool_results=results, output_type=output_type,
                                     usage_limits=limits)  # fmt: skip
        else:
            result = await agent.run(conversation(messages), output_type=output_type, usage_limits=limits)
        if isinstance(result.output, DeferredToolRequests):
            calls = result.output.approvals
            native = json.dumps({"history": ModelMessagesTypeAdapter.dump_json(result.all_messages()).decode(),
                                 "calls": [c.tool_call_id for c in calls]})  # fmt: skip
            raise self.tool_request([(c.tool_name, c.args_as_dict()) for c in calls], native)
        return result.output
