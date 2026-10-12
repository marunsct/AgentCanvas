"""The Pydantic AI adapter (S32): workflows designed for Pydantic AI, run and exported as Pydantic AI flows.

`codegen` writes the workflow as plain async Python with Pydantic AI `Agent`s for the AI blocks and a `FLOW` that the
flow engine (`supersteps`) runs as the canvas draws it; `runtime` adds the Pydantic AI parts (models, tools that wait
for a person as deferred tools, typed answers). The flows run in a worker process started from the Python environment
for Pydantic AI (`AGENTCANVAS_PYDANTIC_AI_PYTHON`, or `.frameworks/pydantic_ai` next to the API: `make framework
FW=pydantic_ai`), as the CrewAI and Google ADK workflows do (see `flowadapter`). Without one, Pydantic AI workflows
can still be designed, checked and exported; runs say how to install it.
"""

from __future__ import annotations

from typing import ClassVar

from agentcanvas import designs
from agentcanvas.adapters.crewai import CrewWorker
from agentcanvas.adapters.flowadapter import FlowAdapter, worker_files
from agentcanvas.adapters.pydantic_ai import codegen
from agentcanvas.registry.adapters import all_manifests

INSTALL_HINT = (
    "Pydantic AI is not installed on this server: run `make framework FW=pydantic_ai` (a separate Python environment "
    f"with pydantic-ai-slim=={codegen.PYDANTIC_AI_VERSION}) or set AGENTCANVAS_PYDANTIC_AI_PYTHON, then restart the "
    "server"
)


def pydantic_ai_python() -> str | None:
    """The Python with Pydantic AI that runs the flows (None: not installed here)."""
    return designs.part_python("pydantic_ai")


class PydanticAIWorker(CrewWorker):
    label = "Pydantic AI"
    package = "pydantic_ai"
    files = worker_files("pydantic_ai")
    extra_env: ClassVar[dict[str, str]] = {"PYDANTIC_AI_NO_BANNER": "1"}


class PydanticAIAdapter(FlowAdapter):
    id = "pydantic_ai"
    manifest = all_manifests()["pydantic_ai"]
    install_hint = INSTALL_HINT
    worker_class = PydanticAIWorker
    generate_source = staticmethod(codegen.generate)
