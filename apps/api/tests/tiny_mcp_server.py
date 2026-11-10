"""A tiny stdio MCP server for the tests (standard library only, so it runs in a bare python image too).

Tools: `remember(text)` / `recall()` (state that lives as long as the process), `whoami()` (its pid, an
environment variable and whether the proxy is set), `fetch(url)` (a GET through the environment's proxy) and
`grow(mb)` (holds that much memory). It speaks newline-delimited JSON-RPC like every stdio MCP server.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

NOTES: list[str] = []
HELD: list[bytes] = []
TOOLS = [
    {"name": "remember", "description": "Keep a note.",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "recall", "description": "The notes kept so far.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "whoami", "description": "About this process.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "fetch", "description": "GET a URL.",
     "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
    {"name": "grow", "description": "Hold memory.",
     "inputSchema": {"type": "object", "properties": {"mb": {"type": "integer"}}, "required": ["mb"]}},
]  # fmt: skip


def call(name: str, args: dict) -> str:
    if name == "remember":
        NOTES.append(args["text"])
        return "ok"
    if name == "recall":
        return json.dumps(NOTES)
    if name == "whoami":
        env = os.environ.get
        return json.dumps({
            "pid": os.getpid(), "token": env("TINY_TOKEN", ""), "proxy": env("HTTPS_PROXY", ""),
            "upstream": env("AC_EGRESS_UPSTREAM", ""), "secret": env("AGENTCANVAS_SECRET_KEY", ""),
        })  # fmt: skip
    if name == "fetch":
        try:
            with urllib.request.urlopen(args["url"], timeout=10) as r:
                return r.read().decode()
        except Exception as exc:  # noqa: BLE001 - the tests read the error
            return f"error: {exc}"
    if name == "grow":
        HELD.append(b"x" * (args["mb"] * 1024 * 1024))
        return "held"
    raise KeyError(name)


def answer(msg: dict) -> dict | None:
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:  # a notification
        return None
    if method == "initialize":
        result = {
            "protocolVersion": msg["params"].get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}}, "serverInfo": {"name": "tiny", "version": "1.0"},
        }  # fmt: skip
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        try:
            text = call(msg["params"]["name"], msg["params"].get("arguments") or {})
        except KeyError as exc:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"unknown tool {exc}"}}
        result = {"content": [{"type": "text", "text": text}], "isError": False}
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"no method {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        out = answer(json.loads(line))
        if out is not None:
            sys.stdout.write(json.dumps(out) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
