"""The CrewAI adapter (S27): workflows designed for CrewAI, run and exported as native CrewAI flows.

`codegen` writes the workflow as a CrewAI `Flow` (a method per block, chained with `@start`, `@listen`, `@router`,
`and_` and `or_`; `Agent`s and `Crew`s for the AI blocks) and `runtime` adds what the canvas needs. CrewAI cannot
be installed next to the server's own packages (it pins an older `openai`), so the flows run in a worker process
(`worker`) started from a Python environment with CrewAI: `AGENTCANVAS_CREWAI_PYTHON`, or `.frameworks/crewai` next
to the API (`make crewai`). Without one, CrewAI workflows can still be designed, checked and exported; runs say how
to install it.

The canvas models reach CrewAI through the model bridge (any catalog provider, scripted replies included, with usage
and replies recorded); tools, knowledge bases and workspace code run on the server when the worker asks for them.
A paused run is kept in memory as JSON, so a server restart forgets it (no saved-state abilities, as for S25).
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path
from typing import Any, ClassVar

from agentcanvas.adapters.crewai import codegen
from agentcanvas.adapters.langchain import nodes as ac
from agentcanvas.adapters.langchain.bridge import model_bridge
from agentcanvas.adapters.langchain.events import UsageTracker, node_index
from agentcanvas.adapters.setup import setup_hint
from agentcanvas.errors import WorkflowHasErrors
from agentcanvas.ir.models import Workflow
from agentcanvas.registry import default_registry
from agentcanvas.registry.adapters import all_manifests
from agentcanvas.runtime import paused as paused_runs
from agentcanvas.runtime.types import RunSpec
from agentcanvas.validation.blast import dry_run_tools

log = logging.getLogger(__name__)
API_ROOT = Path(__file__).parents[2]  # the `agentcanvas` package directory
ENV_DIR = API_ROOT.parent / ".frameworks" / "crewai"
WORKER_FILES = ["expr.py", "adapters/crewai/runtime.py", "adapters/crewai/worker.py"]
INSTALL_HINT = (
    "CrewAI is not installed on this server: run `make crewai` (a separate Python environment with "
    f"crewai=={codegen.CREWAI_VERSION}) or set AGENTCANVAS_CREWAI_PYTHON, then restart the server"
)
MODEL_STEPS = {"core.llm", "core.agent", "crewai.crew", "crewai.task"}


def crewai_python() -> str | None:
    """The Python with CrewAI that runs the flows (None: not installed here)."""
    found = os.environ.get("AGENTCANVAS_CREWAI_PYTHON") or str(ENV_DIR / "bin" / "python")
    return found if Path(found).is_file() else None


class CrewAIAdapter:
    id = "crewai"
    manifest = all_manifests()["crewai"]

    def __init__(self) -> None:
        # paused runs live with their conversation (runtime/paused.py), not in this process: any replica resumes them
        self._shared: ac.Resources | None = None

    # what the Google ADK adapter (S29), which runs the same way, changes
    install_hint = INSTALL_HINT

    def python(self) -> str | None:
        return crewai_python()

    def worker(self, python: str) -> CrewWorker:
        return shared_worker(python)

    def source(self, wf: Workflow) -> str:
        return codegen.generate(wf)

    # ------------------------------------------------------------ compile ----
    def _blocked(self, wf: Workflow) -> list[dict[str, Any]]:
        out = [
            {"code": "P001", "severity": "error", "message": f"{self.manifest.label} cannot run '{el.type}' blocks",
             "node_id": el.id}
            for el in [*wf.nodes, *wf.resources] if self.manifest.support(el.type) == "unsupported"
        ]  # fmt: skip
        out += [
            {"code": "P001", "severity": "error", "node_id": n.id,
             "message": f"'{n.name}': {self.manifest.label} flows cannot ask a person before a tool call yet; clear "
                        "'Ask before using' or pick another framework"}
            for n in wf.nodes if n.type == "core.agent" and any(n.config.get("approve_tools") or [])
        ]  # fmt: skip
        return out

    def generate(self, wf: Workflow, registry: Any = None) -> str:
        """The workflow as a CrewAI flow (a Python module)."""
        from agentcanvas.validation import validate

        errors = [d.model_dump() for d in validate(wf, registry or default_registry()) if d.severity == "error"]
        errors += [b for b in self._blocked(wf) if b not in errors]
        if errors:
            raise WorkflowHasErrors("; ".join(f"{d['code']} {d['message']}" for d in errors), errors=errors)
        return self.source(wf)

    def package(
        self,
        wf: Workflow,
        registry: Any = None,
        *,
        workspace: Any = None,
        test_cases: list[dict[str, Any]] | None = None,
        version: str = "0.0.0",
        target: str = "local",
    ) -> tuple[str, bytes]:
        from agentcanvas.adapters.crewai.packager import package

        return package(wf, self.generate(wf, registry), workspace=workspace, test_cases=test_cases, version=version)

    # ------------------------------------------------------------ runtime ----
    @asynccontextmanager
    async def persistence(self, database_url: str | None) -> AsyncIterator[None]:
        yield  # paused runs live in memory

    def default_resources(self) -> ac.Resources:
        if self._shared is None:
            self._shared = ac.Resources()
        return self._shared

    def new_resources(self, *, resources_class: Any = None, **kw: Any) -> ac.Resources:
        from agentcanvas.adapters import get_adapter

        return get_adapter("langchain").new_resources(resources_class=resources_class, **kw)

    def embedder(self, spec: dict[str, Any], secrets: dict[str, str]) -> Any:
        return ac.knowledge_embedder(spec, secrets)

    def script(self, resources: Any, import_path: str) -> Any:
        return ac.script_node(import_path=import_path, resources=resources)

    async def execute(self, spec: RunSpec, resources: Any) -> AsyncIterator[dict[str, Any]]:
        wf = spec.workflow
        try:
            python = self.python()
            if python is None:
                raise RuntimeError(setup_hint(self) or self.install_hint)
            if blocked := self._blocked(wf):
                raise WorkflowHasErrors("; ".join(f"{d['code']} {d['message']}" for d in blocked), errors=blocked)
            source = self.source(wf)
            if spec.mocks:
                resources = resources.with_mocks(spec.mocks)
            if spec.dry_run:
                resources = resources.with_dry_run(dry_run_tools(wf, _registry(resources)))
            request: dict[str, Any] = {"source": source, "thread": spec.thread_id}
            if spec.resume is not None:
                # kept until the continued run ends or pauses again: a failed continuation can be tried again
                saved = await paused_runs.load(spec.thread_id)
                if saved is None:
                    yield {"type": "RUN_ERROR", "code": "error", "message": "nothing to resume on this conversation"}
                    return
                request["resume"] = {"paused": saved, "answer": spec.resume.value}
            else:
                await paused_runs.store().drop(spec.thread_id)
                request["input"] = spec.input
            usage = UsageTracker(node_index(wf))
            host = ServerHost(wf, resources, await model_bridge(), usage, self)
            worker = self.worker(python)
        except Exception as exc:  # noqa: BLE001 - every failure reaches the UI as RUN_ERROR
            yield {"type": "RUN_ERROR", "code": "error", "message": f"{type(exc).__name__}: {exc}"}
            return
        with host:
            request["models"], request["tools"] = host.models(), host.tool_specs()
            async for ev in worker.run(spec.run_id, request, host.handle):
                for record in usage.drain():
                    yield {"type": "CUSTOM", "name": "ac.usage", "value": record}
                for reply in usage.drain_replies():
                    yield {"type": "CUSTOM", "name": "ac.model_reply", "value": reply}
                for io in usage.drain_tool_io():
                    yield {"type": "CUSTOM", "name": "ac.tool_io", "value": io}
                paused = ev.pop("paused", None)
                if paused is not None:
                    await paused_runs.save(spec.thread_id, paused)
                elif ev.get("type") == "RUN_FINISHED" and spec.resume is not None:
                    await paused_runs.store().drop(spec.thread_id)
                yield run_scoped_interrupts(ev, spec.run_id)


def run_scoped_interrupts(ev: dict[str, Any], run_id: str) -> dict[str, Any]:
    """Interrupt ids made unique per run. The workers name a pause `<thread>:<block>`, so a second pause at the
    same block of one conversation (a loop back through an approval, a re-asked question, the next chat message)
    reused the first one's id: the inbox kept the old, decided row and the new pause could not be answered."""

    def own(info: Any) -> Any:
        if isinstance(info, dict) and info.get("canvas_node_id"):
            return {**info, "id": f"{run_id}:{info['canvas_node_id']}"}
        return info

    if isinstance(ev.get("interrupts"), list):
        ev = {**ev, "interrupts": [own(i) for i in ev["interrupts"]]}
    if ev.get("type") == "CUSTOM" and ev.get("name") == "ac.interrupt":
        ev = {**ev, "value": own(ev.get("value"))}
    return ev


class ServerHost:
    """What a flow's steps get from the server: canvas models through the bridge (a key per step), canvas tools,
    the project's knowledge bases and workspace code."""

    def __init__(self, wf: Workflow, resources: ac.Resources, bridge: Any, usage: UsageTracker, adapter: Any):
        self.wf, self.resources, self.bridge, self.usage, self.adapter = wf, resources, bridge, usage, adapter
        self._stack = ExitStack()
        self._tools: dict[tuple[str, str], Any] = {}

    def __enter__(self) -> ServerHost:
        return self

    def __exit__(self, *exc: object) -> None:
        self._stack.close()  # the bridge stops answering this run's keys

    def _config(self, step: str) -> dict[str, Any]:
        return {"callbacks": [self.usage], "metadata": {"langgraph_node": step}}

    def models(self) -> dict[str, dict[str, str]]:
        """A bridge address and key for each (model block, step) pair the flow has."""
        out: dict[str, dict[str, str]] = {}
        for n in self.wf.nodes:
            if n.type not in MODEL_STEPS:
                continue
            for res in self.wf.wired_into(n.id, "model"):
                lc_model = self.resources.model(res.name, res.config)
                key = self._stack.enter_context(self.bridge.serving(lc_model, self._config(n.name)))
                out[f"{res.name}|{n.name}"] = {"base_url": self.bridge.root + "/v1", "api_key": key}
        return out

    def tool_specs(self) -> dict[str, list[dict[str, Any]]]:
        """Each wired tool block's tools, as name, description and JSON-schema arguments."""
        from langchain_core.utils.function_calling import convert_to_openai_tool

        out: dict[str, list[dict[str, Any]]] = {}
        for n in self.wf.nodes:
            for res in self.wf.wired_into(n.id, "tools"):
                if res.name in out:
                    continue
                found = self.resources.tool(res.name, res.config, res.type)
                out[res.name] = []
                for t in found if isinstance(found, list) else [found]:
                    self._tools[(res.name, t.name)] = t
                    fn = convert_to_openai_tool(t)["function"]
                    out[res.name].append({"name": fn["name"], "description": fn.get("description") or fn["name"],
                                          "parameters": fn.get("parameters") or {"type": "object"}})  # fmt: skip
        return out

    async def handle(self, op: str, args: dict[str, Any]) -> Any:
        if op == "tool":
            t = self._tools[(args["resource"], args["tool"])]
            try:  # a tool's error is told to the model, as LangGraph does
                out = await t.ainvoke(args.get("args") or {}, config=self._config(args.get("step", "")))
            except Exception as exc:  # noqa: BLE001
                return f"Error: {type(exc).__name__}: {exc}"
            content = getattr(out, "content", out)
            return content if isinstance(content, str) else json.dumps(content, default=str)
        if op == "search":
            return await self.resources.search_knowledge(
                args["knowledge_base"], args["query"], mode=args["mode"], top_k=args["top_k"],
                min_score=args["min_score"],
            )  # fmt: skip
        if op == "script":
            return await self.adapter.script(self.resources, args["import_path"])(args["state"])
        raise ValueError(f"unknown request '{op}'")


# ------------------------------------------------------------------ the worker ----
# what a framework worker may see of the server's environment: models come through the model bridge with a key per
# run, so the database URL, encryption keys, provider keys and cloud credentials stay with the server
WORKER_ENV = {
    "PATH", "HOME", "USER", "LANG", "LANGUAGE", "TZ", "TMPDIR", "TEMP", "TMP", "SSL_CERT_FILE", "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "NODE_EXTRA_CA_CERTS", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "no_proxy", "UV_CACHE_DIR", "XDG_CACHE_HOME",
}  # fmt: skip
MAX_WORKER_LINE = 64 * 1024 * 1024  # longest message line read from a worker
IDLE_TIMEOUT_S = 600.0  # a run whose worker says nothing for this long is ended (a hung framework call)


def worker_env(**extra: str) -> dict[str, str]:
    """A framework worker's environment: an allowlist of the server's (2026-10-18 review), plus `extra`."""
    env = {k: v for k, v in os.environ.items() if k in WORKER_ENV or k.startswith("LC_")}
    return {**env, **extra}


class CrewWorker:
    """The worker process and its runs. Reading happens on a thread, so one worker serves every event loop.

    The Google ADK adapter (S29) runs its worker the same way: a subclass names its files, module and label."""

    label = "CrewAI"
    package = "crewai"  # agentcanvas/adapters/<package>/worker.py
    files = WORKER_FILES
    extra_env: ClassVar[dict[str, str]] = {"CREWAI_DISABLE_TELEMETRY": "true", "CREWAI_TRACING_ENABLED": "false"}
    idle_timeout_s: float = IDLE_TIMEOUT_S

    def __init__(self, python: str):
        self.python = python
        self._dir = tempfile.mkdtemp(prefix=f"agentcanvas-{self.package}-")
        command, env = self.prepare()
        self.proc = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=self._dir, env=env
        )
        self._lock = threading.Lock()
        self._runs: dict[str, tuple[asyncio.AbstractEventLoop, asyncio.Queue[dict[str, Any]]]] = {}
        self.version: str | None = None
        self.stderr: list[str] = []
        threading.Thread(target=self._read, daemon=True, name=f"{self.package}-worker-out").start()
        threading.Thread(target=self._read_errors, daemon=True, name=f"{self.package}-worker-err").start()

    def prepare(self) -> tuple[list[str], dict[str, str]]:
        """Lay out the worker's files in its folder; (the command that starts it, its environment)."""
        for rel in self.files:
            dest = Path(self._dir) / "agentcanvas" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(API_ROOT / rel, dest)
        for pkg in ("agentcanvas", "agentcanvas/adapters", f"agentcanvas/adapters/{self.package}"):
            (Path(self._dir) / pkg / "__init__.py").touch()
        env = worker_env(PYTHONPATH=self._dir, PYTHONUNBUFFERED="1", OTEL_SDK_DISABLED="true", **self.extra_env)
        return [self.python, "-m", f"agentcanvas.adapters.{self.package}.worker"], env

    @property
    def alive(self) -> bool:
        return self.proc.poll() is None

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.proc.kill()
            self.proc.wait(timeout=5)
        shutil.rmtree(self._dir, ignore_errors=True)

    def _deliver(self, run_id: str, msg: dict[str, Any]) -> None:
        target = self._runs.get(run_id)
        if target is not None:
            with contextlib.suppress(RuntimeError):  # that run's event loop has closed
                target[0].call_soon_threadsafe(target[1].put_nowait, msg)

    def _read(self) -> None:
        assert self.proc.stdout is not None
        while True:
            try:
                raw = self.proc.stdout.readline(MAX_WORKER_LINE)
            except (OSError, ValueError):
                break
            if not raw:
                break
            if not raw.endswith(b"\n"):  # longer than the limit: drop the rest of that line
                while (rest := self.proc.stdout.readline(MAX_WORKER_LINE)) and not rest.endswith(b"\n"):
                    pass
                log.warning("%s worker: a message over %d bytes was dropped", self.package, MAX_WORKER_LINE)
                continue
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            if msg.get("ready"):
                self.version = msg.get("crewai") or msg.get("version")
            elif "run" in msg:
                self._deliver(msg["run"], msg)
        tail = "; ".join(self.stderr[-3:])
        for run_id in list(self._runs):  # the worker died
            self._deliver(run_id, {"dead": True, "message": f"the {self.label} worker stopped: {tail}"[:600]})

    def _read_errors(self) -> None:
        assert self.proc.stderr is not None
        for raw in self.proc.stderr:
            line = raw.decode(errors="replace").rstrip()
            if line.strip(" │╭╰─"):
                self.stderr = [*self.stderr[-49:], line]
                log.debug("%s worker: %s", self.package, line)

    def send(self, msg: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        data = (json.dumps(msg, default=str) + "\n").encode()
        with self._lock:
            self.proc.stdin.write(data)
            self.proc.stdin.flush()

    async def asend(self, msg: dict[str, Any]) -> None:
        """`send` from async code: a large message to a busy worker fills the pipe and would block the event loop."""
        await asyncio.to_thread(self.send, msg)

    async def run(
        self, run_id: str, request: dict[str, Any], handle: Callable[[str, dict[str, Any]], Any]
    ) -> AsyncIterator[dict[str, Any]]:
        """Run a flow in the worker; yields its events. Requests from the flow are answered with `handle`."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._runs[run_id] = (asyncio.get_running_loop(), queue)
        pending: set[asyncio.Task[None]] = set()
        ended = False
        try:
            try:
                await self.asend({"op": "run", "run": run_id, **request})
            except OSError as exc:  # the worker died before it got the run (a broken pipe)
                ended = True
                yield {"type": "RUN_ERROR", "code": "error", "message": f"the {self.label} worker stopped: {exc}"}
                return
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), self.idle_timeout_s)
                except TimeoutError:
                    quiet = f"the {self.label} worker said nothing for {self.idle_timeout_s:g} s"
                    yield {"type": "RUN_ERROR", "code": "timeout", "message": quiet}
                    return
                if "event" in msg:
                    yield msg["event"]
                elif "call" in msg:
                    task = asyncio.create_task(self._answer(msg, handle))
                    pending.add(task)
                    task.add_done_callback(pending.discard)
                elif msg.get("dead"):
                    ended = True
                    yield {"type": "RUN_ERROR", "code": "error", "message": msg["message"]}
                    return
                elif msg.get("end"):
                    ended = True
                    return
        finally:
            self._runs.pop(run_id, None)
            for task in pending:
                task.cancel()
            if not ended and self.alive:
                with contextlib.suppress(Exception):
                    self.send({"op": "cancel", "run": run_id})

    async def _answer(self, msg: dict[str, Any], handle: Callable[[str, dict[str, Any]], Any]) -> None:
        try:
            value = await handle(msg["op"], msg.get("args") or {})
            reply: dict[str, Any] = {"reply": msg["call"], "value": value}
        except Exception as exc:  # noqa: BLE001 - the step that asked fails with it
            reply = {"reply": msg["call"], "error": f"{type(exc).__name__}: {exc}"}
        with contextlib.suppress(Exception):
            await self.asend(json.loads(json.dumps(reply, default=str)))


_workers: dict[tuple[str, str], CrewWorker] = {}
_workers_lock = threading.Lock()


def shared_worker(python: str, kind: type[CrewWorker] = CrewWorker) -> CrewWorker:
    """The `kind` worker for `python`, started on first use and again if it stopped."""
    with _workers_lock:
        worker = _workers.get((kind.package, python))
        if worker is None or not worker.alive:
            if worker is not None:
                worker.close()
            worker = _workers[(kind.package, python)] = kind(python)
        return worker


@atexit.register
def _stop_workers() -> None:
    for worker in list(_workers.values()):
        worker.close()


def _registry(resources: Any) -> Any:
    factory = getattr(resources, "registry", None)
    return factory() if callable(factory) else None
