"""The canvas model, served to agents of other frameworks (S21 mixed flows).

An agent designed for another framework (`agentcanvas.designs`) talks to its model over the OpenAI chat-completions
API, or the Anthropic messages API for the Claude Agent SDK. Inside AgentCanvas that address is this bridge: a small
HTTP server on 127.0.0.1, in the same event loop as the run, that answers each request with the LangChain chat model
of the canvas model block. So every catalog model (local and scripted ones included) reaches every framework, and the
calls are traced, costed and recorded like any other model call of the run.

Each step registers its model under a random key, which the agent sends as its API key; the key is dropped when the
step ends, so the bridge only answers the steps that are running.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
import time
import uuid
import weakref
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

MAX_BODY = 32 * 1024 * 1024


@dataclass
class _Entry:
    model: Any  # a LangChain chat model
    config: Any  # the run's config, so calls carry its callbacks (traces, usage, token streaming)


class ModelBridge:
    def __init__(self) -> None:
        self._models: dict[str, _Entry] = {}
        self._server: asyncio.Server | None = None
        self.port = 0

    @property
    def root(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def close_sockets(self) -> None:
        """Stop listening (for a bridge whose event loop has closed: no awaiting possible)."""
        for sock in self._server.sockets if self._server else ():
            with contextlib.suppress(OSError):
                getattr(sock, "_sock", sock).close()  # asyncio wraps it in a TransportSocket
        self._server = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    @contextlib.contextmanager
    def serving(self, model: Any, config: Any = None):
        """Serve `model` while the block runs; yields the key the agent sends as its API key."""
        key = "ac-" + secrets.token_urlsafe(24)
        self._models[key] = _Entry(model, config)
        try:
            yield key
        finally:
            self._models.pop(key, None)

    # ------------------------------------------------------------------ HTTP ----
    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            lines = head.decode("latin-1").split("\r\n")
            method, target, _ = [*lines[0].split(" ", 2), "", ""][:3]
            headers = {k.strip().lower(): v.strip() for k, _, v in (h.partition(":") for h in lines[1:] if h)}
            key = headers.get("x-api-key") or headers.get("authorization", "").removeprefix("Bearer ").strip()
            if key not in self._models and not (method == "GET" and target.split("?", 1)[0].endswith("/models")):
                return await _send(writer, 401, {"error": {"type": "authentication_error", "message": "unknown key"}})
            size = int(headers.get("content-length") or 0)
            if size > MAX_BODY:
                return await _send(writer, 413, {"error": {"message": "request too large"}})
            body = json.loads(await reader.readexactly(size) or b"{}") if size else {}
            await self._route(writer, method, target.split("?", 1)[0], headers, body)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        except Exception as exc:  # noqa: BLE001 - a broken request or a failing model: tell the agent, keep serving
            with contextlib.suppress(Exception):
                await _send(writer, 500, {"error": {"type": "api_error", "message": f"{type(exc).__name__}: {exc}"}})
        finally:
            with contextlib.suppress(Exception):
                writer.close()

    async def _route(
        self, writer: asyncio.StreamWriter, method: str, path: str, headers: dict[str, str], body: dict[str, Any]
    ) -> None:
        if method == "GET" and path.endswith("/models"):
            return await _send(writer, 200, {"object": "list", "data": [{"id": "canvas", "object": "model"}]})
        key = headers.get("x-api-key") or headers.get("authorization", "").removeprefix("Bearer ").strip()
        entry = self._models.get(key)
        if entry is None:
            return await _send(writer, 401, {"error": {"type": "authentication_error", "message": "unknown key"}})
        if method != "POST":
            return await _send(writer, 404, {"error": {"message": f"not served: {method} {path}"}})
        if path.endswith("/chat/completions"):
            return await self._openai(writer, entry, body)
        if path.endswith("/messages/count_tokens"):
            return await _send(writer, 200, {"input_tokens": len(json.dumps(body)) // 4})
        if path.endswith("/messages"):
            return await self._anthropic(writer, entry, body)
        await _send(writer, 404, {"error": {"type": "not_found_error", "message": f"not served: {path}"}})

    # ------------------------------------------------------------------ calls ----
    async def _call(
        self, entry: _Entry, messages: list[BaseMessage], tools: list[dict[str, Any]], schema: dict[str, Any] | None
    ) -> AIMessage:
        model = entry.model
        if schema is not None and not tools and getattr(model, "_llm_type", "") == "agentcanvas-demo":
            from agentcanvas.adapters.langchain.testing import _last_human, demo_answer

            return AIMessage(content=json.dumps(demo_answer(schema, _last_human(messages))))
        if schema is not None and not tools and not _scripted(model):
            out = await model.with_structured_output(schema, include_raw=True).ainvoke(messages, config=entry.config)
            raw = out.get("raw") if isinstance(out, dict) else None
            parsed = out.get("parsed") if isinstance(out, dict) else out
            dump = getattr(parsed, "model_dump", None)
            if callable(dump):
                parsed = dump()
            reply = AIMessage(content=json.dumps(parsed, default=str))
            if isinstance(raw, AIMessage):
                reply.usage_metadata = raw.usage_metadata
            return reply
        if schema is not None and tools:  # tools and a typed answer: ask for JSON in the instructions
            note = "When you give your final answer, reply with only JSON matching this schema: " + json.dumps(schema)
            messages = [SystemMessage(content=note), *messages]
        runnable = model.bind_tools(tools) if tools else model
        reply = await runnable.ainvoke(messages, config=entry.config)
        if not isinstance(reply, AIMessage):
            reply = AIMessage(content=getattr(reply, "content", str(reply)))
        if reply.tool_calls and _scripted(model):  # scripts name canvas tools; some frameworks prefix them
            names = [t["function"]["name"] for t in tools]
            for call in reply.tool_calls:
                found = [n for n in names if n.endswith("__" + call["name"])]
                if call["name"] not in names and len(found) == 1:
                    call["name"] = found[0]
        return reply

    async def _openai(self, writer: asyncio.StreamWriter, entry: _Entry, body: dict[str, Any]) -> None:
        tools = [t for t in body.get("tools") or [] if t.get("type") == "function"]
        fmt = body.get("response_format") or {}
        schema = (fmt.get("json_schema") or {}).get("schema") if fmt.get("type") == "json_schema" else None
        reply = await self._call(entry, _from_openai(body.get("messages") or []), tools, schema)
        text, calls = _text(reply), _calls(reply)
        usage = _usage(reply)
        base = {"id": "chatcmpl-" + uuid.uuid4().hex[:24], "created": int(time.time()), "model": body.get("model", "")}
        tool_calls = [{"id": c["id"], "type": "function",
                       "function": {"name": c["name"], "arguments": json.dumps(c["args"])}} for c in calls]  # fmt: skip
        finish = "tool_calls" if calls else "stop"
        openai_usage = {"prompt_tokens": usage[0], "completion_tokens": usage[1], "total_tokens": sum(usage)}
        if not body.get("stream"):
            message: dict[str, Any] = {"role": "assistant", "content": text or (None if calls else "")}
            if tool_calls:
                message["tool_calls"] = tool_calls
            choice = {"index": 0, "message": message, "finish_reason": finish, "logprobs": None}
            return await _send(writer, 200, {**base, "object": "chat.completion", "choices": [choice],
                                             "usage": openai_usage})  # fmt: skip
        chunk = {**base, "object": "chat.completion.chunk"}
        delta: dict[str, Any] = {"role": "assistant", "content": text or ""}
        if tool_calls:
            delta["tool_calls"] = [{**c, "index": i} for i, c in enumerate(tool_calls)]
        events: list[Any] = [
            {**chunk, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
            {**chunk, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
        ]
        if (body.get("stream_options") or {}).get("include_usage"):
            events.append({**chunk, "choices": [], "usage": openai_usage})
        await _stream(writer, [(None, e) for e in events] + [(None, "[DONE]")])

    async def _anthropic(self, writer: asyncio.StreamWriter, entry: _Entry, body: dict[str, Any]) -> None:
        tools = [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                                   "parameters": t.get("input_schema") or {"type": "object"}}}
                 for t in body.get("tools") or [] if t.get("name") and "input_schema" in t]  # fmt: skip
        fmt = body.get("output_format") or {}
        schema = fmt.get("schema") if fmt.get("type") == "json_schema" else None
        reply = await self._call(entry, _from_anthropic(body), tools, schema)
        text, calls = _text(reply), _calls(reply)
        content: list[dict[str, Any]] = [{"type": "text", "text": text}] if text or not calls else []
        content += [{"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["args"]} for c in calls]
        usage = _usage(reply)
        msg = {"id": "msg_" + uuid.uuid4().hex[:24], "type": "message", "role": "assistant",
               "model": body.get("model", ""), "stop_sequence": None}  # fmt: skip
        stop = "tool_use" if calls else "end_turn"
        if not body.get("stream"):
            tokens = {"input_tokens": usage[0], "output_tokens": usage[1]}
            return await _send(writer, 200, {**msg, "content": content, "stop_reason": stop, "usage": tokens})
        start_usage = {"input_tokens": usage[0], "output_tokens": 0}
        first = {**msg, "content": [], "stop_reason": None, "usage": start_usage}
        events: list[tuple[str | None, Any]] = [("message_start", {"type": "message_start", "message": first})]
        for i, block in enumerate(content):
            if block["type"] == "text":
                start = {"type": "text", "text": ""}
                delta = {"type": "text_delta", "text": block["text"]}
            else:
                start = {"type": "tool_use", "id": block["id"], "name": block["name"], "input": {}}
                delta = {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}
            events += [
                ("content_block_start", {"type": "content_block_start", "index": i, "content_block": start}),
                ("content_block_delta", {"type": "content_block_delta", "index": i, "delta": delta}),
                ("content_block_stop", {"type": "content_block_stop", "index": i}),
            ]
        events += [
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                               "usage": {"output_tokens": usage[1]}}),
            ("message_stop", {"type": "message_stop"}),
        ]  # fmt: skip
        await _stream(writer, events)


# ------------------------------------------------------------------ one per event loop ----
_BRIDGES: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, ModelBridge] = weakref.WeakKeyDictionary()


async def model_bridge() -> ModelBridge:
    """The bridge of the running event loop (started on first use)."""
    loop = asyncio.get_running_loop()
    for old in [lp for lp in _BRIDGES if lp.is_closed()]:  # the bridge holds its loop: free closed loops' sockets
        _BRIDGES.pop(old).close_sockets()
    bridge = _BRIDGES.get(loop)
    if bridge is None:
        bridge = _BRIDGES[loop] = ModelBridge()
        await bridge.start()
    return bridge


# ------------------------------------------------------------------ helpers ----
def _scripted(model: Any) -> bool:
    """Scripted and demo models answer from their script; they cannot be asked for a typed answer."""
    return str(getattr(model, "_llm_type", "")).startswith("agentcanvas-")


def _text(reply: AIMessage) -> str:
    content = reply.content
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content
                   if not isinstance(b, dict) or b.get("type") in (None, "text"))  # fmt: skip


def _calls(reply: AIMessage) -> list[dict[str, Any]]:
    return [{"id": c.get("id") or "call_" + uuid.uuid4().hex[:20], "name": c["name"], "args": c.get("args") or {}}
            for c in reply.tool_calls or []]  # fmt: skip


def _usage(reply: AIMessage) -> tuple[int, int]:
    u = reply.usage_metadata or {}
    return int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0)


def _plain(content: Any) -> Any:
    """Message content as LangChain takes it: text, or a list of text and image blocks."""
    if isinstance(content, list):
        texts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") in ("text", "input_text")]
        if len(texts) == len(content):
            return "\n".join(texts)
    return content if content is not None else ""


def _from_openai(messages: list[dict[str, Any]]) -> list[BaseMessage]:
    out: list[BaseMessage] = []
    for m in messages:
        role = m.get("role")
        if role in ("system", "developer"):
            out.append(SystemMessage(content=_plain(m.get("content"))))
        elif role == "user":
            out.append(HumanMessage(content=_plain(m.get("content"))))
        elif role == "assistant":
            calls = []
            for c in m.get("tool_calls") or []:
                fn = c.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                calls.append({"id": c.get("id"), "name": fn.get("name", ""), "args": args if isinstance(args, dict)
                              else {"value": args}})  # fmt: skip
            out.append(AIMessage(content=_plain(m.get("content")) or "", tool_calls=calls))
        elif role == "tool":
            out.append(ToolMessage(content=_plain(m.get("content")), tool_call_id=m.get("tool_call_id") or ""))
    return out


def _from_anthropic(body: dict[str, Any]) -> list[BaseMessage]:
    out: list[BaseMessage] = []
    system = body.get("system")
    if system:
        out.append(SystemMessage(content=_plain(system) if isinstance(system, list) else str(system)))
    for m in body.get("messages") or []:
        content = m.get("content")
        blocks = content if isinstance(content, list) else [{"type": "text", "text": str(content or "")}]
        if m.get("role") == "assistant":
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            calls = [{"id": b.get("id"), "name": b.get("name", ""), "args": b.get("input") or {}}
                     for b in blocks if b.get("type") == "tool_use"]  # fmt: skip
            out.append(AIMessage(content=text, tool_calls=calls))
            continue
        texts = []
        for b in blocks:
            if b.get("type") == "tool_result":
                result = b.get("content")
                result = _plain(result) if isinstance(result, list) else str(result or "")
                out.append(ToolMessage(content=result, tool_call_id=b.get("tool_use_id") or "",
                                       status="error" if b.get("is_error") else "success"))  # fmt: skip
            elif b.get("type") == "text":
                texts.append(b.get("text", ""))
        if texts:
            out.append(HumanMessage(content="\n".join(texts)))
    return out


async def _send(writer: asyncio.StreamWriter, status: int, body: Any) -> None:
    data = json.dumps(body).encode()
    reason = {200: "OK", 401: "Unauthorized", 404: "Not Found", 413: "Too Large", 500: "Error"}.get(status, "OK")
    writer.write(
        f"HTTP/1.1 {status} {reason}\r\ncontent-type: application/json\r\ncontent-length: {len(data)}\r\n"
        "connection: close\r\n\r\n".encode()
        + data
    )
    await writer.drain()


async def _stream(writer: asyncio.StreamWriter, events: Sequence[tuple[str | None, Any]]) -> None:
    writer.write(b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\ncache-control: no-cache\r\n"
                 b"connection: close\r\n\r\n")  # fmt: skip
    for name, data in events:
        line = (f"event: {name}\n" if name else "") + f"data: {data if isinstance(data, str) else json.dumps(data)}\n\n"
        writer.write(line.encode())
    await writer.drain()
