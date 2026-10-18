"""The CrewAI worker (S27): runs CrewAI flows for an AgentCanvas server, in a Python environment with CrewAI.

CrewAI cannot be installed next to the server's own packages (it pins an older `openai`), so the server starts this
module in an environment of its own and talks to it in JSON lines: the server's messages on stdin, the worker's on
the original stdout (CrewAI's console output goes to stderr). One worker runs many flows at once.

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
import json
import os
import sys
import threading
import types
from typing import Any

_PROTOCOL = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")
os.dup2(2, 1)  # anything printed (CrewAI's panels) goes to stderr
sys.stdout = sys.stderr

from crewai import LLM  # noqa: E402
from crewai.tools import BaseTool  # noqa: E402
from pydantic import Field, create_model  # noqa: E402

from agentcanvas.adapters.crewai import runtime as rt  # noqa: E402

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

    def call_sync(self, run_id: str, op: str, **args: Any) -> Any:
        """`call` from a thread CrewAI runs a synchronous tool in."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run_coroutine_threadsafe(self.call(run_id, op, **args), self.loop).result()
        raise RuntimeError("a synchronous tool call cannot wait inside the event loop; CrewAI should use _arun")

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
            module = types.ModuleType(f"agentcanvas_crew_{key[:16]}")
            sys.modules[module.__name__] = module  # Pydantic resolves the output classes through their module
            exec(compile(source, "<crewai flow>", "exec"), module.__dict__)  # the server's generated code
            self._canvases[key] = module.CANVAS
            while len(self._canvases) > 64:
                old = next(iter(self._canvases))
                self._canvases.pop(old)
                sys.modules.pop(f"agentcanvas_crew_{old[:16]}", None)
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

    def llm(self, resource: str, step: str) -> LLM:
        found = self.models.get(f"{resource}|{step}")
        if found is None:
            raise KeyError(f"the step '{step}' has no AI model '{resource}' wired in")
        return LLM(model="openai/canvas", base_url=found["base_url"], api_key=found["api_key"], max_retries=0)

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


def args_model(name: str, schema: dict[str, Any]) -> Any:
    """A Pydantic model for a tool's JSON-schema arguments (CrewAI tools describe their arguments that way)."""
    required = set(schema.get("required") or [])
    fields: dict[str, Any] = {}
    for key, prop in (schema.get("properties") or {}).items():
        kind = _TYPES.get(prop.get("type") if isinstance(prop, dict) else None, Any)
        desc = prop.get("description") if isinstance(prop, dict) else None
        if key in required:
            fields[key] = (kind, Field(..., description=desc))
        else:
            fields[key] = (kind | None, Field(None, description=desc))
    return create_model(f"{name.title().replace('_', '')}Args", **fields)


def remote_tool(host: RemoteHost, resource: str, spec: dict[str, Any], step: str) -> BaseTool:
    """A canvas tool as a CrewAI tool: each call runs on the server (recorded and replayed like any tool call)."""
    target = {"resource": resource, "tool": spec["name"], "step": step}

    class RemoteTool(BaseTool):
        name: str = spec["name"]
        description: str = spec.get("description") or spec["name"]
        args_schema: type = args_model(spec["name"], spec.get("parameters") or {})

        def _run(self, **kwargs: Any) -> Any:
            args = {k: v for k, v in kwargs.items() if v is not None}
            return host.worker.call_sync(host.run_id, "tool", args=args, **target)

        async def _arun(self, **kwargs: Any) -> Any:
            args = {k: v for k, v in kwargs.items() if v is not None}
            return await host.worker.call(host.run_id, "tool", args=args, **target)

    return RemoteTool()


async def main() -> None:
    worker = Worker()
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=MAX_LINE)
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
    import crewai

    worker.send({"ready": True, "crewai": crewai.__version__})
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
