"""Node implementation library of the LangChain adapter (published as `agentcanvas-runtime` in Sprint 8).

IMPORTANT: both the interpreter (canvas runs) and the generated code (export/deploy) call these
same functions (ADR 0004). That is how "what you run on the canvas is what you deploy" is
guaranteed.

Every node callable returned here is ``async`` (ADR 0015): LangGraph per-node timeouts only work
on async nodes, and sync user code is moved off the event loop with ``asyncio.to_thread``
(``interrupt()``/``get_config()`` keep working there because the context is copied).
"""

from __future__ import annotations

import asyncio
import functools
import importlib
import inspect
import operator
from collections.abc import Callable, Sequence
from typing import Annotated, Any

from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AnyMessage, HumanMessage
from langgraph.channels import DeltaChannel
from langgraph.errors import NodeError
from langgraph.graph import StateGraph
from langgraph.graph.message import add_messages
from langgraph.runtime import Runtime
from langgraph.types import CachePolicy, Command, RetryPolicy, TimeoutPolicy, TracePolicy, interrupt
from typing_extensions import TypedDict

from agentcanvas.expr import eval_condition, render_template
from agentcanvas.security.imports import is_allowed_import_path


def safe_import(import_path: str) -> Any:
    """Import `package.module:attr`, refusing anything outside the workspace packages (never "os:system")."""
    if not is_allowed_import_path(import_path):
        raise PermissionError(f"import path '{import_path}' is not allowed")
    module, _, attr = import_path.partition(":")
    return getattr(importlib.import_module(module), attr)


def ensure_async(fn: Callable) -> Callable:
    """Return an async node callable; sync functions run in a worker thread.

    A `runtime` parameter (LangGraph `Runtime`: context, store, stream_writer…) is passed through, so
    scripts may be written as `def run(state)` or `def run(state, runtime)`.
    """
    if inspect.iscoroutinefunction(fn):
        return fn
    if "runtime" in inspect.signature(fn).parameters:

        async def run_rt(state: Any, runtime: Runtime) -> Any:
            return await asyncio.to_thread(fn, state, runtime)

        run_rt.__name__ = getattr(fn, "__name__", "run")
        return run_rt

    async def run(state: Any) -> Any:
        return await asyncio.to_thread(fn, state)

    run.__name__ = getattr(fn, "__name__", "run")
    return run


# ---------------------------------------------------------------- state ----
_PY_TYPES = {"string": str, "integer": int, "number": float, "boolean": bool, "array": list, "object": dict}


def _merge(left: dict | None, right: dict | None) -> dict:
    return {**(left or {}), **(right or {})}


REDUCERS: dict[str, Callable | None] = {
    "replace": None,  # plain field: last writer wins
    "append": operator.add,  # list concatenation
    "add_messages": add_messages,  # chat messages (dedup by id)
    "sum": operator.add,  # numbers
    "merge": _merge,  # dict shallow merge
}


def _append_batch(state: list | None, writes: Sequence[Any]) -> list:
    """DeltaChannel batch reducer for `append` (batching-invariant list concatenation)."""
    out = list(state or [])
    for w in writes:
        out.extend(w if isinstance(w, list) else [w])
    return out


def _messages_batch(state: list | None, writes: Sequence[Any]) -> list:
    """DeltaChannel batch reducer for `add_messages` (id-based merge, applied write by write)."""
    return functools.reduce(add_messages, writes, list(state or []))  # type: ignore[arg-type]


DELTA_REDUCERS: dict[str, Callable] = {"append": _append_batch, "add_messages": _messages_batch}


def _annotation(ch: dict[str, Any]) -> Any:
    reducer = ch.get("reducer", "replace")
    if reducer == "add_messages":
        py: Any = list[AnyMessage]
    else:
        py = _PY_TYPES.get(ch.get("type", {}).get("type", "string"), Any)
    if ch.get("storage") == "delta":  # ADR 0016: storage mode, orthogonal to the reducer
        return Annotated[py, DeltaChannel(DELTA_REDUCERS[reducer])]
    fn = REDUCERS[reducer]
    return Annotated[py, fn] if fn else py


def make_state_type(name: str, channels: list[dict[str, Any]], only: set[str] | None = None) -> type:
    """Build a TypedDict (with reducers) from IR channels at runtime."""
    fields_ = {ch["name"]: _annotation(ch) for ch in channels if only is None or ch["name"] in only}
    return TypedDict(name, fields_, total=False)  # type: ignore[operator]


def make_io_types(channels: list[dict[str, Any]], settings: dict[str, Any]) -> tuple[type, type]:
    """Input/output schemas: declared lists win, otherwise every non-private channel (by `io`)."""
    public = [c["name"] for c in channels if c.get("io", "inout") != "private"]
    inputs = settings.get("input_channels") or [c for c in public if _io(channels, c) in ("inout", "in")]
    outputs = settings.get("output_channels") or [c for c in public if _io(channels, c) in ("inout", "out")]
    return make_state_type("Input", channels, set(inputs)), make_state_type("Output", channels, set(outputs))


def _io(channels: list[dict[str, Any]], name: str) -> str:
    return next(c.get("io", "inout") for c in channels if c["name"] == name)


def make_context_type(fields: list[dict[str, Any]]) -> type | None:
    """Run context schema (`Runtime.context`); None when the workflow declares no context fields."""
    if not fields:
        return None
    fields_ = {f["name"]: _PY_TYPES.get(f.get("type", {}).get("type", "string"), Any) for f in fields}
    return TypedDict("Context", fields_, total=False)  # type: ignore[operator]


# -------------------------------------------------------------- policies ----
def _omit(_: Any) -> dict[str, Any]:
    return {}


_TRACE = {
    "omit_inputs": {"process_inputs": _omit},
    "omit_outputs": {"process_outputs": _omit},
    "omit": {"process_inputs": _omit, "process_outputs": _omit},
}

_EXCEPTIONS: dict[str, type[BaseException]] = {
    "Exception": Exception,
    "ValueError": ValueError,
    "TimeoutError": TimeoutError,
    "KeyError": KeyError,
    "RuntimeError": RuntimeError,
    "ConnectionError": ConnectionError,
    "OSError": OSError,
}


def _retry(spec: dict[str, Any]) -> RetryPolicy:
    kw = {
        k: spec[k]
        for k in ("max_attempts", "initial_interval", "backoff_factor", "max_interval", "jitter")
        if k in spec
    }
    names = spec.get("retry_on") or []
    if names:
        kw["retry_on"] = tuple(_EXCEPTIONS.get(n, Exception) for n in names)
    return RetryPolicy(**kw)


def error_router(target: str, write_channel: str | None = None) -> Callable:
    """`error_handler` that routes to `target` (IR `policies.error_route`) after retries are exhausted."""

    def handle(state: Any, error: NodeError) -> Command:
        update = {write_channel: f"{error.node}: {type(error.error).__name__}: {error.error}"} if write_channel else {}
        return Command(goto=target, update=update)

    return handle


def node_options(
    policies: dict[str, Any] | None, *, error_target: str | None = None, error_channel: str | None = None
) -> dict[str, Any]:
    """Translate IR node policies into `StateGraph.add_node(...)` keyword arguments."""
    p = policies or {}  # keys present = policy configured (defaults may be omitted inside)
    out: dict[str, Any] = {}
    if p.get("retry") is not None:
        out["retry_policy"] = _retry(p["retry"])
    t = p.get("timeout") or {}
    if t.get("run_timeout") or t.get("idle_timeout"):
        out["timeout"] = TimeoutPolicy(
            run_timeout=t.get("run_timeout"), idle_timeout=t.get("idle_timeout"), refresh_on=t.get("refresh_on", "auto")
        )
    if p.get("cache") is not None:
        out["cache_policy"] = CachePolicy(ttl=p["cache"].get("ttl"))
    if error_target:
        out["error_handler"] = error_router(error_target, error_channel)
        out["destinations"] = (error_target,)
    if p.get("trace", "full") != "full":
        out["trace_policy"] = TracePolicy(**_TRACE[p["trace"]])
    return out


def apply_node_defaults(builder: StateGraph, defaults: dict[str, Any] | None) -> None:
    """Workflow `settings.node_defaults` -> `StateGraph.set_node_defaults(...)`."""
    opts = node_options(defaults)
    kw = {k: opts[k] for k in ("retry_policy", "cache_policy", "timeout") if k in opts}
    if kw:
        builder.set_node_defaults(**kw)


# -------------------------------------------------------------- resources ----
class Resources:
    """Resolves resource configs into live objects. Tests and the fake server inject models here."""

    def __init__(self, overrides: dict[str, Any] | None = None, secrets: dict[str, str] | None = None):
        self._overrides = overrides or {}
        self._secrets = secrets or {}
        self._cache: dict[str, Any] = {}

    def model(self, name: str, config: dict[str, Any]) -> BaseChatModel:
        if name in self._overrides:
            return self._overrides[name]
        if name not in self._cache:
            params = {k: self._resolve(v) for k, v in config.items() if k != "model"}
            self._cache[name] = init_chat_model(config["model"], **params)
        return self._cache[name]

    def tool(self, name: str, config: dict[str, Any]):
        if name in self._overrides:
            return self._overrides[name]
        return safe_import(config["import_path"])

    def _resolve(self, value: Any) -> Any:
        if isinstance(value, dict) and "$secret" in value:
            key = value["$secret"]
            if key not in self._secrets:
                raise KeyError(f"secret '{key}' is not set for this project")
            return self._secrets[key]
        return value


# ------------------------------------------------------------------ nodes ----
def llm_node(*, model: BaseChatModel, prompt: str, output: str) -> Callable:
    """One model call: render prompt from state, write the text answer to `output`."""

    async def run(state: Any) -> dict:
        text = render_template(prompt, state)
        reply = await model.ainvoke([HumanMessage(text)])
        return {output: reply.content}

    return run


def agent_node(
    *, name: str, model: BaseChatModel, tools: list, system_prompt: str, approve_tools: list[str] | None = None
):
    """A tool-using agent (LangChain create_agent). Returns a compiled subgraph.

    The subgraph reads/writes the parent's `messages` channel, so the workflow
    state must contain a `messages` channel with reducer `add_messages`.
    """
    middleware = []
    if approve_tools:
        middleware.append(
            HumanInTheLoopMiddleware(
                interrupt_on={t: {"allowed_decisions": ["approve", "edit", "reject"]} for t in approve_tools}
            )
        )
    return create_agent(model, tools=tools, system_prompt=system_prompt, middleware=middleware, name=name)


def router_fn(*, cases: list[dict[str, str]], default: str = "default") -> Callable:
    """Routing function for add_conditional_edges: first matching CEL condition wins."""

    def route(state: Any) -> str:
        for case in cases:
            if eval_condition(case["when"], state):
                return case["route"]
        return default

    return route


def approval_node(*, node_id: str, show: str, output: str) -> Callable:
    """Pause for a human. Resume value must be {"approved": bool, "value": optional edit}."""

    async def run(state: Any) -> dict:
        decision = interrupt({"kind": "approval", "canvas_node_id": node_id, "value": state.get(show)})
        decision = decision if isinstance(decision, dict) else {"approved": bool(decision)}
        result: dict[str, Any] = {output: bool(decision.get("approved"))}
        if decision.get("value") is not None:
            result[show] = decision["value"]
        return result

    return run


def set_node(*, values: dict[str, str]) -> Callable:
    """Write templated values into channels, e.g. {"greeting": "Hi {{ state.name }}"}."""

    async def run(state: Any) -> dict:
        return {k: render_template(v, state) for k, v in values.items()}

    return run


def script_node(*, import_path: str) -> Callable:
    """User code from the Workspace: 'package.module:function', signature (state) -> dict (sync or async)."""
    return ensure_async(safe_import(import_path))


async def passthrough(state: Any) -> dict:
    """Routers and bypassed (disabled) nodes are real graph nodes so they show on the canvas; they change nothing."""
    return {}
