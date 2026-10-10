"""The LangChain / LangGraph / Deep Agents framework adapter (adapter #1, spec doc 12)."""

from __future__ import annotations

import contextlib
import time
import warnings
from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphRecursionError
from langgraph.types import Command

from agentcanvas.adapters.langchain import nodes as ac
from agentcanvas.adapters.langchain.codegen import generate
from agentcanvas.adapters.langchain.events import UsageTracker, jsonable, nested_index, node_index, translate
from agentcanvas.adapters.langchain.interpreter import build_from_plan, prepare
from agentcanvas.adapters.langchain.persistence import Persistence, copy_thread, open_persistence
from agentcanvas.adapters.langchain.plan import lower
from agentcanvas.ir.canonical import ir_hash
from agentcanvas.ir.models import Workflow
from agentcanvas.registry.adapters import all_manifests
from agentcanvas.runtime import masking
from agentcanvas.runtime.tracing import trace_id
from agentcanvas.runtime.types import CheckpointInfo, InterruptInfo, RunSpec, ThreadState, interrupt_kind
from agentcanvas.sdk import _SECRETS
from agentcanvas.services.secrets import hide_values
from agentcanvas.settings import get_settings
from agentcanvas.validation.blast import dry_run_tools

STREAM_MODES = ["tasks", "updates", "messages", "custom"]
# langgraph 1.2.12 skips node `error_handler`s when streaming "messages"/"custom" or with subgraphs=True
# (FAQ P-15, canary test `test_upstream_error_handler_with_messages_stream`). Workflows that use error routes
# therefore stream without tokens until upstream is fixed.
SAFE_STREAM_MODES = ["tasks", "updates"]
warnings.filterwarnings("ignore", message=".*langchain.mcp.*beta.*")  # surfaced as I010 diagnostics instead


class LangChainAdapter:
    id = "langchain"
    manifest = all_manifests()["langchain"]

    def __init__(self, graph_cache_size: int = 128):
        self._p: Persistence | None = None
        self._graphs: OrderedDict[tuple[str, int, str], tuple[Any, Any]] = OrderedDict()
        self._cache_size = graph_cache_size
        self._shared = self.new_resources()

    # ------------------------------------------------------------ plumbing ----
    def script(self, resources: Any, import_path: str) -> Any:
        return ac.script_node(import_path=import_path, resources=resources)

    def default_resources(self) -> ac.Resources:
        return self._shared

    def new_resources(self, *, resources_class: Any = None, **kw: Any) -> ac.Resources:
        """A resolver for one project: its workspace (scripts/tools/nodes), secrets and script sandbox."""
        resources = (resources_class or ac.Resources)(**kw)
        resources.public_only = get_settings().edition == "hosted"
        return resources

    def embedder(self, spec: dict[str, Any], secrets: dict[str, str]) -> Any:
        """A knowledge base's embedding function (S24)."""
        return ac.knowledge_embedder(spec, secrets)

    @asynccontextmanager
    async def persistence(self, database_url: str | None) -> AsyncIterator[None]:
        async with open_persistence(database_url) as p:
            self._p = p
            try:
                yield
            finally:
                self._p = None
                self._graphs.clear()

    @property
    def persistence_kind(self) -> str:
        return self._p.kind if self._p else "none"

    @property
    def store(self) -> Any:
        """The long-term store (Deep Agent /memories/ folders, memory nodes); read by tests and the memory inspector."""
        return self._persist().store

    def _persist(self) -> Persistence:
        if self._p is None:
            raise RuntimeError("persistence is not open; use `async with adapter.persistence(url)`")
        return self._p

    async def graph(self, wf: Workflow, resources: ac.Resources) -> Any:
        """Compiled graph for (IR, resources), cached: rebuilding per run is wasteful for real models."""
        # the entry keeps `resources` alive, so its id cannot be reused; the revision changes when workspace code does
        key = (ir_hash(wf), id(resources), resources.revision())
        if key in self._graphs:
            self._graphs.move_to_end(key)
            return self._graphs[key][1]
        p = self._persist()
        plan = lower(wf, _registry(resources))
        await prepare(plan, resources)  # MCP tool discovery happens before the (synchronous) build
        graph = build_from_plan(plan, resources, checkpointer=p.checkpointer, store=p.store)
        self._graphs[key] = (resources, graph)
        if len(self._graphs) > self._cache_size:
            self._graphs.popitem(last=False)
        return graph

    async def _debug_graph(self, wf: Workflow, resources: ac.Resources, pins: dict[str, dict[str, Any]]) -> Any:
        p = self._persist()
        plan = lower(wf, _registry(resources))
        for pn in plan.nodes:
            if pn.node.id in pins:
                pn.pinned = pins[pn.node.id]
        await prepare(plan, resources)
        return build_from_plan(plan, resources, checkpointer=p.checkpointer, store=p.store)

    @staticmethod
    def _config(
        wf: Workflow,
        thread_id: str,
        checkpoint_id: str | None = None,
        run_id: str | None = None,
        labels: dict[str, str] | None = None,
    ) -> RunnableConfig:
        configurable: dict[str, Any] = {"thread_id": thread_id}
        if checkpoint_id:
            configurable["checkpoint_id"] = checkpoint_id
            configurable["checkpoint_ns"] = ""  # AsyncSqliteSaver.aput_writes needs it for past checkpoints (FAQ P-13)
        cfg: RunnableConfig = {"configurable": configurable, "recursion_limit": wf.settings.recursion_limit}
        if run_id:
            labels = labels or {}
            cfg["metadata"] = {
                "workflow_id": wf.id, "run_id": run_id, "thread_id": thread_id, "ir_hash": ir_hash(wf), **labels
            }  # fmt: skip
            cfg["tags"] = ["agentcanvas", f"workflow:{wf.id}", *(f"{k}:{v}" for k, v in sorted(labels.items()))]
            cfg["run_name"] = wf.name
            cfg["run_id"] = trace_id(run_id)  # the LangSmith root run: "Open trace" finds it (M6-T6)
        return cfg

    # --------------------------------------------------------- compilation ----
    async def inspect_mcp(self, name: str, config: dict[str, Any], resources: ac.Resources) -> list[dict[str, Any]]:
        """List an MCP server's tools with their annotations (the canvas "Browse tools" action)."""
        tools = await resources.load_mcp(name, {**config, "tool_filter": []}, refresh=True)
        return [ac.mcp_tool_info(t) for t in tools]

    # ---------------------------------------------------------- memory (M4-T12) ----
    async def memory_namespaces(self, prefix: tuple[str, ...], limit: int = 200) -> list[tuple[str, ...]]:
        return list(await self.store.alist_namespaces(prefix=prefix or None, limit=limit))

    async def memory_items(self, namespace: tuple[str, ...], query: str | None, limit: int) -> list[dict[str, Any]]:
        items = await self.store.asearch(namespace, limit=1000 if query else limit)
        if query:
            items = ac.keyword_rank(items, query, limit)
        return [
            {
                "namespace": list(i.namespace),
                "key": i.key,
                "value": jsonable(i.value),
                "created_at": i.created_at.isoformat() if i.created_at else None,
                "updated_at": i.updated_at.isoformat() if i.updated_at else None,
            }
            for i in items
        ]

    async def memory_delete(self, namespace: tuple[str, ...], key: str) -> None:
        await self.store.adelete(namespace, key)

    async def memory_erase(self, prefix: tuple[str, ...]) -> int:
        """Delete everything under a namespace prefix (e.g. one user's memories: right to erasure)."""
        deleted = 0
        while items := await self.store.asearch(prefix, limit=500):
            for i in items:
                await self.store.adelete(i.namespace, i.key)
            deleted += len(items)
        return deleted

    async def memory_expire(self, prefix: tuple[str, ...], before: datetime) -> int:
        """Delete the memories under `prefix` last updated before `before` (retention, S12)."""

        def old(at: datetime | None) -> bool:  # the SQLite store returns naive UTC times
            return at is not None and (at if at.tzinfo else at.replace(tzinfo=UTC)) < before

        stale, offset = [], 0
        while page := await self.store.asearch(prefix, limit=500, offset=offset):
            stale += [i for i in page if old(i.updated_at)]
            offset += len(page)
        for i in stale:
            await self.store.adelete(i.namespace, i.key)
        return len(stale)

    ADOPTED = (("org:_system", "migrations"), "memory_adopt")

    async def memory_adopt(self, org_id: str) -> int:
        """Before Sprint 10 the store had no organisation root and every install had one organisation: its
        memories move under that organisation. Runs once per store (a marker records it), so namespaces
        written later without a root, e.g. by an exported app sharing the database, are never misfiled."""
        if await self.store.aget(*self.ADOPTED):
            return 0
        root = ac.org_prefix(org_id)
        unrooted, offset = [], 0
        while page := await self.store.alist_namespaces(limit=1000, offset=offset):
            unrooted += [ns for ns in page if not (ns and str(ns[0]).startswith("org:"))]
            offset += len(page)
        moved = 0
        for ns in unrooted:
            items, offset = [], 0
            while batch := await self.store.asearch(ns, limit=500, offset=offset):  # matches deeper namespaces too
                items += [i for i in batch if tuple(i.namespace) == tuple(ns)]
                offset += len(batch)
            for i in items:
                await self.store.aput((*root, *i.namespace), i.key, i.value)
                await self.store.adelete(i.namespace, i.key)
            moved += len(items)
        await self.store.aput(*self.ADOPTED, {"org_id": org_id, "items": moved})
        return moved

    def generate(self, wf: Workflow, registry: Any = None) -> str:
        return generate(wf, registry=registry)

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
        from agentcanvas.adapters.langchain.packager import package

        source = generate(wf, registry=registry)
        return package(wf, source, workspace=workspace, test_cases=test_cases, version=version, target=target)

    # ----------------------------------------------------------- execution ----
    async def execute(self, spec: RunSpec, resources: ac.Resources) -> AsyncIterator[dict[str, Any]]:
        wf = spec.workflow
        if spec.pins or spec.mocks or spec.dry_run:  # debug aids: a one-off graph, never cached
            if spec.mocks:
                resources = resources.with_mocks(spec.mocks)
            if spec.dry_run:
                resources = resources.with_dry_run(dry_run_tools(wf, _registry(resources)))
            graph = await self._debug_graph(wf, resources, spec.pins)
        else:
            graph = await self.graph(wf, resources)
        names, nested = node_index(wf), nested_index(wf)
        cfg = self._config(wf, spec.thread_id, spec.checkpoint_id, spec.run_id, spec.labels)
        if spec.org_id:  # memory nodes, memory tools and Deep Agent folders prefix their namespaces with it
            cfg.setdefault("configurable", {})[ac.ORG_KEY] = spec.org_id
        if spec.resume is not None:
            value = {spec.resume.interrupt_id: spec.resume.value} if spec.resume.interrupt_id else spec.resume.value
            graph_input: Any = Command(resume=value)
        else:
            graph_input = spec.input
        error_routes = any(n.policies and n.policies.error_route for n in wf.nodes)
        kwargs: dict[str, Any] = {
            "stream_mode": SAFE_STREAM_MODES if error_routes else STREAM_MODES,
            "subgraphs": not error_routes,
            "version": "v2",
            "durability": spec.durability or wf.settings.durability,
        }
        if error_routes:
            yield {
                "type": "CUSTOM",
                "name": "ac.warning",
                "value": {
                    "code": "tokens_disabled",
                    "message": "Token streaming is off for workflows with error routes "
                    "(upstream LangGraph issue, FAQ P-15).",
                },
            }
        if spec.context:
            kwargs["context"] = spec.context
        if spec.mode == "debug":
            id2name = {n.id: n.name for n in wf.nodes}
            kwargs["interrupt_before"] = [id2name[i] for i in spec.breakpoints_before if i in id2name] or None
            kwargs["interrupt_after"] = [id2name[i] for i in spec.breakpoints_after if i in id2name] or None
        usage = UsageTracker(names)
        cfg["callbacks"] = [usage]
        secrets = getattr(resources, "_secrets", None) or {}
        token = _SECRETS.set(secrets)  # sdk.secret() in in-process scripts and custom nodes
        redaction = masking.redacting(wf.settings.trace_redact)  # the root run and model runs in LangSmith (S13)
        redaction.__enter__()
        hidden = sorted((v for v in secrets.values() if len(v) >= 6), key=len, reverse=True)
        started: dict[str, float] = {}
        try:
            async for part in graph.astream(graph_input, cfg, **kwargs):
                for ev in translate(part, names, nested):
                    if ev["type"] == "STEP_STARTED":
                        started[ev["task_id"]] = time.perf_counter()
                    elif ev["type"] == "STEP_FINISHED" and ev["task_id"] in started:
                        ev["duration_ms"] = round((time.perf_counter() - started.pop(ev["task_id"])) * 1000)
                    yield _redacted(ev, hidden)
                for record in usage.drain():
                    yield {"type": "CUSTOM", "name": "ac.usage", "value": record}
                for reply in usage.drain_replies():
                    yield _redacted({"type": "CUSTOM", "name": "ac.model_reply", "value": reply}, hidden)
                for io in usage.drain_tool_io():
                    yield _redacted({"type": "CUSTOM", "name": "ac.tool_io", "value": io}, hidden)
        except GraphRecursionError as exc:
            yield {
                "type": "RUN_ERROR",
                "code": "recursion_limit",
                "message": f"stopped after {wf.settings.recursion_limit} steps without reaching END "
                f"(GraphRecursionError: {exc}). Add a router exit or raise the recursion limit.",
            }
            return
        except Exception as exc:  # noqa: BLE001 - every failure must reach the UI as RUN_ERROR
            yield _redacted({"type": "RUN_ERROR", "code": "error", "message": f"{type(exc).__name__}: {exc}"}, hidden)
            return
        finally:
            with contextlib.suppress(ValueError):  # closed from another context (e.g. cancelled run)
                _SECRETS.reset(token)
            redaction.__exit__(None, None, None)

        for record in usage.drain():
            yield {"type": "CUSTOM", "name": "ac.usage", "value": record}
        for reply in usage.drain_replies():
            yield _redacted({"type": "CUSTOM", "name": "ac.model_reply", "value": reply}, hidden)
        for io in usage.drain_tool_io():
            yield _redacted({"type": "CUSTOM", "name": "ac.tool_io", "value": io}, hidden)
        state = await self._state(graph, self._config(wf, spec.thread_id), names)
        for intr in state.interrupts:
            yield _redacted({"type": "CUSTOM", "name": "ac.interrupt", "value": asdict(intr)}, hidden)
        paused = bool(state.interrupts or state.next)
        yield _redacted(
            {
                "type": "RUN_FINISHED",
                "outcome": "interrupt" if paused else "success",
                "breakpoint": paused and not state.interrupts,
                "interrupts": [asdict(i) for i in state.interrupts],
                "next": state.next,
                "checkpoint_id": state.checkpoint_id,
                "output": _outputs(wf, state.values),
            },
            hidden,
        )

    # --------------------------------------------------------------- state ----
    async def _state(self, graph: Any, cfg: RunnableConfig, names: dict[str, str]) -> ThreadState:
        snap = await graph.aget_state(cfg)
        interrupts: list[InterruptInfo] = []
        for task in snap.tasks:
            for intr in task.interrupts:
                payload = jsonable(intr.value)
                # the payload names the step that asked (also inside a subgraph); tool approvals only have the task
                node = (payload.get("canvas_node_id") if isinstance(payload, dict) else None) or names.get(task.name)
                interrupts.append(
                    InterruptInfo(id=intr.id, kind=interrupt_kind(payload), canvas_node_id=node, payload=payload)
                )
        return ThreadState(
            values=jsonable(snap.values or {}),
            next=[names.get(n) or n for n in snap.next],
            interrupts=interrupts,
            checkpoint_id=(snap.config or {}).get("configurable", {}).get("checkpoint_id"),
            step=(snap.metadata or {}).get("step"),
        )

    async def get_state(self, wf: Workflow, thread_id: str, resources: ac.Resources) -> ThreadState:
        return await self._state(await self.graph(wf, resources), self._config(wf, thread_id), node_index(wf))

    async def history(
        self, wf: Workflow, thread_id: str, resources: ac.Resources, limit: int = 100, include_values: bool = False
    ) -> list[CheckpointInfo]:
        graph, names = await self.graph(wf, resources), node_index(wf)
        out = []
        async for snap in graph.aget_state_history(self._config(wf, thread_id), limit=limit):
            meta = snap.metadata or {}
            writes = {names.get(t.name) or t.name: jsonable(t.result) for t in snap.tasks if t.result is not None}
            out.append(
                CheckpointInfo(
                    checkpoint_id=snap.config["configurable"]["checkpoint_id"],
                    parent_checkpoint_id=(snap.parent_config or {}).get("configurable", {}).get("checkpoint_id"),
                    step=meta.get("step"),
                    source=meta.get("source"),
                    next=[names.get(n) or n for n in snap.next],
                    writes=writes or None,
                    created_at=snap.created_at,
                    values=jsonable(snap.values or {}) if include_values else None,
                )
            )
        return out

    async def update_state(
        self,
        wf: Workflow,
        thread_id: str,
        resources: ac.Resources,
        values: dict[str, Any],
        as_node: str | None,
        checkpoint_id: str | None,
    ) -> str:
        graph = await self.graph(wf, resources)
        node = wf.node(as_node) if as_node else None
        node_name = node.name if node else as_node  # accept canvas id or node name
        new_cfg = await graph.aupdate_state(self._config(wf, thread_id, checkpoint_id), values, as_node=node_name)
        return new_cfg["configurable"]["checkpoint_id"]

    async def copy_thread(
        self, wf: Workflow, src_thread: str, dst_thread: str, resources: ac.Resources, checkpoint_id: str | None
    ) -> str:
        """Copy all checkpoints of a thread (ids preserved) so a fork can continue from any of them."""
        await copy_thread(self._persist().checkpointer, src_thread, dst_thread)
        state = await self.get_state(wf, dst_thread, resources)
        return checkpoint_id or state.checkpoint_id or ""

    async def delete_thread(self, thread_id: str) -> None:
        await self._persist().checkpointer.adelete_thread(thread_id)


def _redacted(event: dict[str, Any], hidden: list[str]) -> dict[str, Any]:
    """Mask project secret values wherever they show up in an event (e.g. an error echoing a header)."""
    return hide_values(event, hidden)


def _registry(resources: Any) -> Any:
    provider = getattr(resources, "registry", None)
    return provider() if callable(provider) else None


def _outputs(wf: Workflow, values: dict[str, Any]) -> dict[str, Any]:
    s = wf.settings
    if s.output_channels:
        keep = set(s.output_channels)
    else:
        keep = {c.name for c in wf.state.channels if c.io in ("inout", "out")}
    return {k: v for k, v in values.items() if k in keep}
