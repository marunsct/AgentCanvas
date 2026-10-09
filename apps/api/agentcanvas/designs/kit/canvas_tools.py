"""Tools designed on AgentCanvas, as plain Python functions any agent framework can use.

Shipped with every single-agent export (S21). A function's name, docstring and type hints tell the model what the tool
does; `agent_tools.py` lists the agent's tools. Web API tools only reach their allowed domains, MCP tools are asked
from their server when the agent starts, and tools marked for approval ask on the terminal before they run.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import typing
from collections.abc import Callable
from typing import Any, Literal
from urllib.parse import quote, urlsplit

_TYPES: dict[str, Any] = {"string": str, "integer": int, "number": float, "boolean": bool}


def _type(prop: Any) -> Any:
    if not isinstance(prop, dict):
        return Any
    if prop.get("enum") and all(isinstance(v, str) for v in prop["enum"]):
        return Literal[tuple(prop["enum"])]  # type: ignore[valid-type]
    t = prop.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), None)
    if t in _TYPES:
        return _TYPES[t]
    if t == "array":
        return list[_type(prop.get("items") or {})]  # type: ignore[misc]
    if t == "object":
        return dict[str, Any]
    return Any


def function(name: str, description: str, schema: dict[str, Any] | None, call: Callable[[dict], Any]) -> Callable:
    """An async function called `name` whose keyword arguments follow the JSON Schema `schema`."""
    schema = schema or {}
    props: dict[str, Any] = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    params, notes = [], []
    for arg in sorted(props, key=lambda a: a not in required):
        hint = _type(props[arg])
        if arg in required:
            params.append(inspect.Parameter(arg, inspect.Parameter.KEYWORD_ONLY, annotation=hint))
        else:
            params.append(inspect.Parameter(arg, inspect.Parameter.KEYWORD_ONLY, annotation=hint | None, default=None))
        if props[arg].get("description"):
            notes.append(f"    {arg}: {props[arg]['description']}")

    async def fn(**kwargs: Any) -> str:
        out = call({k: v for k, v in kwargs.items() if v is not None or k in required})
        if inspect.isawaitable(out):
            out = await out
        return out if isinstance(out, str) else json.dumps(out, default=str)

    fn.__name__ = fn.__qualname__ = re.sub(r"\W", "_", name)
    fn.__doc__ = description + ("\n\nArgs:\n" + "\n".join(notes) if notes else "")
    fn.__signature__ = inspect.Signature(params, return_annotation=str)  # type: ignore[attr-defined]
    fn.__annotations__ = {p.name: p.annotation for p in params} | {"return": str}
    return fn


def secret(value: Any) -> str:
    """A header value, or the environment variable a `{"$secret": NAME}` reference names."""
    if isinstance(value, dict) and "$secret" in value:
        name = value["$secret"]
        if name not in os.environ:
            raise RuntimeError(f"set the environment variable {name} (see .env.example)")
        return os.environ[name]
    return str(value)


def _allowed(host: str, domains: list[str]) -> bool:
    host = host.lower().rstrip(".")
    return any(host == d.lower() or host.endswith("." + d.lower()) for d in domains)


async def _private(host: str) -> bool:
    """Whether `host` resolves to a private, local or cloud-metadata address (as the canvas refuses them too)."""
    import asyncio
    import ipaddress
    import socket

    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return False  # the request itself will fail with a clear error
    for info in infos:
        ip = ipaddress.ip_address(str(info[4][0]).split("%", 1)[0])
        if not ip.is_global:
            return True
    return False


def http_tool(name: str, config: dict[str, Any]) -> Callable:
    """A web API tool: the URL may use the tool's arguments as {{ args.name }}; other hosts are refused."""
    import httpx

    method = str(config.get("method", "GET")).upper()
    send = config.get("send_args", "auto")
    if send == "auto":
        send = "none" if method in ("GET", "DELETE") else "json"
    limit = int(config.get("max_response_chars", 20_000))

    async def call(args: dict[str, Any]) -> str:
        url = re.sub(r"{{\s*args\.(\w+)\s*}}", lambda m: quote(str(args.get(m.group(1), "")), safe=""), config["url"])
        host = urlsplit(url).hostname or ""
        if not _allowed(host, list(config.get("allowed_domains") or [])):
            return f"Error: '{host}' is not an allowed domain for {name}"
        if not config.get("allow_private_network") and await _private(host):
            return f"Error: '{host}' is a private or local address; {name} may not call it"
        headers = {k: secret(v) for k, v in (config.get("headers") or {}).items()}
        async with httpx.AsyncClient(timeout=float(config.get("timeout", 30))) as client:
            r = await client.request(method, url, headers=headers, params=args if send == "query" else None,
                                     json=args if send == "json" else None)  # fmt: skip
        if r.status_code >= 400:
            return f"Error: HTTP {r.status_code}: {r.text[:500]}"
        return r.text[:limit]

    return function(name, config.get("description") or name, config.get("args_schema"), call)


async def mcp_tools(url: str, headers: dict[str, Any] | None = None, only: list[str] | None = None) -> list[Callable]:
    """The tools of an MCP server (streamable HTTP), each opening a short session when it is called."""
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    sent = {k: secret(v) for k, v in (headers or {}).items()}

    def client() -> Client:
        return Client(StreamableHttpTransport(url, headers=sent or None))

    async with client() as c:
        listed = await c.list_tools()
    found = []
    for t in listed:
        if only and t.name not in only:
            continue

        async def call(args: dict[str, Any], tool: str = t.name) -> str:
            async with client() as c:
                result = await c.call_tool(tool, args, raise_on_error=False)
            text = "\n".join(getattr(part, "text", "") for part in result.content)
            failed = getattr(result, "is_error", None) or getattr(result, "isError", False)
            return f"Error: {text}" if failed else text

        schema = getattr(t, "input_schema", None) or getattr(t, "inputSchema", None)  # mcp 2 / mcp 1
        found.append(function(t.name, t.description or t.name, schema, call))
    return found


def approval(fn: Callable) -> Callable:
    """`fn`, asking on the terminal before each call (as the canvas would ask in its inbox)."""

    async def ask(args: dict[str, Any]) -> Any:
        question = f"Allow {fn.__name__}({json.dumps(args)})? [y/N] "
        answer = await asyncio.to_thread(input, question)
        if answer.strip().lower() not in ("y", "yes"):
            return "A person rejected this tool call. Do not try it again."
        out = fn(**args)
        return await out if inspect.isawaitable(out) else out

    wrapped = function(fn.__name__, fn.__doc__ or fn.__name__, None, ask)
    wrapped.__signature__ = inspect.signature(fn, eval_str=True)  # type: ignore[attr-defined]
    wrapped.__annotations__ = typing.get_type_hints(fn)
    return wrapped
