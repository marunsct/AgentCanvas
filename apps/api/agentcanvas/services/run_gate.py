"""Checks every run passes, whatever started it (H3 of the 2026-10-18 review).

Interactive runs refuse a draft with errors (`api/v1/runs._runnable_workflow`), but cron and webhook triggers
without a deployment, event handlers' `start_workflow`, and hand-over ran a draft as stored. That let a builder
schedule a workflow with a local (stdio) MCP server, which the validator only reports (E042), and have the server
launch its command. This gate runs the security rules before every run starts, as a runner prepare hook.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

from agentcanvas.errors import WorkflowHasErrors
from agentcanvas.ir.canonical import ir_hash
from agentcanvas.ir.models import Workflow
from agentcanvas.settings import Settings
from agentcanvas.validation.engine import validate

# host execution in the hosted edition, private networks there, local MCP processes, imports outside the workspace
SECURITY_CODES = frozenset({"E041", "E042", "E043", "E080"})
CACHE_SIZE = 1024


class RunGate:
    """A prepare hook: refuses a workflow that breaks a security rule. Passing workflows are remembered by their
    content hash, so published versions called many times are validated once per server."""

    def __init__(
        self,
        settings: Settings,
        registry: Callable[[Workflow], Any] | None = None,
        settings_for: Callable[[Workflow], Awaitable[Settings]] | None = None,
        revision: Callable[[Workflow], str] | None = None,
    ):
        self.settings = settings
        self.registry = registry  # the workflow's project registry (built-ins + workspace custom nodes)
        self.settings_for = settings_for  # the settings that apply to the workflow's organisation
        self.revision = revision  # the workflow's project workspace code revision
        # passed checks -> the registry they were made with: kept alive, so its id is not reused by another (RT-11)
        self._passed: OrderedDict[str, Any] = OrderedDict()

    def blockers(self, wf: Workflow, registry: Any = None, settings: Settings | None = None) -> list[dict[str, Any]]:
        settings = settings or self.settings
        found = validate(
            wf,
            registry if registry is not None else (self.registry(wf) if self.registry else None),
            edition=settings.edition,
            allow_local_mcp=settings.allow_local_mcp,
        )
        return [d.model_dump() for d in found if d.severity == "error" and d.code in SECURITY_CODES]

    async def __call__(self, wf: Workflow) -> None:
        registry = self.registry(wf) if self.registry else None
        settings = await self.settings_for(wf) if self.settings_for else self.settings
        # the project's registry is rebuilt when its workspace code changes, and some rules read that code (E080
        # imports from outside the workspace): a passed check holds for this code only (R-11). The code's revision
        # names it; without one the registry object does (CPython reuses a freed object's id, so the entry holds it)
        code = self.revision(wf) if self.revision else str(id(registry))
        key = f"{settings.edition}:{settings.allow_local_mcp}:{ir_hash(wf)}:{code}"
        if key in self._passed and (self.revision or self._passed[key] is registry):
            self._passed.move_to_end(key)
            return
        errors = self.blockers(wf, registry, settings) if settings is not self.settings else self.blockers(wf, registry)
        if errors:
            raise WorkflowHasErrors(
                f"workflow breaks {len(errors)} security rule(s) ({', '.join(sorted({e['code'] for e in errors}))}); "
                "it cannot run until they are fixed",
                errors=errors,
            )
        self._passed[key] = None if self.revision else registry
        while len(self._passed) > CACHE_SIZE:
            self._passed.popitem(last=False)
