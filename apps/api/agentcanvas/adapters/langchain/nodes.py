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
import json
import operator
import re
import warnings
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Annotated, Any

from jsonschema import Draft202012Validator
from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    HumanInTheLoopMiddleware,
    LLMToolSelectorMiddleware,
    ModelCallLimitMiddleware,
    ModelFallbackMiddleware,
    ModelRetryMiddleware,
    PIIMiddleware,
    SummarizationMiddleware,
    TodoListMiddleware,
    ToolCallLimitMiddleware,
    ToolRetryMiddleware,
)
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AnyMessage, HumanMessage
from langchain_core.tools import BaseTool, StructuredTool, ToolException
from langgraph.channels import DeltaChannel
from langgraph.config import get_config
from langgraph.errors import NodeError
from langgraph.graph import StateGraph
from langgraph.graph.message import add_messages
from langgraph.runtime import Runtime
from langgraph.types import CachePolicy, Command, RetryPolicy, Send, TimeoutPolicy, TracePolicy, interrupt
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


def _append(left: list | None, right: Any) -> list:
    """List concatenation; a single value is appended as one item (so map workers can write plain values)."""
    return [*(left or []), *(right if isinstance(right, list) else [right])]


REDUCERS: dict[str, Callable | None] = {
    "replace": None,  # plain field: last writer wins
    "append": _append,  # list concatenation (single values become one item)
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
def tag_model(model: Any, model_id: str | None) -> Any:
    """Record the catalog id (provider:model) in the model's callback metadata, so usage can be priced (M4-T11)."""
    if model_id and isinstance(model, BaseChatModel):
        model.metadata = {**(model.metadata or {}), "ac_model": model_id}
    return model


class Resources:
    """Resolves resource configs into live objects. Tests and the fake server inject models here.

    `overrides` replace a resource by name (for an `mcp.server`: any MCP target, e.g. an in-process FastMCP
    server); `http_transport` lets tests serve HTTP tools without a network.
    """

    def __init__(
        self,
        overrides: dict[str, Any] | None = None,
        secrets: dict[str, str] | None = None,
        http_transport: Any = None,
    ):
        self._overrides = overrides or {}
        self._secrets = secrets or {}
        self._http_transport = http_transport
        self._cache: dict[str, Any] = {}
        self._mcp: dict[tuple[str, str], list[BaseTool]] = {}

    def model(self, name: str, config: dict[str, Any]) -> BaseChatModel:
        if name in self._overrides:
            return tag_model(self._overrides[name], config.get("model"))
        if name not in self._cache:
            params = {k: self._resolve(v) for k, v in config.items() if k != "model"}
            self._cache[name] = tag_model(init_chat_model(config["model"], **params), config["model"])
        return self._cache[name]

    def tool(self, name: str, config: dict[str, Any], type_: str = "tool.python") -> Any:
        """A tool (or, for `mcp.server`, the list of tools loaded by `load_mcp`)."""
        if type_ == "mcp.server":
            key = (name, _config_key(config))
            if key not in self._mcp:
                raise RuntimeError(f"MCP tools of '{name}' are not loaded yet; build the graph with abuild_graph()")
            return self._mcp[key]
        if name in self._overrides:
            return self._overrides[name]
        if type_ == "tool.http":
            return http_tool(name=name, config=config, resolve=self._resolve, transport=self._http_transport)
        return safe_import(config["import_path"])

    async def load_mcp(self, name: str, config: dict[str, Any], *, refresh: bool = False) -> list[BaseTool]:
        """Discover an MCP server's tools (network I/O, so it happens before the synchronous graph build)."""
        key = (name, _config_key(config))
        if refresh or key not in self._mcp:
            target = self._overrides.get(name) or mcp_target(config, self._resolve)
            tools = await mcp_tools(target)
            wanted = config.get("tool_filter") or []
            self._mcp[key] = [t for t in tools if not wanted or t.name in wanted]
        return self._mcp[key]

    def middleware(
        self, name: str, type_: str, config: dict[str, Any], *, models: Sequence[BaseChatModel] = ()
    ) -> AgentMiddleware:
        if name in self._overrides:
            return self._overrides[name]
        return middleware(type_, config, models=models)

    def _resolve(self, value: Any) -> Any:
        if isinstance(value, dict) and "$secret" in value:
            key = value["$secret"]
            if key not in self._secrets:
                raise KeyError(f"secret '{key}' is not set for this project")
            return self._secrets[key]
        return value


# ------------------------------------------------------------ tools: MCP ----
def _config_key(config: dict[str, Any]) -> str:
    return json.dumps(config, sort_keys=True, default=str)


def mcp_target(config: dict[str, Any], resolve: Callable[[Any], Any] = lambda v: v) -> Any:
    """FastMCP transport for an `mcp.server` resource. Strings are never passed through: a string naming a file
    would launch it (langchain.mcp rejects that too); stdio is explicit and admin-gated (E042)."""
    from fastmcp.client.transports import StdioTransport, StreamableHttpTransport

    if config.get("transport", "http") == "stdio":
        return StdioTransport(command=config["command"], args=list(config.get("args") or []))
    headers = {k: str(resolve(v)) for k, v in (config.get("headers") or {}).items()}
    return StreamableHttpTransport(config["url"], headers=headers or None)


async def mcp_tools(target: Any) -> list[BaseTool]:
    with warnings.catch_warnings():  # langchain.mcp is beta (surfaced as I010 on the canvas instead)
        warnings.simplefilter("ignore")
        from langchain.mcp import MCPAdapter

    async with MCPAdapter(target) as adapter:
        return await adapter.list_tools()


def mcp_tool_info(tool: BaseTool) -> dict[str, Any]:
    """Name, description and MCP annotations of a discovered tool (the inspector caches these on the resource)."""
    annotations = ((tool.metadata or {}).get("mcp", {}).get("tool", {}) or {}).get("annotations") or {}
    return {
        "name": tool.name,
        "description": (tool.description or "")[:500],
        "destructive": bool(annotations.get("destructive_hint")),
        "read_only": bool(annotations.get("read_only_hint")),
    }


# ----------------------------------------------------------- tools: HTTP ----
def _host_allowed(host: str, allowed: Sequence[str]) -> bool:
    host = host.lower().rstrip(".")
    return any(host == d.lower() or host.endswith("." + d.lower()) for d in allowed)


def http_tool(
    *, name: str, config: dict[str, Any], resolve: Callable[[Any], Any] = lambda v: v, transport: Any = None
) -> StructuredTool:
    """An HTTP request as a tool (M3-T6). Only hosts in `allowed_domains` (and their subdomains) are reachable;
    a refused host or an HTTP error comes back to the model as a tool error, not an exception."""
    from urllib.parse import urlsplit

    import httpx

    method = config.get("method", "GET").upper()
    send = config.get("send_args", "auto")
    if send == "auto":
        send = "none" if method in ("GET", "DELETE") else "json"
    allowed = list(config.get("allowed_domains") or [])
    limit = int(config.get("max_response_chars", 20_000))

    async def call(**args: Any) -> str:
        url = render_template(config["url"], {}, args=args)
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname or not _host_allowed(parts.hostname, allowed):
            raise ToolException(f"'{parts.hostname or url}' is not an allowed domain for {name}")
        headers = {k: str(resolve(v)) for k, v in (config.get("headers") or {}).items()}
        async with httpx.AsyncClient(timeout=config.get("timeout", 20), transport=transport) as client:
            r = await client.request(
                method,
                url,
                headers=headers,
                params=args if send == "query" else None,
                json=args if send == "json" else None,
            )
        text = r.text[:limit]
        if r.status_code >= 400:
            raise ToolException(f"HTTP {r.status_code} from {parts.hostname}: {text[:500]}")
        return text

    return StructuredTool(
        name=name,
        description=config.get("description") or f"HTTP {method} {config['url']}",
        args_schema=config.get("args_schema") or {"type": "object", "properties": {}},
        coroutine=call,
        handle_tool_error=True,
    )


# ------------------------------------------------------------- middleware ----
def _set(config: dict[str, Any], *keys: str) -> dict[str, Any]:
    """Config keys that are present and not empty (empty = use the upstream default)."""
    return {k: config[k] for k in keys if config.get(k) not in (None, "", [])}


_RETRY_KEYS = ("max_retries", "on_failure", "backoff_factor", "initial_delay", "max_delay", "jitter")


def middleware(type_: str, config: dict[str, Any], *, models: Sequence[BaseChatModel] = ()) -> Any:
    """Build a LangChain agent middleware from a `mw.*` resource (spec doc 04 §5, M3-T12).

    Omitted options keep the upstream defaults (V-07 #9), e.g. ModelCallLimit exits with "end",
    ToolCallLimit with "continue".
    """
    c = config
    if type_ == "mw.pii":
        return PIIMiddleware(
            c["pii_type"],
            **_set(c, "strategy", "detector", "apply_to_input", "apply_to_output", "apply_to_tool_results"),
        )
    if type_ == "mw.model_call_limit":
        return ModelCallLimitMiddleware(**_set(c, "run_limit", "thread_limit", "exit_behavior"))
    if type_ == "mw.tool_call_limit":
        return ToolCallLimitMiddleware(**_set(c, "tool_name", "run_limit", "thread_limit", "exit_behavior"))
    if type_ == "mw.model_retry":
        return ModelRetryMiddleware(**_set(c, *_RETRY_KEYS))
    if type_ == "mw.tool_retry":
        return ToolRetryMiddleware(**_set(c, *_RETRY_KEYS, "tools"))
    if type_ == "mw.model_fallback":
        if not models:
            raise ValueError("model fallback needs at least one wired model")
        return ModelFallbackMiddleware(*models)
    if type_ == "mw.summarization":
        trigger: list[Any] = []
        if c.get("trigger_tokens"):
            trigger.append(("tokens", c["trigger_tokens"]))
        if c.get("trigger_messages"):
            trigger.append(("messages", c["trigger_messages"]))
        return SummarizationMiddleware(
            models[0], trigger=trigger or None, keep=("messages", c.get("keep_messages", 20))
        )
    if type_ == "mw.todo":
        return TodoListMiddleware()
    if type_ == "mw.tool_selector":
        return LLMToolSelectorMiddleware(model=models[0] if models else None, **_set(c, "max_tools", "always_include"))
    raise ValueError(f"unknown middleware type '{type_}'")


def _hitl(approve_tools: Sequence[str] | None) -> dict[str, Any]:
    return {t: {"allowed_decisions": ["approve", "edit", "reject"]} for t in approve_tools or []}


def _response_format(schema: dict[str, Any] | None, name: str) -> dict[str, Any] | None:
    """A JSON Schema dict for `response_format`; the title becomes the structured-output tool name."""
    if not schema:
        return None
    title = re.sub(r"[^A-Za-z0-9_-]", "_", str(schema.get("title") or f"{name}_output"))
    return {**schema, "title": title}


# ------------------------------------------------------------ deep agents ----
@dataclass(frozen=True)
class Mount:
    """A Deep Agent file backend; `path` None = the default backend, else a folder mounted on it."""

    path: str | None
    backend: Any


def store_namespace(*, scope: str, namespace: str, workflow_id: str, user_key: str | None) -> Callable:
    """Namespace factory for a long-term folder: never derived from model output (E072)."""

    def factory(rt: Any) -> tuple[str, ...]:
        if scope == "workflow":
            owner = workflow_id
        elif scope == "thread":
            owner = get_config().get("configurable", {}).get("thread_id")
        else:
            ctx = getattr(rt, "context", None) or {}
            owner = ctx.get(user_key) if user_key and isinstance(ctx, dict) else None
            if not owner:
                raise PermissionError("a user-scoped folder needs the run's user (context field with source 'user')")
        return (namespace, scope, str(owner))

    return factory


# ----------------------------------------------------------------- memory ----
def _plain(value: Any) -> Any:
    """JSON-safe copy of a state value for the store (messages and models become dicts)."""
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    if hasattr(value, "model_dump"):
        return _plain(value.model_dump())
    return value if isinstance(value, str | int | float | bool) or value is None else str(value)


def keyword_rank(items: list[Any], query: str, limit: int) -> list[Any]:
    """Without a semantic index the store ignores `query`: rank by how many query words each key+value contains."""
    words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 2]
    if not words:
        return items[:limit]

    def text(i: Any) -> str:
        return f"{i.key} {json.dumps(i.value, default=str)}".lower()

    scored = [(sum(w in text(i) for w in words), n, i) for n, i in enumerate(items)]
    return [i for score, _, i in sorted(scored, key=lambda t: (-t[0], t[1])) if score > 0][:limit]


def memory_node(
    *,
    op: str,
    space: str,
    scope: str,
    workflow_id: str,
    user_key: str | None = None,
    key: str | None = None,
    query: str | None = None,
    top_k: int = 5,
    value: str | None = None,
    output: str | None = None,
) -> Callable:
    """Recall / Remember / Forget in the long-term store (M3-T14). The namespace is (space, scope, owner) —
    the same one a Deep Agent's long-term folder uses — and never comes from model output (E072)."""
    namespace = store_namespace(scope=scope, namespace=space, workflow_id=workflow_id, user_key=user_key)

    async def run(state: Any, runtime: Runtime) -> dict:
        store = runtime.store
        if store is None:
            raise RuntimeError("memory nodes need a long-term store (the runner and exported apps provide one)")
        ns = namespace(runtime)
        if op == "recall":
            q = render_template(query, state).strip() if query else ""
            indexed = getattr(store, "index_config", None) is not None
            items = await store.asearch(ns, query=q or None, limit=top_k if indexed or not q else 1000)
            if q and not indexed:
                items = keyword_rank(items, q, top_k)
            return {output: [{"key": i.key, **i.value} for i in items]} if output else {}
        k = render_template(key or "", state).strip()
        if not k:
            raise ValueError("memory key rendered empty")
        if op == "remember":
            v = _plain(state.get(value)) if value else None
            await store.aput(ns, k, v if isinstance(v, dict) else {"value": v})
        else:
            await store.adelete(ns, k)
        return {}

    return run


def memory_tools(*, space: str, scope: str, workflow_id: str, user_key: str | None = None) -> list[BaseTool]:
    """`search_memory` / `save_memory` tools for agents, bound to one memory space (FR-MEM-03)."""
    from langgraph.runtime import get_runtime

    namespace = store_namespace(scope=scope, namespace=space, workflow_id=workflow_id, user_key=user_key)

    def _ctx() -> tuple[Any, tuple[str, ...]]:
        rt = get_runtime()
        if rt.store is None:
            raise ToolException("no long-term store is configured")
        return rt.store, namespace(rt)

    async def search_memory(query: str) -> str:
        store, ns = _ctx()
        items = keyword_rank(await store.asearch(ns, limit=1000), query, 5)
        return json.dumps([{"key": i.key, **i.value} for i in items], default=str) if items else "nothing found"

    async def save_memory(key: str, text: str) -> str:
        store, ns = _ctx()
        await store.aput(ns, key, {"text": text})
        return f"saved '{key}'"

    return [
        StructuredTool.from_function(
            coroutine=search_memory,
            name="search_memory",
            description=f"Search long-term memory ({space}) for facts saved in earlier conversations.",
            handle_tool_error=True,
        ),
        StructuredTool.from_function(
            coroutine=save_memory,
            name="save_memory",
            description=f"Save a fact to long-term memory ({space}) under a short key, for later conversations.",
            handle_tool_error=True,
        ),
    ]


def backend(type_: str, config: dict[str, Any], *, workflow_id: str, user_key: str | None = None) -> Mount:
    from deepagents.backends import StateBackend, StoreBackend

    if type_ == "backend.state":
        return Mount(None, StateBackend())
    if type_ == "backend.store":
        factory = store_namespace(
            scope=config.get("scope", "user"),
            namespace=config.get("namespace", "memories"),
            workflow_id=workflow_id,
            user_key=user_key,
        )
        return Mount(config.get("mount", "/memories/"), StoreBackend(namespace=factory))
    raise ValueError(f"unknown backend type '{type_}'")


def _compose(mounts: Sequence[Mount]) -> Any:
    from deepagents.backends import CompositeBackend, StateBackend

    default = next((m.backend for m in mounts if m.path is None), None) or StateBackend()
    routes = {m.path: m.backend for m in mounts if m.path}
    return CompositeBackend(default=default, routes=routes) if routes else default


def subagent(
    *,
    name: str,
    description: str,
    system_prompt: str,
    model: BaseChatModel | None = None,
    tools: Sequence[Any] = (),
    middleware: Sequence[AgentMiddleware] = (),
    approve_tools: Sequence[str] | None = None,
) -> dict[str, Any]:
    """A declarative Deep Agents `SubAgent` (called through the main agent's `task` tool)."""
    spec: dict[str, Any] = {"name": name, "description": description, "system_prompt": system_prompt}
    if model is not None:
        spec["model"] = model
    if tools:
        spec["tools"] = list(tools)
    if middleware:
        spec["middleware"] = list(middleware)
    if approve_tools:
        spec["interrupt_on"] = _hitl(approve_tools)
    return spec


def deep_agent_node(
    *,
    name: str,
    model: BaseChatModel,
    tools: Sequence[Any] = (),
    system_prompt: str = "",
    middleware: Sequence[AgentMiddleware] = (),
    subagents: Sequence[Any] = (),
    backends: Sequence[Mount] = (),
    planning: bool = True,
    approve_tools: Sequence[str] | None = None,
    permissions: Sequence[dict[str, Any]] | None = None,
    response_format: dict[str, Any] | None = None,
):
    """A Deep Agent (create_deep_agent) as a subgraph node; reads/writes the parent's `messages`."""
    from deepagents import FilesystemPermission, create_deep_agent

    stack = [TodoListMiddleware(), *middleware] if planning else list(middleware)
    return create_deep_agent(
        model=model,
        tools=list(tools),
        system_prompt=system_prompt or None,
        middleware=stack,
        subagents=list(subagents) or None,
        backend=_compose(backends),
        interrupt_on=_hitl(approve_tools) or None,
        permissions=[FilesystemPermission(**p) for p in permissions or []] or None,
        response_format=_response_format(response_format, name),
        name=name,
    )


# ------------------------------------------------------------------ nodes ----
def llm_node(*, model: BaseChatModel, prompt: str, output: str) -> Callable:
    """One model call: render prompt from state, write the text answer to `output`."""

    async def run(state: Any) -> dict:
        text = render_template(prompt, state)
        reply = await model.ainvoke([HumanMessage(text)])
        return {output: reply.content}

    return run


def agent_node(
    *,
    name: str,
    model: BaseChatModel,
    tools: list,
    system_prompt: str,
    approve_tools: list[str] | None = None,
    middleware: Sequence[AgentMiddleware] = (),
    response_format: dict[str, Any] | None = None,
):
    """A tool-using agent (LangChain create_agent). Returns a compiled subgraph.

    The subgraph reads/writes the parent's `messages` channel, so the workflow
    state must contain a `messages` channel with reducer `add_messages`. With a
    `response_format` it also writes `structured_response`. Wired middleware runs in edge order,
    the tool-approval middleware last.
    """
    stack = list(middleware)
    if approve_tools:
        stack.append(HumanInTheLoopMiddleware(interrupt_on=_hitl(approve_tools)))
    return create_agent(
        model,
        tools=tools,
        system_prompt=system_prompt,
        middleware=stack,
        response_format=_response_format(response_format, name),
        name=name,
    )


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


def human_input_node(
    *, node_id: str, prompt: str, output: str, response_schema: dict[str, Any] | None = None
) -> Callable:
    """Ask a person (M3-T7). The answer is validated against `response_schema`; invalid answers re-ask."""
    validator = Draft202012Validator(response_schema) if response_schema else None

    async def run(state: Any) -> dict:
        request: dict[str, Any] = {"kind": "input", "canvas_node_id": node_id, "prompt": render_template(prompt, state)}
        if response_schema:
            request["response_schema"] = response_schema
        while True:
            answer = interrupt(request)
            problems = sorted(validator.iter_errors(answer), key=str) if validator else []
            if not problems:
                return {output: answer}
            request = {**request, "error": problems[0].message}

    return run


def map_fn(
    *, items: str, item: str, target: str, empty: str, carry: Sequence[str] = (), max_items: int | None = None
) -> Callable:
    """Fan-out for `core.map` (M3-T8): one `Send(target, {item: x, **carry})` per list element.

    The workers run in the same super-step; the step after them runs once, when all have finished.
    """

    def fan_out(state: Any) -> str | list[Send]:
        values = state.get(items) or []
        values = list(values if isinstance(values, list | tuple) else [values])[:max_items]
        if not values:
            return empty
        shared = {c: state[c] for c in carry if c in state}
        return [Send(target, {**shared, item: v}) for v in values]

    return fan_out


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
