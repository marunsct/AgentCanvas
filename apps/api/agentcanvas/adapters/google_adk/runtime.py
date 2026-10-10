"""The runtime for workflows designed for Google ADK (S29): what a generated ADK `Workflow` needs to run as the canvas
does.

A workflow becomes a native ADK 2 graph workflow (see `codegen`): one function node per block, joined by ADK's own
edges (`("START", a, b)`, route dicts for paths, a `JoinNode` where the canvas waits for every branch), and ADK
`Agent`s with `LiteLlm` models for the AI blocks, run as dynamic nodes (`ctx.run_node`). This module adds what the
canvas needs through the `@rt.step("<node id>")` decorator on each function:

* events: each step announces when it starts and finishes and what it changed, and the run ends with one
  RUN_FINISHED (or RUN_ERROR) carrying the output fields;
* fields: a step returns the fields it sets, merged with their reducers (append, sum, merge...) into the run's
  values, and passed on to ADK's session state as the step's `Event(state=...)`;
* paths: a path step returns its route, emitted as ADK's `Event(route=...)`;
* people: an approval or a question yields ADK's `RequestInput`, which pauses the workflow; the run is saved as a
  `Paused` value (JSON). Continuing it starts the workflow again from the saved fields and replays the steps that had
  finished (their routes come from the record, nothing runs twice) until the asking step gets the answer;
* a guard against endless loops: a step that runs more than `recursion_limit` times stops the run.

The same module runs inside AgentCanvas (in a worker process with ADK installed) and in exports. What differs is the
`Host`, which gives steps their models, tools, knowledge bases and workspace functions.

Only the standard library, `google-adk`, `pydantic`, `jsonschema` and `agentcanvas.expr` (vendored into
exports) are imported here.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import inspect
import json
import os
import time
import uuid
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

os.environ.setdefault("OTEL_SDK_DISABLED", "true")  # runs are traced by AgentCanvas (or yours)
os.environ.setdefault("LITELLM_LOG", "ERROR")

from google.adk import Agent, Context, Event, Runner, Workflow
from google.adk.events import RequestInput
from google.adk.models import BaseLlm, LlmRequest
from google.adk.sessions import InMemorySessionService
from google.adk.workflow import node as adk_node
from google.genai import types
from jsonschema import Draft202012Validator

from agentcanvas.expr import eval_condition, render_template

YES_WORDS = {"yes", "y", "true", "approve", "approved", "ok", "okay"}
ROLES = {"human": "user", "user": "user", "ai": "assistant", "assistant": "assistant", "system": "system"}
_MISSING: Any = object()


def render(template: str, state: Any) -> str:
    """A prompt or value with `{{ state.<field> }}` filled in (sandboxed Jinja)."""
    return render_template(template, dict(state))


def literal(text: str) -> Callable[[Any], str]:
    """An agent's instructions as they are: ADK would fill `{name}` in a plain string from the session state."""

    def instructions(context: Any) -> str:
        return text

    return instructions


class Host(Protocol):
    """What the steps of a workflow need from where they run."""

    def llm(self, resource: str, step: str, **settings: Any) -> BaseLlm:
        """The ADK model for the canvas model block `resource`, used by the step named `step`."""
        ...

    def tools(self, resources: list[str], step: str) -> list[Any]:
        """ADK tools (plain functions) for the canvas tool blocks `resources`."""
        ...

    async def search(
        self, knowledge_base: str, query: str, *, mode: str, top_k: int, min_score: float
    ) -> list[dict[str, Any]]: ...

    async def script(self, import_path: str, state: dict[str, Any]) -> dict[str, Any]: ...


@dataclass
class Canvas:
    """What the canvas says about a workflow beyond its graph: its fields (reducer, default) and its output."""

    build: Callable[[], Workflow]
    name: str
    steps: dict[str, str]  # canvas node id -> block name
    channels: dict[str, tuple[str, Any]]
    outputs: list[str]
    recursion_limit: int = 25


@dataclass
class Paused:
    """A run waiting for a person: its fields, the steps that had finished and the step that asks (JSON-safe)."""

    values: dict[str, Any]
    node_id: str  # the step that asked
    occurrence: int  # which run of that step (a step in a loop asks once per round)
    done: list[list[Any]]  # [node id, occurrence, route or None] for each finished step, in order
    payload: dict[str, Any]
    interrupt_id: str
    answers: list[Any] = field(default_factory=list)  # earlier answers to the asking step (one per tool call)

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)

    @classmethod
    def from_json(cls, text: str) -> Paused:
        return cls(**json.loads(text))


class Interrupted(Exception):
    """A step waits for a person."""

    def __init__(self, payload: dict[str, Any]):
        super().__init__("interrupted")
        self.payload = payload


class StopRun(Exception):
    """The run cannot go on (a step ran too many times, an agent made too many model calls)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def interrupt_kind(payload: dict[str, Any]) -> str:
    if "action_requests" in payload:
        return "tool_approval"
    return "approval" if payload.get("kind") == "approval" else "input"


def _reduce(reducer: str, old: Any, new: Any) -> Any:
    if reducer in ("append", "add_messages"):
        return [*(old or []), *(new if isinstance(new, list) else [new])]
    if reducer == "sum":
        return (old or 0) + (new or 0)
    if reducer == "merge":
        return {**(old or {}), **(new or {})}
    return new


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str)) if isinstance(value, list | dict) else value


# ------------------------------------------------------------------ one run ----
_RUN: contextvars.ContextVar[_Execution] = contextvars.ContextVar("agentcanvas_adk_run")


class _Execution:
    """The run in progress: its values, events, the record of finished steps, and the answer to a pause."""

    def __init__(self, canvas: Canvas, host: Host, run_id: str, values: dict[str, Any],
                 resume: tuple[Paused, Any] | None):  # fmt: skip
        self.canvas, self.host, self.run_id, self.values = canvas, host, run_id, values
        self.events: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self.seen: Counter[str] = Counter()
        self.done: list[list[Any]] = []
        self.replay: dict[tuple[str, int], str | None] = {}
        self.answers: tuple[str, int, list[Any]] | None = None  # the step that asked, and its answers so far
        # node id, occurrence, payload, interrupt id, the step's answers so far
        self.asked: tuple[str, int, dict[str, Any], str, list[Any]] | None = None
        if resume is not None:
            paused, answer = resume
            self.done = [list(d) for d in paused.done]
            self.replay = {(d[0], d[1]): d[2] for d in paused.done}
            self.answers = (paused.node_id, paused.occurrence, [*paused.answers, answer])

    def emit(self, event: dict[str, Any]) -> None:
        self.events.put_nowait(event)

    async def step(self, ctx: Context, node_id: str, fn: Callable[..., Awaitable[Any]], disabled: bool) -> Any:
        """Run one step: (fields it set, its route) or Interrupted."""
        self.seen[node_id] += 1
        n = self.seen[node_id]
        if (node_id, n) in self.replay:  # finished before the pause: its fields are in the saved values already
            return {}, self.replay[(node_id, n)]
        name = self.canvas.steps.get(node_id, fn.__name__)
        if n > self.canvas.recursion_limit:
            raise StopRun(
                "recursion_limit", f"stopped: '{name}' ran {self.canvas.recursion_limit} times without reaching END"
            )
        started = time.perf_counter()
        self.emit({"type": "STEP_STARTED", "stepName": name, "canvas_node_id": node_id, "task_id": f"{node_id}#{n}"})
        answers = self.answers[2] if self.answers and self.answers[:2] == (node_id, n) else []
        run = Run(self, ctx, node_id, name, answers)
        try:
            try:
                out = None if disabled else await fn(run)
            except Exception as exc:  # noqa: BLE001 - ADK may wrap a pause or a stop: unwrap it, else re-raise
                raise _cause(exc, Interrupted) or _cause(exc, StopRun) or exc from None
        except Interrupted as intr:
            interrupt_id = f"{self.run_id}:{node_id}"
            if self.asked is None:  # one question at a time: the first step that asks is the one continued
                self.asked = (node_id, n, intr.payload, interrupt_id, list(answers))
            self.emit({
                "type": "STEP_FINISHED", "stepName": name, "canvas_node_id": node_id, "status": "interrupted",
                "task_id": f"{node_id}#{n}", "duration_ms": _ms(started),
            })  # fmt: skip
            self.emit({"type": "CUSTOM", "name": "ac.interrupt", "value": {
                "id": interrupt_id, "kind": interrupt_kind(intr.payload), "canvas_node_id": node_id,
                "payload": intr.payload}})  # fmt: skip
            raise
        except Exception:
            self.emit({
                "type": "STEP_FINISHED", "stepName": name, "canvas_node_id": node_id, "status": "error",
                "task_id": f"{node_id}#{n}", "duration_ms": _ms(started),
            })  # fmt: skip
            raise
        route = out if isinstance(out, str) else None
        update = out if isinstance(out, dict) else {}
        merged = {}
        for k, v in update.items():
            merged[k] = self.values[k] = _reduce(self.canvas.channels.get(k, ("replace", None))[0],
                                                 self.values.get(k), v)  # fmt: skip
        self.done.append([node_id, n, route])
        self.emit({
            "type": "STEP_FINISHED", "stepName": name, "canvas_node_id": node_id, "status": "ok",
            "task_id": f"{node_id}#{n}", "duration_ms": _ms(started),
        })  # fmt: skip
        if update:
            self.emit({"type": "STATE_DELTA", "canvas_node_id": node_id, "delta": _copy(update)})
        return _copy(merged), route


def step(node_id: str, *, disabled: bool = False) -> Any:
    """Make `async def fn(run)` a node of an ADK workflow that runs as the canvas does: events, field merging, paths,
    pauses and replay. The function returns the fields it sets (a path step: its route)."""

    def decorate(fn: Callable[..., Awaitable[Any]]) -> Any:
        async def canvas_step(ctx: Context, node_input: Any = None) -> AsyncIterator[Any]:
            try:
                update, route = await _RUN.get().step(ctx, node_id, fn, disabled)
            except Interrupted as intr:
                yield RequestInput(message=str(intr.payload.get("prompt") or "Approve?"), payload=intr.payload)
                return
            if update or route is not None:
                yield Event(state=update or None, route=route)

        canvas_step.__name__ = canvas_step.__qualname__ = fn.__name__
        canvas_step.__doc__ = fn.__doc__
        # rerun_on_resume: agents run as dynamic child nodes (ctx.run_node), which ADK requires it for
        return adk_node(canvas_step, name=fn.__name__, rerun_on_resume=True)

    return decorate


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def _first_text(values: dict[str, Any]) -> str:
    for m in reversed(values.get("messages") or []):
        content = m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
        if isinstance(content, str) and content:
            return content
    return "start"


async def run(
    canvas: Canvas,
    host: Host,
    *,
    input: dict[str, Any] | None = None,
    resume: tuple[Paused, Any] | None = None,
    run_id: str = "run",
) -> AsyncIterator[dict[str, Any]]:
    """Run the workflow from the start with `input`, or continue a paused run with a person's answer. Yields AG-UI
    style events; the last one is RUN_FINISHED (outcome success or interrupt; a pause carries `paused`) or
    RUN_ERROR."""
    if resume is not None:
        values = _copy(resume[0].values)
    else:
        values = {k: _copy(default) for k, (_, default) in canvas.channels.items()}
        values.update(_copy(input or {}))
    ex = _Execution(canvas, host, run_id, values, resume)
    token = _RUN.set(ex)
    outcome: dict[str, Any] = {}
    app = "".join(c if c.isalnum() else "_" for c in canvas.name).strip("_") or "canvas"

    async def go() -> None:
        try:
            sessions = InMemorySessionService()
            runner = Runner(app_name=app, node=canvas.build(), session_service=sessions)
            session = await sessions.create_session(app_name=app, user_id="canvas", state=_copy(values))
            message = types.Content(role="user", parts=[types.Part(text=_first_text(values))])
            async for _ in runner.run_async(user_id="canvas", session_id=session.id, new_message=message):
                pass  # the steps report through `ex.events`
        except StopRun as exc:
            outcome["error"] = {"type": "RUN_ERROR", "code": exc.code, "message": str(exc)}
        except Exception as exc:  # noqa: BLE001 - every failure ends the run as RUN_ERROR
            stop = _cause(exc, StopRun)
            if stop is not None:
                outcome["error"] = {"type": "RUN_ERROR", "code": stop.code, "message": str(stop)}
            else:
                outcome["error"] = {"type": "RUN_ERROR", "code": "error", "message": f"{type(exc).__name__}: {exc}"}
        finally:
            ex.events.put_nowait(None)

    task = asyncio.create_task(go())
    try:
        while (ev := await ex.events.get()) is not None:
            yield ev
    finally:
        _RUN.reset(token)
        if not task.done():
            task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    out = outputs(canvas, ex.values)
    if "error" in outcome:
        yield outcome["error"]
    elif ex.asked is not None:
        node_id, occurrence, payload, interrupt_id, answers = ex.asked
        paused = Paused(values=_copy(ex.values), node_id=node_id, occurrence=occurrence, done=list(ex.done),
                        payload=payload, interrupt_id=interrupt_id, answers=_copy(answers))  # fmt: skip
        info = {"id": interrupt_id, "kind": interrupt_kind(payload), "canvas_node_id": node_id, "payload": payload}
        yield {
            "type": "RUN_FINISHED", "outcome": "interrupt", "breakpoint": False, "interrupts": [info],
            "next": [node_id], "checkpoint_id": None, "output": out, "paused": paused,
        }  # fmt: skip
    else:
        yield {
            "type": "RUN_FINISHED", "outcome": "success", "breakpoint": False, "interrupts": [], "next": [],
            "checkpoint_id": None, "output": out,
        }  # fmt: skip


def _cause[E: BaseException](exc: BaseException, kind: type[E]) -> E | None:
    """An exception of `kind` raised inside ADK, which may wrap it (or group it with others)."""
    seen: set[int] = set()
    todo: list[BaseException] = [exc]
    while todo:
        e = todo.pop()
        if id(e) in seen:
            continue
        seen.add(id(e))
        if isinstance(e, kind):
            return e
        todo += [x for x in (e.__cause__, e.__context__) if x is not None]
        todo += list(getattr(e, "exceptions", ()))
        error = getattr(e, "error", None)
        if isinstance(error, BaseException):
            todo.append(error)
    return None


def outputs(canvas: Canvas, values: dict[str, Any]) -> dict[str, Any]:
    return {k: _copy(values[k]) for k in canvas.outputs if k in values}


# ------------------------------------------------------------------ steps ----
class Run:
    """What one step can do: call models and agents, pick a path, ask a person, search, run code."""

    def __init__(self, ex: _Execution, ctx: Context, node_id: str, name: str, answers: list[Any] | None = None):
        self._ex, self.ctx, self.node_id, self.name = ex, ctx, node_id, name
        self._answers = list(answers or [])  # a person's answers to this step, oldest first
        self._answer = self._answers[-1] if self._answers else _MISSING  # an approval or question: the latest
        self._tool_answers = iter(self._answers)  # tool approvals: one answer per call, in order

    @property
    def state(self) -> dict[str, Any]:
        """The run's fields as this step sees them."""
        return self._ex.values

    # ---- models and tools ----
    def llm(self, resource: str, **settings: Any) -> BaseLlm:
        return self._ex.host.llm(resource, self.name, **settings)

    def tools(self, resources: list[str], *, approve: list[str] | None = None) -> list[Any]:
        """ADK tools for the canvas tool blocks `resources`; the calls of the tools named in `approve` wait for a
        person (they approve, edit the arguments or reject the call)."""
        found = self._ex.host.tools(resources, self.name)
        return [self._approved(t) if t.__name__ in (approve or []) else t for t in found]

    def _approved(self, tool: Callable[..., Any]) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(tool)
        async def ask_first(**args: Any) -> Any:
            answer = next(self._tool_answers, _MISSING)
            if answer is _MISSING:  # the agent's turn runs again when the person answers, up to this call
                request = {"name": tool.__name__, "args": args, "description": f"{self.name} wants to call "
                           f"{tool.__name__}"}  # fmt: skip
                raise Interrupted({
                    "action_requests": [request], "canvas_node_id": self.node_id,
                    "review_configs": [{"action_name": tool.__name__, "allowed_decisions": ["approve", "edit",
                                                                                             "reject"]}],
                })  # fmt: skip
            decision = _decision(answer)
            kind = decision.get("type", "approve")
            if kind not in ("approve", "edit"):
                return str(decision.get("message") or "A person rejected this tool call.")
            if kind == "edit":
                args = (decision.get("edited_action") or {}).get("args") or args
            out = tool(**args)
            return await out if inspect.isawaitable(out) else out

        return ask_first

    def call_limit(self, limit: int) -> Callable[..., None]:
        """A `before_model_callback` that stops the run when an agent asks its model more than `limit` times."""
        calls = [0]

        def check(callback_context: Any, llm_request: Any) -> None:
            calls[0] += 1
            if calls[0] > limit:
                raise StopRun("model_call_limit", f"stopped: '{self.name}' asked its model more than {limit} times")

        return check

    # ---- AI ----
    async def text(self, llm: BaseLlm, prompt: str) -> str:
        """One answer from the model to `prompt` (an "Ask AI" step)."""
        request = LlmRequest(model=llm.model, contents=[types.Content(role="user", parts=[types.Part(text=prompt)])])
        texts: list[str] = []
        async for reply in llm.generate_content_async(request):
            if reply.error_message:
                raise RuntimeError(f"the model failed: {reply.error_message}")
            if reply.content and not reply.partial:
                texts += [p.text for p in reply.content.parts or [] if p.text and not p.thought]
        return "".join(texts)

    async def agent(
        self, agent: Agent, messages: Any, *, output: str = "messages", structured: bool = False
    ) -> dict[str, Any]:
        """An agent's turn in the conversation `messages`, run as a dynamic node of the workflow: its answer is
        appended to `output` (and, with structured output, also written to `structured_response`)."""
        result = await self.ctx.run_node(agent, node_input=conversation(messages))
        if structured and result is not None and not isinstance(result, str):
            data = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
            reply = {"type": "ai", "role": "assistant", "content": json.dumps(data, ensure_ascii=False),
                     "name": agent.name}  # fmt: skip
            return {output: [reply], "structured_response": data}
        text = result if isinstance(result, str) else json.dumps(result, default=str) if result is not None else ""
        return {output: [{"type": "ai", "role": "assistant", "content": text, "name": agent.name}]}

    # ---- paths ----
    def route(self, cases: list[tuple[str, str]], default: str = "default") -> str:
        """The first route whose CEL rule matches the fields (else `default`)."""
        return next((r for when, r in cases if eval_condition(when, self.state)), default)

    # ---- people ----
    def approval(self, *, show: str, output: str) -> dict[str, Any]:
        """Pause until a person approves or rejects `show` (and maybe edits it)."""
        if self._answer is _MISSING:
            raise Interrupted({"kind": "approval", "canvas_node_id": self.node_id, "value": self.state.get(show)})
        decision = self._answer if isinstance(self._answer, dict) else {"approved": self._answer}
        approved = decision.get("approved")
        if isinstance(approved, str):  # "no" / "false" must not read as approval
            approved = approved.strip().lower() in YES_WORDS
        out: dict[str, Any] = {output: bool(approved)}
        if decision.get("value") is not None:
            out[show] = decision["value"]
        return out

    def ask(self, *, prompt: str, output: str, schema: dict[str, Any] | None = None) -> dict[str, Any]:
        """Pause until a person answers `prompt`; an answer that does not fit `schema` is asked again."""
        request: dict[str, Any] = {"kind": "input", "canvas_node_id": self.node_id,
                                   "prompt": render(prompt, self.state)}  # fmt: skip
        if schema:
            request["response_schema"] = schema
        if self._answer is _MISSING:
            raise Interrupted(request)
        if schema:
            problems = sorted(Draft202012Validator(schema).iter_errors(self._answer), key=str)
            if problems:
                raise Interrupted({**request, "error": problems[0].message})
        return {output: self._answer}

    # ---- knowledge and code ----
    async def search(
        self,
        knowledge_base: str,
        query: str,
        *,
        output: str,
        mode: str = "hybrid",
        top_k: int = 5,
        min_score: float = 0.0,
        sources_output: str | None = None,
    ) -> dict[str, Any]:
        found = await self._ex.host.search(knowledge_base, query, mode=mode, top_k=top_k, min_score=min_score)
        out: dict[str, Any] = {output: found}
        if sources_output:
            out[sources_output] = [{k: r.get(k) for k in ("cite", "title", "source", "page")} for r in found]
        return out

    async def script(self, import_path: str) -> dict[str, Any]:
        return dict(await self._ex.host.script(import_path, _copy(self.state)) or {})


# ------------------------------------------------------------------ helpers ----
def _decision(answer: Any) -> dict[str, Any]:
    """A person's answer to one tool call as a decision (approve, edit or reject)."""
    if isinstance(answer, dict) and isinstance(answer.get("decisions"), list):
        found = answer["decisions"][:1] or [False]
        d = found[0]
        return d if isinstance(d, dict) else {"type": "approve" if d else "reject"}
    if isinstance(answer, dict):
        answer = answer.get("approved")
    if isinstance(answer, str):
        answer = answer.strip().lower() in YES_WORDS
    return {"type": "approve" if answer else "reject"}


def conversation(messages: Any) -> str:
    """What an agent is asked: the latest message, after the earlier turns of the conversation if any."""
    if isinstance(messages, str):
        return messages
    turns = []
    for given in messages or []:
        if isinstance(given, str):
            turns.append(("user", given))
            continue
        m = given if isinstance(given, dict) else {"type": getattr(given, "type", "human"),
                                                   "content": getattr(given, "content", str(given))}  # fmt: skip
        role = ROLES.get(str(m.get("role") or m.get("type") or "user"))
        content = m.get("content")
        if role not in ("user", "assistant") or content in (None, ""):
            continue
        if not isinstance(content, str):
            content = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
        turns.append((role, content))
    if not turns:
        return "Go ahead."
    if len(turns) == 1:
        return turns[0][1]
    earlier = "\n".join(f"{role}: {text}" for role, text in turns[:-1])
    return f"The conversation so far:\n{earlier}\n\nThe latest message:\n{turns[-1][1]}"


def new_id() -> str:
    return uuid.uuid4().hex
