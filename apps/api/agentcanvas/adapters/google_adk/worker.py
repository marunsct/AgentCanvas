"""The Google ADK worker (S29): runs ADK workflows for an AgentCanvas server, in a Python environment with ADK.

ADK cannot be installed next to the server's own packages (its LiteLLM pins an older `openai`), so the server starts
this module in an environment of its own (`make framework FW=google_adk`) and talks to it in JSON lines, exactly
as with the CrewAI worker: the server's messages on stdin, the worker's on the original stdout.

Server -> worker: `{"op": "run", "run": id, "source": <generated module>, "input"|"resume": ..., "models": {...},
"tools": {...}}`, `{"op": "cancel", "run": id}` and `{"reply": n, "value"|"error": ...}`.
Worker -> server: `{"run": id, "event": {...}}`, `{"run": id, "call": n, "op": "tool"|"search"|"script", "args":
{...}}` (the server answers with a reply) and `{"run": id, "end": true}`.

The canvas models are served by the server's model bridge (an OpenAI-compatible address with a key per step), so
every call is traced and costed there; tools, knowledge bases and workspace code stay on the server too.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import sys
import threading
import types
from typing import Any

_PROTOCOL = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")
os.dup2(2, 1)  # anything printed goes to stderr
sys.stdout = sys.stderr

from google.adk.models.lite_llm import LiteLlm  # noqa: E402

from agentcanvas.adapters.google_adk import runtime as rt  # noqa: E402

MAX_LINE = 64 * 1024 * 1024
_TYPES: dict[str, Any] = {"string": str, "integer": int, "number": float, "boolean": bool, "array": list,
                          "object": dict}  # fmt: skip


class Worker:
    def __init__(self) -> None:
        self.loop = asyncio.get_running_loop()
        self._send_lock = threading.Lock()
        self._calls: dict[int, asyncio.Future[Any]] = {}
        self._next = 0
        self.runs: dict[str, asyncio.Task[None]] = {}
        self._canvases: dict[str, Any] = {}

    def send(self, msg: dict[str, Any]) -> None:
        line = json.dumps(msg, default=str)
        with self._send_lock:
            _PROTOCOL.write(line + "\n")
            _PROTOCOL.flush()

    async def call(self, run_id: str, op: str, **args: Any) -> Any:
        """Ask the server to do something for a run (call a tool, search, run workspace code)."""
        self._next += 1
        n = self._next
        future = self.loop.create_future()
        self._calls[n] = future
        self.send({"run": run_id, "call": n, "op": op, "args": args})
        return await future

    def reply(self, msg: dict[str, Any]) -> None:
        future = self._calls.pop(msg["reply"], None)
        if future is None or future.done():
            return
        if "error" in msg:
            future.set_exception(RuntimeError(str(msg["error"])))
        else:
            future.set_result(msg.get("value"))

    def canvas(self, source: str) -> Any:
        key = hashlib.sha256(source.encode()).hexdigest()
        if key not in self._canvases:
            module = types.ModuleType(f"agentcanvas_adk_{key[:16]}")
            sys.modules[module.__name__] = module  # Pydantic resolves the output classes through their module
            exec(compile(source, "<adk workflow>", "exec"), module.__dict__)  # the server's generated code
            self._canvases[key] = module.CANVAS
            while len(self._canvases) > 64:
                old = next(iter(self._canvases))
                self._canvases.pop(old)
                sys.modules.pop(f"agentcanvas_adk_{old[:16]}", None)
        return self._canvases[key]

    async def run(self, msg: dict[str, Any]) -> None:
        run_id = msg["run"]
        try:
            canvas = self.canvas(msg["source"])
            host = RemoteHost(self, run_id, msg.get("models") or {}, msg.get("tools") or {})
            resume = None
            if msg.get("resume") is not None:
                resume = (rt.Paused.from_json(msg["resume"]["paused"]), msg["resume"]["answer"])
            thread = msg.get("thread", run_id)
            async for ev in rt.run(canvas, host, input=msg.get("input"), resume=resume, run_id=thread):
                paused = ev.pop("paused", None)
                if paused is not None:
                    ev["paused"] = paused.to_json()
                self.send({"run": run_id, "event": ev})
        except Exception as exc:  # noqa: BLE001 - the server shows it as RUN_ERROR
            self.send({"run": run_id, "event": {"type": "RUN_ERROR", "code": "error",
                                                "message": f"{type(exc).__name__}: {exc}"}})  # fmt: skip
        finally:
            self.runs.pop(run_id, None)
            self.send({"run": run_id, "end": True})


class RemoteHost:
    """A run's models, tools, knowledge bases and code, all on the server."""

    def __init__(self, worker: Worker, run_id: str, models: dict[str, Any], tools: dict[str, Any]):
        self.worker, self.run_id, self.models, self.tool_specs = worker, run_id, models, tools

    def llm(self, resource: str, step: str, **settings: Any) -> LiteLlm:
        found = self.models.get(f"{resource}|{step}")
        if found is None:
            raise KeyError(f"the step '{step}' has no AI model '{resource}' wired in")
        return LiteLlm(model="openai/canvas", api_base=found["base_url"], api_key=found["api_key"], num_retries=0,
                       **settings)  # fmt: skip

    def tools(self, resources: list[str], step: str) -> list[Any]:
        return [remote_tool(self, res, spec, step) for res in resources for spec in self.tool_specs.get(res, [])]

    async def search(
        self, knowledge_base: str, query: str, *, mode: str, top_k: int, min_score: float
    ) -> list[dict[str, Any]]:
        return await self.worker.call(
            self.run_id, "search", knowledge_base=knowledge_base, query=query, mode=mode, top_k=top_k,
            min_score=min_score,
        )  # fmt: skip

    async def script(self, import_path: str, state: dict[str, Any]) -> dict[str, Any]:
        return await self.worker.call(self.run_id, "script", import_path=import_path, state=state)


def _hint(prop: Any) -> Any:
    t = prop.get("type") if isinstance(prop, dict) else None
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), None)
    return _TYPES.get(str(t), Any)


def remote_tool(host: RemoteHost, resource: str, spec: dict[str, Any], step: str) -> Any:
    """A canvas tool as an ADK function tool (a plain async function with a real signature and docstring): each
    call runs on the server, recorded and replayed like any tool call."""
    schema = spec.get("parameters") or {}
    props: dict[str, Any] = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    params, annotations, doc_args = [], {}, []
    for arg in sorted(props, key=lambda a: a not in required):
        hint = _hint(props[arg])
        if arg in required:
            params.append(inspect.Parameter(arg, inspect.Parameter.KEYWORD_ONLY, annotation=hint))
        else:
            hint = hint | None if hint is not Any else Any
            params.append(inspect.Parameter(arg, inspect.Parameter.KEYWORD_ONLY, annotation=hint, default=None))
        annotations[arg] = hint
        if isinstance(props[arg], dict) and props[arg].get("description"):
            doc_args.append(f"    {arg}: {props[arg]['description']}")
    target = {"resource": resource, "tool": spec["name"], "step": step}

    async def fn(**kwargs: Any) -> str:
        args = {k: v for k, v in kwargs.items() if v is not None or k in required}
        out = await host.worker.call(host.run_id, "tool", args=args, **target)
        return out if isinstance(out, str) else json.dumps(out, default=str)

    fn.__name__ = fn.__qualname__ = spec["name"]
    fn.__doc__ = (spec.get("description") or spec["name"]) + ("\n\nArgs:\n" + "\n".join(doc_args) if doc_args else "")
    fn.__signature__ = inspect.Signature(params, return_annotation=str)  # type: ignore[attr-defined]
    fn.__annotations__ = {**annotations, "return": str}
    return fn


async def main() -> None:
    worker = Worker()
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=MAX_LINE)
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
    from google.adk import __version__

    worker.send({"ready": True, "version": __version__})
    while True:
        try:
            line = await reader.readline()
            if not line:
                break
            msg = json.loads(line)
        except ValueError:  # a bad or over-long line must not end every run this worker serves
            continue
        if not isinstance(msg, dict):
            continue
        if "reply" in msg:
            worker.reply(msg)
        elif msg.get("op") == "run":
            worker.runs[msg["run"]] = asyncio.create_task(worker.run(msg))
        elif msg.get("op") == "cancel" and msg.get("run") in worker.runs:
            worker.runs[msg["run"]].cancel()
    for task in list(worker.runs.values()):  # the server went away
        task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
