"""The server side of flows run by the flow engine (S32): what the Pydantic AI and Agno adapters share.

Both run exactly like the CrewAI and Google ADK adapters, whose server side they reuse: the flow runs in a worker
process started from the framework's own Python environment (`designs.part_python`), the canvas models reach it
through the model bridge, tools, knowledge bases and workspace code run on the server when the worker asks, and a
paused run is kept in memory as JSON. A subclass names its framework, its code generator and its worker.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agentcanvas import designs
from agentcanvas.adapters.crewai import CrewAIAdapter, CrewWorker, shared_worker
from agentcanvas.ir.models import Workflow


def worker_files(framework: str) -> list[str]:
    """What a flow worker for `framework` needs from AgentCanvas (copied next to it when it starts)."""
    return ["expr.py", "adapters/supersteps.py", "adapters/flowworker.py", f"adapters/{framework}/runtime.py",
            f"adapters/{framework}/worker.py"]  # fmt: skip


class FlowAdapter(CrewAIAdapter):
    worker_class: type[CrewWorker]
    generate_source: Callable[[Workflow], str]

    def _blocked(self, wf: Workflow) -> list[dict[str, Any]]:
        return [
            {"code": "P001", "severity": "error", "message": f"{self.manifest.label} cannot run '{el.type}' blocks",
             "node_id": el.id}
            for el in [*wf.nodes, *wf.resources] if self.manifest.support(el.type) == "unsupported"
        ]  # fmt: skip

    def python(self) -> str | None:
        return designs.part_python(self.id)

    def worker(self, python: str) -> CrewWorker:
        return shared_worker(python, self.worker_class)

    def source(self, wf: Workflow) -> str:
        return type(self).generate_source(wf)

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
        from agentcanvas.adapters.flowpackager import package

        return package(wf, self.generate(wf, registry), self.id, workspace=workspace, test_cases=test_cases,
                       version=version)  # fmt: skip
