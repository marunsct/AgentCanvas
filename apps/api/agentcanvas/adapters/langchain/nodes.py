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
import copy
import functools
import inspect
import json
import operator
import re
import warnings
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
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
from langgraph.config import get_config, get_stream_writer
from langgraph.errors import NodeError
from langgraph.graph import StateGraph
from langgraph.graph.message import add_messages
from langgraph.runtime import Runtime
from langgraph.types import CachePolicy, Command, RetryPolicy, Send, TimeoutPolicy, TracePolicy, interrupt
from typing_extensions import TypedDict

from agentcanvas.expr import eval_condition, render_template
from agentcanvas.workspace.loader import load_attr


def safe_import(import_path: str, workspace: Path | None = None) -> Any:
    """Import `package.module:attr` from the project workspace (or installed packages in dev), refusing anything
    outside the workspace packages (never "os:system"). Workspace files are hot-reloaded when they change."""
    return load_attr(import_path, workspace)


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


def _summarize(payload: Any) -> dict[str, Any]:
    """Trace shapes, not contents: `{"ticket": "str(142)", "messages": "list(6)"}` (FR-X-07 "summarize")."""

    def shape(v: Any) -> str:
        n = len(v) if isinstance(v, str | list | tuple | dict) else None
        return f"{type(v).__name__}({n})" if n is not None else type(v).__name__

    if isinstance(payload, dict):
        return {str(k): shape(v) for k, v in payload.items()}
    return {"value": shape(payload)}


_TRACE = {
    "summarize": {"process_inputs": _summarize, "process_outputs": _summarize},
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


def _redactor(keys: set[str]) -> Callable[[Any], Any]:
    """Replace the values of `keys` (at any depth) with "[redacted]" (workflow `settings.trace_redact`, S12)."""

    def walk(v: Any) -> Any:
        if isinstance(v, dict):
            return {k: "[redacted]" if k in keys else walk(x) for k, x in v.items()}
        if isinstance(v, list | tuple):
            return [walk(x) for x in v]
        return v

    return walk


def node_options(
    policies: dict[str, Any] | None = None,
    *,
    error_target: str | None = None,
    error_channel: str | None = None,
    redact: Sequence[str] | None = None,
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
    trace = p.get("trace", "full")
    if trace != "full" or redact:
        fns: dict[str, Any] = dict(_TRACE.get(trace, {}))
        if redact:
            hide = _redactor(set(redact))
            for k in ("process_inputs", "process_outputs"):
                fns[k] = (lambda f: lambda payload: hide(f(payload)))(fns.get(k, lambda payload: payload))
        out["trace_policy"] = TracePolicy(**fns)
    return out


def apply_node_defaults(builder: StateGraph, defaults: dict[str, Any] | None) -> None:
    """Workflow `settings.node_defaults` -> `StateGraph.set_node_defaults(...)`."""
    opts = node_options(defaults)
    kw = {k: opts[k] for k in ("retry_policy", "cache_policy", "timeout") if k in opts}
    if kw:
        builder.set_node_defaults(**kw)


# -------------------------------------------------------------- resources ----
def tag_model(model: Any, model_id: str | None, resource: str | None = None) -> Any:
    """Record the catalog id (provider:model) in the model's callback metadata, so usage can be priced (M4-T11),
    and the canvas resource name, so a run's model replies can be recorded as a cassette (FR-X-13)."""
    if isinstance(model, BaseChatModel):
        extra = {k: v for k, v in (("ac_model", model_id), ("ac_resource", resource)) if v}
        if extra:
            model.metadata = {**(model.metadata or {}), **extra}
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
        workspace: Path | None = None,
        script_runner: Callable[..., Callable] | None = None,
        registry: Callable[[], Any] | None = None,
    ):
        self._overrides = overrides or {}
        self._secrets = secrets if secrets is not None else {}
        self._http_transport = http_transport
        self.workspace = workspace  # project workspace root: scripts./tools./nodes. resolve here
        self.script_runner = script_runner  # sandboxed execution of workspace scripts (M5-T4); None = in-process
        self.registry = registry  # the project's node registry (built-ins + workspace custom nodes)
        self.dry_run: set[str] | None = None  # tool names replaced by recorders (FR-X-02 dry run)
        self._cache: dict[str, Any] = {}
        self._mcp: dict[tuple[str, str], list[BaseTool]] = {}
        self._replays: dict[str, list[dict[str, Any]]] = {}  # tool resource -> recorded calls (FR-X-13 replay)

    def model(self, name: str, config: dict[str, Any]) -> BaseChatModel:
        if name in self._overrides:
            return tag_model(self._overrides[name], config.get("model"), name)
        if name not in self._cache:
            params = {k: self._resolve(v) for k, v in config.items() if k != "model"}
            self._cache[name] = tag_model(init_chat_model(config["model"], **params), config["model"], name)
        return self._cache[name]

    def tool(self, name: str, config: dict[str, Any], type_: str = "tool.python") -> Any:
        """A tool (or, for `mcp.server`, the list of tools loaded by `load_mcp`), tagged with its resource name so
        its calls are recorded (FR-X-13); a resource with recorded calls answers from them instead (replay)."""
        if name in self._replays:
            calls = ReplayQueue(self._replays[name])  # a fresh queue per graph build
            if type_ == "mcp.server":  # never contacts the server
                found: Any = replay_stubs(name, calls)
            else:
                found = replay_tool(as_tool(self._tool(name, config, type_)), name, calls)
        else:
            found = self._tool(name, config, type_)
        if isinstance(found, list):  # the MCP tools this Resources loaded: its own objects, tagged in place
            found = [tagged(t, name, in_place=True) for t in found]
        else:
            found = tagged(as_tool(found), name)
        if self.dry_run is None:
            return found
        if isinstance(found, list):  # MCP: also the live `destructive_hint`, in case "Browse tools" is stale or unused
            return [dry_run_tool(t) if t.name in self.dry_run or mcp_tool_info(t)["destructive"] else t for t in found]
        return dry_run_tool(found) if name in self.dry_run or getattr(found, "name", None) in self.dry_run else found

    def with_dry_run(self, tools: set[str]) -> Resources:
        """A copy whose named tools only record what they would have done (FR-X-02 dry run)."""
        clone = copy.copy(self)
        clone._overrides = {**self._overrides}
        clone.dry_run = set(tools)
        return clone

    def _tool(self, name: str, config: dict[str, Any], type_: str) -> Any:
        if type_ == "mcp.server":
            key = (name, _config_key(config))
            if key not in self._mcp:
                raise RuntimeError(f"MCP tools of '{name}' are not loaded yet; build the graph with abuild_graph()")
            return self._mcp[key]
        if name in self._overrides:
            return self._overrides[name]
        if type_ == "tool.http":
            return http_tool(name=name, config=config, resolve=self._resolve, transport=self._http_transport)
        return safe_import(config["import_path"], self.workspace)

    def revision(self) -> str:
        """Identifies the workspace code a graph was built from (graphs are rebuilt when it changes)."""
        from agentcanvas.workspace import Workspace

        return Workspace(self.workspace).revision() if self.workspace and self.workspace.exists() else ""

    def with_mocks(self, mocks: dict[str, list[Any]]) -> Resources:
        """A copy whose named chat models answer with scripted replies (debug "mock mode", M4-T9), and whose named
        tool resources answer with recorded outputs (FR-X-13 replay) instead of being called.

        A model reply is text or {"content": ..., "tool_calls": [{"name", "args"}]}; the last reply repeats. A tool
        recording is {"tool", "args", "output" | "error"} (see `ReplayQueue`)."""
        from langchain_core.messages import AIMessage

        from agentcanvas.adapters.langchain.testing import MockChatModel

        def message(i: int, reply: Any) -> AIMessage:
            if isinstance(reply, dict):
                calls = [
                    {"name": c["name"], "args": c.get("args", {}), "id": f"mock_{i}_{j}"}
                    for j, c in enumerate(reply.get("tool_calls") or [])
                ]
                return AIMessage(content=str(reply.get("content", "")), tool_calls=calls)
            return AIMessage(content=str(reply))

        clone = Resources(
            {**self._overrides}, self._secrets, self._http_transport, self.workspace, self.script_runner, self.registry
        )
        clone._mcp = self._mcp
        clone._replays = {**self._replays}
        for name, replies in mocks.items():
            if replies and all(is_tool_recording(r) for r in replies):
                clone._replays[name] = list(replies)
                continue
            clone._overrides[name] = MockChatModel(replies=[message(i, r) for i, r in enumerate(replies or [""])])
        return clone

    async def load_mcp(self, name: str, config: dict[str, Any], *, refresh: bool = False) -> list[BaseTool]:
        """Discover an MCP server's tools (network I/O, so it happens before the synchronous graph build)."""
        if name in self._replays:  # replayed: the recorded tools stand in, the server is not contacted
            return replay_stubs(name, ReplayQueue(self._replays[name]))
        key = (name, _config_key(config))
        if refresh or key not in self._mcp:
            target = self._overrides.get(name) or mcp_target(config, self._resolve)
            tools = await mcp_tools(target)
            wanted = config.get("tool_filter") or []
            self._mcp[key] = [t for t in tools if not wanted or t.name in wanted]
        return self._mcp[key]

    def middleware(
        self,
        name: str,
        type_: str,
        config: dict[str, Any],
        *,
        models: Sequence[BaseChatModel] = (),
        tools: Sequence[Any] = (),
    ) -> AgentMiddleware:
        if name in self._overrides:
            return self._overrides[name]
        return middleware(type_, config, models=models, tools=tools)

    def _resolve(self, value: Any) -> Any:
        if isinstance(value, dict) and "$secret" in value:
            key = value["$secret"]
            if key not in self._secrets:
                raise KeyError(f"secret '{key}' is not set for this project")
            return self._secrets[key]
        return value


# ------------------------------------------------------------ dry run (FR-X-02) ----
def dry_run_tool(t: Any) -> Any:
    """The same tool (name, description, arguments) that records the call instead of making it."""
    if not isinstance(t, BaseTool):
        return t

    async def record(**kwargs: Any) -> str:
        args = json.loads(json.dumps(kwargs, default=str))
        try:
            get_stream_writer()({"ac_dry_run": {"tool": t.name, "args": args}})
        except Exception:  # noqa: BLE001 - outside a graph run there is no stream to record to
            pass
        return f"[dry run] '{t.name}' was not called. It would have received: {json.dumps(args)[:800]}"

    return StructuredTool.from_function(
        coroutine=record, name=t.name, description=t.description, args_schema=t.args_schema
    )


# ------------------------------------------------------- record/replay (FR-X-13) ----
def as_tool(t: Any) -> Any:
    """A workspace tool function as the tool the agent would make of it (anything else unchanged)."""
    if isinstance(t, BaseTool) or not callable(t):
        return t
    from langchain_core.tools import tool as make_tool

    return make_tool(t)


def tagged(t: Any, resource: str, *, in_place: bool = False) -> Any:
    """The tool carrying its resource name, so `UsageTracker` records its calls for replay (a copy unless
    `in_place`: a workspace module's tool object is shared)."""
    if not isinstance(t, BaseTool) or (t.metadata or {}).get("ac_resource") == resource:
        return t
    metadata = {**(t.metadata or {}), "ac_resource": resource}
    if in_place:
        t.metadata = metadata
        return t
    return t.model_copy(update={"metadata": metadata})


def is_tool_recording(r: Any) -> bool:
    return isinstance(r, dict) and "tool" in r and ("output" in r or "error" in r) and "content" not in r


class ReplayQueue:
    """A tool resource's recorded calls. A call takes the first unused recording of that tool with the same
    arguments, else the next unused one of that tool, else reuses one with the same arguments (a resumed run
    rebuilds its graph); with none left it is a tool error, never a real call."""

    def __init__(self, recordings: list[dict[str, Any]]):
        self.recordings = list(recordings)
        self.used = [False] * len(self.recordings)

    def take(self, tool: str, args: dict[str, Any]) -> Any:
        same = [i for i, r in enumerate(self.recordings) if r.get("tool") == tool]
        exact = [i for i in same if self.recordings[i].get("args") == args]
        pick = next((i for i in exact + same if not self.used[i]), exact[0] if exact else None)
        if pick is None:
            raise ToolException(f"no recorded output of '{tool}' left to replay")
        self.used[pick] = True
        r = self.recordings[pick]
        if "error" in r:
            raise ToolException(str(r["error"]))
        return r.get("output")

    def tools(self) -> list[str]:
        return list(dict.fromkeys(str(r.get("tool")) for r in self.recordings))


def replay_tool(t: Any, resource: str, calls: ReplayQueue) -> Any:
    """The same tool (name, description, arguments) answering from `calls` instead of running."""
    if not isinstance(t, BaseTool):
        return t

    async def replay(**kwargs: Any) -> Any:
        return calls.take(t.name, json.loads(json.dumps(kwargs, default=str)))

    return StructuredTool.from_function(
        coroutine=replay, name=t.name, description=t.description, args_schema=t.args_schema, handle_tool_error=True
    )


def replay_stubs(resource: str, calls: ReplayQueue) -> list[BaseTool]:
    """An MCP server's recorded tools, replayed (their schemas are not recorded: any arguments are accepted)."""
    schema = {"type": "object", "properties": {}, "additionalProperties": True}

    def stub(tool: str) -> BaseTool:
        async def replay(**kwargs: Any) -> Any:
            return calls.take(tool, json.loads(json.dumps(kwargs, default=str)))

        return StructuredTool.from_function(
            coroutine=replay, name=tool, description=f"{tool} ({resource}, replayed)", args_schema=schema,
            handle_tool_error=True, metadata={"ac_resource": resource},
        )  # fmt: skip

    return [stub(name) for name in calls.tools()]


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


async def _public_address(host: str, port: int) -> str:
    """One address `host` resolves to, refusing hosts that resolve to a private, loopback, link-local (cloud
    metadata, 169.254.169.254), reserved or multicast address (S7). The caller connects to this very address, so
    a second DNS answer cannot point the request elsewhere (rebinding)."""
    import asyncio
    import ipaddress
    import socket

    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ToolException(f"cannot resolve '{host}': {exc}") from None
    addresses = [str(info[4][0]) for info in infos]
    for a in addresses:
        if not ipaddress.ip_address(a.split("%")[0]).is_global:
            raise ToolException(f"'{host}' resolves to a private or internal address ({a}); refused")
    return addresses[0]


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
        extensions: dict[str, Any] = {}
        if transport is None and not config.get("allow_private_network"):  # tests inject a transport
            port = parts.port or (443 if parts.scheme == "https" else 80)
            ip = await _public_address(parts.hostname, port)
            headers.setdefault("Host", parts.netloc.rsplit("@", 1)[-1])
            extensions["sni_hostname"] = parts.hostname  # TLS still checks the certificate of the name
            url = parts._replace(netloc=f"[{ip}]:{port}" if ":" in ip else f"{ip}:{port}").geturl()
        async with httpx.AsyncClient(timeout=config.get("timeout", 20), transport=transport) as client:
            r = await client.request(
                method,
                url,
                headers=headers,
                params=args if send == "query" else None,
                json=args if send == "json" else None,
                extensions=extensions or None,
            )  # redirects are not followed: a redirect cannot leave the allowed domains
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


def middleware(
    type_: str, config: dict[str, Any], *, models: Sequence[BaseChatModel] = (), tools: Sequence[Any] = ()
) -> Any:
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
    if type_ == "mw.rubric":
        if not models:
            raise ValueError("a rubric needs a wired grader model")
        return rubric_middleware(
            c["rubric"], models[0], tools=tools, max_iterations=c.get("max_iterations", 3),
            instructions=c.get("grader_instructions") or None,
        )  # fmt: skip
    raise ValueError(f"unknown middleware type '{type_}'")


def rubric_middleware(
    rubric: str,
    model: BaseChatModel,
    *,
    tools: Sequence[Any] = (),
    max_iterations: int = 3,
    instructions: str | None = None,
) -> Any:
    """deepagents `RubricMiddleware` with the rubric authored on the canvas (FR-EVAL-08).

    Upstream grades only when the caller puts a `rubric` in the agent's input state; a canvas agent reads its
    parent's `messages` alone, so this one supplies its own rubric when the state has none. Its grading events
    (`rubric_evaluation_start` / `_end` on the custom stream) become `ac.rubric` events."""
    from deepagents.middleware import RubricMiddleware

    class CanvasRubric(RubricMiddleware):
        def _with_rubric(self, state: Any) -> tuple[Any, dict[str, Any]]:
            if state.get("rubric"):
                return state, {}
            return {**state, "rubric": rubric}, {"rubric": rubric}

        def before_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
            state, extra = self._with_rubric(state)
            return {**extra, **(super().before_agent(state, runtime) or {})} or None

        async def abefore_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
            state, extra = self._with_rubric(state)
            return {**extra, **(await super().abefore_agent(state, runtime) or {})} or None

    return CanvasRubric(
        model=model, tools=list(tools) or None, max_iterations=max_iterations, system_prompt=instructions
    )


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


ORG_KEY = "agentcanvas_org_id"  # run config: the organisation whose memories a run reads and writes


def org_prefix(org_id: str | None) -> tuple[str, ...]:
    """Long-term memory of an organisation lives under ("org:<id>", ...); exported apps run without one."""
    return (f"org:{org_id}",) if org_id else ()


def store_namespace(*, scope: str, namespace: str, workflow_id: str, user_key: str | None) -> Callable:
    """Namespace factory for a long-term folder: never derived from model output (E072).

    Inside the platform every namespace starts with the run's organisation, so organisations never share memory.
    """

    def factory(rt: Any) -> tuple[str, ...]:
        configurable = get_config().get("configurable", {})
        if scope == "workflow":
            owner = workflow_id
        elif scope == "thread":
            owner = configurable.get("thread_id")
        else:
            ctx = getattr(rt, "context", None) or {}
            owner = ctx.get(user_key) if user_key and isinstance(ctx, dict) else None
            if not owner:
                raise PermissionError("a user-scoped folder needs the run's user (context field with source 'user')")
        return (*org_prefix(configurable.get(ORG_KEY)), namespace, scope, str(owner))

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


def script_node(*, import_path: str, resources: Resources | None = None) -> Callable:
    """User code from the Workspace: 'package.module:function', signature (state) -> dict (sync or async).

    With a sandbox configured (M5-T4) each call runs in an isolated process instead of in this one.
    """
    if resources is not None and resources.script_runner is not None and import_path.startswith("scripts."):
        return resources.script_runner(import_path)
    return ensure_async(safe_import(import_path, resources.workspace if resources else None))


def subgraph_node(*, graph: Any, inputs: Sequence[str], outputs: Sequence[str], append: Sequence[str] = ()) -> Callable:
    """Run a compiled inner workflow as one step (M2-T13): copy `inputs` in, copy `outputs` back.

    For outer `append` channels only the items added inside are returned (the inner copy starts with the outer
    items, which the outer reducer would otherwise append a second time). The inner graph inherits the parent's
    checkpointer, so interrupts inside it pause and resume the whole run.
    """

    async def run(state: Any) -> dict:
        given = {k: state[k] for k in inputs if k in state}
        out = await graph.ainvoke(given)
        update: dict[str, Any] = {}
        for k in outputs:
            if k not in out:
                continue
            value = out[k]
            if k in append and isinstance(value, list):
                value = value[len(given.get(k) or []) :]
            update[k] = value
        return update

    return run


def custom_node(*, import_path: str, config: dict[str, Any], resources: Resources | None = None) -> Callable:
    """A workspace custom node (M5-T7): `fn(state, cfg)` or `fn(state, cfg, runtime)`, sync or async.

    `cfg` is the node's config — an instance of the declared Pydantic model when there is one, else a dict.
    """
    if resources is not None and resources.script_runner is not None:
        return resources.script_runner(import_path, config)  # sandboxed: cfg arrives as a dict
    fn = safe_import(import_path, resources.workspace if resources else None)
    model: Any = (getattr(fn, "__canvas_node__", {}) or {}).get("config")
    cfg = model.model_validate(config) if model is not None and hasattr(model, "model_validate") else dict(config)
    with_runtime = "runtime" in inspect.signature(fn).parameters

    async def run(state: Any, runtime: Runtime) -> Any:
        args = (state, cfg, runtime) if with_runtime else (state, cfg)
        if inspect.iscoroutinefunction(fn):
            return await fn(*args)
        return await asyncio.to_thread(fn, *args)

    run.__name__ = getattr(fn, "__name__", "custom_node")
    return run


def pinned_node(output: dict[str, Any]) -> Callable:
    """Debug runs (M4-T9): replay a pinned output instead of running the node (no model call, no side effect)."""

    async def run(state: Any) -> dict:
        return copy.deepcopy(output)

    return run


async def passthrough(state: Any) -> dict:
    """Routers and bypassed (disabled) nodes are real graph nodes so they show on the canvas; they change nothing."""
    return {}
