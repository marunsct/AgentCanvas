"""Per-project runtime context: the project's workspace and its resolver (models, tools, scripts, secrets).

Workflows are mapped to their project when a workflow is loaded (`PROJECT_OF`), so the runner — which only sees
the IR — can resolve `scripts.`/`tools.`/`nodes.` imports from the right workspace.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agentcanvas.registry import Registry, default_registry
from agentcanvas.settings import Settings
from agentcanvas.workspace import Workspace
from agentcanvas.workspace.catalog import Catalog, scan

PROJECT_OF: dict[str, str] = {}  # workflow id -> project id (filled by WorkflowService)
CURRENT: ProjectRegistry | None = None  # the running app's registry (set at startup; one app per process)
DEFAULT_PROJECT = "prj_default"
_SAFE = re.compile(r"^[A-Za-z0-9_-]+$")


class ProjectRegistry:
    def __init__(self, settings: Settings, adapter: Any, resources_class: Any = None):
        self.settings = settings
        self.adapter = adapter
        self.resources_class = resources_class  # e.g. DemoResources on the key-less dev server
        self._resources: dict[str, Any] = {}
        self._catalogs: dict[str, tuple[str, Catalog, Registry]] = {}
        self.secrets: dict[str, dict[str, str]] = {}  # project id -> {name: value}, filled by the secrets service
        self.knowledge: Any = None  # services.knowledge.Searcher (S24), set at startup
        # project id -> its answer cache; set at startup to the database's, so replicas share it (G-8)
        self.answer_cache: Callable[[str], Any] | None = None

    def workspace(self, project_id: str) -> Workspace:
        if not _SAFE.match(project_id):
            raise ValueError(f"invalid project id '{project_id}'")
        return Workspace(Path(self.settings.workspaces_dir) / project_id).ensure()

    def resources(self, project_id: str) -> Any:
        if project_id not in self._resources:
            ws = self.workspace(project_id)
            secrets = self.secrets.setdefault(project_id, {})
            from agentcanvas.sandbox import script_runner

            runner = script_runner(self.settings, ws.root, secrets)
            self._resources[project_id] = self.adapter.new_resources(
                workspace=ws.root,
                secrets=secrets,
                script_runner=runner,
                registry=lambda: self.registry(project_id),
                resources_class=self.resources_class,
            )
            if self.knowledge is not None:
                self._resources[project_id].knowledge = self.knowledge.for_project(project_id)
            if self.answer_cache is not None:
                self._resources[project_id].answers = self.answer_cache(project_id)
        return self._resources[project_id]

    async def aclose(self) -> None:
        """Release per-project sandboxes (a LangSmith Sandbox stays up until its idle timeout otherwise)."""
        for res in self._resources.values():
            runner = getattr(res, "script_runner", None)
            if runner is not None and hasattr(runner, "aclose"):
                await runner.aclose()

    async def forget(self, project_id: str) -> None:
        """A deleted project: close its sandbox and drop what is cached for it."""
        res = self._resources.pop(project_id, None)
        runner = getattr(res, "script_runner", None)
        if runner is not None and hasattr(runner, "aclose"):
            await runner.aclose()
        self._catalogs.pop(project_id, None)
        self.secrets.pop(project_id, None)
        for wf_id in [w for w, p in PROJECT_OF.items() if p == project_id]:
            PROJECT_OF.pop(wf_id, None)

    def has_handlers(self, project_id: str) -> bool:
        """True when the project's workspace has event handler code (without creating the workspace)."""
        events = Path(self.settings.workspaces_dir) / project_id / "events"
        return events.is_dir() and any(p.name != "__init__.py" for p in events.glob("*.py"))

    def catalog(self, project_id: str) -> Catalog:
        return self._scan(project_id)[1]

    def registry(self, project_id: str) -> Registry:
        """Built-in node types plus the project's workspace custom nodes (M5-T7)."""
        return self._scan(project_id)[2]

    def _scan(self, project_id: str) -> tuple[str, Catalog, Registry]:
        ws = self.workspace(project_id)
        rev = ws.revision()
        cached = self._catalogs.get(project_id)
        if cached is None or cached[0] != rev:
            # importing user code (to read a Pydantic config model) only when scripts run in-process anyway
            cat = scan(ws.root, import_code=self.settings.script_execution == "inprocess")
            builtin = {m.type for m in default_registry().all()}
            custom = [m for m in cat.nodes if m.type not in builtin]
            cat.problems += [
                {"path": "nodes/", "message": f"'{m.type}' is a built-in type"} for m in cat.nodes if m.type in builtin
            ]
            cached = (rev, cat, default_registry().with_manifests(custom))
            self._catalogs[project_id] = cached
        return cached

    def for_workflow(self, wf: Any) -> Any:
        project = PROJECT_OF.get(wf.id, DEFAULT_PROJECT) if wf is not None else DEFAULT_PROJECT
        return self.resources(project)
