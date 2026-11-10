"""Parts of a mixed flow (S28): agents designed for another framework, run in that framework's own environment.

CrewAI, Google ADK, Agno, Strands and LlamaIndex pin packages that clash with LangGraph's, so a flow that mixes them
with its LangGraph host cannot import them in one environment. Such an agent becomes a *part*: its generated module
runs in a worker process (`part_worker.py`) with the framework's own Python, and `PartModule` stands in for the module
in the host, with the same `build(tools, ...)` and `ask(agent, text)`. The host keeps everything else: tool calls
come back over the pipe and run here (traced, approved, recorded), and the model is the host's model bridge, reached
over HTTP on 127.0.0.1.

Every part has a typed contract (`check_answer`): what crosses back must match the agent's structured output schema,
so the next step gets checked data whichever framework produced it.

    <exported project>/parts/<agent>/agent.py  worker.py  requirements.txt  contract.json   (`make install` sets
    up parts/<agent>/.venv; PART_<AGENT>_PYTHON points elsewhere)
"""

from __future__ import annotations

import asyncio
import collections
import concurrent.futures
import contextlib
import json
import os
import subprocess
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

WORKER = Path(__file__).with_name("part_worker.py")
MAX_LINE = 64 * 1024 * 1024
READY_TIMEOUT_S = 60.0  # a worker that has not said it is ready by then is not going to
IDLE_TIMEOUT_S = 600.0  # an ask whose part says nothing for this long (a hung framework call) is given up

# what a framework worker may see of the server's environment (2026-10-18 review): it reaches the model through the
# host's bridge and its tools through the host, so the database URL, encryption keys, provider keys and cloud
# credentials stay with the server. Also the framework adapters' workers (`adapters.crewai.worker_env`).
WORKER_ENV = {
    "PATH", "HOME", "USER", "LANG", "LANGUAGE", "TZ", "TMPDIR", "TEMP", "TMP", "SSL_CERT_FILE", "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "NODE_EXTRA_CA_CERTS", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "no_proxy", "UV_CACHE_DIR", "XDG_CACHE_HOME",
}  # fmt: skip
# whichever framework a part runs, its telemetry is off: a run's details stay on this machine
TELEMETRY_OFF = {
    "CREWAI_DISABLE_TELEMETRY": "true", "CREWAI_TRACING_ENABLED": "false", "AGNO_TELEMETRY": "false",
    "ADK_DISABLE_TELEMETRY": "true", "OTEL_SDK_DISABLED": "true",
}  # fmt: skip


def worker_env(**extra: str) -> dict[str, str]:
    """A framework worker's environment: an allowlist of the server's, plus `extra`."""
    env = {k: v for k, v in os.environ.items() if k in WORKER_ENV or k.startswith("LC_")}
    return {**env, **extra}


class PartError(RuntimeError):
    pass


@dataclass
class _Ask:
    loop: asyncio.AbstractEventLoop
    future: asyncio.Future[Any]
    on_call: Callable[..., Awaitable[Any]]
    heard: float = field(default_factory=time.monotonic)  # when the part last said something about this ask
    tools: int = 0  # tool calls of this ask running on the host (the part waits for them: not quiet)


class PartProcess:
    """One worker process: many asks at once, each with its own tool callbacks."""

    def __init__(self, python: str, worker: Path, agent_file: Path | None) -> None:
        args = [python, str(worker), *([str(agent_file)] if agent_file else [])]
        self.proc = subprocess.Popen(  # our own worker, with the part's Python
            args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
            bufsize=1, env=worker_env(PYTHONUNBUFFERED="1", **TELEMETRY_OFF),
        )  # fmt: skip
        self._lock = threading.Lock()
        self._asks: dict[int, _Ask] = {}
        self._next = 0
        self.ready: concurrent.futures.Future[dict[str, Any]] = concurrent.futures.Future()
        self.stderr: collections.deque[str] = collections.deque(maxlen=40)
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._drain, daemon=True).start()

    @property
    def alive(self) -> bool:
        return self.proc.poll() is None

    def _drain(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            self.stderr.append(line.rstrip())

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("ready"):
                if not self.ready.done():
                    self.ready.set_result(msg)
                continue
            with self._lock:
                entry = self._asks.get(msg.get("id"))
                if entry is not None:
                    entry.heard = time.monotonic()
            if entry is None:
                if "call" in msg:  # its ask was stopped meanwhile: the part must not wait for this tool forever
                    self._send({"reply": msg["call"], "error": "the step was stopped"})
                continue
            if "call" in msg:
                asyncio.run_coroutine_threadsafe(self._tool(msg, entry), entry.loop)
            elif "answer" in msg or "error" in msg:
                with self._lock:
                    self._asks.pop(msg["id"], None)
                if "error" in msg:
                    entry.loop.call_soon_threadsafe(_settle, entry.future, PartError(str(msg["error"])), None)
                else:
                    entry.loop.call_soon_threadsafe(_settle, entry.future, None, msg.get("answer"))
        why = self.why_stopped()
        if not self.ready.done():
            self.ready.set_exception(PartError(why))
        with self._lock:
            pending, self._asks = list(self._asks.values()), {}
        for entry in pending:
            entry.loop.call_soon_threadsafe(_settle, entry.future, PartError(why), None)

    def why_stopped(self) -> str:
        tail = "\n".join(list(self.stderr)[-12:])
        return "the part's worker stopped" + (f":\n{tail}" if tail else "")

    async def _tool(self, msg: dict[str, Any], entry: _Ask) -> None:
        entry.tools += 1
        try:
            value = await entry.on_call(msg["tool"], msg.get("args") or {})
            reply: dict[str, Any] = {"reply": msg["call"], "value": value}
        except Exception as exc:  # noqa: BLE001 - the framework sees it as the tool's error
            reply = {"reply": msg["call"], "error": f"{type(exc).__name__}: {exc}"}
        finally:
            entry.tools -= 1
            entry.heard = time.monotonic()
        try:
            self._send(reply)
        except OSError:  # the worker stopped meanwhile: `_read` fails the ask
            pass

    def _send(self, msg: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        with self._lock:
            self.proc.stdin.write(json.dumps(msg, default=str) + "\n")
            self.proc.stdin.flush()

    async def ask(self, request: dict[str, Any], on_call: Callable[[str, dict[str, Any]], Awaitable[Any]]) -> Any:
        try:
            await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(self.ready)), READY_TIMEOUT_S)
        except TimeoutError:
            self.proc.kill()  # a fresh worker starts on next use
            raise PartError(f"the part's worker did not start within {READY_TIMEOUT_S:g} s") from None
        loop = asyncio.get_running_loop()
        entry = _Ask(loop, loop.create_future(), on_call)
        with self._lock:
            self._next += 1
            n = self._next
            self._asks[n] = entry
        try:
            self._send({"op": "ask", "id": n, **request})
        except OSError as exc:
            with self._lock:
                self._asks.pop(n, None)
            raise PartError(self.why_stopped()) from exc
        hung = False
        try:
            while True:
                quiet = 0.0 if entry.tools else time.monotonic() - entry.heard
                if quiet >= IDLE_TIMEOUT_S:
                    hung = True
                    raise PartError(f"the part said nothing for {IDLE_TIMEOUT_S:g} s")
                await asyncio.wait({entry.future}, timeout=max(IDLE_TIMEOUT_S - quiet, 0.05))
                if entry.future.done():
                    return entry.future.result()
        finally:
            with self._lock:
                self._asks.pop(n, None)
                alone = not self._asks
            if not entry.future.done():  # stopped (a cancel, a time limit) or hung: the part stops the ask too
                entry.future.cancel()
                self._stop(n, kill=hung and alone)

    def _stop(self, n: int, kill: bool) -> None:
        """Tell the part to stop ask `n`. `kill`: the ask hung and nothing else is asking the part, which may be
        blocked for good: it is ended, and a fresh one starts on next use."""
        with contextlib.suppress(OSError):
            self._send({"op": "cancel", "id": n})
        if kill:
            self.proc.kill()


def _settle(future: asyncio.Future[Any], error: BaseException | None, value: Any) -> None:
    if future.done():
        return
    if error is not None:
        future.set_exception(error)
    else:
        future.set_result(value)


_PROCESSES: dict[tuple[str, str, str], PartProcess] = {}
_PROCESSES_LOCK = threading.Lock()


def process(python: str, worker: Path = WORKER, agent_file: Path | None = None) -> PartProcess:
    """The running worker for (python, agent file), started on first use and again if it stopped."""
    key = (python, str(worker), str(agent_file or ""))
    with _PROCESSES_LOCK:
        found = _PROCESSES.get(key)
        if found is None or not found.alive:
            found = _PROCESSES[key] = PartProcess(python, worker, agent_file)
        return found


class _Agent:
    def __init__(self, tools: list[Any], model: str, base_url: str | None, api_key: str | None) -> None:
        self.tools, self.model, self.base_url, self.api_key = list(tools), model, base_url, api_key


class PartModule:
    """Stands in for a designed agent's module when the agent runs as a part (see the module docstring)."""

    PART = True

    def __init__(self, python: str, *, source: str | None = None, agent_file: Path | None = None,
                 worker: Path | None = None) -> None:  # fmt: skip
        self.python, self.source, self.agent_file = python, source, agent_file
        self.worker = worker or (agent_file.with_name("worker.py") if agent_file else WORKER)

    def build(self, tools: Any = (), *, model: str = "canvas", base_url: str | None = None,
              api_key: str | None = None) -> _Agent:  # fmt: skip
        return _Agent(list(tools), model, base_url, api_key)

    async def ask(self, agent: _Agent, text: str) -> Any:
        by_name = {f.__name__: f for f in agent.tools}
        specs = []
        for f in agent.tools:
            spec = getattr(f, "spec", None) or {}
            specs.append({"name": f.__name__, "description": spec.get("description") or (f.__doc__ or f.__name__),
                          "parameters": spec.get("parameters") or {"type": "object", "properties": {}}})  # fmt: skip

        async def on_call(tool: str, args: dict[str, Any]) -> Any:
            if tool not in by_name:
                raise PartError(f"the part asked for a tool it was not given: {tool}")
            return await by_name[tool](**args)

        request: dict[str, Any] = {"text": text, "tools": specs, "model": agent.model, "base_url": agent.base_url,
                                   "api_key": agent.api_key}  # fmt: skip
        if self.source is not None:
            request["source"] = self.source
        return await process(self.python, self.worker, self.agent_file).ask(request, on_call)


def project_part(package: str | None, name: str) -> PartModule | None:
    """The part `name` of an exported project (`parts/<name>/`, next to `src/`), if it has one."""
    import importlib

    if not package:
        return None
    root = Path(importlib.import_module(package).__file__ or "").resolve().parents[2]
    folder = root / "parts" / name
    if not (folder / "agent.py").is_file():
        return None
    python = os.environ.get(f"PART_{name.upper()}_PYTHON") or str(folder / ".venv" / "bin" / "python")
    if not Path(python).is_file():
        raise PartError(
            f"the part '{name}' has no Python environment yet: run `make install` (it sets up parts/{name}/.venv), "
            f"or set PART_{name.upper()}_PYTHON"
        )
    return PartModule(python, agent_file=folder / "agent.py")


# ------------------------------------------------------------------ contracts ----
def check_answer(schema: dict[str, Any] | None, answer: Any) -> Any:
    """`answer` as its contract says it crosses back (S28), or ValueError saying how it breaks the contract.

    A text contract takes any answer as text; a typed one wants data matching the schema (a JSON string is read)."""
    if not schema or schema.get("type") == "string":
        if answer is None or isinstance(answer, str):
            return answer
        return answer.model_dump_json() if hasattr(answer, "model_dump_json") else json.dumps(answer, default=str)
    data = answer.model_dump(mode="json") if hasattr(answer, "model_dump") else answer
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            raise ValueError("it answered in plain text, not the structured output its contract promises") from None
    import jsonschema

    errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(data), key=lambda e: list(e.path))
    if errors:
        e = errors[0]
        where = "/".join(str(p) for p in e.path) or "the answer"
        raise ValueError(f"{where}: {e.message}")
    return data


def worker_source() -> bytes:
    """The worker, as an export copies it into each part."""
    return WORKER.read_bytes()
