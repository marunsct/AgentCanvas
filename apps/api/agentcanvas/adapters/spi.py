"""Framework Adapter SPI (spec doc 12 §C.2, minimal R1 subset — ticket M3-T1).

Everything framework-specific (LangChain/LangGraph today) lives behind this protocol; the core
(IR, validation, registry, db, services, runtime runner, API) never imports a framework (ADR 0011,
enforced by import-linter).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol

from agentcanvas.ir.models import Workflow
from agentcanvas.runtime.types import CheckpointInfo, RunSpec, ThreadState


class FrameworkAdapter(Protocol):
    id: str

    # ---- compilation
    def generate(self, wf: Workflow, registry: Any = None) -> str:
        """Generated source of the workflow (raises agentcanvas.errors.WorkflowHasErrors when invalid)."""
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

    # ---- tools
    async def inspect_mcp(self, name: str, config: dict[str, Any], resources: Any) -> list[dict[str, Any]]:
        """Tools of an MCP server: name, description, destructive/read_only annotations."""
        ...

    def default_resources(self) -> Any:
        """Resources (models/tools resolver) used when the host installs no override."""
        ...

    def new_resources(self, *, resources_class: Any = None, **kw: Any) -> Any:
        """A resolver for one project (workspace, secrets, script_runner keyword arguments)."""
        ...
