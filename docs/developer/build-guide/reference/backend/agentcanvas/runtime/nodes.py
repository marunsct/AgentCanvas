"""Node implementation library.

IMPORTANT: both the interpreter (dev runs) and the generated code (export/deploy)
call these same functions. That is how "what you run on the canvas is what you
deploy" is guaranteed. Every builder returns a LangGraph node callable.
"""

from __future__ import annotations

import importlib
import operator
from collections.abc import Callable
from typing import Annotated, Any

from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AnyMessage, HumanMessage
from langgraph.graph.message import add_messages
from langgraph.types import interrupt
from typing_extensions import TypedDict

from agentcanvas.expr import eval_condition, render_template

# User code may only be imported from these workspace packages (security: never "os:system").
ALLOWED_IMPORT_PREFIXES = ("scripts.", "tools.", "examples.")


def safe_import(import_path: str) -> Any:
    """Import `package.module:attr`, refusing anything outside the workspace packages."""
    module, _, attr = import_path.partition(":")
    if not attr or not module.startswith(ALLOWED_IMPORT_PREFIXES):
        raise PermissionError(f"import path '{import_path}' is not allowed")
    return getattr(importlib.import_module(module), attr)


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


def make_state_type(name: str, channels: list[dict[str, Any]]) -> type:
    """Build a TypedDict (with reducers) from IR channels at runtime."""
    fields: dict[str, Any] = {}
    for ch in channels:
        if ch["reducer"] == "add_messages":
            py: Any = list[AnyMessage]
        else:
            py = _PY_TYPES.get(ch.get("type", {}).get("type", "string"), Any)
        reducer = REDUCERS[ch["reducer"]]
        fields[ch["name"]] = Annotated[py, reducer] if reducer else py
    return TypedDict(name, fields, total=False)  # type: ignore[operator]


# -------------------------------------------------------------- resources ----
class Resources:
    """Resolves resource configs into live objects. Tests inject fake models here."""

    def __init__(self, overrides: dict[str, Any] | None = None):
        self._overrides = overrides or {}
        self._cache: dict[str, Any] = {}

    def model(self, name: str, config: dict[str, Any]) -> BaseChatModel:
        if name in self._overrides:
            return self._overrides[name]
        if name not in self._cache:
            params = {k: v for k, v in config.items() if k != "model"}
            self._cache[name] = init_chat_model(config["model"], **params)
        return self._cache[name]

    def tool(self, name: str, config: dict[str, Any]):
        if name in self._overrides:
            return self._overrides[name]
        return safe_import(config["import_path"])


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

    def run(state: Any) -> dict:
        decision = interrupt({"kind": "approval", "canvas_node_id": node_id, "value": state.get(show)})
        result: dict[str, Any] = {output: bool(decision.get("approved"))}
        if decision.get("value") is not None:
            result[show] = decision["value"]
        return result

    return run


def set_node(*, values: dict[str, str]) -> Callable:
    """Write templated values into channels, e.g. {"greeting": "Hi {{ state.name }}"}."""

    def run(state: Any) -> dict:
        return {k: render_template(v, state) for k, v in values.items()}

    return run


def script_node(*, import_path: str) -> Callable:
    """User code from the Workspace: 'package.module:function', signature (state) -> dict."""
    return safe_import(import_path)


def passthrough(state: Any) -> dict:
    """Routers are real nodes too (so they can show on the canvas); they change nothing."""
    return {}
