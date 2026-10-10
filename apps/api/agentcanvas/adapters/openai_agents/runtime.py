"""The flow runtime for workflows designed for the OpenAI Agents SDK (S25).

The SDK has agents, tools and handoffs but no graph, so a workflow becomes plain Python (see `codegen`): one `Agent`
per AI block, one async function per block, and a `Flow` that names the steps and what follows each. This module
runs that `Flow` in supersteps, the way the canvas draws it: steps side by side run in the same round, a "wait for
all" step waits for every branch, paths follow the first CEL rule that matches, and fields merge with their
reducers. AI steps are `Runner.run` calls; a tool that needs approval pauses the run with the SDK's own `RunState`,
which is saved with the pause and approved or rejected when a person answers.

A paused run is a `Paused` value that turns into JSON and back, so the caller decides where it lives (AgentCanvas
keeps it in memory, an exported project in a file). The same module runs inside AgentCanvas and in exports: what
differs is the `Host`, which gives steps their models, tools, knowledge bases and workspace functions.

Only the standard library, `agents`, `jsonschema` and `agentcanvas.expr` (vendored into exports) are imported here.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from agents import Agent, MaxTurnsExceeded, Model, Runner, RunState, set_tracing_disabled
from jsonschema import Draft202012Validator

from agentcanvas.expr import eval_condition, render_template

set_tracing_disabled(True)  # runs are traced by AgentCanvas (or your own tracer), never sent to OpenAI's

YES_WORDS = {"yes", "y", "true", "approve", "approved", "ok", "okay"}
ROLES = {"human": "user", "user": "user", "ai": "assistant", "assistant": "assistant", "system": "system"}
_MISSING: Any = object()


def render(template: str, state: dict[str, Any]) -> str:
    """A prompt or value with `{{ state.<field> }}` filled in (sandboxed Jinja)."""
    return render_template(template, state)


class Host(Protocol):
    """What the steps of a flow need from where they run."""

    def model(self, resource: str, step: str) -> Any:
        """The agents `Model` for the canvas model block `resource`, used by the step named `step`."""
        ...

    def tools(self, resources: list[str], approve: list[str], step: str) -> list[Any]:
        """Agents tools for the canvas tool blocks `resources`; those named in `approve` need a person's yes."""
        ...

    async def search(
        self, knowledge_base: str, query: str, *, mode: str, top_k: int, min_score: float
    ) -> list[dict[str, Any]]: ...

    async def script(self, import_path: str, state: dict[str, Any]) -> dict[str, Any]: ...


class FallbackModel(Model):
    """A model block with backup models (S26): a call that fails goes to the next model in order, and the first
    answer wins. A streamed answer fails over only before its first event."""

    def __init__(self, models: list[Any]):
        self.models = models

    async def get_response(self, *args: Any, **kwargs: Any) -> Any:
        for i, model in enumerate(self.models):
            try:
                return await model.get_response(*args, **kwargs)
            except Exception:
                if i == len(self.models) - 1:
                    raise
        raise AssertionError("unreachable")

    async def stream_response(self, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        for i, model in enumerate(self.models):
            started = False
            try:
                async for event in model.stream_response(*args, **kwargs):
                    started = True
                    yield event
                return
            except Exception:
                if started or i == len(self.models) - 1:
                    raise


class Interrupted(Exception):
    """A step waits for a person. `native` is what the step needs to continue (an agent's saved `RunState`)."""

    def __init__(self, payload: dict[str, Any], native: str | None = None):
        super().__init__("interrupted")
        self.payload = payload
        self.native = native


StepFn = Callable[[dict[str, Any], "Run"], Awaitable[dict[str, Any]]]


@dataclass
class Step:
    name: str
    type: str
    run: StepFn | None = None  # None: a path (router) or "wait for all" (join), which change nothing
    cases: list[tuple[str, str]] = field(default_factory=list)  # router: (CEL rule, route), first match wins
    default: str = "default"
    disabled: bool = False


@dataclass
class Flow:
    name: str
    steps: dict[str, Step]  # canvas node id -> step
    start: list[str]
    next: dict[str, list[str] | dict[str, list[str]]]  # node id -> following ids (a router: per route)
    channels: dict[str, tuple[str, Any]]  # field -> (reducer, default)
    outputs: list[str]
    recursion_limit: int = 25
    max_turns: int = 10  # model calls one agent step may make

    def following(self, node_id: str, values: dict[str, Any]) -> list[str]:
        nxt = self.start if node_id == "START" else self.next.get(node_id, [])
        if isinstance(nxt, dict):
            step = self.steps[node_id]
            route = next((r for when, r in step.cases if eval_condition(when, values)), step.default)
            return list(nxt.get(route, []))
        return list(nxt)

    def before(self, node_id: str) -> set[str]:
        out = {src for src, nxt in self.next.items() for ids in _targets(nxt) if node_id in ids}
        return out | ({"START"} if node_id in self.start else set())


def _targets(nxt: list[str] | dict[str, list[str]]) -> list[list[str]]:
    return list(nxt.values()) if isinstance(nxt, dict) else [nxt]


@dataclass
class Paused:
    """A run waiting for a person: where it stopped and what it had done (JSON-safe)."""

    values: dict[str, Any]
    node_id: str  # the step that asked
    frontier: list[str]  # steps still to run in that round (the asking one first)
    done: list[str]
    payload: dict[str, Any]
    interrupt_id: str
    steps: int
    native: str | None = None  # the asking step's own state (an agent's RunState)

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)

    @classmethod
    def from_json(cls, text: str) -> Paused:
        return cls(**json.loads(text))


def _reduce(reducer: str, old: Any, new: Any) -> Any:
    if reducer in ("append", "add_messages"):
        return [*(old or []), *(new if isinstance(new, list) else [new])]
    if reducer == "sum":
        return (old or 0) + (new or 0)
    if reducer == "merge":
        return {**(old or {}), **(new or {})}
    return new


def interrupt_kind(payload: dict[str, Any]) -> str:
    if "action_requests" in payload:
        return "tool_approval"
    return "approval" if payload.get("kind") == "approval" else "input"


# ------------------------------------------------------------------ running ----
async def run(
    flow: Flow,
    host: Host,
    *,
    input: dict[str, Any] | None = None,
    resume: tuple[Paused, Any] | None = None,
    run_id: str = "run",
) -> AsyncIterator[dict[str, Any]]:
    """Run `flow` from the start with `input`, or continue a paused run with a person's answer. Yields AG-UI style
    events; the last one is RUN_FINISHED (outcome success or interrupt; a pause carries `paused`) or RUN_ERROR."""
    if resume is not None:
        paused, answer = resume
        values, frontier, done, steps = paused.values, list(paused.frontier), set(paused.done), paused.steps
        answers = {paused.node_id: answer}
        native = {paused.node_id: paused.native} if paused.native else {}
    else:
        values = {k: _copy(default) for k, (_, default) in flow.channels.items()}
        values.update(input or {})
        frontier, done, steps, answers, native = (
            [n for n in flow.following("START", values) if n != "END"],
            set(),
            0,
            {},
            {},
        )
    try:
        async for ev in _loop(flow, host, values, frontier, done, steps, answers, native, run_id):
            yield ev
    except Exception as exc:  # noqa: BLE001 - every failure ends the run as RUN_ERROR
        yield {"type": "RUN_ERROR", "code": "error", "message": f"{type(exc).__name__}: {exc}"}


async def _loop(
    flow: Flow,
    host: Host,
    values: dict[str, Any],
    frontier: list[str],
    done: set[str],
    steps: int,
    answers: dict[str, Any],
    native: dict[str, str],
    run_id: str,
) -> AsyncIterator[dict[str, Any]]:
    while frontier:
        steps += 1
        if steps > flow.recursion_limit:
            yield {
                "type": "RUN_ERROR",
                "code": "recursion_limit",
                "message": f"stopped after {flow.recursion_limit} steps without reaching END",
            }
            return
        following: list[str] = []
        for i, node_id in enumerate(frontier):
            step = flow.steps[node_id]
            if step.type == "core.join" and not flow.before(node_id) <= done | set(frontier[:i]) | {"START"}:
                following.append(node_id)  # wait for the other branches
                continue
            yield {"type": "STEP_STARTED", "stepName": step.name, "canvas_node_id": node_id, "task_id": node_id}
            started = time.perf_counter()
            ctx = Run(flow, host, node_id, step.name, answers.get(node_id, _MISSING), native.get(node_id))
            try:
                update = {} if step.disabled or step.run is None else await step.run(values, ctx)
            except Interrupted as intr:
                info = {
                    "id": f"{run_id}:{node_id}",
                    "kind": interrupt_kind(intr.payload),
                    "canvas_node_id": node_id,
                    "payload": intr.payload,
                }
                paused = Paused(
                    values=values, node_id=node_id, frontier=[node_id, *frontier[i + 1 :], *following],
                    done=sorted(done), payload=intr.payload, interrupt_id=info["id"], steps=steps - 1,
                    native=intr.native,
                )  # fmt: skip
                yield {
                    "type": "STEP_FINISHED", "stepName": step.name, "canvas_node_id": node_id, "status": "interrupted",
                    "task_id": node_id, "duration_ms": _ms(started),
                }  # fmt: skip
                yield {"type": "CUSTOM", "name": "ac.interrupt", "value": info}
                yield {
                    "type": "RUN_FINISHED", "outcome": "interrupt", "breakpoint": False, "interrupts": [info],
                    "next": [node_id], "checkpoint_id": None, "output": outputs(flow, values), "paused": paused,
                }  # fmt: skip
                return
            answers.pop(node_id, None)
            native.pop(node_id, None)
            for k, v in update.items():
                values[k] = _reduce(flow.channels.get(k, ("replace", None))[0], values.get(k), v)
            done.add(node_id)
            yield {
                "type": "STEP_FINISHED", "stepName": step.name, "canvas_node_id": node_id, "status": "ok",
                "task_id": node_id, "duration_ms": _ms(started),
            }  # fmt: skip
            if update:
                yield {"type": "STATE_DELTA", "canvas_node_id": node_id, "delta": update}
            for nxt in flow.following(node_id, values):
                if nxt not in following:
                    following.append(nxt)
        frontier = [n for n in following if n != "END"]
    yield {
        "type": "RUN_FINISHED", "outcome": "success", "breakpoint": False, "interrupts": [], "next": [],
        "checkpoint_id": None, "output": outputs(flow, values),
    }  # fmt: skip


def outputs(flow: Flow, values: dict[str, Any]) -> dict[str, Any]:
    return {k: values[k] for k in flow.outputs if k in values}


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value)) if isinstance(value, list | dict) else value


# ------------------------------------------------------------------ steps ----
class Run:
    """What one step of a flow can do: call agents, ask a person, search, run workspace code."""

    def __init__(
        self, flow: Flow, host: Host, node_id: str, name: str, answer: Any = _MISSING, native: str | None = None
    ):
        self.flow, self.host, self.node_id, self.name = flow, host, node_id, name
        self._answer, self._native = answer, native

    # ---- models and tools ----
    def model(self, resource: str) -> Any:
        return self.host.model(resource, self.name)

    def tools(self, resources: list[str], approve: list[str] | None = None) -> list[Any]:
        return self.host.tools(resources, list(approve or []), self.name)

    # ---- AI ----
    async def text(self, agent: Agent[Any], prompt: str) -> str:
        """One answer from `agent` to `prompt` (an "Ask AI" step)."""
        result = await Runner.run(agent, prompt, max_turns=self.flow.max_turns)
        return str(result.final_output)

    async def agent(
        self,
        agent: Agent[Any],
        messages: Any,
        *,
        output: str = "messages",
        structured: bool = False,
        max_turns: int | None = None,
        stop_at_limit: bool = True,
    ) -> dict[str, Any]:
        """An agent's turn in the conversation `messages`: its answer is appended to `output` (and, with an output
        type, also written to `structured_response`). Tools that need approval pause the run here. After
        `max_turns` model calls the agent stops with a note (or, `stop_at_limit=False`, the run fails)."""
        turns = max_turns or self.flow.max_turns
        try:
            final = await self._agent(agent, messages, turns)
        except MaxTurnsExceeded:
            if not stop_at_limit:
                raise
            note = f"Stopped: {agent.name} reached its limit of {turns} model calls before finishing."
            return {output: [{"type": "ai", "role": "assistant", "content": note, "name": agent.name}]}
        if structured and hasattr(final, "model_dump"):
            data = final.model_dump(mode="json")
            text = json.dumps(data, ensure_ascii=False)
            reply = {"type": "ai", "role": "assistant", "content": text, "name": agent.name}
            return {output: [reply], "structured_response": data}
        return {output: [{"type": "ai", "role": "assistant", "content": str(final), "name": agent.name}]}

    async def _agent(self, agent: Agent[Any], messages: Any, turns: int) -> Any:
        edits: list[dict[str, Any]] = []
        if self._native is not None and self._answer is not _MISSING:  # a person answered the tool approvals
            saved = json.loads(self._native)
            state = await RunState.from_string(agent, saved["state"])
            edits = saved.get("edits", [])
            pending = state.get_interruptions()
            for item, decision in zip(pending, _decisions(self._answer, len(pending)), strict=True):
                kind = decision.get("type", "approve")
                if kind == "approve":
                    state.approve(item)
                elif kind == "edit":  # the SDK runs a call as the model made it: ask the model to make it again
                    args = (decision.get("edited_action") or {}).get("args", {})
                    edits.append({"name": item.tool_name, "args": args})
                    state.reject(
                        item,
                        rejection_message=(
                            f"A person changed the arguments of this call to {json.dumps(args)}. Call "
                            f"{item.tool_name} again with exactly these arguments."
                        ),
                    )
                else:
                    state.reject(item, rejection_message=str(decision.get("message") or "Rejected by a person."))
            result = await Runner.run(agent, state, max_turns=turns)
        else:
            result = await Runner.run(agent, to_input(messages), max_turns=turns)
        while result.interruptions:
            items = list(result.interruptions)
            if not all(_edited(item, edits) for item in items):
                requests = [
                    {"name": item.tool_name, "args": _args(item),
                     "description": f"{agent.name} wants to call {item.tool_name}"}
                    for item in items
                ]  # fmt: skip
                review = [
                    {"action_name": r["name"], "allowed_decisions": ["approve", "edit", "reject"]} for r in requests
                ]
                raise Interrupted(
                    {"action_requests": requests, "review_configs": review, "canvas_node_id": self.node_id},
                    native=json.dumps({"state": result.to_state().to_string(), "edits": edits}),
                )
            state = result.to_state()  # calls a person already edited, made again as they wrote them: approved
            for item in items:
                edits.remove(_edited(item, edits) or {})
                state.approve(item)
            result = await Runner.run(agent, state, max_turns=turns)
        return result.final_output

    # ---- people ----
    def approval(self, state: dict[str, Any], *, show: str, output: str) -> dict[str, Any]:
        """Pause until a person approves or rejects `show` (and maybe edits it)."""
        if self._answer is _MISSING:
            raise Interrupted({"kind": "approval", "canvas_node_id": self.node_id, "value": state.get(show)})
        decision = self._answer if isinstance(self._answer, dict) else {"approved": self._answer}
        approved = decision.get("approved")
        if isinstance(approved, str):  # "no" / "false" must not read as approval
            approved = approved.strip().lower() in YES_WORDS
        out: dict[str, Any] = {output: bool(approved)}
        if decision.get("value") is not None:
            out[show] = decision["value"]
        return out

    def ask(
        self, state: dict[str, Any], *, prompt: str, output: str, schema: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Pause until a person answers `prompt`; an answer that does not fit `schema` is asked again."""
        request: dict[str, Any] = {"kind": "input", "canvas_node_id": self.node_id, "prompt": render(prompt, state)}
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
        found = await self.host.search(knowledge_base, query, mode=mode, top_k=top_k, min_score=min_score)
        out: dict[str, Any] = {output: found}
        if sources_output:
            out[sources_output] = [{k: r.get(k) for k in ("cite", "title", "source", "page")} for r in found]
        return out

    async def script(self, import_path: str, state: dict[str, Any]) -> dict[str, Any]:
        return dict(await self.host.script(import_path, dict(state)) or {})


# ------------------------------------------------------------------ helpers ----
def to_input(messages: Any) -> Any:
    """A conversation as the SDK's input items: canvas, LangChain-style and OpenAI-style messages all work."""
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
            continue  # tool messages and empty tool-call turns: the SDK keeps its own within a run
        if not isinstance(content, str):
            content = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
        items.append({"role": role, "content": content})
    return items


def _decisions(answer: Any, count: int) -> list[dict[str, Any]]:
    """A person's answer to tool approvals as one decision per call (a bare yes/no applies to all)."""
    if isinstance(answer, dict) and isinstance(answer.get("decisions"), list):
        found = [d if isinstance(d, dict) else {"type": "approve" if d else "reject"} for d in answer["decisions"]]
        return (found + [found[-1] if found else {"type": "reject"}] * count)[:count]
    if isinstance(answer, dict):
        answer = answer.get("approved")
    if isinstance(answer, str):
        answer = answer.strip().lower() in YES_WORDS
    return [{"type": "approve" if answer else "reject"}] * count


def _args(item: Any) -> dict[str, Any]:
    raw = item.arguments
    try:
        args = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return {"input": raw}
    return args if isinstance(args, dict) else {"input": args}


def _edited(item: Any, edits: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The edit a person made that this re-issued call follows exactly, if any."""
    return next((e for e in edits if e["name"] == item.tool_name and e["args"] == _args(item)), None)
