"""The flow engine for frameworks without a graph of their own (S32): Pydantic AI and Agno.

Their agents are excellent, but a workflow's shape (paths, parallel branches, loops, people) is not theirs to draw,
so a workflow becomes plain async Python: one function per block and a `Flow` that names the steps and what follows
each (see `flowgen`). This module runs that `Flow` in supersteps, the way the canvas draws it: steps side by side
run in the same round, a "wait for all" step waits for every branch, paths follow the first CEL rule that matches,
and fields merge with their reducers. A step that waits for a person ends the run with a `Paused` value (JSON) that
continues it later from that step, with the step's own framework state (a Pydantic AI message history, an Agno
`RunOutput`) kept in `native`.

Each framework's runtime subclasses `Run` with its AI calls (`text`, `agent`) and passes it to `run`. The same
module runs inside AgentCanvas (in a worker process with the framework) and in exports.

Only the standard library, `jsonschema` and `agentcanvas.expr` (vendored into exports) are imported here.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from jsonschema import Draft202012Validator

from agentcanvas.expr import eval_condition, render_template

YES_WORDS = {"yes", "y", "true", "approve", "approved", "ok", "okay"}
ROLES = {"human": "user", "user": "user", "ai": "assistant", "assistant": "assistant", "system": "system"}
MISSING: Any = object()


def render(template: str, state: dict[str, Any]) -> str:
    """A prompt or value with `{{ state.<field> }}` filled in (sandboxed Jinja)."""
    return render_template(template, state)


class Host(Protocol):
    """What the steps of a flow need from where they run."""

    def model(self, resource: str, step: str, **settings: Any) -> Any:
        """The framework's model for the canvas model block `resource`, used by the step named `step`."""
        ...

    def tools(self, resources: list[str], step: str) -> list[Callable[..., Any]]:
        """The canvas tool blocks `resources` as plain functions (a real signature and docstring each)."""
        ...

    async def search(
        self, knowledge_base: str, query: str, *, mode: str, top_k: int, min_score: float
    ) -> list[dict[str, Any]]: ...

    async def script(self, import_path: str, state: dict[str, Any]) -> dict[str, Any]: ...


class Interrupted(Exception):
    """A step waits for a person. `native` is what the step needs to continue (its framework's own saved state)."""

    def __init__(self, payload: dict[str, Any], native: str | None = None):
        super().__init__("interrupted")
        self.payload = payload
        self.native = native


class StopRun(Exception):
    """A step ends the run on purpose (a model call limit set to fail)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


StepFn = Callable[[dict[str, Any], Any], Awaitable[dict[str, Any]]]


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
    native: str | None = None  # the asking step's own framework state
    next_round: list[str] = field(default_factory=list)  # steps the round's finished steps lead to
    arrived: dict[str, list[str]] = field(default_factory=dict)  # "wait for all" step -> branches in since it ran

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)

    @classmethod
    def from_json(cls, text: str) -> Paused:
        return cls(**json.loads(text))


def reduce(reducer: str, old: Any, new: Any) -> Any:
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
    run_class: type[Run],
    *,
    input: dict[str, Any] | None = None,
    resume: tuple[Paused, Any] | None = None,
    run_id: str = "run",
) -> AsyncIterator[dict[str, Any]]:
    """Run `flow` from the start with `input`, or continue a paused run with a person's answer; each step gets a
    `run_class`. Yields AG-UI style events; the last one is RUN_FINISHED (outcome success or interrupt; a pause
    carries `paused`) or RUN_ERROR."""
    if resume is not None:
        paused, answer = resume
        values, frontier, done, steps = copy(paused.values), list(paused.frontier), set(paused.done), paused.steps
        answers = {paused.node_id: answer}
        native = {paused.node_id: paused.native} if paused.native else {}
        rounds = _Rounds(list(paused.next_round), {k: set(v) for k, v in paused.arrived.items()}, resumed=True)
    else:
        values = {k: copy(default) for k, (_, default) in flow.channels.items()}
        values.update(copy(input or {}))
        rounds = _Rounds([], {})
        frontier = [n for n in rounds.lead(flow, "START", values) if n != "END"]
        done, steps, answers, native = set(), 0, {}, {}
    try:
        async for ev in _loop(flow, host, run_class, values, frontier, done, steps, answers, native, run_id, rounds):
            yield ev
    except StopRun as exc:
        yield {"type": "RUN_ERROR", "code": exc.code, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001 - every failure ends the run as RUN_ERROR
        yield {"type": "RUN_ERROR", "code": "error", "message": f"{type(exc).__name__}: {exc}"}


async def _loop(
    flow: Flow,
    host: Host,
    run_class: type[Run],
    values: dict[str, Any],
    frontier: list[str],
    done: set[str],
    steps: int,
    answers: dict[str, Any],
    native: dict[str, str],
    run_id: str,
    rounds: _Rounds,
) -> AsyncIterator[dict[str, Any]]:
    while frontier := rounds.ready(flow, frontier):
        steps += 1
        if steps > flow.recursion_limit:
            yield {
                "type": "RUN_ERROR",
                "code": "recursion_limit",
                "message": f"stopped after {flow.recursion_limit} steps without reaching END",
            }
            return
        following, rounds.following = rounds.following, []
        for i, node_id in enumerate(frontier):
            step = flow.steps[node_id]
            yield {"type": "STEP_STARTED", "stepName": step.name, "canvas_node_id": node_id, "task_id": node_id}
            started = time.perf_counter()
            ctx = run_class(flow, host, node_id, step.name, answers.get(node_id, MISSING), native.get(node_id))
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
                    values=copy(values), node_id=node_id, frontier=[node_id, *frontier[i + 1 :]], done=sorted(done),
                    payload=intr.payload, interrupt_id=info["id"], steps=steps - 1, native=intr.native,
                    next_round=following, arrived={k: sorted(v) for k, v in rounds.arrived.items() if v},
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
            except Exception:
                yield {
                    "type": "STEP_FINISHED", "stepName": step.name, "canvas_node_id": node_id, "status": "error",
                    "task_id": node_id, "duration_ms": _ms(started),
                }  # fmt: skip
                raise
            answers.pop(node_id, None)
            native.pop(node_id, None)
            for k, v in update.items():
                values[k] = reduce(flow.channels.get(k, ("replace", None))[0], values.get(k), v)
            done.add(node_id)
            yield {
                "type": "STEP_FINISHED", "stepName": step.name, "canvas_node_id": node_id, "status": "ok",
                "task_id": node_id, "duration_ms": _ms(started),
            }  # fmt: skip
            if update:
                yield {"type": "STATE_DELTA", "canvas_node_id": node_id, "delta": copy(update)}
            for nxt in rounds.lead(flow, node_id, values):
                if nxt not in following:
                    following.append(nxt)
        frontier = [n for n in following if n != "END"]
    yield {
        "type": "RUN_FINISHED", "outcome": "success", "breakpoint": False, "interrupts": [], "next": [],
        "checkpoint_id": None, "output": outputs(flow, values),
    }  # fmt: skip


@dataclass
class _Rounds:
    """Which steps run next. A "wait for all" step runs once every branch into it has arrived since it last ran
    (a loop waits again on each pass); one still waiting when nothing else is left to run is dropped, as when a
    path skipped one of its branches."""

    following: list[str]  # the next round, already started (a resumed run)
    arrived: dict[str, set[str]]
    resumed: bool = False

    def lead(self, flow: Flow, node_id: str, values: dict[str, Any]) -> list[str]:
        nxt = flow.following(node_id, values)
        for target in nxt:
            if target in flow.steps and flow.steps[target].type == "core.join":
                self.arrived.setdefault(target, set()).add(node_id)
        return nxt

    def ready(self, flow: Flow, frontier: list[str]) -> list[str]:
        if self.resumed:  # the paused round goes on as it was
            self.resumed = False
            return frontier
        out = []
        for node_id in frontier:
            if flow.steps[node_id].type != "core.join":
                out.append(node_id)
            elif flow.before(node_id) <= self.arrived.get(node_id, set()):
                self.arrived[node_id] = set()
                out.append(node_id)
        return out


def outputs(flow: Flow, values: dict[str, Any]) -> dict[str, Any]:
    return {k: copy(values[k]) for k in flow.outputs if k in values}


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def copy(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str)) if isinstance(value, list | dict) else value


# ------------------------------------------------------------------ steps ----
class Run:
    """What one step of a flow can do: ask a person, search, run workspace code. A framework's runtime adds its
    models, tools and agents."""

    def __init__(
        self, flow: Flow, host: Host, node_id: str, name: str, answer: Any = MISSING, native: str | None = None
    ):
        self.flow, self.host, self.node_id, self.name = flow, host, node_id, name
        self._answer, self._native = answer, native

    def tool_request(self, calls: list[tuple[str, dict[str, Any]]], native: str) -> Interrupted:
        """The pause for tool calls that wait for a person's approval (name and arguments of each)."""
        requests = [{"name": n, "args": a, "description": f"{self.name} wants to call {n}"} for n, a in calls]
        review = [{"action_name": n, "allowed_decisions": ["approve", "edit", "reject"]} for n, _ in calls]
        payload = {"action_requests": requests, "review_configs": review, "canvas_node_id": self.node_id}
        return Interrupted(payload, native=native)

    def tool_answers(self, count: int) -> list[dict[str, Any]] | None:
        """A person's decisions on the paused tool calls (None: this step is not continuing a tool approval)."""
        if self._native is None or self._answer is MISSING:
            return None
        return decisions(self._answer, count)

    # ---- people ----
    def approval(self, state: dict[str, Any], *, show: str, output: str) -> dict[str, Any]:
        """Pause until a person approves or rejects `show` (and maybe edits it)."""
        if self._answer is MISSING:
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
        if self._answer is MISSING:
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
        return dict(await self.host.script(import_path, copy(state)) or {})

    # ---- answers ----
    def reply(self, agent: str, answer: Any, *, output: str, structured: bool) -> dict[str, Any]:
        """An agent's answer as the fields it sets: a message appended to `output`, and with a typed answer also
        `structured_response`."""
        if structured and answer is not None and not isinstance(answer, str):
            data = answer.model_dump(mode="json") if hasattr(answer, "model_dump") else answer
            text = json.dumps(data, ensure_ascii=False)
            return {output: [{"type": "ai", "role": "assistant", "content": text, "name": agent}],
                    "structured_response": data}  # fmt: skip
        text = answer if isinstance(answer, str) else "" if answer is None else json.dumps(answer, default=str)
        return {output: [{"type": "ai", "role": "assistant", "content": text, "name": agent}]}

    def limit_note(self, agent: str, limit: int, *, output: str) -> dict[str, Any]:
        note = f"Stopped: {agent} reached its limit of {limit} model calls before finishing."
        return {output: [{"type": "ai", "role": "assistant", "content": note, "name": agent}]}


# ------------------------------------------------------------------ helpers ----
def decisions(answer: Any, count: int) -> list[dict[str, Any]]:
    """A person's answer to tool approvals as one decision per call, each with its `type` (a bare yes/no applies
    to all; a call the answer leaves out is rejected)."""
    if isinstance(answer, dict) and isinstance(answer.get("decisions"), list):
        found = [_decision(d) for d in answer["decisions"]]
        return (found + [{"type": "reject"}] * count)[:count]
    return [{"type": "approve" if _yes(answer) else "reject"}] * count


def _yes(answer: Any) -> bool:
    if isinstance(answer, dict):
        answer = answer.get("approved")
    if isinstance(answer, str):  # "no" / "reject" must not read as approval
        return answer.strip().lower() in YES_WORDS
    return isinstance(answer, bool | int | float) and bool(answer)


def _decision(given: Any) -> dict[str, Any]:
    if isinstance(given, dict) and given.get("type") in ("approve", "edit", "reject"):
        return given
    if isinstance(given, str) and given.strip().lower() in ("edit", "reject"):
        return {"type": given.strip().lower()}
    return {**(given if isinstance(given, dict) else {}), "type": "approve" if _yes(given) else "reject"}


def edited_args(decision: dict[str, Any]) -> dict[str, Any] | None:
    """The arguments a person wrote for an edited call (None: not an edit)."""
    if decision.get("type") != "edit":
        return None
    return dict((decision.get("edited_action") or {}).get("args") or {})


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
