"""Framework Adapter SPI (spec doc 12 §C.2, minimal R1 subset — ticket M3-T1).

Everything framework-specific (LangChain/LangGraph today) lives behind this protocol; the core
(IR, validation, registry, db, services, runtime runner, API) never imports a framework (ADR 0011,
enforced by import-linter).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Any, Protocol

from agentcanvas.ir.models import Workflow
from agentcanvas.runtime.types import CheckpointInfo, RunSpec, ThreadState


class FrameworkAdapter(Protocol):
    id: str

    # ---- compilation
    def generate(self, wf: Workflow, registry: Any = None) -> str:
        """Generated source of the workflow (raises agentcanvas.errors.WorkflowHasErrors when invalid)."""
        ...

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
        """(file name, zip) of a standalone project running the workflow (M6-T2)."""
        ...

    # ---- persistence (opened once per process)
    def persistence(self, database_url: str | None) -> AbstractAsyncContextManager[None]: ...

    # ---- execution: canonical events (AG-UI based), last one is RUN_FINISHED or RUN_ERROR
    def execute(self, spec: RunSpec, resources: Any) -> AsyncIterator[dict[str, Any]]: ...

    # ---- thread state
    async def get_state(self, wf: Workflow, thread_id: str, resources: Any) -> ThreadState: ...

    async def history(
        self, wf: Workflow, thread_id: str, resources: Any, limit: int, include_values: bool = False
    ) -> list[CheckpointInfo]: ...

    async def update_state(
        self,
        wf: Workflow,
        thread_id: str,
        resources: Any,
        values: dict[str, Any],
        as_node: str | None,
        checkpoint_id: str | None,
    ) -> str: ...

    async def copy_thread(
        self, wf: Workflow, src_thread: str, dst_thread: str, resources: Any, checkpoint_id: str | None
    ) -> str: ...

    async def delete_thread(self, thread_id: str) -> None: ...

    # ---- long-term memory (store)
    async def memory_namespaces(self, prefix: tuple[str, ...], limit: int = 200) -> list[tuple[str, ...]]: ...

    async def memory_items(self, namespace: tuple[str, ...], query: str | None, limit: int) -> list[dict[str, Any]]: ...

    async def memory_delete(self, namespace: tuple[str, ...], key: str) -> None: ...

    async def memory_erase(self, prefix: tuple[str, ...]) -> int: ...

    async def memory_expire(self, prefix: tuple[str, ...], before: datetime) -> int:
        """Delete the memories under `prefix` last updated before `before`; returns how many (S12)."""
        ...

    async def memory_adopt(self, org_id: str) -> int:
        """Move memories written before organisations existed under `org_id`'s root; returns how many moved."""
        ...

    # ---- tools
    async def inspect_mcp(self, name: str, config: dict[str, Any], resources: Any) -> list[dict[str, Any]]:
        """Tools of an MCP server: name, description, destructive/read_only annotations."""
        ...

    # ---- evaluation (FR-EVAL-03)
    async def complete(
        self,
        resources: Any,
        model: str,
        system: str,
        prompt: str,
        usage: dict[str, int] | None = None,
        config: dict[str, Any] | None = None,
    ) -> str:
        """One chat completion with a catalog model (the LLM-as-judge evaluator). Resource name: "judge".
        `usage`, when given, gets the call's `input_tokens` and `output_tokens` added; `config` adds chat-model
        settings such as `base_url` and `api_key` (first-run setup's connection test)."""
        ...

    # ---- Copilot (S14)
    async def chat(
        self,
        resources: Any,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        usage: dict[str, int] | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """One tool-calling chat turn (the Copilot). Resource name: "copilot".

        `messages` are framework-neutral: {"role": "system"|"user"|"assistant"|"tool", "content": str} plus
        `tool_calls` [{"id", "name", "args"}] on assistant turns and `tool_call_id` on tool turns; a system
        message may set `"cache": true` to ask providers that support it to cache that prefix. `tools` are
        {"name", "description", "parameters" (JSON schema)}. Returns {"content": str, "tool_calls": [...]}."""
        ...

    def script(self, resources: Any, import_path: str) -> Any:
        """A workspace function `(state) -> dict` as an async callable, sandboxed when the project has one."""
        ...

    def default_resources(self) -> Any:
        """Resources (models/tools resolver) used when the host installs no override."""
        ...

    def new_resources(self, *, resources_class: Any = None, **kw: Any) -> Any:
        """A resolver for one project (workspace, secrets, script_runner keyword arguments)."""
        ...
