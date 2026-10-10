"""The runtime for workflows designed for CrewAI (S27): what a generated CrewAI `Flow` needs to run like the canvas.

A workflow becomes a native CrewAI flow (see `codegen`): a `Flow` subclass with one method per block, chained with
CrewAI's own `@start`, `@listen`, `@router`, `and_` and `or_`, and CrewAI `Agent`s and `LLM`s for the AI blocks. This
module adds what the canvas needs on top of that, through the `@rt.step("<node id>")` decorator on each method:

* events: each step announces when it starts and finishes and what it changed, and the run ends with one
  RUN_FINISHED (or RUN_ERROR) carrying the output fields;
* fields: a step returns the fields it sets, merged into `self.state` with their reducers (append, sum, merge...);
* paths: a path step returns its route as a label, `"<method>:<route>"`, which the following steps listen for;
* people: an approval or a question pauses the run. CrewAI's own `HumanFeedbackPending` stops the flow, and the run
  is saved as a `Paused` value (JSON). Continuing it starts the flow again from the saved fields and replays the
  steps that had finished (their paths come from the record, nothing runs twice) until the asking step gets the
  answer;
* a guard against endless loops: a step that runs more than `recursion_limit` times stops the run.

The same module runs inside AgentCanvas (in a worker process with CrewAI installed) and in exports. What differs is
the `Host`, which gives steps their models, tools, knowledge bases and workspace functions.

Only the standard library, `crewai`, `pydantic`, `jsonschema` and `agentcanvas.expr` (vendored into exports) are
imported here.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import os
import time
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Any, Protocol

os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")  # runs are traced by AgentCanvas (or yours), not CrewAI's
os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

from crewai import LLM, Agent
from crewai.flow import Flow, HumanFeedbackPending, PendingFeedbackContext
from jsonschema import Draft202012Validator

from agentcanvas.expr import eval_condition, render_template

YES_WORDS = {"yes", "y", "true", "approve", "approved", "ok", "okay"}
ROLES = {"human": "user", "user": "user", "ai": "assistant", "assistant": "assistant", "system": "system"}
_MISSING: Any = object()


def render(template: str, state: Any) -> str:
    """A prompt or value with `{{ state.<field> }}` filled in (sandboxed Jinja)."""
    return render_template(template, _values(state))


class Host(Protocol):
    """What the steps of a flow need from where they run."""

    def llm(self, resource: str, step: str) -> LLM:
        """The CrewAI `LLM` for the canvas model block `resource`, used by the step named `step`."""
        ...

    def tools(self, resources: list[str], step: str) -> list[Any]:
        """CrewAI tools for the canvas tool blocks `resources`."""
        ...

    async def search(
        self, knowledge_base: str, query: str, *, mode: str, top_k: int, min_score: float
    ) -> list[dict[str, Any]]: ...

    async def script(self, import_path: str, state: dict[str, Any]) -> dict[str, Any]: ...


@dataclass
class Canvas:
    """What the canvas says about a flow beyond its methods: its fields (reducer, default) and which are its output."""

    flow: type[Flow[Any]]
    name: str
    steps: dict[str, str]  # canvas node id -> block name
    channels: dict[str, tuple[str, Any]]
    outputs: list[str]
    recursion_limit: int = 25
    max_iter: int = 10  # model calls one agent step may make


@dataclass
class Paused:
    """A run waiting for a person: its fields, the steps that had finished and the step that asks (JSON-safe)."""

    values: dict[str, Any]
    node_id: str  # the step that asked
    occurrence: int  # which run of that step (a step in a loop asks once per round)
    done: list[list[Any]]  # [node id, occurrence, route or None] for each finished step, in order
    payload: dict[str, Any]
    interrupt_id: str

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
    """The run cannot go on (a step ran too many times)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def interrupt_kind(payload: dict[str, Any]) -> str:
    return "approval" if payload.get("kind") == "approval" else "input"


def _reduce(reducer: str, old: Any, new: Any) -> Any:
    if reducer in ("append", "add_messages"):
        return [*(old or []), *(new if isinstance(new, list) else [new])]
    if reducer == "sum":
        return (old or 0) + (new or 0)
    if reducer == "merge":
        return {**(old or {}), **(new or {})}
    return new


def _values(state: Any) -> dict[str, Any]:
    if isinstance(state, dict):
        return state
    return state.model_dump() if hasattr(state, "model_dump") else dict(state)


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str)) if isinstance(value, list | dict) else value


# ------------------------------------------------------------------ one run ----
_RUN: contextvars.ContextVar[_Execution] = contextvars.ContextVar("agentcanvas_crewai_run")


class _Execution:
    """The run in progress: emits events, replays finished steps after a pause, hands the answer to the asker."""

    def __init__(self, canvas: Canvas, host: Host, run_id: str, resume: tuple[Paused, Any] | None):
        self.canvas, self.host, self.run_id = canvas, host, run_id
        self.events: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self.seen: Counter[str] = Counter()
        self.done: list[list[Any]] = []
        self.replay: dict[tuple[str, int], str | None] = {}
        self.answer: tuple[str, int, Any] | None = None
        self.paused: Paused | None = None
        if resume is not None:
            paused, answer = resume
            self.done = [list(d) for d in paused.done]
            self.replay = {(d[0], d[1]): d[2] for d in paused.done}
            self.answer = (paused.node_id, paused.occurrence, answer)

    def emit(self, event: dict[str, Any]) -> None:
        self.events.put_nowait(event)

    async def step(self, flow: Flow[Any], node_id: str, fn: Callable[..., Awaitable[Any]], disabled: bool) -> Any:
        self.seen[node_id] += 1
        n = self.seen[node_id]
        if (node_id, n) in self.replay:  # finished before the pause: its fields are in the saved state already
            return self.replay[(node_id, n)]
        name = self.canvas.steps.get(node_id, fn.__name__)
        if n > self.canvas.recursion_limit:
            raise StopRun(
                "recursion_limit", f"stopped: '{name}' ran {self.canvas.recursion_limit} times without reaching END"
            )
        started = time.perf_counter()
        self.emit({"type": "STEP_STARTED", "stepName": name, "canvas_node_id": node_id, "task_id": f"{node_id}#{n}"})
        answer = self.answer[2] if self.answer and self.answer[:2] == (node_id, n) else _MISSING
        run = Run(self, flow, node_id, name, answer, method=fn.__name__)
        try:
            out = None if disabled else await fn(flow, run)
        except Interrupted as intr:
            info = {
                "id": f"{self.run_id}:{node_id}",
                "kind": interrupt_kind(intr.payload),
                "canvas_node_id": node_id,
                "payload": intr.payload,
            }
            self.paused = Paused(
                values=_copy(_values(flow.state)), node_id=node_id, occurrence=n, done=list(self.done),
                payload=intr.payload, interrupt_id=info["id"],
            )  # fmt: skip
            self.emit({
                "type": "STEP_FINISHED", "stepName": name, "canvas_node_id": node_id, "status": "interrupted",
                "task_id": f"{node_id}#{n}", "duration_ms": _ms(started),
            })  # fmt: skip
            self.emit({"type": "CUSTOM", "name": "ac.interrupt", "value": info})
            raise HumanFeedbackPending(
                context=PendingFeedbackContext(
                    flow_id=str(_values(flow.state).get("id", self.run_id)),
                    flow_class=type(flow).__name__,
                    method_name=fn.__name__,
                    method_output=None,
                    message=str(intr.payload.get("prompt") or "approve?"),
                    metadata={"canvas": info},
                )
            ) from None
        except Exception:
            self.emit({
                "type": "STEP_FINISHED", "stepName": name, "canvas_node_id": node_id, "status": "error",
                "task_id": f"{node_id}#{n}", "duration_ms": _ms(started),
            })  # fmt: skip
            raise
        route = out if isinstance(out, str) else None
        update = out if isinstance(out, dict) else {}
        state = flow.state
        for k, v in update.items():
            state[k] = _reduce(self.canvas.channels.get(k, ("replace", None))[0], state.get(k), v)
        self.done.append([node_id, n, route])
        self.emit({
            "type": "STEP_FINISHED", "stepName": name, "canvas_node_id": node_id, "status": "ok",
            "task_id": f"{node_id}#{n}", "duration_ms": _ms(started),
        })  # fmt: skip
        if update:
            self.emit({"type": "STATE_DELTA", "canvas_node_id": node_id, "delta": _copy(update)})
        return route


def step(node_id: str, *, disabled: bool = False) -> Callable[[Callable[..., Awaitable[Any]]], Any]:
    """Make `async def method(self, run)` a step of the canvas: events, field merging, pauses and replay. Put it
    under CrewAI's `@start`/`@listen`/`@router`. The method returns the fields it sets (a path step: its label)."""

    def decorate(fn: Callable[..., Awaitable[Any]]) -> Any:
        async def method(self: Flow[Any], *_trigger: Any) -> Any:  # CrewAI passes what triggered it: not needed
            return await _RUN.get().step(self, node_id, fn, disabled)

        # not functools.wraps: CrewAI reads the signature to decide what to pass, and must see `(self, *_trigger)`
        method.__name__, method.__qualname__, method.__doc__ = fn.__name__, fn.__qualname__, fn.__doc__
        method.__module__ = fn.__module__
        method.canvas_node_id = node_id  # type: ignore[attr-defined]
        return method

    return decorate


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


async def run(
    canvas: Canvas,
    host: Host,
    *,
    input: dict[str, Any] | None = None,
    resume: tuple[Paused, Any] | None = None,
    run_id: str = "run",
) -> AsyncIterator[dict[str, Any]]:
    """Run the flow from the start with `input`, or continue a paused run with a person's answer. Yields AG-UI style
    events; the last one is RUN_FINISHED (outcome success or interrupt; a pause carries `paused`) or RUN_ERROR."""
    ex = _Execution(canvas, host, run_id, resume)
    if resume is not None:
        values = _copy(resume[0].values)
    else:
        values = {k: _copy(default) for k, (_, default) in canvas.channels.items()}
        values.update(input or {})
    values.pop("id", None)
    flow = canvas.flow(suppress_flow_events=True, max_method_calls=max(100, canvas.recursion_limit + 1))
    token = _RUN.set(ex)
    outcome: dict[str, Any] = {}

    async def go() -> None:
        try:
            await flow.kickoff_async(inputs=values)
        except StopRun as exc:
            outcome["error"] = {"type": "RUN_ERROR", "code": exc.code, "message": str(exc)}
        except Exception as exc:  # noqa: BLE001 - every failure ends the run as RUN_ERROR
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
    out = outputs(canvas, flow.state)
    if "error" in outcome:
        yield outcome["error"]
    elif ex.paused is not None:
        info = {"id": ex.paused.interrupt_id, "kind": interrupt_kind(ex.paused.payload),
                "canvas_node_id": ex.paused.node_id, "payload": ex.paused.payload}  # fmt: skip
        yield {
            "type": "RUN_FINISHED", "outcome": "interrupt", "breakpoint": False, "interrupts": [info],
            "next": [ex.paused.node_id], "checkpoint_id": None, "output": out, "paused": ex.paused,
        }  # fmt: skip
    else:
        yield {
            "type": "RUN_FINISHED", "outcome": "success", "breakpoint": False, "interrupts": [], "next": [],
            "checkpoint_id": None, "output": out,
        }  # fmt: skip


def outputs(canvas: Canvas, state: Any) -> dict[str, Any]:
    values = _values(state)
    return {k: _copy(values[k]) for k in canvas.outputs if k in values}


# ------------------------------------------------------------------ steps ----
class Run:
    """What one step of a flow can do: call models and agents, pick a path, ask a person, search, run code."""

    def __init__(
        self, ex: _Execution, flow: Flow[Any], node_id: str, name: str, answer: Any = _MISSING, method: str = ""
    ):
        self._ex, self.flow, self.node_id, self.name, self._answer = ex, flow, node_id, name, answer
        self.method = method or name

    # ---- models and tools ----
    def llm(self, resource: str, **settings: Any) -> LLM:
        llm = self._ex.host.llm(resource, self.name)
        for k, v in settings.items():
            setattr(llm, k, v)
        return llm

    def tools(self, resources: list[str]) -> list[Any]:
        return self._ex.host.tools(resources, self.name)

    @property
    def max_iter(self) -> int:
        return self._ex.canvas.max_iter

    # ---- AI ----
    async def text(self, llm: LLM, prompt: str) -> str:
        """One answer from the model to `prompt` (an "Ask AI" step)."""
        return str(await llm.acall(prompt))

    async def agent(
        self, agent: Agent, messages: Any, *, output: str = "messages", response_format: Any = None
    ) -> dict[str, Any]:
        """An agent's turn in the conversation `messages`: its answer is appended to `output` (and, with a response
        format, also written to `structured_response`)."""
        result = await agent.kickoff_async(to_messages(messages), response_format=response_format)
        reply = {"type": "ai", "role": "assistant", "content": str(result.raw), "name": agent.role}
        data = getattr(result, "pydantic", None)
        if response_format is not None and data is not None:
            dumped = data.model_dump(mode="json")
            reply["content"] = json.dumps(dumped, ensure_ascii=False)
            return {output: [reply], "structured_response": dumped}
        return {output: [reply]}

    async def crew(self, crew: Any, *, output: str) -> dict[str, Any]:
        """A CrewAI crew's work (its tasks, in order or managed): the final task's answer lands in `output`."""
        result = await crew.kickoff_async()
        return {output: str(result.raw)}

    # ---- paths ----
    def route(self, cases: list[tuple[str, str]], default: str = "default") -> str:
        """The label of the first route whose CEL rule matches the fields (else `default`)."""
        values = _values(self.flow.state)
        chosen = next((r for when, r in cases if eval_condition(when, values)), default)
        return f"{self.method}:{chosen}"

    # ---- people ----
    def approval(self, *, show: str, output: str) -> dict[str, Any]:
        """Pause until a person approves or rejects `show` (and maybe edits it)."""
        if self._answer is _MISSING:
            value = _values(self.flow.state).get(show)
            raise Interrupted({"kind": "approval", "canvas_node_id": self.node_id, "value": value})
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
        text = render(prompt, self.flow.state)
        request: dict[str, Any] = {"kind": "input", "canvas_node_id": self.node_id, "prompt": text}
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
        state = {k: v for k, v in _values(self.flow.state).items() if k != "id"}
        return dict(await self._ex.host.script(import_path, _copy(state)) or {})


# ------------------------------------------------------------------ helpers ----
def to_messages(messages: Any) -> Any:
    """A conversation as CrewAI's messages: canvas, LangChain-style and OpenAI-style messages all work."""
    if isinstance(messages, str):
        return messages
    items = []
    for given in messages or []:
        if isinstance(given, str):
            items.append({"role": "user", "content": given})
            continue
        m = given if isinstance(given, dict) else {"type": getattr(given, "type", "human"),
                                                   "content": getattr(given, "content", str(given))}  # fmt: skip
        role = ROLES.get(str(m.get("role") or m.get("type") or "user"))
        content = m.get("content")
        if role is None or content in (None, ""):
            continue  # tool messages and empty tool-call turns: the agent keeps its own within a run
        if not isinstance(content, str):
            content = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
        items.append({"role": role, "content": content})
    return items or ""
