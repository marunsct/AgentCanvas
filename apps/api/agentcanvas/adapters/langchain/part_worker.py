"""A part's worker (S28): runs one agent designed for another framework, in that framework's own Python environment.

Five frameworks pin packages that clash with LangGraph's, so a mixed flow cannot import them next to its host. The
host (`parts.PartModule`) starts this file with the framework's Python instead and talks to it in JSON lines; the
file needs only the standard library and the framework, so an export copies it into each part as it is.

    python worker.py [agent.py]

Host -> worker: `{"op": "ask", "id": n, "text": ..., "tools": [{"name", "description", "parameters"}], "model",
"base_url", "api_key", "source"?}` (`source`: the agent module's code, when no file was given),
`{"reply": k, "value"|"error": ...}` and `{"op": "cancel", "id": n}` (the step was stopped: so is the ask).
Worker -> host: `{"ready": true, "python": ...}`, then `{"id": n, "call": k, "tool": name, "args": {...}}` for each
tool call (the host runs the tool, so it is traced, approved and recorded there) and `{"id": n, "answer": ...}` or
`{"id": n, "error": ...}`. A typed answer travels as its JSON; the host checks it against the part's contract.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import importlib.util
import inspect
import json
import os
import sys
import threading
import types
from typing import Any

_PROTOCOL = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")
os.dup2(2, 1)  # whatever the framework prints goes to stderr, never into the protocol
sys.stdout = sys.stderr

_TYPES: dict[str, Any] = {"string": str, "integer": int, "number": float, "boolean": bool, "array": list,
                          "object": dict}  # fmt: skip


class Worker:
    def __init__(self, agent_file: str | None) -> None:
        self.loop = asyncio.get_running_loop()
        self._lock = threading.Lock()
        self._calls: dict[int, concurrent.futures.Future[Any]] = {}
        self._next = 0
        self._modules: dict[str, Any] = {}
        self.agent_file = agent_file

    def send(self, msg: dict[str, Any]) -> None:
        line = json.dumps(msg, default=str)
        with self._lock:
            _PROTOCOL.write(line + "\n")
            _PROTOCOL.flush()

    async def call(self, ask_id: int, tool: str, args: dict[str, Any]) -> Any:
        """Run a canvas tool on the host. Safe from any thread or event loop a framework runs tools in."""
        future: concurrent.futures.Future[Any] = concurrent.futures.Future()
        with self._lock:
            self._next += 1
            n = self._next
            self._calls[n] = future
        try:
            self.send({"id": ask_id, "call": n, "tool": tool, "args": args})
            return await asyncio.wrap_future(future)
        finally:
            with self._lock:
                self._calls.pop(n, None)

    def reply(self, msg: dict[str, Any]) -> None:
        with self._lock:
            future = self._calls.pop(msg["reply"], None)
        if future is None or future.done():
            return
        if "error" in msg:
            future.set_exception(RuntimeError(str(msg["error"])))
        else:
            future.set_result(msg.get("value"))

    def module(self, source: str | None) -> Any:
        if source is None:
            if self.agent_file is None:
                raise RuntimeError("no agent: start the worker with the agent's file, or send its code")
            key = "file"
            if key not in self._modules:
                spec = importlib.util.spec_from_file_location("agentcanvas_part_agent", self.agent_file)
                assert spec is not None and spec.loader is not None
                mod = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = mod  # Pydantic resolves the answer classes through their module
                spec.loader.exec_module(mod)
                self._modules[key] = mod
            return self._modules[key]
        key = hashlib.sha256(source.encode()).hexdigest()
        if key not in self._modules:
            mod = types.ModuleType(f"agentcanvas_part_{key[:16]}")
            sys.modules[mod.__name__] = mod
            exec(compile(source, "<designed agent>", "exec"), mod.__dict__)  # the host's generated code
            self._modules[key] = mod
            while len(self._modules) > 32:
                old = next(iter(self._modules))
                sys.modules.pop(f"agentcanvas_part_{old[:16]}", None)
                self._modules.pop(old)
        return self._modules[key]

    async def ask(self, msg: dict[str, Any]) -> None:
        ask_id = msg["id"]
        try:
            mod = self.module(msg.get("source"))
            tools = [tool_function(self, ask_id, spec) for spec in msg.get("tools") or []]
            agent = mod.build(tools, model=msg.get("model") or "canvas", base_url=msg.get("base_url"),
                              api_key=msg.get("api_key"))  # fmt: skip
            answer = await mod.ask(agent, msg["text"])
            if hasattr(answer, "model_dump"):
                answer = answer.model_dump(mode="json")
            elif answer is not None and not isinstance(answer, str | int | float | bool | list | dict):
                answer = str(answer)
            self.send({"id": ask_id, "answer": answer})
        except (Exception, SystemExit) as exc:  # noqa: BLE001 - the host shows it as the step's error
            self.send({"id": ask_id, "error": f"{type(exc).__name__}: {exc}"})


def _hint(prop: Any) -> Any:
    t = prop.get("type") if isinstance(prop, dict) else None
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), None)
    return _TYPES.get(str(t), Any)


def tool_function(worker: Worker, ask_id: int, spec: dict[str, Any]) -> Any:
    """A canvas tool as a plain async function with a real signature and docstring (as the host would pass it)."""
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

    async def fn(**kwargs: Any) -> str:
        args = {k: v for k, v in kwargs.items() if v is not None or k in required}
        out = await worker.call(ask_id, spec["name"], args)
        return out if isinstance(out, str) else json.dumps(out, default=str)

    fn.__name__ = fn.__qualname__ = spec["name"]
    fn.__doc__ = (spec.get("description") or spec["name"]) + ("\n\nArgs:\n" + "\n".join(doc_args) if doc_args else "")
    fn.__signature__ = inspect.Signature(params, return_annotation=str)  # type: ignore[attr-defined]
    fn.__annotations__ = {**annotations, "return": str}
    return fn


async def main() -> None:
    worker = Worker(sys.argv[1] if len(sys.argv) > 1 else None)
    loop = worker.loop
    lines: asyncio.Queue[str | None] = asyncio.Queue()

    def read() -> None:  # a thread: the host's lines arrive whatever the event loop is busy with
        for line in sys.stdin:
            loop.call_soon_threadsafe(lines.put_nowait, line)
        loop.call_soon_threadsafe(lines.put_nowait, None)

    threading.Thread(target=read, daemon=True).start()
    worker.send({"ready": True, "python": sys.version.split()[0]})
    tasks: dict[int, asyncio.Task[None]] = {}
    while (line := await lines.get()) is not None:
        msg = json.loads(line)
        if "reply" in msg:
            worker.reply(msg)
        elif msg.get("op") == "ask":
            ask_id = msg["id"]
            tasks[ask_id] = asyncio.create_task(worker.ask(msg))
            tasks[ask_id].add_done_callback(lambda _, n=ask_id: tasks.pop(n, None))
        elif msg.get("op") == "cancel" and msg.get("id") in tasks:
            tasks[msg["id"]].cancel()
    for task in list(tasks.values()):  # the host went away
        task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
