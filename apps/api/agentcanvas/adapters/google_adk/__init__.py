"""The Google ADK adapter (S29-S30): workflows designed for Google ADK, run and exported as native ADK 2 workflows.

`codegen` writes the workflow as an ADK graph `Workflow` (a function node per block, wired with ADK's edges, route
dicts and `JoinNode`s; `Agent`s with `LiteLlm` models for the AI blocks) and `runtime` adds what the canvas needs.
ADK cannot be installed next to the server's own packages (its LiteLLM pins an older `openai`), so the workflows run
in a worker process (`worker`) started from the Python environment that mixed flows use for ADK agents (S28):
`AGENTCANVAS_GOOGLE_ADK_PYTHON`, or `.frameworks/google_adk` next to the API (`make framework FW=google_adk`).
Without one, ADK workflows can still be designed, checked and exported; runs say how to install it.

It runs exactly like the CrewAI adapter (S27), whose server side it reuses: the canvas models reach ADK through the
model bridge, tools, knowledge bases and workspace code run on the server when the worker asks, and a paused run is
kept as JSON with its conversation (`runtime/paused.py`).
"""

from __future__ import annotations

from typing import Any, ClassVar

from agentcanvas import designs
from agentcanvas.adapters.crewai import CrewAIAdapter, CrewWorker, shared_worker
from agentcanvas.adapters.google_adk import codegen
from agentcanvas.ir.models import Workflow
from agentcanvas.registry.adapters import all_manifests

WORKER_FILES = ["expr.py", "adapters/google_adk/runtime.py", "adapters/google_adk/worker.py"]
INSTALL_HINT = (
    "Google ADK is not installed on this server: run `make framework FW=google_adk` (a separate Python environment "
    f"with google-adk=={codegen.ADK_VERSION}) or set AGENTCANVAS_GOOGLE_ADK_PYTHON, then restart the server"
)


def adk_python() -> str | None:
    """The Python with Google ADK that runs the workflows (None: not installed here)."""
    return designs.part_python("google_adk")


class AdkWorker(CrewWorker):
    label = "Google ADK"
    package = "google_adk"
    files = WORKER_FILES
    extra_env: ClassVar[dict[str, str]] = {"ADK_DISABLE_TELEMETRY": "true", "LITELLM_LOG": "ERROR"}


class GoogleADKAdapter(CrewAIAdapter):
    id = "google_adk"
    manifest = all_manifests()["google_adk"]
    install_hint = INSTALL_HINT

    def _blocked(self, wf: Workflow) -> list[dict[str, Any]]:
        return [
            {"code": "P001", "severity": "error", "message": f"{self.manifest.label} cannot run '{el.type}' blocks",
             "node_id": el.id}
            for el in [*wf.nodes, *wf.resources] if self.manifest.support(el.type) == "unsupported"
        ]  # fmt: skip

    def python(self) -> str | None:
        return adk_python()

    def worker(self, python: str) -> CrewWorker:
        return shared_worker(python, AdkWorker)

    def source(self, wf: Workflow) -> str:
        return codegen.generate(wf)

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
        from agentcanvas.adapters.google_adk.packager import package

        return package(wf, self.generate(wf, registry), workspace=workspace, test_cases=test_cases, version=version)
