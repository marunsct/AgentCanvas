"""A fake model server for the exported agent's tests: no API key or network needed.

It speaks the OpenAI chat-completions and Anthropic messages APIs (both with streaming) and answers
"pong: <the last user text>". Asked for a typed answer (a JSON Schema response format, an answer tool such as
`Answer` or `final_result`, or a tool the request requires), it fills the schema with that text instead.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

ANSWER_TOOLS = {"Answer", "final_result", "StructuredOutput"}


def example(schema: dict[str, Any], text: str) -> Any:
    """A value matching `schema`, using `text` for strings."""
    if schema.get("enum"):
        return schema["enum"][0]
    t = schema.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), "string")
    if t == "object" or "properties" in schema:
        return {k: example(v, text) for k, v in (schema.get("properties") or {}).items()}
    if t == "array":
        return [example(schema.get("items") or {"type": "string"}, text)]
    return {"integer": 1, "number": 1.0, "boolean": True}.get(str(t), text)


def _user_text(messages: list[dict[str, Any]]) -> str:
    for m in reversed(messages):
        if m.get("role") != "user":
            continue
        content = m.get("content")
        if isinstance(content, str):
            return content
        texts = [p.get("text", "") for p in content or [] if isinstance(p, dict) and p.get("type") == "text"]
        if any(texts):
            return texts[-1]
    return ""


def _answered(messages: list[dict[str, Any]]) -> bool:
    """Whether a tool result is already in the conversation (then the model just answers)."""
    for m in messages:
        if m.get("role") == "tool":
            return True
        content = m.get("content")
        if isinstance(content, list) and any(isinstance(p, dict) and p.get("type") == "tool_result" for p in content):
            return True
    return False


def _forced(choice: Any) -> str | None:
    """The tool the request makes the model call: its name, "*" for any tool, or None."""
    if choice in ("required", "any") or (isinstance(choice, dict) and choice.get("type") == "any"):
        return "*"
    if isinstance(choice, dict):
        return (choice.get("function") or {}).get("name") or choice.get("name")
    return None


class Fake(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # quiet
        pass

    def _json(self, body: Any, status: int = 200) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _sse(self, events: list[tuple[str | None, Any]]) -> None:
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        for name, data in events:
            head = f"event: {name}\n" if name else ""
            self.wfile.write(f"{head}data: {data if isinstance(data, str) else json.dumps(data)}\n\n".encode())
        self.wfile.flush()

    def do_GET(self) -> None:
        self._json({"object": "list", "data": [{"id": "fake", "object": "model"}]})

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        path = self.path.split("?")[0]
        if path.endswith("/chat/completions"):
            return self._openai(body)
        if path.endswith("/count_tokens"):
            return self._json({"input_tokens": 10})
        if path.endswith("/messages"):
            return self._anthropic(body)
        self._json({"error": {"message": f"not faked: {self.path}"}}, 404)

    def _reply(self, body: dict[str, Any], tools: list[tuple[str, dict[str, Any]]]) -> tuple[str, dict | None]:
        """(text, answer tool call) for this request."""
        messages = body.get("messages") or []
        text = "pong: " + _user_text(messages)
        fmt = body.get("response_format") or body.get("output_format") or {}
        schema = (fmt.get("json_schema") or {}).get("schema") or fmt.get("schema")
        if schema:
            return json.dumps(example(schema, text)), None
        forced = _forced(body.get("tool_choice"))
        for name, params in tools:
            if (name in ANSWER_TOOLS or forced in (name, "*")) and not _answered(messages):
                return "", {"id": "call_answer", "name": name, "args": example(params, text)}
        return text, None

    def _openai(self, body: dict[str, Any]) -> None:
        tools = [(t["function"]["name"], t["function"].get("parameters") or {}) for t in body.get("tools") or []]
        text, call = self._reply(body, tools)
        usage = {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}
        base = {"id": "chatcmpl-1", "created": int(time.time()), "model": body.get("model", "fake")}
        calls = []
        if call:
            fn = {"name": call["name"], "arguments": json.dumps(call["args"])}
            calls.append({"id": call["id"], "type": "function", "function": fn})
        finish = "tool_calls" if call else "stop"
        if not body.get("stream"):
            msg: dict[str, Any] = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = calls
            choice = {"index": 0, "message": msg, "finish_reason": finish}
            return self._json({**base, "object": "chat.completion", "choices": [choice], "usage": usage})
        chunk = {**base, "object": "chat.completion.chunk"}
        delta: dict[str, Any] = {"role": "assistant", "content": text}
        if calls:
            delta["tool_calls"] = [{**c, "index": 0} for c in calls]
        events: list[tuple[str | None, Any]] = [
            (None, {**chunk, "choices": [{"index": 0, "delta": delta}]}),
            (None, {**chunk, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}),
        ]
        if (body.get("stream_options") or {}).get("include_usage"):
            events.append((None, {**chunk, "choices": [], "usage": usage}))
        self._sse([*events, (None, "[DONE]")])

    def _anthropic(self, body: dict[str, Any]) -> None:
        tools = [(t["name"], t.get("input_schema") or {}) for t in body.get("tools") or [] if "name" in t]
        text, call = self._reply(body, tools)
        content: list[dict[str, Any]] = [{"type": "text", "text": text}] if text else []
        if call:
            content.append({"type": "tool_use", "id": "toolu_answer", "name": call["name"], "input": call["args"]})
        stop = "tool_use" if call else "end_turn"
        msg = {"id": "msg_1", "type": "message", "role": "assistant", "model": body.get("model", "fake"),
               "stop_sequence": None, "usage": {"input_tokens": 5, "output_tokens": 3}}  # fmt: skip
        if not body.get("stream"):
            return self._json({**msg, "content": content, "stop_reason": stop})
        events: list[tuple[str | None, Any]] = [
            ("message_start", {"type": "message_start", "message": {**msg, "content": [], "stop_reason": None}})
        ]
        for i, block in enumerate(content):
            if block["type"] == "text":
                start, delta = {"type": "text", "text": ""}, {"type": "text_delta", "text": block["text"]}
            else:
                start = {**block, "input": {}}
                delta = {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}
            events += [
                ("content_block_start", {"type": "content_block_start", "index": i, "content_block": start}),
                ("content_block_delta", {"type": "content_block_delta", "index": i, "delta": delta}),
                ("content_block_stop", {"type": "content_block_stop", "index": i}),
            ]
        events += [
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                               "usage": {"output_tokens": 3}}),
            ("message_stop", {"type": "message_stop"}),
        ]  # fmt: skip
        self._sse(events)


def serve() -> str:
    """Start the server in a background thread; returns its address, e.g. http://127.0.0.1:50123."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}"
