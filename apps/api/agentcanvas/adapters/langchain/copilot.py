"""The Copilot on Deep Agents (PLAN.md §2): the same turn as `copilot.service.CopilotTurn`, run by LangChain's agent
harness instead of our own loop.

What the harness adds over the loop: the whole conversation is sent (summarised by the harness when it grows long,
instead of the last 24 messages), a plan the agent keeps as a to-do list (`write_todos`, streamed to the panel), a
general-purpose sub-agent for side questions (`task`), a scratch filesystem holding the full workflow
(`/workflow.json`, never cut), text streamed as it is generated, and prompt caching for Anthropic models. What stays
ours: the tools (`copilot.tools`, the working copy, the proposal), the instructions and the events the panel reads.
The shell tool the harness offers is never shown to the model.

Selected with `AGENTCANVAS_COPILOT_ENGINE=deepagents`; the loop stays the default until this one has passed the same
suite and the real-model evaluation.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from deepagents import create_deep_agent
from langchain.agents.middleware import AgentMiddleware, ModelCallLimitMiddleware, TodoListMiddleware
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.outputs import LLMResult
from langchain_core.tools import StructuredTool
from langgraph.errors import GraphRecursionError

from agentcanvas.copilot.service import MAX_ERROR, MAX_STEPS, ROLE, CopilotTurn, Mode, _now
from agentcanvas.copilot.skillbook import skills
from agentcanvas.copilot.tools import READ_ONLY, TOOLS, compact_workflow, result_text, run_tool
from agentcanvas.registry import token_counts

SHELL = "execute"  # the harness's shell tool: the Copilot has no shell
STEPS_PER_TURN = MAX_STEPS * 8  # graph steps (model, tools and middleware nodes): a backstop behind the call limit
LIMIT = "ModelCallLimitMiddleware"


# specialists the agent hands a side question to (`task`), each with read-only tools: they look and report; only
# the main agent proposes changes, so a proposal is always one, reviewable, in the person's conversation
SUBAGENTS: list[dict[str, Any]] = [
    {
        "name": "investigator",
        "description": "Finds why a run failed or gave a wrong answer: reads the problems, the recent runs and one run "
        "step by step, and reports the cause with the step and the evidence. Give it the run or the symptom.",
        "system_prompt": "You investigate one AgentCanvas workflow problem. Use get_problems, list_runs and get_run; "
        "report in a few sentences which step is at fault, what went wrong and the evidence (a value, an error). "
        "Do not suggest unrelated changes.",
        "tools": ["get_problems", "list_runs", "get_run", "get_workflow", "get_block"],
    },
    {
        "name": "researcher",
        "description": "Answers how AgentCanvas or a block works, which block or template fits a need, which models "
        "are available, which secrets and knowledge bases the project has, or what an export would need, from the "
        "guide, the block catalog and the project. Give it the question.",
        "system_prompt": "You answer one question about AgentCanvas from its guide (search_guide), its blocks "
        "(find_blocks, get_block), its templates (get_template), models (list_models), the project's secret names "
        "(list_secrets) and knowledge bases (list_knowledge_bases), and what an export holds and lacks "
        "(export_plan). Answer briefly and name the guide page or the block you used.",
        "tools": [
            "search_guide",
            "find_blocks",
            "get_block",
            "get_template",
            "list_models",
            "list_secrets",
            "list_knowledge_bases",
            "export_plan",
            "read_skill",
        ],
    },
]


class _HideTools(AgentMiddleware):
    """Leaves tools out of every model call: the shell, and in Ask mode the ones that change things. A model that
    calls one anyway gets the tool's own refusal, as with the loop."""

    def __init__(self, hidden: set[str]):
        super().__init__()
        self.hidden = hidden

    def _without(self, request: Any) -> Any:
        tools = [t for t in request.tools if getattr(t, "name", None) not in self.hidden]
        return request.override(tools=tools)

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return handler(self._without(request))

    async def awrap_model_call(self, request: Any, handler: Callable[[Any], Awaitable[Any]]) -> Any:
        return await handler(self._without(request))


class DeepAgentTurn(CopilotTurn):
    """One answer to one message on Deep Agents; same constructor, attributes and events as `CopilotTurn`, plus
    `text_delta` (text as it is generated) and `todos` (the agent's plan)."""

    def _tools(self, allowed: list[str], events: list[dict[str, Any]]) -> list[StructuredTool]:
        lock = asyncio.Lock()  # the harness may run tool calls in parallel; the working copy takes one at a time

        def make(name: str) -> Callable[..., Awaitable[str]]:
            async def call(**args: Any) -> str:
                async with lock:
                    result = await run_tool(self.ctx, name, args, allowed)
                    events.append({
                        "type": "tool", "name": name, "ok": bool(result.get("ok")), "error": result.get("error"),
                        "summary": args.get("summary") if isinstance(args.get("summary"), str) else None,
                    })  # fmt: skip
                    events.extend(self.ctx.events)
                    self.ctx.events.clear()
                return result_text(result)

            return call

        return [
            StructuredTool.from_function(
                coroutine=make(n), name=n, description=TOOLS[n].description, args_schema=TOOLS[n].parameters
            )
            for n in TOOLS  # all registered, so a call to one this mode lacks gets run_tool's refusal
        ]

    def _history(self) -> list[Any]:
        out: list[Any] = []
        for m in self.history:
            if m.get("role") == "user" and m.get("content"):
                out.append(HumanMessage(content=m["content"]))
            elif m.get("role") == "assistant" and m.get("content"):
                out.append(AIMessage(content=m["content"]))
        return out

    def _files(self) -> dict[str, Any]:
        """The scratch filesystem the agent starts with: the full workflow (the prompt's copy may be cut) and the
        skills, which the harness lists by name and description and the agent reads when one applies."""

        def file(text: str) -> dict[str, Any]:
            return {"content": text.splitlines(), "created_at": _now(), "modified_at": _now()}

        files = {"/workflow.json": file(json.dumps(compact_workflow(self.before), indent=1))}
        for name, text in skills().items():  # a framework skill's facts come from the running manifests
            files[f"/skills/{name}/SKILL.md"] = file(text)
        return files

    async def run(self, text: str, mode: Mode) -> AsyncIterator[dict[str, Any]]:
        allowed = list(TOOLS) if mode == "build" else READ_ONLY
        events: list[dict[str, Any]] = []
        system = (
            ROLE + "\n" + self.pack.text + "\n\n" + self._context(mode)
            + "\n\nThe full workflow is also in your files as /workflow.json (the copy above may be cut short). "
            "For a task of several steps, keep a short plan with write_todos and tick items off as you go."
        )  # fmt: skip
        tools = self._tools(allowed, events)
        by_name = {t.name: t for t in tools}
        subagents: list[Any] = [{**spec, "tools": [by_name[n] for n in spec["tools"]]} for spec in SUBAGENTS]
        middleware: list[Any] = [
            TodoListMiddleware(),
            ModelCallLimitMiddleware(run_limit=MAX_STEPS, exit_behavior="end"),
            _HideTools({SHELL, *(set(TOOLS) - set(allowed))}),
        ]
        agent = create_deep_agent(
            model=self.resources.model("copilot", {**self.model.config, "model": self.model.model}),
            tools=tools,
            subagents=subagents,
            system_prompt=system,
            middleware=middleware,
            skills=["/skills/"],
        )
        messages = [*self._history(), HumanMessage(content=text)]
        self.history.append({"role": "user", "content": text, "at": _now(), "mode": mode})
        meter = _Meter()
        counted = dict(self.usage)
        said: list[str] = []
        streamed = ""
        yield {"type": "start", "model": self.model.model, "origin": self.model.origin, "engine": "deepagents"}
        state: Any = {"messages": messages, "files": self._files()}
        stream: Any = agent.astream(
            state,
            config={"recursion_limit": STEPS_PER_TURN, "callbacks": [meter]},
            stream_mode=["messages", "updates"],
        )
        try:
            chunk: Any
            async for kind, chunk in stream:
                if kind == "messages":
                    msg, meta = chunk
                    if isinstance(msg, AIMessageChunk) and meta.get("langgraph_node") == "model" and msg.content:
                        piece = _text(msg.content)
                        if piece:
                            streamed += piece
                            yield {"type": "text_delta", "text": piece}
                    continue
                for node, update in (chunk or {}).items():
                    if node.startswith(LIMIT) and update:
                        yield {
                            "type": "error",
                            "message": "the Copilot stopped after too many steps; ask it to continue",
                        }
                    if not isinstance(update, dict):
                        continue
                    if "todos" in update and update["todos"] is not None:
                        yield {"type": "todos", "todos": list(update["todos"])}
                    for msg in update.get("messages") or []:
                        if node == "model" and isinstance(msg, AIMessage):
                            full = _text(msg.content).strip()
                            if full:
                                said.append(full)
                                yield {"type": "text", "text": full}
                            streamed = ""
                        elif isinstance(msg, ToolMessage) and msg.name in ("write_todos", "task"):
                            yield {"type": "tool", "name": msg.name, "ok": msg.status != "error", "error": None,
                                   "summary": None}  # fmt: skip
                for event in events:
                    yield event
                events.clear()
                self._count(meter, counted)
                yield {"type": "usage", "usage": dict(self.usage), "cap": self.token_cap}
                if self._over_cap():
                    yield {"type": "error", "message": "this conversation reached its token limit; start a new chat"}
                    break
        except (asyncio.CancelledError, GeneratorExit):  # Stop, or the connection went: keep what was said
            self.stopped([*said, streamed.strip()] if streamed.strip() else said)
            raise
        except GraphRecursionError:
            yield {"type": "error", "message": "the Copilot stopped after too many steps; ask it to continue"}
        except Exception as exc:  # noqa: BLE001 - a provider error ends the turn with a readable message
            yield {"type": "error", "message": f"the model call failed: {type(exc).__name__}: {exc}"[:MAX_ERROR]}
        finally:
            await stream.aclose()
            self._count(meter, counted)
        for event in events:
            yield event
        if streamed.strip() and (not said or said[-1] != streamed.strip()):  # stopped while a reply was streaming
            said.append(streamed.strip())
        async for event in self._end(said):
            yield event

    def _count(self, meter: _Meter, before: dict[str, int]) -> None:
        """This turn's tokens (every model call, sub-agents' too) on top of what the conversation had used."""
        for k, v in meter.totals.items():
            self.usage[k] = before.get(k, 0) + v


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # content blocks: keep the text ones
        return "".join(b.get("text", "") if isinstance(b, dict) and b.get("type") == "text" else "" for b in content)
    return str(content or "")


class _Meter(BaseCallbackHandler):
    """Tokens of every model reply in a turn. LangChain's UsageMetadataCallbackHandler counts only replies that name
    their model in `response_metadata["model_name"]`, which some providers (Ollama, OpenAI-compatible servers) and
    test models leave out: their tokens went uncounted, so the token cap and the cost under-read."""

    def __init__(self) -> None:
        super().__init__()
        self.totals = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        for generations in response.generations:
            for g in generations:
                usage = getattr(getattr(g, "message", None), "usage_metadata", None)
                for k, n in token_counts(usage).items():
                    self.totals[k] += n
