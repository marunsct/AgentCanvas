"""The OpenAI Agents SDK adapter (S25): workflows designed for the SDK, run and exported as SDK code.

`codegen` writes the workflow as a Python module (an `Agent` per AI block, a function per block, a `FLOW` of what
follows what) and `runtime` runs it. Inside AgentCanvas the module is executed as generated, so what runs is what
the code view shows and what an export ships. The canvas models reach the SDK through the model bridge (any catalog
provider, scripted replies included, with usage and replies recorded); tools are the canvas tool blocks, wrapped as
SDK function tools, and those an agent must ask about pause the run with the SDK's own `RunState`.

A paused run is kept in memory as JSON, so a server restart forgets it (the adapter declares no saved-state
abilities, so history, state editing and breakpoints are refused for these workflows with a plain message).
"""

from __future__ import annotations

import hashlib
import json
import sys
import types
from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import ExitStack, asynccontextmanager
from typing import Any

from agentcanvas.adapters.langchain import nodes as ac
from agentcanvas.adapters.langchain.bridge import model_bridge
from agentcanvas.adapters.langchain.events import UsageTracker, node_index
from agentcanvas.adapters.openai_agents import codegen
from agentcanvas.errors import WorkflowHasErrors
from agentcanvas.ir.models import Workflow
from agentcanvas.registry import default_registry
from agentcanvas.registry.adapters import all_manifests
from agentcanvas.runtime.types import RunSpec
from agentcanvas.validation.blast import dry_run_tools

FLOW_CACHE_SIZE = 64


class OpenAIAgentsAdapter:
    id = "openai_agents"
    manifest = all_manifests()["openai_agents"]

    def __init__(self) -> None:
        self._paused: dict[str, str] = {}  # thread id -> runtime.Paused as JSON
        self._flows: OrderedDict[str, Any] = OrderedDict()  # source hash -> the generated module's FLOW
        self._shared: ac.Resources | None = None

    # ------------------------------------------------------------ compile ----
    def _blocked(self, wf: Workflow) -> list[dict[str, Any]]:
        return [
            {"code": "P001", "severity": "error", "message": f"{self.manifest.label} cannot run '{el.type}' blocks",
             "node_id": el.id}
            for el in [*wf.nodes, *wf.resources] if self.manifest.support(el.type) == "unsupported"
        ]  # fmt: skip

    def generate(self, wf: Workflow, registry: Any = None) -> str:
        """The workflow as an OpenAI Agents SDK flow (a Python module)."""
        from agentcanvas.validation import validate

        errors = [d.model_dump() for d in validate(wf, registry or default_registry()) if d.severity == "error"]
        if wf.framework != self.id:  # the validator checks the blocks against the workflow's own framework only
            errors += self._blocked(wf)
        if errors:
            raise WorkflowHasErrors("; ".join(f"{d['code']} {d['message']}" for d in errors), errors=errors)
        return codegen.generate(wf)

    def flow(self, wf: Workflow) -> Any:
        """The generated module's `FLOW`, ready for `runtime.run` (cached by source). The runner has validated the
        workflow; only blocks this framework cannot run are refused here."""
        if blocked := self._blocked(wf):
            raise WorkflowHasErrors("; ".join(f"{d['code']} {d['message']}" for d in blocked), errors=blocked)
        source = codegen.generate(wf)
        key = hashlib.sha256(source.encode()).hexdigest()
        if key in self._flows:
            self._flows.move_to_end(key)
            return self._flows[key]
        module = types.ModuleType(f"agentcanvas_flow_{key[:16]}")
        sys.modules[module.__name__] = module  # Pydantic resolves the output classes through their module
        try:
            exec(compile(source, f"<{wf.name} flow>", "exec"), module.__dict__)  # our generated code
        except BaseException:
            sys.modules.pop(module.__name__, None)
            raise
        self._flows[key] = module.FLOW
        while len(self._flows) > FLOW_CACHE_SIZE:
            old, _ = self._flows.popitem(last=False)
            sys.modules.pop(f"agentcanvas_flow_{old[:16]}", None)
        return module.FLOW

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
        from agentcanvas.adapters.openai_agents.packager import package

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
        from agentcanvas.adapters.openai_agents import runtime as rt

        wf = spec.workflow
        try:
            flow = self.flow(wf)
            if spec.mocks:
                resources = resources.with_mocks(spec.mocks)
            if spec.dry_run:
                resources = resources.with_dry_run(dry_run_tools(wf, _registry(resources)))
            resume = None
            if spec.resume is not None:
                saved = self._paused.pop(spec.thread_id, None)
                if saved is None:
                    yield {"type": "RUN_ERROR", "code": "error", "message": "nothing to resume on this conversation"}
                    return
                resume = (rt.Paused.from_json(saved), spec.resume.value)
            else:
                self._paused.pop(spec.thread_id, None)
            usage = UsageTracker(node_index(wf))
            host = ServerHost(wf, resources, await model_bridge(), usage, self)
        except Exception as exc:  # noqa: BLE001 - every failure reaches the UI as RUN_ERROR
            yield {"type": "RUN_ERROR", "code": "error", "message": f"{type(exc).__name__}: {exc}"}
            return
        with host:
            async for ev in rt.run(flow, host, input=spec.input, resume=resume, run_id=spec.run_id):
                for record in usage.drain():
                    yield {"type": "CUSTOM", "name": "ac.usage", "value": record}
                for reply in usage.drain_replies():
                    yield {"type": "CUSTOM", "name": "ac.model_reply", "value": reply}
                for io in usage.drain_tool_io():
                    yield {"type": "CUSTOM", "name": "ac.tool_io", "value": io}
                paused = ev.pop("paused", None)
                if paused is not None:
                    self._paused[spec.thread_id] = paused.to_json()
                yield _jsonable(ev)


class ServerHost:
    """What a flow's steps get inside AgentCanvas: canvas models through the bridge, canvas tools as SDK tools,
    the project's knowledge bases and workspace code."""

    def __init__(self, wf: Workflow, resources: ac.Resources, bridge: Any, usage: UsageTracker, adapter: Any):
        self.wf, self.resources, self.bridge, self.usage, self.adapter = wf, resources, bridge, usage, adapter
        self._stack = ExitStack()
        self._keys: dict[tuple[str, str], str] = {}

    def __enter__(self) -> ServerHost:
        return self

    def __exit__(self, *exc: object) -> None:
        self._stack.close()  # the bridge stops answering this run's keys

    def _resource(self, name: str) -> Any:
        found = next((r for r in self.wf.resources if r.name == name), None)
        if found is None:
            raise KeyError(f"no block named '{name}' in this workflow")
        return found

    def _config(self, step: str) -> dict[str, Any]:
        return {"callbacks": [self.usage], "metadata": {"langgraph_node": step}}

    def model(self, resource: str, step: str) -> Any:
        from agents import OpenAIChatCompletionsModel
        from openai import AsyncOpenAI

        if (resource, step) not in self._keys:
            res = self._resource(resource)
            lc_model = self.resources.model(res.name, res.config)
            self._keys[(resource, step)] = self._stack.enter_context(self.bridge.serving(lc_model, self._config(step)))
        client = AsyncOpenAI(base_url=self.bridge.root + "/v1", api_key=self._keys[(resource, step)], max_retries=0)
        return OpenAIChatCompletionsModel(model="canvas", openai_client=client)

    def tools(self, resources: list[str], approve: list[str], step: str) -> list[Any]:
        out = []
        for name in resources:
            res = self._resource(name)
            found = self.resources.tool(res.name, res.config, res.type)
            for t in found if isinstance(found, list) else [found]:
                out.append(sdk_tool(t, self._config(step), t.name in approve or res.name in approve))
        return out

    async def search(
        self, knowledge_base: str, query: str, *, mode: str, top_k: int, min_score: float
    ) -> list[dict[str, Any]]:
        return await self.resources.search_knowledge(knowledge_base, query, mode=mode, top_k=top_k, min_score=min_score)

    async def script(self, import_path: str, state: dict[str, Any]) -> dict[str, Any]:
        return await self.adapter.script(self.resources, import_path)(state)


def sdk_tool(t: Any, config: dict[str, Any], needs_approval: bool) -> Any:
    """A LangChain tool (a canvas tool block) as an SDK function tool. Calls go through LangChain, so they are
    recorded and replayed like any other tool call of a run; an error is told to the model, as LangGraph does."""
    from agents import FunctionTool
    from langchain_core.utils.function_calling import convert_to_openai_tool

    spec = convert_to_openai_tool(t)["function"]

    async def invoke(_ctx: Any, args_json: str) -> str:
        try:
            out = await t.ainvoke(json.loads(args_json or "{}"), config=config)
        except Exception as exc:  # noqa: BLE001 - the model reads the error and may try again
            return f"Error: {type(exc).__name__}: {exc}"
        content = getattr(out, "content", out)
        return content if isinstance(content, str) else json.dumps(content, default=str)

    return FunctionTool(
        name=spec["name"],
        description=spec.get("description") or spec["name"],
        params_json_schema=spec.get("parameters") or {"type": "object", "properties": {}},
        on_invoke_tool=invoke,
        strict_json_schema=False,
        needs_approval=needs_approval,
    )


def _registry(resources: Any) -> Any:
    factory = getattr(resources, "registry", None)
    return factory() if callable(factory) else None


def _jsonable(v: Any) -> Any:
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [_jsonable(x) for x in v]
    if hasattr(v, "model_dump"):
        return _jsonable(v.model_dump(mode="json"))
    if isinstance(v, str | int | float | bool) or v is None:
        return v
    return str(v)
