"""Per-project runtime context: the project's workspace and its resolver (models, tools, scripts, secrets).

Workflows are mapped to their project when a workflow is loaded (`PROJECT_OF`), so the runner — which only sees
the IR — can resolve `scripts.`/`tools.`/`nodes.` imports from the right workspace.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from agentcanvas.settings import Settings
from agentcanvas.workspace import Workspace

PROJECT_OF: dict[str, str] = {}  # workflow id -> project id (filled by WorkflowService)
DEFAULT_PROJECT = "prj_default"
_SAFE = re.compile(r"^[A-Za-z0-9_-]+$")


class ProjectRegistry:
    def __init__(self, settings: Settings, adapter: Any, resources_class: Any = None):
        self.settings = settings
        self.adapter = adapter
        self.resources_class = resources_class  # e.g. DemoResources on the key-less dev server
        self._resources: dict[str, Any] = {}
        self.secrets: dict[str, dict[str, str]] = {}  # project id -> {name: value}, filled by the secrets service

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
                workspace=ws.root, secrets=secrets, script_runner=runner, resources_class=self.resources_class
            )
        return self._resources[project_id]

    def for_workflow(self, wf: Any) -> Any:
        project = PROJECT_OF.get(wf.id, DEFAULT_PROJECT) if wf is not None else DEFAULT_PROJECT
        return self.resources(project)
